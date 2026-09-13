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
from .migrations import migrate_agent_constraints
from .models import (
    AgentCanvasImport,
    AgentChatMessage,
    AgentRun,
    AgentRunEvent,
    AgentStep,
    AgentTask,
    Asset,
    CanvasOperation,
    User,
)
from .routers import agent, assets, auth, canvas, catalog, conversations, media, skills, uploads, users
from .security import hash_password
from .cleanup import run_periodically
from .tasks import bind_loop, resume_unfinished
from .agent_executor import bind_loop as bind_agent_loop, resume_runs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _seed_default_skills() -> None:
    """Create the initial v3 skill catalog if none exists."""
    from .models import PromptSkill
    db = SessionLocal()
    try:
        if db.query(PromptSkill).count() > 0:
            return
        skills = [
            PromptSkill(name="一句话成片", description="最快路径，一段提示词直接生成",
                instructions="把用户输入扩写为一条完整的视频提示词，包含画面、运镜、风格、光影细节。",
                enabled=True, plan_shape="single", max_nodes=1),
            PromptSkill(name="分镜短片", description="把脚本拆成多个镜头分别生成再拼接",
                instructions="将用户描述的场景拆成2-6个镜头，每个镜头独立生成，最后顺序拼接。每个镜头的提示词要包含完整的画面描述。",
                enabled=True, plan_shape="storyboard", max_nodes=6,
                requires={"capability":"t2v"}),
            PromptSkill(name="让图动起来", description="上传一张图作为首帧，让画面动起来",
                instructions="以用户上传的图片为首帧，描述如何让画面动起来：镜头运动、主体动作、环境变化。",
                enabled=True, plan_shape="single", max_nodes=1,
                requires={"capability":"i2v"}),
            PromptSkill(name="角色一致性组镜", description="用同一批参考图跑多个镜头，保证人物不走样",
                instructions="使用参考生视频(r2v)，用同一批角色参考图生成多个镜头。每个镜头提示词中用[Image 1]引用参考图。",
                enabled=True, plan_shape="fanout", max_nodes=6,
                requires={"capability":"r2v"}),
            PromptSkill(name="商品多角度", description="电商多角度展示商品",
                instructions="用参考生视频从多个角度展示商品：正面、侧面、细节、使用场景。每段5-10秒。",
                enabled=True, plan_shape="fanout", max_nodes=6,
                requires={"capability":"r2v"}),
            PromptSkill(name="模型横评", description="同一提示词用不同模型并排比较效果",
                instructions="用同一提示词和参数，分别用不同模型生成，方便对比效果差异。",
                enabled=True, plan_shape="compare", max_nodes=3),
            PromptSkill(name="迭代改进", description="基于已有结果修改优化",
                instructions="根据用户的修改要求，在上一版计划基础上调整。输出完整的新计划，不做增量patch。",
                enabled=True, plan_shape="refine", max_nodes=1),
        ]
        for skill in skills:
            db.add(skill)
        db.commit()
        logger.info("已创建 %d 个默认技能", len(skills))
    except Exception as exc:
        db.rollback()
        logger.warning("创建默认技能失败: %s", exc)
    finally:
        db.close()


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
    canvas_columns = {column["name"] for column in inspector.get_columns("canvases")}
    canvas_migrations = {
        "revision": "ALTER TABLE canvases ADD COLUMN revision INTEGER NOT NULL DEFAULT 0",
        "control_version": "ALTER TABLE canvases ADD COLUMN control_version INTEGER NOT NULL DEFAULT 0",
    }
    for name, statement in canvas_migrations.items():
        if name not in canvas_columns:
            with engine.begin() as connection:
                connection.execute(text(statement))
    agent_columns = {column["name"] for column in inspector.get_columns("agent_turns")}
    migrations = {
        "agent_model_id": "ALTER TABLE agent_turns ADD COLUMN agent_model_id VARCHAR(128) NOT NULL DEFAULT ''",
        "tokens_in": "ALTER TABLE agent_turns ADD COLUMN tokens_in INTEGER NOT NULL DEFAULT 0",
        "tokens_out": "ALTER TABLE agent_turns ADD COLUMN tokens_out INTEGER NOT NULL DEFAULT 0",
        "repair_count": "ALTER TABLE agent_turns ADD COLUMN repair_count INTEGER NOT NULL DEFAULT 0",
        "warning": "ALTER TABLE agent_turns ADD COLUMN warning TEXT NOT NULL DEFAULT ''",
        "plan_version": "ALTER TABLE agent_turns ADD COLUMN plan_version VARCHAR(64) NOT NULL DEFAULT ''",
        "canvas_control_version": "ALTER TABLE agent_turns ADD COLUMN canvas_control_version INTEGER NOT NULL DEFAULT 0",
    }
    for name, statement in migrations.items():
        if name not in agent_columns:
            with engine.begin() as connection:
                connection.execute(text(statement))
    # Agent execution tables were added after the initial planning-only release.
    # create_all handles fresh installations; existing SQLite databases get them here.
    Base.metadata.create_all(bind=engine, tables=[AgentRun.__table__, AgentTask.__table__])
    run_columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    run_migrations = {
        "canvas_id": "ALTER TABLE agent_runs ADD COLUMN canvas_id VARCHAR(32)",
        "canvas_revision": "ALTER TABLE agent_runs ADD COLUMN canvas_revision INTEGER NOT NULL DEFAULT 0",
        "plan_version": "ALTER TABLE agent_runs ADD COLUMN plan_version VARCHAR(64) NOT NULL DEFAULT ''",
        "input_snapshot": "ALTER TABLE agent_runs ADD COLUMN input_snapshot JSON NOT NULL DEFAULT '{}'",
        "cancel_requested": "ALTER TABLE agent_runs ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0",
    }
    for name, statement in run_migrations.items():
        if name not in run_columns:
            with engine.begin() as connection:
                connection.execute(text(statement))
    task_columns = {column["name"] for column in inspector.get_columns("agent_tasks")}
    task_migrations = {
        "canvas_node_id": "ALTER TABLE agent_tasks ADD COLUMN canvas_node_id VARCHAR(64) NOT NULL DEFAULT ''",
        "input_snapshot": "ALTER TABLE agent_tasks ADD COLUMN input_snapshot JSON NOT NULL DEFAULT '{}'",
        "attempt_count": "ALTER TABLE agent_tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
    }
    for name, statement in task_migrations.items():
        if name not in task_columns:
            with engine.begin() as connection:
                connection.execute(text(statement))
    migrate_agent_constraints(engine)
    # v3: 对话式智能体新表
    Base.metadata.create_all(bind=engine, tables=[AgentChatMessage.__table__, AgentStep.__table__, Asset.__table__])
    Base.metadata.create_all(bind=engine, tables=[
        AgentRunEvent.__table__, AgentCanvasImport.__table__, CanvasOperation.__table__,
    ])

    # v3: PromptSkill 扩列
    skill_columns = {column["name"] for column in inspector.get_columns("prompt_skills")}
    skill_migrations = {
        "requires": "ALTER TABLE prompt_skills ADD COLUMN requires JSON DEFAULT '{}'",
        "inputs": "ALTER TABLE prompt_skills ADD COLUMN inputs JSON DEFAULT '[]'",
        "plan_shape": "ALTER TABLE prompt_skills ADD COLUMN plan_shape VARCHAR(16) NOT NULL DEFAULT 'single'",
        "max_nodes": "ALTER TABLE prompt_skills ADD COLUMN max_nodes INTEGER NOT NULL DEFAULT 1",
    }
    for name, statement in skill_migrations.items():
        if name not in skill_columns:
            with engine.begin() as connection:
                connection.execute(text(statement))

    # v3: AgentTurn 扩列
    turn_columns = {column["name"] for column in inspector.get_columns("agent_turns")}
    turn_migrations = {
        "intent": "ALTER TABLE agent_turns ADD COLUMN intent VARCHAR(16) NOT NULL DEFAULT ''",
        "tool_call_count": "ALTER TABLE agent_turns ADD COLUMN tool_call_count INTEGER NOT NULL DEFAULT 0",
        "reasoning_tokens": "ALTER TABLE agent_turns ADD COLUMN reasoning_tokens INTEGER NOT NULL DEFAULT 0",
    }
    for name, statement in turn_migrations.items():
        if name not in turn_columns:
            with engine.begin() as connection:
                connection.execute(text(statement))

    # 拼接计划（需求⑨）：output.input 接受多条 generate 边 — 重建 canvas_edges
    # 去掉 UNIQUE(canvas_id, target, target_handle)。SQLite 无法 DROP 约束，
    # 建临时表搬家；create_all 对已有表是 no-op，不会自动改。
    edge_constraints = {
        tc["name"] for tc in inspector.get_unique_constraints("canvas_edges")
    } if "canvas_edges" in inspector.get_table_names() else set()
    if "uq_canvas_target_handle" in edge_constraints:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE canvas_edges_migrate AS SELECT id, canvas_id, source, source_handle, target, target_handle FROM canvas_edges"
            ))
            connection.execute(text("DROP TABLE canvas_edges"))
            connection.execute(text(
                "CREATE TABLE canvas_edges (\n"
                "  id VARCHAR(64) NOT NULL PRIMARY KEY,\n"
                "  canvas_id VARCHAR(64),\n"
                "  source VARCHAR(64) NOT NULL,\n"
                "  source_handle VARCHAR(32) NOT NULL,\n"
                "  target VARCHAR(64) NOT NULL,\n"
                "  target_handle VARCHAR(32) NOT NULL,\n"
                "  FOREIGN KEY(canvas_id) REFERENCES canvases (id) ON DELETE CASCADE\n"
                ")"
            ))
            connection.execute(text(
                "INSERT INTO canvas_edges (id, canvas_id, source, source_handle, target, target_handle) "
                "SELECT id, canvas_id, source, source_handle, target, target_handle FROM canvas_edges_migrate"
            ))
            connection.execute(text("DROP TABLE canvas_edges_migrate"))
            connection.execute(text("CREATE INDEX ix_canvas_edges_canvas_id ON canvas_edges (canvas_id)"))

    # v3: Seed default skills
    _seed_default_skills()

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
    bind_agent_loop(asyncio.get_running_loop())
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
    await resume_runs()

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
app.include_router(assets.router)
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
