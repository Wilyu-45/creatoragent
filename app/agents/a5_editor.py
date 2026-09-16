"""A5 编辑审校智能体（移植自 server/agents/a5-editor.ts）。"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult, ReviewItem
from ..core.util import js_round
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
    normalize_score,
    read_confidence,
    read_evidence,
    read_gate,
    read_reviews,
    read_risks,
    system_prompt,
)

META = AgentMeta(
    id="A5",
    name="编辑审校智能体",
    role="主编 / 校对 / 质量门禁",
    kind="reviewer",
    phase="EDITING",
    produces="edited_copy",
    description="检查逻辑、结构、语病与品牌语气，输出修订稿、修改说明与质量评分",
    veto=False,
    capabilities=["结构评审", "语言润色", "质量评分", "退回意见"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 检查逻辑结构、语病、错别字、标点、重复表达
2. 优化表达节奏与可读性，统一品牌语气与术语
3. 给出四维质量评分（结构 / 表达 / 品牌语气 / 吸引力，0-100）
4. 输出修订稿与逐条修改说明；存在重要问题时有权退回 A4 重写

硬性规则：
- 修订不得改变事实主张、不得新增未经验证的数据
- 每个问题必须给出可执行的修改建议，不要只说「建议优化」
- 只做编辑职责内的事，不做合规与事实判定
- 只输出 JSON，不输出任何解释性文字

输出体量（控制 token，超量从简）：
- change_log ≤8 条，只记实质性修改，标点级修正合并为一条
- issues ≤6 条，按严重度排序；revised 只含修改后的成稿""",
)

SCHEMA = """{
  "revised": {"title": "", "body": "", "cta": "", "hashtags": [""]},
  "change_log": [{"type": "", "detail": "", "before": "", "after": ""}],
  "scorecard": {"structure": 0, "clarity": 0, "brand_voice": 0, "appeal": 0, "overall": 0},
  "issues": [{"severity": "blocker|major|minor", "category": "", "detail": "", "suggestion": "", "location": ""}],
  "verdict": "pass|revise",
  "verdict_reason": "",
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    card = as_obj(data.get("scorecard"))
    structure = normalize_score(card.get("structure"), 80)
    clarity = normalize_score(card.get("clarity"), 80)
    brand_voice = normalize_score(card.get("brand_voice"), 80)
    appeal = normalize_score(card.get("appeal"), 80)
    overall = normalize_score(
        card.get("overall"), js_round((structure + clarity + brand_voice + appeal) / 4)
    )
    revised = as_obj(data.get("revised"))
    return {
        "revised": {
            "title": as_str(revised.get("title")),
            "body": as_str(revised.get("body")),
            "cta": as_str(revised.get("cta")),
            "hashtags": as_str_array(revised.get("hashtags")),
        },
        "change_log": as_obj_array(data.get("change_log")),
        "scorecard": {
            "structure": structure,
            "clarity": clarity,
            "brand_voice": brand_voice,
            "appeal": appeal,
            "overall": overall,
        },
        "issues": as_obj_array(data.get("issues")),
        "verdict": as_str(data.get("verdict"), "pass"),
        "verdict_reason": as_str(data.get("verdict_reason")),
    }


def _is_blocking(item: ReviewItem) -> bool:
    return item.severity in ("blocker", "major")


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    draft = ctx.upstream_of("draft")
    ctx.emit("开始审校：结构、表达、品牌语气")
    tools = run_agent_tools(ctx, META)

    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next(
        (v for v in as_obj_array(draft.get("versions")) if as_str(v.get("id")) == recommended),
        {},
    )

    user = f"""【审校对象】{brief.channel} 主推版本「{recommended}」
品牌：{brief.brand}｜调性要求：{brief.tone}｜行业：{brief.industry}

标题：{as_str(target.get('title'))}

正文：
{as_str(target.get('body'))}

CTA：{as_str(target.get('cta'))}
话题标签：{' '.join(as_str_array(target.get('hashtags')))}

{tools.prompt_block()}请完成审校并输出修订稿、修改说明与质量评分，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A5.edit",
        {"brief": brief.model_dump(mode="json"), "draft": draft, "revision": ctx.revision, "tools": tools.context()},
        schema=SCHEMA,
    )

    content = normalize(result.data)
    card = as_obj(content["scorecard"])
    overall = normalize_score(card.get("overall"), 80)
    verdict = read_gate(result.data, "pass" if overall >= 75 else "revise")
    issues = read_reviews(result.data, "issues")
    revised = as_obj(content["revised"])
    rendered_text = (
        f"{as_str(revised.get('title'))}\n\n{as_str(revised.get('body'))}\n\n"
        f"{' '.join(as_str_array(revised.get('hashtags')))}"
    )

    ctx.emit(
        f"审校完成：质量分 {js_round(overall)}，判定 {verdict}",
        {"overall": overall, "blockers": sum(1 for i in issues if _is_blocking(i))},
    )

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="edited_copy",
            title="编辑修订稿",
            content={**content, "target_version": recommended},
            text=rendered_text,
            tags=["审校", f"质量分:{js_round(overall)}"],
        ),
    )

    revision_requests = [
        f"[编辑·{item.category}] {item.detail}"
        + (f"；建议：{item.suggestion}" if item.suggestion else "")
        for item in issues
        if _is_blocking(item)
    ]

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"质量评分 {js_round(overall)}/100，{len(issues)} 条问题，"
                f"判定：{'通过' if verdict == 'pass' else '退回修改'}"
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.85),
            risks=read_risks(result.data),
            evidence=read_evidence(result.data),
            gate_result=verdict,
            revision_requests=[] if verdict == "pass" else revision_requests,
            handoff=(
                {"to": "A6", "reason": "编辑通过，进入事实核查"}
                if verdict == "pass"
                else {"to": "A4", "reason": "质量问题需退回文案重写"}
            ),
        ),
    )


a5_editor = AgentDefinition(meta=META, run=run)
