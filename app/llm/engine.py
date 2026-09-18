"""统一调用入口与多级容错（移植自 server/llm/index.ts）。

   1. 命中响应缓存 → 直接复用，不产生二次费用（plan.md 4.5「缓存」）
   2. 网络 / 5xx / 超时 / 429 → 指数退避重试（最多 3 次）
   3. 仍然失败 → 降级到内置离线引擎，保证流程不中断（plan.md D10 降级策略）
   4. 成本超预算 → 熔断到离线引擎，守住单任务成本上限（plan.md D12）
"""

from __future__ import annotations

import ipaddress
import re
import threading
import time
from urllib.parse import urlsplit

from ..config import LLMSettings, get_config, llm_settings_for
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

#: 输出截断后的重试加成（thread-local）。
#: 真实网关下 A3/A11 这类「结构化长输出」很容易撞上 max_tokens，
#: 而它们的提示词本身是好的 —— 只需给更多输出空间。
#: 用 thread-local 而不是给每个智能体加参数：编排线程与任务一一对应，
#: 这样「重试时放大 token 上限」这件事对智能体完全透明。
_MAX_TOKENS_BOOST_RATIO = 2.0
_MAX_TOKENS_CEILING = 16384
_token_boost = threading.local()


def boost_max_tokens(factor: float = _MAX_TOKENS_BOOST_RATIO, *, ceiling: int = _MAX_TOKENS_CEILING) -> int:
    """放大当前线程的输出上限，返回放大后的值（供日志展示）。"""
    base = int(getattr(_token_boost, "value", 0) or 0)
    source = base or get_config().llm.max_tokens
    boosted = min(ceiling, max(source + 1, int(source * factor)))
    _token_boost.value = boosted
    return boosted


def reset_max_tokens() -> None:
    """清除放大（任务结束或成功返回后调用，避免污染后续步骤）。"""
    _token_boost.value = 0


def effective_max_tokens() -> int:
    """当前线程实际使用的输出上限。"""
    return int(getattr(_token_boost, "value", 0) or 0) or get_config().llm.max_tokens

#: 本地推理服务通常不需要密钥，这里放宽判定。
_NEEDS_KEY_RE = re.compile(
    r"api\.openai\.com|dashscope|volces|moonshot|deepseek\.com", re.IGNORECASE
)

_RETRIABLE_RE = re.compile(
    r"HTTP (429|5\d\d)|abort|timeout|timed out|network|fetch failed|ECONN|返回空内容",
    re.IGNORECASE,
)

MAX_ATTEMPTS = 3


def _needs_api_key(base_url: str) -> bool:
    return bool(_NEEDS_KEY_RE.search(base_url))


def _is_local_endpoint(base_url: str) -> bool:
    """是否指向本机 / 私网推理端点（Ollama、LM Studio、vLLM、自建网关等）。

    本地端点的硬件与电费归使用者所有，没有可依据的云单价——成本按 0 计，
    避免 ``pricing.DEFAULT_PRICE`` 的保守估价把免费模型误判成超预算熔断。
    判不出来（畸形 URL / 无 host）时按远端处理：宁可保守计费。
    """
    try:
        host = (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


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


def vision_enabled(agent_id: str = "") -> bool:
    """当前是否具备多模态（图像）输入能力。

    离线引擎**永远**返回 False：它只消费 ``context`` 结构化数据，根本不读
    ``messages``，给它塞图片块只会污染 token 估算口径（黄金基线的前提是
    「同一输入产出同一结果」，图片字节不该出现在这条路上）。

    ``LLM_VISION`` 默认关闭：文本模型（DeepSeek chat/flash、qwen-plus、本地 Ollama
    文本模型等）收到内容块数组会返回 400，且该失败不可重试，只会让智能体降级到
    离线引擎。确认模型支持读图后再打开——素材的**文字清单**不受此项影响。

    ``agent_id`` 指向每智能体模型覆盖：不同智能体可能指向不同的模型/端点，
    读图能力必须按该智能体**实际生效**的设置判定（空串 = 全局设置）。
    """
    if not get_config().llm.vision:
        return False
    return not getattr(resolve_provider(llm_settings_for(agent_id)), "simulated", False)


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
        _finish_llm_span(span, fallback, degraded="cost_cut", agent_id=request.agent_id)
        return fallback

    # 每智能体模型覆盖：按 request.agent_id 合并设置（无覆盖时即全局设置）
    eff_settings = llm_settings_for(request.agent_id)
    provider = resolve_provider(eff_settings)
    if getattr(provider, "simulated", False):
        response = _mock.chat(request)
        _finish_llm_span(span, response, degraded="simulated", agent_id=request.agent_id)
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
            # 仅按错误信息判定可重试性：网络/超时/5xx/空内容重试，400 等参数错误不重试。
            # 「返回空内容」看似确定失败，但 real_check 实测 deepseek-flash 会间歇性
            # 返回空 content（瞬态故障），重试通常即可恢复；3 次仍空才降级离线引擎。
            retriable = bool(_RETRIABLE_RE.search(message))
            log.warn(f"[{request.purpose}] 第 {attempt}/{MAX_ATTEMPTS} 次调用失败：{message}")
            if not retriable or attempt == MAX_ATTEMPTS:
                break
            time.sleep(0.4 * 2 ** (attempt - 1))

    if response is not None:
        usage = response.usage
        # 本地端点（Ollama/LM Studio/vLLM 等）硬件归用户所有：成本按 0 计，
        # 但 token 必须照记——charge 同时驱动 token 熔断，跳过会让预算彻底失效。
        response.local = _is_local_endpoint(eff_settings.base_url)
        cost_guard.charge(
            usage.prompt_tokens,
            usage.completion_tokens,
            0.0
            if response.local
            else cost_of(
                response.model or getattr(provider, "model", ""),
                usage.prompt_tokens,
                usage.completion_tokens,
                # 缓存命中的输入按低价档计费（DeepSeek 差价约 50 倍）
                cached_tokens=usage.cached_tokens,
            ),
            cached_tokens=usage.cached_tokens,
        )
        if cache_key is not None:
            response_cache.put(cache_key, response)
        _finish_llm_span(span, response, attempts=attempts, agent_id=request.agent_id)
        return response

    reason = str(last_error)
    log.error(f"[{request.purpose}] 真实模型不可用，降级到内置离线引擎：{reason}")
    fallback = _mock.chat(request)
    fallback.degraded_reason = reason[:200]
    _finish_llm_span(span, fallback, attempts=attempts, degraded="provider_error", agent_id=request.agent_id)
    return fallback


def _finish_llm_span(
    span,
    response: LLMResponse,
    *,
    attempts: int = 1,
    cache_hit: bool = False,
    degraded: str = "",
    agent_id: str = "",
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
            "llm.agent_id": agent_id,
            "llm.local": response.local,
            "llm.prompt_tokens": usage.prompt_tokens,
            "llm.cached_tokens": usage.cached_tokens,
            "llm.completion_tokens": usage.completion_tokens,
            "llm.cached": cache_hit or response.cached,
            "llm.simulated": response.simulated,
            "llm.attempts": attempts,
            "llm.degraded": degraded or (response.degraded_reason or ""),
            # 本地端点成本恒为 0（硬件归用户），与 cost_guard 的计费口径一致
            "llm.cost_usd": (
                0.0
                if response.local
                else round(
                    cost_of(
                        response.model,
                        usage.prompt_tokens,
                        usage.completion_tokens,
                        cached_tokens=usage.cached_tokens,
                    ),
                    6,
                )
            ),
        },
    )


mock_provider = _mock
