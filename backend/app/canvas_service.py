"""Shared, transactional canvas persistence and Agent graph operations.

Every writer (manual save, plan import and Agent patch/undo) goes through this
module so graph validation, versioning and ownership semantics stay identical.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlalchemy.orm import Session

from .canvas_graph import GraphValidationError, validate_graph
from .catalog import get_model
from .models import (
    AgentCanvasImport,
    AgentTurn,
    Asset,
    Canvas,
    CanvasEdge,
    CanvasNode,
    CanvasOperation,
    User,
)


class CanvasServiceError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def plan_fingerprint(plan: dict[str, Any]) -> str:
    raw = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def own_canvas(db: Session, canvas_id: str, user: User) -> Canvas:
    canvas = db.get(Canvas, canvas_id)
    if canvas is None or canvas.user_id != user.id:
        raise CanvasServiceError(404, "画布不存在")
    return canvas


def canvas_snapshot(canvas: Canvas) -> dict[str, Any]:
    """Return the canonical, unsigned graph representation."""
    return {
        "canvas_id": canvas.id,
        "title": canvas.title,
        "revision": int(canvas.revision or 0),
        "control_version": int(canvas.control_version or 0),
        "updated_at": canvas.updated_at.isoformat() if canvas.updated_at else "",
        "viewport": deepcopy(canvas.viewport or {"x": 0, "y": 0, "zoom": 1}),
        "nodes": [
            {
                "id": node.id,
                "type": node.type,
                "position": deepcopy(node.position or {}),
                "size": deepcopy(node.size),
                "data": deepcopy(node.data or {}),
                "status": node.status,
                "message_id": node.message_id,
                "input_hash": node.input_hash,
            }
            for node in canvas.nodes
        ],
        "edges": [
            {
                "id": edge.id,
                "source": edge.source,
                "source_handle": edge.source_handle,
                "target": edge.target,
                "target_handle": edge.target_handle,
            }
            for edge in canvas.edges
        ],
    }


def _graph_values(graph: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = []
    for raw in graph.get("nodes") or []:
        nodes.append({
            "id": str(raw.get("id", "")),
            "type": str(raw.get("type", "")),
            "position": deepcopy(raw.get("position") or {}),
            "size": deepcopy(raw.get("size")),
            "data": deepcopy(raw.get("data") or {}),
        })
    edges = []
    for raw in graph.get("edges") or []:
        edges.append({
            "id": str(raw.get("id", "")),
            "source": str(raw.get("source", "")),
            "source_handle": str(raw.get("source_handle", raw.get("sourceHandle", ""))),
            "target": str(raw.get("target", "")),
            "target_handle": str(raw.get("target_handle", raw.get("targetHandle", ""))),
        })
    return nodes, edges


def lock_canvas(
    db: Session, canvas: Canvas, *, expected_revision: int | None = None,
    control_version: int | None = None, bump_revision: bool = False,
    expected_updated_at: datetime | None = None,
) -> None:
    """Serialize graph/result writers; check versions in SQL, not the ORM cache.

    The row lock lasts until the caller commits or rolls back. A no-op UPDATE
    also locks the row on SQLite, where SELECT FOR UPDATE is not supported.
    """
    statement = update(Canvas).where(Canvas.id == canvas.id, Canvas.user_id == canvas.user_id)
    if expected_revision is not None:
        statement = statement.where(Canvas.revision == expected_revision)
    if control_version is not None:
        statement = statement.where(Canvas.control_version == control_version)
    if expected_updated_at is not None:
        statement = statement.where(Canvas.updated_at == expected_updated_at)
    values = {"revision": Canvas.revision, "updated_at": Canvas.updated_at}
    if bump_revision:
        values = {"revision": Canvas.revision + 1, "updated_at": utcnow()}
    with db.no_autoflush:
        changed = db.execute(statement.values(**values).execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        db.rollback()
        raise CanvasServiceError(409, "画布版本已变化或用户已人工接手，请重新读取后重试")
    for child in list(canvas.nodes) + list(canvas.edges):
        db.expire(child)
    db.refresh(canvas)
    db.expire(canvas, ["nodes", "edges"])


def graph_input_hash(graph: dict[str, Any], node_id: str) -> str:
    """Fingerprint inputs and data dependencies, excluding layout and results."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = graph["edges"]
    visiting: set[str] = set()

    def inputs(key: str) -> Any:
        node = nodes.get(key)
        if node is None or key in visiting:
            return {"missing_or_cyclic": key}
        visiting.add(key)
        data = node.get("data") or {}
        kind = node["type"]
        incoming = sorted(
            (edge for edge in edges if edge["target"] == key),
            # Stable sorting preserves reference order within aggregate slots,
            # matching prepare_generation's traversal of the saved edge list.
            key=lambda edge: edge["target_handle"],
        )
        if kind == "generate":
            value = {name: data.get(name) for name in (
                "model", "capability", "resolution", "ratio", "duration",
                "watermark", "audio", "inlinePrompt", "media_slots",
            )}
        elif kind == "output":
            value = {
                "items": [item.get("nodeKey") for item in data.get("items") or []],
                "transitions": data.get("transitions") or [],
                "excluded": data.get("excluded") or [],
            }
            value["clips"] = [inputs(key) for key in value["items"]]
        elif kind == "prompt":
            value = {"text": data.get("text", "")}
        else:
            url = str(data.get("url") or "")
            value = {"url": urlsplit(url).path if url.startswith("/media/") else url}
        value["connections"] = [
            [edge["source"], edge["source_handle"], edge["target_handle"], inputs(edge["source"])]
            for edge in incoming
        ]
        visiting.remove(key)
        return {"type": kind, "inputs": value}

    return plan_fingerprint(inputs(node_id))


def persist_canvas_graph(
    db: Session,
    canvas: Canvas,
    graph: dict[str, Any],
    *,
    expected_updated_at: datetime | None = None,
    expected_revision: int | None = None,
    control_version: int | None = None,
    commit: bool = True,
) -> Canvas:
    """Validate a complete graph, then replace it atomically."""
    if expected_updated_at is not None and expected_updated_at != canvas.updated_at:
        raise CanvasServiceError(409, "画布已在其他窗口更新，请刷新后继续编辑")
    if expected_revision is not None and expected_revision != int(canvas.revision or 0):
        raise CanvasServiceError(409, "画布版本已变化，请读取最新画布后重试")

    nodes, edges = _graph_values(graph)
    try:
        validate_graph(nodes, edges)
    except GraphValidationError as exc:
        raise CanvasServiceError(400, str(exc)) from exc

    # Acquire the write lock before touching nodes/edges. A takeover or another
    # save between the initial read and this UPDATE makes the whole batch fail.
    lock_canvas(
        db, canvas,
        expected_revision=int(canvas.revision or 0) if expected_revision is None else expected_revision,
        control_version=control_version, expected_updated_at=expected_updated_at,
        bump_revision=True,
    )
    before = canvas_snapshot(canvas)
    after = {"nodes": nodes, "edges": edges}
    existing_nodes = {node.id: node for node in canvas.nodes}
    incoming_ids = {node["id"] for node in nodes}

    for edge in list(canvas.edges):
        db.delete(edge)
    for node in list(canvas.nodes):
        if node.id not in incoming_ids:
            db.delete(node)

    for item in nodes:
        node = existing_nodes.get(item["id"])
        if node is None:
            node = CanvasNode(
                id=item["id"], canvas_id=canvas.id,
                status="idle" if item["type"] == "generate" else "",
            )
            db.add(node)
        if item["type"] in {"generate", "output"} or node.type in {"generate", "output"}:
            changed_inputs = node.id not in existing_nodes or (
                graph_input_hash(before, node.id) != graph_input_hash(after, node.id)
            )
            # Result fields belong to the server. A layout save may have been
            # prepared before a result arrived; it must neither erase nor restore it.
            result_fields = {key: (node.data or {})[key] for key in ("outputFile",) if node.type == item["type"] and key in (node.data or {})}
            for key in ("outputFile", "outputVideoSrc"):
                item["data"].pop(key, None)
            if changed_inputs:
                node.status = "idle"
                node.message_id = None
                node.input_hash = ""
            else:
                item["data"].update(result_fields)
        node.type = item["type"]
        node.position = item["position"]
        node.size = item["size"]
        node.data = item["data"]

    db.flush()
    for item in edges:
        db.add(CanvasEdge(canvas_id=canvas.id, **item))

    canvas.viewport = deepcopy(graph.get("viewport") or canvas.viewport or {"x": 0, "y": 0, "zoom": 1})
    if commit:
        db.commit()
        db.refresh(canvas)
    else:
        db.flush()
        db.expire(canvas, ["nodes", "edges"])
    return canvas


def _stable_id(canvas_id: str, turn_id: str, kind: str, source_id: str) -> str:
    value = uuid.uuid5(uuid.NAMESPACE_URL, f"vg:{canvas_id}:{turn_id}:{kind}:{source_id}").hex
    return f"ag-{kind[:4]}-{value[:24]}"


def _asset_media(db: Session, user: User, raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        raw = {"kind": "image", "url": raw}
    if not isinstance(raw, dict):
        raise CanvasServiceError(400, "计划中的参考素材格式无效")

    kind = str(raw.get("kind") or "image")
    if kind not in {"image", "end_frame", "video", "audio"}:
        raise CanvasServiceError(400, f"不支持的参考素材类型：{kind}")
    asset_id = str(raw.get("asset_id") or "")
    if asset_id:
        asset = db.get(Asset, asset_id)
        if asset is None or asset.user_id != user.id:
            raise CanvasServiceError(403, "无权使用计划引用的素材")
        url = asset.source_url or (f"/media/uploads/{asset.filename}" if asset.filename else "")
        name = asset.name
        if kind == "image" and asset.kind in {"video", "audio"}:
            kind = asset.kind
    else:
        url = str(raw.get("url") or "").strip()
        name = str(raw.get("name") or "").strip()
    if url.startswith("/media/"):
        # Never persist a signed query string; it will be freshly signed on read.
        url = urlsplit(url).path
    if not url:
        raise CanvasServiceError(400, "计划中的参考素材缺少地址")
    return {"kind": kind, "url": url, "name": name[:255]}


def _media_for_node(db: Session, user: User, plan_node: dict[str, Any]) -> list[dict[str, str]]:
    values = [_asset_media(db, user, item) for item in (plan_node.get("reference_media") or [])]
    end_frame = plan_node.get("end_frame")
    if end_frame and not isinstance(end_frame, bool):
        value = _asset_media(db, user, end_frame)
        value["kind"] = "end_frame"
        values.append(value)
    return values


def import_plan_to_canvas(
    db: Session,
    turn: AgentTurn,
    canvas: Canvas,
    user: User,
    *,
    commit: bool = True,
) -> tuple[AgentCanvasImport, bool]:
    """Merge a validated plan into the current graph and return its stable map."""
    lock_canvas(db, canvas, control_version=int(turn.canvas_control_version or 0))
    version = turn.plan_version or plan_fingerprint(turn.plan or {})
    turn.plan_version = version
    existing = (
        db.query(AgentCanvasImport)
        .filter_by(turn_id=turn.id, canvas_id=canvas.id)
        .one_or_none()
    )
    if existing is not None:
        if existing.plan_version != version:
            raise CanvasServiceError(409, "该轮计划内容已变化，不能覆盖之前导入的画布")
        return existing, False

    plan_nodes = turn.plan.get("nodes") if isinstance(turn.plan, dict) else None
    if not isinstance(plan_nodes, list) or not plan_nodes:
        raise CanvasServiceError(400, "该轮次没有可转换的计划")

    before = canvas_snapshot(canvas)
    graph_nodes = [
        {key: deepcopy(value) for key, value in node.items() if key in {"id", "type", "position", "size", "data"}}
        for node in before["nodes"]
    ]
    graph_edges = deepcopy(before["edges"])
    existing_ids = {node["id"] for node in graph_nodes}
    node_map: dict[str, str] = {}
    prompt_map: dict[str, str] = {}
    media_map: dict[str, list[str]] = {}

    base_x = 120 + (len(graph_nodes) // 10) * 900
    for index, plan_node in enumerate(plan_nodes):
        plan_id = str(plan_node.get("id") or "")
        if not plan_id:
            raise CanvasServiceError(400, "计划节点缺少 id")
        kind = "out" if plan_node.get("type") == "compose" else "gen"
        mapped = _stable_id(canvas.id, turn.id, kind, plan_id)
        if mapped in existing_ids:
            raise CanvasServiceError(409, "计划节点与现有画布节点冲突")
        node_map[plan_id] = mapped

    generate_nodes = [node for node in plan_nodes if node.get("type") == "generate"]
    for index, plan_node in enumerate(generate_nodes):
        plan_id = str(plan_node["id"])
        node_id = node_map[plan_id]
        y = 100 + index * 260
        prompt_id = _stable_id(canvas.id, turn.id, "prompt", plan_id)
        prompt_map[plan_id] = prompt_id
        graph_nodes.append({
            "id": prompt_id, "type": "prompt",
            "position": {"x": base_x, "y": y}, "size": None,
            "data": {"text": str(plan_node.get("prompt") or ""), "agentTurnId": turn.id, "planNodeId": plan_id},
        })

        model = get_model(str(plan_node.get("model") or ""))
        capability_id = str(plan_node.get("capability") or "t2v")
        capability = model.capability(capability_id) if model else None
        slots: dict[str, list[str]] = {}
        if capability:
            for spec in capability.input_specs():
                slots[spec.kind] = [f"{spec.kind}_{i}" for i in range(spec.max_count)]
        graph_nodes.append({
            "id": node_id, "type": "generate",
            "position": {"x": base_x + 360, "y": y}, "size": None,
            "data": {
                "name": str(plan_node.get("reason") or f"镜头 {index + 1}"),
                "model": str(plan_node.get("model") or ""),
                "capability": capability_id,
                "resolution": str(plan_node.get("resolution") or ""),
                "ratio": str(plan_node.get("ratio") or ""),
                "duration": int(plan_node.get("duration") or 0),
                "watermark": bool(plan_node.get("watermark", model.watermark_default if model else False)),
                "audio": bool(plan_node.get("audio", model.audio_default if model else True)),
                "inlinePrompt": str(plan_node.get("prompt") or ""),
                "media_slots": slots,
                "agentTurnId": turn.id,
                "planNodeId": plan_id,
                "planVersion": version,
            },
        })
        graph_edges.append({
            "id": _stable_id(canvas.id, turn.id, "edge", f"prompt:{plan_id}"),
            "source": prompt_id, "source_handle": "prompt",
            "target": node_id, "target_handle": "prompt",
        })

        media_ids: list[str] = []
        per_kind: dict[str, int] = {}
        for media_index, media in enumerate(_media_for_node(db, user, plan_node)):
            media_kind = media["kind"]
            source_type = "image" if media_kind in {"image", "end_frame"} else media_kind
            media_id = _stable_id(canvas.id, turn.id, "media", f"{plan_id}:{media_index}")
            media_ids.append(media_id)
            graph_nodes.append({
                "id": media_id, "type": source_type,
                "position": {"x": base_x, "y": y + 90 + media_index * 70}, "size": None,
                "data": {
                    **({"kind": source_type} if source_type in {"video", "audio"} else {}),
                    "url": media["url"], "name": media["name"] or f"参考素材 {media_index + 1}",
                    "agentTurnId": turn.id, "planNodeId": plan_id,
                },
            })
            spec = None
            if capability:
                spec = next((item for item in capability.input_specs() if item.kind == media_kind), None)
            offset = per_kind.get(media_kind, 0)
            per_kind[media_kind] = offset + 1
            target_handle = f"{media_kind}_0" if spec and spec.max_count > 1 else f"{media_kind}_{offset}"
            graph_edges.append({
                "id": _stable_id(canvas.id, turn.id, "edge", f"media:{plan_id}:{media_index}"),
                "source": media_id,
                "source_handle": "image" if source_type == "image" else source_type,
                "target": node_id, "target_handle": target_handle,
            })
        media_map[plan_id] = media_ids

    compose_nodes = [node for node in plan_nodes if node.get("type") == "compose"]
    output_plan_id = str(turn.plan.get("output_node") or "")
    output_id: str
    output_inputs: list[str]
    if compose_nodes:
        selected = next((node for node in compose_nodes if str(node.get("id")) == output_plan_id), compose_nodes[-1])
        output_plan_id = str(selected["id"])
        output_id = node_map[output_plan_id]
        output_inputs = [str(value) for value in (selected.get("inputs") or [])]
    else:
        output_id = _stable_id(canvas.id, turn.id, "out", "__output__")
        selected_id = output_plan_id or str(generate_nodes[-1]["id"])
        output_inputs = [selected_id]
        node_map["__output__"] = output_id

    item_ids = [node_map[value] for value in output_inputs if value in node_map]
    graph_nodes.append({
        "id": output_id, "type": "output",
        "position": {"x": base_x + 760, "y": 100 + max(0, len(generate_nodes) - 1) * 130},
        "size": None,
        "data": {
            "label": "最终输出",
            "items": [{"nodeKey": item, "label": f"片段 {index + 1}"} for index, item in enumerate(item_ids)],
            "transitions": [], "excluded": [],
            "agentTurnId": turn.id, "planNodeId": output_plan_id, "planVersion": version,
        },
    })
    for index, source_id in enumerate(item_ids):
        graph_edges.append({
            "id": _stable_id(canvas.id, turn.id, "edge", f"output:{index}:{source_id}"),
            "source": source_id, "source_handle": "output",
            "target": output_id, "target_handle": "input",
        })

    persist_canvas_graph(
        db, canvas,
        {"nodes": graph_nodes, "edges": graph_edges, "viewport": before["viewport"]},
        expected_revision=before["revision"], commit=False,
        control_version=int(turn.canvas_control_version or 0),
    )
    record = AgentCanvasImport(
        turn_id=turn.id, canvas_id=canvas.id, plan_version=version,
        node_map={**node_map, "__prompts__": prompt_map, "__media__": media_map},
        canvas_revision=canvas.revision,
    )
    db.add(record)
    if commit:
        db.commit()
        db.refresh(record)
        db.refresh(canvas)
    else:
        db.flush()
    return record, True


def imported_plan_inputs_match(
    db: Session, turn: AgentTurn, imported: AgentCanvasImport,
) -> bool:
    """Check whether manually edited imported nodes still match the accepted plan."""
    mapping = imported.node_map or {}
    for plan_node in (turn.plan or {}).get("nodes", []):
        if not plan_node_inputs_match(db, imported.canvas_id, plan_node, mapping, turn.plan):
            return False
    return True


def _planned_media_values(db: Session, plan_node: dict[str, Any]) -> list[tuple[str, str]]:
    raw_values = list(plan_node.get("reference_media") or [])
    end_frame = plan_node.get("end_frame")
    if end_frame and not isinstance(end_frame, bool):
        raw_values.append(
            {"kind": "end_frame", "url": end_frame}
            if isinstance(end_frame, str)
            else {**end_frame, "kind": "end_frame"}
        )
    result: list[tuple[str, str]] = []
    for raw in raw_values:
        if isinstance(raw, str):
            raw = {"kind": "image", "url": raw}
        if not isinstance(raw, dict):
            return []
        kind = str(raw.get("kind") or "image")
        asset_id = str(raw.get("asset_id") or "")
        if asset_id:
            asset = db.get(Asset, asset_id)
            if asset is None:
                return []
            url = asset.source_url or (f"/media/uploads/{asset.filename}" if asset.filename else "")
            if kind == "image" and asset.kind in {"video", "audio"}:
                kind = asset.kind
        else:
            url = str(raw.get("url") or "")
        if url.startswith("/media/"):
            url = urlsplit(url).path
        result.append((kind, url))
    return result


def plan_node_inputs_match(
    db: Session,
    canvas_id: str,
    plan_node: dict[str, Any],
    mapping: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> bool:
    """Compare one frozen plan task with its current canvas inputs.

    Position/size/viewport are deliberately ignored. Actual generation inputs,
    material connections and output clip ordering are version-sensitive.
    """
    plan_id = str(plan_node.get("id") or "")
    mapped_id = mapping.get(plan_id)
    if not isinstance(mapped_id, str):
        return False
    node = db.get(CanvasNode, mapped_id)
    if node is None or node.canvas_id != canvas_id:
        return False
    frozen_hash = plan_node.get("_canvas_input_hash")
    if frozen_hash:
        canvas = db.get(Canvas, canvas_id)
        return canvas is not None and graph_input_hash(canvas_snapshot(canvas), mapped_id) == frozen_hash

    if plan_node.get("type") == "generate":
        if node.type != "generate":
            return False
        data = node.data or {}
        model = get_model(str(plan_node.get("model") or ""))
        expected = {
            "model": str(plan_node.get("model") or ""),
            "capability": str(plan_node.get("capability") or "t2v"),
            "resolution": str(plan_node.get("resolution") or ""),
            "ratio": str(plan_node.get("ratio") or ""),
            "duration": int(plan_node.get("duration") or 0),
            "watermark": bool(plan_node.get("watermark", model.watermark_default if model else False)),
            "audio": bool(plan_node.get("audio", model.audio_default if model else True)),
        }
        if any(data.get(key) != value for key, value in expected.items()):
            return False
        prompt = str(plan_node.get("prompt") or "")
        if str(data.get("inlinePrompt") or "") != prompt:
            return False
        prompt_mapping = mapping.get("__prompts__") or {}
        prompt_id = prompt_mapping.get(plan_id)
        prompt_node = db.get(CanvasNode, prompt_id) if isinstance(prompt_id, str) else None
        if (
            prompt_node is None
            or prompt_node.canvas_id != canvas_id
            or str((prompt_node.data or {}).get("text") or "") != prompt
        ):
            return False

        expected_media = _planned_media_values(db, plan_node)
        media_mapping = mapping.get("__media__") or {}
        media_ids = media_mapping.get(plan_id) or []
        if len(media_ids) != len(expected_media):
            return False
        edges = list(db.query(CanvasEdge).filter(CanvasEdge.canvas_id == canvas_id, CanvasEdge.target == mapped_id))
        expected_edges = [(prompt_id, "prompt", "prompt")]
        per_kind: dict[str, int] = {}
        capability = model.capability(str(plan_node.get("capability") or "t2v")) if model else None
        for media_id, (kind, url) in zip(media_ids, expected_media):
            media_node = db.get(CanvasNode, media_id)
            expected_type = "image" if kind in {"image", "end_frame"} else kind
            if (
                media_node is None
                or media_node.canvas_id != canvas_id
                or media_node.type != expected_type
                or str((media_node.data or {}).get("url") or "") != url
            ):
                return False
            spec = next((item for item in capability.input_specs() if item.kind == kind), None) if capability else None
            offset = per_kind.get(kind, 0)
            per_kind[kind] = offset + 1
            handle = f"{kind}_0" if spec and spec.max_count > 1 else f"{kind}_{offset}"
            expected_edges.append((media_id, "image" if expected_type == "image" else expected_type, handle))
        actual_edges = [(edge.source, edge.source_handle, edge.target_handle) for edge in edges]
        if sorted(expected_edges) != sorted(actual_edges):
            return False
        return all(
            [edge[:2] for edge in expected_edges if edge[2] == handle]
            == [edge[:2] for edge in actual_edges if edge[2] == handle]
            for handle in {edge[2] for edge in expected_edges}
        )

    if plan_node.get("type") == "compose":
        if node.type != "output":
            return False
        expected_items = [
            mapping.get(str(source_id)) for source_id in (plan_node.get("inputs") or [])
        ]
        actual_items = [
            str(item.get("nodeKey") or "") for item in ((node.data or {}).get("items") or [])
        ]
        if not all(isinstance(item, str) for item in expected_items) or actual_items != expected_items:
            return False
        if (node.data or {}).get("transitions") or (node.data or {}).get("excluded"):
            return False
        edges = db.query(CanvasEdge).filter_by(canvas_id=canvas_id, target=mapped_id).all()
        if sorted((edge.source, edge.source_handle, edge.target_handle) for edge in edges) != sorted(
            (item, "output", "input") for item in expected_items
        ):
            return False
        by_id = {str(item["id"]): item for item in (plan or {}).get("nodes", [])}
        return all(
            str(source) in by_id and by_id[str(source)].get("type") == "generate"
            and plan_node_inputs_match(db, canvas_id, by_id[str(source)], mapping, plan)
            for source in plan_node.get("inputs") or []
        )
    return False


def apply_agent_patch(
    db: Session,
    canvas: Canvas,
    user: User,
    *,
    base_revision: int,
    control_version: int,
    idempotency_key: str,
    operations: list[dict[str, Any]],
) -> tuple[CanvasOperation, bool]:
    if not idempotency_key:
        raise CanvasServiceError(400, "画布操作缺少幂等键")
    if not operations:
        raise CanvasServiceError(400, "画布操作不能为空")
    existing = (
        db.query(CanvasOperation)
        .filter_by(canvas_id=canvas.id, idempotency_key=idempotency_key)
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    if int(canvas.control_version or 0) != control_version:
        raise CanvasServiceError(409, "用户已接管画布，本次智能体操作已失效")
    if int(canvas.revision or 0) != base_revision:
        raise CanvasServiceError(409, "画布已发生变化，请重新读取后再修改")

    before = canvas_snapshot(canvas)
    nodes = {
        node["id"]: {key: deepcopy(value) for key, value in node.items() if key in {"id", "type", "position", "size", "data"}}
        for node in before["nodes"]
    }
    edges = {edge["id"]: deepcopy(edge) for edge in before["edges"]}
    summaries: list[str] = []

    for operation in operations:
        op = operation.get("op")
        node_id = str(operation.get("node_id") or "")
        if op == "add_node":
            raw = operation.get("node") or {}
            new_id = str(raw.get("id") or "")
            if not new_id or new_id in nodes:
                raise CanvasServiceError(400, "新增节点 id 为空或已存在")
            nodes[new_id] = {key: deepcopy(value) for key, value in raw.items() if key in {"id", "type", "position", "size", "data"}}
            summaries.append(f"新增节点 {new_id}")
        elif op == "update_node":
            if node_id not in nodes:
                raise CanvasServiceError(400, f"节点不存在：{node_id}")
            nodes[node_id]["data"] = {**(nodes[node_id].get("data") or {}), **deepcopy(operation.get("data") or {})}
            summaries.append(f"更新节点 {node_id}")
        elif op == "move_node":
            if node_id not in nodes or not isinstance(operation.get("position"), dict):
                raise CanvasServiceError(400, f"无法移动节点：{node_id}")
            nodes[node_id]["position"] = deepcopy(operation["position"])
            summaries.append(f"移动节点 {node_id}")
        elif op == "delete_node":
            if node_id not in nodes:
                raise CanvasServiceError(400, f"节点不存在：{node_id}")
            nodes.pop(node_id)
            edges = {key: value for key, value in edges.items() if value["source"] != node_id and value["target"] != node_id}
            summaries.append(f"删除节点 {node_id}")
        elif op == "connect":
            raw = operation.get("edge") or {}
            edge_id = str(raw.get("id") or "")
            if not edge_id or edge_id in edges:
                raise CanvasServiceError(400, "新增连线 id 为空或已存在")
            edges[edge_id] = {
                "id": edge_id, "source": str(raw.get("source") or ""),
                "source_handle": str(raw.get("source_handle", raw.get("sourceHandle", ""))),
                "target": str(raw.get("target") or ""),
                "target_handle": str(raw.get("target_handle", raw.get("targetHandle", ""))),
            }
            summaries.append(f"新增连线 {edge_id}")
        elif op == "disconnect":
            edge_id = str(operation.get("edge_id") or "")
            if edge_id not in edges:
                raise CanvasServiceError(400, f"连线不存在：{edge_id}")
            edges.pop(edge_id)
            summaries.append(f"删除连线 {edge_id}")
        else:
            raise CanvasServiceError(400, f"不支持的画布操作：{op}")

    persist_canvas_graph(
        db, canvas,
        {"nodes": list(nodes.values()), "edges": list(edges.values()), "viewport": before["viewport"]},
        expected_revision=base_revision, commit=False,
        control_version=control_version,
    )
    after = canvas_snapshot(canvas)
    record = CanvasOperation(
        canvas_id=canvas.id, user_id=user.id, source="agent",
        idempotency_key=idempotency_key, base_revision=base_revision,
        result_revision=canvas.revision, before_graph=before, after_graph=after,
        summary="；".join(summaries)[:2000],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    db.refresh(canvas)
    return record, True


def undo_agent_operation(
    db: Session, canvas: Canvas, user: User, operation_id: str | None = None,
) -> CanvasOperation:
    query = db.query(CanvasOperation).filter_by(
        canvas_id=canvas.id, user_id=user.id, undone=False,
    )
    if operation_id:
        query = query.filter(CanvasOperation.id == operation_id)
    operation = query.order_by(CanvasOperation.created_at.desc()).first()
    if operation is None:
        raise CanvasServiceError(404, "没有可撤销的智能体画布操作")
    if int(canvas.revision or 0) != operation.result_revision:
        raise CanvasServiceError(409, "画布在该操作后已被修改，不能用旧快照覆盖")
    persist_canvas_graph(
        db, canvas, operation.before_graph,
        expected_revision=operation.result_revision, commit=False,
    )
    operation.undone = True
    db.commit()
    db.refresh(operation)
    db.refresh(canvas)
    return operation
