"""数字人渲染作业的 PostgreSQL 实现（仅 ``CREATOR_STORAGE=pg`` 时实例化）。

接口契约见 storage_contract.md 与 :mod:`app.core.digital_human`（file 版后端）。
作业推进（sample 按流逝时间 / http 按远端状态）在 file 版里就是**纯时间函数**，
多副本下幂等 —— 本模块只负责持久化三件事：

* ``create``    新作业 INSERT；
* ``write_back`` 推进后状态有变化的作业 UPSERT 回写（进度是派生值，不回写，
  下次读取会按 ``started_at`` 重新计算 —— 与 file 版「只回写状态变化」语义一致）；
* ``drop_task`` 任务删除时级联清理其渲染作业（与 tracer.drop / event_bus.drop 同时机）。

作业 dict 全量存 ``payload jsonb``（含 manifest/history），API 输出逐字节零漂移。
"""

from __future__ import annotations

import json
from typing import Any

from ..logger import create_logger

log = create_logger("dh_jobs_pg")

_UPSERT = """
INSERT INTO digital_human_jobs (id, task_id, tenant, status, created_at, updated_at, payload)
VALUES (%s, %s, %s, %s, %s, now(), %s)
ON CONFLICT (id) DO UPDATE
SET status = EXCLUDED.status,
    updated_at = now(),
    payload = EXCLUDED.payload
"""


class PgJobBackend:
    """与 :mod:`app.core.digital_human` file 后端同接口的 PostgreSQL 实现。"""

    def create(self, job: dict[str, Any]) -> None:
        self._upsert(job)

    def snapshot(self) -> list[dict[str, Any]]:
        """全量作业（payload dict）；排序/过滤/推进仍由模块函数统一处理。"""
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM digital_human_jobs ORDER BY created_at, id"
            ).fetchall()
        return [payload for (payload,) in rows]

    def write_back(self, jobs: list[dict[str, Any]]) -> None:
        """把推进后的作业状态写回（仅状态有变化时被调用，同 file 版）。"""
        for job in jobs:
            self._upsert(job)

    def drop_task(self, task_id: str) -> int:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            cursor = conn.execute(
                "DELETE FROM digital_human_jobs WHERE task_id = %s", (task_id,)
            )
        return cursor.rowcount

    def _upsert(self, job: dict[str, Any]) -> None:
        from .pg import pg_pool

        payload = json.dumps(job, ensure_ascii=False)
        with pg_pool().connection() as conn:
            conn.execute(
                _UPSERT,
                (
                    str(job.get("id") or ""),
                    str(job.get("task_id") or ""),
                    str(job.get("tenant") or "default"),
                    str(job.get("status") or "queued"),
                    str(job.get("created_at") or ""),
                    payload,
                ),
            )
