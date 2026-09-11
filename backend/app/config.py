"""全局配置：全部来自项目根目录的 .env 文件。"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    raw = os.getenv(name) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class GenerationProvider:
    """一套可提交并轮询视频任务的 API 凭据与地址。"""

    name: str
    api_key: str
    base_url: str


class Settings:
    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "视频生成工作台")
        self.secret_key = os.getenv("SECRET_KEY", "please-change-this-secret")
        self.token_expire_minutes = _int("TOKEN_EXPIRE_MINUTES", 60 * 24 * 7)

        self.data_dir = Path(os.getenv("DATA_DIR") or (BASE_DIR / "data"))
        self.upload_dir = self.data_dir / "uploads"
        self.video_dir = self.data_dir / "videos"
        for d in (self.data_dir, self.upload_dir, self.video_dir):
            d.mkdir(parents=True, exist_ok=True)

        default_db = f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"
        self.database_url = os.getenv("DATABASE_URL") or default_db

        # wan3.0 / MiniMax 共用一组 Key；HappyHorse 优先使用独立 Token Plan Key。
        self.api_keys = {
            "wan": (os.getenv("DASHSCOPE_API_KEY_WAN") or "").strip(),
            "happyhorse": (os.getenv("DASHSCOPE_API_KEY_HAPPYHORSE") or "").strip(),
        }

        self.dashscope_base_url = (
            os.getenv("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com"
        ).rstrip("/")
        self.happyhorse_base_url = (
            os.getenv("HAPPYHORSE_BASE_URL")
            or "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")

        self.admin_username = os.getenv("ADMIN_USERNAME", "admin")
        self.admin_password = os.getenv("ADMIN_PASSWORD", "admin123")

        # ---------- 域名与反向代理 ----------
        # 只填 APP_DOMAIN，其余三项都能自动推导出来
        self.app_domain = (os.getenv("APP_DOMAIN") or "").strip().lower()
        self.use_https = _bool("USE_HTTPS", True)

        # 上传的参考图需要被 DashScope 公网访问；留空则由 APP_DOMAIN 推导
        public_base_url = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
        if not public_base_url and self.app_domain:
            scheme = "https" if self.use_https else "http"
            public_base_url = f"{scheme}://{self.app_domain}"
        self.public_base_url = public_base_url

        # 允许跨域调用 API 的来源；留空 = 只允许同源（推荐）
        self.allowed_origins = _list("ALLOWED_ORIGINS")
        if not self.allowed_origins and self.public_base_url:
            self.allowed_origins = [self.public_base_url]

        # 允许的 Host 头，防 Host 头注入；留空则不限制
        self.trusted_hosts = _list("TRUSTED_HOSTS")
        if not self.trusted_hosts and self.app_domain:
            self.trusted_hosts = [self.app_domain, f"www.{self.app_domain}", "localhost", "127.0.0.1"]
        # PUBLIC_BASE_URL 指向的主机必须放行——阿里云就是用这个地址来回源取参考图的，
        # 如果它和 APP_DOMAIN 不一致（例如域名走 Caddy、回源用公网 IP），
        # 不加进来就会被 Host 校验拦成 400，而且失败得毫无提示。
        if self.trusted_hosts and self.public_base_url:
            fetch_host = urlparse(self.public_base_url).hostname
            if fetch_host and fetch_host not in self.trusted_hosts:
                self.trusted_hosts.append(fetch_host)

        self.behind_proxy = _bool("BEHIND_PROXY", False)
        self.force_https = _bool("FORCE_HTTPS", False)

        # ---------- 登录保护 ----------
        self.login_max_attempts = _int("LOGIN_MAX_ATTEMPTS", 8)
        self.login_lockout_seconds = _int("LOGIN_LOCKOUT_SECONDS", 300)

        self.max_duration = _int("MAX_VIDEO_DURATION", 15)
        self.poll_interval = _int("POLL_INTERVAL_SECONDS", 8)
        self.poll_timeout = _int("POLL_TIMEOUT_SECONDS", 1800)
        self.download_videos = _bool("DOWNLOAD_VIDEOS", True)
        # 生成的视频保留天数，超期自动删除文件（数据库记录保留）。0 或负数 = 不清理
        self.video_retention_days = _int("VIDEO_RETENTION_DAYS", 7)
        # 清理任务的运行间隔（小时），服务启动时也会先跑一次
        self.cleanup_interval_hours = _int("CLEANUP_INTERVAL_HOURS", 6)
        self.max_upload_mb = _int("MAX_UPLOAD_MB", 20)
        # 媒体签名链接有效期（秒）。要覆盖"打开页面后隔一阵才点播放"的场景，
        # 也要够阿里云回源下载参考图，默认 6 小时
        self.media_link_ttl = _int("MEDIA_LINK_TTL_SECONDS", 6 * 3600)

        # 多轮对话记忆：用文本大模型把历史提示词与本轮修改要求合并成完整提示词
        self.context_enabled = _bool("CONTEXT_ENABLED", True)
        self.context_model = os.getenv("CONTEXT_MODEL", "qwen-plus")
        self.context_api_key = (os.getenv("CONTEXT_API_KEY") or "").strip()
        self.context_max_turns = _int("CONTEXT_MAX_TURNS", 6)

        # 创作智能体：计划只辅助编排，执行始终回到既有生成接口。
        self.agent_enabled = _bool("AGENT_ENABLED", True)
        self.agent_autonomy = os.getenv("AGENT_AUTONOMY", "confirm").strip().lower()
        self.agent_max_auto_cost = _float("AGENT_MAX_AUTO_COST", 20)
        self.agent_max_auto_nodes = _int("AGENT_MAX_AUTO_NODES", 3)
        self.agent_daily_cost_limit = _float("AGENT_DAILY_COST_LIMIT", 200)
        self.agent_price_default = _float("AGENT_PRICE_DEFAULT", 0.8)
        self.agent_price_table = os.getenv("AGENT_PRICE_TABLE", "")
        self.agent_currency = os.getenv("AGENT_CURRENCY", "CNY")
        self.agent_rate_per_minute = _int("AGENT_RATE_PER_MINUTE", 6)
        self.agent_plan_ttl_minutes = _int("AGENT_PLAN_TTL_MINUTES", 60)

        frontend = os.getenv("FRONTEND_DIST")
        self.frontend_dist = Path(frontend) if frontend else BASE_DIR / "frontend" / "dist"

    def api_key_for(self, key_group: str) -> str:
        return self.api_keys.get(key_group, "")

    def generation_provider_for(self, key_group: str, *, fallback: bool = False) -> GenerationProvider:
        """返回模型当前应使用的任务通道。

        HappyHorse 的 Token Plan 通道余额不足时，才允许回退到 wan/MiniMax 共用通道。
        """
        if key_group == "happyhorse" and not fallback:
            return GenerationProvider(
                name="happyhorse-primary",
                api_key=self.api_keys["happyhorse"],
                base_url=self.happyhorse_base_url,
            )
        return GenerationProvider(
            name="wan-shared",
            api_key=self.api_keys["wan"],
            base_url=self.dashscope_base_url,
        )

    def generation_provider_named(self, name: str) -> GenerationProvider:
        if name == "happyhorse-primary":
            return self.generation_provider_for("happyhorse")
        return self.generation_provider_for("wan")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
