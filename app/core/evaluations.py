"""评估历史存储（LLM-as-a-Judge 的落地层）。

与记忆库同样落在 ``data/`` 下的 JSON 文件里，理由也相同：
单机 MVP 不需要为了「查历史分数」引入数据库依赖，而这个存储的读写量
（一次任务一条记录）远低于黑板。接口按可替换设计——``record`` / ``list`` / ``stats``
三个方法就是全部对外能力，日后换 PostgreSQL 不需要改调用方。

两个用途
--------
* **回归对比**：同一批黄金 Brief 在不同 Prompt 版本下的分数变化，是
  「Prompt 调优有没有变好」唯一可量化的证据（plan.md 2.5、D14）。
* **模型对照**：``offline`` 与 ``llm`` 两种评估器对同一条产物的分数差异，
  能反过来校准规则评估器的口径。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import EVAL_FILE
from ..core.clock import now_iso
from ..core.events import new_id
from ..logger import create_logger

log = create_logger("evaluations")

#: 每个任务的评估记录上限（保留最近 N 次，覆盖多轮返工与回填复盘）
MAX_PER_TASK = 20

#: 全局记录上限，超出后淘汰最旧
MAX_RECORDS = 500


@dataclass
class EvaluationRecord:
    """一次评估的完整记录：分数 + 分维度证据 + 当时的上下文。"""

    id: str
    task_id: str
    tenant: str
    created_at: str
    #: final（评审后全链路评估）/ revision（返工轮次评估）/ manual（人工触发）
    kind: str = "final"
    revision: int = 0
    mode: str = "offline"
    model: str = ""
    rubric: str = ""
    total: float = 0.0
    verdict: str = "review"
    summary: str = ""
    confidence: float = 0.0
    fallback: bool = False
    fallback_reason: str = ""
    #: 维度明细：[{key,label,score,weight,rationale,evidence}]
    axes: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    brand: str = ""
    channel: str = ""
    industry: str = ""
    #: 触发来源：review（门禁后自动）/ manual（接口触发）/ feedback（回填复盘后）
    trigger: str = "review"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvaluationStore:
    """单进程评估历史：内存索引 + JSON 落盘。"""

    def __init__(self) -> None:
        self._records: list[EvaluationRecord] = []
        self._lock = threading.RLock()
        self._loaded = False

    # --------------------------- 持久化 --------------------------- #

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                raw = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
                items = raw.get("records") if isinstance(raw, dict) else None
                for item in items or []:
                    try:
                        self._records.append(EvaluationRecord(**item))
                    except TypeError:  # 字段漂移时逐条跳过，不阻断服务
                        continue
                if self._records:
                    log.info(f"已加载 {len(self._records)} 条评估记录")
            except FileNotFoundError:
                pass
            except Exception as error:  # noqa: BLE001 - 评估历史损坏不应阻断服务
                log.warn("评估历史文件损坏，以空记录启动", error)

    def flush(self) -> None:
        with self._lock:
            payload = {
                "records": [record.to_dict() for record in self._records],
                "updated_at": now_iso(),
            }
        try:
            EVAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = EVAL_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, EVAL_FILE)
        except OSError as error:
            log.error("评估历史落盘失败", error)

    # ---------------------------- 写入 ---------------------------- #

    def record(self, entry: EvaluationRecord) -> EvaluationRecord:
        """写入一条评估记录并落盘。"""
        self.load()
        with self._lock:
            self._records.append(entry)
            # 单任务只保留最近 MAX_PER_TASK 条：多轮返工会产生大量中间分数
            scoped = [r for r in self._records if r.task_id == entry.task_id]
            if len(scoped) > MAX_PER_TASK:
                drop = {r.id for r in scoped[: len(scoped) - MAX_PER_TASK]}
                self._records = [r for r in self._records if r.id not in drop]
            overflow = len(self._records) - MAX_RECORDS
            if overflow > 0:
                self._records = self._records[overflow:]
        self.flush()
        log.info(
            f"任务 {entry.task_id} 评估完成：{entry.total:.1f}/100（{entry.verdict}，{entry.mode}）"
        )
        return entry

    # ---------------------------- 查询 ---------------------------- #

    def list(
        self,
        *,
        task_id: str | None = None,
        tenant: str | None = None,
        limit: int = 50,
    ) -> list[EvaluationRecord]:
        """按时间倒序返回评估记录；``tenant`` 非空时按租户过滤。"""
        self.load()
        owner = (tenant or "").strip()
        with self._lock:
            records = [
                record
                for record in self._records
                if (not task_id or record.task_id == task_id)
                and (not owner or record.tenant == owner)
            ]
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records[: max(0, limit)]

    def latest(self, task_id: str, *, tenant: str | None = None) -> EvaluationRecord | None:
        records = self.list(task_id=task_id, tenant=tenant, limit=1)
        return records[0] if records else None

    def stats(self, *, tenant: str | None = None) -> dict[str, Any]:
        """聚合统计：平均分、通过率、按模式/维度分组。"""
        self.load()
        owner = (tenant or "").strip()
        with self._lock:
            records = [r for r in self._records if not owner or r.tenant == owner]
        return aggregate_stats(records)


def aggregate_stats(records: list["EvaluationRecord"]) -> dict[str, Any]:
    """对一组评估记录做聚合（file / PG 两个后端共用，保证口径同源）。"""
    axis_totals: dict[str, list[float]] = {}
    by_mode: dict[str, int] = {}
    for record in records:
        by_mode[record.mode] = by_mode.get(record.mode, 0) + 1
        for axis in record.axes:
            key = str(axis.get("key") or "")
            if not key:
                continue
            axis_totals.setdefault(key, []).append(float(axis.get("score") or 0))

    def avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    labels = {
        str(axis.get("key")): str(axis.get("label") or axis.get("key"))
        for record in records
        for axis in record.axes
    }
    ordered = sorted(records, key=lambda record: record.created_at)
    return {
        "total": len(records),
        "tasks": len({record.task_id for record in records}),
        "avg_total": avg([record.total for record in records]),
        "pass_rate": (
            round(
                sum(1 for record in records if record.verdict == "pass") / len(records) * 100, 1
            )
            if records
            else 0.0
        ),
        "verdicts": {
            verdict: sum(1 for record in records if record.verdict == verdict)
            for verdict in ("pass", "review", "reject")
        },
        "modes": by_mode,
        "fallbacks": sum(1 for record in records if record.fallback),
        "axis_avg": [
            {"key": key, "label": labels.get(key, key), "score": avg(values)}
            for key, values in sorted(
                axis_totals.items(), key=lambda item: -avg(item[1])
            )
        ],
        "latest_at": ordered[-1].created_at if ordered else None,
    }


def build_record(
    *,
    task_id: str,
    tenant: str,
    report: Any,
    brand: str = "",
    channel: str = "",
    industry: str = "",
    trigger: str = "review",
) -> EvaluationRecord:
    """把 ``JudgeReport`` 转成可持久化的记录。"""
    return EvaluationRecord(
        id=new_id("eval"),
        task_id=task_id,
        tenant=tenant or "default",
        created_at=now_iso(),
        kind=str(getattr(report, "kind", "final")),
        revision=int(getattr(report, "revision", 0)),
        mode=str(getattr(report, "mode", "offline")),
        model=str(getattr(report, "model", "")),
        rubric=str(getattr(report, "rubric", "")),
        # 落盘前统一到 1 位小数：加权求和会产生 90.99999999999999 这类浮点尾数，
        # 让它流到接口与文件里既难看又会在「分数是否相等」的对比里咬人。
        total=round(float(getattr(report, "total", 0.0)), 1),
        verdict=str(getattr(report, "verdict", "review")),
        summary=str(getattr(report, "summary", "")),
        confidence=float(getattr(report, "confidence", 0.0)),
        fallback=bool(getattr(report, "fallback", False)),
        fallback_reason=str(getattr(report, "fallback_reason", "")),
        axes=[axis.to_dict() for axis in getattr(report, "axes", [])],
        issues=list(getattr(report, "issues", [])),
        suggestions=list(getattr(report, "suggestions", [])),
        brand=brand,
        channel=channel,
        industry=industry,
        trigger=trigger,
    )


def _build_evaluation_store():
    """按 ``CREATOR_STORAGE`` 选择后端；PG 实现延迟 import（file 模式零依赖）。"""
    from ..config import STORAGE_MODE

    if STORAGE_MODE == "pg":
        from .evaluations_pg import PgEvaluationStore

        return PgEvaluationStore()
    return EvaluationStore()


evaluation_store = _build_evaluation_store()

__all__ = [
    "EvaluationRecord",
    "EvaluationStore",
    "MAX_PER_TASK",
    "MAX_RECORDS",
    "aggregate_stats",
    "build_record",
    "evaluation_store",
]
