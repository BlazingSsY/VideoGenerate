"""后台任务：提交视频生成任务、轮询状态、落盘视频。"""
import asyncio
import logging
import time

import httpx

from .catalog import get_model
from .config import settings
from .dashscope import (
    TERMINAL_BAD,
    TERMINAL_OK,
    DashScopeError,
    InsufficientBalanceError,
    build_payload,
    fetch_task,
    submit_task,
)
from .database import SessionLocal
from .media_resolver import MediaError, resolve_inputs
from .models import Conversation, Message, PromptSkill
from .prompt_context import resolve_prompt
from .prompt_skills import enhance_prompt

logger = logging.getLogger(__name__)

_running: set[str] = set()
_tasks: set[asyncio.Task] = set()  # 持有强引用，避免任务被 GC 回收
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """在服务启动时记录主事件循环，供同步接口投递后台任务。"""
    global _loop
    _loop = loop


def _update(message_id: str, **fields) -> None:
    db = SessionLocal()
    try:
        message = db.get(Message, message_id)
        if message is None:
            return
        for key, value in fields.items():
            setattr(message, key, value)
        db.commit()
    finally:
        db.close()


async def _download_video(url: str, message_id: str) -> str:
    """把 DashScope 的临时视频地址下载到本地长期保存。"""
    filename = f"{message_id}.mp4"
    target = settings.video_dir / filename
    partial = target.with_suffix(".mp4.part")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0)) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with partial.open("wb") as fp:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        fp.write(chunk)
        if partial.stat().st_size == 0:
            raise RuntimeError("下载到的视频为空文件")
        partial.replace(target)
        return filename
    except Exception as exc:
        partial.unlink(missing_ok=True)
        logger.warning("视频落盘失败（不影响在线播放）：%s", exc)
        return ""


async def _poll_until_done(provider, task_id: str, message_id: str, started: float) -> None:
    deadline = time.monotonic() + settings.poll_timeout
    while True:
        if time.monotonic() > deadline:
            _update(
                message_id,
                status="failed",
                error=f"等待超时（超过 {settings.poll_timeout // 60} 分钟仍未完成）",
                elapsed_seconds=int(time.monotonic() - started),
            )
            return

        result = await fetch_task(provider.api_key, task_id, base_url=provider.base_url)
        status = result["status"]

        if status == TERMINAL_OK:
            video_url = result["video_url"]
            if not video_url:
                _update(message_id, status="failed", error="任务成功但接口未返回视频地址")
                return
            local_video = ""
            if settings.download_videos:
                local_video = await _download_video(video_url, message_id)
            _update(
                message_id,
                status="succeeded",
                video_url=video_url,
                local_video=local_video,
                elapsed_seconds=int(time.monotonic() - started),
            )
            return

        if status in TERMINAL_BAD:
            _update(
                message_id,
                status="failed",
                error=result["message"] or f"任务状态：{status}",
                elapsed_seconds=int(time.monotonic() - started),
            )
            return

        _update(message_id, status="running")
        await asyncio.sleep(settings.poll_interval)


async def run_generation(assistant_message_id: str, user_message_id: str | None = None) -> None:
    if assistant_message_id in _running:
        return
    _running.add(assistant_message_id)
    started = time.monotonic()
    try:
        db = SessionLocal()
        try:
            assistant = db.get(Message, assistant_message_id)
            if assistant is None:
                return
            existing_task_id = assistant.task_id
            model_id = assistant.model
            params = dict(assistant.params or {})
            reference_images = list(assistant.reference_images or [])
            reference_media = list(assistant.reference_media or [])
            conversation_id = assistant.conversation_id
            user_message = db.get(Message, user_message_id) if user_message_id else None
            raw_prompt = user_message.prompt if user_message else assistant.resolved_prompt
            use_context = bool(params.pop("use_context", True))
        finally:
            db.close()

        model = get_model(model_id)
        if model is None:
            _update(assistant_message_id, status="failed", error=f"未知模型：{model_id}")
            return
        provider_name = params.get("generation_provider")
        provider = (
            settings.generation_provider_named(provider_name)
            if provider_name
            else settings.generation_provider_for(model.key_group)
        )
        if not provider.api_key:
            _update(
                assistant_message_id,
                status="failed",
                error=(
                    "后端未配置 HappyHorse 专用 API Key，请在 .env 中填写 "
                    "DASHSCOPE_API_KEY_HAPPYHORSE 后重启服务"
                    if model.key_group == "happyhorse" and not provider_name
                    else "后端未配置 wan/MiniMax 共用 API Key，请在 .env 中填写 "
                    "DASHSCOPE_API_KEY_WAN 后重启服务"
                ),
            )
            return

        # 断线重连：已经有 task_id 就直接继续轮询
        if existing_task_id:
            await _poll_until_done(provider, existing_task_id, assistant_message_id, started)
            return

        # 1. 结合上下文得到最终提示词
        db = SessionLocal()
        try:
            final_prompt = await resolve_prompt(
                db, conversation_id, raw_prompt, provider.api_key, use_context
            )
            skill_id = params.get("skill_id")
            if skill_id:
                skill = db.get(PromptSkill, skill_id)
                if skill is not None:
                    final_prompt = await enhance_prompt(
                        final_prompt, skill.instructions, provider.api_key
                    )
        finally:
            db.close()

        if user_message_id:
            _update(user_message_id, resolved_prompt=final_prompt)
        _update(assistant_message_id, resolved_prompt=final_prompt, status="pending")

        # 2. 解析输入素材（公网 URL 或图片 Base64）后提交任务
        capability = model.capability(params.get("capability", "t2v"))
        if not reference_media and reference_images:
            reference_media = [
                {"kind": "image", "url": url, "name": ""} for url in reference_images
            ]
        resolved_media = (
            resolve_inputs(reference_media, model, capability)
            if reference_media and capability
            else []
        )
        payload = build_payload(
            model=model_id,
            prompt=final_prompt,
            resolution=params.get("resolution", model.default_resolution),
            duration=int(params.get("duration", model.default_duration)),
            ratio=params.get("ratio") or "",
            watermark=params.get("watermark") if model.supports_watermark else None,
            audio=params.get("audio") if model.supports_audio else None,
            media=resolved_media,
        )
        try:
            task_id = await submit_task(provider.api_key, payload, base_url=provider.base_url)
        except InsufficientBalanceError:
            if model.key_group != "happyhorse" or provider.name != "happyhorse-primary":
                raise
            fallback = settings.generation_provider_for(model.key_group, fallback=True)
            if not fallback.api_key:
                raise DashScopeError(
                    "HappyHorse 专用 Key 余额不足，且未配置 wan/MiniMax 共用 "
                    "DASHSCOPE_API_KEY_WAN，无法切换备用通道"
                )
            logger.warning("HappyHorse 专用 Key 余额不足，改用 wan/MiniMax 共用通道提交任务")
            provider = fallback
            task_id = await submit_task(provider.api_key, payload, base_url=provider.base_url)

        params["generation_provider"] = provider.name
        _update(
            assistant_message_id,
            task_id=task_id,
            status="running",
            params=params,
        )

        # 3. 轮询直到完成
        await _poll_until_done(provider, task_id, assistant_message_id, started)

    except MediaError as exc:
        _update(assistant_message_id, status="failed", error=str(exc))
    except DashScopeError as exc:
        _update(assistant_message_id, status="failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("生成任务异常")
        _update(assistant_message_id, status="failed", error=f"服务异常：{exc}")
    finally:
        _running.discard(assistant_message_id)


def spawn(assistant_message_id: str, user_message_id: str | None = None) -> None:
    """从任意上下文（同步接口在线程池中、或事件循环内）投递生成任务。"""
    coro = run_generation(assistant_message_id, user_message_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return

    if _loop is None:
        coro.close()
        _update(
            assistant_message_id,
            status="failed",
            error="服务尚未就绪，无法启动后台任务，请稍后重试",
        )
        return
    asyncio.run_coroutine_threadsafe(coro, _loop)


async def resume_unfinished() -> None:
    """服务重启后，继续跟进未完成的任务。"""
    db = SessionLocal()
    try:
        pending = (
            db.query(Message)
            .filter(Message.role == "assistant", Message.status.in_(["pending", "running"]))
            .all()
        )
        items = [(m.id, m.task_id) for m in pending]
    finally:
        db.close()

    for message_id, task_id in items:
        if task_id:
            logger.info("恢复轮询任务 %s", task_id)
            spawn(message_id)
        else:
            _update(message_id, status="failed", error="服务重启，任务未提交成功，请重新生成")
