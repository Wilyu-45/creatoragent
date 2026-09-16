"""A3 内容策划智能体的工具集。

选题与排期类工具：渠道结构规范、关键词种子、发布时段与标题上限。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..knowledge.industry import channel_rule, publish_slots, seo_pattern, title_limit
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def channel_structure(ctx: "AgentRunContext") -> ToolOutcome:
    """目标渠道的内容结构 / 长度 / 标题风格 / 标签策略。"""
    brief = ctx.brief
    rule = channel_rule(brief.channel)
    limit = title_limit(brief.channel)
    lines = [
        f"内容形态：{rule.format}｜长度基准：{rule.length_hint}",
        f"必备结构：{' → '.join(rule.blocks)}",
        f"标题风格：{rule.title_style}（上限 {limit} 字）",
        f"标签策略：{rule.hashtag_policy}",
        "（来源：内置渠道规范库——大纲必须覆盖必备结构，标题不得超上限）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」结构规范与标题上限 {limit} 字",
        detail="\n".join(lines),
        data={
            "channel": brief.channel,
            "format": rule.format,
            "length_hint": rule.length_hint,
            "blocks": rule.blocks,
            "title_style": rule.title_style,
            "title_limit": limit,
            "hashtag_policy": rule.hashtag_policy,
        },
    )


def keyword_seeds(ctx: "AgentRunContext") -> ToolOutcome:
    """按渠道搜索意图生成长尾关键词种子（修饰模板 × Brief 关键词）。"""
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
        f"长尾词种子：{'、'.join(filled) or '（Brief 未提供关键词，请基于产品自拟）'}",
        f"关键词布局位置：{'；'.join(pattern.placement)}",
        f"竞争难度参考：{pattern.difficulty}",
        "（来源：内置 SEO 规则库）",
    ]
    return ToolOutcome(
        summary=f"生成 {len(filled)} 条长尾词种子",
        detail="\n".join(lines),
        data={
            "search_intent": pattern.search_intent,
            "seeds": filled,
            "placement": pattern.placement,
            "difficulty": pattern.difficulty,
            "notes": pattern.notes,
        },
    )


def publish_windows(ctx: "AgentRunContext") -> ToolOutcome:
    """该渠道的建议发布时段（通用经验值）。"""
    brief = ctx.brief
    slots = publish_slots(brief.channel)
    lines = [
        f"建议时段：{'；'.join(slots)}",
        "（来源：内置经验时段——面向人群活跃峰值的通用参考，不承诺流量结果）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」{len(slots)} 个建议发布时段",
        detail="\n".join(lines),
        data={"slots": slots},
    )


TOOLS: list[Tool] = [
    Tool(
        name="channel_structure",
        description="目标渠道的内容结构/长度/标题风格/标签策略",
        agent_ids=("A3",),
        handler=channel_structure,
    ),
    Tool(
        name="keyword_seeds",
        description="按渠道搜索意图生成长尾关键词种子",
        agent_ids=("A3",),
        handler=keyword_seeds,
    ),
    Tool(
        name="publish_windows",
        description="该渠道的建议发布时段",
        agent_ids=("A3",),
        handler=publish_windows,
    ),
]

__all__ = ["TOOLS", "channel_structure", "keyword_seeds", "publish_windows"]
