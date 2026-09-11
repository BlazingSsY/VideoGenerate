"""Translate a generate node and its incoming edges into the existing task contract."""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .catalog import get_model
from .models import Canvas, CanvasNode, Message, User
from .routers.conversations import _validate
from .schemas import GenerateRequest


def prepare_generation(
    db: Session,
    canvas: Canvas,
    node: CanvasNode,
    user: User,
    request: Request,
) -> Message:
    if node.type != "generate":
        raise HTTPException(status_code=400, detail="只有生成节点可以运行")

    incoming = {edge.target_handle: edge for edge in canvas.edges if edge.target == node.id}
    nodes = {item.id: item for item in canvas.nodes}
    prompt_edge = incoming.get("prompt")
    if prompt_edge:
        prompt_node = nodes.get(prompt_edge.source)
        prompt = str((prompt_node.data if prompt_node else {}).get("text", "")).strip()
    else:
        prompt = str((node.data or {}).get("inlinePrompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="提示词不能为空，请连接提示词节点或填写内联提示词")

    data = node.data or {}
    model = get_model(str(data.get("model", "")))
    capability_id = str(data.get("capability", ""))
    capability = model.capability(capability_id) if model else None
    media_inputs: list[dict[str, str]] = []
    if model and capability:
        for spec in capability.input_specs():
            for index in range(spec.max_count):
                handle = f"{spec.kind}_{index}"
                edge = incoming.get(handle)
                if edge is None:
                    if index < spec.min_count:
                        raise HTTPException(status_code=400, detail=f"{handle} 未连接")
                    continue
                source = nodes.get(edge.source)
                url = str((source.data if source else {}).get("url", "")).strip()
                if not url:
                    raise HTTPException(status_code=400, detail=f"{handle} 对应的{spec.label}为空")
                media_inputs.append({
                    "kind": spec.kind,
                    "url": url,
                    "name": str((source.data if source else {}).get("name", "")),
                })
        if len(media_inputs) < capability.minimum_media():
            raise HTTPException(
                status_code=400,
                detail=f"至少连接 {capability.minimum_media()} 个参考素材",
            )

    try:
        payload = GenerateRequest(
            prompt=prompt,
            model=str(data.get("model", "")),
            capability=capability_id,
            resolution=str(data.get("resolution", "")),
            ratio=str(data.get("ratio", "") or ""),
            duration=int(data.get("duration", 0)),
            watermark=data.get("watermark"),
            audio=data.get("audio"),
            reference_media=media_inputs,
            use_context=False,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="生成节点参数不完整") from exc

    media = _validate(payload, user, request, db)
    images = [item["url"] for item in media if item["kind"] == "image"]
    params = {
        "capability": payload.capability,
        "resolution": payload.resolution,
        "ratio": payload.ratio,
        "duration": payload.duration,
        "use_context": False,
    }
    if model and model.supports_watermark:
        params["watermark"] = (
            model.watermark_default if payload.watermark is None else payload.watermark
        )
    if model and model.supports_audio:
        params["audio"] = model.audio_default if payload.audio is None else payload.audio

    message = Message(
        conversation_id=canvas.id,
        role="assistant",
        resolved_prompt=prompt,
        model=payload.model,
        params=params,
        reference_images=images,
        reference_media=media,
        status="pending",
    )
    db.add(message)
    db.flush()
    node.message_id = message.id
    node.status = "pending"
    db.commit()
    db.refresh(message)
    return message
