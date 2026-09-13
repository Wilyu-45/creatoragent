"""A10 数据分析与复盘智能体（移植自 server/agents/a10-analyst.ts）。"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult
from .base import (
    AgentDefinition,
    AgentMeta,
    AgentRunContext,
    ArtifactDraft,
    ResultDraft,
    as_obj,
    as_obj_array,
    as_str,
    as_str_array,
    build_artifact,
    build_result,
    call_with_prompts,
    content_to_text,
    read_confidence,
    read_evidence,
    read_risks,
    system_prompt,
)

META = AgentMeta(
    id="A10",
    name="数据分析与复盘智能体",
    role="数据分析师 / 增长顾问",
    kind="analyst",
    phase="ANALYZED",
    produces="effect_report",
    description="发布前预估效果区间，发布后回收数据做归因分析，输出优化建议与 A/B 方案",
    veto=False,
    capabilities=["效果预估", "归因分析", "优化建议", "A/B 方案"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 发布前给出曝光、点击、互动、转化的区间预估，并说明预估依据
2. 归因内容表现的驱动因素（标题、开头、标签、时段）
3. 输出可执行的优化建议与 A/B 测试方案
4. 给出下一轮 Brief 建议

硬性规则：
- 预估必须是区间，并明确「不构成效果承诺」
- 不得编造具体的历史数据或平台内幕数据
- 优化建议要给出预期收益方向与实施成本
- 只输出 JSON，不输出任何解释性文字""",
)

SCHEMA = """{
  "predicted": {"exposure": {"low": 0, "mid": 0, "high": 0, "unit": "", "basis": ""}, "ctr": {}, "engagement": {}, "conversion": {}},
  "objective_alignment": {"objective": "", "score": 0, "note": ""},
  "attribution": [{"factor": "", "impact": "high|medium|low", "note": ""}],
  "optimizations": [{"priority": "high|medium|low", "action": "", "expected_gain": "", "effort": ""}],
  "ab_tests": [{"hypothesis": "", "variant_a": "", "variant_b": "", "metric": ""}],
  "next_brief_suggestions": [""],
  "cautions": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    cautions = as_str_array(data.get("cautions"))
    return {
        "mode": "pre_publish_estimate",
        "predicted": as_obj(data.get("predicted")),
        "objective_alignment": as_obj(data.get("objective_alignment")),
        "attribution": as_obj_array(data.get("attribution")),
        "optimizations": as_obj_array(data.get("optimizations")),
        "ab_tests": as_obj_array(data.get("ab_tests")),
        "next_brief_suggestions": as_str_array(data.get("next_brief_suggestions")),
        "cautions": cautions or ["以上为区间预估，不构成效果承诺"],
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    draft = ctx.upstream_of("draft")
    plan = ctx.upstream_of("plan")
    strategy = ctx.upstream_of("strategy")

    ctx.emit("基于内容版本与渠道规则做效果预估（发布前）")

    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next(
        (v for v in as_obj_array(draft.get("versions")) if as_str(v.get("id")) == recommended),
        {},
    )

    objectives_text = "\n".join(
        f"{as_str(o.get('type'))}：{as_str(o.get('metric'))} → {as_str(o.get('target'))}"
        for o in as_obj_array(strategy.get("objectives"))
    )

    user = f"""【预估对象】{brief.channel} 主推版本
标题：{as_str(target.get('title'))}
目标：{brief.objective}｜受众：{brief.audience}
选题：{as_str(plan.get('selected_topic'))}｜标签：{as_str(as_obj(plan.get('keywords')).get('hashtags'))}

【策略目标】
{objectives_text or '（未指定）'}

请给出效果预估区间、归因、优化建议与 A/B 方案，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A10.analyze",
        {
            "brief": brief.model_dump(mode="json"),
            "strategy": strategy,
            "plan": plan,
            "draft": draft,
            "revision": ctx.revision,
        },
    )

    content = normalize(result.data)
    predicted = as_obj(content["predicted"])
    ctr = as_obj(predicted.get("ctr"))
    optimizations = as_obj_array(content["optimizations"])

    ctx.emit("效果预估完成", {"ctr_mid": ctr.get("mid"), "optimizations": len(optimizations)})

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="effect_report",
            title="效果预估与优化报告",
            content=content,
            text=content_to_text(content),
            tags=["数据分析", brief.channel],
        ),
    )

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"完成发布前效果预估与 {len(optimizations)} 条优化建议，"
                f"预估 CTR 区间 {as_str(ctr.get('low'))}%~{as_str(ctr.get('high'))}%（非承诺值）"
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.66),
            risks=read_risks(result.data),
            evidence=read_evidence(result.data),
            handoff={"to": None, "reason": "分析完成，等待人工审批"},
        ),
    )


a10_analyst = AgentDefinition(meta=META, run=run)
