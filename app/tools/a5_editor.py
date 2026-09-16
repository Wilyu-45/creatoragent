"""A5 编辑审校智能体的工具集。

文本度量类工具：草稿硬指标（字数/标题长度/标签数）、渠道结构对照、品牌语气检查。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..knowledge.compliance import check_brand_voice
from ..knowledge.industry import channel_rule, title_limit
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

_WHITESPACE_RE = re.compile(r"\s")


def _recommended_draft(ctx: "AgentRunContext") -> tuple[dict, list[dict]]:
    from ..llm.json_utils import as_obj, as_obj_array, as_str

    draft = ctx.upstream_of("draft")
    recommended = as_str(draft.get("recommended_version"), "V1")
    versions = as_obj_array(draft.get("versions"))
    target = next((v for v in versions if as_str(v.get("id")) == recommended), {})
    return target, versions


def draft_metrics(ctx: "AgentRunContext") -> ToolOutcome:
    """草稿硬指标：标题字数 vs 平台上限、正文字数、标签数量。"""
    from ..llm.json_utils import as_obj_array, as_str, as_str_array

    brief = ctx.brief
    target, versions = _recommended_draft(ctx)
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


def brand_voice_check(ctx: "AgentRunContext") -> ToolOutcome:
    """品牌语气检查：书面腔 / 网络用语过度 / 标题党倾向。"""
    from ..llm.json_utils import as_str

    target, _ = _recommended_draft(ctx)
    body = as_str(target.get("body"))
    title = as_str(target.get("title"))
    if not body:
        return ToolOutcome(summary="上游暂无文案草稿，跳过语气检查", data={})
    report = check_brand_voice(f"{title}\n{body}", ctx.brief.tone)
    lines = [f"语气得分：{report.score}/100（基准调性：{ctx.brief.tone}）"]
    for issue in report.issues[:5]:
        lines.append(f"- [{issue.type}] {issue.detail}；建议：{issue.suggestion}")
    if not report.issues:
        lines.append("未检出语气偏移")
    lines.append("（来源：内置品牌语气规则——只覆盖常见偏移模式，不替代整体判断）")
    return ToolOutcome(
        summary=f"语气检查 {report.score}/100，{len(report.issues)} 处偏移",
        detail="\n".join(lines),
        data={"score": report.score, "issues": [issue.__dict__ for issue in report.issues]},
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
        name="brand_voice_check",
        description="品牌语气检查：书面腔/网络用语/标题党",
        agent_ids=("A5",),
        handler=brand_voice_check,
    ),
]

__all__ = ["TOOLS", "draft_metrics", "structure_checklist", "brand_voice_check"]
