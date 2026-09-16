"""A4 文案创作智能体的工具集。

写前防错类工具：广告法禁用词预览（写前规避，减少 A7 返工）、
品牌词库（记忆库 brand 卡片中的调性基线与有效表达）。
渠道规范已内联在 A4 提示词中，不再重复为工具。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..knowledge.compliance import LEXICON_GROUPS
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def banned_words(ctx: "AgentRunContext") -> ToolOutcome:
    """广告法禁用词预览：blocker 级通用词 + 本行业专项词。

    写前规避的价值：真实链路实测，无来源数据与绝对化用语是 A6/A7 返工的
    两大主因；把红线词在写前亮出来，比写后被否决便宜得多。
    """
    brief = ctx.brief
    sections: list[str] = []
    data_groups: list[dict[str, Any]] = []
    total = 0
    for group in LEXICON_GROUPS:
        if group.severity != "blocker":
            continue
        terms = group.terms[:18]
        total += len(terms)
        sections.append(f"【{group.category}】（{group.law.split('：')[0]}）：{'、'.join(terms)}…")
        data_groups.append(
            {"category": group.category, "severity": group.severity, "terms": group.terms}
        )
    for rule in _industry_rules(brief.industry):
        terms = rule.terms[:8]
        total += len(terms)
        sections.append(f"【{rule.category}·{brief.industry}专项】：{'、'.join(terms)}")
        data_groups.append(
            {"category": rule.category, "severity": rule.severity, "terms": rule.terms}
        )
    sections.append("（来源：内置广告法词库——以上为部分预览，正文应完全避免此类表述）")
    return ToolOutcome(
        summary=f"载入 {total} 个禁用词红线（写前规避）",
        detail="\n".join(sections),
        data={"groups": data_groups},
    )


def _industry_rules(industry: str):
    from ..knowledge.compliance import INDUSTRY_RULES

    return INDUSTRY_RULES.get(industry, [])


def brand_lexicon(ctx: "AgentRunContext") -> ToolOutcome:
    """从 A11 记忆库的品牌卡片中提取调性基线与有效表达。"""
    cards = [hit for hit in ctx.memory if str(hit.get("kind")) == "brand"][:3]
    if not cards:
        return ToolOutcome(summary="知识库暂无本品牌资产卡片", data={"cards": []})
    lines = [
        f"{index}. {card.get('title')}：{str(card.get('content'))[:80]}"
        for index, card in enumerate(cards, start=1)
    ]
    lines.append("（来源：A11 记忆库·品牌资产——历史沉淀，供保持调性一致）")
    return ToolOutcome(
        summary=f"召回 {len(cards)} 张品牌资产卡片",
        detail="\n".join(lines),
        data={
            "cards": [
                {"title": card.get("title"), "content": card.get("content")}
                for card in cards
            ]
        },
    )


TOOLS: list[Tool] = [
    Tool(
        name="banned_words",
        description="广告法禁用词红线预览（写前规避）",
        agent_ids=("A4",),
        handler=banned_words,
    ),
    Tool(
        name="brand_lexicon",
        description="从记忆库品牌卡片提取调性基线与有效表达",
        agent_ids=("A4",),
        handler=brand_lexicon,
    ),
]

__all__ = ["TOOLS", "banned_words", "brand_lexicon"]
