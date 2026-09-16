"""A9 渠道运营与 SEO 智能体的工具集。

对应《plan.md》2.2.3 的「SEO Server」内置替代：
搜索意图蓝图、全平台标题截断规则、发布时段、渠道合规清单种子。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..knowledge.industry import (
    CHANNEL_RULES,
    CHANNEL_TITLE_LIMIT,
    channel_rule,
    publish_slots,
    seo_pattern,
    title_limit,
)
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def seo_blueprint(ctx: "AgentRunContext") -> ToolOutcome:
    """该渠道的搜索意图、长尾词模板与关键词布局位置。"""
    brief = ctx.brief
    pattern = seo_pattern(brief.channel)
    seeds = brief.keywords or ([brief.product] if brief.product else [])
    filled = [
        modifier.replace("{kw}", keyword)
        for keyword in seeds[:3]
        for modifier in pattern.modifiers[:3]
    ][:9]
    lines = [
        f"搜索意图：{pattern.search_intent}",
        f"长尾词模板展开：{'、'.join(filled) or '（Brief 未提供关键词）'}",
        f"布局位置：{'；'.join(pattern.placement)}",
        f"竞争难度：{pattern.difficulty}｜注意：{'；'.join(pattern.notes[:2])}",
        "（来源：内置 SEO 规则库——关键词不得堆砌，标题同一关键词出现 1 次即可）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」SEO 蓝图与 {len(filled)} 条长尾词",
        detail="\n".join(lines),
        data={
            "search_intent": pattern.search_intent,
            "long_tail": filled,
            "placement": pattern.placement,
            "difficulty": pattern.difficulty,
            "notes": pattern.notes,
        },
    )


def title_rules(ctx: "AgentRunContext") -> ToolOutcome:
    """全平台标题字数上限（标题截断是硬约束，由工具给出、代码复核）。"""
    brief = ctx.brief
    lines = [
        f"{name}：{limit} 字"
        for name, limit in CHANNEL_TITLE_LIMIT.items()
    ]
    primary_limit = CHANNEL_TITLE_LIMIT.get(brief.channel, title_limit(brief.channel))
    lines.append("（来源：内置渠道规范库——超出上限必须给压缩版）")
    return ToolOutcome(
        summary=f"主渠道「{brief.channel}」标题上限 {primary_limit} 字",
        detail="\n".join(lines),
        data={
            "primary_channel": brief.channel,
            "primary_limit": primary_limit,
            "all_limits": dict(CHANNEL_TITLE_LIMIT),
        },
    )


def publish_windows(ctx: "AgentRunContext") -> ToolOutcome:
    """该渠道的建议发布时段（通用经验值）。"""
    brief = ctx.brief
    slots = publish_slots(brief.channel)
    lines = [
        f"建议时段：{'；'.join(slots)}",
        "（来源：内置经验时段——不承诺流量结果）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」{len(slots)} 个建议发布时段",
        detail="\n".join(lines),
        data={"slots": slots},
    )


def channel_policy(ctx: "AgentRunContext") -> ToolOutcome:
    """目标渠道的标签策略与合规注意项（channel_checklist 的核对种子）。"""
    brief = ctx.brief
    rule = channel_rule(brief.channel)
    lines = [
        f"标签策略：{rule.hashtag_policy}",
        f"合规注意：{'；'.join(rule.compliance_notes)}",
        "（来源：内置渠道规范库——channel_checklist 应逐条核对这些规则）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」标签策略与 {len(rule.compliance_notes)} 项合规注意",
        detail="\n".join(lines),
        data={
            "hashtag_policy": rule.hashtag_policy,
            "compliance_notes": rule.compliance_notes,
            "all_channels": list(CHANNEL_RULES.keys()),
        },
    )


TOOLS: list[Tool] = [
    Tool(
        name="seo_blueprint",
        description="该渠道的搜索意图/长尾词模板/布局位置",
        agent_ids=("A9",),
        handler=seo_blueprint,
    ),
    Tool(
        name="title_rules",
        description="全平台标题字数上限",
        agent_ids=("A9",),
        handler=title_rules,
    ),
    Tool(
        name="publish_windows",
        description="该渠道的建议发布时段",
        agent_ids=("A9",),
        handler=publish_windows,
    ),
    Tool(
        name="channel_policy",
        description="渠道标签策略与合规注意项",
        agent_ids=("A9",),
        handler=channel_policy,
    ),
]

__all__ = ["TOOLS", "seo_blueprint", "title_rules", "publish_windows", "channel_policy"]
