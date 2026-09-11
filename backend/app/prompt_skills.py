"""提示词 Skill 深模块：选择校验与提示词增强都收敛在此。"""
import logging

from sqlalchemy.orm import Session

from .config import settings
from .dashscope import chat
from .models import PromptSkill

logger = logging.getLogger(__name__)


class PromptSkillError(ValueError):
    pass


def select_for_generation(
    db: Session, skill_id: str | None, capability: str
) -> PromptSkill | None:
    """校验一次生成请求中的 Skill，并返回可用记录。"""
    if not skill_id:
        return None
    if capability != "t2v":
        raise PromptSkillError("Skill 仅可用于文生视频")
    skill = db.get(PromptSkill, skill_id)
    if skill is None:
        raise PromptSkillError("所选 Skill 不存在")
    if not skill.enabled:
        raise PromptSkillError("所选 Skill 已停用，请重新选择")
    return skill


async def enhance_prompt(prompt: str, instructions: str, api_key: str) -> str:
    """应用 Skill；文本模型异常时回退原提示词，不阻断视频生成。"""
    instructions = instructions.strip()
    if not instructions:
        return prompt

    key = settings.context_api_key or api_key
    if not key:
        return prompt

    system_prompt = (
        "你是视频生成提示词增强器。请严格应用下方 Skill 规则，"
        "把用户提示词改写成一条完整、可直接提交给视频生成模型的中文提示词。\n"
        "保留 [Image 1] 等素材占位符；只输出最终提示词正文，不解释、不加标题，"
        "总长度不超过 400 字。\n\n"
        f"Skill 规则：\n{instructions}"
    )
    try:
        content = await chat(
            key,
            settings.context_model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:  # Skill 是增强项，不应让主生成链路失败
        logger.warning("Skill 提示词增强失败，回退原提示词：%s", exc)
        return prompt
    return content or prompt
