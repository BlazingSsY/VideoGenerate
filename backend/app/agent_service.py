"""Agent planning contract and validation.

The provider may suggest a plan, but this module is the authority for what can
be accepted. Provider output is never executed directly.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .agent_provider import AgentError, AgentResult, plan as provider_plan
from .catalog import I2V, R2V, T2V, get_model, models_for_role
from .config import settings
from .models import User
from .routers.conversations import _validate
from .schemas import GenerateRequest

AGENT_SYSTEM_PROMPT = """你是视频任务编排器。只输出 JSON，不要 Markdown，不要解释。
把用户目标拆成可执行的短视频任务。每个 generate 节点的 duration 必须在所选视频模型和系统上限内。
长视频必须输出多个 generate 节点和一个 compose 节点，compose 只使用 operation=concat 顺序拼接。
节点字段：id、type、prompt、model、capability、resolution、ratio、duration、reference_media、end_frame、depends_on、reason。
compose 字段：id、type=compose、operation=concat、inputs、depends_on。
输出字段：title、target_duration、nodes、output_node、reason。
不要调用工具，不要填写 API 地址、Key，不要伪造模型能力。"""


def _price(model_id: str, resolution: str) -> float:
    values: dict[str, float] = {}
    for entry in settings.agent_price_table.split(","):
        key, sep, raw = entry.rpartition("=")
        if sep:
            try:
                values[key.strip()] = float(raw.strip())
            except ValueError:
                pass
    return values.get(f"{model_id}:{resolution}", settings.agent_price_default)


def estimate(plan: dict) -> tuple[float, int]:
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        nodes = plan.get("generations", [])
    generations = [item for item in nodes if isinstance(item, dict) and item.get("type", "generate") == "generate"]
    seconds = sum(int(item.get("duration", 0)) for item in generations)
    cost = sum(_price(str(item.get("model", "")), str(item.get("resolution", ""))) * int(item.get("duration", 0)) for item in generations)
    return round(cost, 2), seconds


def _fallback_plan(user: User, text: str, media: list[dict], target_duration: int | None) -> tuple[str, dict]:
    capability_id = I2V if len(media) == 1 and media[0].get("kind") == "image" else R2V if media else T2V
    candidates = [m for m in models_for_role(user.role) if m.capability(capability_id)]
    if not candidates:
        raise HTTPException(status_code=400, detail="当前账号没有可用于该创作请求的模型")
    model = candidates[0]
    target = max(1, target_duration or model.default_duration)
    maximum = min(model.duration_max, settings.max_duration)
    minimum = model.duration_min
    count = max(1, (target + maximum - 1) // maximum)
    while count > 1 and target < count * minimum:
        count -= 1
    base, remainder = divmod(target, count)
    durations = [base + (1 if index < remainder else 0) for index in range(count)]
    if any(duration < minimum or duration > maximum for duration in durations):
        raise HTTPException(
            status_code=400,
            detail=f"目标时长无法拆分为 {minimum}-{maximum} 秒的合法片段",
        )
    nodes: list[dict[str, Any]] = []
    for index, duration in enumerate(durations):
        nodes.append({
            "id": f"shot-{index + 1}", "type": "generate", "prompt": text.strip(),
            "model": model.id, "capability": capability_id, "resolution": model.default_resolution,
            "ratio": model.default_ratio_for(capability_id), "duration": duration,
            "reference_media": media if index == 0 else [], "end_frame": None,
            "depends_on": [f"shot-{index}"] if index else [], "reason": f"第 {index + 1} 个镜头",
        })
    output = nodes[-1]["id"]
    if len(nodes) > 1:
        compose_id = "compose-1"
        nodes.append({"id": compose_id, "type": "compose", "operation": "concat", "inputs": [node["id"] for node in nodes], "depends_on": [node["id"] for node in nodes]})
        output = compose_id
    return "one-line-video", {"title": text.strip()[:40], "target_duration": target, "nodes": nodes, "output_node": output, "reason": "按模型单段时长上限拆分并顺序拼接"}


async def create_plan(user: User, text: str, media: list[dict], surface: str, agent_model_id: str | None = None, target_duration: int | None = None) -> tuple[str, dict, AgentResult | None, str]:
    configured = settings.agent_provider_configured
    if not configured:
        if not settings.agent_fallback_rules:
            raise AgentError("Agent Provider 未配置，请设置 AGENT_BASE_URL、AGENT_API_KEY 或 AGENT_MODELS_JSON")
        skill, value = _fallback_plan(user, text, media, target_duration)
        return skill, value, None, "未配置 Agent Provider，使用兼容规划器"
    request = {"surface": surface, "user_input": text, "target_duration": target_duration, "reference_media": media}
    result = await provider_plan(
        agent_model_id,
        AGENT_SYSTEM_PROMPT,
        json.dumps(request, ensure_ascii=False),
    )
    return "agent-plan", result.value, result, ""


def validate_plan(db: Session, user: User, request: Request | None, plan: dict) -> tuple[float, int]:
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= settings.agent_max_auto_nodes + 3:
        raise HTTPException(status_code=400, detail="计划节点数量超出限制")
    by_id: dict[str, dict] = {}
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id") or str(node["id"]) in by_id:
            raise HTTPException(status_code=400, detail="计划节点 id 无效或重复")
        if node.get("type") not in {"generate", "compose"}:
            raise HTTPException(status_code=400, detail=f"不支持的计划节点类型：{node.get('type')}")
        by_id[str(node["id"])] = node
    for node in nodes:
        dependencies = node.get("depends_on", [])
        if not isinstance(dependencies, list) or any(str(dep) not in by_id for dep in dependencies):
            raise HTTPException(status_code=400, detail="计划依赖引用了不存在的节点")
        if node.get("type") == "generate":
            model = get_model(str(node.get("model", "")))
            capability = model.capability(str(node.get("capability", T2V))) if model else None
            supports_end_frame = bool(
                capability and any(item.kind == "end_frame" for item in capability.input_specs())
            )
            if (node.get("end_frame") is not None or node.get("require_end_frame")) and not supports_end_frame:
                raise HTTPException(status_code=400, detail="当前模型的图生视频不支持尾帧输入")
            try:
                payload = GenerateRequest(
                    prompt=str(node.get("prompt", "")), model=str(node.get("model", "")),
                    capability=str(node.get("capability", T2V)), resolution=str(node.get("resolution", "")),
                    ratio=str(node.get("ratio", "")), duration=int(node.get("duration", 0)),
                    reference_media=node.get("reference_media", []), use_context=False,
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="计划生成节点参数无效") from exc
            _validate(payload, user, request, db)
        else:
            if node.get("operation") != "concat" or not isinstance(node.get("inputs"), list) or len(node["inputs"]) < 2:
                raise HTTPException(status_code=400, detail="合成节点只支持至少两个输入的顺序拼接")
            if any(str(item) not in by_id or by_id[str(item)].get("type") != "generate" for item in node["inputs"]):
                raise HTTPException(status_code=400, detail="合成输入必须是生成节点")
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise HTTPException(status_code=400, detail="计划不能包含环形依赖")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in by_id[node_id].get("depends_on", []): visit(str(dependency))
        visiting.remove(node_id); visited.add(node_id)
    for node_id in by_id: visit(node_id)
    cost, seconds = estimate(plan)
    target = int(plan.get("target_duration", seconds) or seconds)
    if target > 0 and abs(seconds - target) > max(3, target // 5):
        raise HTTPException(status_code=400, detail="计划镜头总时长与目标时长差异过大")
    return cost, seconds


def expire_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=settings.agent_plan_ttl_minutes)
