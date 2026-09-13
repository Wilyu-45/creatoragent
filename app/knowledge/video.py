"""视频脚本：判断是否需要、以及结构化脚本骨架（plan.md v2.0「视频脚本」）。

为什么单独成模块
----------------
「要不要出视频脚本」与「脚本长什么样」是**知识层判断**，不是某个智能体的私有逻辑：
A8（视觉/分镜）负责产出，A9（渠道）需要按平台时长做二次裁剪，前端要按结构渲染，
黄金数据集要断言「短视频 Brief 必须产出带时长与口播的脚本」。
放在知识层，这些消费者才共用同一套判断与结构。

判断口径
--------
需要视频脚本的情形（任一命中）：

* 渠道本身是短视频平台（抖音 / 视频号 / TikTok / YouTube Shorts / Reels …）；
* 交付物里点名要脚本（「短视频脚本」「视频脚本」「口播稿」「分镜」「TVC」…）；
* 渠道形态声明里含视频特征（知识层的 ``ChannelRule.format`` 提到「短视频」「口播」「分镜」）。

**刻意不做「一律产出」**：图文类渠道硬塞一份视频脚本只会制造噪声，
而且会让「交付物是否符合 Brief」这件事失去可判断性。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 短视频平台渠道名（含常见中英文写法）
VIDEO_CHANNELS: tuple[str, ...] = (
    "抖音",
    "快手",
    "视频号",
    "bilibili",
    "B站",
    "小红书视频",
    "TikTok",
    "YouTube",
    "Instagram Reels",
    "Reels",
    "Shorts",
)

#: 交付物里表示「要脚本」的关键词
VIDEO_DELIVERABLE_HINTS: tuple[str, ...] = (
    "短视频",
    "视频脚本",
    "脚本",
    "口播",
    "分镜",
    "storyboard",
    "script",
    "TVC",
    "宣传片",
    "vlog",
)

#: 渠道形态里表示视频的关键词
_VIDEO_FORMAT_HINTS: tuple[str, ...] = ("短视频", "口播", "分镜", "视频", "video")


@dataclass
class VideoSpec:
    """视频脚本的形态参数。"""

    #: 目标时长（秒）
    duration_seconds: int = 45
    #: 画幅
    aspect_ratio: str = "9:16"
    #: 镜头数
    shot_count: int = 5
    #: 是否有口播
    has_voiceover: bool = True
    #: 是否需字幕
    has_subtitles: bool = True
    #: 命中原因（用于可解释性：为什么系统认为要出脚本）
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "duration_seconds": self.duration_seconds,
            "aspect_ratio": self.aspect_ratio,
            "shot_count": self.shot_count,
            "has_voiceover": self.has_voiceover,
            "has_subtitles": self.has_subtitles,
            "reasons": list(self.reasons),
        }


#: 渠道 → (时长秒, 画幅, 镜头数)
_CHANNEL_VIDEO_SPECS: dict[str, tuple[int, str, int]] = {
    "抖音": (45, "9:16", 5),
    "快手": (40, "9:16", 5),
    "视频号": (60, "9:16", 6),
    "bilibili": (120, "16:9", 8),
    "B站": (120, "16:9", 8),
    "tiktok": (30, "9:16", 5),
    "youtube": (60, "16:9", 6),
    "reels": (30, "9:16", 5),
    "shorts": (30, "9:16", 5),
}

DEFAULT_VIDEO_SPEC = (45, "9:16", 5)


def needs_video_script(
    *,
    channel: str,
    deliverables: list[str] | None = None,
    channel_format: str = "",
) -> tuple[bool, list[str]]:
    """判断是否需要视频脚本，并给出命中的原因（便于解释与调试）。"""
    reasons: list[str] = []
    name = (channel or "").strip()
    lower = name.lower()

    for video_channel in VIDEO_CHANNELS:
        if video_channel.lower() in lower:
            reasons.append(f"渠道「{name}」属于短视频平台")
            break

    for item in deliverables or []:
        text = str(item)
        if any(hint.lower() in text.lower() for hint in VIDEO_DELIVERABLE_HINTS):
            reasons.append(f"交付物点名要求脚本：「{text}」")
            break

    if channel_format and any(hint in channel_format for hint in _VIDEO_FORMAT_HINTS):
        reasons.append("渠道形态规范含视频特征")

    return bool(reasons), reasons


def video_spec(channel: str) -> VideoSpec:
    """按渠道给出视频形态参数。"""
    key = (channel or "").strip().lower()
    for name, (duration, ratio, shots) in _CHANNEL_VIDEO_SPECS.items():
        if name.lower() in key:
            return VideoSpec(duration_seconds=duration, aspect_ratio=ratio, shot_count=shots)
    duration, ratio, shots = DEFAULT_VIDEO_SPEC
    return VideoSpec(duration_seconds=duration, aspect_ratio=ratio, shot_count=shots)


#: 分镜骨架：每个镜头的时间占比与作用（0-1 的区间）
SHOT_SKELETON: tuple[tuple[float, float, str, str], ...] = (
    (0.00, 0.07, "钩子", "3 秒内制造冲突或悬念，直接给结果不给铺垫"),
    (0.07, 0.20, "痛点", "把观众正在经历的具体场景摆出来"),
    (0.20, 0.62, "方案", "给做法与证据，用可核对的细节替代形容词"),
    (0.62, 0.87, "佐证", "真实使用记录 / 参数 / 第三方数据"),
    (0.87, 1.00, "转化", "一句明确行动指令，不做诱导性表述"),
)


def script_skeleton(channel: str, *, duration_seconds: int | None = None) -> list[dict[str, object]]:
    """按渠道时长生成分镜骨架（时间轴 + 每镜作用）。

    只给**骨架**（时间与作用），具体画面与文案由智能体填充 ——
    知识层不应假装知道这次创作的具体内容。
    """
    spec = video_spec(channel)
    total = duration_seconds or spec.duration_seconds
    shots: list[dict[str, object]] = []
    for index, (start_ratio, end_ratio, role, intent) in enumerate(SHOT_SKELETON, start=1):
        start = round(total * start_ratio)
        end = round(total * end_ratio)
        shots.append(
            {
                "shot": index,
                "role": role,
                "start_second": start,
                "end_second": max(end, start + 1),
                "duration_seconds": max(1, end - start),
                "intent": intent,
            }
        )
    return shots


def required_sections() -> list[str]:
    """视频脚本必须齐备的段落（供断言与提示词共用）。"""
    return ["hook", "shots", "voiceover", "subtitles", "cta"]


__all__ = [
    "DEFAULT_VIDEO_SPEC",
    "SHOT_SKELETON",
    "VIDEO_CHANNELS",
    "VIDEO_DELIVERABLE_HINTS",
    "VideoSpec",
    "needs_video_script",
    "required_sections",
    "script_skeleton",
    "video_spec",
]
