"""Compile the saved canvas into the existing durable generate/compose scheduler.

Manual canvas runs need no model planning. Their execution turn only records the
user's run command; AgentRun/AgentTask provide snapshots, recovery and events.
"""
from copy import deepcopy
from datetime import datetime, timezone
import shutil

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .agent_run_service import append_run_event
from .canvas_executor import generation_input, output_items
from .canvas_service import canvas_snapshot, graph_input_hash, lock_canvas, plan_fingerprint
from .config import settings
from .models import AgentRun, AgentSession, AgentTask, AgentTurn, Canvas, Message, User


def ensure_canvas_available(db: Session, canvas: Canvas) -> None:
    # The caller holds the canvas write lock, shared with graph saves and starts.
    active = db.query(AgentRun).filter(
        AgentRun.canvas_id == canvas.id, AgentRun.status.in_(["queued", "running"]),
    ).first()
    if active:
        raise HTTPException(409, "画布已有任务正在运行，请等待本次完成")
    for node in canvas.nodes:
        message = db.get(Message, node.message_id) if node.message_id else None
        if (message and message.status in {"pending", "running"}) or (
            not message and node.status in {"pending", "running"}
        ):
            raise HTTPException(409, "画布有节点正在运行，请等待本次完成")


def has_local_clip(message: Message) -> bool:
    path = settings.video_dir / message.local_video if message.local_video else None
    return bool(path and path.is_file() and path.stat().st_size > 0)


def reusable_generations(db: Session, canvas: Canvas, frozen_nodes: list[dict]) -> dict[str, Message]:
    """Reuse only attached successes whose effective request still matches.

    Graph saves already detach results after input edits. Comparing the actual
    request also catches an upstream video regenerated with unchanged inputs.
    Dependencies must themselves be reusable before their consumers can be reused.
    """
    by_id = {node.id: node for node in canvas.nodes}
    snapshots = {node['id']: node for node in frozen_nodes}
    reused: dict[str, Message] = {}
    visited: set[str] = set()
    reference_sources = {dep for node in frozen_nodes for dep in node['depends_on']}

    def check(key: str) -> Message | None:
        if key in visited:
            return reused.get(key)
        visited.add(key)
        node = by_id[key]
        frozen = snapshots[key]
        message = db.get(Message, node.message_id) if node.message_id else None
        if (message is None or message.conversation_id != canvas.id or message.status != 'succeeded'
                or message.video_expired or not (has_local_clip(message) or message.video_url)):
            return None
        if key in reference_sources and not message.video_url:
            return None
        dependencies = {dep: check(dep) for dep in frozen['depends_on']}
        if any(value is None for value in dependencies.values()):
            return None
        if message.model != frozen['model'] or message.resolved_prompt != frozen['prompt']:
            return None
        for field in ('capability', 'resolution', 'ratio', 'duration', 'watermark', 'audio'):
            if field in frozen and (message.params or {}).get(field) != frozen[field]:
                return None
        expected = [(item['kind'], dependencies[item['source_node']].video_url if item.get('source_node') else item['url'])
                    for item in frozen['reference_media']]
        actual = [(item.get('kind'), item.get('url')) for item in (message.reference_media or [])]
        if expected != actual:
            return None
        reused[key] = message
        return message

    for key in snapshots:
        check(key)
    return reused


def start_canvas_run(db: Session, canvas: Canvas, user: User, request: Request, revision: int) -> AgentRun:
    lock_canvas(db, canvas, expected_revision=revision)
    ensure_canvas_available(db, canvas)
    generators = [node for node in canvas.nodes if node.type == "generate"]
    outputs = [node for node in canvas.nodes if node.type == "output"]
    if not generators:
        raise HTTPException(400, "画布中没有生成节点")
    if not outputs:
        raise HTTPException(400, "请添加最终输出节点并连接需要拼接的生成节点")
    if not shutil.which("ffmpeg"):
        raise HTTPException(400, "服务器未安装 FFmpeg，无法自动拼接视频")

    graph = canvas_snapshot(canvas)
    # These placeholders are validated as video slots, never sent to a provider.
    # The scheduler resolves them to this run's upstream results after dependencies finish.
    generated_urls = {node.id: f"https://canvas-output.invalid/{node.id}.mp4" for node in generators}
    sources_by_url = {url: key for key, url in generated_urls.items()}
    nodes = []
    for node in generators:
        try:
            frozen = generation_input(db, canvas, node, user, request, generated_urls=generated_urls)
        except HTTPException as exc:
            label = str((node.data or {}).get("name") or node.id)
            raise HTTPException(exc.status_code, f"生成节点「{label}」：{exc.detail}") from exc
        dependencies = []
        for media in frozen["reference_media"]:
            source = sources_by_url.get(media["url"])
            if source:
                media["source_node"] = source
                media["url"] = ""
                dependencies.append(source)
        frozen["depends_on"] = list(dict.fromkeys(dependencies))
        nodes.append(frozen)
    reused = reusable_generations(db, canvas, nodes)
    for node in outputs:
        inputs = [item["nodeKey"] for item in output_items(canvas, node)]
        if not inputs:
            raise HTTPException(400, f"输出节点「{(node.data or {}).get('label') or node.id}」没有可拼接的片段")
        transitions = deepcopy((node.data or {}).get("transitions") or [])
        if any(item.get("type") not in {"none", "fade", "slide", "zoom"} for item in transitions):
            raise HTTPException(400, "输出节点包含不支持的转场")
        nodes.append({"id": node.id, "type": "compose", "inputs": inputs,
                      "depends_on": inputs, "transitions": transitions})
    for node in nodes:
        node["_canvas_input_hash"] = graph_input_hash(graph, node["id"])
    plan = {"title": canvas.title, "nodes": nodes, "output_node": outputs[0].id}
    version = plan_fingerprint(plan)
    now = datetime.now(timezone.utc)
    session = AgentSession(user_id=user.id, surface="canvas", target_id=canvas.id)
    db.add(session)
    db.flush()
    turn = AgentTurn(session_id=session.id, user_input="一键运行画布", intent="canvas_run",
                     plan=deepcopy(plan), plan_version=version, status="accepted",
                     canvas_control_version=canvas.control_version, accepted_at=now, expires_at=now)
    db.add(turn)
    db.flush()
    run = AgentRun(turn_id=turn.id, user_id=user.id, canvas_id=canvas.id,
                   canvas_revision=canvas.revision, plan_version=version, status="queued",
                   input_snapshot={"origin": "canvas", "plan": plan,
                                   "reused_nodes": list(reused),
                                   "node_map": {node["id"]: node["id"] for node in nodes}})
    db.add(run)
    db.flush()
    by_id = {node.id: node for node in canvas.nodes}
    for frozen in nodes:
        cached = reused.get(frozen['id'])
        completed = cached is not None and has_local_clip(cached)
        status = 'succeeded' if completed else 'queued'
        task = AgentTask(run_id=run.id, node_id=frozen["id"], canvas_node_id=frozen["id"],
                         task_type=frozen["type"], depends_on=frozen["depends_on"],
                         input_snapshot=deepcopy(frozen), status=status,
                         message_id=cached.id if cached else None,
                         output_file=cached.local_video if completed else '')
        db.add(task)
        node = by_id[frozen["id"]]
        node.message_id = cached.id if cached else None
        node.status = status
        node.input_hash = version
        node.data = {key: value for key, value in (node.data or {}).items()
                     if key not in {"outputFile", "outputVideoSrc", "outputError"}}
        append_run_event(db, run.id, "task.status", {"node_id": node.id, "canvas_node_id": node.id,
                         "status": status, "reused": cached is not None}, commit=False, broadcast=False)
    append_run_event(db, run.id, "run.status", {"status": "queued", "canvas_id": canvas.id},
                     commit=False, broadcast=False)
    db.commit()
    db.refresh(run)
    return run
