"""Execute accepted Agent plans as recoverable generate/compose tasks."""
import asyncio
import logging
import shutil
from pathlib import Path

from .config import settings
from .database import SessionLocal
from .models import AgentRun, AgentTask, AgentTurn, Conversation, Message
from .tasks import run_generation

logger = logging.getLogger(__name__)
_runs: set[str] = set()
_tasks: set[asyncio.Task] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _set_run(run_id: str, **fields) -> None:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if run is None:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        db.commit()


def _set_task(run_id: str, node_id: str, **fields) -> None:
    with SessionLocal() as db:
        task = db.query(AgentTask).filter_by(run_id=run_id, node_id=node_id).one_or_none()
        if task is None:
            return
        for key, value in fields.items():
            setattr(task, key, value)
        db.commit()


def _create_message(run_id: str, node: dict) -> str:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        turn = db.get(AgentTurn, run.turn_id) if run else None
        if run is None or turn is None:
            raise RuntimeError("Agent 运行记录不存在")
        session = turn.session_id
        from .models import AgentSession
        agent_session = db.get(AgentSession, session)
        conversation_id = agent_session.target_id if agent_session else ""
        conversation = db.get(Conversation, conversation_id) if conversation_id else None
        if conversation is None:
            conversation = Conversation(
                user_id=run.user_id,
                title=str(turn.plan.get("title") or turn.user_input)[:120] or "Agent 视频任务",
                last_model=str(node.get("model", "")),
                kind="chat",
            )
            db.add(conversation)
            db.flush()
            if agent_session is not None:
                agent_session.target_id = conversation.id
        media = node.get("reference_media") or []
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
                "use_context": False,
                "agent_run_id": run_id,
                "agent_node_id": str(node.get("id", "")),
            },
            reference_images=[item.get("url", "") for item in media if item.get("kind") == "image"],
            reference_media=media,
            status="pending",
        )
        db.add(message)
        db.flush()
        task = db.query(AgentTask).filter_by(run_id=run_id, node_id=str(node["id"])).one()
        task.message_id = message.id
        task.status = "running"
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


async def _compose(run_id: str, node: dict) -> str:
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


async def execute_run(run_id: str) -> None:
    if run_id in _runs:
        return
    _runs.add(run_id)
    try:
        _set_run(run_id, status="running", error="")
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            turn = db.get(AgentTurn, run.turn_id) if run else None
            if turn is None:
                raise RuntimeError("Agent 计划不存在")
            plan = dict(turn.plan or {})
            task_rows = {
                task.node_id: task
                for task in db.query(AgentTask).filter(AgentTask.run_id == run_id).all()
            }
        nodes = {str(node["id"]): node for node in plan.get("nodes", [])}
        completed = {
            node_id for node_id, task in task_rows.items()
            if task.status == "succeeded"
        }
        while len(completed) < len(nodes):
            progressed = False
            for node_id, node in nodes.items():
                if node_id in completed or any(str(dep) not in completed for dep in node.get("depends_on", [])):
                    continue
                progressed = True
                _set_task(run_id, node_id, status="running", error="")
                try:
                    if node.get("type") == "generate":
                        existing = task_rows.get(node_id)
                        message_id = existing.message_id if existing and existing.message_id else _create_message(run_id, node)
                        await run_generation(message_id)
                        with SessionLocal() as db:
                            message = db.get(Message, message_id)
                            if message is None or message.status != "succeeded":
                                raise RuntimeError(message.error if message else "视频生成任务丢失")
                            output = message.local_video
                        _set_task(run_id, node_id, status="succeeded", output_file=output)
                    else:
                        output = await _compose(run_id, node)
                        _set_task(run_id, node_id, status="succeeded", output_file=output)
                    completed.add(node_id)
                except Exception as exc:  # noqa: BLE001
                    _set_task(run_id, node_id, status="failed", error=str(exc))
                    _set_run(run_id, status="failed", error=f"{node_id}：{exc}")
                    return
            if not progressed:
                raise RuntimeError("Agent 任务依赖无法继续执行")
        output_node = str(plan.get("output_node", ""))
        output = _task_output(run_id, output_node).name if output_node else ""
        _set_run(run_id, status="succeeded", output_file=output)
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
        ids = [row.id for row in db.query(AgentRun).filter(AgentRun.status.in_(["queued", "running"])).all()]
    for run_id in ids:
        spawn_run(run_id)
