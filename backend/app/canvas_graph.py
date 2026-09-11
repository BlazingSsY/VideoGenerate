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


def _output_type(node: Mapping[str, Any], handle: str) -> str | None:
    if handle != "out":
        return None
    return {
        "prompt": "text",
        "image": "image",
        "video": "video",
        "audio": "audio",
        "generate": "video",
    }.get(node.get("type"))


def _input_type(node: Mapping[str, Any], handle: str) -> str | None:
    if node.get("type") == "output":
        return "video" if handle == "in" else None
    if node.get("type") != "generate":
        return None
    if handle == "prompt":
        return "text"
    data = node.get("data") or {}
    model = get_model(str(data.get("model", "")))
    capability = model.capability(str(data.get("capability", ""))) if model else None
    if capability is None:
        return None
    for kind, handles in media_handles(node).items():
        if handle in handles:
            return "image" if kind == "end_frame" else kind
    return None


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
        if target_key in occupied_targets:
            raise GraphValidationError(f"输入槽 {target_handle} 只能连接一条边")
        occupied_targets.add(target_key)

        source_type = _output_type(by_id[source], source_handle)
        target_type = _input_type(by_id[target], target_handle)
        if source_type is None or target_type is None:
            raise GraphValidationError("连线使用了无效的输入或输出槽")
        if source_type != target_type:
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
