"""A4 文案创作智能体的工具集。

写前防错类工具：广告法禁用词预览（写前规避，减少 A7 返工）、
品牌词库（记忆库 brand 卡片中的调性基线与有效表达）、
标题备选的**开场钩子强度评分**（hook_strength）。
渠道规范已内联在 A4 提示词中，不再重复为工具。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..knowledge.compliance import INDUSTRY_RULES, LEXICON_GROUPS
from ..knowledge.industry import title_limit
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

#: 开场钩子的可测量信号（正则，命中即计一项）。这些是**文本特征**，
#: 不代表效果保证——钩子强度评分只用于同批备选之间的相对比较。
_HOOK_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("数字锚点", re.compile(r"\d")),
    ("悬念/提问", re.compile(r"[?？]|吗|如何|怎么|为什么|到底|居然|竟然")),
    ("身份锚点", re.compile(r"新手|学生党|宝妈|上班族|打工人|通勤族|敏感肌|油皮|干皮|租房|预算党")),
    ("让渡/反差", re.compile(r"别再|不要再|不是|其实|反而|后悔|踩坑|避雷|翻车|劝退")),
    ("具体场景", re.compile(r"早上|晚上|通勤|办公室|出差|睡前|周末|开会|聚会|换季|加班|出差")),
    ("情绪承诺", re.compile(r"省心|安心|松弛|终于|舒服|惊喜|解压|不折腾|少走弯路")),
)


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
    return INDUSTRY_RULES.get(industry, [])


def _redline_terms(industry: str) -> set[str]:
    """写前红线词集合：blocker/major 级通用词 + 本行业专项词。"""
    terms: set[str] = set()
    for group in LEXICON_GROUPS:
        if group.severity in ("blocker", "major"):
            terms.update(group.terms)
    for rule in _industry_rules(industry):
        if rule.severity in ("blocker", "major"):
            terms.update(rule.terms)
    return terms


def hook_strength(ctx: "AgentRunContext") -> ToolOutcome:
    """对 A3 的标题备选做开场钩子强度评分 + 红线词预检。

    工具在 A4 动笔前执行，评的是**上游已经给出的标题备选**：A4 既要从中选标题，
    也要写出与之匹配的开场，先看清哪条备选的钩子最结实，比写完再回头改便宜。
    """
    from ..llm.json_utils import as_str_array

    brief = ctx.brief
    candidates = as_str_array(ctx.upstream_of("plan").get("headline_candidates"))
    if not candidates:
        return ToolOutcome(summary="上游暂无标题备选，跳过钩子强度评分", data={"candidates": []})

    limit = title_limit(brief.channel)
    redlines = _redline_terms(brief.industry)

    rows: list[dict[str, Any]] = []
    for text in candidates[:10]:
        signals = [label for label, pattern in _HOOK_SIGNALS if pattern.search(text)]
        hits = sorted({term for term in redlines if term in text})
        over = len(text) > limit
        score = 40 + 12 * len(signals) - 28 * len(hits) - (15 if over else 0)
        rows.append(
            {
                "text": text,
                "length": len(text),
                "title_limit": limit,
                "over_limit": over,
                "score": int(max(5, min(100, score))),
                "signals": signals,
                "blocked_terms": hits,
            }
        )
    rows.sort(key=lambda row: row["score"], reverse=True)

    lines = []
    for index, row in enumerate(rows, start=1):
        marks = "、".join(row["signals"]) or "无钩子信号"
        line = f"{index}. {row['score']} 分｜{row['text']}（{row['length']}/{limit} 字；{marks}"
        if row["over_limit"]:
            line += "；⚠️超标题上限"
        if row["blocked_terms"]:
            line += f"；⛔红线词：{'、'.join(row['blocked_terms'])}"
        lines.append(line + "）")
    lines.append(
        "（来源：内置正则特征 + 词库预检——**钩子强度是文本特征计数，不是点击率预测**；"
        "最高分只是同批备选里的相对最优，可结合语境另选并说明理由；"
        "命中红线词的备选不要使用，A7 会直接退回）"
    )
    return ToolOutcome(
        summary=f"评分 {len(rows)} 条标题备选，最高 {rows[0]['score']} 分",
        detail="\n".join(lines),
        data={"title_limit": limit, "candidates": rows},
    )


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
    Tool(
        name="hook_strength",
        description="标题备选的开场钩子强度评分与红线词预检",
        agent_ids=("A4",),
        handler=hook_strength,
    ),
]

__all__ = ["TOOLS", "banned_words", "brand_lexicon", "hook_strength"]
