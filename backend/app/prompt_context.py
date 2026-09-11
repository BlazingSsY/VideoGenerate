"""多轮对话记忆：把历史提示词 + 本轮修改要求合并成一条完整提示词。"""
import logging

from sqlalchemy.orm import Session

from .config import settings
from .dashscope import chat
from .models import Message

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是视频生成提示词专家。用户会在多轮对话里围绕同一个视频创意不断修改。\n"
    "请根据历史提示词和用户的最新输入，输出一条完整、自洽、可直接提交给视频生成模型的中文提示词。\n"
    "规则：\n"
    "1. 若最新输入是修改要求（如“把猫换成狗”“节奏再快一点”），在上一版提示词基础上应用修改，"
    "保留所有未被修改的画面、风格、运镜等细节；\n"
    "2. 若最新输入是全新创意，直接基于它撰写，不要沿用旧内容；\n"
    "3. 保留提示词中形如 [Image 1] 的参考图占位符；\n"
    "4. 只输出提示词正文，不要解释、不要引号、不要任何前缀；\n"
    "5. 篇幅控制在 400 字以内。"
)


def history_pairs(db: Session, conversation_id: str, limit: int) -> list[tuple[str, str]]:
    """返回 [(用户输入, 实际使用的提示词), ...]，按时间正序，只取成功或已提交的轮次。"""
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [(r.prompt, r.resolved_prompt or r.prompt) for r in rows if r.prompt]


# 出现这些词通常说明用户是在修改上一版，而不是提出新创意
MODIFY_HINTS = (
    "改", "换", "调整", "再", "增加", "加上", "去掉", "删掉", "删除", "变成",
    "更", "不要", "替换", "保持", "继续", "加个", "加点", "换成", "改成",
)


def _fallback(previous: str, latest: str) -> str:
    """没有可用文本模型时的降级合并策略：短输入或带修改词的输入视为增量修改。"""
    if not previous:
        return latest
    incremental = len(latest) <= 30 or (
        len(latest) <= 80 and any(word in latest for word in MODIFY_HINTS)
    )
    if incremental:
        return f"{previous}\n\n在以上画面基础上进行如下调整：{latest}"
    return latest


async def resolve_prompt(
    db: Session,
    conversation_id: str,
    latest: str,
    api_key: str,
    use_context: bool,
) -> str:
    if not use_context:
        return latest

    pairs = history_pairs(db, conversation_id, settings.context_max_turns)
    if not pairs:
        return latest

    previous = pairs[-1][1]
    if not settings.context_enabled:
        return _fallback(previous, latest)

    key = settings.context_api_key or api_key
    if not key:
        return _fallback(previous, latest)

    lines = ["以下是本次对话的历史记录："]
    for idx, (raw, resolved) in enumerate(pairs, start=1):
        lines.append(f"第{idx}轮用户输入：{raw}")
        lines.append(f"第{idx}轮实际使用的提示词：{resolved}")
    lines.append(f"用户最新输入：{latest}")
    lines.append("请输出本轮应当使用的完整提示词。")

    try:
        content = await chat(
            key,
            settings.context_model,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ],
        )
    except Exception as exc:  # 改写失败不应阻断视频生成
        logger.warning("提示词改写失败，回退到简单拼接：%s", exc)
        return _fallback(previous, latest)

    return content or _fallback(previous, latest)
