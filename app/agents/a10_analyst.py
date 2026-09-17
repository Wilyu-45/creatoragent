"""A10 数据分析与复盘智能体（移植自 server/agents/a10-analyst.ts）。"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult
from ..tools import run_agent_tools
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
- 只输出 JSON，不输出任何解释性文字

工具用法：
- ab_significance 给出的是**判定口径**（每臂样本量、显著线、多重比较校正），
  不是现成的结论：用它把 ab_tests / ab_conclusion 写成「达到 N 个样本才可判定」，
  不得在样本不足时宣布胜负
- channel_benchmarks 是经验基准区间，引用时必须在 basis 标注
  「非平台真实数据、仅供区间参考」；若你另用了外部数据，必须写明来源
- web_search 返回带链接的外部数据时，可在 basis 写明链接与口径；
  若工具说明「本轮未启用联网」，则一律不得引用外部实时数据
- funnel_sensitivity 给出「曝光 → 点击/互动」的确定性换算表：predicted 各层
  必须与表中量级自洽（同一曝光基数下点击率、互动率不得互相矛盾），
  且 basis 要写明「基于内部经验基准的推导」
- actuals_audit 在复盘模式下直接给出实测派生指标（CTR/互动率/转化率）与
  相对预估中值的偏离：actuals 与 predicted_comparison 的数值**必须照抄工具结果**，
  不得自行相除或另算一套；工具报「未填写 / 无法对照」的项要如实写进 cautions""",
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


REVIEW_SCHEMA = """{
  "predicted_comparison": {"metric": "ctr", "predicted_mid": 0, "actual": 0, "delta": 0, "verdict": "超预期|符合预期|低于预期", "note": ""},
  "actuals": {"exposure": 0, "clicks": 0, "interactions": 0, "conversions": 0, "ctr": 0, "engagement": 0, "conversion": 0},
  "attribution": [{"factor": "", "impact": "high|medium|low", "note": ""}],
  "ab_conclusion": {"hypothesis": "", "winner": "A|B", "confidence": 0.0, "note": "", "ready_to_scale": false},
  "optimizations": [{"priority": "high|medium|low", "action": "", "expected_gain": "", "effort": ""}],
  "next_brief_suggestions": [""],
  "cautions": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize_review(data: dict[str, Any], actuals: dict[str, Any]) -> dict[str, Any]:
    """复盘结果归一化：``mode`` 固定为 ``post_publish_review``。

    前端据此区分同为 ``effect_report`` 的两种产物——发布前预估与发布后复盘。
    """
    cautions = as_str_array(data.get("cautions"))
    return {
        "mode": "post_publish_review",
        "window": as_str(actuals.get("window"), "发布后 72 小时"),
        "channel": as_str(actuals.get("channel")),
        "actuals": as_obj(data.get("actuals")) or dict(actuals),
        "predicted_comparison": as_obj(data.get("predicted_comparison")),
        "attribution": as_obj_array(data.get("attribution")),
        "ab_conclusion": as_obj(data.get("ab_conclusion")),
        "optimizations": as_obj_array(data.get("optimizations")),
        "next_brief_suggestions": as_str_array(data.get("next_brief_suggestions")),
        "cautions": cautions or ["复盘结论基于已回填的投放窗口数据，样本有限，不代表长期规律"],
    }


def _run_estimate(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    draft = ctx.upstream_of("draft")
    plan = ctx.upstream_of("plan")
    strategy = ctx.upstream_of("strategy")

    ctx.emit("基于内容版本与渠道规则做效果预估（发布前）")
    tools = run_agent_tools(ctx, META)

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

{tools.prompt_block()}请给出效果预估区间、归因、优化建议与 A/B 方案，严格要求 JSON 结构如下：
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
            "tools": tools.context(),
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


def _run_review(ctx: AgentRunContext, actuals: dict[str, Any]) -> AgentResult:
    """发布后复盘：拿真实数据与发布前预估对照，输出归因与 A/B 结论。"""
    brief = ctx.brief
    predicted = ctx.upstream_of("predicted")
    window = as_str(actuals.get("window"), "发布后 72 小时")

    ctx.emit("收到运营回填的真实效果数据，开始发布后复盘")
    tools = run_agent_tools(ctx, META)

    user = f"""【复盘对象】{brief.channel}｜观察窗口：{window}
品牌：{brief.brand}｜目标：{brief.objective}｜受众：{brief.audience}

【真实数据（运营回填）】
曝光：{as_str(actuals.get('exposure'))}｜点击：{as_str(actuals.get('clicks'))}
互动：{as_str(actuals.get('interactions'))}｜转化：{as_str(actuals.get('conversions'))}

【发布前预估（用于对照，可能缺失）】
{content_to_text(predicted) if predicted else '（无预估基线，请只做绝对表现解读，不要编造对照数据）'}

{tools.prompt_block()}请输出复盘报告，严格要求 JSON 结构如下：
{REVIEW_SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A10.review",
        {
            "brief": brief.model_dump(mode="json"),
            "actuals": actuals,
            "predicted": predicted,
            "tools": tools.context(),
        },
    )

    content = normalize_review(result.data, actuals)
    comparison = as_obj(content["predicted_comparison"])
    optimizations = as_obj_array(content["optimizations"])
    verdict = as_str(comparison.get("verdict"), "已完成复盘")

    ctx.emit("发布后复盘完成", {"verdict": verdict, "delta": comparison.get("delta")})

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="effect_report",
            title="发布后效果复盘报告",
            content=content,
            text=content_to_text(content),
            tags=["数据分析", "复盘", brief.channel],
        ),
    )

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"完成发布后复盘：{verdict}"
                f"（实际 CTR {as_str(as_obj(content['actuals']).get('ctr'))}%），"
                f"输出 {len(optimizations)} 条优化建议与下一轮 Brief 建议"
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.78),
            risks=read_risks(result.data),
            evidence=read_evidence(result.data),
            handoff={"to": None, "reason": "复盘完成，结论可用于下一轮 Brief"},
        ),
    )


def run(ctx: AgentRunContext) -> AgentResult:
    """A10 有两种模式，由编排层注入的上游数据决定：

    * 上游带 ``actuals``（运营回填的真实数据）→ 发布后复盘；
    * 否则 → 发布前效果预估。
    """
    actuals = ctx.upstream.get("actuals")
    if isinstance(actuals, dict) and actuals:
        return _run_review(ctx, actuals)
    return _run_estimate(ctx)


a10_analyst = AgentDefinition(meta=META, run=run)
