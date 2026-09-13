"""A6 事实核查智能体（移植自 server/agents/a6-factchecker.ts）。"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult, GateResult
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
    id="A6",
    name="事实核查智能体",
    role="事实核查员",
    kind="reviewer",
    phase="FACT_CHECK",
    produces="fact_check_report",
    description="核查数据、时间、引用与案例真实性，标记无来源、过期、矛盾或夸大的信息",
    veto=True,
    capabilities=["主张核验", "数值断言扫描", "来源追溯", "否决权"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 逐条核查文案中的主张：数据、时间、人物、参数、引用、案例
2. 标记状态：verified（可核实）/ unverified（无来源）/ exaggerated（夸大）/ expired（过期）/ contradicted（矛盾）
3. 要求文案补充来源，或改为安全表达
4. 输出核查报告与置信度；高风险未修正时行使否决权

硬性规则：
- 你没有联网能力时，不得假装查证过；无法核实的应标为 unverified 而不是 verified
- 数值型断言（百分比、倍数、天数、样本量）若无来源，一律视为高风险
- 情绪化、主观的最高级表述应标记为 exaggerated
- 只输出 JSON，不输出任何解释性文字""",
)

SCHEMA = """{
  "checks": [{"claim": "", "status": "verified|unverified|exaggerated|expired|contradicted", "source": "", "note": "", "confidence": 0.0}],
  "risk_level": "low|medium|high",
  "verdict": "pass|revise|reject",
  "verdict_reason": "",
  "required_fixes": [""],
  "safe_rewrites": [{"original": "", "rewrite": "", "reason": ""}],
  "verification_scope": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    checks = [
        {
            "claim": as_str(item.get("claim")),
            "status": as_str(item.get("status"), "unverified"),
            "source": as_str(item.get("source"), "未提供"),
            "note": as_str(item.get("note")),
            "confidence": as_num(item.get("confidence"), 0) or 0.6,
        }
        for item in as_obj_array(data.get("checks"))
    ]
    return {
        "checks": checks,
        "risk_level": as_str(data.get("risk_level"), "medium"),
        "verdict": as_str(data.get("verdict"), "revise"),
        "verdict_reason": as_str(data.get("verdict_reason")),
        "required_fixes": as_str_array(data.get("required_fixes")),
        "safe_rewrites": as_obj_array(data.get("safe_rewrites")),
        "verification_scope": as_str_array(data.get("verification_scope")),
        "summary": {
            "total": len(checks),
            "verified": sum(1 for c in checks if c["status"] == "verified"),
            "unverified": sum(1 for c in checks if c["status"] == "unverified"),
            "exaggerated": sum(1 for c in checks if c["status"] == "exaggerated"),
        },
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    draft = ctx.upstream_of("draft")
    ctx.emit("开始核查文案中的数据、引用与主张来源")

    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next(
        (v for v in as_obj_array(draft.get("versions")) if as_str(v.get("id")) == recommended),
        {},
    )
    claims = as_obj_array(draft.get("claims"))

    claims_text = "\n".join(
        f"{i + 1}. 主张：{as_str(c.get('text'))}｜来源：{as_str(c.get('source')) or '（未提供）'}"
        for i, c in enumerate(claims)
    )

    user = f"""【核查对象】{brief.channel} 主推版本
标题：{as_str(target.get('title'))}

正文：
{as_str(target.get('body'))}

【作者声明的主张及来源】
{claims_text or '（作者未声明主张，请自行从正文中抽取）'}

行业：{brief.industry}
请逐条核查并给出风险等级与修改要求，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A6.factcheck",
        {"brief": brief.model_dump(mode="json"), "draft": draft, "revision": ctx.revision},
    )

    content = normalize(result.data)
    summary = as_obj(content["summary"])
    risk_level = as_str(content.get("risk_level"), "medium")
    raw_verdict = as_str(content.get("verdict"))
    if raw_verdict in ("pass", "revise", "reject"):
        verdict: GateResult = raw_verdict  # type: ignore[assignment]
    else:
        verdict = "revise" if risk_level == "high" else "pass"

    ctx.emit(
        f"核查完成：{summary['total']} 项，无来源 {summary['unverified']} 项，风险等级 {risk_level}",
        {"risk_level": risk_level, "verdict": verdict},
    )

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="fact_check_report",
            title="事实核查报告",
            content=content,
            text=content_to_text(content),
            tags=["核查", f"风险:{risk_level}"],
        ),
    )

    required_fixes = as_str_array(content.get("required_fixes"))
    needs_human = risk_level == "high" and (ctx.revision > 0 or verdict == "reject")

    revision_requests: list[str] = []
    if verdict != "pass":
        revision_requests = [f"[事实核查] {fix}" for fix in required_fixes] + [
            f"[事实核查·建议改写] 「{as_str(item.get('original'))}」→「{as_str(item.get('rewrite'))}」"
            for item in as_obj_array(content.get("safe_rewrites"))
        ]

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"核查 {summary['total']} 项主张：可核实 {summary['verified']} 项、"
                f"无来源 {summary['unverified']} 项、夸大 {summary['exaggerated']} 项，"
                f"风险等级 {risk_level}"
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.84),
            risks=read_risks(result.data),
            evidence=read_evidence(result.data),
            needs_human_review=needs_human,
            gate_result=verdict,
            revision_requests=revision_requests,
            handoff=(
                {"to": "A7", "reason": "事实核查通过，进入品牌合规审查"}
                if verdict == "pass"
                else {"to": "A4", "reason": "存在无来源或夸大表述，需补充来源或改写"}
            ),
        ),
    )


a6_factchecker = AgentDefinition(meta=META, run=run)
