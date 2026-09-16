"""A1 策略与洞察智能体的工具集。

对应《plan.md》2.2.3 的「搜索 Server / RAG Server」内置替代：
行业洞察画像（industry_insight）、案例库检索（case_library）、
渠道格局（channel_landscape）。记忆召回由编排层注入 ``ctx.memory``，不在此重复。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..knowledge.industry import CHANNEL_RULES, cases_for, channel_rule, industry_profile
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def industry_insight(ctx: "AgentRunContext") -> ToolOutcome:
    """内置行业洞察画像：痛点 / 场景 / 动机 / 阻力 / 证明素材类型。"""
    brief = ctx.brief
    profile = industry_profile(brief.industry)
    detail = "\n".join(
        [
            f"行业痛点参考：{'；'.join(profile.pain_points[:4])}",
            f"典型场景：{'；'.join(profile.scenarios[:4])}",
            f"核心动机：{'；'.join(profile.motivations[:4])}",
            f"决策阻力：{'；'.join(profile.objections[:3])}",
            f"可准备的证明素材类型：{'；'.join(profile.proof_assets[:4])}",
            "（来源：内置行业洞察库——供推断参考，不得当作调研数据或用户证言引用）",
        ]
    )
    return ToolOutcome(
        summary=f"检索到「{brief.industry}」行业洞察画像",
        detail=detail,
        data={
            "industry": brief.industry,
            "pain_points": profile.pain_points,
            "scenarios": profile.scenarios,
            "motivations": profile.motivations,
            "objections": profile.objections,
            "proof_assets": profile.proof_assets,
        },
    )


def case_library(ctx: "AgentRunContext") -> ToolOutcome:
    """同行业、同渠道的历史案例参考。"""
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
                {
                    "name": case.name,
                    "industry": case.industry,
                    "channel": case.channel,
                    "angle": case.angle,
                    "why": case.why,
                    "source": case.source,
                }
                for case in cases
            ]
        },
    )


def channel_landscape(ctx: "AgentRunContext") -> ToolOutcome:
    """全部候选渠道的内容形态一览，支撑渠道优先级判断。"""
    brief = ctx.brief
    primary = channel_rule(brief.channel)
    lines = [f"主渠道「{brief.channel}」：{primary.format}（{primary.length_hint}）"]
    others = [
        f"{name}：{rule.format}"
        for name, rule in CHANNEL_RULES.items()
        if name != brief.channel
    ]
    lines.extend(f"候选——{item}" for item in others)
    lines.append("（来源：内置渠道规范库）")
    return ToolOutcome(
        summary=f"渠道格局：主渠道 {brief.channel} + {len(others)} 个候选渠道",
        detail="\n".join(lines),
        data={
            "primary_channel": brief.channel,
            "primary_format": primary.format,
            "candidates": [
                {"channel": name, "format": rule.format}
                for name, rule in CHANNEL_RULES.items()
                if name != brief.channel
            ],
        },
    )


TOOLS: list[Tool] = [
    Tool(
        name="industry_insight",
        description="检索行业洞察画像（痛点/场景/动机/阻力/证明素材）",
        agent_ids=("A1",),
        handler=industry_insight,
    ),
    Tool(
        name="case_library",
        description="检索同行业、同渠道的历史案例",
        agent_ids=("A1",),
        handler=case_library,
    ),
    Tool(
        name="channel_landscape",
        description="查看全部候选渠道的内容形态，支撑渠道优先级判断",
        agent_ids=("A1",),
        handler=channel_landscape,
    ),
]

__all__ = ["TOOLS", "industry_insight", "case_library", "channel_landscape"]
