import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..agent_provider import AgentError, public_models, resolve_model
from ..agent_service import expire_at
from ..agent_chat import orchestrate_turn, get_event_queue
from ..agent_tools import TOOL_REGISTRY
from ..agent_run_service import (
    AgentRunError,
    accept_plan,
    request_cancel,
    retry_failed_run,
    run_out,
    subscribe_run,
    unsubscribe_run,
)
from ..canvas_service import CanvasServiceError, import_plan_to_canvas
from ..config import settings
from ..database import get_db, SessionLocal
from ..agent_executor import spawn_run
from ..models import (
    AgentChatMessage, AgentRun, AgentRunEvent, AgentSession, AgentStep, AgentTurn,
    Canvas, PromptSkill, User,
)
from ..schemas import (
    AgentTurnOut,
    AgentTurnCreate, AgentChatMessageOut, PromptSkillV3Out,
)
from ..security import current_user
from ..ratelimit import retry_after, record_failure

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _out(turn: AgentTurn, surface: str, target_id: str, run_id: str | None = None) -> dict:
    return {
        "id": turn.id, "surface": surface, "target_id": target_id,
        "status": turn.status, "skill_id": turn.skill_id, "plan": turn.plan,
        "est_cost": turn.est_cost, "est_seconds": turn.est_seconds,
        "expires_at": turn.expires_at, "created_at": turn.created_at,
        "accepted_at": turn.accepted_at,
        "agent_model_id": turn.agent_model_id, "warning": turn.warning,
        "tokens_in": turn.tokens_in, "tokens_out": turn.tokens_out,
        "repair_count": turn.repair_count,
        "run_id": run_id,
    }


# ── v1.5 兼容端点（保留） ─────────────────────────────────────

@router.get("/models")
def models(user: User = Depends(current_user)):
    del user
    return public_models()


# ── v3: 对话式智能体端点 ──────────────────────────────────────

@router.get("/tools")
def tools(user: User = Depends(current_user)):
    del user
    return TOOL_REGISTRY


@router.post("/turns", status_code=201)
async def create_turn(
    payload: AgentTurnCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="智能体暂时不可用")
    # §4.3：未配置 Provider 且禁止规则降级 → 直接 503
    if not settings.agent_provider_configured and not settings.agent_fallback_rules:
        raise HTTPException(
            status_code=503,
            detail="智能体未配置：请设置 AGENT_MODELS_JSON / AGENT_BASE_URL + AGENT_API_KEY，"
                   "或开启 AGENT_FALLBACK_RULES 允许规则降级",
        )
    # Rate limit: AGENT_RATE_PER_MINUTE per user
    rate_key = f"agent:{user.id}"
    wait = retry_after([rate_key])
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"请求过于频繁，请 {wait} 秒后再试")
    record_failure([rate_key])

    target_canvas = None
    if payload.surface == "canvas":
        if not payload.target_id:
            raise HTTPException(status_code=400, detail="请先选择或创建画布，再让智能体开始制作")
        target_canvas = db.get(Canvas, payload.target_id)
        if target_canvas is None or target_canvas.user_id != user.id:
            raise HTTPException(status_code=404, detail="目标画布不存在")

    session_id = payload.session_id
    if session_id:
        session = db.get(AgentSession, session_id)
        if (
            session is None
            or session.user_id != user.id
            or session.surface != payload.surface
            or session.target_id != payload.target_id
        ):
            session_id = None
    if not session_id:
        session = AgentSession(
            user_id=user.id, surface=payload.surface, target_id=payload.target_id
        )
        db.add(session); db.flush()
        session_id = session.id

    selected_model = ""
    if settings.agent_provider_configured or payload.agent_model_id:
        try:
            selected_model = resolve_model(payload.agent_model_id).id
        except AgentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    # 当日 token 限额（用户级）：S1/S4 之前拦截
    if settings.agent_daily_token_limit > 0:
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        used = (
            db.query(func.coalesce(func.sum(AgentTurn.tokens_in + AgentTurn.tokens_out + AgentTurn.reasoning_tokens), 0))
            .join(AgentSession, AgentTurn.session_id == AgentSession.id)
            .filter(AgentSession.user_id == user.id, AgentTurn.created_at >= day_start)
            .scalar()
        ) or 0
        if used >= settings.agent_daily_token_limit:
            raise HTTPException(
                status_code=429,
                detail=f"今日 token 用量已达上限（{used}/{settings.agent_daily_token_limit}），请明天再试",
            )

    turn = AgentTurn(
        session_id=session_id, user_input=payload.user_input,
        skill_id="", agent_model_id=selected_model,
        plan={}, status="draft", expires_at=expire_at(),
        canvas_control_version=int(target_canvas.control_version or 0) if target_canvas else 0,
    )
    db.add(turn); db.commit(); db.refresh(turn)

    asyncio.create_task(orchestrate_turn(
        turn.id, user.id, payload.user_input, user.role,
        payload.surface, payload.target_id, selected_model or None,
        [item.model_dump() for item in payload.reference_media],
        payload.target_duration, payload.autonomy,
    ))

    return {"id": turn.id, "session_id": session_id, "status": turn.status}


@router.get("/turns/{turn_id}")
def get_turn(turn_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    turn = db.get(AgentTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="轮次不存在")
    session = db.get(AgentSession, turn.session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="轮次不存在")
    steps = (
        db.query(AgentStep)
        .filter(AgentStep.turn_id == turn_id)
        .order_by(AgentStep.seq)
        .all()
    )
    return {
        "id": turn.id, "session_id": turn.session_id,
        "user_input": turn.user_input, "intent": turn.intent,
        "status": turn.status, "skill_id": turn.skill_id,
        "agent_model_id": turn.agent_model_id, "plan": turn.plan,
        "est_cost": turn.est_cost, "est_seconds": turn.est_seconds,
        "expires_at": turn.expires_at, "created_at": turn.created_at,
        "accepted_at": turn.accepted_at, "tokens_in": turn.tokens_in,
        "tokens_out": turn.tokens_out, "reasoning_tokens": turn.reasoning_tokens,
        "repair_count": turn.repair_count, "warning": turn.warning,
        "tool_call_count": turn.tool_call_count,
        "steps": [
            {"seq": s.seq, "kind": s.kind, "title": s.title,
             "payload": s.payload, "status": s.status,
             "tokens_in": s.tokens_in, "tokens_out": s.tokens_out,
             "created_at": s.created_at.isoformat() if s.created_at else ""}
            for s in steps
        ],
    }


@router.get("/turns/{turn_id}/events")
async def turn_events(
    turn_id: str,
    from_seq: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    turn = db.get(AgentTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="轮次不存在")
    session = db.get(AgentSession, turn.session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="轮次不存在")

    async def event_stream():
        with SessionLocal() as replay_db:
            steps = (
                replay_db.query(AgentStep)
                .filter(AgentStep.turn_id == turn_id, AgentStep.seq > from_seq)
                .order_by(AgentStep.seq)
                .all()
            )
            for step in steps:
                replay_data = json.dumps({
                    "seq": step.seq, "kind": step.kind, "title": step.title,
                    "payload": step.payload, "status": step.status,
                }, ensure_ascii=False)
                yield f"event: step.replay\ndata: {replay_data}\n\n"

        queue = get_event_queue(turn_id)
        while True:
            # The producer discards its in-memory queue after completion. A
            # delayed first connection or reconnect must recover from the DB,
            # rather than wait forever on a fresh, empty queue.
            if queue.empty():
                with SessionLocal() as snapshot_db:
                    current = snapshot_db.get(AgentTurn, turn_id)
                    if current is not None and current.status != "draft":
                        snapshot = {
                            "messages": session_messages(current.session_id, snapshot_db, user),
                            "runs": session_runs(current.session_id, snapshot_db, user),
                        }
                        encoded = json.dumps(jsonable_encoder(snapshot), ensure_ascii=False)
                        yield f"event: session.snapshot\ndata: {encoded}\n\n"
                        yield f"event: done\ndata: {{\"turn_id\": \"{turn_id}\"}}\n\n"
                        return
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=min(2.0, float(settings.agent_stream_idle_timeout)),
                )
                event_type = event.get("event", "message")
                event_data_raw = event.get("data", {})
                # 断线重连去重：from_seq 之前的事件（含 replay 阶段已推过的）不再下发
                event_seq = event_data_raw.get("seq") if isinstance(event_data_raw, dict) else None
                if isinstance(event_seq, int) and event_seq <= from_seq:
                    continue
                event_data = json.dumps(event_data_raw, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {event_data}\n\n"
                if event_type in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield f"event: ping\ndata: {{}}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/sessions")
def list_sessions(
    surface: str = "canvas",
    target_id: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    # Scope in SQL: a history entry must belong to this user and this canvas.
    first_input = (
        db.query(AgentTurn.user_input)
        .filter(AgentTurn.session_id == AgentSession.id)
        .order_by(AgentTurn.created_at, AgentTurn.id)
        .limit(1).correlate(AgentSession).scalar_subquery()
    )
    latest_id = (
        db.query(AgentTurn.id)
        .filter(AgentTurn.session_id == AgentSession.id)
        .order_by(AgentTurn.created_at.desc(), AgentTurn.id.desc())
        .limit(1).correlate(AgentSession).scalar_subquery()
    )
    rows = (
        db.query(AgentSession, AgentTurn, first_input.label("title"))
        .join(AgentTurn, AgentTurn.id == latest_id)
        .filter(AgentSession.user_id == user.id, AgentSession.surface == surface, AgentSession.target_id == target_id)
        .order_by(AgentTurn.created_at.desc(), AgentSession.id)
        .all()
    )
    return [
        {"id": session.id, "title": (title or "新对话")[:100],
         "updated_at": turn.created_at, "latest_turn_id": turn.id,
         "latest_turn_status": turn.status, "latest_user_input": turn.user_input}
        for session, turn, title in rows
    ]


@router.get("/sessions/{session_id}/messages", response_model=list[AgentChatMessageOut])
def session_messages(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    session = db.get(AgentSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = (
        db.query(AgentChatMessage)
        .filter(AgentChatMessage.session_id == session_id)
        .order_by(AgentChatMessage.created_at)
        .all()
    )
    turn_ids = {message.turn_id for message in messages if message.turn_id}
    turns = {
        turn.id: turn for turn in (
            db.query(AgentTurn).filter(AgentTurn.id.in_(turn_ids)).all()
            if turn_ids else []
        )
    }
    return [
        {
            "id": message.id,
            "session_id": message.session_id,
            "turn_id": message.turn_id,
            "role": message.role,
            "content": message.content,
            "plan": message.plan,
            "plan_validated": bool(
                message.role == "assistant"
                and message.plan
                and turns.get(message.turn_id)
                and turns[message.turn_id].plan == message.plan
            ),
            "turn_status": turns[message.turn_id].status if message.turn_id in turns else "",
            "est_cost": turns[message.turn_id].est_cost if message.turn_id in turns else 0.0,
            "est_seconds": turns[message.turn_id].est_seconds if message.turn_id in turns else 0,
            "warning": turns[message.turn_id].warning if message.turn_id in turns else "",
            "tokens_in": message.tokens_in,
            "tokens_out": message.tokens_out,
            "created_at": message.created_at,
        }
        for message in messages
    ]


@router.post("/turns/{turn_id}/plan/accept", response_model=AgentTurnOut)
def accept_turn_plan(
    turn_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    turn = db.get(AgentTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="轮次不存在")
    session = db.get(AgentSession, turn.session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="轮次不存在")
    try:
        run, created = accept_plan(db, turn, user, request)
    except AgentRunError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if not created:
        raise HTTPException(status_code=409, detail=f"这个计划已经在运行（run_id={run.id}）")
    db.refresh(turn)
    spawn_run(run.id)
    return _out(turn, session.surface, session.target_id, run.id)



@router.post("/turns/{turn_id}/plan/to-canvas")
def plan_to_canvas(
    turn_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """把智能体产出的计划转为画布节点（提示词→prompt，generate→generate，输出→output + 连线）。"""
    turn = db.get(AgentTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="轮次不存在")
    session = db.get(AgentSession, turn.session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="轮次不存在")
    canvas_id = session.target_id
    canvas = db.get(Canvas, canvas_id) if canvas_id else None
    if canvas is None or canvas.user_id != user.id:
        raise HTTPException(status_code=404, detail="画布不存在，请先在画布页面创建一个画布")
    if int(turn.canvas_control_version or 0) != int(canvas.control_version or 0):
        raise HTTPException(status_code=409, detail="该轮次已因人工接手失效，请发起新指令")
    try:
        imported, added = import_plan_to_canvas(db, turn, canvas, user)
    except CanvasServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    # Return the authoritative graph so the caller can refresh immediately.
    from .canvas import _detail
    return {
        "canvas_id": canvas_id,
        "added": added,
        "revision": imported.canvas_revision,
        "node_map": imported.node_map,
        "canvas": _detail(canvas),
    }


def _own_run(db: Session, run_id: str, user: User) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=404, detail="运行不存在")
    return run


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return run_out(db, _own_run(db, run_id, user))


@router.get("/sessions/{session_id}/runs")
def session_runs(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    session = db.get(AgentSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = (
        db.query(AgentRun)
        .join(AgentTurn, AgentRun.turn_id == AgentTurn.id)
        .filter(AgentTurn.session_id == session_id)
        .order_by(AgentRun.created_at.desc())
        .all()
    )
    return [run_out(db, row) for row in rows]


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    from_seq: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _own_run(db, run_id, user)

    async def event_stream():
        cursor = max(0, from_seq)
        queue = subscribe_run(run_id)
        terminal = {"succeeded", "failed", "canceled"}
        try:
            while True:
                with SessionLocal() as replay_db:
                    rows = (
                        replay_db.query(AgentRunEvent)
                        .filter(AgentRunEvent.run_id == run_id, AgentRunEvent.seq > cursor)
                        .order_by(AgentRunEvent.seq)
                        .all()
                    )
                    for row in rows:
                        cursor = row.seq
                        payload = dict(row.payload or {})
                        output_file = str(payload.get("output_file") or "")
                        if output_file:
                            from ..media_links import format_video_src
                            payload["video_src"] = format_video_src(output_file)
                        data = json.dumps(
                            {"seq": row.seq, **payload}, ensure_ascii=False,
                        )
                        yield f"event: {row.kind}\ndata: {data}\n\n"
                    current = replay_db.get(AgentRun, run_id)
                    is_terminal = current is None or current.status in terminal
                if is_terminal:
                    yield f"event: done\ndata: {json.dumps({'seq': cursor, 'run_id': run_id}, ensure_ascii=False)}\n\n"
                    break
                try:
                    await asyncio.wait_for(
                        queue.get(), timeout=float(settings.agent_stream_idle_timeout),
                    )
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            unsubscribe_run(run_id, queue)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    run = _own_run(db, run_id, user)
    request_cancel(db, run)
    db.refresh(run)
    return run_out(db, run)


@router.post("/runs/{run_id}/retry")
def retry_run(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    run = _own_run(db, run_id, user)
    try:
        retry_failed_run(db, run)
    except AgentRunError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    spawn_run(run.id)
    db.refresh(run)
    return run_out(db, run)



@router.get("/skills", response_model=list[PromptSkillV3Out])
def list_skills_v3(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = db.query(PromptSkill)
    if user.role != "admin":
        query = query.filter(PromptSkill.enabled.is_(True))
    return query.order_by(PromptSkill.created_at.desc()).all()
