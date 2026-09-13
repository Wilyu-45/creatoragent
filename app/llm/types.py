"""LLM 层数据结构（移植自 server/llm/types.ts）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.util import js_round

ChatRole = str  # 'system' | 'user' | 'assistant'

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class ChatMessage:
    role: ChatRole
    content: str


@dataclass
class LLMRequest:
    #: 调用用途，形如 ``A4.copy``。Mock 引擎据此选择生成器，真实模型据此打点。
    purpose: str
    messages: list[ChatMessage] = field(default_factory=list)
    #: 结构化上下文，Mock 引擎从中取材
    context: dict[str, Any] = field(default_factory=dict)
    temperature: float | None = None
    max_tokens: int | None = None
    #: 期望返回 JSON
    json: bool = False


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class LLMResponse:
    content: str = ""
    provider: str = ""
    model: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: int = 0
    #: True 表示来自内置离线模拟引擎或降级兜底
    simulated: bool = False
    #: True 表示命中响应缓存（未真正产生一次模型调用）
    cached: bool = False
    #: 降级说明（例如真实模型不可用、成本熔断）
    degraded_reason: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    simulated: bool

    def chat(self, request: LLMRequest) -> LLMResponse: ...


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文按字符、英文按 4 字符/token。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    return max(1, js_round(cjk + rest / 4))
