"""任意 OpenAI 兼容 /chat/completions 接口（移植自 server/llm/openai.ts）。

已验证可对接：OpenAI、DeepSeek、通义千问、豆包、Moonshot、vLLM、Ollama、LM Studio。

刻意不使用 ``response_format=json_object`` —— 部分兼容实现不支持该字段会直接报错，
统一改为在提示词中约束 + 客户端稳健解析（见 ``json_utils.extract_json``）。

实现上直接走 HTTP（httpx），而非 openai SDK：
一是与旧 Node 实现逐字对齐（含错误串格式，重试判定依赖它），
二是避免 SDK 大版本变动带来的隐性行为差异。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import LLMSettings
from .types import LLMRequest, LLMResponse, LLMUsage, estimate_tokens


class LLMError(RuntimeError):
    """模型调用失败。message 形如 ``HTTP 429 Too Many Requests ...``，供上层判定是否可重试。"""


class OpenAICompatibleProvider:
    name = "openai"
    simulated = False

    def __init__(self, settings: LLMSettings) -> None:
        self.model = settings.model
        self._base_url = settings.base_url.rstrip("/")
        self._api_key = settings.api_key
        self._temperature = settings.temperature
        self._max_tokens = settings.max_tokens
        self._timeout_ms = settings.timeout_ms

    def chat(self, request: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature if request.temperature is not None else self._temperature,
            "max_tokens": request.max_tokens if request.max_tokens is not None else self._max_tokens,
            "stream": False,
        }

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                json=payload,
                timeout=self._timeout_ms / 1000,
            )
        except httpx.TimeoutException as error:
            raise LLMError(f"timeout: {error}") from error
        except httpx.HTTPError as error:
            raise LLMError(f"network error: {error}") from error

        if not (200 <= response.status_code < 300):
            detail = response.text[:300]
            raise LLMError(f"HTTP {response.status_code} {response.reason_phrase} {detail}")

        try:
            data = response.json()
        except ValueError as error:
            raise LLMError(f"invalid JSON payload: {error}") from error

        choices = data.get("choices") or []
        content = ""
        if choices:
            content = ((choices[0].get("message") or {}).get("content")) or ""
        if not content.strip():
            raise LLMError("模型返回空内容")

        usage = data.get("usage") or {}
        prompt_text = "\n".join(m.content for m in request.messages)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=data.get("model") or self.model,
            usage=LLMUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or estimate_tokens(prompt_text)),
                completion_tokens=int(usage.get("completion_tokens") or estimate_tokens(content)),
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
            simulated=False,
        )
