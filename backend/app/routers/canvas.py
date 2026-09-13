import uuid
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..canvas_executor import compose_canvas_output, prepare_generation
from ..agent_executor import spawn_run
from ..agent_run_service import run_out
from ..canvas_run_service import start_canvas_run
from ..canvas_service import (
    CanvasServiceError,
    apply_agent_patch,
    canvas_snapshot,
    graph_input_hash,
    lock_canvas,
    persist_canvas_graph,
    undo_agent_operation,
)
from ..cleanup import delete_videos_for
from ..database import get_db
from ..media_links import format_video_src, sign_path
from ..models import AgentRun, Canvas, CanvasNode, Conversation, Message, User
from ..schemas import (
    CanvasCreate,
    CanvasAgentPatch,
    CanvasDetail,
    CanvasGraphPut,
    CanvasOut,
    CanvasPatch,
    CanvasRunRequest,
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
        output_file = str((node.data or {}).get("outputFile", ""))
        if output_file:
            node.data = {**node.data, "outputVideoSrc": format_video_src(output_file)}
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
    try:
        persist_canvas_graph(
            db,
            canvas,
            {
                "viewport": payload.viewport,
                "nodes": [node.model_dump() for node in payload.nodes],
                "edges": [edge.model_dump() for edge in payload.edges],
            },
            expected_updated_at=payload.updated_at,
            expected_revision=payload.revision,
        )
    except CanvasServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _detail(canvas)


@router.get("/{canvas_id}/agent/context")
def agent_canvas_context(
    canvas_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Return the current authoritative graph for Agent inspection."""
    return canvas_snapshot(_own(db, canvas_id, user))


@router.post("/{canvas_id}/agent/patch")
def patch_canvas_as_agent(
    canvas_id: str,
    payload: CanvasAgentPatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    try:
        operation, changed = apply_agent_patch(
            db, canvas, user,
            base_revision=payload.base_revision,
            control_version=payload.control_version,
            idempotency_key=payload.idempotency_key,
            operations=[item.model_dump(exclude_none=True) for item in payload.operations],
        )
    except CanvasServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "operation_id": operation.id,
        "changed": changed,
        "summary": operation.summary,
        "canvas": _detail(canvas),
    }


@router.post("/{canvas_id}/agent/undo")
def undo_canvas_agent_operation(
    canvas_id: str,
    operation_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    try:
        operation = undo_agent_operation(db, canvas, user, operation_id)
    except CanvasServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"operation_id": operation.id, "canvas": _detail(canvas)}


@router.post("/{canvas_id}/takeover")
def take_over_canvas(
    canvas_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Invalidate all Agent writes created under the previous control epoch."""
    canvas = _own(db, canvas_id, user)
    db.execute(update(Canvas).where(Canvas.id == canvas.id).values(
        control_version=Canvas.control_version + 1,
        revision=Canvas.revision + 1, updated_at=_now(),
    ).execution_options(synchronize_session=False))
    db.commit()
    db.refresh(canvas)
    return {
        "canvas_id": canvas.id,
        "revision": canvas.revision,
        "control_version": canvas.control_version,
        "message": "已停止智能体继续修改，画布现在由你接管",
    }


@router.get("/{canvas_id}/status")
def canvas_status(
    canvas_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    result = []
    for node in canvas.nodes:
        message = db.get(Message, node.message_id) if node.message_id else None
        status = message.status if message is not None else node.status
        output_error = str((node.data or {}).get("outputError") or "")
        if output_error and node.status in {"failed", "blocked", "canceled"}:
            status = node.status
        if message is None:
            output_file = str((node.data or {}).get("outputFile", ""))
            video_src = format_video_src(output_file) if output_file else ""
        elif message.video_expired:
            video_src = ""
        elif message.local_video:
            video_src = format_video_src(message.local_video)
        else:
            video_src = message.video_url
        result.append(
            {
                "node_id": node.id,
                "status": status,
                "message_id": node.message_id,
                "video_src": video_src,
                "video_expired": bool(message.video_expired) if message else False,
                "error": output_error or (message.error if message else ""),
            }
        )
    return result


@router.post("/{canvas_id}/run", status_code=202)
def run_canvas(canvas_id: str, payload: CanvasRunRequest, request: Request,
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        run = start_canvas_run(db, _own(db, canvas_id, user), user, request, payload.revision)
    except CanvasServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    spawn_run(run.id)
    return run_out(db, run)


@router.get("/{canvas_id}/runs/latest")
def latest_canvas_run(canvas_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _own(db, canvas_id, user)
    run = db.query(AgentRun).filter(AgentRun.canvas_id == canvas_id).order_by(AgentRun.created_at.desc()).first()
    return run_out(db, run) if run else None


def _lock_for_node_run(db: Session, canvas: Canvas, payload: CanvasRunRequest | None) -> None:
    try:
        lock_canvas(db, canvas, expected_revision=payload.revision if payload else None)
    except CanvasServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    if db.query(AgentRun).filter(AgentRun.canvas_id == canvas.id, AgentRun.status.in_(["queued", "running"])).first():
        raise HTTPException(409, "画布正在运行，请等待本次完成后再单独运行节点")


@router.post("/{canvas_id}/nodes/{node_id}/run", status_code=202)
def run_node(
    canvas_id: str,
    node_id: str,
    request: Request,
    payload: CanvasRunRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    canvas = _own(db, canvas_id, user)
    _lock_for_node_run(db, canvas, payload)
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


@router.post("/{canvas_id}/output/{node_id}/compose")
async def compose_output(
    canvas_id: str,
    node_id: str,
    payload: CanvasRunRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """最终输出合成：按输出节点的 items 顺序 + transitions 转场拼接已生成片段。"""
    canvas = _own(db, canvas_id, user)
    _lock_for_node_run(db, canvas, payload)
    node = next((item for item in canvas.nodes if item.id == node_id), None)
    if node is None or node.type != "output":
        raise HTTPException(status_code=404, detail="输出节点不存在")
    if node.status in {"pending", "running"}:
        raise HTTPException(409, "输出节点正在合成，请等待本次完成")
    frozen_hash = graph_input_hash(canvas_snapshot(canvas), node.id)
    token = uuid.uuid4().hex
    frozen = SimpleNamespace(id=canvas.id,
        nodes=[SimpleNamespace(id=n.id, type=n.type, message_id=n.message_id, data=deepcopy(n.data)) for n in canvas.nodes],
        edges=[SimpleNamespace(source=e.source, target=e.target, target_handle=e.target_handle) for e in canvas.edges])
    frozen_node = next(n for n in frozen.nodes if n.id == node.id)
    node.status = "running"
    node.input_hash = token
    node.message_id = None
    node.data = {k: v for k, v in (node.data or {}).items() if k not in {"outputFile", "outputVideoSrc", "outputError"}}
    db.commit()
    error = None
    try:
        filename = await compose_canvas_output(frozen, frozen_node)
    except HTTPException as exc:
        error = exc
    except Exception as exc:  # noqa: BLE001
        error = HTTPException(status_code=500, detail=f"合成失败：{exc}")
    canvas = db.get(Canvas, canvas_id)
    if canvas:
        lock_canvas(db, canvas)
        node = next((n for n in canvas.nodes if n.id == node_id), None)
        if node and node.input_hash == token and graph_input_hash(canvas_snapshot(canvas), node_id) == frozen_hash:
            node.status = "failed" if error else "succeeded"
            node.data = {**node.data, "outputError": str(error.detail) if error else ""}
            if not error:
                node.data = {**node.data, "outputFile": filename}
        db.commit()
    if error:
        raise error
    return {"video_src": format_video_src(filename), "filename": filename}
