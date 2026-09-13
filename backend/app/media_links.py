"""媒体文件的签名链接。

/media/ 下的视频和参考图不能走 Bearer 鉴权：
  - 浏览器 <video src> / <img src> 没法附加请求头；
  - 阿里云要从公网回源下载参考图，它不可能带着谁的登录态。

所以改成"链接自带签名 + 有效期"：链接由后端签发，过期即失效。
即使链接被转发出去，过一段时间也访问不了。

签名范围是「路径 + 过期时间」，不绑定用户——因为同一张参考图既要给浏览器看，
也要给阿里云下载，后者没有用户身份。

注意：SECRET_KEY 变更会让此前签发的所有链接立即失效。
"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

from .config import settings

_SIG_BYTES = 16  # 128 位，够抗伪造，又不至于让 URL 太长


def _digest(path: str, expires: int) -> str:
    message = f"{path}\n{expires}".encode()
    return hmac.new(
        settings.secret_key.encode(), message, hashlib.sha256
    ).hexdigest()[: _SIG_BYTES * 2]


def sign_path(path: str, ttl: int | None = None) -> str:
    """给 /media/... 相对路径加上 ?exp=&sig=，返回仍是相对路径。"""
    if not path.startswith("/media/"):
        return path
    expires = int(time.time()) + (ttl or settings.media_link_ttl)
    query = urlencode({"exp": expires, "sig": _digest(path, expires)})
    return f"{path}?{query}"


def absolute_signed(path: str, ttl: int | None = None) -> str:
    """给阿里云回源用的完整地址：公网前缀 + 签名后的路径。"""
    signed = sign_path(path, ttl)
    if not settings.public_base_url or not signed.startswith("/media/"):
        return signed
    return f"{settings.public_base_url}{signed}"


def verify(path: str, expires: str | None, signature: str | None) -> tuple[bool, str]:
    """校验签名。返回 (是否通过, 失败原因)。"""
    if not expires or not signature:
        return False, "链接缺少签名参数"
    try:
        deadline = int(expires)
    except ValueError:
        return False, "链接签名参数无效"
    if deadline < time.time():
        return False, "链接已过期，请回到页面重新获取"
    if not hmac.compare_digest(_digest(path, deadline), signature):
        return False, "链接签名无效"
    return True, ""


def format_video_src(filename: str | None) -> str:
    """产出文件名 → 带签名的播放地址；空文件名返回空串。"""
    if not filename:
        return ""
    return sign_path(f"/media/videos/{filename}")
