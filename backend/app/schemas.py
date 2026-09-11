from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    display_name: str = ""
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: Literal["admin", "user"] = "user"
    display_name: str = ""


class UserUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    role: Optional[Literal["admin", "user"]] = None
    display_name: Optional[str] = None
    is_active: Optional[bool] = None


class PromptSkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    instructions: str = Field(min_length=1, max_length=12000)
    enabled: bool = True


class PromptSkillUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=240)
    instructions: Optional[str] = Field(default=None, min_length=1, max_length=12000)
    enabled: Optional[bool] = None


class PromptSkillOut(BaseModel):
    id: str
    name: str
    description: str
    instructions: str
    enabled: bool
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None


class ConversationOut(BaseModel):
    id: str
    title: str
    last_model: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str
    prompt: str
    resolved_prompt: str
    model: str
    params: dict[str, Any] = {}
    reference_images: List[str] = []
    reference_media: List[dict[str, Any]] = []
    status: str
    task_id: str
    video_url: str
    local_video: str
    video_expired: bool = False
    error: str
    elapsed_seconds: int
    created_at: datetime
    # 下面两项由后端签发，前端直接拿去当 src 用，不要自己拼 /media/ 路径
    video_src: str = ""
    reference_image_urls: List[str] = []

    class Config:
        from_attributes = True


class ReferenceMedia(BaseModel):
    kind: Literal["image", "end_frame", "video", "audio"]
    url: str = Field(min_length=1, max_length=4000)
    name: str = Field(default="", max_length=255)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: str
    capability: str = "t2v"          # t2v | i2v | r2v
    resolution: str
    ratio: str = ""
    duration: int
    watermark: Optional[bool] = None
    audio: Optional[bool] = None
    reference_images: List[str] = []
    reference_media: List[ReferenceMedia] = []
    use_context: bool = True
    skill_id: Optional[str] = None


class GenerateResponse(BaseModel):
    conversation: ConversationOut
    user_message: MessageOut
    assistant_message: MessageOut


class UploadOut(BaseModel):
    url: str            # 规范相对路径，存库与提交生成时用这个
    preview_url: str    # 带签名的预览地址，前端显示用
    absolute_url: str
    filename: str
    kind: Literal["image", "video", "audio"]


class AgentPlanRequest(BaseModel):
    surface: Literal["studio", "canvas"]
    target_id: str = Field(default="", max_length=64)
    user_input: str = Field(min_length=1, max_length=4000)
    agent_model_id: Optional[str] = Field(default=None, max_length=128)
    target_duration: Optional[int] = Field(default=None, ge=1, le=600)
    autonomy: Literal["suggest", "confirm", "auto"] = "confirm"
    reference_media: List[ReferenceMedia] = []


class AgentTurnOut(BaseModel):
    id: str
    surface: str
    target_id: str
    status: str
    skill_id: str
    plan: dict[str, Any]
    est_cost: float
    est_seconds: int
    expires_at: datetime
    created_at: datetime
    accepted_at: Optional[datetime] = None
    agent_model_id: str = ""
    warning: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    repair_count: int = 0


class AgentAcceptRequest(BaseModel):
    # UI can edit draft parameters, but all fields are revalidated at acceptance.
    plan: Optional[dict[str, Any]] = None
    execute: bool = False


class AgentTaskOut(BaseModel):
    node_id: str
    task_type: str
    status: str
    depends_on: list[str] = []
    message_id: Optional[str] = None
    output_file: str = ""
    video_src: str = ""
    error: str = ""


class AgentRunOut(BaseModel):
    id: str
    turn_id: str
    status: str
    output_file: str = ""
    video_src: str = ""
    error: str = ""
    created_at: datetime
    updated_at: datetime
    tasks: list[AgentTaskOut] = []


class CanvasCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)


class CanvasPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    viewport: Optional[dict[str, float]] = None


class CanvasNodeIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    type: str
    position: dict[str, float]
    size: Optional[dict[str, float]] = None
    data: dict[str, Any]


class CanvasEdgeIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    source: str
    source_handle: str
    target: str
    target_handle: str


class CanvasGraphPut(BaseModel):
    updated_at: datetime
    viewport: dict[str, float]
    nodes: list[CanvasNodeIn]
    edges: list[CanvasEdgeIn]


class CanvasOut(BaseModel):
    id: str
    title: str
    viewport: dict[str, float]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CanvasNodeOut(CanvasNodeIn):
    status: str = ""
    message_id: Optional[str] = None
    input_hash: str = ""

    class Config:
        from_attributes = True


class CanvasEdgeOut(CanvasEdgeIn):
    class Config:
        from_attributes = True


class CanvasDetail(CanvasOut):
    nodes: list[CanvasNodeOut] = Field(default_factory=list)
    edges: list[CanvasEdgeOut] = Field(default_factory=list)
