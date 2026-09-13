"""文本向量化：本地确定性 hashing embedding + 可选 OpenAI 兼容 ``/embeddings``。

对应 plan.md 2.2.3「RAG Server」与 creator.md A11「知识层」的向量检索能力。
设计目标是**离线可用、可平滑升级、永不因 embedding 服务故障而中断检索**：

1. **零依赖可用**：默认 ``provider=local``，用 hashing trick 把字符 bigram / 词
   映射到固定维度并对向量做 L2 归一化。同一文本永远得到同一向量，无需模型文件；
   符号哈希（sign hashing）把碰撞的方差摊平，避免同桶叠加导致方向失真。
2. **可升级**：``provider=openai`` 时改调任意 OpenAI 协议网关的 ``/embeddings``
   （OpenAI / DeepSeek / 通义 / vLLM / Ollama 均兼容）。
3. **失败回退**：远端报错或返回条数/维度异常时，静默回退到本地向量，检索照常返回。

检索时 ``memory.py`` 把「关键词得分」与「向量余弦」按 ``embedding.weight`` 混合，
权重为 0 时退化为纯关键词检索，与历史行为完全一致。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import httpx

from ..config import EmbeddingSettings, get_config
from ..logger import create_logger

log = create_logger("embedding")

#: 本地向量默认维度（hashing trick 的桶数）
DEFAULT_DIM = 256
#: 中文 n-gram 长度（与 memory.py 的关键词口径保持一致）
NGRAM = 2

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """中英混合分词：英文/数字按词，中文按 ``NGRAM`` 长度滑窗。

    与记忆库的关键词打分共用同一口径，保证关键词分量与向量分量语义一致。
    """
    lowered = (text or "").lower()
    tokens = _WORD_RE.findall(lowered)
    cjk = _CJK_RE.findall(lowered)
    if len(cjk) >= NGRAM:
        tokens.extend("".join(pair) for pair in zip(cjk, cjk[1:]))
    else:
        tokens.extend(cjk)
    return tokens


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        return vector
    return [value / norm for value in vector]


def hashing_vector(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """把文本映射为 L2 归一化的稠密向量（确定性、无外部依赖）。"""
    if dim <= 0:
        return []
    vector = [0.0] * dim
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    return _l2_normalize(vector)


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；维度不一致视为不可比（返回 0，不会误加分）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, dot))


# ------------------------------------------------------------------ #
# 提供方                                                              #
# ------------------------------------------------------------------ #


class LocalEmbeddingProvider:
    """本地 hashing embedding：零依赖、确定性、离线可用。"""

    name = "local"
    simulated = True

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hashing_vector(text, self.dim) for text in texts]


class OpenAIEmbeddingProvider:
    """OpenAI 兼容 ``/embeddings`` 客户端。"""

    name = "openai"
    simulated = False

    def __init__(self, base_url: str, api_key: str, model: str, timeout_ms: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = max(1.0, timeout_ms / 1000.0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "input": texts},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        # 服务端可能乱序返回，按 index 归位
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return [list(item.get("embedding") or []) for item in ordered]


def _resolve_provider() -> LocalEmbeddingProvider | OpenAIEmbeddingProvider:
    cfg = get_config()
    embedding: EmbeddingSettings = cfg.embedding
    if embedding.provider != "openai":
        return LocalEmbeddingProvider(embedding.dim)
    base_url = (embedding.base_url or cfg.llm.base_url or "").strip()
    api_key = embedding.api_key or cfg.llm.api_key
    if not base_url or not api_key:
        log.warn("embedding 配置为 openai 但缺少 base_url/api_key，已回退本地向量")
        return LocalEmbeddingProvider(embedding.dim)
    return OpenAIEmbeddingProvider(base_url, api_key, embedding.model, cfg.llm.timeout_ms)


def describe() -> dict[str, Any]:
    """检索元信息，供 ``/api/memory``、``/api/metrics`` 展示。"""
    cfg = get_config()
    return {
        "provider": cfg.embedding.provider,
        "model": cfg.embedding.model if cfg.embedding.provider == "openai" else "local-hashing",
        "dim": cfg.embedding.dim,
        "weight": cfg.embedding.weight,
    }


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化；任何异常都回退本地向量，保证调用方无需处理失败。"""
    if not texts:
        return []
    dim = get_config().embedding.dim
    provider = _resolve_provider()
    if getattr(provider, "simulated", True):
        return [hashing_vector(text, dim) for text in texts]
    try:
        vectors = provider.embed(texts)
    except Exception as error:  # noqa: BLE001 - 兜底回退，检索不能因 embedding 挂了而失败
        log.warn("embedding 提供方调用失败，回退本地向量", error)
        return [hashing_vector(text, dim) for text in texts]
    if len(vectors) != len(texts) or len({len(vector) for vector in vectors}) != 1:
        log.warn(f"embedding 返回异常（{len(vectors)} 条 / 期望 {len(texts)} 条），回退本地向量")
        return [hashing_vector(text, dim) for text in texts]
    return vectors
