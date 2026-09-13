"""端到端验收：拉起真实 uvicorn 进程，逐条核对 ``/api/*`` 与 SSE 契约。

覆盖范围
--------
* 基础信息：``/api/health``、``/api/agents``、``/api/knowledge``、``/api/settings``
* 主流程：建任务 → SSE 实时事件 → 挂起等待人工 → ``decide`` → 归档交付
* 自动审批：``autoApprove`` 走完全程
* 记忆闭环：``/api/memory``、``/api/memory/search``，以及第二个任务是否真的复用了第一个任务沉淀的知识
* 发布闭环：``/api/tasks/{id}/publish`` 登记发布、``/api/tasks/{id}/feedback`` 回填效果并触发 A10 复盘
* 聚合视图：``/api/tasks``、``/api/metrics``（含成本 / 缓存 / 黑板租约指标）
* 错误分支：404 / 400 / 409（重复裁决、删除运行中任务）
* 持久化：任务 JSON、``checkpoints.sqlite``、``blackboard.json``、``memory.json``

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
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="creator-api-"))
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
            json={"qualityThreshold": 70, "temperature": 0.3, "model": "gpt-4o-mini"},
        ).json()
        print(f"  threshold {before['qualityThreshold']} → {updated['qualityThreshold']} "
              f"| temperature {updated['llm']['temperature']}")
        ok = ok and updated["qualityThreshold"] == 70 and updated["llm"]["temperature"] == 0.3

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
        ok = ok and stats["total"] > 0

        search = call(
            "POST",
            "/api/memory/search",
            json={"query": "冷萃 咖啡 早八通勤 小红书", "topK": 3},
        ).json()
        hit_text = "；".join(f"{h['title']}({h['score']})" for h in search["hits"][:3])
        print(f"  检索命中 {len(search['hits'])} 条" + (f"：{hit_text}" if hit_text else ""))
        ok = ok and bool(search["hits"])

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
        ok = ok and {"cost", "cache", "leases"} <= set(metrics)
        # 任务全部结束，说明租约都已成对释放（无残留）
        ok = ok and leases["active"] == 0

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
              f"memory={(TMP / 'memory.json').exists()}")
        ok = ok and (TMP / "checkpoints.sqlite").exists() and (TMP / "blackboard.json").exists()
        ok = ok and (TMP / "memory.json").exists()
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
