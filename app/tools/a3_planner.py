"""A3 内容策划智能体的工具集。

选题与排期类工具：渠道结构规范、关键词种子、发布时段与标题上限，
以及历史查重（topic_dedupe）——本任务拟用角度 vs A11 记忆库已沉淀选题。

``topic_dedupe`` 不经过编排层的 RAG 注入（``MEMORY_RECALL_AGENTS`` 只含
A1/A2/A4），而是**只读**直查记忆库：查重要看的是全库，不是按当前 Brief
召回的那 5 条——召回口径天然偏向「相关」，恰好会漏掉「相似但不同行业」的旧选题。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..knowledge.industry import channel_rule, publish_slots, seo_pattern, title_limit
from ..knowledge.memory import memory_store
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

#: 词面重合度（2-gram 召回率）达到该值即视为「疑似重复选题」
_DUPLICATE_THRESHOLD = 0.34


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


def _overlap_ratio(left: str, right: str) -> float:
    """词面重合度：较短一侧的 2-gram 有多少被另一侧覆盖（与记忆库召回同口径）。"""
    from ..knowledge.embedding import tokenize

    a, b = set(tokenize(left)), set(tokenize(right))
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def topic_dedupe(ctx: "AgentRunContext") -> ToolOutcome:
    """拟用角度 vs 记忆库历史选题的查重（只读，不改动库内计数）。"""
    from ..llm.json_utils import as_obj, as_str, as_str_array

    brief = ctx.brief
    strategy = ctx.upstream_of("strategy")
    creative = ctx.upstream_of("creative")
    big_idea = as_obj(creative.get("big_idea"))

    probes: list[str] = []
    probes.extend(as_str_array(strategy.get("key_takeaways"))[:3])
    probes.extend(
        part
        for part in (as_str(big_idea.get("title")), as_str(big_idea.get("statement")))
        if part.strip()
    )
    probes.extend(brief.keywords[:4])
    probes = [probe.strip() for probe in probes if probe.strip()]
    if not probes:
        return ToolOutcome(summary="上游暂无拟用角度，跳过查重", data={})

    cards = memory_store.list_cards(tenant=ctx.tenant, limit=200)
    if not cards:
        return ToolOutcome(
            summary="记忆库为空，暂无可比历史选题",
            detail=(
                f"本租户（{ctx.tenant}）记忆库暂无历史资产，**无法查重**——"
                "这不等于「本选题一定没发过」，只说明系统里没有可比记录。"
            ),
            data={"probes": probes, "history_size": 0, "collisions": []},
        )

    collisions: list[dict[str, Any]] = []
    for probe in probes:
        for card in cards:
            ratio = _overlap_ratio(probe, f"{card.title} {card.content}")
            if ratio >= _DUPLICATE_THRESHOLD:
                collisions.append(
                    {
                        "probe": probe,
                        "card_title": card.title,
                        "kind": card.kind,
                        "created_at": card.created_at,
                        "overlap": round(ratio, 3),
                    }
                )
    collisions.sort(key=lambda item: item["overlap"], reverse=True)
    collisions = collisions[:6]

    if collisions:
        lines = [
            f"- 拟用角度「{item['probe'][:24]}」≈ 历史卡片「{item['card_title']}」"
            f"（[{item['kind']}]，重合 {item['overlap']:.0%}，{item['created_at'][:10]}）"
            for item in collisions
        ]
        lines.append(
            "（来源：与 A11 记忆库标题/正文的 2-gram 词面重合度——"
            "命中不等于重复：可能是同一角度的续集，也可能是换汤不换药；"
            "请为命中项说明「差异点」，或改换角度并记入选题理由）"
        )
    else:
        lines = [f"未检出与历史卡片高度重合的角度（比对 {len(cards)} 张历史卡片）"]
        lines.append("（来源：同库 2-gram 词面比对——只覆盖词面相似，语义近似但换词表达不会命中）")

    detail = "\n".join(lines)
    return ToolOutcome(
        summary=(
            f"查重命中 {len(collisions)} 处（历史卡片 {len(cards)} 张）"
            if collisions
            else f"未检出重复选题（历史卡片 {len(cards)} 张）"
        ),
        detail=detail,
        data={
            "probes": probes,
            "history_size": len(cards),
            "collisions": collisions,
            "threshold": _DUPLICATE_THRESHOLD,
        },
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
    Tool(
        name="topic_dedupe",
        description="拟用角度与记忆库历史选题的重复度查重",
        agent_ids=("A3",),
        handler=topic_dedupe,
    ),
]

__all__ = ["TOOLS", "channel_structure", "keyword_seeds", "publish_windows", "topic_dedupe"]
