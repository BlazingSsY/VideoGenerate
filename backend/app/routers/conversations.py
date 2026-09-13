from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..catalog import can_use, get_model
from .. import media_resolver
from ..config import settings
from ..database import get_db
from ..models import Conversation, Message, Upload, User
from ..prompt_skills import PromptSkillError, select_for_generation
from ..schemas import (
    ConversationCreate,
    ConversationOut,
    GenerateRequest,
    GenerateResponse,
    MessageOut,
)
from ..cleanup import delete_videos_for
from ..media_links import format_video_src, sign_path
from ..security import current_user
from ..tasks import spawn

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def serialize_message(message: Message) -> MessageOut:
    """统一在这里签发媒体链接，避免各处自己拼 /media/ 路径。"""
    out = MessageOut.model_validate(message)
    if message.video_expired:
        out.video_src = ""          # 文件已被保留期清理，前端会给出说明
    elif message.local_video:
        out.video_src = format_video_src(message.local_video)
    else:
        out.video_src = message.video_url
    out.reference_image_urls = [sign_path(url) for url in (message.reference_images or [])]
    out.reference_media = [
        {
            **item,
            "signed_url": sign_path(str(item.get("url", ""))),
        }
        for item in (message.reference_media or [])
        if isinstance(item, dict)
    ]
    return out


def _own(db: Session, conversation_id: str, user: User) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id or conversation.kind != "chat":
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


@router.get("", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id, Conversation.kind == "chat")
        .order_by(Conversation.updated_at.desc())
        .all()
    )


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    conversation = Conversation(
        user_id=user.id,
        title=(payload.title or "新对话").strip()[:120] or "新对话",
        last_model=payload.model or "",
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.patch("/{conversation_id}", response_model=ConversationOut)
def rename_conversation(
    conversation_id: str,
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    conversation = _own(db, conversation_id, user)
    if payload.title:
        conversation.title = payload.title.strip()[:120]
    db.commit()
    db.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    conversation = _own(db, conversation_id, user)
    delete_videos_for(conversation.messages)
    db.delete(conversation)
    db.commit()


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    _own(db, conversation_id, user)
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
        .all()
    )
    return [serialize_message(row) for row in rows]


def _validate(payload: GenerateRequest, user: User, request: Request, db: Session | None = None) -> list[dict]:
    if not can_use(user.role, payload.model):
        raise HTTPException(status_code=403, detail="当前账号无权使用该模型")
    model = get_model(payload.model)
    assert model is not None

    capability = model.capability(payload.capability)
    if capability is None:
        supported = "、".join(c.label for c in model.capabilities)
        raise HTTPException(
            status_code=400, detail=f"{model.label} 不支持该生成方式，可用：{supported}"
        )

    if payload.resolution not in model.resolutions:
        raise HTTPException(status_code=400, detail=f"{model.label} 不支持分辨率 {payload.resolution}")

    allowed_ratios = model.ratios_for(capability.id)
    if allowed_ratios:
        if payload.ratio not in allowed_ratios:
            raise HTTPException(
                status_code=400, detail=f"{model.label}（{capability.label}）不支持画面比例 {payload.ratio}"
            )
    elif payload.ratio:
        raise HTTPException(
            status_code=400, detail=f"{model.label}（{capability.label}）的画面比例跟随原图，不能指定"
        )

    if payload.duration not in model.durations():
        top = min(model.duration_max, settings.max_duration)
        raise HTTPException(
            status_code=400,
            detail=f"{model.label} 支持的时长为 {model.duration_min}-{top} 秒",
        )

    media = [
        {"kind": item.kind, "url": item.url.strip(), "name": item.name}
        for item in payload.reference_media
        if item.url.strip()
    ]
    if not media:
        media = [
            {"kind": "image", "url": url.strip(), "name": ""}
            for url in payload.reference_images
            if url and url.strip()
        ]

    specs = {item.kind: item for item in capability.input_specs()}
    counts = {kind: 0 for kind in specs}
    for item in media:
        kind = item["kind"]
        if kind not in specs:
            raise HTTPException(
                status_code=400,
                detail=f"{model.label}（{capability.label}）不支持 {kind} 素材",
            )
        counts[kind] += 1

    for kind, spec in specs.items():
        count = counts[kind]
        if count < spec.min_count:
            raise HTTPException(
                status_code=400,
                detail=f"{model.label}（{capability.label}）需要至少 {spec.min_count} 个{spec.label}",
            )
        if count > spec.max_count:
            raise HTTPException(
                status_code=400,
                detail=f"{model.label}（{capability.label}）最多 {spec.max_count} 个{spec.label}",
            )
    if len(media) < capability.minimum_media():
        raise HTTPException(
            status_code=400,
            detail=f"{model.label}（{capability.label}）至少需要 {capability.minimum_media()} 个参考素材",
        )

    for item in media:
        if media_resolver.is_local_upload(item["url"]):
            filename = item["url"].rsplit("/", 1)[-1]
            owner = db.get(Upload, filename) if db is not None else None
            if owner is None or owner.user_id != user.id:
                raise HTTPException(status_code=403, detail="无权使用该上传素材")
        if (
            media_resolver.is_local_upload(item["url"])
            and media_resolver.describe_upload_mode(model, item["kind"]) == "unavailable"
        ):
            label = specs[item["kind"]].label
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{model.label} 的本机{label}需要由阿里云通过公网地址读取。"
                    "请配置 PUBLIC_BASE_URL（域名或公网 IP），"
                    f"或改用「粘贴{label}外链」。"
                ),
            )
    return media


@router.post("/{conversation_id}/generate", response_model=GenerateResponse)
def generate(
    conversation_id: str,
    payload: GenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    conversation = _own(db, conversation_id, user)
    media = _validate(payload, user, request, db)
    images = [item["url"] for item in media if item["kind"] == "image"]

    try:
        skill = select_for_generation(db, payload.skill_id, payload.capability)
    except PromptSkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    model = get_model(payload.model)
    params = {
        "capability": payload.capability,
        "resolution": payload.resolution,
        "ratio": payload.ratio,
        "duration": payload.duration,
        "use_context": payload.use_context,
    }
    if skill is not None:
        params["skill_id"] = skill.id
        params["skill_name"] = skill.name
    if model and model.supports_watermark:
        params["watermark"] = (
            model.watermark_default if payload.watermark is None else payload.watermark
        )
    if model and model.supports_audio:
        params["audio"] = model.audio_default if payload.audio is None else payload.audio

    prompt = payload.prompt.strip()
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        prompt=prompt,
        model=payload.model,
        params=params,
        reference_images=images,
        reference_media=media,
        status="",
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        model=payload.model,
        params=params,
        reference_images=images,
        reference_media=media,
        status="pending",
    )
    db.add(user_message)
    db.flush()
    db.add(assistant_message)

    conversation.last_model = payload.model
    if conversation.title in ("", "新对话"):
        conversation.title = prompt[:24] + ("…" if len(prompt) > 24 else "")
    db.commit()
    db.refresh(conversation)
    db.refresh(user_message)
    db.refresh(assistant_message)

    spawn(assistant_message.id, user_message.id)

    return GenerateResponse(
        conversation=ConversationOut.model_validate(conversation),
        user_message=serialize_message(user_message),
        assistant_message=serialize_message(assistant_message),
    )
