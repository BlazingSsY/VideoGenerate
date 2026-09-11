"""OpenAI-compatible Agent provider with redacted, structured responses."""
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import AgentModelConfig, settings

logger = logging.getLogger(__name__)


class AgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentResult:
    value: dict[str, Any]
    tokens_in: int = 0
    tokens_out: int = 0


def _chat_completions_url(base_url: str) -> str:
    """Accept either an OpenAI base URL or the full chat endpoint."""
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def public_models() -> list[dict[str, Any]]:
    return [{"id": item.id, "name": item.name, "supports_json": item.supports_json} for item in settings.agent_models()]


def resolve_model(model_id: str | None) -> AgentModelConfig:
    models = settings.agent_models()
    selected = model_id or settings.agent_default_model or (models[0].id if models else "")
    model = next((item for item in models if item.id == selected), None)
    if model is None:
        raise AgentError("所选 Agent 模型不可用，请从服务器返回的模型列表中选择")
    if not model.base_url or not model.api_key:
        raise AgentError(f"Agent 模型 {model.id} 未配置地址或 API Key")
    return model


def context_model() -> AgentModelConfig:
    """Use the same configured Agent model for memory and Skill rewriting."""
    return resolve_model(None)


def _content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise AgentError("Agent 未返回计划内容")
    content = (choices[0].get("message") or {}).get("content", "")
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return str(content).strip()


def _parse_json(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise AgentError("Agent 返回的计划不是有效 JSON") from None
        try:
            value = json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            raise AgentError("Agent 返回的计划不是有效 JSON") from None
    if not isinstance(value, dict):
        raise AgentError("Agent 计划必须是 JSON 对象")
    return value


def plan(model_id: str | None, system_prompt: str, user_prompt: str) -> AgentResult:
    model = resolve_model(model_id)
    headers = {"Authorization": f"Bearer {model.api_key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "model": model.id,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "temperature": settings.agent_temperature,
        "max_tokens": settings.agent_max_tokens,
    }
    if model.supports_json:
        body["response_format"] = {"type": "json_object"}
    try:
        with httpx.Client(timeout=settings.agent_timeout_seconds) as client:
            response = client.post(
                _chat_completions_url(model.base_url),
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.ConnectTimeout:
        logger.warning("Agent 连接超时：model=%s", model.id)
        raise AgentError("Agent 连接 SiliconFlow 超时，请检查服务器网络或接口地址") from None
    except httpx.ReadTimeout:
        logger.warning("Agent 读取响应超时：model=%s timeout=%ss", model.id, settings.agent_timeout_seconds)
        raise AgentError(
            f"Agent 等待 {settings.agent_timeout_seconds} 秒仍未返回，请稍后重试或增大 AGENT_TIMEOUT_SECONDS"
        ) from None
    except httpx.HTTPStatusError as exc:
        logger.warning("Agent HTTP 错误：model=%s status=%s", model.id, exc.response.status_code)
        raise AgentError(f"Agent 服务返回 HTTP {exc.response.status_code}，请检查模型 ID、Key 和请求格式") from None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Agent 请求失败：model=%s error_type=%s", model.id, type(exc).__name__)
        raise AgentError("Agent 服务暂时不可用，请检查模型地址、Key 或网络配置") from None
    usage = data.get("usage") or {}
    return AgentResult(_parse_json(_content(data)), int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))
