"""A8 视觉美术指导智能体的工具集。

视觉规范类工具：渠道画幅规格、风格库候选、视频脚本必要性判断。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..knowledge.industry import channel_rule
from ..knowledge.video import needs_video_script
from ..knowledge.visual import channel_visual_spec, visual_styles_for
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def visual_spec(ctx: "AgentRunContext") -> ToolOutcome:
    """目标渠道的画幅与张数规格。"""
    brief = ctx.brief
    main_ratio, cover_ratio, shot_count = channel_visual_spec(brief.channel)
    lines = [
        f"主图画幅：{main_ratio}｜封面画幅：{cover_ratio}｜建议张数：{shot_count}",
        "（来源：内置渠道视觉规范——image_prompts 与 assets 必须遵循该规格）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」规格：{main_ratio} / {cover_ratio} / {shot_count} 张",
        detail="\n".join(lines),
        data={
            "channel": brief.channel,
            "main_ratio": main_ratio,
            "cover_ratio": cover_ratio,
            "shot_count": shot_count,
        },
    )


def style_library(ctx: "AgentRunContext") -> ToolOutcome:
    """按行业匹配度的视觉风格候选（含负面词与配色）。"""
    brief = ctx.brief
    styles = visual_styles_for(brief.industry)[:4]
    lines: list[str] = []
    for index, style in enumerate(styles, start=1):
        palette = "、".join(f"{color.name}{color.hex}" for color in style.palette[:3])
        lines.append(
            f"{index}. {style.name}｜情绪：{style.mood}｜构图：{style.composition}｜"
            f"光线：{style.lighting}｜配色：{palette}"
        )
    lines.append("（来源：内置视觉风格库——visual_direction 应从中选择或说明偏离理由）")
    return ToolOutcome(
        summary=f"检索到 {len(styles)} 个候选风格",
        detail="\n".join(lines),
        data={
            "styles": [
                {
                    "key": style.key,
                    "name": style.name,
                    "mood": style.mood,
                    "composition": style.composition,
                    "lighting": style.lighting,
                    "negative": style.negative,
                    "palette": [
                        {"name": c.name, "hex": c.hex, "usage": c.usage}
                        for c in style.palette
                    ],
                }
                for style in styles
            ]
        },
    )


def video_check(ctx: "AgentRunContext") -> ToolOutcome:
    """判断本任务是否需要输出视频脚本，并给出命中原因。"""
    brief = ctx.brief
    needed, reasons = needs_video_script(
        channel=brief.channel,
        deliverables=brief.deliverables,
        channel_format=channel_rule(brief.channel).format,
    )
    detail = (
        "判定：需要输出视频脚本｜原因：" + "；".join(reasons)
        if needed
        else "判定：无需视频脚本（图文形态即可完整交付）"
    )
    detail += "（来源：内置渠道形态规则）"
    return ToolOutcome(
        summary="需要视频脚本" if needed else "无需视频脚本",
        detail=detail,
        data={"needed": needed, "reasons": reasons},
    )


TOOLS: list[Tool] = [
    Tool(
        name="visual_spec",
        description="目标渠道的画幅与张数规格",
        agent_ids=("A8",),
        handler=visual_spec,
    ),
    Tool(
        name="style_library",
        description="按行业匹配度检索视觉风格候选（含负面词与配色）",
        agent_ids=("A8",),
        handler=style_library,
    ),
    Tool(
        name="video_check",
        description="判断是否需要输出视频脚本",
        agent_ids=("A8",),
        handler=video_check,
    ),
]

__all__ = ["TOOLS", "visual_spec", "style_library", "video_check"]
