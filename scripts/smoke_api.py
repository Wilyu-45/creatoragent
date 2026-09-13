"""端到端验收：拉起真实 uvicorn 进程，逐条核对 ``/api/*`` 与 SSE 契约。

覆盖范围
--------
* 基础信息：``/api/health``、``/api/agents``、``/api/knowledge``、``/api/settings``
* 主流程：建任务 → SSE 实时事件 → 挂起等待人工 → ``decide`` → 归档交付
* 自动审批：``autoApprove`` 走完全程
* 记忆闭环：``/api/memory``、``/api/memory/search``（含向量检索字段），以及第二个任务是否真的复用了第一个任务沉淀的知识
* 发布闭环：``/api/tasks/{id}/publish`` 登记发布、``/api/tasks/{id}/publish/dispatch`` 自动投递、
  ``/api/publish/queue`` 待发布队列、``/api/tasks/{id}/feedback`` 回填效果并触发 A10 复盘
* 评估流水线：``/api/evaluations`` 评估历史与聚合、``/api/tasks/{id}/evaluate`` 按需评估、
  门禁事件里携带评估结论（``/api/metrics.system.judge``）
* 黄金数据集：``/api/golden`` 用例/基线/覆盖、``/api/golden/run`` 后台运行与重复触发 409、
  ``/api/golden/reset``；并核对部分运行的对比结论
* 调用轨迹：``/api/tasks/{id}/trace`` 的 span 树层级、llm span 嵌套、OTel 形状导出、
  事件与 trace/span 的关联，以及 ``/api/metrics.system.tracing`` 聚合
* 聚合视图：``/api/tasks``、``/api/metrics``（含成本 / 缓存 / 黑板租约 / 评估指标）
* 错误分支：404 / 400 / 409（重复裁决、删除运行中任务）
* **租户隔离**：另起一个带 ``CREATOR_API_TOKENS`` 的实例，验证 401、任务不可见（404）、
  记忆库不串租户、按租户统计
* 持久化：任务 JSON、``checkpoints.sqlite``、``blackboard.json``、``memory.json``、``evaluations.json``

两个容易踩的坑（都已在实现里规避）
-----------------------------------
1. **每个请求用一个新连接**：共享连接池时，服务端可能在 keep-alive 超时后关闭
   套接字，而客户端仍复用它，于是请求石沉大海。浏览器（fetch/EventSource）与
   curl 不受影响，是压测客户端独有的问题。
2. **服务端输出重定向到文件而非 ``subprocess.PIPE``**：父进程若不在读取，管道
   缓冲区写满会让子进程卡在写日志上，表现为「服务整体无响应」，且因为写日志时
   持有 GIL，连 ``faulthandler`` 都无法转储线程栈——极易被误判成服务端死锁。

用法::

    python scripts/smoke_api.py      # 退出码 0 表示全部通过
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.util import make_temp_dir  # noqa: E402 - 需先补好 sys.path

#: 数据目录必须**可写**：``tempfile.mkdtemp`` 的 ``0o700`` 目录在受限沙箱下
#: 会拒绝子进程写入，导致记忆库/检查点静默退化（见 app/core/util.make_temp_dir）。
TMP = make_temp_dir("api-", base=ROOT / ".doctor-data")
#: 服务端输出重定向到文件，**不要用 subprocess.PIPE**（见模块说明第 2 条）。
SERVER_LOG = TMP / "server.log"
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"

BRIEF = {
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
    """单次请求 + 单次连接（见模块说明第 1 条）。"""
    with httpx.Client(base_url=BASE, timeout=60.0) as client:
        return client.request(method, path, **kwargs)


def wait_for(predicate, timeout: float = 180.0, label: str = "条件") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001 - 轮询期间的瞬态错误直接重试
            pass
        time.sleep(0.3)
    print(f"  ! 超时：{label}")
    return False


def task_of(task_id: str) -> dict[str, Any]:
    return call("GET", f"/api/tasks/{task_id}").json()["task"]


# ------------------------------------------------------------------ #
# 鉴权 + 租户隔离的独立实例（plan.md D17）                             #
# ------------------------------------------------------------------ #

TENANT_PORT = 8802
TENANT_BASE = f"http://127.0.0.1:{TENANT_PORT}"
#: acme 与 beta 各持一个 token：两者都看不到对方的任务与记忆库
TOKENS = "acme:tok_acme,beta:tok_beta"


def call_as(token: str, method: str, path: str, **kwargs: Any) -> httpx.Response:
    headers = {"X-API-Token": token} if token else {}
    with httpx.Client(base_url=TENANT_BASE, timeout=60.0) as client:
        return client.request(method, path, headers=headers, **kwargs)


def check_tenant_isolation() -> bool:
    """起第二个服务实例（带鉴权），验证鉴权与任务/记忆库的双向隔离。

    隔离是这个项目里最容易被「看起来能跑」掩盖的能力：不专门起一个带 token 的
    实例，就永远不知道越权查询到底返回 404 还是别人的数据。
    """
    data_dir = make_temp_dir("smoke-tenant-", base=ROOT / ".doctor-data")
    log_path = data_dir / "server.log"
    env = {
        **os.environ,
        "CREATOR_DATA_DIR": str(data_dir),
        "PORT": str(TENANT_PORT),
        "CREATOR_API_TOKENS": TOKENS,
        "PYTHONNOUSERSITE": "1",
    }
    handle = log_path.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )

    print("\n[鉴权与租户隔离（独立实例，CREATOR_API_TOKENS 已配置）]")
    ok = True
    try:
        def ready() -> bool:
            with httpx.Client(base_url=TENANT_BASE, timeout=5.0) as client:
                return client.get("/api/health").status_code == 200

        if not wait_for(ready, 60, "鉴权实例启动"):
            handle.close()
            return False

        health = call_as("", "GET", "/api/health").json()
        no_token = call_as("", "GET", "/api/tasks").status_code
        bad_token = call_as("wrong", "GET", "/api/tasks").status_code
        print(f"  /api/health 免鉴权=200｜无 token 访问任务={no_token}｜错误 token={bad_token}")
        ok = ok and health["config"]["authRequired"] is True
        ok = ok and (no_token, bad_token) == (401, 401)

        acme_task = call_as("tok_acme", "POST", "/api/tasks", json={"brief": BRIEF}).json()["task"]
        acme_id = acme_task["id"]
        print(f"  acme 建任务 {acme_id} tenant={acme_task['tenant']}")
        ok = ok and acme_task["tenant"] == "acme"

        acme_list = call_as("tok_acme", "GET", "/api/tasks").json()["tasks"]
        beta_list = call_as("tok_beta", "GET", "/api/tasks").json()["tasks"]
        cross = call_as("tok_beta", "GET", f"/api/tasks/{acme_id}").status_code
        print(f"  acme 可见 {len(acme_list)} 条｜beta 可见 {len(beta_list)} 条｜"
              f"beta 访问 acme 任务={cross}（应为 404）")
        ok = ok and len(acme_list) == 1 and len(beta_list) == 0 and cross == 404

        # 等 acme 任务挂起后裁决，让它把知识卡片写进 acme 租户
        wait_for(
            lambda: call_as("tok_acme", "GET", f"/api/tasks/{acme_id}").json()["task"]["status"]
            in ("awaiting_approval", "completed", "failed"),
            label="acme 任务到达挂起态",
        )
        call_as(
            "tok_acme",
            "POST",
            f"/api/tasks/{acme_id}/decide",
            json={"decision": "approve", "comment": "租户隔离冒烟"},
        )
        wait_for(
            lambda: call_as("tok_acme", "GET", f"/api/tasks/{acme_id}").json()["task"]["status"]
            in ("completed", "rejected", "failed"),
            label="acme 任务终态",
        )

        acme_memory = call_as("tok_acme", "GET", "/api/memory").json()
        beta_memory = call_as("tok_beta", "GET", "/api/memory").json()
        acme_cards = acme_memory["cards"]
        beta_cards = beta_memory["cards"]
        tenants = {card["tenant"] for card in acme_cards}
        print(f"  acme 记忆库 {len(acme_cards)} 张（租户 {tenants}）｜"
              f"beta 记忆库 {len(beta_cards)} 张｜全局 {acme_memory['stats']['global_total']} 张")
        ok = ok and bool(acme_cards) and not beta_cards
        ok = ok and tenants == {"acme"}

        search_cross = call_as(
            "tok_beta", "POST", "/api/memory/search", json={"query": "冷萃 咖啡", "topK": 3}
        ).json()["hits"]
        print(f"  beta 检索 acme 的知识 → 命中 {len(search_cross)} 条（应为 0）")
        ok = ok and not search_cross

        beta_metrics = call_as("tok_beta", "GET", "/api/metrics").json()["system"]
        print(f"  beta 视图内任务数={beta_metrics['total_tasks']}（应为 0）")
        ok = ok and beta_metrics["total_tasks"] == 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        handle.close()
    return ok


def main() -> int:
    env = {**os.environ, "CREATOR_DATA_DIR": str(TMP), "PORT": str(PORT)}
    env["PYTHONNOUSERSITE"] = "1"
    log_handle = SERVER_LOG.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=str(ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    ok = True
    try:
        if not wait_for(lambda: call("GET", "/api/health").status_code == 200, 60, "服务启动"):
            return 1

        print("[GET /api/health]")
        health = call("GET", "/api/health").json()
        print(f"  ok={health['ok']} provider={health['provider']} "
              f"threshold={health['config']['qualityThreshold']}")

        print("[GET / （前端静态托管）]")
        page = call("GET", "/")
        print(f"  status={page.status_code} content-type={page.headers.get('content-type')} "
              f"bytes={len(page.content)}")
        ok = ok and page.status_code == 200 and 'id="root"' in page.text

        print("[GET /api/agents]")
        agents = call("GET", "/api/agents").json()
        print(f"  implemented={[a['id'] for a in agents['implemented']]} "
              f"planned={[a['id'] for a in agents['planned']]} pipeline={len(agents['pipeline'])}")

        print("[GET /api/knowledge]")
        kn = call("GET", "/api/knowledge").json()
        print(f"  lexicon={len(kn['lexicon'])} 行业规则={len(kn['industry_rules'])} "
              f"渠道={len(kn['channels'])} 行业={len(kn['industries'])}")

        print("[PUT /api/settings]")
        before = call("GET", "/api/settings").json()
        updated = call(
            "PUT",
            "/api/settings",
            json={
                "qualityThreshold": 70,
                "temperature": 0.3,
                "model": "gpt-4o-mini",
                "embeddingProvider": "local",
                "embeddingWeight": 0.4,
                "publishWebhookUrl": "https://example.com/hook",
                "publishAutoDispatch": False,
                "judgeMode": "advisory",
                "judgeProvider": "offline",
                "judgePassThreshold": 70,
                "judgeWeight": 0.2,
            },
        ).json()
        print(f"  threshold {before['qualityThreshold']} → {updated['qualityThreshold']} "
              f"| temperature {updated['llm']['temperature']}")
        print(f"  向量：{updated['embedding']['provider']} 权重={updated['embedding']['weight']}｜"
              f"发布 webhook 已配置={updated['publish']['webhookSet']}｜鉴权={updated['authRequired']}")
        print(f"  评估：{updated['judge']['mode']}/{updated['judge']['provider']} "
              f"通过线={updated['judge']['passThreshold']} 权重={updated['judge']['weight']} "
              f"rubric={updated['judge']['rubric']}")
        ok = ok and updated["qualityThreshold"] == 70 and updated["llm"]["temperature"] == 0.3
        ok = ok and updated["embedding"]["weight"] == 0.4
        ok = ok and updated["publish"]["webhookSet"] is True
        ok = ok and updated["authRequired"] is False
        ok = ok and updated["judge"]["mode"] == "advisory"
        ok = ok and updated["judge"]["passThreshold"] == 70
        ok = ok and bool(updated["judge"]["rubric"])
        # 立刻清空 webhook：下面的「自动投递」要在无 webhook 的离线语义下验证（不真发请求）
        cleared = call("PUT", "/api/settings", json={"publishWebhookUrl": ""}).json()
        ok = ok and cleared["publish"]["webhookSet"] is False

        print("\n[POST /api/tasks（人工审批 + SSE 实时订阅）]")
        created = call("POST", "/api/tasks", json={"brief": BRIEF}).json()["task"]
        task_id = created["id"]
        print(f"  {task_id} status={created['status']} phase={created['phase']}")

        saw_events: list[str] = []
        approval_seen = False
        # SSE 用独立 Client 承载：流被提前中断时不污染 REST 的连接池
        with httpx.Client(base_url=BASE, timeout=httpx.Timeout(120.0)) as sse:
            with sse.stream("GET", f"/api/tasks/{task_id}/events?since=0") as resp:
                print(f"  SSE status={resp.status_code} "
                      f"content-type={resp.headers.get('content-type')}")
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    saw_events.append(event["type"])
                    if event["type"] == "approval.required":
                        approval_seen = True
                        print(f"  收到 approval.required：{event['message']}")
                        break
        ok = ok and approval_seen

        detail = call("GET", f"/api/tasks/{task_id}").json()
        print(f"  挂起 phase={detail['task']['phase']} "
              f"产物={detail['blackboard']['stats']['artifact_count']} "
              f"黑板书={len(detail['blackboard']['facts'])} "
              f"活动={len(detail['blackboard']['activities'])}")
        ok = ok and detail["task"]["status"] == "awaiting_approval"

        print("[POST /api/tasks/{id}/decide]")
        decided = call(
            "POST",
            f"/api/tasks/{task_id}/decide",
            json={"decision": "approve", "comment": "接口冒烟"},
        ).json()["task"]
        print(f"  decide 后 status={decided['status']}")
        wait_for(
            lambda: task_of(task_id)["status"] in ("completed", "rejected", "failed"),
            label="等待终态",
        )
        final = task_of(task_id)
        print(f"  终态 status={final['status']} phase={final['phase']} "
              f"score={final['scorecard']['overall'] if final['scorecard'] else None} "
              f"artifacts={len(final['artifacts'])}")
        ok = ok and final["status"] == "completed"
        ok = ok and any(a["type"] == "final_delivery" for a in final["artifacts"])

        # 第二阶段智能体：A8/A9/A11 必须真的产出产物，且在流水线上标记为完成
        produced = {a["type"] for a in final["artifacts"]}
        ran = {n["agent_id"] for n in final["pipeline"] if n["status"] == "done"}
        ok = ok and {"visual_brief", "channel_adaptation", "knowledge_card"} <= produced
        ok = ok and {"A8", "A9", "A11"} <= ran
        print(f"  新增阶段已执行：{sorted({'A8', 'A9', 'A11'} & ran)}  "
              f"产物类型 {len(produced)} 种")

        delivery = next((a for a in final["artifacts"] if a["type"] == "final_delivery"), None)
        payload = (delivery or {}).get("content") or {}
        channels = payload.get("channels") or []
        visual = payload.get("visual") or {}
        cards = payload.get("knowledge_cards") or []
        print(f"  交付件含平台 {len(channels)} 个｜视觉「{visual.get('style', '（无）')}」"
              f"｜知识卡片 {len(cards)} 张")
        ok = ok and bool(channels) and bool(visual.get("style")) and bool(cards)

        print("\n[GET /api/memory（A11 知识沉淀入库）]")
        memory_view = call("GET", "/api/memory").json()
        stats = memory_view["stats"]
        print(f"  卡片总数={stats['total']} 覆盖任务={stats['tasks']} 品牌={stats['brands']} "
              f"容量上限={stats['capacity']}")
        print(f"  新鲜度：新鲜={stats['fresh']} 陈旧={stats['stale']} 最旧={stats['oldest_days']}天 "
              f"阈值={stats['max_age_days']}天")
        print(f"  向量检索：{stats['embedding']['provider']} 权重={stats['embedding']['weight']} "
              f"已建索引={stats['vector_indexed']}")
        ok = ok and stats["total"] > 0
        ok = ok and stats["embedding"]["provider"] in ("local", "openai")

        search = call(
            "POST",
            "/api/memory/search",
            json={"query": "冷萃 咖啡 早八通勤 小红书", "topK": 3},
        ).json()
        hit_text = "；".join(f"{h['title']}({h['score']}/语义{h.get('vector_score')})"
                            for h in search["hits"][:3])
        print(f"  检索命中 {len(search['hits'])} 条" + (f"：{hit_text}" if hit_text else ""))
        # 混合检索：命中项必须带语义分量，且与向量提供方一致
        ok = ok and bool(search["hits"])
        ok = ok and all("vector_score" in hit for hit in search["hits"])
        ok = ok and any(hit["vector_score"] > 0 for hit in search["hits"])

        print("\n[POST /api/tasks（自动审批）]")
        auto = call("POST", "/api/tasks", json={"brief": BRIEF, "autoApprove": True}).json()["task"]
        wait_for(
            lambda: task_of(auto["id"])["status"] in ("completed", "rejected", "failed"),
            label="等待自动完成",
        )
        auto_final = task_of(auto["id"])
        print(f"  {auto['id']} status={auto_final['status']} artifacts={len(auto_final['artifacts'])}")
        ok = ok and auto_final["status"] == "completed"

        # 记忆闭环的关键证据：第二个任务必须真的复用了第一个任务沉淀的知识
        cards_of_auto = [a for a in auto_final["artifacts"] if a["type"] == "knowledge_card"]
        recalled = ((cards_of_auto[-1]["content"] or {}).get("recalled") if cards_of_auto else None) or []
        reuse = [
            e
            for result in auto_final["results"]
            if result["agent_id"] == "A1"
            for e in result["evidence"]
            if str(e.get("source", "")).startswith("记忆库/")
        ]
        print(f"  跨任务召回：A11 检索结果 {len(recalled)} 条｜A1 复用历史资产 {len(reuse)} 条")
        if recalled:
            print(f"    召回示例：{recalled[0]['title']}（{recalled[0]['score']}）")
        if reuse:
            print(f"    复用来源：{reuse[0]['source']}")
        ok = ok and bool(recalled) and bool(reuse)

        print("\n[GET/POST /api/tasks/{id}/publish（审批后自动排期 → 登记发布）]")
        plan = call("GET", f"/api/tasks/{task_id}/publish").json()
        slots = plan["schedule"]
        print(f"  排期渠道 {len(slots)} 个："
              + "、".join(f"{s['channel']}@{s['slot']}" for s in slots[:3]))
        ok = ok and bool(slots)

        published = call("POST", f"/api/tasks/{task_id}/publish", json={}).json()
        print(f"  登记发布 → {published['published']}")
        ok = ok and bool(published["published"])

        print("\n[POST /api/tasks/{id}/feedback（效果回填 → A10 复盘 → A/B 结论）]")
        first_channel = slots[0]["channel"] if slots else ""
        feedback = call(
            "POST",
            f"/api/tasks/{task_id}/feedback",
            json={
                "channel": first_channel,
                "window": "发布后 72 小时",
                "metrics": {
                    "exposure": 12000,
                    "clicks": 480,
                    "interactions": 260,
                    "conversions": 24,
                },
            },
        ).json()
        artifacts = task_of(task_id)["artifacts"]
        review = next(
            (
                a
                for a in artifacts
                if a["type"] == "effect_report"
                and (a["content"] or {}).get("mode") == "post_publish_review"
            ),
            None,
        )
        review_content = (review or {}).get("content") or {}
        comparison = review_content.get("predicted_comparison") or {}
        conclusion = review_content.get("ab_conclusion") or {}
        print(f"  复盘 verdict={comparison.get('verdict')} "
              f"实际CTR={review_content.get('actuals', {}).get('ctr')}% "
              f"预估中位={comparison.get('predicted_mid')}% A/B={conclusion.get('winner')} "
              f"可放量={conclusion.get('ready_to_scale')}")
        ok = ok and review is not None and bool(feedback["artifact_id"])
        ok = ok and bool(comparison.get("verdict")) and bool(conclusion.get("note"))

        print("\n[POST /api/tasks/{id}/publish/dispatch（自动投递 → 待发布队列）]")
        auto_plan = call("GET", f"/api/tasks/{auto['id']}/publish").json()["schedule"]
        print("  排期含投递字段："
              + "、".join(f"{s['channel']}[{s['dispatch_status']}@{s.get('due_at')}]"
                         for s in auto_plan[:3]))
        ok = ok and all({"due_at", "dispatch_status", "attempts"} <= set(s) for s in auto_plan)

        dispatched = call(
            "POST", f"/api/tasks/{auto['id']}/publish/dispatch", json={"channel": "", "force": True}
        ).json()
        states = sorted({s["dispatch_status"] for s in dispatched["schedule"]})
        print(f"  投递结果：成功 {dispatched['dispatched']}｜失败 {dispatched['failed']}｜"
              f"状态 {states}")
        # 未配置 webhook 时后端退化为「登记发布」，离线也应成功
        ok = ok and bool(dispatched["dispatched"]) and not dispatched["failed"]
        ok = ok and all(s["status"] == "published" for s in dispatched["schedule"])

        queue = call("GET", "/api/publish/queue").json()
        pending_auto = [row for row in queue["items"] if row["task_id"] == auto["id"]]
        print(f"  队列共 {queue['total']} 条；本任务残留 {len(pending_auto)} 条")
        ok = ok and isinstance(queue["total"], int) and not pending_auto

        print("\n[GET /api/tasks 与 /api/metrics]")
        tasks = call("GET", "/api/tasks").json()["tasks"]
        print(f"  任务列表 {len(tasks)} 条；首条 phase_label={tasks[0]['phase_label']} "
              f"artifact_count={tasks[0]['artifact_count']}")
        metrics = call("GET", "/api/metrics").json()["system"]
        print(f"  total={metrics['total_tasks']} completed={metrics['completed']} "
              f"avg_score={metrics['avg_overall_score']} gate_pass={metrics['gate_pass_rate']} "
              f"p99={metrics['p99_agent_latency_ms']}ms simulated={metrics['simulated_ratio']}%")
        cost = metrics["cost"]
        leases = metrics["leases"]
        print(f"  成本：单篇=${cost['avg_cost_per_task_usd']} 预算=${cost['budget_usd']} "
              f"熔断任务={cost['cut_off_tasks']} 缓存命中率={metrics['cache']['hitRate']}")
        print(f"  黑板租约：活动={leases['active']} 累计冲突={leases['conflicts']}")
        ok = ok and metrics["total_tasks"] >= 2 and metrics["completed"] == 2
        ok = ok and {"cost", "cache", "leases", "judge", "tracing"} <= set(metrics)
        # 任务全部结束，说明租约都已成对释放（无残留）
        ok = ok and leases["active"] == 0
        tracing = metrics["tracing"]
        print(f"  追踪：已追踪 {tracing['traces']} 个任务｜span {tracing['spans']} 个｜"
              f"错误 {tracing['errors']}｜聚合条目 {len(tracing['by_name'])}")
        ok = ok and tracing["traces"] >= 1 and tracing["spans"] >= 20
        # 降级与缓存命中被设计为正常路径，不应污染错误数
        ok = ok and tracing["errors"] == 0

        print("\n[LLM-as-a-Judge 评估流水线（plan.md D14）]")
        judge_metrics = metrics["judge"]
        print(f"  模式={judge_metrics['mode']} 记录={judge_metrics['total']} 条｜"
              f"覆盖任务={judge_metrics['tasks']}｜均分={judge_metrics['avg_total']}｜"
              f"通过率={judge_metrics['pass_rate']}%｜回退={judge_metrics['fallbacks']}")
        print("  维度均分：" + "、".join(
            f"{axis['label']} {axis['score']}" for axis in judge_metrics["axis_avg"][:3]
        ))
        ok = ok and metrics.get("judge") is not None
        ok = ok and judge_metrics["total"] > 0 and judge_metrics["tasks"] >= 1
        ok = ok and bool(judge_metrics["axis_avg"])

        evaluations = call("GET", "/api/evaluations?limit=5").json()
        latest = evaluations["records"][0] if evaluations["records"] else None
        print(f"  GET /api/evaluations → 记录 {len(evaluations['records'])} 条｜"
              f"rubric={evaluations['config']['rubric']}")
        if latest:
            print(f"    最近一条：{latest['total']}/100（{latest['verdict']}，{latest['mode']}，"
                  f"trigger={latest['trigger']}）{latest['summary'][:40]}")
        ok = ok and bool(evaluations["records"])
        # 交付前的评估（kind=final）与门禁时刻的那次都要留痕
        ok = ok and any(r["task_id"] == task_id for r in evaluations["records"])
        ok = ok and all(len(r["axes"]) == 6 for r in evaluations["records"])

        manual = call("POST", f"/api/tasks/{task_id}/evaluate", json={"provider": "offline"}).json()
        manual_record = manual["record"]
        print(f"  POST /evaluate → {manual_record['total']}/100（{manual_record['verdict']}，"
              f"trigger={manual_record['trigger']}）｜维度 {len(manual_record['axes'])} 个｜"
              f"建议 {len(manual_record['suggestions'])} 条")
        ok = ok and manual_record["task_id"] == task_id
        ok = ok and manual_record["trigger"] == "manual"
        ok = ok and manual_record["mode"] == "offline"
        ok = ok and manual_record["total"] > 0

        # 评估门禁：advisory 只记录不改裁决；blocking 下低分必须触发返工
        gate_events = [
            e for e in call("GET", f"/api/tasks/{task_id}").json()["events"]
            if e["type"] == "gate.decision"
        ]
        judged_gate = next(
            (e for e in gate_events if (e.get("payload") or {}).get("judge")), None
        )
        print(f"  门禁事件 {len(gate_events)} 条｜含评估结论="
              f"{(judged_gate['payload']['judge'] or {}).get('total') if judged_gate else '无'}")
        ok = ok and judged_gate is not None

        print("\n[GET /api/tasks/{id}/trace（调用轨迹）]")
        trace = call("GET", f"/api/tasks/{task_id}/trace").json()
        spans = trace["spans"]
        roots = [s for s in spans if s["parent_span_id"] is None]
        llm_spans = [s for s in spans if s["name"].startswith("llm.")]
        agent_spans = [s for s in spans if s.get("agent_id")]
        print(f"  trace={str(trace['trace_id'])[:8]} span={len(spans)} 根={len(roots)} "
              f"总耗时={trace['summary']['duration_ms']}ms")
        print(f"  智能体 span={len(agent_spans)}｜llm span={len(llm_spans)}｜"
              f"最贵={trace['summary']['by_name'][0]['name']}"
              f"（{trace['summary']['by_name'][0]['total_ms']}ms）")
        ok = ok and bool(trace["trace_id"]) and len(spans) >= 20
        # 树而非平铺列表：根 span 必须少于总数，否则追踪对排障没有助益
        ok = ok and len(roots) < len(spans)
        ok = ok and len(agent_spans) >= 11 and len(llm_spans) >= 11
        ok = ok and bool(trace["summary"]["by_name"])
        ok = ok and trace["export"]["format"] == "otel-shaped-json"
        # 事件与 span 的关联（排查时不必靠时间戳猜归属）
        trace_events = call("GET", f"/api/tasks/{task_id}").json()["events"]
        ok = ok and any(e.get("span_id") for e in trace_events)
        ok = ok and {e["trace_id"] for e in trace_events if e.get("trace_id")} == {
            trace["trace_id"]
        }
        missing_trace = call("GET", "/api/tasks/nope/trace")
        print(f"  跨任务/不存在任务的 trace → {missing_trace.status_code}（应为 404）")
        ok = ok and missing_trace.status_code == 404

        print("\n[错误分支]")
        detail_404 = call("GET", "/api/tasks/nope").status_code
        bad_decision = call(
            "POST", f"/api/tasks/{task_id}/decide", json={"decision": "x"}
        ).status_code
        dup_decide = call(
            "POST", f"/api/tasks/{task_id}/decide", json={"decision": "approve"}
        ).status_code
        running = call(
            "POST", "/api/tasks", json={"brief": BRIEF, "autoApprove": True}
        ).json()["task"]
        del_running = call("DELETE", f"/api/tasks/{running['id']}").status_code
        print(f"  404 详情={detail_404}  400 非法 decision={bad_decision}")
        print(f"  409 重复 decide={dup_decide}  409 删除运行中={del_running}")
        ok = ok and (detail_404, bad_decision, dup_decide, del_running) == (404, 400, 409, 409)

        finished = wait_for(
            lambda: task_of(running["id"])["status"] in ("completed", "rejected", "failed"),
            timeout=60,
            label="等待第三个任务结束",
        )
        snapshot = task_of(running["id"])
        print(f"  第三个任务 status={snapshot['status']} phase={snapshot['phase']} "
              f"turn={snapshot['turn_used']} error={snapshot['error']}")
        print("  节点：" + " ".join(f"{n['agent_id']}:{n['status']}" for n in snapshot["pipeline"]))
        ok = ok and finished

        deleted = call("DELETE", f"/api/tasks/{running['id']}")
        print(f"  已完成任务 DELETE → {deleted.status_code} {deleted.json()}")
        ok = ok and deleted.status_code == 200
        ok = ok and call("GET", f"/api/tasks/{running['id']}").status_code == 404

        print("\n[持久化核对]")
        print(f"  tasks 文件={len(list((TMP / 'tasks').glob('*.json')))} "
              f"checkpoint={(TMP / 'checkpoints.sqlite').exists()} "
              f"blackboard={(TMP / 'blackboard.json').exists()} "
              f"memory={(TMP / 'memory.json').exists()} "
              f"evaluations={(TMP / 'evaluations.json').exists()}")
        ok = ok and (TMP / "checkpoints.sqlite").exists() and (TMP / "blackboard.json").exists()
        ok = ok and (TMP / "memory.json").exists()
        ok = ok and (TMP / "evaluations.json").exists()

        # ---- 黄金数据集放最后：它会真的建任务并跑完整流水线，会改变任务计数，
        #      因此前面的「精确计数」断言不受它影响，也不需要为它放宽。
        print("\n[黄金数据集（plan.md D13）]")
        golden = call("GET", "/api/golden").json()
        cov = golden["coverage"]
        print(f"  用例 {len(golden['cases'])} 条｜覆盖渠道 {len(cov['channels'])}"
              f"｜未覆盖渠道 {cov['uncovered_channels'] or '无'}")
        print(f"  基线 {len(golden['baseline']['cases'])} 条｜rubric={golden['rubric']}"
              f"｜容差 ±{golden['tolerance']}｜运行中={golden['state']['running']}")
        ok = ok and len(golden["cases"]) >= 10
        ok = ok and not cov["uncovered_channels"]
        ok = ok and golden["rubric"] == golden["baseline"]["rubric"]
        # 用例结构必须完整：改数据集时漏字段会在这里被拦住
        ok = ok and all(
            {"id", "channel", "industry", "brief"} <= set(item) for item in golden["cases"]
        )

        # 单跑一条用例：验证后台任务链路（202 → 轮询 → 结果 → 对比结论）
        started_golden = call("POST", "/api/golden/run", json={"caseIds": ["xiaohongshu_food"]})
        print(f"  POST /api/golden/run → {started_golden.status_code}"
              f"｜total={started_golden.json()['state']['total']}")
        ok = ok and started_golden.status_code == 202
        # 运行中再次触发必须 409（不排队）：双触发被静默吞掉会让人以为跑了两遍
        dup_run = call("POST", "/api/golden/run", json={"caseIds": ["xiaohongshu_food"]})
        print(f"  运行中重复触发 → {dup_run.status_code}（应为 409）")
        ok = ok and dup_run.status_code == 409

        finished_golden = wait_for(
            lambda: call("GET", "/api/golden").json()["state"]["running"] is False,
            timeout=180,
            label="黄金用例运行完成",
        )
        golden = call("GET", "/api/golden").json()
        state = golden["state"]
        comparison = golden.get("comparison") or {}
        result = state["results"][0] if state["results"] else {}
        print(f"  运行结果：{state['completed']}/{state['total']}｜{result.get('id')} "
              f"status={result.get('status')} 质量分={result.get('quality_score')} "
              f"评估分={result.get('judge_final_total')} 返工={result.get('revision_round')}")
        print(f"  对比结论：ok={comparison.get('ok')} counts={comparison.get('counts')} "
              f"partial={comparison.get('partial')}")
        ok = ok and finished_golden
        ok = ok and state["completed"] == 1 and not state["error"]
        ok = ok and result.get("status") == "completed"
        # 部分运行：范围内用例应与基线一致（离线评估器确定性），且显式标注 partial
        ok = ok and comparison.get("partial") is True
        ok = ok and comparison.get("ok") is True

        reset = call("POST", "/api/golden/reset")
        print(f"  POST /api/golden/reset → {reset.status_code} ok={reset.json().get('ok')}")
        ok = ok and reset.status_code == 200
        ok = ok and call("GET", "/api/golden").json()["state"]["results"] == []

        # 最后一个环节：另起一个带鉴权的实例验证租户隔离（不污染上面的数据目录）
        ok = check_tenant_isolation() and ok
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_handle.close()
        lines = SERVER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        errors = [ln for ln in lines if "ERROR" in ln or "Traceback" in ln]
        print(f"\n[服务端日志尾部，共 {len(lines)} 行]")
        print("\n".join(lines[-12:]))
        if errors:
            print("\n[服务端异常]")
            print("\n".join(errors[:10]))

    print("\n结果：", "通过" if ok else "未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
