"""真实模型链路压测与 Prompt 调优基线（plan.md 4.2 / 待办「真实模型链路压测」）。

用途
----
* **Mock 模式（默认）**：无外部依赖，验证编排吞吐与**并发正确性**
  （黑板租约是否冲突、成本账本是否串味、检查点是否互相覆盖），CI 可直接跑。
* **OpenAI 兼容模式**：把 ``LLM_PROVIDER=openai`` 打到真实网关，量出真实延迟与成本，
  为 Prompt 调优（对比 ``avg_overall_score``）和预算设置（``COST_BUDGET_USD``）提供数据。

产出指标
--------
* 任务级：完成率、端到端延迟 p50 / p95 / p99、平均返工轮次、首轮通过率
* 质量级：门禁通过率、平均总分、事实准确率、品牌一致性
* 成本级：总成本 / 单篇成本 / token / 缓存命中率 / 熔断任务数
* 并发级：黑板租约冲突次数、活跃租约残留（应归零）

用法::

    python scripts/stress_llm.py --tasks 12 --concurrency 4
    LLM_PROVIDER=openai OPENAI_API_KEY=sk-xxx python scripts/stress_llm.py -n 6 -c 2

退出码 0 表示全部任务跑完且无失败任务。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="creator-stress-"))
SERVER_LOG = TMP / "server.log"
PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"

TERMINAL = {"completed", "rejected", "failed"}

BRIEF_TEMPLATE = {
    "brand": "晨野",
    "product": "冷萃即饮咖啡",
    "objective": "转化",
    "audience": "早八通勤的都市白领",
    "channel": "小红书",
    "tone": "轻松、真实、有种草感",
    "industry": "食品饮料",
    "keywords": "0 糖, 冷萃",
    "constraints": ["不承诺减肥功效"],
    "deliverables": ["图文笔记 1 篇"],
}


def call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """每次请求独立连接：压测下复用 keep-alive 连接会被服务端提前关闭（见 smoke_api 说明）。"""
    with httpx.Client(base_url=BASE, timeout=120.0) as client:
        return client.request(method, path, **kwargs)


def percentile(values: list[float], ratio: float) -> float:
    """最近秩百分位（nearest-rank），样本少时比插值更稳。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(ratio * len(ordered)) - 1))
    return ordered[index]


def run_one(index: int, timeout: float) -> dict[str, Any]:
    """提交一个任务并轮询到终态，返回该任务的耗时与质量指标。"""
    brief = {**BRIEF_TEMPLATE, "product": f"{BRIEF_TEMPLATE['product']} #{index:02d}"}
    started = time.time()
    try:
        response = call("POST", "/api/tasks", json={"brief": brief, "autoApprove": True})
        response.raise_for_status()
        task_id = response.json()["task"]["id"]
    except Exception as error:  # noqa: BLE001 - 单个任务失败不应中断整场压测
        return {"id": f"#{index:02d}", "status": "submit_failed", "error": str(error), "elapsed": 0.0}

    deadline = started + timeout
    while time.time() < deadline:
        try:
            task = call("GET", f"/api/tasks/{task_id}").json()["task"]
        except Exception:  # noqa: BLE001 - 轮询瞬态错误直接重试
            time.sleep(0.4)
            continue
        if task["status"] in TERMINAL:
            scorecard = task.get("scorecard") or {}
            return {
                "id": task_id,
                "status": task["status"],
                "elapsed": time.time() - started,
                "revision_round": task["revision_round"],
                "overall": scorecard.get("overall", 0),
                "cost_usd": (task.get("tokens") or {}).get("cost_usd", 0),
                "cached": (task.get("tokens") or {}).get("cached", 0),
                "cut_off": (task.get("tokens") or {}).get("cut_off", False),
                "intent_conflicts": task.get("intent_conflicts", 0),
            }
        time.sleep(0.5)

    return {"id": task_id, "status": "timeout", "elapsed": time.time() - started}


def report(results: list[dict[str, Any]], provider: str) -> bool:
    """打印压测报告；返回是否「无失败任务」。"""
    total = len(results)
    done = [r for r in results if r["status"] == "completed"]
    failed = [r for r in results if r["status"] not in ("completed",)]

    latencies = [r["elapsed"] for r in done]
    rounds = [r["revision_round"] for r in done]
    scores = [r["overall"] for r in done if r.get("overall")]

    print("\n" + "=" * 62)
    print(f"压测报告（provider={provider}，任务 {total} 个）")
    print("=" * 62)
    print(f"完成 {len(done)}｜失败/超时 {len(failed)}｜完成率 {len(done) / total * 100:.1f}%")
    if latencies:
        print(
            f"端到端延迟：p50 {percentile(latencies, 0.5):.2f}s｜"
            f"p95 {percentile(latencies, 0.95):.2f}s｜p99 {percentile(latencies, 0.99):.2f}s"
            f"｜max {max(latencies):.2f}s"
        )
    if rounds:
        print(
            f"返工：平均 {sum(rounds) / len(rounds):.2f} 轮｜"
            f"首轮通过率 {sum(1 for r in rounds if r == 0) / len(rounds) * 100:.1f}%"
        )
    if scores:
        print(f"质量分：平均 {sum(scores) / len(scores):.1f}｜最低 {min(scores):.1f}｜最高 {max(scores):.1f}")

    try:
        metrics = call("GET", "/api/metrics").json()["system"]
    except Exception as error:  # noqa: BLE001
        print(f"（无法读取 /api/metrics：{error}）")
        return not failed

    cost = metrics["cost"]
    cache = metrics["cache"]
    leases = metrics["leases"]
    print("-" * 62)
    print(
        f"成本：总计 ${cost['total_cost_usd']:.4f}｜单篇 ${cost['avg_cost_per_task_usd']:.4f}｜"
        f"熔断任务 {cost['cut_off_tasks']}"
    )
    print(
        f"缓存：命中 {cache['hits']}｜未命中 {cache['misses']}｜"
        f"命中率 {cache['hitRate'] * 100:.1f}%｜条目 {cache['entries']}/{cache['capacity']}"
    )
    print(
        f"并发：租约冲突 {leases['conflicts']}｜活跃租约 {leases['active']}"
        + ("（已归零 ✓）" if leases["active"] == 0 else "（⚠ 存在残留租约，检查释放逻辑）")
    )
    print(
        f"门禁：通过率 {metrics['gate_pass_rate']:.1f}%｜事实准确率 {metrics['fact_accuracy']:.1f}%｜"
        f"品牌一致性 {metrics['brand_consistency']:.1f}"
    )
    print("=" * 62)
    if provider != "mock":
        print(
            "提示：调优 Prompt 时固定同一批 Brief、对比 avg_overall_score 与返工轮次；"
            "成本突然抬高多半是返工变多，可回看 A6/A7 的 verdict 分布。"
        )
    return not failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Creator Agent Studio 压测 / Prompt 调优基线")
    parser.add_argument("-n", "--tasks", type=int, default=8, help="任务总数（默认 8）")
    parser.add_argument("-c", "--concurrency", type=int, default=4, help="并发度（默认 4）")
    parser.add_argument("-t", "--timeout", type=float, default=600.0, help="单任务超时秒数（默认 600）")
    parser.add_argument(
        "--provider",
        default=os.environ.get("LLM_PROVIDER", "mock"),
        choices=("mock", "openai"),
        help="模型提供方（默认取 LLM_PROVIDER，未设置则 mock）",
    )
    args = parser.parse_args()

    if args.provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        print("！provider=openai 但未检测到 OPENAI_API_KEY，服务端会降级到离线引擎（成本恒为 0）")

    env = {
        **os.environ,
        "CREATOR_DATA_DIR": str(TMP),
        "PORT": str(PORT),
        "LLM_PROVIDER": args.provider,
        "AUTO_APPROVE": "true",
        "PYTHONNOUSERSITE": "1",
    }
    log_handle = SERVER_LOG.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=str(ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    results: list[dict[str, Any]] = []
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if call("GET", "/api/health").status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        else:
            print("服务启动超时，日志见：", SERVER_LOG)
            return 1

        print(f"服务就绪（provider={args.provider}，数据目录 {TMP}）")
        print(f"开始压测：{args.tasks} 个任务 / 并发 {args.concurrency}\n")

        started = time.time()
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = [pool.submit(run_one, i, args.timeout) for i in range(1, args.tasks + 1)]
            for future in futures:
                result = future.result()
                results.append(result)
                mark = "✓" if result["status"] == "completed" else "✗"
                print(f"  {mark} {result['id']} {result['status']} {result.get('elapsed', 0):.2f}s")
        wall = time.time() - started

        ok = report(results, args.provider)
        print(f"总墙钟时间 {wall:.2f}s｜吞吐 {len(results) / wall:.2f} 任务/秒")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_handle.close()
        print(f"服务端日志：{SERVER_LOG}")
        if not results:
            tail = SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-2000:]
            print(tail)


if __name__ == "__main__":
    raise SystemExit(main())
