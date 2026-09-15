"""任务持久化（移植自 server/core/store.ts）。

每个任务一个 JSON 文件，内存保留索引。
MVP 用文件存储代替 A11 记忆知识库（见 plan.md 4.2）。

相对 TS 版的两点增强：
1. 落盘改为「写临时文件 + 原子替换」，避免进程被杀死时留下半个 JSON。
2. `load_all` 不再直接判定中断任务失败，而是返回「疑似中断」的任务列表，
   交由编排器尝试用 LangGraph checkpointer 断点续跑；确实无法续跑才标失败。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from ..config import TASK_DIR
from ..logger import create_logger
from .types import TaskRecord

log = create_logger("store")

SAVE_DEBOUNCE_SECONDS = 0.25


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.RLock()

    # -------------------------------------------------------------- #
    # 载入 / 查询                                                     #
    # -------------------------------------------------------------- #

    def load_all(self) -> dict[str, Any]:
        """从磁盘载入全部任务。

        返回 ``{"loaded": int, "interrupted": list[str]}``，
        其中 interrupted 是重启时仍处于 running / awaiting_approval 的任务 id。
        """
        TASK_DIR.mkdir(parents=True, exist_ok=True)
        loaded = 0
        interrupted: list[str] = []

        for file in sorted(TASK_DIR.glob("*.json")):
            try:
                task = TaskRecord.model_validate_json(file.read_text(encoding="utf-8"))
                if task.status in ("running", "awaiting_approval"):
                    interrupted.append(task.id)
                with self._lock:
                    self._tasks[task.id] = task
                loaded += 1
            except Exception as error:  # noqa: BLE001
                log.warn(f"跳过损坏的任务文件 {file.name}", error)

        log.info(
            f"已加载 {loaded} 个历史任务"
            + (f"，其中 {len(interrupted)} 个疑似因重启中断，待尝试断点续跑" if interrupted else "")
        )
        return {"loaded": loaded, "interrupted": interrupted}

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[TaskRecord]:
        with self._lock:
            tasks = list(self._tasks.values())
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def set(self, task: TaskRecord) -> None:
        with self._lock:
            self._tasks[task.id] = task

    # -------------------------------------------------------------- #
    # 落盘                                                            #
    # -------------------------------------------------------------- #

    def schedule_save(self, task: TaskRecord, immediate: bool = False) -> None:
        """防抖落盘，避免高频事件导致磁盘 IO 抖动。"""
        with self._lock:
            self._tasks[task.id] = task
            pending = self._timers.pop(task.id, None)
            if pending is not None:
                pending.cancel()
            if immediate:
                self._save_unlocked(task)
                return
            timer = threading.Timer(SAVE_DEBOUNCE_SECONDS, self._fire, args=(task.id,))
            timer.daemon = True
            self._timers[task.id] = timer
            timer.start()

    def _fire(self, task_id: str) -> None:
        with self._lock:
            self._timers.pop(task_id, None)
            task = self._tasks.get(task_id)
            if task is not None:
                self._save_unlocked(task)

    def save(self, task: TaskRecord) -> None:
        with self._lock:
            self._save_unlocked(task)

    def _save_unlocked(self, task: TaskRecord) -> None:
        try:
            TASK_DIR.mkdir(parents=True, exist_ok=True)
            target = TASK_DIR / f"{task.id}.json"
            # `mode="json"` 让 datetime / Literal 等全部落成 JSON 原生类型
            payload = json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2)
            tmp = TASK_DIR / f".{task.id}.tmp"
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, target)
        except Exception as error:  # noqa: BLE001
            log.error(f"任务 {task.id} 落盘失败", error)

    def flush_all(self) -> None:
        """进程退出前强制刷盘。"""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            tasks = list(self._tasks.values())
            for task in tasks:
                self._save_unlocked(task)
        log.info(f"已刷盘 {len(tasks)} 个任务")

    def delete(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)
            timer = self._timers.pop(task_id, None)
            if timer is not None:
                timer.cancel()
        try:
            (TASK_DIR / f"{task_id}.json").unlink(missing_ok=True)
        except OSError as error:
            log.warn(f"删除任务文件失败 {task_id}", error)


def _build_task_store():
    """按 ``CREATOR_STORAGE`` 选择后端；PG 实现延迟 import（file 模式零依赖）。"""
    from ..config import STORAGE_MODE

    if STORAGE_MODE == "pg":
        from .store_pg import PgTaskStore

        return PgTaskStore()
    return TaskStore()


task_store = _build_task_store()
