"""登录失败限流：同一账号 / 同一来源 IP 连续失败到阈值后短暂锁定。

单进程内存实现，够用于本项目的单容器部署。如果以后横向扩多副本，
把 _FAILURES 换成 Redis 即可，接口不用改。
"""
import threading
import time

from fastapi import Request

from .config import settings

_LOCK = threading.Lock()
_FAILURES: dict[str, list[float]] = {}


def client_ip(request: Request) -> str:
    """取真实来源 IP；只有在 BEHIND_PROXY 时才信任 X-Forwarded-For。"""
    if settings.behind_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(now: float, stamps: list[float]) -> list[float]:
    window = settings.login_lockout_seconds
    return [item for item in stamps if now - item < window]


def retry_after(keys: list[str]) -> int:
    """还需等待多少秒才能再试；0 表示可以尝试。"""
    if settings.login_max_attempts <= 0:
        return 0
    now = time.time()
    wait = 0
    with _LOCK:
        for key in keys:
            stamps = _prune(now, _FAILURES.get(key, []))
            _FAILURES[key] = stamps
            if len(stamps) >= settings.login_max_attempts:
                remaining = int(settings.login_lockout_seconds - (now - stamps[0])) + 1
                wait = max(wait, remaining)
    return wait


def record_failure(keys: list[str]) -> None:
    now = time.time()
    with _LOCK:
        for key in keys:
            _FAILURES[key] = _prune(now, _FAILURES.get(key, [])) + [now]


def clear(keys: list[str]) -> None:
    with _LOCK:
        for key in keys:
            _FAILURES.pop(key, None)
