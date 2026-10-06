"""生成类旁路作业的通用存储后端（图片 / 视频通道共用）。

为什么抽这一层
--------------
图片生成与视频生成的作业生命周期与数字人渲染（``core/digital_human.py``）完全同构：
都是「受理 → 记账 → 异步推进 → 失败如实呈现」的旁路作业，作业 dict 全量存
``payload``，推进逻辑是纯时间函数（sample）或远端状态映射（http/openai/local），
多副本下幂等。与其在两个模块里各写一份 file/pg 后端，不如抽出这一个通用实现，
按 ``store_file``（file 模式落盘路径）与 ``table``（pg 模式表名）参数化。

契约（见 storage_contract.md）：pg 表与 ``digital_human_jobs`` 同构，``payload jsonb``
存全量作业，API 输出逐字节零漂移；file 模式进程内字典 + 原子写盘。

**不假装成功**：未配置真实端点时显式失败，与 digital_human / web 搜索同一原则。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ..config import STORAGE_MODE
from ..logger import create_logger

log = create_logger("gen_jobs")


class FileJobBackend:
    """file 模式后端：进程内字典 + 原子写盘（与 digital_human 的 file 版同行为）。"""

    def __init__(self, store_file: Path) -> None:
        self._file = store_file
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] | None = None
        self._dirty = False

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        if self._jobs is None:
            self._jobs = {}
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                for job in raw.get("jobs") or []:
                    if isinstance(job, dict) and job.get("id"):
                        self._jobs[str(job["id"])] = job
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as error:
                log.warn(f"生成作业存储读取失败（按空库继续）：{error}")
            self._dirty = False
        return self._jobs

    def _save_locked(self) -> None:
        if not self._dirty:
            return
        payload = {"jobs": sorted(self._jobs.values(), key=lambda job: job.get("created_at") or "")}
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._file)
        self._dirty = False

    def create(self, job: dict[str, Any]) -> None:
        with self._lock:
            self._load_locked()[str(job["id"])] = job
            self._dirty = True
            self._save_locked()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in self._load_locked().values()]

    def write_back(self, jobs: list[dict[str, Any]]) -> None:
        with self._lock:
            live = self._load_locked()
            for job in jobs:
                if job.get("id") in live:
                    live[str(job["id"])] = job
            self._dirty = True
            self._save_locked()

    def drop_task(self, task_id: str) -> int:
        with self._lock:
            jobs = self._load_locked()
            doomed = [jid for jid, job in jobs.items() if str(job.get("task_id")) == task_id]
            for jid in doomed:
                jobs.pop(jid, None)
            if doomed:
                self._dirty = True
                self._save_locked()
        return len(doomed)


_UPSERT = """
INSERT INTO {table} (id, task_id, tenant, status, created_at, updated_at, payload)
VALUES (%s, %s, %s, %s, %s, now(), %s)
ON CONFLICT (id) DO UPDATE
SET status = EXCLUDED.status,
    updated_at = now(),
    payload = EXCLUDED.payload
"""


class PgJobBackend:
    """pg 模式后端：作业 dict 全量存 ``payload jsonb``（FileJobBackend 同接口）。"""

    def __init__(self, table: str) -> None:
        self._table = table

    def create(self, job: dict[str, Any]) -> None:
        self._upsert(job)

    def snapshot(self) -> list[dict[str, Any]]:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            rows = conn.execute(
                f"SELECT payload FROM {self._table} ORDER BY created_at, id"
            ).fetchall()
        return [payload for (payload,) in rows]

    def write_back(self, jobs: list[dict[str, Any]]) -> None:
        for job in jobs:
            self._upsert(job)

    def drop_task(self, task_id: str) -> int:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            cursor = conn.execute(
                f"DELETE FROM {self._table} WHERE task_id = %s", (task_id,)
            )
        return cursor.rowcount

    def _upsert(self, job: dict[str, Any]) -> None:
        from .pg import pg_pool

        payload = json.dumps(job, ensure_ascii=False)
        with pg_pool().connection() as conn:
            conn.execute(
                _UPSERT.format(table=self._table),
                (
                    str(job.get("id") or ""),
                    str(job.get("task_id") or ""),
                    str(job.get("tenant") or "default"),
                    str(job.get("status") or "queued"),
                    str(job.get("created_at") or ""),
                    payload,
                ),
            )


def make_backend(store_file: Path, table: str) -> FileJobBackend | PgJobBackend:
    """按 ``CREATOR_STORAGE`` 选择后端；pg 实现延迟生效（file 模式零外部依赖）。"""
    if STORAGE_MODE == "pg":
        return PgJobBackend(table)
    return FileJobBackend(store_file)


__all__ = ["FileJobBackend", "PgJobBackend", "make_backend"]
