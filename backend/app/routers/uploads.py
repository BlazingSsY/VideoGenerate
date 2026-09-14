import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..media_links import sign_path
from ..models import Asset, Upload, User
from ..schemas import UploadOut
from ..security import current_user

router = APIRouter(prefix="/api", tags=["uploads"])

EXTENSION_KIND = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
    ".bmp": "image",
    ".mp4": "video",
    ".mov": "video",
    ".mp3": "audio",
    ".wav": "audio",
}
ALLOWED_SUFFIX = set(EXTENSION_KIND)
CHUNK = 1024 * 1024


def _sniff(content: bytes) -> str | None:
    """按文件头判断真实格式，不信任扩展名。"""
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"BM"):
        return ".bmp"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return ".gif"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return ".mp4"
    if content.startswith(b"ID3") or (
        len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0
    ):
        return ".mp3"
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return ".wav"
    return None


def absolute_url(request: Request, path: str) -> str:
    if settings.public_base_url:
        return f"{settings.public_base_url}{path}"
    return str(request.base_url).rstrip("/") + path


@router.post("/uploads", response_model=UploadOut)
def upload_media(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(
            status_code=400, detail=f"不支持该素材格式，可用：{', '.join(sorted(ALLOWED_SUFFIX))}"
        )

    kind = EXTENSION_KIND[suffix]
    limit_mb = min(settings.max_upload_mb, 15) if kind == "audio" else settings.max_upload_mb
    limit = limit_mb * 1024 * 1024

    # 先看 Content-Length，明显超限的请求不必读进来
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + CHUNK:
        raise HTTPException(status_code=413, detail=f"素材超过 {limit_mb}MB 限制")

    # 分块读取并随时截断，避免超大文件被整个读进内存
    parts: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=f"素材超过 {limit_mb}MB 限制")
        parts.append(chunk)

    content = b"".join(parts)
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    # 校验真实内容，挡掉「HTML 改名成媒体文件」这类上传。
    detected_suffix = _sniff(content)
    if detected_suffix is None:
        raise HTTPException(status_code=400, detail="文件内容不是有效的图片、视频或音频")
    detected_kind = EXTENSION_KIND.get(detected_suffix)
    image_extension_mismatch = kind == "image" and not (
        detected_suffix == suffix or {detected_suffix, suffix} <= {".jpg", ".jpeg"}
    )
    if detected_kind != kind or image_extension_mismatch:
        raise HTTPException(
            status_code=400,
            detail=f"扩展名与实际内容不符：内容是 {detected_suffix}，文件名是 {suffix}",
        )

    name = f"{uuid.uuid4().hex}{suffix}"
    (settings.upload_dir / name).write_bytes(content)
    # 文件名不可当作授权；生成前会依据这条记录验证归属。
    # 先落库，失败时删掉刚写入的文件，避免产生不可管理的孤儿文件。
    filename_only = Path(file.filename or "material").stem or "material"
    try:
        db.add(Upload(filename=name, user_id=user.id, kind=kind))
        db.add(Asset(user_id=user.id, name=filename_only, kind=kind, filename=name))
        db.commit()
    except Exception:
        db.rollback()
        (settings.upload_dir / name).unlink(missing_ok=True)
        raise
    path = f"/media/uploads/{name}"
    # absolute_url 仅供参考/调试；提交生成时用的是相对路径，由后端决定公网 URL 还是 Base64
    return UploadOut(
        url=path,
        preview_url=sign_path(path),
        absolute_url=path,
        filename=name,
        kind=kind,
    )
