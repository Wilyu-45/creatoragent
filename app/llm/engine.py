"""统一调用入口与多级容错（移植自 server/llm/index.ts）。

   1. 命中响应缓存 → 直接复用，不产生二次费用（plan.md 4.5「缓存」）
   2. 网络 / 5xx / 超时 / 429 → 指数退避重试（最多 3 次）
   3. 仍然失败 → 降级到内置离线引擎，保证流程不中断（plan.md D10 降级策略）
   4. 成本超预算 → 熔断到离线引擎，守住单任务成本上限（plan.md D12）
"""

from __future__ import annotations

import re
import time

from ..config import LLMSettings, get_config
from ..core.tracing import tracer
from ..logger import create_logger
from .cache import response_cache
from .cost import cost_guard
from .mock import MockProvider
from .openai_provider import OpenAICompatibleProvider
from .pricing import cost_of
from .types import LLMProvider, LLMRequest, LLMResponse

log = create_logger("llm")

_mock = MockProvider()

#: 本地推理服务通常不需要密钥，这里放宽判定。
_NEEDS_KEY_RE = re.compile(
    r"api\.openai\.com|dashscope|volces|moonshot|deepseek\.com", re.IGNORECASE
)

_RETRIABLE_RE = re.compile(
    r"HTTP (429|5\d\d)|abort|timeout|timed out|network|fetch failed|ECONN",
    re.IGNORECASE,
)

MAX_ATTEMPTS = 3


def _needs_api_key(base_url: str) -> bool:
    return bool(_NEEDS_KEY_RE.search(base_url))


def resolve_provider(settings: LLMSettings | None = None) -> LLMProvider:
    """按当前设置挑选提供方；任何异常都回退到离线引擎，绝不抛给上层。"""
    cfg = settings or get_config().llm
    if cfg.provider != "openai":
        return _mock

    base_url = (cfg.base_url or "").strip()
    if not base_url:
        return _mock
    if _needs_api_key(base_url) and not cfg.api_key:
        log.warn("LLM_PROVIDER=openai 但未配置 API Key，已回退到内置离线引擎")
        return _mock
    try:
        return OpenAICompatibleProvider(cfg)
    except Exception as error:  # noqa: BLE001
        log.error("初始化 OpenAI 兼容提供方失败，回退到离线引擎", error)
        return _mock


def current_provider_name() -> str:
    """用于 ``/api/health`` 上报当前实际生效的提供方。"""
    provider = resolve_provider()
    return getattr(provider, "name", "mock")


def chat(request: LLMRequest) -> LLMResponse:
    """统一模型调用入口；每次调用都会产生一个 ``llm.<purpose>`` span。

    span 上记录 provider / model / 命中缓存 / token / 成本 / 降级原因，
    因此「这次任务的 6 秒花在哪、钱花在哪」可以直接从 trace 树读出来，
    不需要再去日志里拼时间戳。
    """
    span = tracer.start_span(
        f"llm.{request.purpose}",
        kind="client",
        attributes={"llm.purpose": request.purpose, "llm.json": request.json},
    )

    # 1) 成本熔断：本任务已超预算，后续步骤一律走离线引擎，不再产生费用
    if cost_guard.should_cut():
        fallback = _mock.chat(request)
        fallback.degraded_reason = "成本熔断：本任务已达成本预算，后续步骤改用内置离线引擎"
        _finish_llm_span(span, fallback, degraded="cost_cut")
        return fallback

    provider = resolve_provider()
    if getattr(provider, "simulated", False):
        response = _mock.chat(request)
        _finish_llm_span(span, response, degraded="simulated")
        return response

    cache_key: str | None = None
    if get_config().llm_cache:
        cache_key = response_cache.key(
            getattr(provider, "name", "unknown"), getattr(provider, "model", ""), request
        )
        cached = response_cache.get(cache_key)
        if cached is not None:
            cached.latency_ms = 0
            log.info(f"[{request.purpose}] 命中响应缓存，跳过模型调用")
            _finish_llm_span(span, cached, cache_hit=True)
            return cached

    last_error: Exception | None = None
    response: LLMResponse | None = None
    attempts = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        try:
            response = provider.chat(request)
            break
        except Exception as error:  # noqa: BLE001
            last_error = error
            message = str(error)
            # 与旧实现一致：仅按错误信息判定可重试性。
            # 400 / 参数错误 / 空内容属于确定性失败，重试只是浪费额度。
            retriable = bool(_RETRIABLE_RE.search(message))
            log.warn(f"[{request.purpose}] 第 {attempt}/{MAX_ATTEMPTS} 次调用失败：{message}")
            if not retriable or attempt == MAX_ATTEMPTS:
                break
            time.sleep(0.4 * 2 ** (attempt - 1))

    if response is not None:
        usage = response.usage
        cost_guard.charge(
            usage.prompt_tokens,
            usage.completion_tokens,
            cost_of(
                response.model or getattr(provider, "model", ""),
                usage.prompt_tokens,
                usage.completion_tokens,
            ),
        )
        if cache_key is not None:
            response_cache.put(cache_key, response)
        _finish_llm_span(span, response, attempts=attempts)
        return response

    reason = str(last_error)
    log.error(f"[{request.purpose}] 真实模型不可用，降级到内置离线引擎：{reason}")
    fallback = _mock.chat(request)
    fallback.degraded_reason = reason[:200]
    _finish_llm_span(span, fallback, attempts=attempts, degraded="provider_error")
    return fallback


def _finish_llm_span(
    span,
    response: LLMResponse,
    *,
    attempts: int = 1,
    cache_hit: bool = False,
    degraded: str = "",
) -> None:
    """把模型调用的关键信息写进 span。

    降级与缓存命中都记为 ``ok`` 而不是 ``error``：它们是被设计出来的正常路径
    （plan.md D10/D12），记成错误会让错误率失去意义。
    """
    usage = response.usage
    tracer.finish_span(
        span,
        status="ok",
        attributes={
            "llm.provider": response.provider,
            "llm.model": response.model,
            "llm.prompt_tokens": usage.prompt_tokens,
            "llm.completion_tokens": usage.completion_tokens,
            "llm.cached": cache_hit or response.cached,
            "llm.simulated": response.simulated,
            "llm.attempts": attempts,
            "llm.degraded": degraded or (response.degraded_reason or ""),
            "llm.cost_usd": round(
                cost_of(response.model, usage.prompt_tokens, usage.completion_tokens), 6
            ),
        },
    )


mock_provider = _mock
