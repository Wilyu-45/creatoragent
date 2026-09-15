"""评估历史的 PostgreSQL 实现（仅 ``CREATOR_STORAGE=pg`` 时实例化）。

接口契约见 storage_contract.md §3.4 与 :mod:`app.core.evaluations`（file 版）。
逐出语义原样保留：单任务最近 ``MAX_PER_TASK`` 条、全局最近 ``MAX_RECORDS`` 条；
聚合统计复用 :func:`app.core.evaluations.aggregate_stats`，口径同源。
"""

from __future__ import annotations

import json
from typing import Any

from ..logger import create_logger
from .evaluations import (
    MAX_PER_TASK,
    MAX_RECORDS,
    EvaluationRecord,
    aggregate_stats,
)

log = create_logger("evaluations_pg")

_INSERT = """
INSERT INTO evaluations (id, tenant, task_id, kind, revision, total, verdict, created_at, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO NOTHING
"""


class PgEvaluationStore:
    """与 :class:`app.core.evaluations.EvaluationStore` 同接口的 PostgreSQL 后端。"""

    # --------------------------- 持久化 --------------------------- #

    def load(self) -> None:
        """PG 模式无需预载；保留签名供兼容调用。"""

    def flush(self) -> None:
        """写入即持久；保留签名兼容退出钩子。"""

    # ---------------------------- 写入 ---------------------------- #

    def record(self, entry: EvaluationRecord) -> EvaluationRecord:
        from .pg import pg_pool

        payload = json.dumps(entry.to_dict(), ensure_ascii=False)
        with pg_pool().connection() as conn:
            conn.execute(
                _INSERT,
                (
                    entry.id,
                    entry.tenant or "default",
                    entry.task_id,
                    entry.kind,
                    entry.revision,
                    entry.total,
                    entry.verdict,
                    entry.created_at,
                    payload,
                ),
            )
            # 单任务只保留最近 MAX_PER_TASK 条（同 file 版：多轮返工产生大量中间分）
            conn.execute(
                "DELETE FROM evaluations WHERE id IN ("
                "  SELECT id FROM evaluations WHERE task_id = %s"
                "  ORDER BY created_at DESC, id DESC OFFSET %s"
                ")",
                (entry.task_id, MAX_PER_TASK),
            )
            # 全局只保留最近 MAX_RECORDS 条
            conn.execute(
                "DELETE FROM evaluations WHERE id IN ("
                "  SELECT id FROM evaluations ORDER BY created_at DESC, id DESC OFFSET %s"
                ")",
                (MAX_RECORDS,),
            )
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
        """按时间倒序返回；``tenant`` 非空时按租户过滤（跨租户不可见）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = %s")
            params.append(task_id)
        owner = (tenant or "").strip()
        if owner:
            clauses.append("tenant = %s")
            params.append(owner)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT payload FROM evaluations "
            f"{where} ORDER BY created_at DESC, id DESC LIMIT %s"
        )
        params.append(max(0, limit))
        return self._fetch(sql, tuple(params))

    def latest(self, task_id: str, *, tenant: str | None = None) -> EvaluationRecord | None:
        records = self.list(task_id=task_id, tenant=tenant, limit=1)
        return records[0] if records else None

    def stats(self, *, tenant: str | None = None) -> dict[str, Any]:
        """聚合统计：取该租户全量 payload（≤500 条）后复用共享聚合，口径同源。"""
        owner = (tenant or "").strip()
        where = "WHERE tenant = %s" if owner else ""
        sql = f"SELECT payload FROM evaluations {where} ORDER BY created_at, id"
        params: tuple = (owner,) if owner else ()
        records = self._fetch(sql, params)
        return aggregate_stats(records)

    # ---------------------------- 内部 ---------------------------- #

    @staticmethod
    def _fetch(sql: str, params: tuple) -> list[EvaluationRecord]:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        records: list[EvaluationRecord] = []
        for (payload,) in rows:
            try:
                records.append(EvaluationRecord(**payload))
            except TypeError:  # 字段漂移时逐条跳过，不阻断服务（同 file 版）
                continue
        return records
