"""Canvas graph validation shared by persistence and execution."""

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from typing import Any

from .catalog import get_model

MAX_CANVAS_NODES = 200
P0_NODE_TYPES = {"prompt", "image", "video", "audio", "generate", "output"}


class GraphValidationError(ValueError):
    pass


def media_handles(node: Mapping[str, Any] | Any) -> dict[str, list[str]]:
    """Return declared dynamic handles, with legacy fixed-slot compatibility."""
    data = node.get("data") if isinstance(node, Mapping) else getattr(node, "data", None)
    data = data or {}
    declared = data.get("media_slots")
    model = get_model(str(data.get("model", "")))
    capability = model.capability(str(data.get("capability", ""))) if model else None
    if capability is None:
        return {}
    result: dict[str, list[str]] = {}
    for spec in capability.input_specs():
        values = declared.get(spec.kind) if isinstance(declared, dict) else None
        if isinstance(values, list):
            handles = [str(value) for value in values]
            if any(not value.startswith(f"{spec.kind}_") for value in handles):
                raise GraphValidationError(f"{spec.label}槽位格式无效")
            if len(handles) > spec.max_count:
                raise GraphValidationError(f"{spec.label}最多允许 {spec.max_count} 个槽位")
            result[spec.kind] = handles
        else:
            result[spec.kind] = [f"{spec.kind}_{index}" for index in range(spec.max_count)]
    return result


# Handle names are the single source of truth shared with the frontend canvas:
# source handles: prompt / image / end_frame / video / audio (kind id), generate — output;
# target handles: prompt, {kind}_{index} media slots, output node — input.
_KIND_SOURCE_HANDLES = {"image", "end_frame", "video", "audio"}


def _output_type(node: Mapping[str, Any], handle: str) -> str | None:
    node_type = node.get("type")
    if node_type == "prompt":
        return "text" if handle == "prompt" else None
    if node_type in ("image", "video", "audio", "end_frame"):
        # the source handle id is simply the media kind
        if node_type == "image" and handle == "image":
            # image 节点仅保留单一 source handle；它可同时充当首帧图或尾帧图，
            # 因此对 end_frame 槽也是一种合法源（目标槽的语义由 generate 侧决定）
            return "image-or-end-frame"
        return handle if handle in _KIND_SOURCE_HANDLES else None
    if node_type == "generate":
        return "video" if handle == "output" else None
    return None


def _input_type(node: Mapping[str, Any], handle: str) -> str | None:
    if node.get("type") == "output":
        return "video" if handle == "input" else None
    if node.get("type") != "generate":
        return None
    return "text" if handle == "prompt" else _media_input_type(node, handle)


def _media_input_type(node: Mapping[str, Any], handle: str) -> str | None:
    data = node.get("data") or {}
    model = get_model(str(data.get("model", "")))
    capability = model.capability(str(data.get("capability", ""))) if model else None
    if capability is None:
        return None
    # end_frame slots pair with the image node's dedicated end_frame source
    # handle — both sides speak the "end_frame" type (see _output_type).
    for kind, handles in media_handles(node).items():
        if handle in handles:
            return kind
    return None


def _is_aggregate_slot(node: Mapping[str, Any], handle: str) -> bool:
    """此媒体槽是否为聚合槽：其 kind 的 spec.max_count > 1（r2v 参考素材）。"""
    data = node.get("data") or {}
    model = get_model(str(data.get("model", "")))
    capability = model.capability(str(data.get("capability", ""))) if model else None
    if capability is None or handle == "prompt":
        return False
    for kind, handles in media_handles(node).items():
        if handle in handles:
            spec = next((s for s in capability.input_specs() if s.kind == kind), None)
            return bool(spec and spec.max_count > 1)
    return False


def validate_graph(
    nodes: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]]
) -> None:
    node_list = list(nodes)
    edge_list = list(edges)
    if len(node_list) > MAX_CANVAS_NODES:
        raise GraphValidationError(f"画布最多允许 {MAX_CANVAS_NODES} 个节点")

    by_id: dict[str, Mapping[str, Any]] = {}
    for node in node_list:
        node_id = str(node.get("id", ""))
        if not node_id or node_id in by_id:
            raise GraphValidationError("节点 id 不能为空且不能重复")
        if node.get("type") not in P0_NODE_TYPES:
            raise GraphValidationError(f"暂不支持节点类型：{node.get('type')}")
        by_id[node_id] = node

    occupied_targets: set[tuple[str, str]] = set()
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in by_id}
    edge_ids: set[str] = set()
    seen_pairs: set[tuple[str, str, str, str]] = set()

    for edge in edge_list:
        edge_id = str(edge.get("id", ""))
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        source_handle = str(edge.get("source_handle", edge.get("sourceHandle", "")))
        target_handle = str(edge.get("target_handle", edge.get("targetHandle", "")))
        if not edge_id or edge_id in edge_ids:
            raise GraphValidationError("连线 id 不能为空且不能重复")
        edge_ids.add(edge_id)
        if source not in by_id or target not in by_id:
            raise GraphValidationError("连线引用了不存在的节点")
        if source == target:
            raise GraphValidationError("节点不能连接到自身")

        target_key = (target, target_handle)
        # 聚合槽允许多条边共连一个视觉点：output.input（多片段拼接计划）与
        # generate 的聚合媒体槽（r2v 参考图片/视频/音频 — max_count > 1 的 kind，
        # 前端每类只渲染一个连接点）。单槽类（首帧图/尾帧图/prompt）仍一槽一边。
        target_node = by_id[target]
        is_output_input = str(target_node.get("type")) == "output" and target_handle == "input"
        is_aggregate_media = (
            str(target_node.get("type")) == "generate"
            and target_handle != "prompt"
            and _is_aggregate_slot(target_node, target_handle)
        )
        if not is_output_input and not is_aggregate_media and target_key in occupied_targets:
            raise GraphValidationError(f"输入槽 {target_handle} 只能连接一条边")
        if (
            source, source_handle, target, target_handle
        ) in seen_pairs:
            raise GraphValidationError("不能重复连接同一条线")
        seen_pairs.add((source, source_handle, target, target_handle))
        occupied_targets.add(target_key)

        source_type = _output_type(by_id[source], source_handle)
        target_type = _input_type(by_id[target], target_handle)
        if source_type is None or target_type is None:
            raise GraphValidationError("连线使用了无效的输入或输出槽")
        # image-or-end-frame：单一图片源 handle 允许落 image 槽或 end_frame 槽
        compatible = source_type == target_type or (
            source_type == "image-or-end-frame" and target_type in ("image", "end_frame")
        )
        if not compatible:
            raise GraphValidationError(
                f"连线类型不匹配：{source_type} 不能连接到 {target_type}"
            )

        adjacency[source].append(target)
        indegree[target] += 1

    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in adjacency[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(by_id):
        raise GraphValidationError("画布不能包含环形连接")
