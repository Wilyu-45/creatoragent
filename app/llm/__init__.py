"""模型接入层：OpenAI 兼容协议 + 内置离线 Mock 引擎。

对上层只暴露 ``chat()``：内部完成「重试 → 降级」两级容错，
保证任何情况下都能返回可用于流水线的响应（无 API Key 也能整条链路跑通）。
"""

from .engine import chat, current_provider_name, resolve_provider, vision_enabled
from .types import (
    ChatMessage,
    ImagePart,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    estimate_tokens,
)

__all__ = [
    "chat",
    "resolve_provider",
    "current_provider_name",
    "vision_enabled",
    "ChatMessage",
    "ImagePart",
    "LLMRequest",
    "LLMResponse",
    "LLMUsage",
    "estimate_tokens",
]
