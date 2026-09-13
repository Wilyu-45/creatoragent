"""模型计价表（plan.md 2.4「单篇内容成本」指标）。

从 ``agents/base.py`` 抽出到 llm 层，让「智能体计费」与「成本熔断」
共用同一张价目表，同时避免 ``agents -> llm -> agents`` 的循环依赖。
"""

from __future__ import annotations

#: 每百万 token 的美元单价（in / out）
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4o": (2.5, 10.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "deepseek-chat": (0.27, 1.1),
    "qwen-plus": (0.4, 1.2),
    "qwen-max": (1.6, 6.4),
}

#: 未命中价目表时的保守估价：宁可高估触发熔断，也不要低估让预算失控
DEFAULT_PRICE = (0.5, 1.5)


def price_of(model: str) -> tuple[float, float]:
    """返回该模型的 (输入单价, 输出单价)，单位：美元 / 百万 token。"""
    lowered = (model or "").lower()
    for key, price in PRICING.items():
        if key in lowered:
            return price
    return DEFAULT_PRICE


def cost_of(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """按 token 用量折算美元成本（保留 6 位，避免浮点噪声写进任务文件）。"""
    price_in, price_out = price_of(model)
    total = (prompt_tokens / 1_000_000) * price_in + (completion_tokens / 1_000_000) * price_out
    return round(total, 6)
