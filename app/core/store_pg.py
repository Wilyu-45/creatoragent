"""任务记录的 PostgreSQL 实现（仅 ``CREATOR_STORAGE=pg`` 时实例化）。

接口契约见 storage_contract.md §3.1 与 :mod:`app.core.store`（file 版）。
与 file 版的关键差异（多副本语义所必需，调用方零感知）：

* **内存索引移除** —— get/list 直查数据库，副本 B 能立刻看到副本 A 写入的任务；
* **防抖移除** —— ``schedule_save`` 保留签名但立即 UPSERT（单行写本地 PG <1ms，
  一次任务约 20-40 次，无压力）；
* ``load_all`` 的 interrupted 语义原样保留：重启时 ``status ∈ {running,
  awaiting_approval}`` 的任务交由编排器尝试断点续跑。
"""

from __future__ import annotations

import json
from typing import Any

from ..logger import create_logger
from .types import TaskRecord

log = create_logger("store_pg")

_UPSERT = """
INSERT INTO tasks (id, tenant, status, created_at, updated_at, payload)
VALUES (%s, %s, %s, %s, now(), %s)
ON CONFLICT (id) DO UPDATE
SET tenant = EXCLUDED.tenant,
    status = EXCLUDED.status,
    created_at = EXCLUDED.created_at,
    updated_at = now(),
    payload = EXCLUDED.payload
"""


def _validate(payload: Any) -> TaskRecord:
    """payload（jsonb dict）→ TaskRecord；字段漂移时抛异常由调用方跳过。"""
    return TaskRecord.model_validate(payload)


class PgTaskStore:
    """与 :class:`app.core.store.TaskStore` 同接口的 PostgreSQL 后端。"""

    # -------------------------------------------------------------- #
    # 载入 / 查询                                                     #
    # -------------------------------------------------------------- #

    def load_all(self) -> dict[str, Any]:
        """载入全部任务并返回 ``{"loaded": int, "interrupted": list[str]}``。"""
        from .pg import pg_pool

        loaded = 0
        interrupted: list[str] = []
        with pg_pool().connection() as conn:
            rows = conn.execute(
                "SELECT id, status, payload FROM tasks ORDER BY created_at, id"
            ).fetchall()
        for task_id, status, payload in rows:
            try:
                _validate(payload)
                if status in ("running", "awaiting_approval"):
                    interrupted.append(task_id)
                loaded += 1
            except Exception as error:  # noqa: BLE001 - 字段漂移行跳过不崩溃（同 file 版）
                log.warn(f"跳过无法解析的任务记录 {task_id}", error)
        log.info(
            f"已加载 {loaded} 个历史任务"
            + (f"，其中 {len(interrupted)} 个疑似因重启中断，待尝试断点续跑" if interrupted else "")
        )
        return {"loaded": loaded, "interrupted": interrupted}

    def get(self, task_id: str) -> TaskRecord | None:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            row = conn.execute(
                "SELECT payload FROM tasks WHERE id = %s", (task_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return _validate(row[0])
        except Exception as error:  # noqa: BLE001
            log.warn(f"任务 {task_id} 记录无法解析", error)
            return None

    def list(self) -> list[TaskRecord]:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM tasks ORDER BY created_at DESC, id DESC"
            ).fetchall()
        tasks: list[TaskRecord] = []
        for (payload,) in rows:
            try:
                tasks.append(_validate(payload))
            except Exception as error:  # noqa: BLE001
                log.warn("跳过无法解析的任务记录", error)
        return tasks

    # -------------------------------------------------------------- #
    # 写入                                                            #
    # -------------------------------------------------------------- #

    def set(self, task: TaskRecord) -> None:
        """UPSERT（接口完备；file 版仅更新内存索引，PG 版直写数据库）。"""
        self._upsert(task)

    def schedule_save(self, task: TaskRecord, immediate: bool = False) -> None:  # noqa: ARG002
        """立即 UPSERT。签名保留（immediate 参数等效忽略）—— 事务直写无需防抖。"""
        self._upsert(task)

    def save(self, task: TaskRecord) -> None:
        self._upsert(task)

    def _upsert(self, task: TaskRecord) -> None:
        from .pg import pg_pool

        payload = task.model_dump(mode="json")
        try:
            with pg_pool().connection() as conn:
                conn.execute(
                    _UPSERT,
                    (
                        task.id,
                        task.tenant or "default",
                        task.status,
                        task.created_at,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
        except Exception as error:  # noqa: BLE001
            log.error(f"任务 {task.id} 落库失败", error)

    def flush_all(self) -> None:
        """进程退出前的刷盘钩子：PG 模式下写入即持久，无需处理。"""
        log.info("PG 存储无需刷盘（写入即持久）")

    def delete(self, task_id: str) -> None:
        from .pg import pg_pool

        try:
            with pg_pool().connection() as conn:
                conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        except Exception as error:  # noqa: BLE001
            log.warn(f"删除任务失败 {task_id}", error)
