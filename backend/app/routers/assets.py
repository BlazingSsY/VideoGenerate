"""v3 素材库：用户素材的登记、检索、外链补录与删除。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..media_links import sign_path
from ..models import Asset, Upload, User
from ..schemas import AssetCreate, AssetOut, AssetUpdate
from ..security import current_user

router = APIRouter(prefix="/api/assets", tags=["assets"])

KINDS = ("image", "video", "audio")


def _out(a: Asset) -> AssetOut:
    file_url = a.source_url or (f"/media/uploads/{a.filename}" if a.filename else "")
    preview_url = ""
    if a.filename:
        preview_url = sign_path(f"/media/uploads/{a.filename}")
    elif a.source_url:
        preview_url = a.source_url
    return AssetOut(
        id=a.id, name=a.name, kind=a.kind, category=a.category,
        description=a.description, file_url=file_url,
        preview_url=preview_url, source_url=a.source_url or "",
        created_at=a.created_at,
    )


@router.get("", response_model=list[AssetOut])
def list_assets(
    kind: str | None = Query(default=None),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Asset).filter(Asset.user_id == user.id)
    if kind:
        if kind not in KINDS:
            raise HTTPException(status_code=400, detail=f"kind 只能是 {'/'.join(KINDS)}")
        query = query.filter(Asset.kind == kind)
    if category:
        query = query.filter(Asset.category == category)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Asset.name.ilike(like), Asset.description.ilike(like)))
    rows = query.order_by(Asset.created_at.desc()).limit(200).all()
    return [_out(a) for a in rows]




def _validate_file_url(db: Session, user: User, file_url: str, source_url: str) -> tuple[str, str]:
    """返回 (filename, source_url)。库内文件必须真实存在且归属本用户。"""
    if file_url:
        prefix = "/media/uploads/"
        if not file_url.startswith(prefix):
            raise HTTPException(status_code=400, detail="file_url 必须以 /media/uploads/ 开头")
        filename = file_url[len(prefix):]
        if "/" in filename or ".." in filename or not filename:
            raise HTTPException(status_code=400, detail="file_url 不是有效的素材地址")
        upload = db.query(Upload).filter(
            Upload.filename == filename, Upload.user_id == user.id).first()
        if upload is None:
            raise HTTPException(status_code=404, detail="素材文件不存在或不属于当前用户")
        return filename, ""
    if source_url:
        return "", source_url
    raise HTTPException(status_code=400, detail="file_url 与 source_url 至少填一个")


@router.post("", response_model=AssetOut, status_code=201)
def register_asset(
    create: AssetCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    filename, source_url = _validate_file_url(db, user, create.file_url, create.source_url)
    asset = Asset(
        user_id=user.id, name=create.name,
        kind=create.kind, category=create.category,
        description=create.description,
        filename=filename, source_url=source_url,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return _out(asset)


@router.patch("/{asset_id}", response_model=AssetOut)
def update_asset(
    asset_id: str,
    update: AssetUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.user_id == user.id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    if update.name is not None:
        asset.name = update.name
    if update.category is not None:
        asset.category = update.category
    if update.description is not None:
        asset.description = update.description
    db.commit()
    db.refresh(asset)
    return _out(asset)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(
    asset_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    asset = db.query(Asset).filter(Asset.id == asset_id, Asset.user_id == user.id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    db.delete(asset)
    db.commit()
    # 库内文件的物理文件与 Upload 记录保留：同一文件可能还挂在本用户其他 Asset 上，
    # 生成与占盘清理由 uploads 侧负责。
