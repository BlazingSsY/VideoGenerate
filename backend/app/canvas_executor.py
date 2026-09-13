"""Translate a generate node and its incoming edges into the existing task contract."""

import asyncio
import shutil
from collections import defaultdict

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .catalog import get_model
from .canvas_graph import media_handles
from .canvas_references import bind_references
from .config import settings
from .models import Canvas, CanvasNode, Message, User
from .routers.conversations import _validate
from .schemas import GenerateRequest


def generation_input(
    db: Session,
    canvas: Canvas,
    node: CanvasNode,
    user: User,
    request: Request,
    *,
    generated_urls: dict[str, str] | None = None,
) -> dict:
    if node.type != "generate":
        raise HTTPException(status_code=400, detail="只有生成节点可以运行")

    incoming: dict[str, list] = defaultdict(list)
    for edge in canvas.edges:
        if edge.target == node.id:
            incoming[edge.target_handle].append(edge)
    nodes = {item.id: item for item in canvas.nodes}
    prompt_edges = incoming.get("prompt")
    prompt_parts = []
    if prompt_edges:
        prompt_node = nodes.get(prompt_edges[0].source)
        prompt_parts.append(str((prompt_node.data if prompt_node else {}).get("text", "")).strip())
    # Legacy canvases keep connected-prompt precedence. Reference settings opt
    # into adding the node's local reference instructions to the connected text.
    if not prompt_edges or ((node.data or {}).get("capability") == "r2v" and (
        (node.data or {}).get("reference_bindings") or "{{" in str((node.data or {}).get("inlinePrompt", ""))
    )):
        prompt_parts.append(str((node.data or {}).get("inlinePrompt", "")).strip())
    prompt = "\n\n".join(part for part in prompt_parts if part)
    if not prompt:
        raise HTTPException(status_code=400, detail="提示词不能为空，请连接提示词节点或填写内联提示词")

    data = node.data or {}
    model = get_model(str(data.get("model", "")))
    capability_id = str(data.get("capability", ""))
    capability = model.capability(capability_id) if model else None
    media_inputs: list[dict[str, str]] = []
    if model and capability:
        handles = media_handles(node)
        for spec in capability.input_specs():
            connected_for_kind = sum(
                len(incoming.get(handle, [])) for handle in handles.get(spec.kind, [])
            )
            if connected_for_kind < spec.min_count:
                raise HTTPException(status_code=400, detail=f"{spec.label} 至少需要 {spec.min_count} 个")
            for handle in handles.get(spec.kind, []):
                attached = incoming.get(handle, [])
                if len(attached) > spec.max_count:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{spec.label}最多 {spec.max_count} 个",
                    )
                # 聚合槽（r2v）：多条边共连一个 handle，全部收进来
                for edge in attached:
                    source = nodes.get(edge.source)
                    url = str((source.data if source else {}).get("url", "")).strip()
                    if source is not None and source.type == "generate":
                        if generated_urls is not None:
                            url = generated_urls.get(source.id, "")
                        else:
                            upstream = db.get(Message, source.message_id) if source.message_id else None
                            url = upstream.video_url if upstream and upstream.status == "succeeded" else ""
                    if not url:
                        raise HTTPException(status_code=400, detail=f"{handle} 对应的{spec.label}为空")
                    media_inputs.append({
                        "_edge_id": edge.id,
                        "kind": spec.kind,
                        "url": url,
                        "name": str((source.data if source else {}).get("name") or (source.data if source else {}).get("label") or ""),
                    })
        if len(media_inputs) < capability.minimum_media():
            raise HTTPException(
                status_code=400,
                detail=f"至少连接 {capability.minimum_media()} 个参考素材",
            )

    if capability_id == "r2v":
        prompt, media_inputs = bind_references(str(data.get("model", "")), prompt, media_inputs, data.get("reference_bindings") or [])

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

    return {"id": node.id, "type": "generate", "prompt": prompt,
            "model": payload.model, **params, "reference_media": media}


def prepare_generation(db: Session, canvas: Canvas, node: CanvasNode, user: User, request: Request) -> Message:
    frozen = generation_input(db, canvas, node, user, request)
    media = frozen["reference_media"]
    params = {key: value for key, value in frozen.items()
              if key not in {"id", "type", "prompt", "model", "reference_media"}}

    message = Message(
        conversation_id=canvas.id,
        role="assistant",
        resolved_prompt=frozen["prompt"],
        model=frozen["model"],
        params=params,
        reference_images=[item["url"] for item in media if item["kind"] == "image"],
        reference_media=media,
        status="pending",
    )
    db.add(message)
    db.flush()
    node.message_id = message.id
    node.status = "pending"
    node.data = {key: value for key, value in (node.data or {}).items()
                 if key not in {"outputFile", "outputVideoSrc", "outputError"}}
    db.commit()
    db.refresh(message)
    return message


# ---------------------------------------------------------------------------
# 最终输出合成：output 节点按 items 顺序 + transitions 转场拼接已生成的片段。
# 复用 AgentRun/AgentTask 之外的最小路径 —— 只产出文件名并即时返回，
# 不引入任务队列（合成通常秒级完成；失败时直接 4xx 给前端）。
# ---------------------------------------------------------------------------

async def _run_ffmpeg(*args: str) -> tuple[int, bytes]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("服务器未安装 FFmpeg，无法执行视频拼接")
    process = await asyncio.create_subprocess_exec(
        ffmpeg, *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    return process.returncode or 0, stderr


_XFADE_ARGS: dict[str, tuple[float, str]] = {
    # transition type -> (duration(s), xfade effect name)
    "fade": (0.5, "fade"),
    "slide": (0.4, "slideleft"),
    "zoom": (0.5, "zoomin"),
}


def _clip_of(node: CanvasNode) -> str:
    """生成节点对应已落盘的本地视频文件名；未产出时抛 406。"""
    row = None
    if node.message_id:
        from .database import SessionLocal as _SL
        session = _SL()
        try:
            row = session.get(Message, node.message_id)
        finally:
            session.close()
    if row is None or row.status != "succeeded" or not row.local_video:
        named = str((node.data or {}).get("name") or "") or node.id[:6]
        detail = "尚未生成" if (row is None or row.status != "succeeded") else "该片段没有本地视频（仅在线地址）"
        raise HTTPException(406, f"片段「{named}」{detail}")
    return row.local_video


async def compose_canvas_output(
    canvas: Canvas,
    output_node: CanvasNode,
) -> str:
    """按 output.data.items 顺序与 transitions 转场合成最终视频，返回文件名。"""
    data = output_node.data or {}
    items = output_items(canvas, output_node)
    transitions = {t.get("after"): t.get("type") for t in (data.get("transitions") or [])}
    nodes_by_id = {n.id: n for n in canvas.nodes}
    clips: list[tuple[str, str]] = []   # (filename, 该片段出转场类型)
    for it in items:
        key = str(it.get("nodeKey") or "")
        node = nodes_by_id.get(key)
        if node is None or node.type != "generate":
            continue
        clips.append((_clip_of(node), str(transitions.get(key) or "none")))
    return await compose_video_clips(clips, canvas.id)


def output_items(canvas: Canvas, node: CanvasNode) -> list[dict]:
    """Saved order first, then new connections; removed/disconnected clips stay out."""
    by_id = {item.id: item for item in canvas.nodes}
    connected = list(dict.fromkeys(edge.source for edge in canvas.edges
        if edge.target == node.id and edge.target_handle == "input"
        and by_id.get(edge.source) is not None and by_id[edge.source].type == "generate"))
    data = node.data or {}
    excluded = set(data.get("excluded") or [])
    ordered = list(dict.fromkeys(item["nodeKey"] for item in data.get("items") or []
        if item.get("nodeKey") in connected and item["nodeKey"] not in excluded))
    ordered.extend(key for key in connected if key not in ordered and key not in excluded)
    return [{"nodeKey": key} for key in ordered]


async def compose_video_clips(clips: list[tuple[str, str]], canvas_id: str) -> str:
    """Compose frozen local clip filenames, shared by manual and queued runs."""
    if not clips:
        raise HTTPException(406, "没有任何可合成的已生成片段")
    # 最后一段没有"下一段"，出转场无意义（前端也不渲染该控件）；
    # 归一化掉，否则重排残留会让 all-none 判定误入 xfade 分支。
    clips[-1] = (clips[-1][0], "none")

    import uuid as _uuid
    stem = _uuid.uuid4().hex[:12]
    # 无转场（全部 none / 单片段）：concat demuxer 一步到位
    if len(clips) == 1 or all(t == "none" for _, t in clips):
        return await _concat_plain(clips, stem, f"canvas-{canvas_id[:8]}-{stem}.mp4")

    # 分组拼接：出转场为 none 的相邻段先用 concat 滤镜并成一组，组间再 xfade——
    # 否则中间的"直接切换"会被 xfade 默认值静默替换成 fade。
    groups: list[list[int]] = [[0]]
    for i in range(1, len(clips)):
        if clips[i - 1][1] == "none":
            groups[-1].append(i)
        else:
            groups.append([i])

    inputs: list[str] = []
    for f, _t in clips:
        inputs += ["-i", str(settings.video_dir / f)]
    labels = [f"v{i}" for i in range(len(clips))]
    chain = [
        f"[{i}:v]scale=1280:720:force_original_aspect_ratio=decrease,"
        f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1[{labels[i]}]"
        for i in range(len(clips))
    ]
    group_labels: list[str] = []   # 形如 "[v0]" / "[g0]"，统一带括号，避免裸名拼接出错
    for gi, idxs in enumerate(groups):
        if len(idxs) == 1:
            group_labels.append(f"[{labels[idxs[0]]}]")
        else:
            group_labels.append(f"[g{gi}]")
            chain.append(
                "".join(f"[{labels[j]}]" for j in idxs)
                + f"concat=n={len(idxs)}:v=1:a=0[g{gi}]"
            )
    ffprobe = shutil.which("ffprobe")

    async def duration_of(i: int) -> float:
        if not ffprobe:
            return 5.0
        proc = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(settings.video_dir / clips[i][0]),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        try:
            return float(out.decode().strip())
        except ValueError:
            return 5.0

    async def group_duration(gi: int) -> float:
        total = 0.0
        for j in groups[gi]:
            total += await duration_of(j)
        return total

    cur = group_labels[0]
    offset = 0.0
    for gi in range(1, len(group_labels)):
        t = clips[groups[gi][0] - 1][1]
        dur, effect = _XFADE_ARGS.get(t, (0.5, "fade"))
        prev_dur = await group_duration(gi - 1)
        offset += max(0.1, prev_dur - dur)
        chain.append(f"{cur}{group_labels[gi]}xfade=transition={effect}:duration={dur}:offset={offset:.3f}[x{gi}]")
        cur = f"[x{gi}]"
    graph = ";".join(chain) + f";{cur}format=yuv420p[vout]"
    target = settings.video_dir / f"canvas-{canvas_id[:8]}-{stem}.mp4"
    code, stderr = await _run_ffmpeg(
        "-y", *inputs,
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(target),
    )
    if code == 0 and target.is_file() and target.stat().st_size > 0:
        return target.name
    # 转场链失败（分辨率/流不齐等）→ 降级为无转场 concat，保证出片
    target.unlink(missing_ok=True)
    return await _concat_plain(clips, stem, f"canvas-fb-{stem}.mp4")


async def _concat_plain(clips: list[tuple[str, str]], stem: str, filename: str) -> str:
    """concat demuxer 拼接；copy 失败退回重编码。"""
    target = settings.video_dir / filename
    list_file = settings.video_dir / f".{stem}.concat.txt"
    list_file.write_text(
        "".join(f"file '{(settings.video_dir / f).as_posix()}'\n" for f, _ in clips),
        encoding="utf-8",
    )
    try:
        code, stderr = await _run_ffmpeg(
            "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", str(target),
        )
        if code != 0 or not target.is_file():
            code, stderr = await _run_ffmpeg(
                "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(target),
            )
        if code != 0 or not target.is_file() or target.stat().st_size == 0:
            detail = stderr.decode("utf-8", errors="replace")[-600:]
            raise RuntimeError(f"FFmpeg 拼接失败：{detail}")
        return target.name
    finally:
        list_file.unlink(missing_ok=True)
