"""LLM 响应缓存（plan.md 4.5「成本熔断 + 小模型用于审核类 Agent + 缓存」）。

只缓存真实模型调用：``(provider, model, temperature, max_tokens, messages)``
完全一致的重复请求直接复用上次结果。收益有两个：

* 省钱 —— 同一任务重跑同一节点（断点续跑、人工打回后重试）不再二次付费；
* 可预期 —— 缓存命中是零延迟，避免重复的秒级等待。

离线引擎本身不产生费用，因此不进缓存。
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..logger import create_logger
from .types import LLMRequest, LLMResponse

log = create_logger("llm.cache")

#: 条目上限与存活时间；超出上限按插入顺序淘汰最旧的条目
MAX_ENTRIES = 256
TTL_SECONDS = 3600


@dataclass
class _Entry:
    response: LLMResponse
    created_at: float
    hits: int = 0


class ResponseCache:
    def __init__(self, max_entries: int = MAX_ENTRIES, ttl_seconds: int = TTL_SECONDS) -> None:
        self._entries: dict[str, _Entry] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._max = max_entries
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    @staticmethod
    def key(provider: str, model: str, request: LLMRequest) -> str:
        """请求指纹。含 temperature / max_tokens，避免不同采样参数互相污染。

        ``max_tokens`` 为 None 时用**当前生效的上限**（含截断重试的放大值）参与指纹 ——
        否则「放大后重试」会命中放大前的缓存，重试拿回同一份被截断的输出。
        这里延迟 import 以避免 ``cache -> engine -> cache`` 的循环依赖。
        """
        from .engine import effective_max_tokens

        payload = json.dumps(
            {
                "provider": provider,
                "model": model,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens or effective_max_tokens(),
                "json": request.json,
                "messages": [[m.role, m.content] for m in request.messages],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> LLMResponse | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.time() - entry.created_at > self._ttl:
                self._drop(key)
                self._misses += 1
                return None
            entry.hits += 1
            self._hits += 1
            return self._copy(entry.response)

    def put(self, key: str, response: LLMResponse) -> None:
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                existing.created_at = time.time()
                return
            self._entries[key] = _Entry(response=self._copy(response), created_at=time.time())
            self._order.append(key)
            while len(self._order) > self._max:
                self._drop(self._order[0])

    def _drop(self, key: str) -> None:
        self._entries.pop(key, None)
        if key in self._order:
            self._order.remove(key)

    @staticmethod
    def _copy(response: LLMResponse) -> LLMResponse:
        """深拷贝并标记 ``cached``：调用方拿到的永远是副本，改不坏缓存。"""
        clone = copy.deepcopy(response)
        clone.cached = True
        return clone

    def clear(self) -> int:
        with self._lock:
            size = len(self._entries)
            self._entries.clear()
            self._order.clear()
            return size

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._entries),
                "capacity": self._max,
                "ttlSeconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hitRate": round(self._hits / total, 4) if total else 0.0,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0


response_cache = ResponseCache()
