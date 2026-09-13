"""模型计价表（plan.md 2.4「单篇内容成本」指标）。

从 ``agents/base.py`` 抽出到 llm 层，让「智能体计费」与「成本熔断」
共用同一张价目表，同时避免 ``agents -> llm -> agents`` 的循环依赖。

⚠️ 为什么要建模「输入缓存命中」这一维
------------------------------------
DeepSeek 的输入侧分两档价：**缓存命中比未命中便宜约 50 倍**
（2026-09-10 起 flash 系列：空闲时段缓存命中 ¥0.02/M、未命中 ¥1/M、输出 ¥4/M，
高峰时段为空闲时段 2 倍 —— 见 https://finance.eastmoney.com/a/202609093869233423.html ）。

只用「一个输入单价」会把成本算错一个量级：本项目大量提示词是**固定前缀**
（系统提示词 + 共享规则 + 渠道规范），天然命中缓存。忽略它会让成本严重高估，
从而**过早触发熔断**、把本该走真实模型的任务降级到离线引擎。
因此 ``price_of`` 返回 ``(miss, hit, out)`` 三元组，``cost_of`` 按实际命中量计价。

缓存命中量从哪来
----------------
``LLMResponse`` 里 OpenAI 协议会回报 ``prompt_tokens_details.cached_tokens``；
本项目把它透传为 ``usage.cached_tokens``。缺失时按 0 处理（保守：宁可高估成本）。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 每百万 token 的美元单价。三元组 = (输入未命中, 输入缓存命中, 输出)
#: 人民币价按 ≈7.1 折算；改动前请核对官方定价页。
PRICING: dict[str, tuple[float, float, float]] = {
    "gpt-4o-mini": (0.15, 0.075, 0.6),
    "gpt-4o": (2.5, 1.25, 10.0),
    "gpt-4.1-mini": (0.4, 0.2, 1.6),
    # DeepSeek 2026-09-10 定价（空闲时段）：输入未命中 ¥1、缓存命中 ¥0.02、输出 ¥4
    "deepseek-flash": (0.14, 0.003, 0.56),
    "deepseek-v4-flash": (0.14, 0.003, 0.56),
    "deepseek-v4-pro": (0.4, 0.02, 1.6),
    # 兼容旧模型名（deepseek-chat 现由 flash 承载）
    "deepseek-chat": (0.14, 0.003, 0.56),
    "deepseek-reasoner": (0.55, 0.14, 2.2),
    "qwen-plus": (0.4, 0.2, 1.2),
    "qwen-max": (1.6, 0.8, 6.4),
}

#: 未命中价目表时的保守估价：宁可高估触发熔断，也不要低估让预算失控
DEFAULT_PRICE = (0.5, 0.25, 1.5)

#: 价目表版本：成本口径变更时应递增，便于解释「为什么同一任务的成本变了」
PRICING_VERSION = "2026-09-13.deepseek-v4"


@dataclass(frozen=True)
class Price:
    """一个模型的三档单价（美元 / 百万 token）。"""

    input_miss: float
    input_hit: float
    output: float
    matched: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "inputMiss": self.input_miss,
            "inputHit": self.input_hit,
            "output": self.output,
            "matched": self.matched,
        }


def price_of(model: str) -> Price:
    """返回该模型的单价。

    匹配顺序：**先长名后短名**，避免 ``deepseek-v4-pro`` 被 ``deepseek-v4`` 这类
    前缀规则抢先命中（价差可达 3 倍）。
    """
    lowered = (model or "").lower()
    for key in sorted(PRICING, key=len, reverse=True):
        if key in lowered:
            miss, hit, out = PRICING[key]
            return Price(miss, hit, out, matched=key)
    miss, hit, out = DEFAULT_PRICE
    return Price(miss, hit, out, matched="")


def cost_of(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cached_tokens: int = 0,
) -> float:
    """按 token 用量折算美元成本（保留 6 位，避免浮点噪声写进任务文件）。

    ``cached_tokens`` 是 ``prompt_tokens`` 中**命中缓存**的部分，
    按低价档计费；传 0 时退化为「全部按未命中价」，即最保守的算法。
    """
    price = price_of(model)
    cached = max(0, min(int(cached_tokens), int(prompt_tokens)))
    uncached = max(0, int(prompt_tokens) - cached)
    total = (
        (uncached / 1_000_000) * price.input_miss
        + (cached / 1_000_000) * price.input_hit
        + (completion_tokens / 1_000_000) * price.output
    )
    return round(total, 6)


def describe_pricing() -> dict[str, object]:
    """供 ``/api/health`` 与界面展示当前计价口径。"""
    return {
        "version": PRICING_VERSION,
        "models": {key: list(value) for key, value in PRICING.items()},
        "default": list(DEFAULT_PRICE),
        "note": "输入分「缓存命中 / 未命中」两档；未命中价目表时用保守估价",
    }


__all__ = [
    "DEFAULT_PRICE",
    "PRICING",
    "PRICING_VERSION",
    "Price",
    "cost_of",
    "describe_pricing",
    "price_of",
]
