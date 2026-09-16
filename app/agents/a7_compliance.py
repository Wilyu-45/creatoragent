"""A7 品牌与合规智能体（移植自 server/agents/a7-compliance.ts）。"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult, GateResult
from ..knowledge.language import compliance_coverage
from ..tools import run_agent_tools
from .base import (
    AgentDefinition,
    AgentMeta,
    AgentRunContext,
    ArtifactDraft,
    ResultDraft,
    as_num,
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
    id="A7",
    name="品牌与合规智能体",
    role="品牌守护者 / 法务合规",
    kind="reviewer",
    phase="COMPLIANCE",
    produces="compliance_report",
    description="检查品牌一致性、广告法禁用词、行业特殊限制与版权风险，输出合规等级与强制修改项",
    veto=True,
    capabilities=["敏感词库", "行业法规", "品牌一致性", "否决权"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 检查绝对化用语、虚假宣传、效果承诺（《广告法》第九条/第十七条/第二十五条/第二十八条）
2. 检查行业特殊限制：医疗、金融、教育、食品、化妆品的专门规定
3. 检查品牌调性与术语一致性
4. 输出命中项、法规依据、强制修改要求与合规等级

硬性规则：
- 每条命中必须给出法规依据与可执行的替换方案，不能只说「违规」
- 区分阻断项（blocker）与建议项（minor），不要过度扩大打击面
- 合规不通过时行使否决权，禁止进入发布阶段
- 只输出 JSON，不输出任何解释性文字""",
)

SCHEMA = """{
  "hits": [{"severity": "blocker|major|minor", "category": "", "term": "", "detail": "", "suggestion": "", "law": ""}],
  "summary": {"blocker": 0, "major": 0, "minor": 0},
  "brand_consistency": {"score": 0.0, "issues": [{"type": "", "detail": "", "suggestion": ""}]},
  "risk_level": "low|medium|high",
  "verdict": "pass|revise|reject",
  "verdict_reason": "",
  "required_fixes": [""],
  "safe_rewrites": [{"from": "", "to": ""}],
  "checked_against": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    brand = as_obj(data.get("brand_consistency"))
    summary = as_obj(data.get("summary"))
    return {
        "hits": [
            {
                "severity": as_str(item.get("severity"), "minor"),
                "category": as_str(item.get("category"), "一般"),
                "term": as_str(item.get("term")),
                "detail": as_str(item.get("detail")),
                "suggestion": as_str(item.get("suggestion")),
                "law": as_str(item.get("law"), "—"),
            }
            for item in as_obj_array(data.get("hits"))
        ],
        # 命中项计数必须是整数，避免 JSON 里出现 "1.0 项" 这类脏值
        "summary": {
            "blocker": int(as_num(summary.get("blocker"), 0)),
            "major": int(as_num(summary.get("major"), 0)),
            "minor": int(as_num(summary.get("minor"), 0)),
        },
        "brand_consistency": {
            "score": as_num(brand.get("score"), 0),
            "issues": as_obj_array(brand.get("issues")),
        },
        "risk_level": as_str(data.get("risk_level"), "medium"),
        "verdict": as_str(data.get("verdict"), "revise"),
        "verdict_reason": as_str(data.get("verdict_reason")),
        "required_fixes": as_str_array(data.get("required_fixes")),
        "safe_rewrites": as_obj_array(data.get("safe_rewrites")),
        "checked_against": as_str_array(data.get("checked_against")),
        "compliance_score": as_num(data.get("compliance_score"), 0),
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    draft = ctx.upstream_of("draft")
    ctx.emit("开始品牌一致性与广告法合规扫描")
    tools = run_agent_tools(ctx, META)

    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next(
        (v for v in as_obj_array(draft.get("versions")) if as_str(v.get("id")) == recommended),
        {},
    )

    user = f"""【审查对象】{brief.channel} 主推版本
标题：{as_str(target.get('title'))}

正文：
{as_str(target.get('body'))}

话题标签：{' '.join(as_str_array(target.get('hashtags')))}

【审查基准】
品牌：{brief.brand}｜行业：{brief.industry}｜要求调性：{brief.tone}
品牌约束：{'；'.join(brief.constraints) or '（未指定）'}

{tools.prompt_block()}请输出合规报告，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A7.compliance",
        {"brief": brief.model_dump(mode="json"), "draft": draft, "revision": ctx.revision, "tools": tools.context()},
    )

    content = normalize(result.data)
    summary = as_obj(content["summary"])
    risk_level = as_str(content.get("risk_level"), "medium")
    raw_verdict = as_str(content.get("verdict"))
    if raw_verdict in ("pass", "revise", "reject"):
        verdict: GateResult = raw_verdict  # type: ignore[assignment]
    else:
        verdict = "revise"

    ctx.emit(
        f"合规扫描完成：阻断 {summary['blocker']} 项、重要 {summary['major']} 项，风险等级 {risk_level}",
        {"risk_level": risk_level, "verdict": verdict},
    )

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="compliance_report",
            title="品牌与合规报告",
            content=content,
            text=content_to_text(content),
            tags=["合规", f"风险:{risk_level}"],
        ),
    )

    required_fixes = as_str_array(content.get("required_fixes"))
    needs_human = risk_level == "high" and ctx.revision > 0

    # 非中文市场：广告法词库不适用，系统**不能**假装合规已通过。
    # 明确降级为「需人工复核」并把当地红线写进 risks —— 这与「静默放行」的区别
    # 就是「如实告知未覆盖」与「假装检查过了」的区别。
    extra_risks = read_risks(result.data)
    coverage = compliance_coverage(ctx.brief.language)
    if not coverage["lexicon_coverage"]:
        needs_human = True
        extra_risks.append(
            f"{coverage['label']}尚无自动合规词库，本次合规结论未经当地法规校验，需人工复核"
        )
        for note in list(coverage["notes"])[:3]:
            extra_risks.append(f"[当地红线] {note}")
        if coverage["ad_disclosure_required"]:
            extra_risks.append(
                f"商业推广须显式标注 {coverage['ad_disclosure_text']}（当地强制要求）"
            )

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"合规等级 {risk_level}：阻断 {summary['blocker']} 项、"
                f"重要 {summary['major']} 项、建议 {summary['minor']} 项"
                + (
                    ""
                    if coverage["lexicon_coverage"]
                    else f"（{coverage['label']}无自动词库，需人工复核）"
                )
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.9),
            risks=extra_risks,
            evidence=read_evidence(result.data),
            needs_human_review=needs_human,
            gate_result=verdict,
            revision_requests=(
                [] if verdict == "pass" else [f"[品牌合规] {fix}" for fix in required_fixes]
            ),
            handoff=(
                {"to": "A10", "reason": "合规通过，进入效果预估与人工审批"}
                if verdict == "pass"
                else {"to": "A4", "reason": "存在合规风险，必须修改后方可发布"}
            ),
        ),
    )


a7_compliance = AgentDefinition(meta=META, run=run)
