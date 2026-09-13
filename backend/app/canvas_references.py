"""Stable canvas reference tokens -> provider prompt ordinals and media order.

Wan 3: https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference
HappyHorse: https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference
MiniMax documents typed media order, but no special reference-token syntax;
use explicit natural-language ordinals with the accompanying reference legend.
"""
import re

from fastapi import HTTPException

KINDS = {"image": "图片", "video": "视频", "audio": "音频"}
TOKEN = re.compile(r"\{\{([^{}]+)\}\}")


def reference_tag(model: str, kind: str, number: int) -> str:
    if model.startswith("happyhorse"):
        return f"[{dict(image='Image', video='Video', audio='Audio')[kind]} {number}]"
    if model.startswith("wan"):
        return f"{dict(image='图', video='视频', audio='音频')[kind]} {number}"
    return f"第 {number} {dict(image='张参考图片', video='段参考视频', audio='段参考音频')[kind]}"


def bind_references(model: str, prompt: str, media: list[dict], bindings: list[dict]) -> tuple[str, list[dict]]:
    if not isinstance(bindings, list):
        raise HTTPException(400, "素材引用设置格式不正确")
    by_edge = {}
    used = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise HTTPException(400, "素材引用设置格式不正确")
        edge_id = binding.get("edge_id")
        alias = binding.get("alias")
        description = binding.get("description", "")
        if not isinstance(edge_id, str) or not isinstance(alias, str) or not re.fullmatch(r"[\w\-]{1,40}", alias):
            raise HTTPException(400, "素材引用名称或连线无效")
        if edge_id in by_edge or alias in used:
            raise HTTPException(400, "素材引用名称或连线不能重复")
        if not isinstance(description, str) or len(description) > 200:
            raise HTTPException(400, "素材用途不能超过 200 个字符")
        by_edge[edge_id] = binding
        used.add(alias)
    rank = {item["edge_id"]: index for index, item in enumerate(bindings)}
    ordered = []
    tags = {}
    legend = []
    for kind, label in KINDS.items():
        group = sorted((item for item in media if item["kind"] == kind),
                       key=lambda item: rank.get(item.get("_edge_id"), len(bindings)))
        for index, item in enumerate(group, 1):
            binding = by_edge.get(item.get("_edge_id"), {})
            number = 1
            while f"{label}{number}" in used:
                number += 1
            alias = binding.get("alias") or f"{label}{number}"
            used.add(alias)
            tag = reference_tag(model, kind, index)
            tags[alias] = tag
            name = item.get("name") or f"{label}素材"
            legend.append(f"{tag}：{binding.get('description', '').strip() or name}")
            ordered.append({key: value for key, value in item.items() if key != "_edge_id"})

    def replace(match):
        alias = match[1].strip()
        if alias not in tags:
            raise HTTPException(400, f"引用素材「{alias}」已断开或不存在，请重新连接或修改提示词")
        return tags[alias]

    explicit = bool(bindings) or bool(TOKEN.search(prompt))
    prompt = TOKEN.sub(replace, prompt)
    if explicit and legend:
        prompt += "\n\n参考素材对应关系（各类型分别按发送顺序编号）：\n" + "\n".join(legend)
    return prompt, ordered
