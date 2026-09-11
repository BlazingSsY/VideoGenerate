from fastapi import APIRouter, Depends

from ..catalog import MODE_LABELS, capability_matrix, models_for_role
from ..config import settings
from ..media_resolver import public_base_url_usable
from ..models import User
from ..security import current_user

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/models")
def list_models(user: User = Depends(current_user)):
    return {
        "max_duration": settings.max_duration,
        "mode_labels": MODE_LABELS,
        "models": [m.to_dict() for m in models_for_role(user.role)],
    }


@router.get("/models/matrix")
def models_matrix(user: User = Depends(current_user)):
    """全部模型的能力对照表（含当前账号无权使用的），用于界面上的能力说明。"""
    allowed = {m.id for m in models_for_role(user.role)}
    return {
        "mode_labels": MODE_LABELS,
        "rows": [{**row, "allowed": row["id"] in allowed} for row in capability_matrix()],
    }


@router.get("/config")
def app_config(user: User = Depends(current_user)):
    return {
        "app_name": settings.app_name,
        "max_duration": settings.max_duration,
        "max_upload_mb": settings.max_upload_mb,
        "context_enabled": settings.context_enabled,
        "public_base_url_configured": bool(settings.public_base_url),
        "public_base_url_usable": public_base_url_usable(),
    }
