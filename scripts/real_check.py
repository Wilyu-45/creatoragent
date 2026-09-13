"""真实网关链路核验：跑一条完整任务，输出延迟 / token / 成本 / 缓存命中。

用法::

    python scripts/real_check.py                 # 1 条任务
    python scripts/real_check.py --tasks 2

它量的是**真实模型的账**：mock 的数字（成本 0、延迟固定）不能用来定预算，
因此这个脚本存在的主要目的就是给 ``COST_BUDGET_USD`` / ``TOKEN_BUDGET`` 提供依据。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.util import make_temp_dir  # noqa: E402

os.environ.setdefault("CREATOR_DATA_DIR", str(make_temp_dir("real-", base=ROOT / ".doctor-data")))

from app.core.orchestrator import orchestrator  # noqa: E402
from app.core.store import task_store  # noqa: E402
from app.core.types import Brief  # noqa: E402
from app.llm import resolve_provider  # noqa: E402
from app.llm.cost import cost_guard  # noqa: E402
from app.llm.pricing import PRICING_VERSION, price_of  # noqa: E402

BRIEFS = [
    {
        "brand": "晨野",
        "product": "冷萃即饮咖啡",
        "objective": "转化",
        "audience": "早八通勤的都市白领",
        "channel": "小红书",
        "tone": "轻松、真实、有种草感",
        "industry": "食品饮料",
        "keywords": ["0 糖", "冷萃"],
        "constraints": ["不承诺减肥功效"],
        "deliverables": ["图文笔记 1 篇", "标题备选 5 条"],
    },
    {
        "brand": "云枢",
        "product": "云原生可观测平台",
        "objective": "教育",
        "audience": "中大型企业的 SRE 负责人",
        "channel": "公众号",
        "tone": "专业、克制、用数据说话",
        "industry": "科技",
        "keywords": ["可观测性", "全链路追踪"],
        "constraints": ["不贬损竞品"],
        "deliverables": ["长图文 1 篇"],
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="真实网关链路核验")
    parser.add_argument("--tasks", type=int, default=1, help="跑几条任务")
    parser.add_argument("--timeout", type=float, default=900.0, help="单任务等待上限（秒）")
    args = parser.parse_args()

    provider = resolve_provider()
    print(f"提供方：{provider.name} / {provider.model}｜simulated={getattr(provider, 'simulated', True)}")
    if getattr(provider, "simulated", True):
        print("! 当前是离线引擎，本脚本的意义是量真实模型；请检查 .env 的 LLM_PROVIDER/OPENAI_*")
        return 1
    print(f"计价口径：{PRICING_VERSION}｜{price_of(provider.model).to_dict()}\n")

    failures = 0
    for index in range(min(args.tasks, len(BRIEFS))):
        brief = Brief(**BRIEFS[index])
        task = orchestrator.create_task(brief, auto_approve=True)
        started = time.monotonic()
        status = "timeout"
        while time.monotonic() - started < args.timeout:
            current = task_store.get(task.id)
            if current and current.status in ("completed", "rejected", "failed"):
                status = current.status
                break
            time.sleep(0.5)
        elapsed = time.monotonic() - started
        current = task_store.get(task.id)

        ledger = cost_guard.ledger(task.id)
        calls = len([r for r in current.results if r.metrics.provider != "orchestrator"])
        latencies = [r.metrics.latency_ms for r in current.results if r.metrics.latency_ms > 0]

        print("=" * 78)
        print(f"[{index + 1}/{args.tasks}] {brief.brand}｜{brief.channel}｜status={status}")
        print(f"  墙钟耗时      : {elapsed:.1f}s")
        print(f"  模型调用次数  : {calls}")
        print(f"  token         : prompt={current.tokens.prompt} completion={current.tokens.completion}"
              f" 合计={current.tokens.prompt + current.tokens.completion}")
        print(f"  成本          : ${current.tokens.cost_usd:.6f}")
        if ledger is not None:
            print(f"  提供方缓存命中: {ledger.provider_cached_tokens} tokens"
                  f"（命中率 {ledger.provider_cached_tokens / max(1, current.tokens.prompt):.1%}）")
            print(f"  熔断          : {ledger.cut_off}｜本地缓存命中 {ledger.cached} 次")
        if latencies:
            latencies.sort()
            print(f"  单次调用延迟  : min={latencies[0]}ms p50={latencies[len(latencies)//2]}ms"
                  f" max={latencies[-1]}ms")
        print(f"  质量分        : {current.scorecard.overall if current.scorecard else '—'}"
              f"｜返工 {current.revision_round} 轮｜产物 {len(current.artifacts)}")
        delivery = next((a for a in reversed(current.artifacts) if a.type == "final_delivery"), None)
        if delivery:
            content = delivery.content or {}
            print(f"  交付标题      : {content.get('title')}")
            body = str(content.get("body") or "")
            print(f"  交付正文      : {len(body)} 字")
            print(f"  正文预览      : {body[:120].replace(chr(10), ' ')}")
        for result in current.results:
            if result.gate_result and result.gate_result != "pass":
                print(f"  门禁不通过    : {result.agent_id} {result.gate_result} — {result.summary[:60]}")
        if status != "completed":
            failures += 1
            print(f"  ! 未正常完成：{current.error}")

    print("=" * 78)
    print("结果：", "全部完成" if failures == 0 else f"{failures} 条未完成")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
