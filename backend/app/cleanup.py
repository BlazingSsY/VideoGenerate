"""视频文件的过期清理。

服务器磁盘有限，生成的视频是最占空间的东西（1080P 15 秒可达上百 MB）。
这里做两件事：

1. **过期清理**：超过保留期的视频删除文件，但**保留数据库记录**——
   对话历史、提示词、参数都还在，只是视频播不了了，界面上会明确说明。
2. **孤儿清理**：删掉没有任何数据库记录指向的视频文件。
   删除对话/画布时只级联删了数据行，文件会留在磁盘上，不扫就永远占着。

清理在服务启动时跑一次（补上停机期间欠的），之后按间隔定期跑。
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from .config import settings
from .database import SessionLocal
from .models import Message

logger = logging.getLogger(__name__)

# 孤儿文件的宽限期：刚下载完还没提交事务的文件不能误删
ORPHAN_GRACE_SECONDS = 3600


def _utc_naive_now() -> datetime:
    """SQLite 的 DateTime 列存的是不带时区的 UTC，比较时要对齐。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _remove(path) -> int:
    """删除文件并返回释放的字节数。"""
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0
    except OSError as exc:
        logger.warning("删除 %s 失败：%s", path.name, exc)
        return 0


def delete_videos_for(messages) -> int:
    """删除对话/画布时同步删掉视频文件。

    孤儿扫描本来也能兜住，但那要等下一轮（默认最长 6 小时），
    磁盘紧张时这段时间的占用没必要。
    """
    freed = 0
    for message in messages:
        if message.local_video:
            freed += _remove(settings.video_dir / message.local_video)
    return freed


def sweep() -> dict:
    """执行一次清理，返回统计结果。"""
    result = {"expired": 0, "orphans": 0, "freed_bytes": 0}
    retention_days = settings.video_retention_days

    db = SessionLocal()
    try:
        # ---- 1. 过期视频 ----
        if retention_days > 0:
            cutoff = _utc_naive_now() - timedelta(days=retention_days)
            stale = (
                db.query(Message)
                .filter(
                    Message.local_video != "",
                    Message.created_at < cutoff,
                )
                .all()
            )
            for message in stale:
                freed = _remove(settings.video_dir / message.local_video)
                result["freed_bytes"] += freed
                result["expired"] += 1
                # 记录保留，只是标记视频已被清理，界面据此给出说明
                message.local_video = ""
                message.video_expired = True
            if stale:
                db.commit()

        # ---- 2. 孤儿文件 ----
        referenced = {
            name
            for (name,) in db.query(Message.local_video).filter(Message.local_video != "").all()
        }
    finally:
        db.close()

    now = time.time()
    for path in settings.video_dir.glob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".part":
            # 下载中的临时文件，只清理明显残留的
            if now - path.stat().st_mtime > ORPHAN_GRACE_SECONDS:
                result["freed_bytes"] += _remove(path)
                result["orphans"] += 1
            continue
        if path.name in referenced:
            continue
        if now - path.stat().st_mtime < ORPHAN_GRACE_SECONDS:
            continue  # 可能是刚落盘、事务还没提交的文件
        result["freed_bytes"] += _remove(path)
        result["orphans"] += 1

    if result["expired"] or result["orphans"]:
        logger.info(
            "视频清理完成：过期 %d 个，孤儿 %d 个，释放 %.1f MB",
            result["expired"],
            result["orphans"],
            result["freed_bytes"] / 1024 / 1024,
        )
    return result


async def run_periodically() -> None:
    """后台常驻任务：启动时先扫一次，之后按间隔重复。"""
    interval = max(settings.cleanup_interval_hours, 1) * 3600
    while True:
        try:
            await asyncio.to_thread(sweep)
        except Exception:  # noqa: BLE001 —— 清理失败不能拖垮服务
            logger.exception("视频清理任务异常")
        await asyncio.sleep(interval)
