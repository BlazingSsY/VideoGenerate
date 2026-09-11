import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..canvas_graph import GraphValidationError, validate_graph
from ..canvas_executor import prepare_generation
from ..cleanup import delete_videos_for
from ..database import get_db
from ..media_links import sign_path
from ..models import Canvas, CanvasEdge, CanvasNode, Conversation, Message, User
from ..schemas import (
    CanvasCreate,
    CanvasDetail,
    CanvasGraphPut,
    CanvasOut,
    CanvasPatch,
)
from ..security import current_user
from ..tasks import spawn

router = APIRouter(prefix="/api/canvases", tags=["canvases"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _own(db: Session, canvas_id: str, user: User) -> Canvas:
    canvas = db.get(Canvas, canvas_id)
    if canvas is None or canvas.user_id != user.id:
        raise HTTPException(status_code=404, detail="画布不存在")
    return canvas


def _detail(canvas: Canvas) -> CanvasDetail:
    detail = CanvasDetail.model_validate(canvas)
    # 图片节点存的是 /media/uploads/xxx 相对路径；出站时补一份带签名的地址给前端显示。
    # 存的那份保持不变，提交生成时仍由 media_resolver 决定用公网 URL 还是 Base64。
    for node in detail.nodes:
        url = str((node.data or {}).get("url", ""))
        if url.startswith("/media/"):
            node.data = {**node.data, "signed_url": sign_path(url)}
    return detail


@router.get("", response_model=list[CanvasOut])
def list_canvases(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return (
        db.query(Canvas)
        .filter(Canvas.user_id == user.id)
        .order_by(Canvas.updated_at.desc())
        .all()
    )


@router.post("", response_model=CanvasOut, status_code=201)
def create_canvas(
    payload: CanvasCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas_id = uuid.uuid4().hex
    title = (payload.title or "未命名画布").strip()[:120] or "未命名画布"
    shadow = Conversation(id=canvas_id, user_id=user.id, title=title, kind="canvas")
    canvas = Canvas(id=canvas_id, user_id=user.id, title=title)
    db.add(shadow)
    db.add(canvas)
    db.commit()
    db.refresh(canvas)
    return canvas


@router.get("/{canvas_id}", response_model=CanvasDetail)
def get_canvas(
    canvas_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return _detail(_own(db, canvas_id, user))


@router.patch("/{canvas_id}", response_model=CanvasOut)
def update_canvas(
    canvas_id: str,
    payload: CanvasPatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    if payload.title is not None:
        canvas.title = payload.title.strip()[:120] or "未命名画布"
        shadow = db.get(Conversation, canvas.id)
        if shadow is not None:
            shadow.title = canvas.title
    if payload.viewport is not None:
        canvas.viewport = payload.viewport
    canvas.updated_at = _now()
    db.commit()
    db.refresh(canvas)
    return canvas


@router.delete("/{canvas_id}", status_code=204)
def delete_canvas(
    canvas_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    shadow = db.get(Conversation, canvas.id)
    if shadow is not None:
        delete_videos_for(shadow.messages)
    db.delete(canvas)
    db.flush()
    if shadow is not None:
        db.delete(shadow)
    db.commit()


@router.put("/{canvas_id}/graph", response_model=CanvasDetail)
def save_graph(
    canvas_id: str,
    payload: CanvasGraphPut,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    if payload.updated_at != canvas.updated_at:
        raise HTTPException(
            status_code=409,
            detail="画布已在其他窗口更新，请刷新后继续编辑",
        )

    node_values = [node.model_dump() for node in payload.nodes]
    edge_values = [edge.model_dump() for edge in payload.edges]
    try:
        validate_graph(node_values, edge_values)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing_nodes = {node.id: node for node in canvas.nodes}
    incoming_ids = {node.id for node in payload.nodes}
    for edge in list(canvas.edges):
        db.delete(edge)
    for node in list(canvas.nodes):
        if node.id not in incoming_ids:
            db.delete(node)

    for item in payload.nodes:
        node = existing_nodes.get(item.id)
        if node is None:
            node = CanvasNode(
                id=item.id,
                canvas_id=canvas.id,
                status="idle" if item.type == "generate" else "",
            )
            db.add(node)
        node.type = item.type
        node.position = item.position
        node.size = item.size
        node.data = item.data

    db.flush()
    for item in payload.edges:
        db.add(
            CanvasEdge(
                id=item.id,
                canvas_id=canvas.id,
                source=item.source,
                source_handle=item.source_handle,
                target=item.target,
                target_handle=item.target_handle,
            )
        )

    canvas.viewport = payload.viewport
    canvas.updated_at = _now()
    db.commit()
    db.refresh(canvas)
    return _detail(canvas)


@router.get("/{canvas_id}/status")
def canvas_status(
    canvas_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    result = []
    changed = False
    for node in canvas.nodes:
        message = db.get(Message, node.message_id) if node.message_id else None
        status = message.status if message is not None else node.status
        if status != node.status:
            node.status = status
            changed = True
        if message is None:
            video_src = ""
        elif message.video_expired:
            video_src = ""
        elif message.local_video:
            video_src = sign_path(f"/media/videos/{message.local_video}")
        else:
            video_src = message.video_url
        result.append(
            {
                "node_id": node.id,
                "status": status,
                "message_id": node.message_id,
                "video_src": video_src,
                "video_expired": bool(message.video_expired) if message else False,
                "error": message.error if message else "",
            }
        )
    if changed:            # 轮询接口默认只读，状态真的变了才写库
        db.commit()
    return result


@router.post("/{canvas_id}/nodes/{node_id}/run", status_code=202)
def run_node(
    canvas_id: str,
    node_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    node = next((item for item in canvas.nodes if item.id == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")

    # 同一个节点已有任务在跑就不要再提交——重复提交等于重复扣费
    if node.message_id:
        running = db.get(Message, node.message_id)
        if running is not None and running.status in ("pending", "running"):
            raise HTTPException(
                status_code=409,
                detail="该节点正在生成中，请等待本次完成后再运行",
            )

    message = prepare_generation(db, canvas, node, user, request)
    spawn(message.id)
    return {"node_id": node.id, "status": node.status, "message_id": message.id}
