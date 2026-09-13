from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PromptSkill, User
from ..schemas import PromptSkillCreate, PromptSkillOut, PromptSkillUpdate
from ..security import current_admin, current_user

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _clean_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Skill 名称不能为空")
    return name


@router.get("", response_model=list[PromptSkillOut])
def list_skills(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = db.query(PromptSkill)
    if user.role != "admin":
        query = query.filter(PromptSkill.enabled.is_(True))
    return query.order_by(PromptSkill.created_at.desc()).all()


@router.post("", response_model=PromptSkillOut, status_code=201)
def install_skill(
    payload: PromptSkillCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    skill = PromptSkill(
        name=_clean_name(payload.name),
        description=payload.description.strip(),
        instructions=payload.instructions.strip(),
        enabled=payload.enabled,
        created_by=admin.id,
        requires=payload.requires,
        inputs=payload.inputs,
        plan_shape=payload.plan_shape,
        max_nodes=payload.max_nodes,
    )
    db.add(skill)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="同名 Skill 已存在") from None
    db.refresh(skill)
    return skill


@router.patch("/{skill_id}", response_model=PromptSkillOut)
def update_skill(
    skill_id: str,
    payload: PromptSkillUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(current_admin),
):
    skill = db.get(PromptSkill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    if payload.name is not None:
        skill.name = _clean_name(payload.name)
    if payload.description is not None:
        skill.description = payload.description.strip()
    if payload.instructions is not None:
        skill.instructions = payload.instructions.strip()
    if payload.enabled is not None:
        skill.enabled = payload.enabled
    if payload.requires is not None:
        skill.requires = payload.requires
    if payload.inputs is not None:
        skill.inputs = payload.inputs
    if payload.plan_shape is not None:
        skill.plan_shape = payload.plan_shape
    if payload.max_nodes is not None:
        skill.max_nodes = payload.max_nodes
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="同名 Skill 已存在") from None
    db.refresh(skill)
    return skill


@router.delete("/{skill_id}", status_code=204)
def uninstall_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(current_admin),
):
    skill = db.get(PromptSkill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    db.delete(skill)
    db.commit()
