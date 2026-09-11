"""Constrained, deterministic planning for the creative agent.

The planner intentionally produces only existing GenerateRequest-shaped values.  A
future LLM adapter may choose a skill and fill these fields, but it never receives
authority to execute a provider request or to mint arbitrary canvas nodes.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .catalog import I2V, R2V, T2V, can_use, get_model, models_for_role
from .models import AgentTurn, Upload, User
from .routers.conversations import _validate
from .schemas import GenerateRequest


def _price(model_id: str, resolution: str) -> float:
    values: dict[str, float] = {}
    from .config import settings
    for entry in settings.agent_price_table.split(","):
        key, sep, raw = entry.rpartition("=")
        if sep:
            try:
                values[key.strip()] = float(raw.strip())
            except ValueError:
                pass
    return values.get(f"{model_id}:{resolution}", settings.agent_price_default)


def estimate(plan: dict) -> tuple[float, int]:
    total_cost = 0.0
    total_seconds = 0
    for item in plan.get("generations", []):
        duration = int(item.get("duration", 0))
        total_seconds += duration
        total_cost += _price(str(item.get("model", "")), str(item.get("resolution", ""))) * duration
    return round(total_cost, 2), total_seconds


def create_plan(user: User, text: str, media: list[dict], surface: str) -> tuple[str, dict]:
    wants_reference = bool(media) or any(word in text for word in ("参考图", "角色一致", "多角度"))
    wants_image = bool(media) and all(item.get("kind") == "image" for item in media)
    capability = R2V if wants_reference and len(media) > 1 else I2V if wants_image else T2V
    candidates = [m for m in models_for_role(user.role) if m.capability(capability)]
    if not candidates:
        raise HTTPException(status_code=400, detail="当前账号没有可用于该创作请求的模型")
    model = candidates[0]
    cap = model.capability(capability)
    assert cap is not None
    item = {
        "prompt": text.strip(), "model": model.id, "capability": capability,
        "resolution": model.default_resolution, "ratio": model.default_ratio_for(capability),
        "duration": model.default_duration, "reference_media": media,
        "reason": f"已选择 {model.label}，它支持所需的{cap.label}",
    }
    skill = {T2V: "one-line-video", I2V: "animate-image", R2V: "consistent-reference"}[capability]
    return skill, {"generations": [item], "reason": item["reason"]}


def validate_plan(db: Session, user: User, request: Request, plan: dict) -> tuple[float, int]:
    generations = plan.get("generations")
    if not isinstance(generations, list) or not 1 <= len(generations) <= 6:
        raise HTTPException(status_code=400, detail="计划必须包含 1-6 个生成节点")
    for item in generations:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="计划节点格式无效")
        try:
            payload = GenerateRequest(
                prompt=str(item.get("prompt", "")), model=str(item.get("model", "")),
                capability=str(item.get("capability", "t2v")), resolution=str(item.get("resolution", "")),
                ratio=str(item.get("ratio", "")), duration=int(item.get("duration", 0)),
                reference_media=item.get("reference_media", []), use_context=False,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="计划节点参数无效") from exc
        _validate(payload, user, request, db)
    return estimate(plan)


def expire_at() -> datetime:
    from .config import settings
    return datetime.now(timezone.utc) + timedelta(minutes=settings.agent_plan_ttl_minutes)
