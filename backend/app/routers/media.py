"""视频下载：本地已落盘的直接返回文件，未落盘的由后端代理转发。

统一带 Content-Disposition: attachment，浏览器会真正保存为文件，
而不是像直接点 DashScope 链接那样在新标签页里打开播放。
"""
import mimetypes
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..media_links import verify
from ..database import get_db
from ..models import Conversation, Message, User
from ..security import current_user

router = APIRouter(prefix="/api/messages", tags=["media"])


def _attachment_headers(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}


@router.get("/{message_id}/download")
async def download_video(
    message_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    conversation = db.get(Conversation, message.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    owner = conversation.user_id == user.id
    admin_chat_access = user.role == "admin" and conversation.kind == "chat"
    if not owner and not admin_chat_access:
        raise HTTPException(status_code=404, detail="记录不存在")
    if message.status != "succeeded":
        raise HTTPException(status_code=400, detail="该视频尚未生成完成")
    if message.video_expired:
        raise HTTPException(
            status_code=410,
            detail=f"视频已超过 {settings.video_retention_days} 天保留期被自动清理",
        )

    title = (conversation.title or "video").strip()[:40] or "video"
    safe_title = "".join(ch for ch in title if ch not in '\\/:*?"<>|').strip() or "video"
    filename = f"{safe_title}-{message.id[:8]}.mp4"

    if message.local_video:
        path = settings.video_dir / message.local_video
        if path.is_file():
            return FileResponse(
                path,
                media_type="video/mp4",
                filename=filename,
                headers=_attachment_headers(filename),
            )

    if not message.video_url:
        raise HTTPException(status_code=404, detail="视频文件不存在，可能已过期")

    async def proxy():
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0)) as client:
            async with client.stream("GET", message.video_url) as response:
                if response.status_code >= 400:
                    raise HTTPException(
                        status_code=502, detail="源视频地址已失效（DashScope 链接 24 小时后过期）"
                    )
                async for chunk in response.aiter_bytes(1024 * 256):
                    yield chunk

    return StreamingResponse(
        proxy(), media_type="video/mp4", headers=_attachment_headers(filename)
    )


# ---------------------------------------------------------------------------
# 带签名的媒体文件访问
#
# 这两条路由取代了原来的 StaticFiles 挂载。不做 Bearer 鉴权（<video>/<img>
# 带不了请求头，阿里云回源也没有登录态），改为校验链接自带的签名与有效期。
# ---------------------------------------------------------------------------

media_router = APIRouter(prefix="/media", tags=["media"])


def _serve(
    directory,
    name: str,
    url_path: str,
    exp: str | None,
    sig: str | None,
    *,
    download: bool = False,
    range_header: str | None = None,
):
    ok, reason = verify(url_path, exp, sig)
    if not ok:
        raise HTTPException(status_code=403, detail=reason)

    # 只取文件名，挡掉 ../ 之类的穿越；再做一次归属校验兜底
    safe = Path(name).name
    target = (directory / safe).resolve()
    if not target.is_relative_to(directory.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    media_type = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    headers = {
        # 链接本身就带有效期，缓存时长不要超过它
        "Cache-Control": f"private, max-age={settings.media_link_ttl}",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
    }
    if download:
        headers.update(_attachment_headers(safe))

    total = target.stat().st_size
    if not range_header:
        headers["Content-Length"] = str(total)
        return FileResponse(target, media_type=media_type, headers=headers)

    # Browsers use byte ranges for seeking and for starting playback without
    # downloading the entire file.  Keep this implementation explicit so the
    # behavior is stable across Starlette and reverse-proxy versions.
    if "," in range_header or not range_header.startswith("bytes="):
        raise HTTPException(
            status_code=416,
            detail="不支持的 Range 请求",
            headers={"Content-Range": f"bytes */{total}"},
        )
    raw = range_header.removeprefix("bytes=").strip()
    if "-" not in raw:
        raise HTTPException(
            status_code=416,
            detail="无效的 Range 请求",
            headers={"Content-Range": f"bytes */{total}"},
        )
    start_text, end_text = raw.split("-", 1)
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                raise ValueError
            start, end = max(total - suffix, 0), total - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else total - 1
            if start < 0 or start >= total or end < start:
                raise ValueError
            end = min(end, total - 1)
    except ValueError:
        raise HTTPException(
            status_code=416,
            detail="Range 超出文件范围",
            headers={"Content-Range": f"bytes */{total}"},
        ) from None

    length = end - start + 1
    headers.update({
        "Content-Length": str(length),
        "Content-Range": f"bytes {start}-{end}/{total}",
    })

    def body():
        with target.open("rb") as file:
            file.seek(start)
            remaining = length
            while remaining:
                chunk = file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=206, media_type=media_type, headers=headers)


@media_router.get("/videos/{name}")
def media_video(
    name: str,
    exp: str | None = Query(default=None),
    sig: str | None = Query(default=None),
    download: bool = Query(default=False),
    range_header: str | None = Header(default=None, alias="Range"),
):
    return _serve(
        settings.video_dir,
        name,
        f"/media/videos/{Path(name).name}",
        exp,
        sig,
        download=download,
        range_header=range_header,
    )


@media_router.get("/uploads/{name}")
def media_upload(
    name: str,
    exp: str | None = Query(default=None),
    sig: str | None = Query(default=None),
):
    return _serve(settings.upload_dir, name, f"/media/uploads/{Path(name).name}", exp, sig)
