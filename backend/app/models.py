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
    # v3: 技能 schema 字段
    requires: Mapped[dict] = mapped_column(JSON, default=dict)
    inputs: Mapped[list] = mapped_column(JSON, default=list)
    plan_shape: Mapped[str] = mapped_column(String(16), default="single")
    max_nodes: Mapped[int] = mapped_column(Integer, default=1)


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
    # 单调递增的图版本用于 Agent 写入、运行输入冻结和撤销冲突检测。
    # updated_at 仍保留给旧客户端做乐观锁，revision 是新的权威版本。
    revision: Mapped[int] = mapped_column(Integer, default=0)
    # 用户点击“人工接手”时递增。在途 Agent patch 必须携带创建时版本，
    # 旧控制版本的写操作会被拒绝，避免覆盖后续人工编辑。
    control_version: Mapped[int] = mapped_column(Integer, default=0)
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
    agent_model_id: Mapped[str] = mapped_column(String(128), default="")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    est_cost: Mapped[float] = mapped_column(Float, default=0)
    est_seconds: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    warning: Mapped[str] = mapped_column(Text, default="")
    # v3: 意图分类与工具调用记账
    intent: Mapped[str] = mapped_column(String(16), default="")
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # 计划内容的稳定指纹；接受、导入和运行都以它作为幂等/过期依据。
    plan_version: Mapped[str] = mapped_column(String(64), default="")
    # Canvas control epoch frozen when the turn starts. Human takeover bumps the
    # canvas epoch, invalidating every write or execution from an older turn.
    canvas_control_version: Mapped[int] = mapped_column(Integer, default=0)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("turn_id", name="uq_agent_run_turn"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    turn_id: Mapped[str] = mapped_column(ForeignKey("agent_turns.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    canvas_id: Mapped[str | None] = mapped_column(
        ForeignKey("canvases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    canvas_revision: Mapped[int] = mapped_column(Integer, default=0)
    plan_version: Mapped[str] = mapped_column(String(64), default="")
    # 完整的不可变执行输入。画布之后被修改也不影响已经启动的运行。
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    output_file: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_agent_task_node"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    task_type: Mapped[str] = mapped_column(String(16))  # generate | compose
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    canvas_node_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    output_file: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AgentRunEvent(Base):
    """可重放的运行事件；运行进度不再依赖规划阶段的内存队列。"""

    __tablename__ = "agent_run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_agent_run_event_seq"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AgentCanvasImport(Base):
    """一个计划在一个画布上的稳定节点映射，保证重复导入不会制造副本。"""

    __tablename__ = "agent_canvas_imports"
    __table_args__ = (
        UniqueConstraint("turn_id", "canvas_id", name="uq_agent_canvas_import"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="CASCADE"), index=True
    )
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    plan_version: Mapped[str] = mapped_column(String(64))
    node_map: Mapped[dict] = mapped_column(JSON, default=dict)
    canvas_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CanvasOperation(Base):
    """Agent 原子画布操作的前后快照，用于一次性撤销与审计。"""

    __tablename__ = "canvas_operations"
    __table_args__ = (
        UniqueConstraint("canvas_id", "idempotency_key", name="uq_canvas_operation_key"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    canvas_id: Mapped[str] = mapped_column(
        ForeignKey("canvases.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(16), default="agent")
    idempotency_key: Mapped[str] = mapped_column(String(96))
    base_revision: Mapped[int] = mapped_column(Integer)
    result_revision: Mapped[int] = mapped_column(Integer)
    before_graph: Mapped[dict] = mapped_column(JSON, default=dict)
    after_graph: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

class AgentChatMessage(Base):
    """v3: 对话气泡 - Agent 与用户的多轮对话历史。"""
    __tablename__ = "agent_chat_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AgentStep(Base):
    """v3: 执行步骤 - 流式输出的来源。"""
    __tablename__ = "agent_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(240), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Asset(Base):
    """v3: 素材库 - 用户上传的、可被 Agent 检索的素材。"""
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))  # image | video | audio
    category: Mapped[str] = mapped_column(String(32), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    filename: Mapped[str] = mapped_column(String(255), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
