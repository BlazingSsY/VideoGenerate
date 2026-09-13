"""Execute frozen Agent plans as recoverable generate/compose tasks."""
from __future__ import annotations

import asyncio
import logging
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .agent_run_service import append_run_event
from .canvas_service import lock_canvas, plan_node_inputs_match
from .agent_service import _validated_reference_media
from .config import settings
from .catalog import get_model
from .database import SessionLocal
from .media_links import format_video_src
from .models import AgentRun, AgentTask, AgentTurn, Canvas, CanvasNode, Conversation, Message, User
from .tasks import _download_video, run_generation

logger = logging.getLogger(__name__)
_runs: set[str] = set()
_tasks: set[asyncio.Task] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _push_turn_task_event(
    turn_id: str,
    node_id: str,
    status: str,
    video_src: str = "",
    error: str = "",
    run_id: str = "",
) -> None:
    """Temporary compatibility stream for clients still following turn SSE."""
    from .agent_chat import _push_event

    data: dict[str, Any] = {"node_id": node_id, "status": status}
    if run_id:
        data["run_id"] = run_id
    if video_src:
        data["video_src"] = video_src
    if error:
        data["error"] = error
    _push_event(turn_id, {"event": "run.task", "data": data})


# Compatibility alias retained for existing integrations.
_push_task_event = _push_turn_task_event


def _set_run(run_id: str, **fields: Any) -> None:
    """Persist a run transition and its event in one transaction."""
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if run is None:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        append_run_event(db, run.id, "run.status", {
            "status": run.status,
            "error": run.error,
            "output_file": run.output_file,
            "canvas_id": run.canvas_id,
        })


def _set_task(run_id: str, node_id: str, **fields: Any) -> None:
    """Persist task status, canvas binding and durable event atomically."""
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        task = db.query(AgentTask).filter_by(run_id=run_id, node_id=node_id).one_or_none()
        if run is None or task is None:
            return
        previous = task.status
        for key, value in fields.items():
            setattr(task, key, value)
        if fields.get("status") == "running" and previous != "running":
            task.attempt_count = int(task.attempt_count or 0) + 1

        # Layout-only canvas revisions are harmless. Compare the exact frozen task
        # inputs so a moved node still receives its result, while an edited prompt,
        # material edge or output order cannot be overwritten by a late task.
        if run.canvas_id and task.canvas_node_id:
            canvas = db.get(Canvas, run.canvas_id)
            if canvas is not None:
                lock_canvas(db, canvas)
            canvas_node = db.get(CanvasNode, task.canvas_node_id)
            if (
                canvas is not None
                and canvas_node is not None
                and canvas_node.canvas_id == canvas.id
                and plan_node_inputs_match(
                    db, canvas.id, task.input_snapshot or {},
                    (run.input_snapshot or {}).get("node_map") or {},
                    (run.input_snapshot or {}).get("plan"),
                )
            ):
                canvas_node.status = task.status
                canvas_node.data = {**(canvas_node.data or {}), "outputError": task.error or ""}
                if task.message_id and task.task_type == "generate":
                    canvas_node.message_id = task.message_id
                if task.status == "succeeded":
                    canvas_node.input_hash = run.plan_version
                    if task.task_type == "compose" and task.output_file:
                        canvas_node.data = {**(canvas_node.data or {}), "outputFile": task.output_file}

        append_run_event(db, run.id, "task.status", {
            "task_id": task.id,
            "node_id": task.node_id,
            "canvas_node_id": task.canvas_node_id,
            "status": task.status,
            "attempt_count": task.attempt_count,
            "output_file": task.output_file,
            "error": task.error,
        })


def _create_message(run_id: str, node: dict[str, Any]) -> str:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        turn = db.get(AgentTurn, run.turn_id) if run else None
        if run is None or turn is None:
            raise RuntimeError("Agent 运行记录不存在")

        conversation: Conversation | None = None
        if run.canvas_id:
            # A canvas has a hidden shadow conversation with the same id.
            conversation = db.get(Conversation, run.canvas_id)
            if conversation is None or conversation.user_id != run.user_id:
                raise RuntimeError("Agent 运行绑定的画布会话不存在")
        if conversation is None:
            conversation = Conversation(
                user_id=run.user_id,
                title=str((run.input_snapshot or {}).get("plan", {}).get("title") or turn.user_input)[:120] or "Agent 视频任务",
                last_model=str(node.get("model", "")),
                kind="chat",
            )
            db.add(conversation)
            db.flush()

        # New runs already contain resolved immutable URLs. Resolve legacy
        # asset-only snapshots through the same ownership-checked path as planning.
        user = db.get(User, run.user_id)
        if user is None:
            raise RuntimeError("Agent 运行用户不存在")
        references = deepcopy(node.get("reference_media") or [])
        for item in references:
            if not isinstance(item, dict) or not item.get("source_node"):
                continue
            source = db.query(AgentTask).filter_by(run_id=run_id, node_id=item.pop("source_node")).one()
            upstream = db.get(Message, source.message_id) if source.message_id else None
            if source.status != "succeeded" or not upstream or not upstream.video_url:
                raise RuntimeError("上游生成节点没有可供参考的在线视频地址")
            item["url"] = upstream.video_url
        media = _validated_reference_media(db, user, references, node.get("end_frame"))
        model_spec = get_model(str(node.get("model", "")))
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            resolved_prompt=str(node.get("prompt", "")),
            model=str(node.get("model", "")),
            params={
                "capability": str(node.get("capability", "t2v")),
                "resolution": str(node.get("resolution", "")),
                "ratio": str(node.get("ratio", "")),
                "duration": int(node.get("duration", 0)),
                "watermark": node.get("watermark", model_spec.watermark_default if model_spec else None),
                "audio": node.get("audio", model_spec.audio_default if model_spec else None),
                "use_context": False,
                "agent_run_id": run_id,
                "agent_node_id": str(node.get("id", "")),
                "canvas_revision": run.canvas_revision,
                "plan_version": run.plan_version,
            },
            reference_images=[
                item.get("url", "") for item in media
                if isinstance(item, dict) and item.get("kind") == "image"
            ],
            reference_media=media,
            status="pending",
        )
        db.add(message)
        db.flush()
        task = db.query(AgentTask).filter_by(run_id=run_id, node_id=str(node["id"])).one()
        task.message_id = message.id
        db.commit()
        return message.id


def _task_output(run_id: str, node_id: str) -> Path:
    with SessionLocal() as db:
        task = db.query(AgentTask).filter_by(run_id=run_id, node_id=node_id).one()
        if task.output_file:
            return settings.video_dir / task.output_file
        message = db.get(Message, task.message_id) if task.message_id else None
        if message is None or message.status != "succeeded" or not message.local_video:
            raise RuntimeError(f"上游任务 {node_id} 没有可合成的本地视频")
        return settings.video_dir / message.local_video


async def _compose(run_id: str, node: dict[str, Any]) -> str:
    if "transitions" in node:
        from .canvas_executor import compose_video_clips
        transitions = {item["after"]: item["type"] for item in node["transitions"]}
        clips = [(_task_output(run_id, key).name, transitions.get(key, "none")) for key in node["inputs"]]
        return await compose_video_clips(clips, run_id)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("服务器未安装 FFmpeg，无法执行视频拼接")
    inputs = [_task_output(run_id, str(node_id)) for node_id in node.get("inputs", [])]
    if any(not path.is_file() for path in inputs):
        raise RuntimeError("合成输入视频不存在")
    filename = f"agent-{run_id}-{node['id']}.mp4"
    target = settings.video_dir / filename
    list_file = settings.video_dir / f"agent-{run_id}-{node['id']}.concat.txt"
    list_file.write_text(
        "".join(f"file '{path.as_posix().replace(chr(39), chr(39) * 2)}'\n" for path in inputs),
        encoding="utf-8",
    )
    try:
        async def run_ffmpeg(*args: str) -> tuple[int, bytes]:
            process = await asyncio.create_subprocess_exec(
                ffmpeg, *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            return process.returncode or 0, stderr

        code, stderr = await run_ffmpeg(
            "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", str(target),
        )
        if code != 0 or not target.is_file() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            code, stderr = await run_ffmpeg(
                "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(target),
            )
        if code != 0 or not target.is_file() or target.stat().st_size == 0:
            detail = stderr.decode("utf-8", errors="replace")[-800:]
            raise RuntimeError(f"FFmpeg 拼接失败：{detail}")
        return filename
    finally:
        list_file.unlink(missing_ok=True)


def _load_task_node(run_id: str, node_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        task = db.query(AgentTask).filter_by(run_id=run_id, node_id=node_id).one()
        if task.input_snapshot:
            return deepcopy(task.input_snapshot)
        turn = db.get(AgentTurn, run.turn_id) if run else None
        for node in (turn.plan or {}).get("nodes", []) if turn else []:
            if str(node.get("id")) == node_id:
                return deepcopy(node)
    raise RuntimeError(f"任务输入快照不存在：{node_id}")


async def _execute_task(run_id: str, node_id: str, turn_id: str) -> bool:
    node = _load_task_node(run_id, node_id)
    _set_task(run_id, node_id, status="running", error="")
    _push_turn_task_event(turn_id, node_id, "running", run_id=run_id)
    try:
        if node.get("type") == "generate":
            with SessionLocal() as db:
                current = db.query(AgentTask).filter_by(run_id=run_id, node_id=node_id).one()
                message_id = current.message_id
            if not message_id:
                message_id = _create_message(run_id, node)
            _set_task(run_id, node_id, message_id=message_id)
            with SessionLocal() as db:
                already_succeeded = db.get(Message, message_id).status == "succeeded"
            if not already_succeeded:
                await run_generation(message_id)
            with SessionLocal() as db:
                message = db.get(Message, message_id)
                if message is None or message.status != "succeeded":
                    raise RuntimeError(message.error if message else "视频生成任务丢失")
                output = message.local_video
                video_url = message.video_url
            if already_succeeded and (not output or not (settings.video_dir / output).is_file()):
                # Reusing an online result needs only a local copy for composition;
                # never submit a new generation because its download is missing.
                output = await _download_video(video_url, message_id) if video_url else ''
                if output:
                    with SessionLocal() as db:
                        db.get(Message, message_id).local_video = output
                        db.commit()
            if not output:
                raise RuntimeError("上游只返回了临时地址，缺少本地视频，无法保证后续拼接")
            _set_task(run_id, node_id, status="succeeded", output_file=output, message_id=message_id, error="")
        elif node.get("type") == "compose":
            output = await _compose(run_id, node)
            _set_task(run_id, node_id, status="succeeded", output_file=output, error="")
        else:
            raise RuntimeError(f"不支持的任务类型：{node.get('type')}")
        _push_turn_task_event(
            turn_id, node_id, "succeeded",
            video_src=format_video_src(output), run_id=run_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _set_task(run_id, node_id, status="failed", error=str(exc))
        _push_turn_task_event(turn_id, node_id, "failed", error=str(exc)[:200], run_id=run_id)
        return False


def _cancel_remaining(run_id: str, turn_id: str) -> None:
    with SessionLocal() as db:
        tasks = db.query(AgentTask).filter_by(run_id=run_id).all()
        ids = [task.node_id for task in tasks if task.status in {"queued", "running"}]
    for node_id in ids:
        _set_task(run_id, node_id, status="canceled", error="运行已取消")
        _push_turn_task_event(turn_id, node_id, "canceled", run_id=run_id)


def _block_remaining(run_id: str, failed_node: str) -> None:
    with SessionLocal() as db:
        tasks = db.query(AgentTask).filter_by(run_id=run_id).all()
        blocked = {failed_node}
        changed = True
        while changed:
            changed = False
            for task in tasks:
                if task.status == "queued" and task.node_id not in blocked and any(dep in blocked for dep in (task.depends_on or [])):
                    blocked.add(task.node_id)
                    changed = True
        queued = [task.node_id for task in tasks if task.status == "queued" and task.node_id in blocked]
    for node_id in queued:
        _set_task(run_id, node_id, status="blocked", error=f"上游任务 {failed_node} 失败")


async def execute_run(run_id: str) -> None:
    if run_id in _runs:
        return
    _runs.add(run_id)
    turn_id = ""
    try:
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            if run is None:
                return
            turn_id = run.turn_id
            task_rows = db.query(AgentTask).filter(AgentTask.run_id == run_id).all()
            legacy_serial = not bool(run.input_snapshot)
            for task in task_rows:
                if task.status in {"queued", "running"}:
                    _push_turn_task_event(turn_id, task.node_id, "queued", run_id=run_id)
        _set_run(run_id, status="running", error="")

        while True:
            with SessionLocal() as db:
                run = db.get(AgentRun, run_id)
                tasks = db.query(AgentTask).filter(AgentTask.run_id == run_id).all()
                if run is None:
                    return
                if run.cancel_requested:
                    canceled = True
                    ready_ids: list[str] = []
                    failed = None
                    all_succeeded = False
                else:
                    canceled = False
                    succeeded = {task.node_id for task in tasks if task.status == "succeeded"}
                    pending = [task for task in tasks if task.status in {"queued", "running"}]
                    ready_ids = [
                        task.node_id for task in pending
                        if all(dep in succeeded for dep in (task.depends_on or []))
                    ]
                    failed = next((task for task in tasks if task.status == "failed"), None)
                    all_succeeded = bool(tasks) and all(task.status == "succeeded" for task in tasks)

            if canceled:
                _cancel_remaining(run_id, turn_id)
                _set_run(run_id, status="canceled", error="用户已停止后续任务")
                _push_turn_task_event(turn_id, "__run__", "canceled", error="已停止后续任务", run_id=run_id)
                return
            if failed is not None:
                for task in tasks:
                    if task.status == "failed":
                        _block_remaining(run_id, task.node_id)
                # Other independent branches can still finish; only dependent
                # outputs are blocked, so a failed shot doesn't strand the canvas.
                if not ready_ids:
                    _set_run(run_id, status="failed", error=f"{failed.node_id}：{failed.error}")
                    return
            if all_succeeded:
                break
            if not ready_ids:
                raise RuntimeError("Agent 任务依赖无法继续执行")

            limit = 1 if legacy_serial else min(settings.agent_run_concurrency, len(ready_ids))
            results = await asyncio.gather(
                *(_execute_task(run_id, node_id, turn_id) for node_id in ready_ids[:limit])
            )
            if not all(results):
                continue

        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            snapshot = (run.input_snapshot or {}).get("plan", {}) if run else {}
            turn = db.get(AgentTurn, run.turn_id) if run else None
            plan = snapshot or (turn.plan if turn else {}) or {}
        output_node = str(plan.get("output_node", ""))
        output = _task_output(run_id, output_node).name if output_node else ""
        _set_run(run_id, status="succeeded", output_file=output, error="")
        _push_turn_task_event(
            turn_id, "__run__", "succeeded",
            video_src=format_video_src(output), run_id=run_id,
        )
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            turn = db.get(AgentTurn, run.turn_id) if run else None
            if turn is not None:
                turn.status = "executed"
                db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent 执行异常：run=%s", run_id)
        _set_run(run_id, status="failed", error=str(exc))
    finally:
        _runs.discard(run_id)


def spawn_run(run_id: str) -> None:
    coro = execute_run(run_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        task = loop.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return
    if _loop is None:
        coro.close()
        _set_run(run_id, status="failed", error="服务尚未就绪，无法启动 Agent 执行")
        return
    asyncio.run_coroutine_threadsafe(coro, _loop)


async def resume_runs() -> None:
    with SessionLocal() as db:
        ids = [
            row.id for row in db.query(AgentRun)
            .filter(AgentRun.status.in_(["queued", "running"]))
            .all()
        ]
    for run_id in ids:
        spawn_run(run_id)
