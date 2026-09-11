from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from ..agent_provider import AgentError, public_models, resolve_model
from ..agent_service import AGENT_SYSTEM_PROMPT
from ..agent_provider import plan as provider_plan
from ..agent_service import create_plan, estimate, expire_at, validate_plan
from ..config import settings
from ..database import get_db
from ..agent_executor import spawn_run
from ..media_links import sign_path
from ..models import AgentRun, AgentSession, AgentTask, AgentTurn, Message, User
from ..schemas import AgentAcceptRequest, AgentPlanRequest, AgentRunOut, AgentTurnOut
from ..security import current_user

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _out(turn: AgentTurn, surface: str, target_id: str) -> dict:
    return {
        "id": turn.id, "surface": surface, "target_id": target_id,
        "status": turn.status, "skill_id": turn.skill_id, "plan": turn.plan,
        "est_cost": turn.est_cost, "est_seconds": turn.est_seconds,
        "expires_at": turn.expires_at, "created_at": turn.created_at,
        "accepted_at": turn.accepted_at,
        "agent_model_id": turn.agent_model_id, "warning": turn.warning,
        "tokens_in": turn.tokens_in, "tokens_out": turn.tokens_out,
        "repair_count": turn.repair_count,
    }


def _run_out(db: Session, run: AgentRun) -> dict:
    tasks = db.query(AgentTask).filter(AgentTask.run_id == run.id).order_by(AgentTask.created_at).all()
    values = []
    for task in tasks:
        message = db.get(Message, task.message_id) if task.message_id else None
        file_name = task.output_file or (message.local_video if message else "")
        values.append({
            "node_id": task.node_id,
            "task_type": task.task_type,
            "status": message.status if message and task.task_type == "generate" else task.status,
            "depends_on": task.depends_on or [],
            "message_id": task.message_id,
            "output_file": file_name,
            "video_src": sign_path(f"/media/videos/{file_name}") if file_name else "",
            "error": message.error if message and message.status == "failed" else task.error,
        })
    return {
        "id": run.id,
        "turn_id": run.turn_id,
        "status": run.status,
        "output_file": run.output_file,
        "video_src": sign_path(f"/media/videos/{run.output_file}") if run.output_file else "",
        "error": run.error,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "tasks": values,
    }


@router.get("/models")
def models(user: User = Depends(current_user)):
    del user
    return public_models()


@router.post("/plans", response_model=AgentTurnOut, status_code=201)
def plan(payload: AgentPlanRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="智能体暂时不可用")
    try:
        provider_configured = settings.agent_provider_configured
        selected_agent_model_id = payload.agent_model_id
        if provider_configured or payload.agent_model_id:
            selected_agent_model_id = resolve_model(payload.agent_model_id).id
        skill_id, value, provider_result, warning = create_plan(
            user, payload.user_input, [item.model_dump() for item in payload.reference_media],
            payload.surface, selected_agent_model_id, payload.target_duration,
        )
        if provider_result:
            # Reject malformed provider output before it becomes a persisted draft.
            try:
                validate_plan(db, user, request, value)
            except HTTPException as first_error:
                if provider_result is None or settings.agent_max_repair_attempts <= 0:
                    raise first_error
                repair_prompt = (
                    f"原计划未通过后端校验：{first_error.detail}。请修正后只输出完整 JSON 计划。\n"
                    f"原始用户目标：{payload.user_input}"
                )
                repaired = provider_plan(
                    selected_agent_model_id,
                    AGENT_SYSTEM_PROMPT,
                    repair_prompt,
                )
                value = repaired.value
                validate_plan(db, user, request, value)
                provider_result = type(provider_result)(
                    value=value,
                    tokens_in=provider_result.tokens_in + repaired.tokens_in,
                    tokens_out=provider_result.tokens_out + repaired.tokens_out,
                )
                warning = "Agent 计划经过一次后端校验修复"
    except AgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    cost, seconds = estimate(value)
    session = AgentSession(user_id=user.id, surface=payload.surface, target_id=payload.target_id)
    db.add(session); db.flush()
    turn = AgentTurn(
        session_id=session.id, user_input=payload.user_input, skill_id=skill_id,
        agent_model_id=selected_agent_model_id or (resolve_model(None).id if provider_result else ""),
        plan=value, est_cost=cost, est_seconds=seconds, expires_at=expire_at(),
        tokens_in=provider_result.tokens_in if provider_result else 0,
        tokens_out=provider_result.tokens_out if provider_result else 0,
        warning=warning,
        repair_count=1 if warning else 0,
    )
    db.add(turn); db.commit(); db.refresh(turn)
    return _out(turn, payload.surface, payload.target_id)


@router.post("/plans/{turn_id}/accept", response_model=AgentTurnOut)
def accept(turn_id: str, request: Request, payload: AgentAcceptRequest | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    turn = db.get(AgentTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    session = db.get(AgentSession, turn.session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="计划不存在")
    if turn.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        db.execute(update(AgentTurn).where(AgentTurn.id == turn.id, AgentTurn.status == "draft").values(status="expired")); db.commit()
        raise HTTPException(status_code=410, detail="计划已过期，请重新生成")
    plan = payload.plan if payload and payload.plan is not None else turn.plan
    cost, seconds = validate_plan(db, user, request, plan)
    today = datetime.now(timezone.utc).date()
    spent = (
        db.query(func.coalesce(func.sum(AgentTurn.est_cost), 0.0))
        .join(AgentSession, AgentTurn.session_id == AgentSession.id)
        .filter(
            AgentSession.user_id == user.id,
            AgentTurn.status.in_(["accepted", "executed"]),
            AgentTurn.accepted_at >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
        )
        .scalar()
    )
    if float(spent) + cost > settings.agent_daily_cost_limit:
        raise HTTPException(status_code=429, detail="该计划超过每日智能体成本上限")
    changed = db.execute(update(AgentTurn).where(AgentTurn.id == turn.id, AgentTurn.status == "draft").values(status="accepted", plan=plan, est_cost=cost, est_seconds=seconds, accepted_at=datetime.now(timezone.utc))).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="这个计划已经被处理，不能重复接受")
    db.commit(); db.refresh(turn)
    if payload and payload.execute:
        run = AgentRun(turn_id=turn.id, user_id=user.id, status="queued")
        db.add(run); db.flush()
        for node in plan.get("nodes", []):
            db.add(AgentTask(
                run_id=run.id,
                node_id=str(node["id"]),
                task_type=str(node["type"]),
                status="queued",
                depends_on=[str(item) for item in node.get("depends_on", [])],
            ))
        db.commit(); db.refresh(run)
        spawn_run(run.id)
    return _out(turn, session.surface, session.target_id)


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def get_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = db.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=404, detail="执行批次不存在")
    return _run_out(db, run)


@router.get("/plans/{turn_id}/run", response_model=AgentRunOut)
def get_plan_run(turn_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = (
        db.query(AgentRun)
        .filter(AgentRun.turn_id == turn_id, AgentRun.user_id == user.id)
        .order_by(AgentRun.created_at.desc())
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="该计划尚未执行")
    return _run_out(db, run)


@router.post("/runs/{run_id}/tasks/{node_id}/retry", response_model=AgentRunOut)
def retry_task(run_id: str, node_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = db.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=404, detail="执行批次不存在")
    task = db.query(AgentTask).filter_by(run_id=run.id, node_id=node_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="子任务不存在")
    if task.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败的子任务可以重试")
    task.status = "queued"
    task.error = ""
    task.output_file = ""
    if task.task_type == "generate":
        task.message_id = None
    run.status = "queued"
    run.error = ""
    run.output_file = ""
    db.commit()
    spawn_run(run.id)
    db.refresh(run)
    return _run_out(db, run)


@router.post("/plans/{turn_id}/reject", response_model=AgentTurnOut)
def reject(turn_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    turn = db.get(AgentTurn, turn_id); session = db.get(AgentSession, turn.session_id) if turn else None
    if turn is None or session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="计划不存在")
    if db.execute(update(AgentTurn).where(AgentTurn.id == turn.id, AgentTurn.status == "draft").values(status="rejected")).rowcount != 1:
        db.rollback(); raise HTTPException(status_code=409, detail="这个计划已经被处理")
    db.commit(); db.refresh(turn)
    return _out(turn, session.surface, session.target_id)
