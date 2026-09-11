"""视频模型目录。

各模型的参数取值均依据阿里云百炼官方 API 文档（2026-09 核对）：
- 万相 3.0    https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference
- MiniMax H3  https://help.aliyun.com/zh/model-studio/minimax-video-generation-api-reference
- HappyHorse 文生视频  https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference
- HappyHorse 图生视频  https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference
- HappyHorse 参考生视频 https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference

新增或调整模型只改这个文件，前端的下拉选项、能力标注会自动同步。
"""
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .config import settings

ROLE_ALL = ["admin", "user"]
ROLE_ADMIN_ONLY = ["admin"]

# 三种生成方式
T2V = "t2v"
I2V = "i2v"
R2V = "r2v"

MODE_LABELS = {
    T2V: "文生视频",
    I2V: "图生视频",
    R2V: "参考生视频",
}


@dataclass
class MediaInput:
    """一种可连接/上传的输入素材及其供应商字段约束。"""

    kind: str                         # image | video | audio
    media_type: str                   # 提交给 DashScope 的 media[].type
    label: str
    min_count: int = 0
    max_count: int = 1
    accept: str = ""
    max_mb: int = 20
    note: str = ""


@dataclass
class Capability:
    """一个模型支持的一种生成方式。"""

    id: str
    description: str
    media_type: str = ""            # 提交给接口的 media[].type，文生视频为空
    min_images: int = 0
    max_images: int = 0
    ratios: Optional[List[str]] = None       # 该方式下的比例取值（覆盖模型默认）
    default_ratio: Optional[str] = None
    supports_ratio: bool = True
    media_inputs: List[MediaInput] = field(default_factory=list)
    min_media: int = 0               # 多模态参考模式至少需要的素材总数

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.id, self.id)

    def input_specs(self) -> List[MediaInput]:
        """统一返回素材槽；旧的图片能力也通过这个接口暴露。"""
        if self.media_inputs:
            return self.media_inputs
        if not self.max_images:
            return []
        return [
            MediaInput(
                kind="image",
                media_type=self.media_type,
                label="首帧图" if self.id == I2V else "参考图",
                min_count=self.min_images,
                max_count=self.max_images,
                accept=".jpg,.jpeg,.png,.webp,.bmp",
                max_mb=20,
            )
        ]

    def minimum_media(self) -> int:
        return max(self.min_media, sum(item.min_count for item in self.input_specs()))


@dataclass
class VideoModel:
    id: str
    label: str
    description: str
    key_group: str                  # 对应 .env 中的哪一组 API Key
    allowed_roles: List[str]
    capabilities: List[Capability]
    resolutions: List[str]
    default_resolution: str
    ratios: List[str]
    default_ratio: str
    duration_min: int
    duration_max: int
    default_duration: int = 5
    resolution_note: str = ""        # 分辨率档位的额外说明（各模型档位不同）
    supports_base64_media: bool = False  # media[].url 是否接受 data:image/...;base64,
    supports_watermark: bool = False
    watermark_default: bool = False
    supports_audio: bool = False
    audio_default: bool = True
    doc_url: str = ""
    notes: List[str] = field(default_factory=list)  # 官方支持、但本系统暂未开放的能力

    def capability(self, mode: str) -> Optional[Capability]:
        return next((c for c in self.capabilities if c.id == mode), None)

    def durations(self) -> List[int]:
        top = min(self.duration_max, settings.max_duration)
        return [d for d in range(self.duration_min, top + 1)]

    def ratios_for(self, mode: str) -> List[str]:
        cap = self.capability(mode)
        if cap and not cap.supports_ratio:
            return []
        if cap and cap.ratios is not None:
            return cap.ratios
        return self.ratios

    def default_ratio_for(self, mode: str) -> str:
        cap = self.capability(mode)
        if cap and not cap.supports_ratio:
            return ""
        if cap and cap.default_ratio:
            return cap.default_ratio
        return self.default_ratio

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("key_group", None)
        data.pop("allowed_roles", None)
        data["durations"] = self.durations()
        data["default_duration"] = min(self.default_duration, settings.max_duration)
        for index, cap in enumerate(data["capabilities"]):
            source = self.capabilities[index]
            cap["label"] = MODE_LABELS.get(cap["id"], cap["id"])
            cap["media_inputs"] = [asdict(item) for item in source.input_specs()]
            cap["min_media"] = source.minimum_media()
        # 每种生成方式下实际可选的比例，前端直接用
        data["ratio_options"] = {
            cap.id: {
                "options": self.ratios_for(cap.id),
                "default": self.default_ratio_for(cap.id),
            }
            for cap in self.capabilities
        }
        return data


# HappyHorse 三个模型共用的比例列表（官方文档一致，不支持 adaptive）
HH_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "9:21", "21:9"]
HH_RESOLUTIONS = ["480P", "720P", "1080P"]

MODELS: List[VideoModel] = [
    VideoModel(
        id="wan3.0-video-prime",
        label="Wan 3.0 Video Prime",
        description="通义万相 3.0 高速版，一个模型覆盖全部生成方式，原生输出对白、BGM 与音效。",
        key_group="wan",
        allowed_roles=ROLE_ADMIN_ONLY,
        capabilities=[
            Capability(T2V, "只给提示词，不传任何素材"),
            Capability(I2V, "以一张图作为视频首帧", "first_frame", 1, 1),
            Capability(
                R2V,
                "传入参考图片、视频或音频，融合其中的主体、动作、风格与声音",
                "reference_image",
                0,
                10,
                media_inputs=[
                    MediaInput(
                        "image", "reference_image", "参考图", 0, 10,
                        ".jpg,.jpeg,.png,.webp,.bmp", 20,
                        "用于参考人物、物体或风格，不会被强制作为首帧",
                    ),
                    MediaInput(
                        "video", "reference_video", "参考视频", 0, 5,
                        ".mp4,.mov", 100,
                        "单段 1-15 秒，合计不超过 15 秒",
                    ),
                    MediaInput(
                        "audio", "reference_audio", "参考音频", 0, 5,
                        ".mp3,.wav", 15,
                        "单段 1-15 秒，合计不超过 15 秒",
                    ),
                ],
                min_media=1,
            ),
        ],
        resolutions=HH_RESOLUTIONS,
        default_resolution="1080P",
        resolution_note="万相 3.0 三档通用，全部生成方式一致",
        supports_base64_media=True,
        ratios=["adaptive", "16:9", "9:16", "1:1", "4:3", "3:4"],
        default_ratio="adaptive",
        duration_min=2,
        duration_max=30,
        supports_watermark=True,
        watermark_default=False,
        supports_audio=True,
        audio_default=True,
        doc_url="https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference",
        notes=[
            "多模态参考支持参考图、参考视频与参考音频自由组合；首帧/尾帧素材不能与参考素材混用",
            "视频编辑与续写可通过参考视频配合明确的编辑或延长提示词完成",
            f"官方时长上限 30 秒，本系统按配置限制为 {settings.max_duration} 秒",
        ],
    ),
    VideoModel(
        id="MiniMax/MiniMax-H3",
        label="MiniMax H3",
        description="MiniMax H3，擅长强戏剧性与电影感镜头，支持 2K 输出。",
        key_group="wan",
        allowed_roles=ROLE_ADMIN_ONLY,
        capabilities=[
            Capability(
                T2V,
                "只给提示词；该方式必须指定明确比例，不能用 adaptive",
                ratios=["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
                default_ratio="16:9",
            ),
            Capability(
                I2V,
                "以一张图作为视频首帧；该方式比例恒为自适应，传其他值会被忽略",
                "first_frame",
                1,
                1,
                supports_ratio=False,
            ),
            Capability(
                R2V,
                "多模态参考生视频，可组合参考图片、视频与驱动音频",
                "image_url",
                0,
                9,
                media_inputs=[
                    MediaInput(
                        "image", "image_url", "参考图", 0, 9,
                        ".jpg,.jpeg,.png,.webp,.heic,.heif", 30,
                        "用于参考人物、物体或风格，不会被强制作为首帧",
                    ),
                    MediaInput(
                        "video", "feature", "参考视频", 0, 3,
                        ".mp4,.mov", 50,
                        "单段 2-15 秒，合计不超过 15 秒",
                    ),
                    MediaInput(
                        "audio", "driving_audio", "驱动音频", 0, 3,
                        ".mp3,.wav", 15,
                        "单段 2-15 秒，合计不超过 15 秒",
                    ),
                ],
                min_media=1,
            ),
        ],
        resolutions=["768P", "2K"],
        default_resolution="768P",
        resolution_note="MiniMax H3 官方仅提供 768P 与 2K 两档，没有 1080P",
        ratios=["adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
        default_ratio="adaptive",
        duration_min=4,
        duration_max=15,
        supports_watermark=True,
        watermark_default=False,
        doc_url="https://help.aliyun.com/zh/model-studio/minimax-video-generation-api-reference",
        notes=[
            "多模态参考支持参考图、参考视频与驱动音频自由组合；首帧/尾帧素材不能与参考素材混用",
            "仅北京地域可用，请使用该地域的 API Key",
            "图片只接受公网 http(s) 链接，不支持 Base64，本机上传需先配置 PUBLIC_BASE_URL",
        ],
    ),
    VideoModel(
        id="happyhorse-1.1-t2v",
        label="HappyHorse 1.1 T2V",
        description="快马 1.1 文生视频，速度快、性价比高，只支持纯文本生成。",
        key_group="happyhorse",
        allowed_roles=ROLE_ALL,
        capabilities=[Capability(T2V, "只给提示词，不传任何素材")],
        resolutions=HH_RESOLUTIONS,
        default_resolution="1080P",
        resolution_note="快马 1.1 三档通用",
        supports_base64_media=True,
        ratios=HH_RATIOS,
        default_ratio="16:9",
        duration_min=3,
        duration_max=15,
        supports_watermark=True,
        watermark_default=True,
        doc_url="https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference",
    ),
    VideoModel(
        id="happyhorse-1.1-i2v",
        label="HappyHorse 1.1 I2V",
        description="快马 1.1 图生视频，以上传的图片作为首帧让画面动起来，适合商品展示、静图转动态。",
        key_group="happyhorse",
        allowed_roles=ROLE_ALL,
        capabilities=[
            Capability(
                I2V,
                "上传 1 张图作为首帧；画面比例自动跟随该图，无需选择",
                "first_frame",
                1,
                1,
                supports_ratio=False,
            )
        ],
        resolutions=HH_RESOLUTIONS,
        default_resolution="1080P",
        resolution_note="快马 1.1 三档通用",
        supports_base64_media=True,
        ratios=[],
        default_ratio="",
        duration_min=3,
        duration_max=15,
        supports_watermark=True,
        watermark_default=True,
        doc_url="https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference",
        notes=["首帧图要求：JPEG/JPG/PNG/WEBP，不小于 300×300，宽高比 1:2.5 ~ 2.5:1，不超过 20MB"],
    ),
    VideoModel(
        id="happyhorse-1.1-r2v",
        label="HappyHorse 1.1 R2V",
        description="快马 1.1 参考生视频，最多 9 张参考图，用 [Image 1] 在提示词中指名道姓地引用。",
        key_group="happyhorse",
        allowed_roles=ROLE_ALL,
        capabilities=[
            Capability(R2V, "上传 1-9 张参考图，融合其中的人物、道具与风格", "reference_image", 1, 9)
        ],
        resolutions=HH_RESOLUTIONS,
        default_resolution="1080P",
        resolution_note="快马 1.1 三档通用",
        supports_base64_media=True,
        ratios=HH_RATIOS,
        default_ratio="16:9",
        duration_min=3,
        duration_max=15,
        supports_watermark=True,
        watermark_default=True,
        doc_url="https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference",
        notes=["提示词中用 [Image 1]、[Image 2] 按上传顺序引用参考图，并说明引用的是图中哪个对象"],
    ),
]

MODEL_MAP = {m.id: m for m in MODELS}


def get_model(model_id: str) -> Optional[VideoModel]:
    return MODEL_MAP.get(model_id)


def models_for_role(role: str) -> List[VideoModel]:
    return [m for m in MODELS if role in m.allowed_roles]


def can_use(role: str, model_id: str) -> bool:
    model = get_model(model_id)
    return bool(model and role in model.allowed_roles)


def capability_matrix() -> List[dict]:
    """给前端展示的“哪个模型支持哪种生成方式”对照表。"""
    return [
        {
            "id": m.id,
            "label": m.label,
            "modes": [c.id for c in m.capabilities],
            "doc_url": m.doc_url,
        }
        for m in MODELS
    ]
