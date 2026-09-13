"""把进程内 span 转发到真实的 OpenTelemetry（OTLP）——可选能力，默认关闭。

设计原则：**默认零依赖，按需接线**
----------------------------------
本项目默认「离线可跑通」，因此 OTel SDK **只在 ``OTLP_ENDPOINT`` 配置后才被 import**。
未配置时进程内追踪（``core/tracing.py``）完全自实现，不产生任何 OTel 依赖。

为什么不是「用 OTel SDK 替换自实现」
-----------------------------------
自实现那棵树承担了两件 OTel SDK 不直接提供的事：

1. **可离线回归**：span 树结构与层级被 ``doctor.py`` / ``verify_contracts.py`` 断言，
   而这些断言必须在没有 collector 的环境下也能跑（CI 里没有 Jaeger）；
2. **与业务语义绑定**：`gate.verdict` / `judge.total` / `llm.degraded` 这些属性
   是我们自己的观测口径，写入时机在编排代码里。

所以正确的分工是：**自实现负责「采集与本地可测」，OTel 负责「导出与生态」**。
转发时复刻父子关系（否则在 Jaeger 里会变成一堆并列的根 span，
正是第七轮踩过的那个坑），并把同样的属性一并带上。

环境变量
--------
* ``OTLP_ENDPOINT``：如 ``http://localhost:4318``（OTLP/HTTP）或完整路径
  ``http://localhost:4318/v1/traces``。为空则完全不加载 OTel。
* ``OTEL_SERVICE_NAME``：服务名，默认 ``creator-agent-studio``。
* ``OTLP_HEADERS``：形如 ``key1=value1,key2=value2``，用于带鉴权的托管 collector。
* ``OTLP_INSECURE``：``true`` 时使用 http（默认按 endpoint 协议判断）。
"""

from __future__ import annotations

import threading
from typing import Any

from ..logger import create_logger

log = create_logger("otel")

#: 全局状态：未启用时为 None，不引入任何 OTel 对象
_provider: Any = None
_processors: list[Any] = []
_lock = threading.RLock()
_enabled = False
_error = ""
_exported = 0
_failed = 0

#: OTLP/HTTP 的 span 批量处理器参数
_SCHEDULE_DELAY_MS = 2000
_MAX_EXPORT_BATCH = 256


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for chunk in (raw or "").split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, _, value = item.partition("=")
        if key.strip():
            headers[key.strip()] = value.strip()
    return headers


def _normalize_endpoint(raw: str) -> str:
    """OTLP/HTTP 的 endpoint 必须带 ``/v1/traces`` 路径，这里做一次规整。

    用户往往只写到 ``http://localhost:4318``，而 SDK 期望完整路径；
    缺路径时导出会 404，且错误很容易被忽略 —— 所以这里替用户补全。
    """
    endpoint = raw.strip().rstrip("/")
    if not endpoint:
        return ""
    if endpoint.endswith("/v1/traces"):
        return endpoint
    return f"{endpoint}/v1/traces"


def setup(
    *,
    endpoint: str = "",
    service_name: str = "creator-agent-studio",
    headers: str = "",
    processor: Any = None,
) -> bool:
    """按配置初始化 OTLP 导出。未配置 endpoint 时直接返回 False（不 import OTel）。

    ``processor`` 可注入自定义的 span processor（测试用内存导出器），
    这样「导出链路是否正确」可以在没有 collector 的环境里被断言 ——
    与项目其它能力一致：**关键路径必须可离线验证**。
    """
    global _provider, _processors, _enabled, _error

    target = _normalize_endpoint(endpoint)
    if not target and processor is None:
        return False

    with _lock:
        if _enabled:
            return True
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider

            resource = Resource.create({"service.name": service_name})
            providers: list[Any] = []
            if processor is not None:
                providers.append(processor)
            else:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                exporter = OTLPSpanExporter(
                    endpoint=target, headers=_parse_headers(headers) or None
                )
                providers.append(
                    BatchSpanProcessor(
                        exporter,
                        schedule_delay_millis=_SCHEDULE_DELAY_MS,
                        max_export_batch_size=_MAX_EXPORT_BATCH,
                    )
                )

            # provider 本身不挂 processor —— 我们**手工构造 ReadableSpan** 后直接投给
            # processor（见 export_span 的说明）。provider 仅用于持有 resource 与优雅关闭。
            _provider = TracerProvider(resource=resource)
            _processors = providers
            _enabled = True
            _error = ""
            log.info(
                "OTLP 导出已启用："
                + (target if target else "（注入了自定义 span processor）")
                + f"（service={service_name}）"
            )
            return True
        except Exception as error:  # noqa: BLE001 - 导出失败绝不影响任务
            _enabled = False
            _error = f"{type(error).__name__}: {error}"
            log.warn(f"初始化 OTLP 导出失败，继续使用进程内追踪：{_error}")
            return False


def enabled() -> bool:
    return _enabled


def export_span(span: Any, *, agent_id: str = "") -> None:
    """把一个已收尾的进程内 span 转发到 OTel。

    ⚠️ 这里**手工构造 ``ReadableSpan``** 而不是走 ``Tracer.start_span``。
    原因：OTel 的 span id 由 SDK 生成，用 API 创建的 span 不可能带着我们自己的
    ``span_id``，导致本地 trace JSON 与 Jaeger 里的 id 对不上 ——
    排查时「按 id 去 Jaeger 查这一条」就断了。既然我们已经有完整的
    trace_id / span_id / 起止时间 / 属性，直接构造并投给 processor 最诚实：
    **本地与远端看到的完全是同一份数据**。

    任何异常都吞掉并计数 —— 观测能力不能成为新的失败面。
    """
    global _exported, _failed
    if not _enabled or not _processors or span is None:
        return
    try:
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.sdk.util.instrumentation import InstrumentationScope
        from opentelemetry.trace import SpanContext, SpanKind, TraceFlags
        from opentelemetry.trace.status import Status, StatusCode

        kind_map = {
            "internal": SpanKind.INTERNAL,
            "server": SpanKind.SERVER,
            "client": SpanKind.CLIENT,
            "producer": SpanKind.PRODUCER,
            "consumer": SpanKind.CONSUMER,
        }

        context = SpanContext(
            trace_id=int(span.trace_id, 16),
            span_id=int(span.span_id, 16),
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        parent = None
        if span.parent_span_id:
            parent = SpanContext(
                trace_id=int(span.trace_id, 16),
                span_id=int(span.parent_span_id, 16),
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )

        attributes = {key: _attr_value(value) for key, value in span.attributes.items()}
        # 智能体归属：llm.* 这类 span 自身没有 agent_id，由调用方传入其所属智能体，
        # 否则在 Jaeger 里按 agent 过滤时这些 span 会全部丢失
        owner = span.agent_id or agent_id
        if owner:
            attributes["creator.agent_id"] = owner
        attributes["creator.self_ratio"] = round(span.self_ratio, 4)

        readable = ReadableSpan(
            name=span.name,
            context=context,
            parent=parent,
            resource=_provider.resource if _provider is not None else None,
            attributes=attributes,
            kind=kind_map.get(span.kind, SpanKind.INTERNAL),
            status=(
                Status(StatusCode.ERROR, (span.status_message or "error")[:200])
                if span.status == "error"
                else Status(StatusCode.OK)
            ),
            start_time=span.start_ns,
            end_time=span.start_ns + span.duration_ms * 1_000_000,
            instrumentation_scope=InstrumentationScope("creator-agent-studio", "1.0.0"),
        )
        for processor in _processors:
            processor.on_end(readable)
        _exported += 1
    except Exception as error:  # noqa: BLE001
        _failed += 1
        log.warn(f"OTLP 导出 span 失败（不影响任务）：{type(error).__name__}: {error}")


def _attr_value(value: Any) -> Any:
    """OTel 属性只接受 str/bool/int/float 与其序列，其余统一转字符串。"""
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def stats() -> dict[str, Any]:
    return {
        "enabled": _enabled,
        "exported": _exported,
        "failed": _failed,
        "error": _error,
    }


def shutdown() -> None:
    """优雅退出：先 flush 掉 BatchSpanProcessor 里未发送的 span，再关闭。"""
    global _enabled
    with _lock:
        provider = _provider
        processors = list(_processors)
        _enabled = False
    if provider is None:
        return
    try:
        # 批量处理器是异步发送的：不 force_flush 就 shutdown 会丢掉最后一批 span
        for processor in processors:
            flush = getattr(processor, "force_flush", None)
            if callable(flush):
                flush(timeout_millis=5000)
        for processor in processors:
            close = getattr(processor, "shutdown", None)
            if callable(close):
                close()
        log.info(f"OTLP 导出已关闭（累计导出 {_exported} 个 span）")
    except Exception as error:  # noqa: BLE001
        log.warn(f"关闭 OTLP 导出失败：{error}")


__all__ = ["enabled", "export_span", "setup", "shutdown", "stats"]
