from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from ..agent_service import create_plan, estimate, expire_at, validate_plan
from ..config import settings
from ..database import get_db
from ..models import AgentSession, AgentTurn, User
from ..schemas import AgentAcceptRequest, AgentPlanRequest, AgentTurnOut
from ..security import current_user

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _out(turn: AgentTurn, surface: str, target_id: str) -> dict:
    return {
        "id": turn.id, "surface": surface, "target_id": target_id,
        "status": turn.status, "skill_id": turn.skill_id, "plan": turn.plan,
        "est_cost": turn.est_cost, "est_seconds": turn.est_seconds,
        "expires_at": turn.expires_at, "created_at": turn.created_at,
        "accepted_at": turn.accepted_at,
    }


@router.post("/plans", response_model=AgentTurnOut, status_code=201)
def plan(payload: AgentPlanRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="智能体暂时不可用")
    skill_id, value = create_plan(user, payload.user_input, [item.model_dump() for item in payload.reference_media], payload.surface)
    cost, seconds = estimate(value)
    session = AgentSession(user_id=user.id, surface=payload.surface, target_id=payload.target_id)
    db.add(session); db.flush()
    turn = AgentTurn(session_id=session.id, user_input=payload.user_input, skill_id=skill_id, plan=value, est_cost=cost, est_seconds=seconds, expires_at=expire_at())
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
    return _out(turn, session.surface, session.target_id)


@router.post("/plans/{turn_id}/reject", response_model=AgentTurnOut)
def reject(turn_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    turn = db.get(AgentTurn, turn_id); session = db.get(AgentSession, turn.session_id) if turn else None
    if turn is None or session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="计划不存在")
    if db.execute(update(AgentTurn).where(AgentTurn.id == turn.id, AgentTurn.status == "draft").values(status="rejected")).rowcount != 1:
        db.rollback(); raise HTTPException(status_code=409, detail="这个计划已经被处理")
    db.commit(); db.refresh(turn)
    return _out(turn, session.surface, session.target_id)
