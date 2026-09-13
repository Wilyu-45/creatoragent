"""黄金数据集的执行器：把一条用例跑完整条流水线并压缩成可比较的结果。

放在 ``app/core`` 而不是脚本里，是为了让「Web 界面点一下」与「CLI 跑回归」
共用**同一段执行逻辑** —— 否则两边迟早对「什么算通过」产生分歧。

执行方式：直接驱动编排器（而不是发 HTTP），因为调用方（CLI 脚本）本身
就已经起了一个服务进程；再走一遍 HTTP 只会多一层超时与连接管理。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..logger import create_logger
from .blackboard import blackboard
from .clock import now_iso
from .golden import CaseResult, GoldenBrief, check_expectations, load_dataset
from .orchestrator import orchestrator
from .store import task_store
from .types import Brief

log = create_logger("golden.runner")

#: 单条用例的等待上限：Mock 引擎约 6s，真实模型可能到分钟级
CASE_TIMEOUT_SECONDS = 300

#: 轮询间隔
POLL_SECONDS = 0.3

TERMINAL_STATUSES = ("completed", "rejected", "failed", "cancelled")


def _brief_of(case: GoldenBrief) -> Brief:
    raw = dict(case.brief)
    base = Brief()
    return Brief(
        brand=str(raw.get("brand") or base.brand),
        product=str(raw.get("product") or base.product),
        objective=str(raw.get("objective") or base.objective),
        audience=str(raw.get("audience") or base.audience),
        channel=str(raw.get("channel") or base.channel),
        tone=str(raw.get("tone") or base.tone),
        industry=str(raw.get("industry") or base.industry),
        keywords=[str(item) for item in (raw.get("keywords") or [])],
        constraints=[str(item) for item in (raw.get("constraints") or [])],
        deliverables=[str(item) for item in (raw.get("deliverables") or [])],
        notes=str(raw.get("notes") or ""),
        priority=raw.get("priority") if raw.get("priority") in ("low", "normal", "high", "urgent") else "normal",
    )


def _extract(task: Any) -> CaseResult:
    """把任务记录压缩成可比较的标量集合。

    刻意只取**稳定、可解释**的指标：质量分、评估分、返工轮次、合规裁决序列。
    不复用「产物全文」—— 内容本身是发散的，拿它做 diff 只会产生噪声。
    """
    artifacts = list(task.artifacts)
    result = CaseResult(
        id="",
        status=str(task.status),
        quality_score=float(task.scorecard.overall) if task.scorecard else 0.0,
        revision_round=int(task.revision_round),
        artifact_count=len(artifacts),
        task_id=str(task.id),
        error=str(task.error or ""),
    )

    # 合规裁决序列：首轮是否被拦，是强监管用例的核心信号
    result.compliance_verdicts = [
        str(gate.verdict) for gate in task.gates if str(gate.agent_id) == "A7"
    ]
    result.first_round_blocked = bool(
        result.compliance_verdicts and result.compliance_verdicts[0] != "pass"
    )

    facts = blackboard.facts(task.id)
    verified = [fact for fact in facts if fact.status == "verified"]
    result.fact_accuracy = round(len(verified) / len(facts) * 100, 1) if facts else 0.0

    compliance = next((a for a in reversed(artifacts) if a.type == "compliance_report"), None)
    consistency = (compliance.content or {}).get("brand_consistency") if compliance else None
    score = consistency.get("score") if isinstance(consistency, dict) else None
    result.brand_consistency = float(score) if isinstance(score, (int, float)) else 0.0

    effect = next((a for a in reversed(artifacts) if a.type == "effect_report"), None)
    predicted = (effect.content or {}).get("predicted") if effect else None
    ctr = predicted.get("ctr") if isinstance(predicted, dict) else None
    if isinstance(ctr, (int, float)):
        result.predicted_ctr = float(ctr)

    return result


def _delivered_text(task: Any) -> str:
    """取最终交付文本；没有交付件时退回编辑定稿。

    内容级断言必须作用在**最终要发出去的那一稿**上 —— 检查中间稿会漏掉
    「返工后又把违规表述加回来」这种情况。
    """
    delivery = next(
        (a for a in reversed(list(task.artifacts)) if a.type == "final_delivery"), None
    )
    if delivery is not None:
        content = delivery.content or {}
        parts = [
            str(content.get("title") or ""),
            str(content.get("body") or ""),
            str(content.get("cta") or ""),
            " ".join(str(tag) for tag in (content.get("hashtags") or [])),
        ]
        text = "\n".join(part for part in parts if part.strip())
        if text.strip():
            return text

    edit = next((a for a in reversed(list(task.artifacts)) if a.type == "edited_copy"), None)
    if edit is not None:
        revised = (edit.content or {}).get("revised")
        if isinstance(revised, dict):
            parts = [
                str(revised.get("title") or ""),
                str(revised.get("body") or ""),
                str(revised.get("cta") or ""),
                " ".join(str(tag) for tag in (revised.get("hashtags") or [])),
            ]
            return "\n".join(part for part in parts if part.strip())

    draft = next((a for a in reversed(list(task.artifacts)) if a.type == "copy_draft"), None)
    return str((draft.content or {}).get("text") or "") if draft is not None else ""


def run_case(case: GoldenBrief, *, timeout: float = CASE_TIMEOUT_SECONDS) -> CaseResult:
    """跑一条用例（自动审批，跳过人工裁决），返回压缩后的结果。"""
    started = time.monotonic()
    brief = _brief_of(case)

    try:
        task = orchestrator.create_task(brief, auto_approve=True)
    except Exception as error:  # noqa: BLE001 - 建任务失败也要落成一条可比较的结果
        log.error(f"黄金用例 {case.id} 创建任务失败", error)
        failed = CaseResult(id=case.id, status="failed", error=f"{type(error).__name__}: {error}")
        failed.duration_ms = int((time.monotonic() - started) * 1000)
        return failed

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = task_store.get(task.id)
        if current is not None and current.status in TERMINAL_STATUSES:
            result = _extract(current)
            result.id = case.id
            result.duration_ms = int((time.monotonic() - started) * 1000)

            # 评估分来自评估历史：门禁时刻一份、交付前一份
            from .evaluations import evaluation_store

            records = evaluation_store.list(task_id=current.id, limit=20)
            finals = [r for r in records if r.trigger in ("delivery", "review")]
            review = next((r for r in reversed(finals) if r.trigger == "review"), None)
            delivery = next((r for r in finals if r.trigger == "delivery"), None)
            if review is not None:
                result.judge_total = float(review.total)
                result.axis_scores = {
                    str(axis.get("key")): float(axis.get("score") or 0)
                    for axis in review.axes
                }
            if delivery is not None:
                result.judge_final_total = float(delivery.total)
                # 交付前的那份是最终口径，维度分以它为准
                result.axis_scores = {
                    str(axis.get("key")): float(axis.get("score") or 0)
                    for axis in delivery.axes
                }
            elif review is not None:
                result.judge_final_total = float(review.total)

            # 内容级期望：分数之外的硬约束（品牌名、关键词、阻断用语、标题字数…）
            result.delivered_text = _delivered_text(current)
            result.artifact_types = sorted({artifact.type for artifact in current.artifacts})
            script = next(
                (a for a in reversed(list(current.artifacts)) if a.type == "video_script"), None
            )
            if script is not None:
                content = script.content or {}
                shots = content.get("shots") if isinstance(content.get("shots"), list) else []
                result.video_shot_count = len(shots)
                voiceover = content.get("voiceover")
                result.video_voiceover_count = (
                    len(voiceover) if isinstance(voiceover, list) else 0
                )
                starts = [
                    int(item.get("start_second") or 0)
                    for item in shots
                    if isinstance(item, dict)
                ]
                result.video_timeline_ok = bool(starts) and starts == sorted(starts)
            result.checks = check_expectations(
                case, result, delivered_text=result.delivered_text
            )
            return result
        time.sleep(POLL_SECONDS)

    timed_out = _extract(task_store.get(task.id) or task)
    timed_out.id = case.id
    timed_out.status = "timeout"
    timed_out.error = f"超过 {timeout:.0f}s 未到达终态"
    timed_out.duration_ms = int((time.monotonic() - started) * 1000)
    log.warn(f"黄金用例 {case.id} 超时（{timeout:.0f}s）")
    return timed_out


def run_dataset(
    *,
    limit: int | None = None,
    case_ids: list[str] | None = None,
    timeout: float = CASE_TIMEOUT_SECONDS,
) -> list[CaseResult]:
    """按顺序跑一批用例。

    **刻意串行**：并发会引入黑板租约竞争与资源抢占，让「分数变化」多出一个
    与代码无关的变量。回归测试要的是可复现，不是吞吐。
    """
    cases = load_dataset()
    if case_ids:
        wanted = {item.strip() for item in case_ids if item.strip()}
        cases = [case for case in cases if case.id in wanted]
    if limit is not None:
        cases = cases[: max(0, limit)]

    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        log.info(f"[{index}/{len(cases)}] 黄金用例 {case.id}（{case.channel}/{case.industry}）")
        results.append(run_case(case, timeout=timeout))
    return results


def run_dataset_async(
    *,
    limit: int | None = None,
    case_ids: list[str] | None = None,
    timeout: float = CASE_TIMEOUT_SECONDS,
) -> threading.Thread:
    """后台线程跑数据集，供「不阻塞请求」的调用方使用。"""
    thread = threading.Thread(
        target=run_dataset,
        kwargs={"limit": limit, "case_ids": case_ids, "timeout": timeout},
        name="golden-runner",
        daemon=True,
    )
    thread.start()
    return thread


# ------------------------------------------------------------------ #
# 后台运行状态（供 Web 界面「点一下跑回归」）                          #
# ------------------------------------------------------------------ #


@dataclass
class RunState:
    """一次黄金数据集运行的状态快照。

    为什么要有这层状态：跑完 10 条用例需要分钟级，**不能**把 HTTP 请求挂在上面
    （浏览器与 fetch 都有超时）。因此接口只负责「启动 + 查询」，
    真实执行放在后台线程里，状态在这里聚合。
    """

    running: bool = False
    started_at: str = ""
    finished_at: str = ""
    total: int = 0
    completed: int = 0
    current: str = ""
    #: 本次有意运行的用例 id（全量运行时为 None）。用于让「部分运行」也能判定通过，
    #: 同时不影响「全量运行时缺用例算失败」这条纪律。
    scope: list[str] | None = None
    results: list[CaseResult] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "completed": self.completed,
            "current": self.current,
            "scope": self.scope,
            "error": self.error,
            "results": [result.to_dict() for result in self.results],
        }


_state = RunState()
_state_lock = threading.Lock()


def state() -> RunState:
    with _state_lock:
        return _state


def reset_state() -> RunState:
    global _state
    with _state_lock:
        _state = RunState()
        return _state


def start_background_run(
    *,
    limit: int | None = None,
    case_ids: list[str] | None = None,
    timeout: float = CASE_TIMEOUT_SECONDS,
) -> tuple[bool, RunState]:
    """启动一次后台运行；已在运行时返回 ``(False, 当前状态)`` 而不是排队。"""
    global _state
    with _state_lock:
        if _state.running:
            return False, _state
        cases = load_dataset()
        if case_ids:
            wanted = {item.strip() for item in case_ids if item.strip()}
            cases = [case for case in cases if case.id in wanted]
        if limit is not None:
            cases = cases[: max(0, limit)]
        _state = RunState(
            running=True,
            started_at=now_iso(),
            total=len(cases),
            # 只在「有意只跑一部分」时记录范围；全量运行时保持 None
            scope=[case.id for case in cases] if (case_ids or limit is not None) else None,
        )
        snapshot = _state

    def worker() -> None:
        global _state
        results: list[CaseResult] = []
        try:
            for index, case in enumerate(cases, start=1):
                with _state_lock:
                    _state.current = case.id
                log.info(f"[{index}/{len(cases)}] 黄金用例 {case.id}（{case.channel}/{case.industry}）")
                results.append(run_case(case, timeout=timeout))
                with _state_lock:
                    _state.completed = index
                    _state.results = list(results)
        except Exception as error:  # noqa: BLE001 - 后台任务异常必须落到状态里可见
            log.error("黄金数据集运行失败", error)
            with _state_lock:
                _state.error = f"{type(error).__name__}: {error}"
        finally:
            with _state_lock:
                _state.running = False
                _state.finished_at = now_iso()
                _state.current = ""
                _state.results = list(results)

    threading.Thread(target=worker, name="golden-runner", daemon=True).start()
    return True, snapshot


__all__ = [
    "CASE_TIMEOUT_SECONDS",
    "TERMINAL_STATUSES",
    "RunState",
    "reset_state",
    "run_case",
    "run_dataset",
    "run_dataset_async",
    "start_background_run",
    "state",
]
