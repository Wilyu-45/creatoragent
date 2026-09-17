"""A5 编辑审校智能体的工具集。

文本度量类工具：草稿硬指标（字数/标题长度/标签数）、渠道结构对照、
**可读性度量**（句长分布/段落长度/标点密度/重复表达）。

职责边界（与 A7 的分工）：A5 只度量**结构与可读性**这类可计算指标，
不判品牌一致性与术语一致性——那是 A7 的判定权（见 ``a7_compliance.brand_voice_scan``）。
原先 A5 的 ``brand_voice_check`` 与 A7 的 ``brand_voice_scan`` 是同一份实现，
已删除前者，避免同一个结论由两个智能体各说一遍。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..knowledge.industry import channel_rule, title_limit
from .base import Tool, ToolOutcome, recommended_draft

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

_WHITESPACE_RE = re.compile(r"\s")

#: 句子切分：中文句末标点 + 换行（换行在小红书这类渠道里就是断句）
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")

#: 标点集合（用于标点密度：密度过低说明长句连缀，过高说明碎句过多）
_PUNCT_CHARS = frozenset("，。！？、；：（）《》〈〉【】…—,.!?;:()[]{}~～-\"'“”‘’")

#: 高频 2-gram 里需要排除的「无意义重复」（虚词组合）
_STOP_2GRAMS = frozenset(
    {"的话", "我们", "这个", "那个", "可以", "就是", "还是", "因为", "所以", "而且", "但是", "如果"}
)

#: 长句阈值（字）：超过即建议拆句
_LONG_SENTENCE_CHARS = 35

#: 易读阈值（平均句长，字）
_EASY_AVG_CHARS = 18


def draft_metrics(ctx: "AgentRunContext") -> ToolOutcome:
    """草稿硬指标：标题字数 vs 平台上限、正文字数、标签数量。"""
    from ..llm.json_utils import as_str, as_str_array

    brief = ctx.brief
    target, versions = recommended_draft(ctx)
    if not versions:
        return ToolOutcome(summary="上游暂无文案草稿，跳过度量", data={})
    limit = title_limit(brief.channel)
    lines: list[str] = []
    metrics: list[dict[str, Any]] = []
    for version in versions:
        title = as_str(version.get("title"))
        body = as_str(version.get("body"))
        hashtags = as_str_array(version.get("hashtags"))
        word_count = len(_WHITESPACE_RE.sub("", f"{title}{body}"))
        metrics.append(
            {
                "id": as_str(version.get("id")),
                "title_length": len(title),
                "body_chars": len(_WHITESPACE_RE.sub("", body)),
                "word_count": word_count,
                "hashtag_count": len(hashtags),
                "title_ok": len(title) <= limit,
            }
        )
        mark = "（主推）" if version is target else ""
        lines.append(
            f"{as_str(version.get('id'))}{mark}：标题 {len(title)}/{limit} 字"
            f"{'⚠️超限' if len(title) > limit else '✓'}，"
            f"去空白 {word_count} 字，标签 {len(hashtags)} 个"
        )
    lines.append(f"（渠道长度基准：{channel_rule(brief.channel).length_hint}）")
    return ToolOutcome(
        summary=f"度量 {len(versions)} 个版本的硬指标",
        detail="\n".join(lines),
        data={"versions": metrics, "title_limit": limit},
    )


def structure_checklist(ctx: "AgentRunContext") -> ToolOutcome:
    """渠道必备结构清单，供审校时逐段对照。"""
    from ..knowledge.industry import channel_rule

    rule = channel_rule(ctx.brief.channel)
    lines = [
        f"「{ctx.brief.channel}」必备结构：{' → '.join(rule.blocks)}",
        "（来源：内置渠道规范库——审校时核对草稿是否覆盖各段功能）",
    ]
    return ToolOutcome(
        summary=f"{len(rule.blocks)} 段必备结构",
        detail="\n".join(lines),
        data={"blocks": rule.blocks},
    )


def readability_metrics(ctx: "AgentRunContext") -> ToolOutcome:
    """可读性度量：句长分布 / 段落长度 / 标点密度 / 高频重复 2-gram。

    全部是**可计算的文本特征**，不掺入主观「好读不好读」的判断：
    编辑可以据此把「表达节奏需要优化」这类模糊意见，换成「第 3 句 48 字，拆成两句」。
    """
    from ..knowledge.embedding import tokenize
    from ..llm.json_utils import as_str

    target, _ = recommended_draft(ctx)
    body = as_str(target.get("body"))
    if not _WHITESPACE_RE.sub("", body):
        return ToolOutcome(summary="上游暂无文案草稿，跳过可读性度量", data={})

    sentences = [
        _WHITESPACE_RE.sub("", part)
        for part in _SENTENCE_SPLIT_RE.split(body)
        if _WHITESPACE_RE.sub("", part)
    ]
    if not sentences:
        return ToolOutcome(summary="正文无法切分出句子，跳过可读性度量", data={})

    lengths = [len(sentence) for sentence in sentences]
    paragraphs = [
        _WHITESPACE_RE.sub("", part) for part in body.split("\n") if _WHITESPACE_RE.sub("", part)
    ]
    plain = _WHITESPACE_RE.sub("", body)
    punct_count = sum(1 for char in plain if char in _PUNCT_CHARS)
    long_sentences = [s for s in sentences if len(s) > _LONG_SENTENCE_CHARS]
    short_count = sum(1 for length in lengths if length <= 15)

    # 高频 2-gram：同一词组反复出现是「表达重复」最可测量的信号
    counter: dict[str, int] = {}
    for token in tokenize(body):
        if len(token) == 2 and token not in _STOP_2GRAMS:
            counter[token] = counter.get(token, 0) + 1
    repeated = sorted(
        ((token, count) for token, count in counter.items() if count >= 3),
        key=lambda item: item[1],
        reverse=True,
    )[:4]

    avg = round(sum(lengths) / len(lengths), 1)
    if avg <= _EASY_AVG_CHARS and len(long_sentences) / len(sentences) <= 0.15:
        level = "易读"
    elif avg <= 28:
        level = "偏长，建议压缩"
    else:
        level = "读起来吃力，必须拆句"

    lines = [
        f"句数 {len(sentences)}｜平均句长 {avg} 字｜最长句 {max(lengths)} 字｜可读性判定：{level}",
        f"句长分布：短句（≤15 字）{short_count} 句、"
        f"长句（>{_LONG_SENTENCE_CHARS} 字）{len(long_sentences)} 句"
        f"（{len(long_sentences) / len(sentences):.0%}）",
        f"段落 {len(paragraphs)} 段、最长段 {max((len(p) for p in paragraphs), default=0)} 字｜"
        f"标点密度 {punct_count / max(1, len(plain)) * 100:.1f} 个/100 字",
    ]
    for sentence in long_sentences[:3]:
        lines.append(f"- 待拆长句（{len(sentence)} 字）：{sentence[:32]}…")
    if repeated:
        lines.append("高频重复 2-gram：" + "、".join(f"{token}×{count}" for token, count in repeated))
    lines.append(
        "（来源：正文文本特征统计——阈值（平均 18 字 / 长句 35 字）为通用经验值，"
        "不是平台算法结论；issues 里引用这些数字，比「表达不够流畅」更可执行）"
    )
    return ToolOutcome(
        summary=f"平均句长 {avg} 字（{level}），长句 {len(long_sentences)} 句，重复词 {len(repeated)} 处",
        detail="\n".join(lines),
        data={
            "sentence_count": len(sentences),
            "avg_sentence_chars": avg,
            "max_sentence_chars": max(lengths),
            "long_sentence_count": len(long_sentences),
            "paragraph_count": len(paragraphs),
            "punct_per_100": round(punct_count / max(1, len(plain)) * 100, 1),
            "repeated_2grams": [{"token": token, "count": count} for token, count in repeated],
            "readability_level": level,
        },
    )


TOOLS: list[Tool] = [
    Tool(
        name="draft_metrics",
        description="草稿硬指标：标题字数/正文字数/标签数 vs 平台规则",
        agent_ids=("A5",),
        handler=draft_metrics,
    ),
    Tool(
        name="structure_checklist",
        description="渠道必备结构清单，供逐段对照",
        agent_ids=("A5",),
        handler=structure_checklist,
    ),
    Tool(
        name="readability_metrics",
        description="可读性度量：句长分布/段落长度/标点密度/重复表达",
        agent_ids=("A5",),
        handler=readability_metrics,
    ),
]

__all__ = ["TOOLS", "draft_metrics", "structure_checklist", "readability_metrics"]
