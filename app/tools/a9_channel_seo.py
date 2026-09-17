"""A9 渠道运营与 SEO 智能体的工具集。

对应《plan.md》2.2.3 的「SEO Server」内置替代：
搜索意图蓝图、全平台标题截断规则、发布时段、渠道合规清单种子，
以及**标签质量审计**（tag_quality_audit）。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..knowledge.compliance import LEXICON_GROUPS
from ..knowledge.industry import (
    CHANNEL_RULES,
    CHANNEL_TITLE_LIMIT,
    channel_rule,
    publish_slots,
    seo_pattern,
    title_limit,
)
from .base import Tool, ToolOutcome, recommended_draft

#: 标签数量区间（从渠道规则的 hashtag_policy 文本里解析，如「5-10 个」）
_TAG_RANGE_RE = re.compile(r"(\d+)\s*[-~—至到]\s*(\d+)")

#: 宽泛无指向标签：占用配额但不带来垂类流量
_BROAD_TAGS = frozenset(
    {"好物分享", "日常", "生活", "推荐", "分享", "种草", "热门", "vlog", "fyp", "随手拍", "记录"}
)

#: 标签长度上限（字）：过长标签在移动端不完整展示，也不再被当作搜索词
_TAG_MAX_CHARS = 12

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


def tag_quality_audit(ctx: "AgentRunContext") -> ToolOutcome:
    """标签质量审计：数量区间 / 重复 / 宽泛度 / 长度 / 红线词 / 关键词覆盖。

    渠道规则里的 ``hashtag_policy``（如「5-10 个，2 个大词 + 3 个垂类词 + 2 个长尾词」）
    原先只是提示词里的一句话，没人核对；这里把它变成可判定的审计项。
    """
    from ..llm.json_utils import as_str_array

    brief = ctx.brief
    target, _ = recommended_draft(ctx)
    hashtags = as_str_array(target.get("hashtags"))
    if not hashtags:
        return ToolOutcome(summary="主推版本暂无话题标签，跳过标签审计", data={"tags": []})

    policy = channel_rule(brief.channel).hashtag_policy
    matched = _TAG_RANGE_RE.search(policy)
    lower, upper = (int(matched.group(1)), int(matched.group(2))) if matched else (0, 0)

    redlines = {
        term
        for group in LEXICON_GROUPS
        if group.severity == "blocker"
        for term in group.terms
    }

    normalized = [tag.lstrip("#＃").strip() for tag in hashtags]
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for tag, bare in zip(hashtags, normalized):
        flags: list[str] = []
        if not tag.startswith(("#", "＃")):
            flags.append("缺 # 前缀")
        if bare in seen:
            flags.append("重复标签")
        seen.add(bare)
        if bare in _BROAD_TAGS:
            flags.append("宽泛无指向")
        if len(bare) > _TAG_MAX_CHARS:
            flags.append(f"过长（>{_TAG_MAX_CHARS} 字）")
        hits = sorted({term for term in redlines if term in bare})
        if hits:
            flags.append(f"红线词：{'、'.join(hits)}")
        rows.append({"tag": tag, "flags": flags})
        if flags:
            issues.append(f"{tag}：{'；'.join(flags)}")

    if lower and not (lower <= len(hashtags) <= upper):
        issues.append(f"标签数量 {len(hashtags)} 个，超出渠道区间 {lower}-{upper} 个")

    covered = [keyword for keyword in brief.keywords if keyword and any(keyword in tag for tag in normalized)]
    uncovered = [keyword for keyword in brief.keywords if keyword and keyword not in covered]

    lines = [
        f"「{brief.channel}」标签策略：{policy}",
        f"当前 {len(hashtags)} 个标签"
        + (f"（区间 {lower}-{upper}）" if lower else "（渠道未给出明确区间）"),
    ]
    for row in rows:
        lines.append(f"- {row['tag']}" + (f"　⚠️ {'；'.join(row['flags'])}" if row["flags"] else "　✓"))
    if issues:
        lines.append("待处理：" + "；".join(issues[:6]))
    if brief.keywords:
        lines.append(
            f"关键词覆盖：{len(covered)}/{len(brief.keywords)}"
            + (f"（未覆盖：{'、'.join(uncovered)}）" if uncovered else "")
        )
    else:
        lines.append("关键词覆盖：Brief 未提供关键词，无法核对标签是否覆盖搜索词")
    lines.append(
        "（来源：渠道规则文本 + 词库与字符串比对——数量/重复/长度/红线词是可判定的，"
        "「大词/垂类词/长尾词」的结构配比需你结合搜索意图自查，审计结果请写入 channel_checklist）"
    )
    return ToolOutcome(
        summary=f"审计 {len(hashtags)} 个标签，{len(issues)} 项待处理",
        detail="\n".join(lines),
        data={
            "channel": brief.channel,
            "policy": policy,
            "count_range": [lower, upper],
            "count": len(hashtags),
            "tags": rows,
            "issues": issues,
            "keywords_covered": covered,
            "keywords_uncovered": uncovered,
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
    Tool(
        name="tag_quality_audit",
        description="标签质量审计：数量/重复/宽泛度/长度/红线词/关键词覆盖",
        agent_ids=("A9",),
        handler=tag_quality_audit,
    ),
]

__all__ = [
    "TOOLS",
    "seo_blueprint",
    "title_rules",
    "publish_windows",
    "channel_policy",
    "tag_quality_audit",
]
