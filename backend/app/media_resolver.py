"""把上传的参考图解析成阿里云能真正取到的地址。

阿里云是"反向下载"：请求体里给的是图片地址，DashScope 服务器自己去抓。
所以本机跑的 http://localhost:8008/media/... 它是打不开的。两条出路：

1. 配了公网可达的 PUBLIC_BASE_URL  -> 拼成公网 URL 交给它下载；
2. 模型支持 Base64（wan / happyhorse）-> 直接把图片内容编码进请求体，不需要公网。

MiniMax H3 只认 http(s)，没有第 2 条路，只能走第 1 条。
"""
import base64
import ipaddress
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from .catalog import Capability, VideoModel
from .config import settings
from .media_links import absolute_signed

LOCAL_PREFIX = "/media/uploads/"

# 阿里云文档给的图片大小上限
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class MediaError(RuntimeError):
    """参考图无法被阿里云访问时抛出，消息直接展示给用户。"""


def _host_is_private(host: str) -> bool:
    host = (host or "").split(":")[0].strip().lower()
    if not host:
        return True
    if host in {"localhost", "0.0.0.0"} or host.endswith((".local", ".localhost", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 是个域名，认为公网可达
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified


def public_base_url_usable() -> bool:
    """PUBLIC_BASE_URL 是否填了且看起来是公网地址。"""
    if not settings.public_base_url:
        return False
    return not _host_is_private(urlparse(settings.public_base_url).hostname or "")


def is_local_upload(url: str) -> bool:
    return url.startswith(LOCAL_PREFIX)


def _to_data_uri(filename: str) -> str:
    path = settings.upload_dir / Path(filename).name
    if not path.is_file():
        raise MediaError(f"上传的图片已不存在：{filename}")
    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise MediaError(
            f"图片 {path.name} 有 {len(data) / 1024 / 1024:.1f}MB，超过阿里云 20MB 上限"
        )
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def describe_upload_mode(model: VideoModel, kind: str = "image") -> str:
    """说明本机素材会以公网 URL、Base64 还是无法提交。"""
    if public_base_url_usable():
        return "public_url"
    if kind == "image" and model.supports_base64_media:
        return "base64"
    return "unavailable"


def _resolve_url(url: str, model: VideoModel, kind: str) -> str:
    use_public = public_base_url_usable()

    if not is_local_upload(url):
        return url
    if use_public:
        return absolute_signed(url)
    if kind == "image" and model.supports_base64_media:
        return _to_data_uri(url[len(LOCAL_PREFIX):])
    label = {"image": "图片", "video": "视频", "audio": "音频"}.get(kind, "素材")
    raise MediaError(
        f"{model.label} 的本机{label}需要由阿里云通过公网地址读取。"
        "请配置 PUBLIC_BASE_URL（域名或公网 IP），"
        f"或改用「粘贴{label}外链」。"
    )


def resolve(urls: list[str], model: VideoModel) -> list[str]:
    """兼容旧消息：把图片地址转成提交给 DashScope 的地址。"""
    return [_resolve_url(url, model, "image") for url in urls]


def resolve_inputs(
    items: list[dict],
    model: VideoModel,
    capability: Capability,
) -> list[dict[str, str]]:
    """把通用素材转换为供应商要求的 media 对象。"""
    specs = {item.kind: item for item in capability.input_specs()}
    resolved: list[dict[str, str]] = []
    for item in items:
        kind = str(item.get("kind", ""))
        spec = specs.get(kind)
        if spec is None:
            raise MediaError(f"{model.label}（{capability.label}）不支持 {kind} 素材")
        url = str(item.get("url", "")).strip()
        if not url:
            raise MediaError(f"{spec.label}地址不能为空")
        resolved.append({"type": spec.media_type, "url": _resolve_url(url, model, kind)})
    return resolved
