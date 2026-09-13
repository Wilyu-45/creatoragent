"""门禁控制器 Gatekeeper（移植自 server/core/gatekeeper.ts）。

对应《creator.md》第 7 章「权限与门禁设计」，以及《plan.md》2.2.1 的条件边逻辑。规则：

    - A5 质量分低于阈值      → 退回 A4
    - A6 高风险未修正        → 否决，禁止发布
    - A7 合规不通过          → 否决，禁止发布
    - 返工次数用尽仍不通过   → 升级为人工介入
    - Turn Budget 命中       → 人工介入
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .clock import now_iso
from .types import AgentResult, GateRecord, QualityScorecard
from .util import js_round

GateVerdict = Literal["pass", "revise", "escalate"]

REVIEWER_PHASE: dict[str, str] = {
    "A5": "EDITING",
    "A6": "FACT_CHECK",
    "A7": "COMPLIANCE",
}


@dataclass
class GateDecision:
    verdict: GateVerdict
    reason: str
    #: 合并后的返工要求，直接交给 A4
    requests: list[str] = field(default_factory=list)
    #: 阻断项描述，用于界面展示
    blocking: list[str] = field(default_factory=list)
    #: 每个审核智能体的裁决（round / created_at 由调用方补齐）
    records: list[dict[str, Any]] = field(default_factory=list)


def decide_gate(
    *,
    results: list[AgentResult],
    revision: int,
    max_revisions: int,
    quality_threshold: int,
    scorecard: QualityScorecard,
    judge: Any | None = None,
    judge_mode: str = "advisory",
) -> GateDecision:
    """汇总审核结论。

    ``judge`` 是 LLM-as-a-Judge 的评估报告（``core/judge.JudgeReport``，类型用
    ``Any`` 以免门禁层反向依赖评估器）。它有三种介入方式：

    * 未传或 ``judge_mode="off"``：完全不参与，行为与历史版本一致；
    * ``advisory``（默认）：只把评估结论写进门禁理由与阻断项，**不改裁决**；
    * ``blocking``：``reject`` 直接升级人工，``review`` 计入返工理由。
    """
    records: list[dict[str, Any]] = []
    requests: list[str] = []
    blocking: list[str] = []

    has_reject = False
    has_revise = False

    judge_verdict = str(getattr(judge, "verdict", "") or "") if judge is not None else ""
    judge_total = float(getattr(judge, "total", 0.0) or 0.0)
    judge_notes = [
        *[str(item) for item in (getattr(judge, "issues", None) or [])],
        *[str(item) for item in (getattr(judge, "suggestions", None) or [])],
    ][:4]

    for result in results:
        phase = REVIEWER_PHASE.get(result.agent_id)
        if phase is None:
            continue

        verdict: str = result.gate_result or "pass"

        # A5 额外应用质量分阈值门禁（creator.md 第 7 章：质量分低于阈值退回 A4）
        if result.agent_id == "A5" and verdict == "pass" and scorecard.overall < quality_threshold:
            verdict = "revise"
            result.revision_requests.append(
                f"[编辑·质量门禁] 综合质量分 {scorecard.overall:.0f} 低于阈值 {quality_threshold}，需整体重写"
            )

        if verdict == "reject":
            has_reject = True
        if verdict == "revise":
            has_revise = True

        if verdict != "pass":
            requests.extend(result.revision_requests)
            blocking.extend(
                [r for r in result.revision_requests if "必改" in r or "否决" in r or "阻断" in r][:3]
            )

        if result.agent_id == "A5":
            score = scorecard.overall
        elif result.agent_id == "A6":
            score = scorecard.fact_safety
        else:
            score = scorecard.compliance

        records.append(
            {
                "phase": phase,
                "agent_id": result.agent_id,
                "verdict": verdict,
                "score": score,
                "blocking": result.revision_requests[:5],
            }
        )

    # ---- LLM-as-a-Judge 的介入（plan.md 4.3 D14） -------------------- #
    judge_note = ""
    if judge is not None and judge_mode != "off":
        judge_note = f"（质量评估 {judge_total:.1f}/100：{judge_verdict}）"
        if judge_mode == "blocking":
            if judge_verdict == "reject":
                has_reject = True
                blocking.append(f"[评估·否决] 质量评估 {judge_total:.1f}/100 显著低于通过线")
                requests.extend(f"[评估] {item}" for item in judge_notes)
            elif judge_verdict == "review":
                has_revise = True
                requests.extend(f"[评估·待改进] {item}" for item in judge_notes)
        elif judge_verdict != "pass":
            # advisory 模式：只记录，不影响裁决
            blocking.extend(f"[评估·参考] {item}" for item in judge_notes[:2])

    if has_reject:
        return GateDecision(
            verdict="escalate",
            reason="事实核查或合规智能体行使否决权，需人工复核后方可继续" + judge_note,
            requests=requests,
            blocking=blocking,
            records=records,
        )

    if has_revise:
        if revision >= max_revisions:
            return GateDecision(
                verdict="escalate",
                reason=f"已用完 {max_revisions} 轮返工额度仍未通过审核，升级人工处理" + judge_note,
                requests=requests,
                blocking=blocking,
                records=records,
            )
        return GateDecision(
            verdict="revise",
            reason=f"存在 {len(requests)} 条需修订项，退回文案智能体修订"
            f"（第 {revision + 1}/{max_revisions} 轮）" + judge_note,
            requests=requests,
            blocking=blocking,
            records=records,
        )

    return GateDecision(
        verdict="pass",
        reason="编辑、事实核查、合规三项审核全部通过" + judge_note,
        requests=[],
        blocking=[],
        records=records,
    )


def build_scorecard(
    *,
    editor: dict[str, Any] | None,
    fact_check: dict[str, Any] | None,
    compliance: dict[str, Any] | None,
) -> QualityScorecard:
    """把各审核智能体的结论合并为统一质量记分卡。"""

    def num(value: Any, fallback: float) -> float:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback

    card = (editor or {}).get("scorecard") or {}
    structure = num(card.get("structure"), 80)
    clarity = num(card.get("clarity"), 80)
    brand_voice = num(card.get("brand_voice"), 80)
    appeal = num(card.get("appeal"), 80)

    fact_summary = (fact_check or {}).get("summary") or {}
    unverified = num(fact_summary.get("unverified"), 0)
    exaggerated = num(fact_summary.get("exaggerated"), 0)
    fact_safety = max(0.0, 100 - unverified * 15 - exaggerated * 12)

    compliance_summary = (compliance or {}).get("summary") or {}
    blocker = num(compliance_summary.get("blocker"), 0)
    major = num(compliance_summary.get("major"), 0)
    compliance_score = num((compliance or {}).get("compliance_score"), 0) or max(
        0.0, 100 - blocker * 30 - major * 12
    )

    if card.get("overall") is not None:
        overall = num(card.get("overall"), 80)
    else:
        overall = js_round((structure + clarity + brand_voice + appeal) / 4)

    return QualityScorecard(
        structure=js_round(structure),
        clarity=js_round(clarity),
        brand_voice=js_round(brand_voice),
        appeal=js_round(appeal),
        fact_safety=js_round(fact_safety),
        compliance=js_round(compliance_score),
        overall=js_round(overall * 0.5 + fact_safety * 0.25 + compliance_score * 0.25),
    )


def to_gate_records(decision: GateDecision, round_: int) -> list[GateRecord]:
    """把 GateDecision.records 落成 TaskRecord.gates 条目。"""
    stamp = now_iso()
    return [
        GateRecord(round=round_, created_at=stamp, **record) for record in decision.records
    ]
