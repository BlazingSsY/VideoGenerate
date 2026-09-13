"""v3: 对话式视频制作智能体 — 编排状态机核心。

状态机流程：
  S1 classify → S2 retrieve → S3 select_skill → S4 draft → S5 validate ⇄ S6 repair → S7 gate → S8 execute

chat / asset / diagnose / unsupported 四类意图直接跳到 S9 返回。
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import httpx
from sqlalchemy.orm import Session

from .agent_provider import AgentError, resolve_model, _chat_completions_url
from .agent_service import _fallback_plan, validate_plan, estimate, expire_at
from .agent_run_service import AgentRunError, AutoApprovalRequired, accept_plan
from .agent_tools import TOOL_REGISTRY, execute_tool, extract_urls_from_input
from .catalog import models_for_role, MODELS
from .config import settings
from .database import SessionLocal
from .models import AgentChatMessage, AgentSession, AgentStep, AgentTurn, User

logger = logging.getLogger(__name__)

# per-turn event queues for SSE broadcast; every event carries a monotonic
# `seq` so a reconnecting client (from_seq) can skip what it already saw.
# Event seq shares the counter with step seq — a pushed event always has a seq
# strictly greater than any earlier one for the same turn.
_event_queues: dict[str, asyncio.Queue] = {}
_step_counter: dict[str, int] = {}


def get_event_queue(turn_id: str) -> asyncio.Queue:
    """获取或创建 turn 的事件队列。"""
    if turn_id not in _event_queues:
        _event_queues[turn_id] = asyncio.Queue()
    return _event_queues[turn_id]


def cleanup_event_queue(turn_id: str) -> None:
    _event_queues.pop(turn_id, None)
    _step_counter.pop(turn_id, None)


def _next_seq(turn_id: str) -> int:
    seq = _step_counter.get(turn_id, 0) + 1
    _step_counter[turn_id] = seq
    return seq


# ── 系统提示词 ──────────────────────────────────────────────

def _build_system_prompt(user_role: str) -> str:
    """动态注入模型能力目录摘要。"""
    lines = [
        f"- {m.label}({m.id})：{'、'.join(c.label for c in m.capabilities)}"
        for m in models_for_role(user_role)
    ]
    catalog = "\n".join(lines)
    return f"""你是视频创作智能体，帮助用户完成视频制作。

## 当前可用模型（详细档位用 catalog.describe 工具查）
{catalog}

## 全局限制
- 单段视频最长 {settings.max_duration} 秒
- 超过单段上限的视频需拆成多段 + compose 顺序拼接

## 对话规则
- 用自然中文对话，参数建议要给出理由
- 确定要出计划时，把计划 JSON 嵌在回复末尾，用 ```plan 围栏包裹
- 围栏外的正文是给人看的说明，不要把 JSON 重复一遍
- 计划 JSON 字段：title, target_duration, nodes, output_node, reason
- 每个 generate 节点：id, type, prompt, model, capability, resolution, ratio, duration, reference_media, depends_on, reason
- compose 节点：id, type=compose, operation=concat, inputs, depends_on
"""


def _build_intent_prompt(user_input: str, has_prior_plan: bool) -> str:
    """S1 意图分类的 system prompt。"""
    return f"""你是意图分类器。只输出 JSON，格式 {{"intent": "...", "reason": "..."}}。
intent 取值：chat / generate / refine / diagnose / asset / unsupported
- chat: 用户在提问、咨询
- generate: 用户要创作新视频
- refine: 用户要修改已有计划{"（存在上一版计划）" if has_prior_plan else "（不存在上一版计划，应归为 generate）"}
- diagnose: 用户在问为什么失败
- asset: 用户要找素材
- unsupported: 超出现有模型能力
reason 限 20 字。
用户输入：{user_input}"""


# ── 计划提取 ────────────────────────────────────────────────

def _extract_plan(content: str) -> dict[str, Any] | None:
    """从回复中提取计划 JSON，四级兜底。"""
    # 1. ```plan 围栏
    match = re.search(r"```plan\s*\n(.*?)```", content, re.DOTALL)
    if match:
        return _try_parse_json(match.group(1).strip())
    # 2. ```json 围栏
    match = re.search(r"```json\s*\n(.*?)```", content, re.DOTALL)
    if match:
        return _try_parse_json(match.group(1).strip())
    # 3. 裸围栏且以 { 开头
    match = re.search(r"```\s*\n(\{.*?)```", content, re.DOTALL)
    if match:
        return _try_parse_json(match.group(1).strip())
    # 4. 裸 JSON：从第一个 { 到最后一个 }
    start, end = content.find("{"), content.rfind("}")
    if start >= 0 and end > start:
        return _try_parse_json(content[start:end + 1])
    # 5. 全部失败 → 这轮是追问，没出计划
    return None


def _try_parse_json(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
        if isinstance(value, dict) and "nodes" in value:
            return value
    except json.JSONDecodeError:
        pass
    return None


# ── 意图分类 (S1) ──────────────────────────────────────────

_REFINE_KEYWORDS = ("改", "换", "加长", "去掉", "调")
_GENERATE_KEYWORDS = ("做一个", "生成", "来一个", "帮我做", "创作")
_CHAT_KEYWORDS = ("区别", "怎么选", "能不能", "是什么")
_ASSET_KEYWORDS = ("找找", "我有哪些", "素材")
_DIAGNOSE_KEYWORDS = ("为什么失败", "报错")
_CANVAS_READ_KEYWORDS = ("查看当前分镜", "看看当前分镜", "画布有什么", "当前画布", "有哪些镜头")


def _rule_based_intent(user_input: str, has_prior_plan: bool) -> str:
    """未配置 Provider 时的关键词意图分类（设计 §4.2 降级）。"""
    if "为什么失败" in user_input or "报错" in user_input:
        return "diagnose"
    if any(kw in user_input for kw in _CANVAS_READ_KEYWORDS):
        return "chat"
    if has_prior_plan and any(kw in user_input for kw in _REFINE_KEYWORDS):
        return "refine"
    if any(kw in user_input for kw in _CHAT_KEYWORDS):
        return "chat"
    if any(kw in user_input for kw in _ASSET_KEYWORDS):
        return "asset"
    if any(kw in user_input for kw in _GENERATE_KEYWORDS):
        return "generate"
    if "?" in user_input or "？" in user_input:
        return "chat"
    return "generate"


async def classify_intent(
    model_id: str | None, user_input: str, has_prior_plan: bool
) -> dict[str, str]:
    """S1: 独立一次 LLM 调用，输出 intent 枚举。未配置 Provider 时按规则兜底。"""
    if not settings.agent_provider_configured:
        intent = _rule_based_intent(user_input, has_prior_plan)
        return {"intent": intent, "reason": "规则规划器"}
    try:
        model = resolve_model(model_id)
    except AgentError:
        logger.warning("S1 意图分类失败（模型不可用），按 generate 兜底")
        return {"intent": "generate", "reason": "分类失败，兜底"}
    url = _chat_completions_url(model.base_url)
    headers = {"Authorization": f"Bearer {model.api_key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "model": model.id,
        "messages": [
            {"role": "system", "content": _build_intent_prompt(user_input, has_prior_plan)},
            {"role": "user", "content": user_input},
        ],
        "temperature": 0,
        "max_tokens": 64,
    }
    if model.supports_json:
        body["response_format"] = {"type": "json_object"}
    timeout = httpx.Timeout(settings.agent_timeout_seconds, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        result = json.loads(content) if content else {}
        intent = result.get("intent", "generate")
        if intent not in ("chat", "generate", "refine", "diagnose", "asset", "unsupported"):
            intent = "generate"
        return {"intent": intent, "reason": result.get("reason", "")[:20]}
    except Exception as exc:
        logger.warning("S1 意图分类失败，按 generate 兜底: %s", exc)
        return {"intent": "generate", "reason": "分类失败，兜底"}


# ── 流式对话 (S4) ───────────────────────────────────────────

async def stream_chat(
    model_id: str | None, system_prompt: str, messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[tuple[str, Any], None]:
    """流式调用 LLM，yield (kind, payload)。

    kind ∈ {"think", "text", "tool_calls", "usage"}:
    - think: reasoning_content（思维链增量，payload 为 str）
    - text: content（正文增量，含 plan 围栏，payload 为 str）
    - tool_calls: 模型请求的工具调用列表（payload 为 list[dict]），
      仅在本次响应里模型发起了 function call 时流末 yield 一次
    - usage: token 计账（payload 为 dict），stream_options.include_usage 开启时流末 yield
    """
    model = resolve_model(model_id)
    url = _chat_completions_url(model.base_url)
    headers = {"Authorization": f"Bearer {model.api_key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "model": model.id,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "temperature": settings.agent_temperature,
        "max_tokens": settings.agent_chat_max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    provider_tool_names: dict[str, str] = {}
    if tools:
        # OpenAI-compatible providers require the function wrapper and commonly
        # reject dots in function names. Keep the public registry readable while
        # translating names at the provider boundary.
        wrapped = []
        for tool in tools:
            public_name = str(tool.get("name") or "")
            provider_name = public_name.replace(".", "__")
            provider_tool_names[provider_name] = public_name
            wrapped.append({
                "type": "function",
                "function": {
                    "name": provider_name,
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            })
        body["tools"] = wrapped
        body["tool_choice"] = "auto"
    if settings.agent_enable_thinking:
        body["enable_thinking"] = True
    timeout = httpx.Timeout(settings.agent_stream_idle_timeout, connect=10.0)
    # tool_calls 按 index 聚合；DashScope 兼容协议在增量中分段下发 name/arguments
    aggregated: dict[int, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    delta = choices[0].get("delta", {}) if choices else {}
                    if delta.get("reasoning_content"):
                        yield ("think", delta["reasoning_content"])
                    if delta.get("content"):
                        yield ("text", delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = aggregated.setdefault(idx, {
                            "id": tc.get("id", ""), "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = provider_tool_names.get(fn["name"], fn["name"])
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    if not choices:
                        # usage-only 尾帧（choices=[]），include_usage 开启时出现
                        usage = chunk.get("usage")
                        if isinstance(usage, dict):
                            yield ("usage", usage)
                if aggregated:
                    yield ("tool_calls", [aggregated[i] for i in sorted(aggregated)])
    except httpx.HTTPError as exc:
        logger.warning("流式对话失败: %s", exc)
        raise AgentError(f"智能体对话失败: {exc}") from None


def _summarize_tool_result(name: str, result: Any) -> str:
    """给步骤流水和事件用的工具结果一句话摘要。"""
    if not isinstance(result, dict):
        return str(result)[:120]
    if "error" in result:
        return f"失败: {str(result['error'])[:100]}"
    if name == "catalog.describe":
        models = result.get("models")
        return f"返回 {len(models) if isinstance(models, list) else 1} 个模型的能力档位"
    if name in ("library.search", "history.search"):
        results = result.get("results") or []
        return f"命中 {len(results)} 条记录"
    if name == "url.fetch":
        return "已抓取页面内容" if result.get("ok") else f"抓取失败: {str(result.get('error', ''))[:80]}"
    return "完成"


async def select_skill(
    model_id: str | None, user_input: str, skills: list,
) -> Any | None:
    """S3: 短调用让 LLM 从启用技能中选一个；LLM 不可用时回退关键词匹配。"""
    catalog = [
        {"id": s.id, "name": s.name, "description": (s.description or "")[:60]}
        for s in skills
    ]
    names = {s.id: s for s in skills}
    try:
        model = resolve_model(model_id)
        url = _chat_completions_url(model.base_url)
        headers = {"Authorization": f"Bearer {model.api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": model.id,
            "messages": [
                {"role": "system", "content": "根据用户输入选择最相关的一个技能，只输出 JSON：{\"skill_id\": \"...\"}。没有合适的技能输出 {\"skill_id\": \"\"}。"},
                {"role": "user", "content": f"用户输入：{user_input}\n技能列表：{json.dumps(catalog, ensure_ascii=False)}"},
            ],
            "temperature": 0,
            "max_tokens": 64,
        }
        if model.supports_json:
            body["response_format"] = {"type": "json_object"}
        timeout = httpx.Timeout(settings.agent_timeout_seconds, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        picked = json.loads(content) if content else {}
        skill_id = picked.get("skill_id", "")
        if skill_id and skill_id in names:
            return names[skill_id]
        return None
    except Exception as exc:
        logger.warning("S3 选技能 LLM 调用失败，回退关键词匹配: %s", exc)
        for skill in skills:
            if any(kw in user_input for kw in (skill.description or "").split()):
                return skill
        return None


# ── 三段式围栏状态机 ─────────────────────────────────────────

class FenceParser:
    """解析流式 content，分离正文和计划块。"""

    def __init__(self) -> None:
        self._buffer = ""
        self._in_plan = False
        self._plan_buffer = ""

    def feed(self, text: str) -> tuple[str, str | None]:
        """返回 (text_delta, plan_json_or_None)。plan_json 仅在围栏闭合时返回。"""
        self._buffer += text
        output_text = ""
        plan_result = None

        while self._buffer:
            if not self._in_plan:
                # 查找 plan 围栏起始
                idx = self._buffer.find("```plan")
                if idx >= 0:
                    output_text += self._buffer[:idx]
                    self._buffer = self._buffer[idx + 7:]  # skip ```plan
                    self._in_plan = True
                    self._plan_buffer = ""
                elif "```" in self._buffer and self._buffer.rstrip().endswith("```"):
                    # 可能是不完整围栏起始，保留等下次
                    keep = self._buffer.rfind("```")
                    output_text += self._buffer[:keep]
                    self._buffer = self._buffer[keep:]
                    break
                else:
                    # 没有围栏起始，输出全部（保留最后 3 字符防止截断 ```pl）
                    safe = max(0, len(self._buffer) - 3)
                    output_text += self._buffer[:safe]
                    self._buffer = self._buffer[safe:]
                    break
            else:
                # 在 plan 围栏内，查找闭合
                idx = self._buffer.find("```")
                if idx >= 0:
                    self._plan_buffer += self._buffer[:idx]
                    self._buffer = self._buffer[idx + 3:]
                    self._in_plan = False
                    plan_result = self._plan_buffer.strip()
                    self._plan_buffer = ""
                else:
                    self._plan_buffer += self._buffer
                    self._buffer = ""
                    break

        return output_text, plan_result

    def flush(self) -> tuple[str, str | None]:
        """Flush remaining buffer at end of stream."""
        text = ""
        plan = None
        if self._in_plan:
            # Unclosed fence — try to parse as plan anyway
            plan = self._plan_buffer.strip()
        else:
            text = self._buffer
        self._buffer = ""
        self._plan_buffer = ""
        self._in_plan = False
        return text, plan


# ── 事件推送 ────────────────────────────────────────────────

def _push_event(turn_id: str, event: dict[str, Any]) -> None:
    """推送事件到 turn 的队列。不带 seq 的事件自动分配一个，供断线重连去重。"""
    queue = get_event_queue(turn_id)
    data = event.get("data")
    if isinstance(data, dict) and "seq" not in data:
        data = {**data, "seq": _next_seq(turn_id)}
        event = {**event, "data": data}
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass  # 不丢失旧事件，但丢弃溢出


def _save_step(db: Session, turn_id: str, seq: int, kind: str, title: str,
               payload: dict, status: str = "done", tokens_in: int = 0,
               tokens_out: int = 0) -> None:
    """保存步骤到数据库。"""
    step = AgentStep(
        turn_id=turn_id, seq=seq, kind=kind, title=title[:240],
        payload=payload, status=status, tokens_in=tokens_in, tokens_out=tokens_out,
    )
    db.add(step)
    db.commit()


# ── 编排主循环 ──────────────────────────────────────────────

async def orchestrate_turn(
    turn_id: str, user_id: str, user_input: str, user_role: str,
    surface: str, target_id: str, agent_model_id: str | None,
    reference_media: list[dict], target_duration: int | None,
    autonomy: str,
) -> None:
    """运行编排状态机，推送 SSE 事件。"""
    queue = get_event_queue(turn_id)
    # 贯穿整轮的统计，供分支处理函数累积、finally 落库
    stats = {"tokens_in": 0, "tokens_out": 0, "reasoning": 0, "tool_calls": 0}

    try:
        db = SessionLocal()
        # ── S1: 意图分类 ──
        seq = _next_seq(turn_id)
        _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "classify", "title": "理解意图"}})

        # 检查是否有上一版计划
        prior_plan = None
        prior_msgs = (
            db.query(AgentChatMessage)
            .filter(AgentChatMessage.session_id == (
                db.get(AgentTurn, turn_id).session_id if db.get(AgentTurn, turn_id) else ""
            ))
            .order_by(AgentChatMessage.created_at.desc())
            .limit(20)
            .all()
        ) if db.get(AgentTurn, turn_id) else []
        for msg in prior_msgs:
            if msg.plan and msg.role == "assistant":
                prior_plan = msg.plan
                break

        intent_result = await classify_intent(agent_model_id, user_input, prior_plan is not None)
        intent = intent_result["intent"]

        _push_event(turn_id, {"event": "step.end", "data": {"seq": seq, "status": "done", "intent": intent}})
        _save_step(db, turn_id, seq, "classify", f"意图: {intent}", {"intent": intent, "reason": intent_result["reason"]})

        # 更新 turn 的 intent
        turn = db.get(AgentTurn, turn_id)
        if turn:
            turn.intent = intent
            db.commit()

        # ── 分支处理 ──
        if intent in ("chat", "asset", "diagnose", "unsupported"):
            await _handle_non_generating(
                db, turn_id, user_id, user_role, user_input, intent,
                agent_model_id, prior_plan, prior_msgs, reference_media, target_id,
                stats=stats,
            )
        else:
            # generate / refine
            await _handle_generating(
                db, turn_id, user_id, user_role, user_input, intent,
                agent_model_id, prior_plan, prior_msgs, reference_media,
                target_duration, autonomy, surface, target_id,
                stats=stats,
            )

        # ── 完成 ──
        turn = db.get(AgentTurn, turn_id)
        if turn and turn.status == "draft":
            turn.status = "answered"
            db.commit()

        _push_event(turn_id, {"event": "done", "data": {"turn_id": turn_id}})

    except AgentError as exc:
        logger.warning("Agent 编排失败: %s", exc)
        _push_event(turn_id, {"event": "error", "data": {"message": str(exc)}})
        with SessionLocal() as db:
            turn = db.get(AgentTurn, turn_id)
            if turn:
                turn.status = "answered"
                turn.warning = str(exc)
                db.commit()
    except Exception as exc:
        logger.exception("Agent 编排异常: turn=%s", turn_id)
        _push_event(turn_id, {"event": "error", "data": {"message": f"内部错误: {exc}"}})
    finally:
        try:
            db.close()
        except Exception:
            pass
        # 更新 token 统计
        with SessionLocal() as db:
            turn = db.get(AgentTurn, turn_id)
            if turn:
                turn.tokens_in += stats["tokens_in"]
                turn.tokens_out += stats["tokens_out"]
                turn.reasoning_tokens += stats["reasoning"]
                turn.tool_call_count = stats["tool_calls"]
                db.commit()
        # 延迟清理队列，让 SSE 客户端有机会读取最后的事件
        await asyncio.sleep(5)
        cleanup_event_queue(turn_id)


async def _handle_non_generating(
    db: Session, turn_id: str, user_id: str, user_role: str,
    user_input: str, intent: str, agent_model_id: str | None,
    prior_plan: dict | None, prior_msgs: list, reference_media: list,
    target_id: str, stats: dict | None = None,
) -> None:
    """处理 chat / asset / diagnose / unsupported 四类意图。"""
    if stats is None:
        stats = {"tokens_in": 0, "tokens_out": 0, "reasoning": 0, "tool_calls": 0}

    # 加载对话历史
    messages = [{"role": m.role, "content": m.content} for m in reversed(prior_msgs)]
    messages.append({"role": "user", "content": user_input})

    canvas_result: dict[str, Any] | None = None
    if target_id:
        tool_user = db.get(User, user_id)
        canvas_result = execute_tool(
            "canvas.read", {}, tool_user, db, [], target_id=target_id,
        )
        if "error" not in canvas_result:
            seq = _next_seq(turn_id)
            _push_event(turn_id, {"event": "tool.result", "data": {
                "seq": seq, "ok": True,
                "summary": f"已读取当前画布（{len(canvas_result.get('nodes') or [])} 个节点）",
            }})
            _save_step(db, turn_id, seq, "tool_call", "读取当前画布", {
                "revision": canvas_result.get("revision"),
                "node_count": len(canvas_result.get("nodes") or []),
            })
            messages.append({
                "role": "system",
                "content": "当前画布事实：\n" + json.dumps(canvas_result, ensure_ascii=False)[:8000],
            })

    # S2: 工具调用（asset 意图走检索）
    if intent == "asset":
        url_whitelist = extract_urls_from_input(user_input)
        seq = _next_seq(turn_id)
        _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "tool_call", "title": "检索素材"}})

        # 搜索素材库
        from .agent_tools import library_search
        results = library_search(db, user_id, query=user_input[:50], limit=10)
        _push_event(turn_id, {"event": "tool.result", "data": {"seq": seq, "ok": True, "summary": f"命中 {len(results)} 个素材"}})
        _save_step(db, turn_id, seq, "tool_call", "检索素材", {"results": results})

    elif intent == "diagnose":
        # 读取最近的失败记录
        from .models import Conversation, Message
        seq = _next_seq(turn_id)
        _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "tool_call", "title": "读取失败记录"}})
        failed = (
            db.query(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .filter(Conversation.user_id == user_id, Message.status == "failed")
            .order_by(Message.created_at.desc())
            .limit(3)
            .all()
        )
        failures = [{"error": m.error, "model": m.model, "prompt": m.resolved_prompt[:100]} for m in failed]
        _push_event(turn_id, {"event": "tool.result", "data": {"seq": seq, "ok": True, "summary": f"找到 {len(failures)} 条失败记录"}})
        _save_step(db, turn_id, seq, "tool_call", "读取失败记录", {"failures": failures})

    # S4: 流式回答
    seq = _next_seq(turn_id)
    _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "text", "title": "回答"}})

    system = _build_system_prompt(user_role)
    full_content = ""
    parser = FenceParser()

    # 未配置 Provider 时给固定话术，避免 stream_chat 崩掉整轮
    if not settings.agent_provider_configured:
        if intent == "asset":
            reply = f"当前未连接智能体服务。命中 {len(results)} 条素材记录，可在画布中直接使用。"
        elif intent == "diagnose":
            detail = "；".join(f"{f.get('model', '')}: {str(f.get('error', ''))[:80]}" for f in failures[:3]) or "暂无失败记录。"
            reply = f"当前未连接智能体服务。最近 {len(failures)} 条失败记录：{detail}"
        elif intent == "chat" and canvas_result and "error" not in canvas_result:
            generate_count = sum(1 for node in canvas_result.get("nodes", []) if node.get("type") == "generate")
            output_count = sum(1 for node in canvas_result.get("nodes", []) if node.get("type") == "output")
            reply = (
                f"当前画布版本 {canvas_result.get('revision', 0)}，共有 "
                f"{generate_count} 个生成镜头、{output_count} 个输出节点。"
            )
        else:
            reply = ("当前未连接智能体服务（未配置 Agent Provider）。"
                     "我暂时无法回答对话类问题，但你可以直接描述想生成的视频，我会用规则规划器起草计划。")
        _push_event(turn_id, {"event": "text.delta", "data": {"seq": seq, "text": reply}})
        full_content = reply
    else:
        async for kind, payload in stream_chat(agent_model_id, system, messages):
            if kind == "usage":
                stats["tokens_in"] += payload.get("prompt_tokens", 0)
                stats["tokens_out"] += payload.get("completion_tokens", 0)
                continue
            if kind == "think":
                stats["reasoning"] += len(payload)
                _push_event(turn_id, {"event": "think.delta", "data": {"seq": seq, "text": payload}})
            elif kind == "text":
                text_part, plan_json = parser.feed(payload)
                if text_part:
                    _push_event(turn_id, {"event": "text.delta", "data": {"seq": seq, "text": text_part}})
                    full_content += text_part
                if plan_json:
                    plan = _try_parse_json(plan_json)
                    if plan:
                        _push_event(turn_id, {"event": "plan.draft", "data": {"turn_id": turn_id, "plan": plan}})

        # 刷新 parser 残留
        remaining_text, remaining_plan = parser.flush()
        if remaining_text:
            _push_event(turn_id, {"event": "text.delta", "data": {"seq": seq, "text": remaining_text}})
            full_content += remaining_text
        if remaining_plan:
            plan = _try_parse_json(remaining_plan)
            if plan:
                _push_event(turn_id, {"event": "plan.draft", "data": {"turn_id": turn_id, "plan": plan}})

    _push_event(turn_id, {"event": "step.end", "data": {"seq": seq, "status": "done"}})
    _save_step(db, turn_id, seq, "text", "回答", {"content": full_content})

    # 保存对话消息
    session_id = db.get(AgentTurn, turn_id).session_id if db.get(AgentTurn, turn_id) else ""
    db.add(AgentChatMessage(session_id=session_id, turn_id=turn_id, role="user", content=user_input))
    db.add(AgentChatMessage(session_id=session_id, turn_id=turn_id, role="assistant", content=full_content))
    db.commit()


async def _handle_generating(
    db: Session, turn_id: str, user_id: str, user_role: str,
    user_input: str, intent: str, agent_model_id: str | None,
    prior_plan: dict | None, prior_msgs: list, reference_media: list,
    target_duration: int | None, autonomy: str, surface: str, target_id: str,
    stats: dict | None = None,
) -> None:
    """处理 generate / refine 意图 — 走完整 S2→S7 链路。"""
    if stats is None:
        stats = {"tokens_in": 0, "tokens_out": 0, "reasoning": 0, "tool_calls": 0}
    turn_context = db.get(AgentTurn, turn_id)
    agent_control_version = int(turn_context.canvas_control_version or 0) if turn_context else 0

    # 检查 provider 是否配置
    if not settings.agent_provider_configured:
        # 降级：走规则规划器
        if settings.agent_fallback_rules:
            if intent == "refine" and surface == "canvas" and target_id:
                changed = await _fallback_canvas_refine(
                    db, turn_id, user_id, user_input, target_id,
                )
                if changed:
                    return
            await _fallback_generate(db, turn_id, user_id, user_role, user_input,
                                     reference_media, target_duration, autonomy, surface, target_id)
            return
        else:
            raise AgentError("Agent Provider 未配置，请设置 AGENT_BASE_URL、AGENT_API_KEY 或 AGENT_MODELS_JSON")

    # 加载对话历史
    messages = [{"role": m.role, "content": m.content} for m in reversed(prior_msgs)]
    messages.append({"role": "user", "content": user_input})

    # 如果有上一版计划，注入到上下文
    if intent == "refine" and prior_plan:
        messages.append({"role": "system", "content": f"上一版计划：\n```json\n{json.dumps(prior_plan, ensure_ascii=False)}\n```\n请在此基础上修改。"})

    # S2: 工具检索（首轮预注入 catalog，供无 function-calling 模型维持原有行为）
    tool_calls_used = 0
    url_whitelist = extract_urls_from_input(user_input)
    seq = _next_seq(turn_id)
    _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "tool_call", "title": "检索信息"}})

    tool_user = db.get(User, user_id) if user_id else None
    if tool_calls_used < settings.agent_max_tool_calls:
        catalog_result = execute_tool("catalog.describe", {}, tool_user, db, url_whitelist)
        tool_calls_used += 1
    else:
        catalog_result = {"models": []}
    _push_event(turn_id, {"event": "tool.result", "data": {"seq": seq, "ok": True, "summary": "已加载模型目录"}})
    _save_step(db, turn_id, seq, "tool_call", "检索信息", {"catalog": catalog_result})

    # S3: 选技能（LLM 短调用，失败回退关键词匹配）
    from .models import PromptSkill
    skills = db.query(PromptSkill).filter(PromptSkill.enabled.is_(True)).all()
    selected_skill = None
    if skills:
        selected_skill = await select_skill(agent_model_id, user_input, skills)
        if selected_skill:
            seq = _next_seq(turn_id)
            _push_event(turn_id, {"event": "skill.selected", "data": {"seq": seq, "id": selected_skill.id, "label": selected_skill.name}})
            _save_step(db, turn_id, seq, "skill", selected_skill.name, {"skill_id": selected_skill.id})

    # S4: 流式起草计划（带工具调用循环）
    seq = _next_seq(turn_id)
    _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "draft", "title": "起草计划"}})

    system = _build_system_prompt(user_role)
    if surface == "canvas" and target_id:
        system += """

## 当前画布操作
- 需要了解现状时先调用 canvas.read，不能根据旧聊天猜测节点。
- 用户明确要求修改已有画布时，可以调用 canvas.apply_patch；必须原样使用 canvas.read 返回的 revision 和 control_version。
- “第二个镜头”等序号按最终输出节点 items 的片段顺序解析。
- 画布修改完成后说明改动和 operation_id；视频生成仍输出完整计划并走执行闸门。
"""
    if selected_skill:
        system += f"\n\n## 技能规则\n{selected_skill.instructions}"

    full_content = ""
    parser = FenceParser()
    extracted_plan = None
    have_final_answer = False

    # 兼容无 function-calling 的模型：首轮把 catalog 结果内联进上下文
    seeded = json.dumps(catalog_result, ensure_ascii=False)[:3000]
    loop_messages = messages + [{
        "role": "system",
        "content": f"模型目录（catalog.describe 的结果，已代查）：\n{seeded}",
    }]

    # 工具调用循环：模型请求工具 → 执行 → 回喂 → 下一流；不再请求则结束
    while not have_final_answer:
        finished = True
        async for kind, payload in stream_chat(
            agent_model_id, system, loop_messages, tools=TOOL_REGISTRY,
        ):
            if kind == "usage":
                stats["tokens_in"] += payload.get("prompt_tokens", 0)
                stats["tokens_out"] += payload.get("completion_tokens", 0)
                continue
            if kind == "tool_calls":
                finished = False
                for tc in payload:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    stats["tool_calls"] += 1
                    tool_calls_used += 1
                    _push_event(turn_id, {"event": "tool.call", "data": {
                        "seq": seq, "name": name, "args": args,
                    }})
                    result = execute_tool(
                        name, args, tool_user, db, url_whitelist,
                        target_id=target_id,
                        allow_canvas_write=(surface == "canvas" and intent == "refine"),
                        agent_control_version=agent_control_version,
                    )
                    ok = "error" not in result if isinstance(result, dict) else False
                    _push_event(turn_id, {"event": "tool.result", "data": {
                        "seq": seq, "ok": ok, "summary": _summarize_tool_result(name, result),
                    }})
                    _save_step(db, turn_id, seq, "tool_call", f"调用 {name}", {"args": args, "result_keys": list(result.keys()) if isinstance(result, dict) else []})
                    if name == "canvas.apply_patch" and ok:
                        _push_event(turn_id, {"event": "canvas.changed", "data": {
                            "canvas_id": target_id,
                            "revision": result.get("revision"),
                            "operation_id": result.get("operation_id"),
                            "summary": result.get("summary", ""),
                        }})
                    provider_tc = {
                        **tc,
                        "function": {**fn, "name": name.replace(".", "__")},
                    }
                    loop_messages = loop_messages + [
                        {"role": "assistant", "tool_calls": [provider_tc]},
                        {"role": "tool", "name": name.replace(".", "__"), "content": json.dumps(result, ensure_ascii=False)[:4000]},
                    ]
                continue
            if kind == "think":
                stats["reasoning"] += len(payload)
                _push_event(turn_id, {"event": "think.delta", "data": {"seq": seq, "text": payload}})
            elif kind == "text":
                text_part, plan_json = parser.feed(payload)
                if text_part:
                    _push_event(turn_id, {"event": "text.delta", "data": {"seq": seq, "text": text_part}})
                    full_content += text_part
                if plan_json:
                    extracted_plan = _try_parse_json(plan_json)
                    if extracted_plan:
                        cost, seconds = estimate(extracted_plan)
                        _push_event(turn_id, {"event": "plan.draft", "data": {
                            "turn_id": turn_id, "plan": extracted_plan,
                            "est_cost": cost, "est_seconds": seconds,
                        }})
        if finished:
            have_final_answer = True
        elif tool_calls_used >= settings.agent_max_tool_calls:
            # 超过轮次上限，不再发起下一流；当前已是工具响应尾
            have_final_answer = True

    # 刷新残留
    remaining_text, remaining_plan = parser.flush()
    if remaining_text:
        _push_event(turn_id, {"event": "text.delta", "data": {"seq": seq, "text": remaining_text}})
        full_content += remaining_text
    if remaining_plan:
        extracted_plan = _try_parse_json(remaining_plan)
        if extracted_plan:
            cost, seconds = estimate(extracted_plan)
            _push_event(turn_id, {"event": "plan.draft", "data": {
                "turn_id": turn_id, "plan": extracted_plan, "est_cost": cost, "est_seconds": seconds
            }})

    _push_event(turn_id, {"event": "step.end", "data": {"seq": seq, "status": "done"}})
    _save_step(db, turn_id, seq, "draft", "起草计划", {"content": full_content})

    # 保存对话消息
    turn = db.get(AgentTurn, turn_id)
    session_id = turn.session_id if turn else ""
    db.add(AgentChatMessage(session_id=session_id, turn_id=turn_id, role="user", content=user_input))
    assistant_message = AgentChatMessage(
        session_id=session_id, turn_id=turn_id, role="assistant",
        content=full_content, plan=extracted_plan,
    )
    db.add(assistant_message)

    # S5: 校验
    plan_validated = False
    if extracted_plan:
        seq = _next_seq(turn_id)
        _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "validate", "title": "校验计划"}})

        try:
            cost, seconds = validate_plan(db, tool_user, None, extracted_plan)
            _push_event(turn_id, {"event": "step.end", "data": {"seq": seq, "status": "done"}})
            _save_step(db, turn_id, seq, "validate", "校验通过", {"cost": cost, "seconds": seconds})

            # 更新 turn 的计划和估算
            if turn:
                turn.plan = extracted_plan
                turn.est_cost = cost
                turn.est_seconds = seconds
                turn.plan_version = ""
                db.commit()
                plan_validated = True

            _push_event(turn_id, {"event": "plan.draft", "data": {
                "turn_id": turn_id, "plan": extracted_plan, "est_cost": cost, "est_seconds": seconds,
                "validated": True,
            }})

        except Exception as exc:
            _push_event(turn_id, {"event": "validate.error", "data": {"seq": seq, "detail": str(exc)}})
            _save_step(db, turn_id, seq, "validate", "校验失败", {"error": str(exc)}, status="failed")

            # S6: 修复（简化版 — 只重试一次）
            if settings.agent_max_repair_attempts > 0:
                seq = _next_seq(turn_id)
                _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "repair", "title": "修复计划"}})

                repair_prompt = f"原计划未通过校验：{exc}。请修正后只输出完整 JSON 计划。\n原始目标：{user_input}"
                repair_messages = messages + [{"role": "assistant", "content": full_content},
                                              {"role": "user", "content": repair_prompt}]

                repaired_content = ""
                async for kind, delta in stream_chat(agent_model_id, system, repair_messages):
                    if kind == "text":
                        repaired_content += delta

                repaired_plan = _extract_plan(repaired_content)
                if repaired_plan:
                    try:
                        cost, seconds = validate_plan(db, tool_user, None, repaired_plan)
                        _push_event(turn_id, {"event": "step.end", "data": {"seq": seq, "status": "done"}})
                        _save_step(db, turn_id, seq, "repair", "修复成功", {"plan": repaired_plan})

                        if turn:
                            turn.plan = repaired_plan
                            turn.est_cost = cost
                            turn.est_seconds = seconds
                            turn.repair_count = 1
                            turn.plan_version = ""
                            assistant_message.plan = repaired_plan
                            db.commit()

                        extracted_plan = repaired_plan
                        plan_validated = True
                        _push_event(turn_id, {"event": "plan.draft", "data": {
                            "turn_id": turn_id, "plan": extracted_plan, "est_cost": cost, "est_seconds": seconds,
                            "validated": True, "repaired": True,
                        }})
                    except Exception as exc2:
                        _save_step(db, turn_id, seq, "repair", "修复失败", {"error": str(exc2)}, status="failed")
                        if turn:
                            turn.warning = f"计划校验失败: {exc2}"
                            db.commit()
                else:
                    _save_step(db, turn_id, seq, "repair", "未提取到计划", {}, status="failed")
            else:
                if turn:
                    turn.warning = f"计划校验失败: {exc}"
                    db.commit()

    # S7: 闸门
    if plan_validated and extracted_plan and turn:
        cost = turn.est_cost
        if autonomy == "auto" and cost > settings.agent_max_auto_cost:
            _push_event(turn_id, {"event": "gate", "data": {
                "mode": "ask", "cost": cost, "blocked_reason": f"预估 ¥{cost:.2f} 超过自动执行上限 ¥{settings.agent_max_auto_cost:.2f}",
            }})
            # 降级为 Ask
            autonomy = "ask"
        else:
            _push_event(turn_id, {"event": "gate", "data": {
                "mode": autonomy, "cost": turn.est_cost,
                "seconds": turn.est_seconds,
            }})
    elif plan_validated:
        _push_event(turn_id, {"event": "gate", "data": {
            "mode": autonomy, "cost": turn.est_cost if turn else 0,
            "seconds": turn.est_seconds if turn else 0,
        }})

    db.commit()

    # Auto 模式下直接执行
    if autonomy == "auto" and plan_validated and extracted_plan and turn:
        from .agent_executor import spawn_run
        user = db.get(User, user_id)
        try:
            run, created = accept_plan(db, turn, user, None, auto=True)
        except AutoApprovalRequired as exc:
            _push_event(turn_id, {"event": "gate", "data": {
                "mode": "ask", "cost": turn.est_cost,
                "blocked_reason": exc.detail,
            }})
        except AgentRunError as exc:
            turn.warning = exc.detail
            db.commit()
            _push_event(turn_id, {"event": "error", "data": {"message": exc.detail}})
        else:
            _push_event(turn_id, {"event": "run.started", "data": {
                "turn_id": turn_id, "run_id": run.id, "created": created,
            }})
            if created:
                spawn_run(run.id)


async def _fallback_generate(
    db: Session, turn_id: str, user_id: str, user_role: str,
    user_input: str, reference_media: list, target_duration: int | None,
    autonomy: str, surface: str, target_id: str,
) -> None:
    """Provider 未配置时的规则降级。"""
    from .models import User as UserModel
    seq = _next_seq(turn_id)
    _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "draft", "title": "规则规划器"}})

    user = db.get(UserModel, user_id)
    skill_id, plan = _fallback_plan(user, user_input, reference_media, target_duration)

    try:
        cost, seconds = validate_plan(db, user, None, plan)
    except Exception as exc:
        _push_event(turn_id, {"event": "validate.error", "data": {"seq": seq, "detail": str(exc)}})
        _save_step(db, turn_id, seq, "draft", "规则计划校验失败", {"error": str(exc)}, status="failed")
        turn = db.get(AgentTurn, turn_id)
        if turn:
            turn.warning = str(exc)
            db.commit()
        return

    turn = db.get(AgentTurn, turn_id)
    if turn:
        turn.plan = plan
        turn.plan_version = ""
        turn.skill_id = skill_id
        turn.est_cost = cost
        turn.est_seconds = seconds
    _push_event(turn_id, {"event": "plan.draft", "data": {
        "turn_id": turn_id, "plan": plan, "est_cost": cost,
        "est_seconds": seconds, "validated": True,
    }})
    _push_event(turn_id, {"event": "step.end", "data": {"seq": seq, "status": "done"}})
    _save_step(db, turn_id, seq, "draft", "规则规划器", {"plan": plan, "cost": cost, "seconds": seconds})

    # 保存对话消息
    session_id = turn.session_id if turn else ""
    db.add(AgentChatMessage(session_id=session_id, turn_id=turn_id, role="user", content=user_input))
    db.add(AgentChatMessage(
        session_id=session_id, turn_id=turn_id, role="assistant",
        content="规则规划器已生成计划。", plan=plan,
    ))
    db.commit()

    if autonomy == "auto" and cost > settings.agent_max_auto_cost:
        _push_event(turn_id, {"event": "gate", "data": {
            "mode": "ask", "cost": cost,
            "blocked_reason": f"预估 ¥{cost:.2f} 超过自动执行上限 ¥{settings.agent_max_auto_cost:.2f}",
        }})
        autonomy = "ask"
    else:
        _push_event(turn_id, {"event": "gate", "data": {
            "mode": autonomy, "cost": cost, "seconds": seconds,
        }})

    if autonomy == "auto" and turn and user:
        from .agent_executor import spawn_run
        try:
            run, created = accept_plan(db, turn, user, None, auto=True)
        except AutoApprovalRequired as exc:
            _push_event(turn_id, {"event": "gate", "data": {
                "mode": "ask", "cost": cost, "blocked_reason": exc.detail,
            }})
        except AgentRunError as exc:
            turn.warning = exc.detail
            db.commit()
            _push_event(turn_id, {"event": "error", "data": {"message": exc.detail}})
        else:
            _push_event(turn_id, {"event": "run.started", "data": {
                "turn_id": turn_id, "run_id": run.id, "created": created,
            }})
            if created:
                spawn_run(run.id)


async def _fallback_canvas_refine(
    db: Session, turn_id: str, user_id: str, user_input: str, canvas_id: str,
) -> bool:
    """Apply a small, explicit prompt edit when no Agent provider is configured."""
    from .canvas_service import CanvasServiceError, apply_agent_patch, canvas_snapshot
    from .models import Canvas

    user = db.get(User, user_id)
    canvas = db.get(Canvas, canvas_id)
    if user is None or canvas is None or canvas.user_id != user.id:
        return False
    turn = db.get(AgentTurn, turn_id)
    if turn is None or int(turn.canvas_control_version or 0) != int(canvas.control_version or 0):
        return False
    graph = canvas_snapshot(canvas)
    nodes = {node["id"]: node for node in graph["nodes"]}
    output = next((node for node in graph["nodes"] if node["type"] == "output"), None)
    ordered = [
        str(item.get("nodeKey") or "")
        for item in ((output or {}).get("data", {}).get("items") or [])
        if str(item.get("nodeKey") or "") in nodes
    ]
    if not ordered:
        ordered = [node["id"] for node in graph["nodes"] if node["type"] == "generate"]
    if not ordered:
        return False

    ordinal_map = {"第一": 0, "第1": 0, "第二": 1, "第2": 1, "第三": 2, "第3": 2, "第四": 3, "第4": 3}
    index = next((value for key, value in ordinal_map.items() if key in user_input), 0)
    if index >= len(ordered):
        return False
    node_id = ordered[index]
    node = nodes[node_id]
    old_prompt = str((node.get("data") or {}).get("inlinePrompt") or "").strip()
    new_prompt = f"{old_prompt}。修改要求：{user_input}" if old_prompt else user_input
    operations: list[dict[str, Any]] = [
        {"op": "update_node", "node_id": node_id, "data": {"inlinePrompt": new_prompt}},
    ]
    prompt_source = next((
        edge["source"] for edge in graph["edges"]
        if edge["target"] == node_id and edge["target_handle"] == "prompt"
        and nodes.get(edge["source"], {}).get("type") == "prompt"
    ), "")
    if prompt_source:
        operations.append({"op": "update_node", "node_id": prompt_source, "data": {"text": new_prompt}})
    try:
        operation, _ = apply_agent_patch(
            db, canvas, user,
            base_revision=graph["revision"], control_version=graph["control_version"],
            idempotency_key=f"fallback-refine-{turn_id}", operations=operations,
        )
    except CanvasServiceError:
        return False

    seq = _next_seq(turn_id)
    _push_event(turn_id, {"event": "step.start", "data": {"seq": seq, "kind": "tool_call", "title": "修改当前画布"}})
    _push_event(turn_id, {"event": "canvas.changed", "data": {
        "canvas_id": canvas.id, "revision": canvas.revision,
        "operation_id": operation.id, "summary": operation.summary,
    }})
    reply = f"已按要求更新第 {index + 1} 个镜头，可在画布中检查；本次修改可以撤销。"
    _push_event(turn_id, {"event": "text.delta", "data": {"seq": seq, "text": reply}})
    _push_event(turn_id, {"event": "step.end", "data": {"seq": seq, "status": "done"}})
    _save_step(db, turn_id, seq, "tool_call", "修改当前画布", {"operation_id": operation.id, "node_id": node_id})
    if turn:
        db.add(AgentChatMessage(session_id=turn.session_id, turn_id=turn_id, role="user", content=user_input))
        db.add(AgentChatMessage(session_id=turn.session_id, turn_id=turn_id, role="assistant", content=reply))
        db.commit()
    return True
