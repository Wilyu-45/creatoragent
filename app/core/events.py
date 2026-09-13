"""任务级事件总线（移植自 server/core/events.ts）。

所有智能体的动作都会变成事件：
- 写入环形缓冲，供 SSE 客户端「断线重连 + 回放」
- 广播给所有订阅者（Web 界面实时进度）

与 TS 版的差异：编排图跑在工作线程、SSE 跑在事件循环，因此这里加锁保证并发安全。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from .clock import now_iso
from .types import AgentEvent

Listener = Callable[[AgentEvent], None]

REPLAY_LIMIT = 400


class EventBus:
    def __init__(self) -> None:
        self._seq = 0
        self._buffers: dict[str, list[AgentEvent]] = {}
        self._listeners: dict[str, set[Listener]] = {}
        self._lock = threading.RLock()

    def publish(
        self,
        *,
        task_id: str,
        type: str,
        message: str,
        agent_id: str | None = None,
        phase: str | None = None,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> AgentEvent:
        with self._lock:
            self._seq += 1
            event = AgentEvent(
                seq=self._seq,
                task_id=task_id,
                ts=now_iso(),
                type=type,  # type: ignore[arg-type]
                agent_id=agent_id,  # type: ignore[arg-type]
                phase=phase,  # type: ignore[arg-type]
                level=level,  # type: ignore[arg-type]
                message=message,
                payload=payload,
            )
            self._append(event)
            return event

    def _append(self, event: AgentEvent) -> None:
        buf = self._buffers.setdefault(event.task_id, [])
        buf.append(event)
        if len(buf) > REPLAY_LIMIT:
            del buf[: len(buf) - REPLAY_LIMIT]

        listeners = list(self._listeners.get(event.task_id, ()))
        # 在锁内复制快照、锁外回调，避免订阅者反向调用总线时死锁
        for listener in listeners:
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - 单个订阅者异常不影响其他订阅者
                pass

    def replay(self, task_id: str, since_seq: int = 0) -> list[AgentEvent]:
        """返回 since_seq 之后的事件（用于 SSE 回放）。"""
        with self._lock:
            return [e for e in self._buffers.get(task_id, ()) if e.seq > since_seq]

    def history(self, task_id: str) -> list[AgentEvent]:
        """返回全量事件（用于任务详情页一次性渲染时间线）。"""
        with self._lock:
            return list(self._buffers.get(task_id, ()))

    def subscribe(self, task_id: str, listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.setdefault(task_id, set()).add(listener)

        def unsubscribe() -> None:
            with self._lock:
                group = self._listeners.get(task_id)
                if group is None:
                    return
                group.discard(listener)
                if not group:
                    self._listeners.pop(task_id, None)

        return unsubscribe

    def drop(self, task_id: str) -> None:
        with self._lock:
            self._buffers.pop(task_id, None)
            self._listeners.pop(task_id, None)

    @property
    def last_seq(self) -> int:
        return self._seq


event_bus = EventBus()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"
