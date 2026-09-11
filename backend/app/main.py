import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from sqlalchemy import inspect, text

from .database import Base, SessionLocal, engine
from .models import User
from .routers import agent, auth, canvas, catalog, conversations, media, skills, uploads, users
from .security import hash_password
from .cleanup import run_periodically
from .tasks import bind_loop, resume_unfinished

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # create_all does not add columns to an existing SQLite database.
    inspector = inspect(engine)
    if "kind" not in {column["name"] for column in inspector.get_columns("conversations")}:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE conversations ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'chat'")
            )
    if "video_expired" not in {column["name"] for column in inspector.get_columns("messages")}:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE messages ADD COLUMN video_expired BOOLEAN NOT NULL DEFAULT 0")
            )
    if "reference_media" not in {column["name"] for column in inspector.get_columns("messages")}:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE messages ADD COLUMN reference_media JSON NOT NULL DEFAULT '[]'")
            )
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin = User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                role="admin",
                display_name="管理员",
            )
            db.add(admin)
            db.commit()
            logger.info("已创建默认管理员账号：%s", settings.admin_username)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bind_loop(asyncio.get_running_loop())
    init_db()
    for group, key in settings.api_keys.items():
        if not key:
            logger.warning("未配置 %s 分组的 API Key（.env）", group)
    if len(settings.secret_key) < 32 or settings.secret_key == "please-change-this-secret":
        logger.warning(
            "SECRET_KEY 过短或仍为默认值，生产环境请改为随机字符串（至少 32 字符）"
        )
    if settings.admin_password == "admin123":
        logger.warning("管理员仍在使用默认密码，请登录后立即修改")
    if not settings.public_base_url:
        logger.warning(
            "未配置 APP_DOMAIN / PUBLIC_BASE_URL：MiniMax 的图片上传不可用，"
            "wan 与 happyhorse 会自动改用 Base64 直传"
        )
    if not settings.trusted_hosts:
        logger.warning("未配置 APP_DOMAIN / TRUSTED_HOSTS，未启用 Host 头校验")
    await resume_unfinished()

    cleanup_task = None
    if settings.video_retention_days > 0:
        logger.info(
            "视频保留 %d 天，每 %d 小时清理一次",
            settings.video_retention_days,
            settings.cleanup_interval_hours,
        )
        cleanup_task = asyncio.create_task(run_periodically())
    else:
        logger.info("VIDEO_RETENTION_DAYS <= 0，已关闭视频自动清理")

    yield

    if cleanup_task is not None:
        cleanup_task.cancel()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# 注意：中间件是"后添加的先执行"，顺序不要随意调整。

@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    # 关掉 MIME 嗅探，避免伪装成图片的文件被当成 HTML 执行
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if settings.force_https:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS 允许来源：%s", settings.allowed_origins)
else:
    # 未配置来源时不装 CORS 中间件 = 只允许同源，比 "*" 安全
    logger.info("未配置 ALLOWED_ORIGINS，API 仅允许同源访问")

if settings.force_https:
    app.add_middleware(HTTPSRedirectMiddleware)

if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    logger.info("允许的 Host：%s", settings.trusted_hosts)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(skills.router)
app.include_router(catalog.router)
app.include_router(agent.router)
app.include_router(uploads.router)
app.include_router(conversations.router)
app.include_router(canvas.router)
app.include_router(media.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}


# /media 下的文件用签名链接访问（见 routers/media.py），不再直接挂 StaticFiles
app.include_router(media.media_router)

if settings.frontend_dist.exists():
    assets_dir = settings.frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_file = settings.frontend_dist / "index.html"

    dist_root = settings.frontend_dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "media/")):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            try:
                # resolve() 之后再校验归属，挡掉 ../ 与 %2e%2e 之类的目录穿越
                candidate = (dist_root / full_path).resolve()
            except (OSError, ValueError):
                candidate = None
            if (
                candidate is not None
                and candidate.is_relative_to(dist_root)
                and candidate.is_file()
            ):
                return FileResponse(candidate)
        return FileResponse(index_file)
else:
    logger.warning("未找到前端构建产物：%s（仅提供 API）", settings.frontend_dist)
