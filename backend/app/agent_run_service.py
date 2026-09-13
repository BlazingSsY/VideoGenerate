"""Accepted-plan transaction, durable run snapshots and replayable events."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from .agent_service import _validated_reference_media, validate_plan
from .canvas_service import (
    CanvasServiceError,
    canvas_snapshot,
    graph_input_hash,
    import_plan_to_canvas,
    imported_plan_inputs_match,
    plan_fingerprint,
)
from .config import settings
from .media_links import format_video_src
from .models import (
    AgentRun,
    AgentRunEvent,
    AgentSession,
    AgentTask,
    AgentTurn,
    Canvas,
    User,
)


class AgentRunError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class AutoApprovalRequired(AgentRunError):
    pass


_subscribers: dict[str, set[asyncio.Queue]] = {}


def _broadcast(run_id: str, event: dict[str, Any]) -> None:
    for queue in list(_subscribers.get(run_id, set())):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


def subscribe_run(run_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers.setdefault(run_id, set()).add(queue)
    return queue


def unsubscribe_run(run_id: str, queue: asyncio.Queue) -> None:
    queues = _subscribers.get(run_id)
    if not queues:
        return
    queues.discard(queue)
    if not queues:
        _subscribers.pop(run_id, None)


def append_run_event(
    db: Session,
    run_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    commit: bool = True,
    broadcast: bool = True,
) -> AgentRunEvent:
    seq = int(
        db.query(func.coalesce(func.max(AgentRunEvent.seq), 0))
        .filter(AgentRunEvent.run_id == run_id)
        .scalar()
        or 0
    ) + 1
    event = AgentRunEvent(run_id=run_id, seq=seq, kind=kind, payload=deepcopy(payload))
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    if broadcast:
        _broadcast(run_id, {"seq": seq, "kind": kind, "payload": deepcopy(payload)})
    return event


def run_out(db: Session, run: AgentRun) -> dict[str, Any]:
    tasks = (
        db.query(AgentTask)
        .filter(AgentTask.run_id == run.id)
        .order_by(AgentTask.created_at, AgentTask.id)
        .all()
    )
    return {
        "id": run.id,
        "turn_id": run.turn_id,
        "canvas_id": run.canvas_id,
        "canvas_revision": run.canvas_revision,
        "plan_version": run.plan_version,
        "status": run.status,
        "cancel_requested": bool(run.cancel_requested),
        "output_file": run.output_file,
        "video_src": format_video_src(run.output_file) if run.output_file else "",
        "error": run.error,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "tasks": [
            {
                "id": task.id,
                "node_id": task.node_id,
                "canvas_node_id": task.canvas_node_id,
                "task_type": task.task_type,
                "status": task.status,
                "depends_on": task.depends_on or [],
                "attempt_count": task.attempt_count,
                "message_id": task.message_id,
                "output_file": task.output_file,
                "video_src": format_video_src(task.output_file) if task.output_file else "",
                "error": task.error,
            }
            for task in tasks
        ],
    }


def _daily_spent(db: Session, user_id: str) -> float:
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    value = (
        db.query(func.coalesce(func.sum(AgentTurn.est_cost), 0.0))
        .join(AgentSession, AgentTurn.session_id == AgentSession.id)
        .filter(
            AgentSession.user_id == user_id,
            AgentTurn.status.in_(["accepted", "executed"]),
            AgentTurn.accepted_at >= day_start,
        )
        .scalar()
    )
    return float(value or 0.0)


def accept_plan(
    db: Session,
    turn: AgentTurn,
    user: User,
    request: Request | None,
    *,
    auto: bool = False,
) -> tuple[AgentRun, bool]:
    """Accept/import/create a run exactly once and commit all local state together."""
    session = db.get(AgentSession, turn.session_id)
    if session is None or session.user_id != user.id:
        raise AgentRunError(404, "轮次不存在")

    existing = db.query(AgentRun).filter(AgentRun.turn_id == turn.id).one_or_none()
    if existing is not None:
        return existing, False
    if not turn.plan:
        raise AgentRunError(400, "该轮次没有可接受的计划")
    expires = turn.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise AgentRunError(410, "计划已过期，请重新生成")

    try:
        cost, seconds = validate_plan(db, user, request, turn.plan)
    except Exception as exc:
        if hasattr(exc, "status_code") and hasattr(exc, "detail"):
            raise AgentRunError(int(exc.status_code), str(exc.detail)) from exc
        raise
    if auto and cost > settings.agent_max_auto_cost:
        raise AutoApprovalRequired(
            409,
            f"预估 ¥{cost:.2f} 超过自动执行上限 ¥{settings.agent_max_auto_cost:.2f}",
        )
    if _daily_spent(db, user.id) + cost > settings.agent_daily_cost_limit:
        raise AgentRunError(429, "该计划超过每日智能体成本上限")

    accepted_at = datetime.now(timezone.utc)
    changed = db.execute(
        update(AgentTurn)
        .where(AgentTurn.id == turn.id, AgentTurn.status.in_(["draft", "answered"]))
        .values(
            status="accepted", est_cost=cost, est_seconds=seconds,
            accepted_at=accepted_at,
        )
    ).rowcount
    if changed != 1:
        db.rollback()
        existing = db.query(AgentRun).filter(AgentRun.turn_id == turn.id).one_or_none()
        if existing is not None:
            return existing, False
        raise AgentRunError(409, "这个计划已经被处理，不能重复接受")

    version = turn.plan_version or plan_fingerprint(turn.plan)
    canvas: Canvas | None = None
    node_map: dict[str, Any] = {}
    canvas_revision = 0
    if session.surface == "canvas":
        if not session.target_id:
            db.rollback()
            raise AgentRunError(400, "当前会话没有绑定画布，请先选择或创建画布")
        canvas = db.get(Canvas, session.target_id)
        if canvas is None or canvas.user_id != user.id:
            db.rollback()
            raise AgentRunError(404, "目标画布不存在")
        if int(turn.canvas_control_version or 0) != int(canvas.control_version or 0):
            db.rollback()
            raise AgentRunError(409, "该轮次已因人工接手失效，请发起新指令")
        try:
            imported, added = import_plan_to_canvas(db, turn, canvas, user, commit=False)
        except CanvasServiceError as exc:
            db.rollback()
            raise AgentRunError(exc.status_code, exc.detail) from exc
        if (
            not added
            and int(canvas.revision or 0) != int(imported.canvas_revision or 0)
            and not imported_plan_inputs_match(db, turn, imported)
        ):
            db.rollback()
            raise AgentRunError(409, "画布中的计划节点已被手动修改，请重新规划或恢复后再执行")
        node_map = deepcopy(imported.node_map or {})
        canvas_revision = int(canvas.revision or 0)

    execution_plan = deepcopy(turn.plan)
    graph = canvas_snapshot(canvas) if canvas else None
    for node in execution_plan.get("nodes", []):
        if node.get("type") == "generate":
            node["reference_media"] = _validated_reference_media(
                db, user, node.get("reference_media") or [], node.get("end_frame"),
            )
            node["end_frame"] = None
        if graph and isinstance(node_map.get(str(node["id"])), str):
            node["_canvas_input_hash"] = graph_input_hash(graph, node_map[str(node["id"])])
    snapshot = {
        "plan": execution_plan,
        "plan_version": version,
        "canvas_id": canvas.id if canvas else None,
        "canvas_revision": canvas_revision,
        "node_map": node_map,
    }
    run = AgentRun(
        turn_id=turn.id, user_id=user.id,
        canvas_id=canvas.id if canvas else None,
        canvas_revision=canvas_revision, plan_version=version,
        input_snapshot=snapshot, status="queued",
    )
    db.add(run)
    db.flush()
    for node in execution_plan.get("nodes", []):
        plan_node_id = str(node["id"])
        mapped = node_map.get(plan_node_id)
        if not isinstance(mapped, str):
            mapped = ""
        dependencies = [str(value) for value in node.get("depends_on", [])]
        if node.get("type") == "compose":
            dependencies = list(dict.fromkeys(
                dependencies + [str(value) for value in node.get("inputs", [])]
            ))
        db.add(AgentTask(
            run_id=run.id, node_id=plan_node_id,
            canvas_node_id=mapped,
            task_type=str(node["type"]), status="queued",
            depends_on=dependencies,
            input_snapshot=deepcopy(node),
        ))
    turn.plan_version = version
    db.flush()
    append_run_event(
        db, run.id, "run.status",
        {"status": "queued", "canvas_id": run.canvas_id},
        commit=False, broadcast=False,
    )
    for task in db.query(AgentTask).filter(AgentTask.run_id == run.id).all():
        append_run_event(
            db, run.id, "task.status",
            {
                "task_id": task.id, "node_id": task.node_id,
                "canvas_node_id": task.canvas_node_id, "status": "queued",
            },
            commit=False, broadcast=False,
        )
    db.commit()
    db.refresh(run)
    # Wake current subscribers after the transaction is visible. Fresh clients replay
    # these rows from the database, so missing this in-memory signal is harmless.
    _broadcast(run.id, {"seq": 0, "kind": "run.available", "payload": {"run_id": run.id}})
    return run, True


def request_cancel(db: Session, run: AgentRun) -> None:
    if run.status in {"succeeded", "failed", "canceled"}:
        return
    run.cancel_requested = True
    for task in db.query(AgentTask).filter_by(run_id=run.id).all():
        if task.status == "queued":
            task.status = "canceled"
            append_run_event(
                db, run.id, "task.status",
                {"task_id": task.id, "node_id": task.node_id, "canvas_node_id": task.canvas_node_id, "status": "canceled"},
                commit=False, broadcast=False,
            )
    append_run_event(db, run.id, "run.cancel_requested", {"status": run.status}, commit=False, broadcast=False)
    db.commit()
    _broadcast(run.id, {"seq": 0, "kind": "run.available", "payload": {"run_id": run.id}})


def retry_failed_run(db: Session, run: AgentRun) -> None:
    if run.status not in {"failed", "canceled"}:
        raise AgentRunError(409, "只有失败或已取消的运行可以重试")
    tasks = db.query(AgentTask).filter_by(run_id=run.id).all()
    failed_ids = {
        task.node_id for task in tasks
        if task.status in {"failed", "canceled", "blocked"}
    }
    if not failed_ids:
        raise AgentRunError(409, "没有可重试的任务")
    exhausted = [
        task.node_id for task in tasks
        if task.node_id in failed_ids and int(task.attempt_count or 0) >= settings.agent_task_max_attempts
    ]
    if exhausted:
        raise AgentRunError(409, f"任务已达到最大尝试次数：{'、'.join(exhausted)}")
    # Reset the failed branch and any downstream dependants; successful independent
    # clips stay reusable and compose will be rebuilt when it depends on a reset node.
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if task.node_id not in failed_ids and any(dep in failed_ids for dep in (task.depends_on or [])):
                failed_ids.add(task.node_id)
                changed = True
    for task in tasks:
        if task.node_id in failed_ids:
            task.status = "queued"
            task.error = ""
            task.output_file = ""
            if task.task_type == "generate" and task.message_id:
                # A retry creates a new provider attempt/message instead of overwriting
                # the old result or failure record.
                task.message_id = None
            append_run_event(
                db, run.id, "task.status",
                {"task_id": task.id, "node_id": task.node_id, "canvas_node_id": task.canvas_node_id, "status": "queued", "retry": True},
                commit=False, broadcast=False,
            )
    run.cancel_requested = False
    run.status = "queued"
    run.error = ""
    run.output_file = ""
    append_run_event(db, run.id, "run.status", {"status": "queued", "retry": True}, commit=False, broadcast=False)
    db.commit()
    _broadcast(run.id, {"seq": 0, "kind": "run.available", "payload": {"run_id": run.id}})
