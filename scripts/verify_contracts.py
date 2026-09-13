"""联调核验：拉起真实服务，逐条比对「本轮新增能力」的实际响应与契约。

与 ``smoke_api.py`` 的分工
-------------------------
``smoke_api.py`` 是**端到端验收**（跑完整流水线、覆盖全部接口与错误分支，一次几十秒）；
本脚本是**快速契约核验**（约 15 秒），只盯住最容易在重构中悄悄回归的几处：
断点续跑后端、评估配置与聚合、记忆库租户视角、六维评分结构、`judge.scored` 事件、
**调用轨迹的 span 树层级与 OTel 形状导出**。

用途：改完评估/租户/追踪相关代码后先跑它，比直接上 smoke 更快拿到「哪里断了」。

用法::

    python scripts/verify_contracts.py      # 退出码 0 表示全部符合预期
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.util import make_temp_dir  # noqa: E402

DATA = make_temp_dir("verify-", base=ROOT / ".doctor-data")
PORT = 8813
BASE = f"http://127.0.0.1:{PORT}"

env = {**os.environ, "CREATOR_DATA_DIR": str(DATA), "PORT": str(PORT), "PYTHONNOUSERSITE": "1"}
log = (DATA / "server.log").open("w", encoding="utf-8", errors="replace")
proc = subprocess.Popen(
    [sys.executable, "-m", "app.main"],
    cwd=str(ROOT),
    env=env,
    stdout=log,
    stderr=subprocess.STDOUT,
)

failures: list[str] = []


def check(label: str, actual: object, expected: object) -> None:
    good = actual == expected
    print(f"  {'OK  ' if good else 'FAIL'} {label}: {actual!r}" + ("" if good else f"（期望 {expected!r}）"))
    if not good:
        failures.append(label)


try:
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with httpx.Client(base_url=BASE, timeout=3.0) as client:
                if client.get("/api/health").status_code == 200:
                    break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    else:
        print("服务未在 60s 内就绪")
        raise SystemExit(1)

    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        print("[GET /api/health]")
        health = client.get("/api/health").json()
        check("checkpointer.kind", health["checkpointer"]["kind"], "sqlite")
        check("provider.name", health["provider"]["name"], "mock")

        print("[GET /（静态托管，含新构建的前端）]")
        page = client.get("/")
        check("status", page.status_code, 200)
        check("index.html 挂载点", 'id="root"' in page.text, True)

        print("[GET /api/settings → judge]")
        judge = client.get("/api/settings").json()["judge"]
        check("mode", judge["mode"], "advisory")
        check("provider", judge["provider"], "offline")
        check("passThreshold", judge["passThreshold"], 75.0)
        check("weight", judge["weight"], 0.2)
        check("rubric 非空", bool(judge["rubric"]), True)

        print("[GET /api/evaluations（空库）]")
        evals = client.get("/api/evaluations").json()
        check("records", evals["records"], [])
        check("stats.total", evals["stats"]["total"], 0)
        check("config.mode", evals["config"]["mode"], "advisory")

        print("[GET /api/memory（租户视角）]")
        mem = client.get("/api/memory").json()
        check("stats.global_total", mem["stats"]["global_total"], 0)
        # 未启用鉴权时租户固定为 default（与 TaskRecord.tenant 口径一致）
        check("stats.tenant（未鉴权 → default）", mem["stats"]["tenant"], "default")
        check("stats.capacity", mem["stats"]["capacity"], 500)

        print("[GET /api/metrics → system.judge]")
        system = client.get("/api/metrics").json()["system"]
        check("judge.mode", system["judge"]["mode"], "advisory")
        check("judge.total", system["judge"]["total"], 0)

        print("[POST /api/tasks → 评估落地 → POST /evaluate]")
        task = client.post("/api/tasks", json={"brief": {
            "brand": "核验品牌", "product": "核验产品", "channel": "小红书",
            "industry": "消费品", "keywords": ["核验"], "constraints": ["不夸大功效"],
        }, "autoApprove": True}).json()["task"]
        tid = task["id"]
        deadline = time.time() + 120
        while time.time() < deadline:
            status = client.get(f"/api/tasks/{tid}").json()["task"]["status"]
            if status in ("completed", "rejected", "failed"):
                break
            time.sleep(0.5)
        check("任务终态", status, "completed")

        evals = client.get(f"/api/evaluations?taskId={tid}").json()
        check("评估记录数（门禁 + 交付两次）", len(evals["records"]) >= 2, True)
        first = evals["records"][0]
        check("六维维度", len(first["axes"]), 6)
        check("trigger 取值", first["trigger"] in ("review", "delivery"), True)
        check("分数区间", 0 < first["total"] <= 100, True)

        manual = client.post(f"/api/tasks/{tid}/evaluate", json={"provider": "offline"}).json()["record"]
        check("按需评估 trigger", manual["trigger"], "manual")
        check("按需评估 mode", manual["mode"], "offline")

        print("[评估事件与门禁事件]")
        events = client.get(f"/api/tasks/{tid}").json()["events"]
        types = [e["type"] for e in events]
        check("含 judge.scored 事件", "judge.scored" in types, True)
        gates = [e for e in events if e["type"] == "gate.decision"]
        check("门禁事件携带 evaluator 结论", bool((gates[0]["payload"] or {}).get("judge")), True)

        print("[GET /api/tasks/{id}/trace（调用轨迹）]")
        trace = client.get(f"/api/tasks/{tid}/trace").json()
        spans = trace["spans"]
        roots = [s for s in spans if s["parent_span_id"] is None]
        check("trace_id 非空", bool(trace["trace_id"]), True)
        check("span 数量", len(spans) >= 20, True)
        # 关键断言：树而不是平铺列表
        check("根 span 少于总数（层级成型）", len(roots) < len(spans), True)
        llm_spans = [s for s in spans if s["name"].startswith("llm.")]
        by_id = {s["span_id"]: s for s in spans}
        check("llm span 数量", len(llm_spans) >= 11, True)
        check(
            "llm span 嵌套在智能体/评估之下",
            all(
                s["parent_span_id"] in by_id
                and by_id[s["parent_span_id"]]["name"].startswith(("A", "judge", "gate"))
                for s in llm_spans
            ),
            True,
        )
        check("span 带模型属性", all("llm.model" in s["attributes"] for s in llm_spans), True)
        check("span 状态为 ok", {s["status"] for s in spans}, {"ok"})
        check("耗时聚合非空", len(trace["summary"]["by_name"]) > 0, True)
        check("导出格式为 OTel 形状", trace["export"]["format"], "otel-shaped-json")
        # 事件与 span 的关联
        linked = [e for e in events if e.get("span_id")]
        check("事件带 span_id", len(linked) > 0, True)
        check("事件 trace_id 一致", {e["trace_id"] for e in events if e.get("trace_id")} == {trace["trace_id"]}, True)

        print("[GET /api/metrics → system.tracing]")
        tracing = client.get("/api/metrics").json()["system"]["tracing"]
        check("tracing.traces", tracing["traces"] >= 1, True)
        check("tracing.spans", tracing["spans"] >= 20, True)
        check("tracing.errors", tracing["errors"], 0)
        check("tracing.by_name 非空", len(tracing["by_name"]) > 0, True)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()

print()
if failures:
    print(f"核验未通过：{len(failures)} 项 → {'、'.join(failures)}")
    raise SystemExit(1)
print("核验通过：新增接口在真实服务下响应符合契约。")
