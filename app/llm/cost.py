"""成本核算与熔断（plan.md D12「Turn Budget + 成本熔断机制」、2.4 监控指标）。

设计要点：

* **一任务一账本**（``CostLedger``），与「一个任务一个线程」的编排模型对齐；
  用 thread-local 记录当前任务，使 ``llm.chat`` 这种无状态底层函数也能计费 / 熔断。
* **熔断而非抛错**：超预算时不再中断任务，而是让后续步骤强制走离线引擎
  （``should_cut`` 返回 True），既守住成本上限，又保证交付链路不中断，
  与 D10「降级策略」保持同一套语义。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..logger import create_logger

log = create_logger("llm.cost")

#: 预算使用到该比例时预警（plan.md 2.4 的「> 预算 120% 熔断」在此取更保守的 80%）
WARN_RATIO = 0.8


@dataclass
class CostLedger:
    """单任务成本账本。"""

    task_id: str
    budget_usd: float = 0.0
    token_budget: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    cached: int = 0
    #: 提供方侧输入缓存命中的 token 数（DeepSeek 等按低价档计费）
    provider_cached_tokens: int = 0
    cut_off: bool = False
    warned: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "tokens": self.tokens,
            "costUsd": round(self.cost_usd, 6),
            "calls": self.calls,
            "cached": self.cached,
            "providerCachedTokens": self.provider_cached_tokens,
            "budgetUsd": self.budget_usd,
            "tokenBudget": self.token_budget,
            "cutOff": self.cut_off,
            "notes": list(self.notes),
        }


class CostGuard:
    """按任务累计成本，并在超预算时熔断到离线引擎。"""

    def __init__(self) -> None:
        self._ledgers: dict[str, CostLedger] = {}
        self._local = threading.local()
        self._lock = threading.RLock()
        self._cut_off_tasks = 0

    # --------------------------- 账本生命周期 --------------------------- #

    def begin(self, task_id: str, *, budget_usd: float, token_budget: int) -> CostLedger:
        """为新任务开账本并绑定到当前线程。"""
        ledger = CostLedger(task_id=task_id, budget_usd=budget_usd, token_budget=token_budget)
        with self._lock:
            self._ledgers[task_id] = ledger
        self.bind(task_id)
        return ledger

    def bind(self, task_id: str) -> None:
        """把当前线程绑定到某任务账本（一个任务一个线程）。"""
        self._local.task_id = task_id

    def unbind(self) -> None:
        self._local.task_id = None

    def end(self, task_id: str) -> CostLedger | None:
        """任务终态时取走账本（保留统计，不占内存）。"""
        with self._lock:
            ledger = self._ledgers.pop(task_id, None)
        if getattr(self._local, "task_id", None) == task_id:
            self.unbind()
        return ledger

    def current(self) -> CostLedger | None:
        task_id = getattr(self._local, "task_id", None)
        if not task_id:
            return None
        with self._lock:
            return self._ledgers.get(task_id)

    def ledger(self, task_id: str) -> CostLedger | None:
        with self._lock:
            return self._ledgers.get(task_id)

    # ------------------------------ 计费 ------------------------------- #

    def charge(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        *,
        cached: bool = False,
        cached_tokens: int = 0,
    ) -> None:
        """记一笔账；未绑定任务时静默跳过（例如脚本直接调 ``chat``）。"""
        ledger = self.current()
        if ledger is None:
            return
        ledger.prompt_tokens += max(0, prompt_tokens)
        ledger.completion_tokens += max(0, completion_tokens)
        # 提供方侧的输入缓存命中量单独累计：它与「本地响应缓存」是两件事，
        # 前者是同一前缀在厂商侧被复用（便宜），后者是本进程直接没有发起调用。
        ledger.provider_cached_tokens += max(0, min(int(cached_tokens), int(prompt_tokens)))
        ledger.cost_usd = round(ledger.cost_usd + max(0.0, cost_usd), 6)
        ledger.calls += 1
        if cached:
            ledger.cached += 1

    def should_cut(self, ledger: CostLedger | None = None) -> bool:
        """判断是否应当熔断；首次触发时返回 True 并记账。"""
        target = ledger if ledger is not None else self.current()
        if target is None:
            return False
        if target.cut_off:
            return True

        over_cost = target.budget_usd > 0 and target.cost_usd >= target.budget_usd
        over_token = target.token_budget > 0 and target.tokens >= target.token_budget
        if over_cost or over_token:
            target.cut_off = True
            reason = (
                f"成本 ${target.cost_usd:.4f} 已达预算 ${target.budget_usd:.2f}"
                if over_cost
                else f"token {target.tokens} 已达上限 {target.token_budget}"
            )
            target.notes.append(f"成本熔断：{reason}")
            with self._lock:
                self._cut_off_tasks += 1
            log.warn(f"任务 {target.task_id} 触发成本熔断（{reason}），后续步骤改用内置离线引擎")
            return True

        if not target.warned and target.budget_usd > 0:
            if target.cost_usd >= target.budget_usd * WARN_RATIO:
                target.warned = True
                log.warn(
                    f"任务 {target.task_id} 成本已达预算 {WARN_RATIO:.0%}"
                    f"（${target.cost_usd:.4f}/${target.budget_usd:.2f}）"
                )
        return False

    # ------------------------------ 查询 ------------------------------- #

    def snapshot(self, task_id: str) -> dict[str, Any]:
        ledger = self.ledger(task_id)
        if ledger is None:
            return {
                "taskId": task_id,
                "promptTokens": 0,
                "completionTokens": 0,
                "tokens": 0,
                "costUsd": 0.0,
                "calls": 0,
                "cached": 0,
                "budgetUsd": 0.0,
                "tokenBudget": 0,
                "cutOff": False,
                "notes": [],
            }
        return ledger.to_dict()

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            active = len(self._ledgers)
        return {
            "cutOffTasks": self._cut_off_tasks,
            "activeLedgers": active,
        }

    def reset_stats(self) -> None:
        with self._lock:
            self._cut_off_tasks = 0


cost_guard = CostGuard()
