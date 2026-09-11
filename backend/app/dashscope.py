"""DashScope 视频生成接口封装（异步任务：提交 -> 轮询 -> 取视频地址）。"""
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import settings

SUBMIT_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
TASK_PATH = "/api/v1/tasks/{task_id}"
CHAT_PATH = "/compatible-mode/v1/chat/completions"

TERMINAL_OK = "SUCCEEDED"
TERMINAL_BAD = {"FAILED", "CANCELED", "UNKNOWN"}


class DashScopeError(RuntimeError):
    pass


class InsufficientBalanceError(DashScopeError):
    """供应商明确返回账户余额或额度耗尽，可安全尝试备用通道。"""


def is_insufficient_balance(data: dict[str, Any]) -> bool:
    """只识别明确的余额耗尽错误，避免把参数或鉴权错误误切到备用 Key。"""
    output = data.get("output") or {}
    values = [
        data.get("code"), data.get("message"),
        output.get("code"), output.get("message"),
    ]
    text = " ".join(str(value) for value in values if value).lower()
    markers = (
        "insufficientbalance", "insufficient_balance", "balance insufficient",
        "quotaexhausted", "quota_exhausted", "quota exhausted",
        "余额不足", "余额已用尽", "额度不足", "额度已用尽",
    )
    return any(marker in text for marker in markers)


def _service_base_url(base_url: str) -> str:
    """兼容模式根地址与异步视频任务根地址共用同一地域主机。

    视频生成仍使用 DashScope 的异步任务路径；不能把
    /api/v1/services/... 直接拼在 /compatible-mode/v1 后面。
    """
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    if path.endswith("/compatible-mode/v1"):
        path = path[: -len("/compatible-mode/v1")]
    return f"{parts.scheme}://{parts.netloc}{path}".rstrip("/")


def _describe(action: str, data: dict[str, Any]) -> str:
    output = data.get("output") or {}
    code = data.get("code") or output.get("code") or "未知错误码"
    message = data.get("message") or output.get("message") or "接口未返回错误说明"
    request_id = data.get("request_id")
    text = f"{action}：{code} - {message}"
    if request_id:
        text += f"（request_id: {request_id}）"
    return text


def _read(action: str, response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        raise DashScopeError(f"{action}：接口返回了无法解析的数据（HTTP {response.status_code}）")
    if response.status_code >= 400:
        detail = _describe(action, data)
        if is_insufficient_balance(data):
            raise InsufficientBalanceError(detail)
        raise DashScopeError(detail)
    return data


def build_payload(
    model: str,
    prompt: str,
    resolution: str,
    duration: int,
    ratio: str = "",
    watermark: bool | None = None,
    audio: bool | None = None,
    media_type: str = "",
    media_urls: list[str] | None = None,
    media: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """按官方文档拼装请求体。

    media_type 由模型 + 生成方式决定：first_frame（图生视频）、
    reference_image / image_url（参考生视频），文生视频则不带 media。
    """
    payload: dict[str, Any] = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {
            "resolution": resolution,
            "duration": duration,
        },
    }
    if ratio:  # happyhorse-1.1-i2v 没有 ratio 参数，比例跟随首帧图
        payload["parameters"]["ratio"] = ratio
    if media:
        payload["input"]["media"] = [
            {"type": item["type"], "url": item["url"]} for item in media
        ]
    elif media_type and media_urls:
        payload["input"]["media"] = [{"type": media_type, "url": url} for url in media_urls]
    if watermark is not None:
        payload["parameters"]["watermark"] = watermark
    if audio is not None:
        payload["parameters"]["audio"] = audio
    return payload


async def submit_task(api_key: str, payload: dict[str, Any], *, base_url: str | None = None) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            _service_base_url(base_url or settings.dashscope_base_url) + SUBMIT_PATH,
            headers=headers,
            json=payload,
        )
    data = _read("提交任务失败", response)
    task_id = (data.get("output") or {}).get("task_id")
    if not task_id:
        raise DashScopeError(_describe("提交任务失败", data))
    return task_id


async def fetch_task(api_key: str, task_id: str, *, base_url: str | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            _service_base_url(base_url or settings.dashscope_base_url) + TASK_PATH.format(task_id=task_id),
            headers=headers,
        )
    data = _read("查询任务失败", response)
    output = data.get("output") or {}
    status = output.get("task_status")
    if not status:
        raise DashScopeError(_describe("查询任务失败", data))
    return {
        "status": status,
        "video_url": _extract_video_url(output),
        "raw": data,
        "message": _describe("视频生成失败", data) if status in TERMINAL_BAD else "",
    }


def _extract_video_url(output: dict[str, Any]) -> str:
    """不同模型返回结构略有差异，做一次兼容提取。"""
    if isinstance(output.get("video_url"), str):
        return output["video_url"]
    results = output.get("results")
    if isinstance(results, dict) and isinstance(results.get("video_url"), str):
        return results["video_url"]
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("video_url"), str):
                return item["video_url"]
    return ""


async def chat(api_key: str, model: str, messages: list[dict[str, str]]) -> str:
    """调用 DashScope 兼容模式的文本模型（用于多轮提示词改写）。"""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages, "temperature": 0.3}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            settings.dashscope_base_url + CHAT_PATH, headers=headers, json=body
        )
    data = _read("提示词改写失败", response)
    choices = data.get("choices") or []
    if not choices:
        raise DashScopeError("提示词改写失败：模型未返回内容")
    return (choices[0].get("message") or {}).get("content", "").strip()
