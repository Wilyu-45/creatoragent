"""A10 数据分析与复盘智能体的工具集。

对应《plan.md》2.2.3 的「数据分析 Server」内置替代：
本系统未接平台开放接口，因此提供**如实标注的内部经验基准**与可测量的内容因子，
用于给出预估区间锚点与归因候选——不冒充平台真实数据。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..knowledge.industry import channel_rule, title_limit
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

_WHITESPACE_RE = re.compile(r"\s")

#: 内部经验基准（非平台真实数据）：CTR / 互动率 的区间锚点，按渠道区分。
#: 单位：百分比。来源为本团队的通用经验值，仅用于给出「区间预估」的锚点。
CHANNEL_BENCHMARKS: dict[str, dict[str, list[float]]] = {
    "小红书": {"ctr": [3.0, 8.0], "engagement": [4.0, 10.0]},
    "抖音": {"ctr": [2.0, 6.0], "engagement": [3.0, 8.0]},
    "公众号": {"ctr": [1.5, 5.0], "engagement": [2.0, 6.0]},
    "知乎": {"ctr": [2.0, 6.0], "engagement": [2.0, 7.0]},
    "电商详情页": {"ctr": [2.5, 7.0], "engagement": [1.0, 4.0]},
    "官网": {"ctr": [1.0, 4.0], "engagement": [1.0, 3.0]},
    "PR稿": {"ctr": [0.5, 2.5], "engagement": [0.5, 2.0]},
}

#: 传播目标 → 建议观测指标
OBJECTIVE_METRICS: dict[str, list[str]] = {
    "曝光": ["曝光量", "CPM", "完播率/阅读完成率"],
    "互动": ["互动率", "评论数", "收藏/转发比"],
    "转化": ["点击率", "转化率", "转化成本"],
    "教育": ["收藏率", "完读率", "搜索量变化"],
    "信任": ["评论正面占比", "涨粉数", "复访率"],
}

_DEFAULT_BENCHMARK = {"ctr": [1.5, 6.0], "engagement": [2.0, 7.0]}


def _benchmark_for(channel: str) -> dict[str, list[float]]:
    if channel in CHANNEL_BENCHMARKS:
        return CHANNEL_BENCHMARKS[channel]
    for key, value in CHANNEL_BENCHMARKS.items():
        if key in channel or channel in key:
            return value
    return _DEFAULT_BENCHMARK


def channel_benchmarks(ctx: "AgentRunContext") -> ToolOutcome:
    """内部经验基准区间（如实标注：非平台数据，仅作区间锚点）。"""
    brief = ctx.brief
    bench = _benchmark_for(brief.channel)
    lines = [
        f"「{brief.channel}」经验锚点：CTR {bench['ctr'][0]}%-{bench['ctr'][1]}%，"
        f"互动率 {bench['engagement'][0]}%-{bench['engagement'][1]}%",
        "（来源：内部经验基准——**非平台真实数据**，仅用于锚定预估区间，"
        "预估必须保持区间形式并注明不构成效果承诺）",
    ]
    return ToolOutcome(
        summary="载入渠道经验基准（CTR/互动率区间锚点）",
        detail="\n".join(lines),
        data={"channel": brief.channel, "benchmarks": bench},
    )


def content_factors(ctx: "AgentRunContext") -> ToolOutcome:
    """可测量的内容因子：标题长度、正文字数、标签数、CTA、选题关键词。

    归因必须建立在这些可测量因子上，而不是编造「平台内幕」。
    """
    from ..llm.json_utils import as_obj, as_obj_array, as_str, as_str_array

    brief = ctx.brief
    draft = ctx.upstream_of("draft")
    if not draft:
        return ToolOutcome(summary="复盘模式：以回填数据为准，跳过内容因子提取", data={})
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next(
        (v for v in as_obj_array(draft.get("versions")) if as_str(v.get("id")) == recommended),
        {},
    )
    title = as_str(target.get("title"))
    body = as_str(target.get("body"))
    hashtags = as_str_array(target.get("hashtags"))
    factors = {
        "title_length": len(title),
        "title_limit": title_limit(brief.channel),
        "body_chars": len(_WHITESPACE_RE.sub("", body)),
        "channel_length_hint": channel_rule(brief.channel).length_hint,
        "hashtag_count": len(hashtags),
        "has_cta": bool(as_str(target.get("cta"))),
        "version_count": len(as_obj_array(draft.get("versions"))),
        "topic": as_str(ctx.upstream_of("plan").get("selected_topic")),
        "keywords": as_str_array(as_obj(ctx.upstream_of("plan").get("keywords")).get("primary")),
    }
    lines = [
        f"标题 {factors['title_length']}/{factors['title_limit']} 字，正文 "
        f"{factors['body_chars']} 字（基准 {factors['channel_length_hint']}），"
        f"标签 {factors['hashtag_count']} 个，CTA {'有' if factors['has_cta'] else '无'}",
        "（来源：草稿与计划产物的可测量特征——归因请围绕这些因子展开）",
    ]
    return ToolOutcome(
        summary="提取 9 项可测量内容因子",
        detail="\n".join(lines),
        data=factors,
    )


def objective_metrics(ctx: "AgentRunContext") -> ToolOutcome:
    """传播目标 → 建议观测指标映射（objective_alignment 的口径参考）。"""
    brief = ctx.brief
    metrics: list[str] = []
    for objective_key, values in OBJECTIVE_METRICS.items():
        if objective_key in brief.objective:
            metrics = values
            break
    if not metrics:
        metrics = OBJECTIVE_METRICS["互动"]
    lines = [
        f"传播目标「{brief.objective}」建议观测：{'、'.join(metrics)}",
        "（来源：内置指标映射——策略 objectives 中的自定义口径优先）",
    ]
    return ToolOutcome(
        summary=f"建议观测指标：{'、'.join(metrics)}",
        detail="\n".join(lines),
        data={"objective": brief.objective, "metrics": metrics},
    )


TOOLS: list[Tool] = [
    Tool(
        name="channel_benchmarks",
        description="渠道经验基准区间（如实标注非平台数据）",
        agent_ids=("A10",),
        handler=channel_benchmarks,
    ),
    Tool(
        name="content_factors",
        description="提取草稿的可测量内容因子（归因候选）",
        agent_ids=("A10",),
        handler=content_factors,
    ),
    Tool(
        name="objective_metrics",
        description="传播目标到观测指标的映射",
        agent_ids=("A10",),
        handler=objective_metrics,
    ),
]

__all__ = [
    "TOOLS",
    "channel_benchmarks",
    "content_factors",
    "objective_metrics",
    "CHANNEL_BENCHMARKS",
]
