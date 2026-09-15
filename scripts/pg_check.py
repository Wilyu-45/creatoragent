"""PG 存储层直连冒烟（不起 HTTP，仅 ``CREATOR_STORAGE=pg`` 下运行）。

与 ``verify_contracts.py`` 的分工
---------------------------------
``verify_contracts.py`` 拉起真实服务走 HTTP 契约；本脚本**直连存储层**，
验证 HTTP 之下最容易漂移的部分：

* 五类 Store 的读写语义（任务 / 黑板 / 记忆库 / 评估 / 数字人作业）；
* 跨副本可见性（两个独立实例互相读写 = 多进程部署的最小仿真）；
* 租户隔离（A 租户的数据对 B 租户不可见）；
* 意图租约三态（claimed / renewed / conflict）与 TTL 过期；
* 断点检查点为 ``postgres``（PostgresSaver setup 成功）。

全部写入使用 ``pgcheck-`` 前缀的临时任务/租户，结束时清理，可反复重跑。

用法（先起本地库）::

    docker compose --profile pg up -d postgres redis
    $env:CREATOR_STORAGE='pg'
    python scripts/pg_check.py          # 退出码 0 表示全部符合预期
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if (os.environ.get("CREATOR_STORAGE") or "file").strip().lower() != "pg":
    print("本脚本只在 CREATOR_STORAGE=pg 下运行（file 模式请用 doctor / verify_contracts）")
    raise SystemExit(1)

from app.core.clock import now_iso  # noqa: E402
from app.core.pg import database_url, mask_url, pg_pool  # noqa: E402
from app.core.pg_schema import ensure_pg_schema  # noqa: E402
from app.core.redis_client import ping_redis  # noqa: E402
from app.logger import create_logger  # noqa: E402

log = create_logger("pg_check")

failures: list[str] = []


def check(label: str, actual: object, expected: object, detail: str = "") -> None:
    good = actual == expected
    message = "" if good else f"（期望 {expected!r}）"
    if not good and detail:
        message += f" {detail}"
    print(f"  {'OK  ' if good else 'FAIL'} {label}: {actual!r}{message}")
    if not good:
        failures.append(label)


# ------------------------------------------------------------------ #
# 各存储分项                                                          #
# ------------------------------------------------------------------ #


def check_task_store(task_id: str, tenant_a: str, tenant_b: str) -> None:
    print("[PgTaskStore：写入 / 跨副本可见 / 删除]")
    from app.core.store_pg import PgTaskStore
    from app.core.types import TaskRecord

    replica_a = PgTaskStore()
    replica_b = PgTaskStore()  # 独立实例 = 独立副本的最小仿真

    task = TaskRecord(
        id=task_id, tenant=tenant_a, status="running",
        created_at=now_iso(), updated_at=now_iso(),
    )
    replica_a.save(task)
    seen = replica_b.get(task_id)
    check("副本 B 能读到副本 A 新写入的任务", bool(seen), True)
    check("租户字段原样", str(seen.tenant) if seen else "", tenant_a)

    replica_b.list()  # 不抛异常即可（直查库）
    interrupted = replica_b.load_all()
    check("load_all 返回结构", sorted(interrupted), ["interrupted", "loaded"])

    replica_b.delete(task_id)
    check("删除后不可见", replica_a.get(task_id), None)


def check_blackboard(task_id: str) -> None:
    print("[PgBlackboard：事实去重升级 / 活动 / 产物取号 / 评审 / 意图租约]")
    from app.core.blackboard_pg import PgBlackboard

    replica_a = PgBlackboard()
    replica_b = PgBlackboard()

    first = replica_a.add_fact(
        task_id=task_id, agent_id="A6", claim="  成分A 通过 SGS 检测  ",
        source="https://example.com/report", confidence=0.7,
    )
    duplicate = replica_b.add_fact(
        task_id=task_id, agent_id="A6", claim="成分A 通过 SGS 检测",
        source="https://example.com/report", confidence=0.7,
    )
    check("同 claim 跨副本去重（返回已有条目）", duplicate.id, first.id)
    check("事实表只有一行", len(replica_a.facts(task_id)), 1)

    replica_b.add_fact(
        task_id=task_id, agent_id="A6", claim="成分A 通过 SGS 检测",
        source="https://example.com/report", status="verified", confidence=0.9,
    )
    facts = replica_a.facts(task_id)
    check("verified 升级跨副本可见", facts[0].status, "verified")

    replica_a.add_activity(task_id, "A3", "outline_built", f"{task_id}:outline")
    check("活动签名跨副本可查", replica_b.has_activity(task_id, f"{task_id}:outline"), True)

    version_before = replica_b.next_version(task_id, "copy_draft")
    check("next_version 首次为 1", version_before, 1)
    from app.core.types import Artifact

    artifact = Artifact(
        id=f"{task_id}-art-1", task_id=task_id, agent_id="A4",
        type="copy_draft", version=1, title="初稿", created_at=now_iso(),
    )
    replica_a.put_artifact(artifact)
    check("副本 B 能取到副本 A 的产物", (replica_b.latest_artifact(task_id, "copy_draft") or Artifact()).id, artifact.id)
    check("落库后取号 +1", replica_b.next_version(task_id, "copy_draft"), 2)

    from app.core.types import ReviewItem

    replica_a.add_review(
        task_id=task_id, agent_id="A5", target_artifact_id=artifact.id,
        verdict="pass", score=88, items=[ReviewItem(category="tone", detail="语气达标")],
    )
    check("评审跨副本可见", len(replica_b.reviews(task_id)), 1)

    stats = replica_a.snapshot(task_id).stats
    check(
        "快照统计（事实/产物/评审）",
        (stats.fact_count, stats.artifact_count, stats.review_count),
        (1, 1, 1),
    )

    # 意图租约三态 + TTL 过期
    entry, outcome = replica_a.acquire_intent(task_id, "A4", "write")
    check("首次获取 → claimed", outcome, "claimed")
    _, outcome = replica_a.acquire_intent(task_id, "A4", "write")
    check("同智能体重取 → renewed", outcome, "renewed")
    conflict, outcome = replica_b.acquire_intent(task_id, "A5", "write")
    check("他者抢占 → conflict", outcome, "conflict")
    check("冲突时返回持有者", bool(conflict) and str(conflict.agent_id), "A4")
    check("active_intents 能列出", len(replica_b.active_intents(task_id)) >= 1, True)

    holder, outcome = replica_a.acquire_intent(task_id, "A9", "publish", ttl_ms=150)
    check("短 TTL 获取 → claimed", outcome, "claimed")
    time.sleep(0.3)
    _, outcome = replica_b.acquire_intent(task_id, "A10", "publish")
    check("TTL 过期后他人可获取", outcome, "claimed")
    check("回收任务租约", replica_a.release_task_intents(task_id) >= 1, True)
    check("回收后无活跃租约", replica_b.active_intents(task_id), [])


def check_memory(task_id: str, tenant_a: str, tenant_b: str) -> None:
    print("[PgMemoryStore：去重 / 检索 / 租户隔离]")
    # 注意入口顺序：真实应用总是先加载 memory.py（单例在文件底部实例化），
    # 再由其条件分支加载 memory_pg。直接 import memory_pg 会撞上循环 import。
    import app.knowledge.memory  # noqa: F401
    from app.knowledge.memory_pg import PgMemoryStore

    store_a = PgMemoryStore()
    store_b = PgMemoryStore()

    cards = [{
        "type": "brand",
        "title": "品牌调性：真实感优先",
        "content": "所有文案必须口语化，禁止堆砌形容词，多用短句与生活场景",
        "tags": ["调性", "口语化"],
        "reuse_hint": "写初稿前先对照",
    }]
    added = store_a.remember(
        task_id=task_id, brand="pgcheck", channel="小红书", industry="消费品",
        cards=cards, tenant=tenant_a, language="zh",
    )
    check("首次入库计数", added, 1)
    added = store_b.remember(
        task_id=task_id, brand="pgcheck", channel="小红书", industry="消费品",
        cards=cards, tenant=tenant_a, language="zh",
    )
    check("同内容重复入库去重", added, 0)

    hits = store_b.retrieve("文案 口语化 短句", brand="pgcheck", tenant=tenant_a, language="zh")
    check("副本 B 能检索到副本 A 沉淀的知识", len(hits) >= 1, True)
    hits_b_tenant = store_b.retrieve("文案 口语化 短句", brand="pgcheck", tenant=tenant_b)
    check("租户隔离：B 租户不可见", hits_b_tenant, [])

    stats = store_b.stats(tenant=tenant_a)
    check("stats 计数", stats["total"] >= 1, True)
    all_stats = store_b.stats()
    check("stats 全局视角可执行（SQL 拼接无误）", isinstance(all_stats["total"], int), True)


def check_evaluations(task_id: str, tenant_a: str, tenant_b: str) -> None:
    print("[PgEvaluationStore：记录 / 最新 / 租户过滤]")
    # 同 check_memory：先加载 file 版模块，避免「evaluations → evaluations_pg」循环 import
    import app.core.evaluations  # noqa: F401
    from app.core.evaluations import EvaluationRecord
    from app.core.evaluations_pg import PgEvaluationStore

    store_a = PgEvaluationStore()
    store_b = PgEvaluationStore()

    entry = store_a.record(
        EvaluationRecord(
            id=f"{task_id}-eval-1", task_id=task_id, tenant=tenant_a,
            created_at=now_iso(), total=82.5, verdict="pass",
            axes=[], issues=[], suggestions=[],
        )
    )
    latest = store_b.latest(task_id)
    check("副本 B 能读到副本 A 的评估", bool(latest) and latest.id, entry.id)

    check("租户过滤：B 租户不可见", store_b.latest(task_id, tenant=tenant_b), None)
    stats = store_b.stats(tenant=tenant_a)
    check("stats 聚合", stats["total"] >= 1, True)


def check_digital_human(task_id: str, tenant_a: str) -> None:
    print("[PgJobBackend：创建 / 可见 / 回收]")
    from app.core.dh_jobs_pg import PgJobBackend

    backend_a = PgJobBackend()
    backend_b = PgJobBackend()

    job = {
        "id": f"{task_id}-dh-1", "task_id": task_id, "tenant": tenant_a,
        "provider": "sample", "status": "queued", "progress": 0,
        "created_at": now_iso(), "updated_at": now_iso(),
        "manifest": {"segments": []}, "history": [],
    }
    backend_a.create(job)
    snapshot = backend_b.snapshot()
    check("副本 B 能看到副本 A 创建的作业", any(j.get("id") == job["id"] for j in snapshot), True)
    check("回收任务作业", backend_b.drop_task(task_id), 1)
    check("回收后不可见", [j for j in backend_a.snapshot() if j.get("task_id") == task_id], [])


def check_checkpointer() -> None:
    print("[Checkpointer：PostgresSaver]")
    from app.core.orchestrator import orchestrator

    check("checkpointer_kind", orchestrator.checkpointer_kind, "postgres")
    check("无降级报错", orchestrator.checkpointer_error, "")


# ------------------------------------------------------------------ #
# 清理与入口                                                          #
# ------------------------------------------------------------------ #


def cleanup(task_id: str) -> None:
    """回收本脚本产生的全部数据（按 task_id 精确删除；失败不影响退出码判定）。"""
    from app.core.blackboard_pg import PgBlackboard

    try:
        PgBlackboard().release_task_intents(task_id)
        with pg_pool().connection() as conn:
            # tasks 表的主键列是 id（其余表才有 task_id 外键列）
            conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
            for table in ("bb_facts", "bb_activities", "bb_artifacts",
                          "bb_reviews", "evaluations", "digital_human_jobs", "memory_cards"):
                conn.execute(f"DELETE FROM {table} WHERE task_id = %s", (task_id,))
    except Exception as error:  # noqa: BLE001
        log.warn(f"清理 pgcheck 数据失败（请手工按 task_id={task_id} 删除）", error)


def main() -> int:
    print(f"目标库：{mask_url(database_url())}")
    print("[启动自检（fail-loud）]")
    ensure_pg_schema()
    ping_redis()
    print("  OK   schema 就绪、Redis 连通")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_id = f"pgcheck-{stamp}"
    tenant_a = f"pgcheck-a-{stamp}"
    tenant_b = f"pgcheck-b-{stamp}"

    try:
        check_task_store(task_id, tenant_a, tenant_b)
        check_blackboard(task_id)
        check_memory(task_id, tenant_a, tenant_b)
        check_evaluations(task_id, tenant_a, tenant_b)
        check_digital_human(task_id, tenant_a)
        check_checkpointer()
    finally:
        cleanup(task_id)

    print(f"\n==== 结果：{len(failures)} 项失败 ====")
    if failures:
        for failure in failures:
            print(f"  FAIL {failure}")
        return 1
    print("全部通过：PG 存储层读写、跨副本可见性、租户隔离、意图租约、检查点均符合契约")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
