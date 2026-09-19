"""联调核验：拉起真实服务，逐条比对「本轮新增能力」的实际响应与契约。

与 ``smoke_api.py`` 的分工
-------------------------
``smoke_api.py`` 是**端到端验收**（跑完整流水线、覆盖全部接口与错误分支，一次几十秒）；
本脚本是**快速契约核验**（约 40 秒），只盯住最容易在重构中悄悄回归的几处：
断点续跑后端、评估配置与聚合、记忆库租户视角、六维评分结构、`judge.scored` 事件、
**调用轨迹的 span 树层级与 OTel 形状导出**、**入站 traceparent 的跨进程延续**、
**数字人样例的创建 → 惰性推进 → 完成闭环**。

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

from app.core.util import force_offline_provider, free_port, make_temp_dir  # noqa: E402

DATA = make_temp_dir("verify-", base=ROOT / ".doctor-data")
PORT = free_port(8813)
BASE = f"http://127.0.0.1:{PORT}"

env = {**os.environ, "CREATOR_DATA_DIR": str(DATA), "PORT": str(PORT), "PYTHONNOUSERSITE": "1"}
env["LLM_PROVIDER"] = force_offline_provider()
log = (DATA / "server.log").open("w", encoding="utf-8", errors="replace")
proc = subprocess.Popen(
    [sys.executable, "-m", "app.main"],
    cwd=str(ROOT),
    env=env,
    stdout=log,
    stderr=subprocess.STDOUT,
)

failures: list[str] = []


def check(label: str, actual: object, expected: object, detail: str = "") -> None:
    """断言 ``actual == expected``。

    ``detail`` 是**失败时的补充说明**，与 ``expected`` 分开 ——
    把说明误传进 ``expected`` 会让断言与字符串比较，从而永远失败。
    """
    good = actual == expected
    message = "" if good else f"（期望 {expected!r}）"
    if not good and detail:
        message += f" {detail}"
    print(f"  {'OK  ' if good else 'FAIL'} {label}: {actual!r}{message}")
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
        # 存储模式决定检查点后端：file → sqlite；pg → postgres（子进程环境已透传）
        storage_mode = (os.environ.get("CREATOR_STORAGE") or "file").strip().lower()
        expected_kind = "postgres" if storage_mode == "pg" else "sqlite"
        check("checkpointer.kind", health["checkpointer"]["kind"], expected_kind)
        check("provider.name", health["provider"]["name"], "mock")

        print("[GET /（静态托管，含新构建的前端）]")
        page = client.get("/")
        exists = (ROOT / "dist" / "index.html").exists()
        check(
            "dist/index.html 存在",
            exists,
            True,
            "请先执行 npm run build（CI 中该步骤必须排在契约核验之前）",
        )
        check("status", page.status_code, 200)
        check("index.html 挂载点", 'id="root"' in page.text, True)
        if not exists:
            print("    ! 后端只在 dist/ 存在时才挂载静态托管与 SPA 回落路由；")
            print("      缺 dist/ 会让 GET / 返回 404 —— 这正是 CI 上曾失败的原因。")

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
        # OTel 的 `unset` 是合法的「未显式设置状态」，项目里只在个别收尾路径出现；
        # 这里断言「没有 error」而不是要求全是 ok —— 后者会在收尾竞态下偶发失败。
        span_statuses = {s["status"] for s in spans}
        check("span 无 error 状态", "error" not in span_statuses, True, f"实际状态 {span_statuses}")
        check("耗时聚合非空", len(trace["summary"]["by_name"]) > 0, True)
        check("导出格式为 OTel 形状", trace["export"]["format"], "otel-shaped-json")
        # 事件与 span 的关联
        linked = [e for e in events if e.get("span_id")]
        check("事件带 span_id", len(linked) > 0, True)
        check("事件 trace_id 一致", {e["trace_id"] for e in events if e.get("trace_id")} == {trace["trace_id"]}, True)

        print("[GET /api/tasks/{id}/export（成品导出）]")
        missing = client.get("/api/tasks/verify-no-such-task/export")
        check("无任务 → 404", missing.status_code, 404)
        export = client.get(f"/api/tasks/{tid}/export")
        check("有 final_delivery → 200", export.status_code, 200)
        check("导出为 text/plain", "text/plain" in export.headers.get("content-type", ""), True)
        check("导出内容非空", len(export.content) > 0, True)

        print("[GET /api/metrics → system.tracing]")
        tracing = client.get("/api/metrics").json()["system"]["tracing"]
        check("tracing.traces", tracing["traces"] >= 1, True)
        check("tracing.spans", tracing["spans"] >= 20, True)
        check("tracing.errors", tracing["errors"], 0)
        check("tracing.by_name 非空", len(tracing["by_name"]) > 0, True)
        sampling = tracing["sampling"]
        check("sampling.sampler 非空", bool(sampling["sampler"]), True)
        check("采样计数齐备", {"traces_sampled", "traces_unsampled"} <= set(sampling), True)

        print("[health → tracing 采样与传播可见]")
        health_tracing = client.get("/api/health").json()["tracing"]
        check("health.sampler", bool(health_tracing["sampler"]), True)
        check("health.propagation", "traceparent" in health_tracing["propagation"], True)

        print("[入站 traceparent → trace 跨进程延续]")
        remote_tid = "1234567890abcdef1234567890abcdef"
        remote_sid = "1234567890abcdef"
        video_task = client.post(
            "/api/tasks",
            json={"brief": {
                "brand": "核验品牌", "product": "核验产品", "channel": "抖音",
                "industry": "消费品", "keywords": ["核验"], "deliverables": ["短视频脚本 1 支"],
            }, "autoApprove": True},
            headers={"traceparent": f"00-{remote_tid}-{remote_sid}-01"},
        )
        check("携带 traceparent 创建任务", video_task.status_code, 201)
        vid = video_task.json()["task"]["id"]
        prop_trace = client.get(f"/api/tasks/{vid}/trace").json()
        check("trace 延续远端 trace_id", prop_trace["trace_id"], remote_tid)
        check(
            "根 span 挂在远端 span 之下",
            any(s["parent_span_id"] == remote_sid for s in prop_trace["spans"]),
            True,
        )
        check("坏头不阻塞任务创建",
              client.post("/api/tasks", json={"brief": {
                  "brand": "核验品牌", "product": "核验产品", "channel": "小红书", "industry": "消费品",
              }, "autoApprove": True}, headers={"traceparent": "garbage"}).status_code,
              201)

        print("[POST /api/tasks/{id}/digital-human（无脚本 → 409）]")
        no_script = client.post(f"/api/tasks/{tid}/digital-human", json={})
        check("无视频脚本拒绝创建", no_script.status_code, 409)

        print("[抖音任务的数字人样例闭环]")
        deadline = time.time() + 120
        video_status = "running"
        while time.time() < deadline:
            video_status = client.get(f"/api/tasks/{vid}").json()["task"]["status"]
            if video_status in ("completed", "rejected", "failed"):
                break
            time.sleep(0.5)
        check("视频任务终态", video_status, "completed")

        dh_view = client.get(f"/api/tasks/{vid}/digital-human").json()
        check("has_video_script", dh_view["has_video_script"], True)
        check("初始无作业", dh_view["jobs"], [])
        created_job = client.post(f"/api/tasks/{vid}/digital-human", json={})
        check("创建样例渲染作业", created_job.status_code, 201)
        job = created_job.json()["job"]
        check("provider 为 sample", job["provider"], "sample")
        check("渲染清单来自分镜", job["shot_count"] >= 1, True)
        check("初始状态 queued", job["status"], "queued")

        deadline = time.time() + 40
        final_status = job["status"]
        while time.time() < deadline:
            jobs = client.get(f"/api/tasks/{vid}/digital-human").json()["jobs"]
            final_status = next(j["status"] for j in jobs if j["id"] == job["id"])
            if final_status in ("done", "failed"):
                break
            time.sleep(1.0)
        check("样例引擎完成渲染", final_status, "done")
        final_job = next(j for j in jobs if j["id"] == job["id"])
        check("产出成片地址", str(final_job["video_url"]).startswith("sample://"), True)
        check("分镜段落齐备", len(final_job["manifest"]["segments"]), job["shot_count"])
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
