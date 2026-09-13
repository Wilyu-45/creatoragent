"""任务级分布式追踪：把一次创作变成一棵可下钻、耗时占比可量化的 span 树。

为什么需要它
------------
SSE 事件流能告诉你「发生了什么」，但回答不了这几个运维问题：

* 这次任务的 6 秒里，**哪一段最贵**？（模型调用 vs 门禁评估 vs 黑板写入）
* 某次失败到底发生在**哪一层**？（编排 → 智能体 → 模型调用 → 缓存/熔断）
* 同类任务之间的**耗时结构**是否漂移？（Prompt 变长会让 LLM span 变宽）

事件是扁平的、按时间排的；span 树是有层级、有耗时占比的。两者互补，不能互相替代。

与 OpenTelemetry 的关系（务必如实理解）
--------------------------------------
这里实现的是 **OTel 的数据模型**（trace_id / span_id / parent_span_id /
kind / attributes / status / 嵌套 span），**不是 OTel SDK**：

* trace_id / span_id 采用 W3C 的 32/16 位十六进制格式，因此语义上与 OTel 兼容；
* 但**没有接入 OTLP 导出、没有采样策略、没有跨进程上下文传播**——
  一个任务固定一个 trace_id，span 全部在进程内。

之所以自建而不直接上 SDK，是为了守住本项目的一条底线：
**默认零外部依赖、离线可跑通**（与 embedding、评估器、发布投递同一原则）。
接入 OTLP 的路径是清晰的：`Span.to_otel()` 已按 OTel 语义命名，
把 `Tracer.finish_span` 的结果转发给 OTLP exporter 即可，不需要改动埋点处。

⚠️ 线程模型：编排跑在工作线程，HTTP 跑在事件循环线程。
因此 span 栈与存储都用 thread-local / 加锁，**不能**假设单线程。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import DATA_DIR
from ..logger import create_logger
from . import otel
from .clock import now_iso

log = create_logger("tracing")

#: span 类型（与 OTel SpanKind 对齐命名）
SpanKind = str  # internal | server | client | producer | consumer

#: 单任务保留的 span 上限。正常任务 40-80 个；上限用于防止异常环路把内存吃光。
MAX_SPANS_PER_TRACE = 600

#: 进程内保留的 trace 数上限（任务删除时会主动释放，这里是兜底）
MAX_TRACES = 200


def new_trace_id() -> str:
    """W3C trace-id 格式：32 位十六进制。"""
    return uuid4().hex


def new_span_id() -> str:
    """W3C span-id 格式：16 位十六进制。"""
    return uuid4().hex[:16]


@dataclass
class Span:
    """一个追踪片段。"""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind = "internal"
    #: 归属智能体（A0-A11），便于按智能体聚合
    agent_id: str | None = None
    started_at: str = ""
    finished_at: str = ""
    start_ns: int = 0
    duration_ms: int = 0
    #: ok / error / unset
    status: str = "unset"
    status_message: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    #: 子 span 数量（收尾时统计，便于不必展开就看规模）
    children: int = 0
    #: 该 span 自身耗时占 trace 总耗时的比例（收尾时计算）
    self_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "agent_id": self.agent_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "status_message": self.status_message,
            "attributes": dict(self.attributes),
            "children": self.children,
            "self_ratio": round(self.self_ratio, 4),
        }

    def to_otel(self) -> dict[str, Any]:
        """按 OTel 语义导出（供未来接 OTLP exporter 使用，当前仅作契约说明）。"""
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id or "",
            "name": self.name,
            "kind": self.kind.upper(),
            "startTimeUnixNano": self.start_ns,
            "endTimeUnixNano": self.start_ns + self.duration_ms * 1_000_000,
            "attributes": [
                {"key": key, "value": {"stringValue": str(value)}}
                for key, value in self.attributes.items()
            ],
            "status": {"code": self.status.upper(), "message": self.status_message},
        }


@dataclass
class Trace:
    """一个任务的完整调用轨迹。"""

    trace_id: str
    task_id: str
    tenant: str = "default"
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    spans: list[Span] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "tenant": self.tenant,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "span_count": len(self.spans),
            "roots": [span.span_id for span in self.spans if span.parent_span_id is None],
            "spans": [span.to_dict() for span in self.spans],
        }


class Tracer:
    """进程内 tracer：thread-local span 栈 + 按任务的 span 存储。"""

    def __init__(self) -> None:
        self._local = threading.local()
        self._traces: dict[str, Trace] = {}
        self._by_task: dict[str, str] = {}  # task_id → trace_id
        self._lock = threading.RLock()
        self._export_failures = 0

    # ---------------------------- 上下文 ---------------------------- #

    def _stack(self) -> list[str]:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def current_trace_id(self) -> str | None:
        return getattr(self._local, "trace_id", None)

    def current_span_id(self) -> str | None:
        stack = self._stack()
        return stack[-1] if stack else None

    def trace_id_of(self, task_id: str) -> str | None:
        with self._lock:
            return self._by_task.get(task_id)

    # ---------------------------- 开 trace --------------------------- #

    def start_trace(self, task_id: str, *, tenant: str = "default") -> str:
        """为一个任务开启 trace 并绑定到当前线程。"""
        trace_id = new_trace_id()
        trace = Trace(
            trace_id=trace_id,
            task_id=task_id,
            tenant=tenant,
            started_at=_now_iso(),
        )
        with self._lock:
            self._traces[trace_id] = trace
            self._by_task[task_id] = trace_id
            self._evict_locked()
        self._local.trace_id = trace_id
        self._local.stack = []
        return trace_id

    def _evict_locked(self) -> None:
        """超出容量时淘汰最旧的 trace（调用方需持锁）。"""
        overflow = len(self._traces) - MAX_TRACES
        if overflow <= 0:
            return
        for trace_id in list(self._traces)[:overflow]:
            dropped = self._traces.pop(trace_id, None)
            if dropped is not None:
                self._by_task.pop(dropped.task_id, None)

    def finish_trace(self, task_id: str) -> Trace | None:
        """收尾：计算耗时占比并按需落盘；返回 trace 快照。"""
        with self._lock:
            trace_id = self._by_task.get(task_id)
            trace = self._traces.get(trace_id) if trace_id else None
        if trace is None:
            return None

        trace.finished_at = _now_iso()
        if trace.spans:
            started = min(span.start_ns for span in trace.spans if span.start_ns)
            trace.duration_ms = int((time.time_ns() - started) / 1_000_000) if started else 0
            # 自身耗时占比：直接子 span 的总耗时之外，剩下的就是本层级真正花的时间
            children_total: dict[str, int] = {}
            for span in trace.spans:
                if span.parent_span_id:
                    children_total[span.parent_span_id] = children_total.get(
                        span.parent_span_id, 0
                    ) + span.duration_ms
            for span in trace.spans:
                span.children = sum(
                    1 for child in trace.spans if child.parent_span_id == span.span_id
                )
                own = max(0, span.duration_ms - children_total.get(span.span_id, 0))
                span.self_ratio = (own / trace.duration_ms) if trace.duration_ms else 0.0
        self._export(trace)
        self._local.trace_id = None
        self._local.stack = []
        return trace

    def trace(self, task_id: str) -> Trace | None:
        with self._lock:
            trace_id = self._by_task.get(task_id)
            return self._traces.get(trace_id) if trace_id else None

    def drop(self, task_id: str) -> None:
        """任务被删除时回收其 trace。"""
        with self._lock:
            trace_id = self._by_task.pop(task_id, None)
            if trace_id:
                self._traces.pop(trace_id, None)

    # ----------------------------- span ----------------------------- #

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        agent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span | None:
        """开启一个子 span；无当前 trace 时返回 ``None``（调用方无需分支）。"""
        trace_id = self.current_trace_id()
        if trace_id is None:
            return None
        span = Span(
            trace_id=trace_id,
            span_id=new_span_id(),
            parent_span_id=self.current_span_id(),
            name=name,
            kind=kind,
            agent_id=agent_id,
            started_at=_now_iso(),
            start_ns=time.time_ns(),
            attributes=dict(attributes or {}),
        )
        self._stack().append(span.span_id)
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return span
            if len(trace.spans) >= MAX_SPANS_PER_TRACE:
                # 超限时不再记录，但 span 对象仍返回：调用方照常收尾，不改变控制流
                return span
            trace.spans.append(span)
        return span

    def finish_span(
        self,
        span: Span | None,
        *,
        status: str = "ok",
        status_message: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if span is None:
            return
        stack = self._stack()
        if stack and stack[-1] == span.span_id:
            stack.pop()
        elif span.span_id in stack:
            # 非严格嵌套（异常路径）时也要摘掉，避免栈泄漏
            stack.remove(span.span_id)

        span.finished_at = _now_iso()
        span.duration_ms = int((time.time_ns() - span.start_ns) / 1_000_000)
        span.status = status
        span.status_message = status_message[:300]
        if attributes:
            span.attributes.update(attributes)

    def span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        agent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ):
        """上下文管理器：``with tracer.span("A4.copy"): ...``（异常自动记为 error）。"""
        return _SpanContext(self, name, kind=kind, agent_id=agent_id, attributes=attributes)

    # ----------------------------- 聚合 ----------------------------- #

    def stats(self, *, tenant: str | None = None) -> dict[str, Any]:
        """按 span 名聚合：次数、总耗时、平均耗时、错误数。"""
        with self._lock:
            traces = list(self._traces.values())
        if tenant:
            traces = [trace for trace in traces if trace.tenant == tenant]

        by_name: dict[str, dict[str, Any]] = {}
        errors = 0
        for trace in traces:
            for span in trace.spans:
                row = by_name.setdefault(
                    span.name,
                    {
                        "name": span.name,
                        "kind": span.kind,
                        "count": 0,
                        "total_ms": 0,
                        "max_ms": 0,
                        "errors": 0,
                    },
                )
                row["count"] += 1
                row["total_ms"] += span.duration_ms
                row["max_ms"] = max(row["max_ms"], span.duration_ms)
                if span.status == "error":
                    row["errors"] += 1
                    errors += 1

        rows = sorted(by_name.values(), key=lambda item: -item["total_ms"])
        for row in rows:
            row["avg_ms"] = round(row["total_ms"] / row["count"], 1) if row["count"] else 0
        return {
            "traces": len(traces),
            "spans": sum(len(trace.spans) for trace in traces),
            "errors": errors,
            "by_name": rows,
            "exportPath": str(EXPORT_DIR),
            "exportFormat": "otel-shaped-json",
            "exportFailures": self._export_failures,
        }

    # ----------------------------- 导出 ----------------------------- #

    def _export(self, trace: Trace) -> None:
        """把完成的 trace 落盘（OTel 形状的 JSON），并在启用时转发到 OTLP。

        两种失败都**绝不能**影响任务：追踪是观测能力，不是业务链路的一环。
        """
        # 智能体归属需要沿树继承：llm.* / memory.* 这类子 span 自身不带 agent_id，
        # 若不继承，在 Jaeger 里按 agent 过滤时它们会全部消失。
        owners: dict[str, str] = {}
        for span in trace.spans:
            if span.agent_id:
                owners[span.span_id] = span.agent_id
            elif span.parent_span_id and span.parent_span_id in owners:
                owners[span.span_id] = owners[span.parent_span_id]

        for span in trace.spans:
            try:
                otel.export_span(span, agent_id=owners.get(span.span_id, ""))
            except Exception as error:  # noqa: BLE001 - 双保险：转发层自己也兜底
                log.warn(f"OTLP 转发异常（不影响任务）：{error}")

        try:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            target = EXPORT_DIR / f"{trace.task_id}.json"
            payload = {
                **trace.to_dict(),
                "resource": {"service.name": SERVICE_NAME},
                # 同时给出 OTel 形状，便于将来直接喂给 OTLP collector
                "otel": [span.to_otel() for span in trace.spans],
            }
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as error:
            self._export_failures += 1
            log.warn(f"trace 落盘失败（不影响任务）：{error}")


class _SpanContext:
    """``with tracer.span(...)`` 的实现（异常时自动标 error 并重新抛出）。"""

    def __init__(
        self,
        tracer: Tracer,
        name: str,
        *,
        kind: SpanKind,
        agent_id: str | None,
        attributes: dict[str, Any] | None,
    ) -> None:
        self._tracer = tracer
        self._name = name
        self._kind = kind
        self._agent_id = agent_id
        self._attributes = attributes
        self.span: Span | None = None

    def __enter__(self) -> Span | None:
        self.span = self._tracer.start_span(
            self._name, kind=self._kind, agent_id=self._agent_id, attributes=self._attributes
        )
        return self.span

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc_type is not None:
            self._tracer.finish_span(
                self.span, status="error", status_message=f"{exc_type.__name__}: {exc}"
            )
        else:
            self._tracer.finish_span(self.span, status="ok")
        return False  # 不吞异常


def _now_iso() -> str:
    """间接引用（保留此包装是为了让 tracing 不直接依赖具体时钟实现）。"""
    return now_iso()


#: trace 落盘目录（OTel 形状的 JSON，可直接被下游工具消费）
EXPORT_DIR: Path = DATA_DIR / "traces"

#: 资源里的服务名（落盘与 OTLP 保持一致）
SERVICE_NAME = "creator-agent-studio"

tracer = Tracer()

__all__ = [
    "EXPORT_DIR",
    "MAX_SPANS_PER_TRACE",
    "MAX_TRACES",
    "SERVICE_NAME",
    "Span",
    "Trace",
    "Tracer",
    "new_span_id",
    "new_trace_id",
    "tracer",
]
