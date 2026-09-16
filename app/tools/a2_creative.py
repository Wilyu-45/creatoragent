"""A2 创意总监智能体的工具集。

创意参考类工具：案例库（与 A1 共用同一知识源）、视觉风格库、开场钩子结构。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..knowledge.industry import cases_for
from ..knowledge.visual import visual_styles_for
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def case_library(ctx: "AgentRunContext") -> ToolOutcome:
    """同行业、同渠道的历史案例，供 Big Idea 参考。"""
    brief = ctx.brief
    cases = cases_for(brief.industry, brief.channel)[:3]
    if not cases:
        return ToolOutcome(summary="案例库中暂无同类案例", data={"cases": []})
    lines = [
        f"{index}. {case.name}（{case.channel}）｜{case.angle}｜可借鉴：{case.why}"
        for index, case in enumerate(cases, start=1)
    ]
    lines.append("（来源：内置案例库——方向参考，非本品牌真实投放数据）")
    return ToolOutcome(
        summary=f"检索到 {len(cases)} 条同类案例",
        detail="\n".join(lines),
        data={
            "cases": [
                {"name": c.name, "angle": c.angle, "why": c.why, "channel": c.channel}
                for c in cases
            ]
        },
    )


def visual_style_library(ctx: "AgentRunContext") -> ToolOutcome:
    """按行业匹配度排序的视觉风格候选，支撑调性指南中的视觉建议。"""
    brief = ctx.brief
    styles = visual_styles_for(brief.industry)[:3]
    lines = [
        f"{index}. {style.name}｜情绪：{style.mood}｜构图：{style.composition}｜光线：{style.lighting}"
        for index, style in enumerate(styles, start=1)
    ]
    lines.append("（来源：内置视觉风格库）")
    return ToolOutcome(
        summary=f"检索到 {len(styles)} 个候选视觉风格",
        detail="\n".join(lines),
        data={
            "styles": [
                {
                    "key": style.key,
                    "name": style.name,
                    "mood": style.mood,
                    "composition": style.composition,
                    "lighting": style.lighting,
                }
                for style in styles
            ]
        },
    )


def hook_patterns(ctx: "AgentRunContext") -> ToolOutcome:
    """该渠道的必备内容结构，作为开场钩子与节奏的骨架参考。"""
    brief = ctx.brief
    from ..knowledge.industry import channel_rule

    rule = channel_rule(brief.channel)
    lines = [
        f"内容结构（{brief.channel}）：{' → '.join(rule.blocks)}",
        f"标题风格：{rule.title_style}",
        "（来源：内置渠道规范库——钩子须落在结构前段，不得承诺效果）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」内容结构与标题风格",
        detail="\n".join(lines),
        data={"blocks": rule.blocks, "title_style": rule.title_style},
    )


TOOLS: list[Tool] = [
    Tool(
        name="case_library",
        description="检索同行业、同渠道的历史案例",
        agent_ids=("A2",),
        handler=case_library,
    ),
    Tool(
        name="visual_style_library",
        description="按行业匹配度检索视觉风格候选",
        agent_ids=("A2",),
        handler=visual_style_library,
    ),
    Tool(
        name="hook_patterns",
        description="该渠道的必备内容结构与标题风格",
        agent_ids=("A2",),
        handler=hook_patterns,
    ),
]

__all__ = ["TOOLS", "case_library", "visual_style_library", "hook_patterns"]
