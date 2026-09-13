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
* **跨进程上下文传播**：入站支持 W3C ``traceparent`` 头（``POST /api/tasks``
  携带时，任务的 trace 会延续调用方的 trace_id，根 span 挂在远端 span 之下）；
  出站投递（发布 webhook / 数字人渲染服务）同样携带 ``traceparent``，
  让网关侧能把回调与任务对齐；
* **采样**：``OTEL_TRACES_SAMPLER``（与 OTel 环境变量规范同名）决定 trace 是否导出
  （OTLP 转发 + 落盘 JSON）。**采样只作用于导出面**：进程内 span 树始终完整，
  界面排障不受影响；未采样的 trace 在 Jaeger 里查不到是预期行为而非丢失。

之所以自建而不直接上 SDK，是为了守住本项目的一条底线：
**默认零外部依赖、离线可跑通**（与 embedding、评估器、发布投递同一原则）。
接入 OTLP 的路径是清晰的：`Span.to_otel()` 已按 OTel 语义命名，
把 `Tracer.finish_span` 的结果转发给 OTLP exporter 即可，不需要改动埋点处。

⚠️ 线程模型：编排跑在工作线程，HTTP 跑在事件循环线程。
因此 span 栈与存储都用 thread-local / 加锁，**不能**假设单线程。
由 API 线程触发的旁路操作（发布投递、数字人渲染）用 ``span_on_task``
把 span 以**根 span** 挂到所属任务的 trace 上。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import DATA_DIR, get_config
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


# ------------------------------------------------------------------ #
# W3C Trace Context 传播（traceparent 头的解析与构造）                #
# ------------------------------------------------------------------ #


@dataclass
class RemoteParent:
    """从入站 ``traceparent`` 头解析出的远端父上下文。"""

    trace_id: str  # 32 位十六进制
    span_id: str  # 16 位十六进制
    sampled: bool = True


_TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)


def parse_traceparent(raw: str | None) -> RemoteParent | None:
    """解析 W3C ``traceparent`` 头（``00-<traceid>-<spanid>-<flags>``）。

    任何不合法的输入（缺段、非十六进制、全零 id、版本 ff）都返回 ``None``——
    传播是尽力而为的观测能力，绝不能让坏头打坏任务创建。
    """
    if not raw:
        return None
    match = _TRACEPARENT_RE.match(raw.strip().lower())
    if match is None:
        return None
    version, trace_id, span_id, flags = match.groups()
    if version == "ff" or set(trace_id) == {"0"} or set(span_id) == {"0"}:
        return None
    return RemoteParent(trace_id=trace_id, span_id=span_id, sampled=bool(int(flags, 16) & 0x01))


def format_traceparent(trace_id: str, span_id: str, *, sampled: bool = True) -> str:
    """按 W3C 格式构造 ``traceparent`` 头；入参不合法返回空串。"""
    if (
        not re.fullmatch(r"[0-9a-f]{32}", trace_id or "")
        or not re.fullmatch(r"[0-9a-f]{16}", span_id or "")
        or set(trace_id) == {"0"}
        or set(span_id) == {"0"}
    ):
        return ""
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"


# ------------------------------------------------------------------ #
# 采样决策（与 OTel 采样器语义对齐，但决策在 trace 创建时一次做出）    #
# ------------------------------------------------------------------ #


def decide_sampling(
    sampler: str, ratio: float, trace_id: str, parent_sampled: bool | None
) -> bool:
    """按采样器名称决定一个 trace 是否导出。

    * ``always_on`` / ``always_off``：无条件采样 / 丢弃；
    * ``traceidratio``：以 trace_id 为随机源做确定性判定（同一 trace 结论恒定，
      便于离线回归断言；与 SDK 的「随机比例」语义等价、可复现）；
    * ``parentbased_*``：有远端父上下文时沿用父方的采样决定，否则按底层采样器。
    """
    ratio = min(1.0, max(0.0, ratio))
    base = sampler[12:] if sampler.startswith("parentbased_") else sampler
    if parent_sampled is not None and sampler.startswith("parentbased_"):
        if base == "always_on":
            return True if parent_sampled else False
        if base == "traceidratio":
            # parentbased_traceidratio：父方未采样直接丢弃，父方采样才按比例
            return parent_sampled and _ratio_hit(trace_id, ratio)
        return bool(parent_sampled)
    if base == "always_on":
        return True
    if base == "always_off":
        return False
    if base == "traceidratio":
        return _ratio_hit(trace_id, ratio)
    return True  # 未知采样器按 always_on 处理，宁可多留不可丢


def _ratio_hit(trace_id: str, ratio: float) -> bool:
    if ratio >= 1.0:
        return True
    if ratio <= 0.0:
        return False
    # 取 trace_id 前 8 位十六进制（0-2^32）映射到 [0,1)，确定性且均匀
    bucket = int(trace_id[:8], 16) / float(0x1_0000_0000)
    return bucket < ratio


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
    #: 采样决策：False 时不做 OTLP 转发与落盘（进程内轨迹仍完整）
    sampled: bool = True
    #: 远端父上下文（跨进程传播时保留来源信息）
    remote_parent: str = ""
    spans: list[Span] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "tenant": self.tenant,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "sampled": self.sampled,
            "remote_parent": self.remote_parent,
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
        self._sampled_traces = 0
        self._unsampled_traces = 0

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

    def start_trace(
        self,
        task_id: str,
        *,
        tenant: str = "default",
        parent: RemoteParent | None = None,
    ) -> str:
        """为一个任务开启 trace 并绑定到当前线程。

        传入 ``parent``（来自入站 ``traceparent`` 头）时，本 trace **延续调用方的
        trace_id**，根 span 挂在远端 span 之下 —— 跨进程的同一条链路因此在
        Jaeger 里能拼成完整一棵树。采样决策同时做出（见 ``decide_sampling``）。
        """
        trace_id = parent.trace_id if parent is not None else new_trace_id()
        cfg = get_config().tracing
        sampled = decide_sampling(
            cfg.sampler,
            cfg.sample_ratio,
            trace_id,
            parent.sampled if parent is not None else None,
        )
        trace = Trace(
            trace_id=trace_id,
            task_id=task_id,
            tenant=tenant,
            started_at=_now_iso(),
            sampled=sampled,
            remote_parent=f"{parent.trace_id}:{parent.span_id}" if parent else "",
        )
        with self._lock:
            self._traces[trace_id] = trace
            self._by_task[task_id] = trace_id
            self._evict_locked()
        self._local.trace_id = trace_id
        self._local.stack = []
        # 远端父 span：只对「本线程创建的第一个根 span」生效（见 start_span）
        self._local.remote_parent_span_id = parent.span_id if parent else None
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
        """开启一个子 span；无当前 trace 时返回 ``None``（调用方无需分支）。

        栈为空（根 span）且存在远端父上下文时，父 span 取远端 span ——
        这是跨进程传播在本进程内的落地：任务的第一个 span 成为远端调用的子节点。
        """
        trace_id = self.current_trace_id()
        if trace_id is None:
            return None
        parent_span_id = self.current_span_id()
        if parent_span_id is None:
            parent_span_id = getattr(self._local, "remote_parent_span_id", None)
            self._local.remote_parent_span_id = None  # 只对第一个根 span 生效
        span = Span(
            trace_id=trace_id,
            span_id=new_span_id(),
            parent_span_id=parent_span_id,
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

    @contextmanager
    def span_on_task(
        self,
        task_id: str,
        name: str,
        *,
        kind: SpanKind = "internal",
        agent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ):
        """在**当前线程**为指定任务的 trace 开一个根 span。

        编排的 span 栈绑定在工作线程的 thread-local 上；由 API 线程触发的旁路操作
        （发布投递、数字人渲染）默认拿不到任务上下文。这个方法临时把线程上下文
        切到任务 trace 上，span 以**根 span** 挂进任务 trace（与编排根并列），
        离开时恢复原状。任务无 trace（尚未开跑）时退化为无 span。
        """
        with self._lock:
            trace_id = self._by_task.get(task_id)
        if trace_id is None:
            yield None
            return
        prev_trace = getattr(self._local, "trace_id", None)
        prev_stack = getattr(self._local, "stack", None)
        prev_remote = getattr(self._local, "remote_parent_span_id", None)
        self._local.trace_id = trace_id
        self._local.stack = []
        self._local.remote_parent_span_id = None
        try:
            with self.span(name, kind=kind, agent_id=agent_id, attributes=attributes) as span:
                yield span
        finally:
            self._local.trace_id = prev_trace
            self._local.stack = prev_stack if prev_stack is not None else []
            self._local.remote_parent_span_id = prev_remote

    def current_traceparent(self) -> str:
        """当前线程 span 的 W3C ``traceparent`` 头（无上下文时返回空串）。

        供出站调用（发布 webhook / 数字人渲染服务）携带：远端网关据此把
        回调与任务对齐。未采样的 trace 也会传播，但 flags 置 00（W3C 语义）。
        """
        trace_id = self.current_trace_id()
        span_id = self.current_span_id()
        if not trace_id or not span_id:
            return ""
        with self._lock:
            trace = self._traces.get(trace_id)
        sampled = trace.sampled if trace is not None else True
        return format_traceparent(trace_id, span_id, sampled=sampled)

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
        cfg = get_config().tracing
        return {
            "traces": len(traces),
            "spans": sum(len(trace.spans) for trace in traces),
            "errors": errors,
            "by_name": rows,
            "exportPath": str(EXPORT_DIR),
            "exportFormat": "otel-shaped-json",
            "exportFailures": self._export_failures,
            # 采样：只作用于导出面（OTLP + 落盘），进程内轨迹始终完整
            "sampling": {
                "sampler": cfg.sampler,
                "sampleRatio": cfg.sample_ratio,
                "traces_sampled": self._sampled_traces,
                "traces_unsampled": self._unsampled_traces,
            },
        }

    # ----------------------------- 导出 ----------------------------- #

    def _export(self, trace: Trace) -> None:
        """把完成的 trace 落盘（OTel 形状的 JSON），并在启用时转发到 OTLP。

        两种失败都**绝不能**影响任务：追踪是观测能力，不是业务链路的一环。
        采样决策在 ``start_trace`` 时做出：未采样的 trace 不导出（不转发、不落盘），
        进程内轨迹仍完整 —— Jaeger 里查不到它是预期行为，不是数据丢失。
        """
        if not trace.sampled:
            self._unsampled_traces += 1
            return
        self._sampled_traces += 1

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
    "RemoteParent",
    "SERVICE_NAME",
    "Span",
    "Trace",
    "Tracer",
    "decide_sampling",
    "format_traceparent",
    "new_span_id",
    "new_trace_id",
    "parse_traceparent",
    "tracer",
]
