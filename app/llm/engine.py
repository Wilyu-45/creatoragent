"""统一调用入口与两级容错（移植自 server/llm/index.ts）。

   1. 网络 / 5xx / 超时 / 429 → 指数退避重试（最多 3 次）
   2. 仍然失败 → 降级到内置离线引擎，保证流程不中断（plan.md D10 降级策略）
"""

from __future__ import annotations

import re
import time

from ..config import LLMSettings, get_config
from ..logger import create_logger
from .mock import MockProvider
from .openai_provider import OpenAICompatibleProvider
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
    provider = resolve_provider()
    if getattr(provider, "simulated", False):
        return _mock.chat(request)

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return provider.chat(request)
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

    reason = str(last_error)
    log.error(f"[{request.purpose}] 真实模型不可用，降级到内置离线引擎：{reason}")
    fallback = _mock.chat(request)
    fallback.degraded_reason = reason[:200]
    return fallback


mock_provider = _mock
