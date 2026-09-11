import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin | user
    display_name: Mapped[str] = mapped_column(String(64), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    canvases: Mapped[list["Canvas"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class PromptSkill(Base):
    """管理员安装的提示词增强规则。"""

    __tablename__ = "prompt_skills"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(240), default="")
    instructions: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    last_model: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(16), default="chat", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    prompt: Mapped[str] = mapped_column(Text, default="")            # 用户原始输入
    resolved_prompt: Mapped[str] = mapped_column(Text, default="")   # 实际提交给模型的提示词
    model: Mapped[str] = mapped_column(String(64), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    reference_images: Mapped[list] = mapped_column(JSON, default=list)
    reference_media: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="")      # pending|running|succeeded|failed
    task_id: Mapped[str] = mapped_column(String(64), default="")
    video_url: Mapped[str] = mapped_column(Text, default="")         # DashScope 临时地址
    local_video: Mapped[str] = mapped_column(String(255), default="")  # 本地留存文件名
    # 视频文件已超过保留期被清理；记录本身保留，界面据此说明而不是显示坏掉的播放器
    video_expired: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    elapsed_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Canvas(Base):
    __tablename__ = "canvases"

    # Canvas and its shadow conversation intentionally share an id.
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="未命名画布")
    viewport: Mapped[dict] = mapped_column(JSON, default=lambda: {"x": 0, "y": 0, "zoom": 1})
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="canvases")
    nodes: Mapped[list["CanvasNode"]] = relationship(
        back_populates="canvas", cascade="all, delete-orphan"
    )
    edges: Mapped[list["CanvasEdge"]] = relationship(
        back_populates="canvas", cascade="all, delete-orphan"
    )


class CanvasNode(Base):
    __tablename__ = "canvas_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(24))
    position: Mapped[dict] = mapped_column(JSON)
    size: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="")
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    canvas: Mapped[Canvas] = relationship(back_populates="nodes")


class CanvasEdge(Base):
    __tablename__ = "canvas_edges"
    __table_args__ = (
        UniqueConstraint("canvas_id", "target", "target_handle", name="uq_canvas_target_handle"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(64))
    source_handle: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(64))
    target_handle: Mapped[str] = mapped_column(String(32))

    canvas: Mapped[Canvas] = relationship(back_populates="edges")


class Upload(Base):
    __tablename__ = "uploads"

    filename: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    surface: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AgentTurn(Base):
    __tablename__ = "agent_turns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)
    user_input: Mapped[str] = mapped_column(Text)
    skill_id: Mapped[str] = mapped_column(String(64), default="")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    est_cost: Mapped[float] = mapped_column(Float, default=0)
    est_seconds: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
