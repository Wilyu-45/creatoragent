"""A7 品牌与合规智能体的工具集。

对应《plan.md》2.2.3 的「敏感词 Server」内置替代：
广告法词库扫描、自动改写预览、词库覆盖面如实告知。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..knowledge.compliance import auto_rewrite, check_brand_voice, scan_compliance
from ..knowledge.language import compliance_coverage
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

_SEVERITY_LABEL = {"blocker": "阻断", "major": "重要", "minor": "建议"}


def _recommended_target(ctx: "AgentRunContext") -> dict[str, Any]:
    from ..llm.json_utils import as_obj, as_obj_array, as_str

    draft = ctx.upstream_of("draft")
    recommended = as_str(draft.get("recommended_version"), "V1")
    versions = as_obj_array(draft.get("versions"))
    return next((v for v in versions if as_str(v.get("id")) == recommended), {})


def _scan_text(ctx: "AgentRunContext") -> str:
    from ..llm.json_utils import as_str, as_str_array

    target = _recommended_target(ctx)
    return " ".join(
        part
        for part in (
            as_str(target.get("title")),
            as_str(target.get("body")),
            " ".join(as_str_array(target.get("hashtags"))),
        )
        if part
    )


def lexicon_scan(ctx: "AgentRunContext") -> ToolOutcome:
    """广告法词库 + 行业专项词扫描（词表匹配，可解释、可复现）。"""
    brief = ctx.brief
    text = _scan_text(ctx)
    if not text.strip():
        return ToolOutcome(summary="上游暂无文案草稿，跳过词库扫描", data={})

    result = scan_compliance(text, brief.industry)
    lines: list[str] = []
    for hit in result.hits[:8]:
        lines.append(
            f"- [{_SEVERITY_LABEL.get(hit.severity, hit.severity)}] 「{hit.term}」"
            f"（{hit.category}）…{hit.snippet}…｜依据：{hit.law.split('：')[0]}"
        )
    for failure in result.required_failures[:3]:
        lines.append(f"- [缺失] {failure.hint}｜依据：{failure.law.split('：')[0]}")
    if not lines:
        lines.append("词库扫描未命中")
    lines.append("（来源：内置广告法词库——规则匹配结果，请逐条核实后写入 hits）")
    return ToolOutcome(
        summary=(
            f"词库扫描：阻断 {sum(1 for h in result.hits if h.severity == 'blocker')}、"
            f"重要 {sum(1 for h in result.hits if h.severity == 'major')}、"
            f"缺失 {len(result.required_failures)}，风险 {result.risk_level}"
        ),
        detail="\n".join(lines),
        data={
            "risk_level": result.risk_level,
            "hits": [
                {
                    "term": hit.term,
                    "category": hit.category,
                    "severity": hit.severity,
                    "law": hit.law,
                    "snippet": hit.snippet,
                    "fix": hit.fix,
                }
                for hit in result.hits
            ],
            "required_failures": [
                {
                    "category": failure.category,
                    "hint": failure.hint,
                    "severity": failure.severity,
                    "law": failure.law,
                    "fix": failure.fix,
                }
                for failure in result.required_failures
            ],
        },
    )


def safe_rewrite_preview(ctx: "AgentRunContext") -> ToolOutcome:
    """对命中项生成合规替换预览，供 safe_rewrites 与强制修改项直接引用。"""
    brief = ctx.brief
    text = _scan_text(ctx)
    if not text.strip():
        return ToolOutcome(summary="上游暂无文案草稿，跳过改写预览", data={})
    result = scan_compliance(text, brief.industry)
    rewrite = auto_rewrite(text, result.hits)
    if not rewrite.applied:
        return ToolOutcome(
            summary="命中项暂无自动替换建议（需人工给出改写方案）",
            data={"applied": []},
        )
    lines = [f"「{item['from']}」→「{item['to']}」" for item in rewrite.applied[:10]]
    lines.append("（来源：内置安全替换表——供参考，须结合语境确认语义不变）")
    return ToolOutcome(
        summary=f"{len(rewrite.applied)} 条合规替换建议",
        detail="\n".join(lines),
        data={"applied": rewrite.applied},
    )


def coverage_check(ctx: "AgentRunContext") -> ToolOutcome:
    """当前语言的合规词库覆盖面（非中文市场必须如实告知未覆盖）。"""
    coverage = compliance_coverage(ctx.brief.language)
    if coverage["lexicon_coverage"]:
        return ToolOutcome(
            summary=f"{coverage['label']}有自动合规词库覆盖",
            data={"covered": True},
        )
    lines = [
        f"{coverage['label']}尚无自动合规词库，本地扫描结果不能代表当地法规校验",
    ]
    lines.extend(f"- 当地红线：{note}" for note in list(coverage["notes"])[:3])
    if coverage["ad_disclosure_required"]:
        lines.append(f"- 商业推广须显式标注 {coverage['ad_disclosure_text']}")
    return ToolOutcome(
        summary=f"{coverage['label']}无自动词库，需人工复核",
        detail="\n".join(lines),
        data={"covered": False, "coverage": {k: v for k, v in coverage.items() if k != "notes"}},
    )


def brand_voice_scan(ctx: "AgentRunContext") -> ToolOutcome:
    """品牌语气扫描：书面腔 / 网络用语 / 标题党（供品牌一致性维度参考）。"""
    from ..llm.json_utils import as_str

    target = _recommended_target(ctx)
    text = f"{as_str(target.get('title'))}\n{as_str(target.get('body'))}"
    if not text.strip():
        return ToolOutcome(summary="上游暂无文案草稿，跳过语气扫描", data={})
    report = check_brand_voice(text, ctx.brief.tone)
    lines = [f"语气得分：{report.score}/100（基准调性：{ctx.brief.tone}）"]
    lines.extend(f"- [{issue.type}] {issue.detail}" for issue in report.issues[:5])
    if not report.issues:
        lines.append("未检出语气偏移")
    lines.append("（来源：内置品牌语气规则）")
    return ToolOutcome(
        summary=f"语气扫描 {report.score}/100，{len(report.issues)} 处偏移",
        detail="\n".join(lines),
        data={"score": report.score, "issues": [issue.__dict__ for issue in report.issues]},
    )


TOOLS: list[Tool] = [
    Tool(
        name="lexicon_scan",
        description="广告法词库+行业专项词扫描（含法规依据与替换建议）",
        agent_ids=("A7",),
        handler=lexicon_scan,
    ),
    Tool(
        name="safe_rewrite_preview",
        description="命中项的合规替换建议预览",
        agent_ids=("A7",),
        handler=safe_rewrite_preview,
    ),
    Tool(
        name="coverage_check",
        description="当前语言的合规词库覆盖面核查",
        agent_ids=("A7",),
        handler=coverage_check,
    ),
    Tool(
        name="brand_voice_scan",
        description="品牌语气扫描（书面腔/网络用语/标题党）",
        agent_ids=("A7",),
        handler=brand_voice_scan,
    ),
]

__all__ = [
    "TOOLS",
    "lexicon_scan",
    "safe_rewrite_preview",
    "coverage_check",
    "brand_voice_scan",
]
