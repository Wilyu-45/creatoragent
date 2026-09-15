"""file 模式 → PostgreSQL 的一次性数据迁移（幂等，可重入）。

迁移范围（plan.md 5.1 / storage_contract.md）
---------------------------------------------
只迁 **5 类业务 JSON**；``checkpoints.sqlite`` **不迁**（LangGraph 检查点是
不透明二进制，跨后端无迁移语义）——状态仍为 ``running / awaiting_approval``
的旧中断任务在 pg 模式首次启动时由断点续跑逻辑明确标失败，本脚本在报告中列出。

* ``data/tasks/*.json``       → ``tasks``
* ``data/blackboard.json``    → ``bb_facts / bb_activities / bb_artifacts / bb_reviews``
  （``intents`` 是 TTL 租约，权威源在 Redis，不迁、报告跳过条数）
* ``data/memory.json``        → ``memory_cards``（digest 按现行算法重算，命中计数保留）
* ``data/evaluations.json``   → ``evaluations``
* ``data/digital_human.json`` → ``digital_human_jobs``

幂等性：全部 ``INSERT ... ON CONFLICT DO NOTHING``（不带冲突目标，任何唯一键
冲突都按跳过处理），重复执行安全；``--dry-run`` 只读不写。

用法::

    python scripts/pg_migrate.py --dry-run          # 只核对，不写库
    python scripts/pg_migrate.py                    # 迁移 + 计数核对报告
    python scripts/pg_migrate.py --data-dir D:/old/data --url postgresql://...

前提：先建库建表（``docker compose --profile pg up -d postgres redis`` 即可，
schema 由本脚本自动建齐）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JSON → PostgreSQL 数据迁移（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只读取与核对，不写库")
    parser.add_argument("--data-dir", default="", help="源数据目录（默认 data/ 或 CREATOR_DATA_DIR）")
    parser.add_argument("--url", default="", help="目标连接串（默认 CREATOR_DATABASE_URL 或本地库）")
    return parser.parse_args()


_ARGS = _parse_args()

# app.config 在 import 时读环境变量，因此必须在导入前覆写
if _ARGS.data_dir:
    os.environ["CREATOR_DATA_DIR"] = _ARGS.data_dir
if _ARGS.url:
    os.environ["CREATOR_DATABASE_URL"] = _ARGS.url

from app.config import BLACKBOARD_FILE, DATA_DIR, EVAL_FILE, MEMORY_FILE, TASK_DIR  # noqa: E402
from app.core.pg import database_url, mask_url, pg_pool  # noqa: E402
from app.core.pg_schema import ensure_pg_schema  # noqa: E402
from app.knowledge.memory import DEFAULT_TENANT, MemoryCard, _digest  # noqa: E402
from app.core.types import TaskRecord  # noqa: E402

# psycopg 不会自动把 dict 适配成 jsonb：payload 列必须显式包 Json（其余
# migrate_* 用 json.dumps 字符串，这里保持对象写法以便与库内 jsonb 直读一致）
from psycopg.types.json import Json  # noqa: E402

DH_FILE = DATA_DIR / "digital_human.json"

failures: list[str] = []


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        failures.append(f"{path.name} 读取失败：{error}")
        return None
    return raw if isinstance(raw, dict) else None


def fact_digest(claim: str) -> str:
    """与 blackboard_pg.add_fact 同口径的事实指纹。"""
    return hashlib.sha1(claim.strip().encode("utf-8")).hexdigest()[:16]


def migrate_tasks(conn: Any) -> dict[str, int]:
    source = 0
    interrupted = 0
    inserted = 0
    skipped = 0
    payloads: list[tuple[str, str, str, str, dict]] = []
    for path in sorted(TASK_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            task = TaskRecord.model_validate(payload)
        except Exception as error:  # noqa: BLE001 - 损坏记录跳过并报告
            skipped += 1
            failures.append(f"任务 {path.name} 无法解析：{error}")
            continue
        source += 1
        if task.status in ("running", "awaiting_approval"):
            interrupted += 1
        payloads.append(
            (
                task.id,
                task.tenant or "default",
                task.status,
                task.created_at,
                task.model_dump(mode="json"),
            )
        )
    if payloads and not _ARGS.dry_run:
        for task_id, tenant, status, created_at, payload in payloads:
            cursor = conn.execute(
                "INSERT INTO tasks (id, tenant, status, created_at, updated_at, payload) "
                "VALUES (%s, %s, %s, %s, now(), %s) ON CONFLICT DO NOTHING",
                (task_id, tenant, status, created_at, json.dumps(payload, ensure_ascii=False)),
            )
            inserted += cursor.rowcount
    return {"source": source, "inserted": inserted, "skipped": skipped, "interrupted": interrupted}


def migrate_blackboard(conn: Any) -> dict[str, int]:
    raw = read_json(BLACKBOARD_FILE) or {}
    counts = {"source": 0, "inserted": 0, "skipped_intents": 0}
    for kind, sql, extract in (
        (
            "facts",
            "INSERT INTO bb_facts (id, task_id, agent_id, claim_digest, status, created_at, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            lambda f: (
                f.get("id"), f.get("task_id"), f.get("agent_id"),
                fact_digest(str(f.get("claim") or "")), f.get("status"),
                f.get("created_at"), Json(f),
            ),
        ),
        (
            "activities",
            "INSERT INTO bb_activities (id, task_id, agent_id, signature, created_at, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            lambda a: (
                a.get("id"), a.get("task_id"), a.get("agent_id"),
                a.get("signature"), a.get("created_at"), Json(a),
            ),
        ),
        (
            "artifacts",
            "INSERT INTO bb_artifacts (id, task_id, type, version, created_at, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            lambda a: (
                a.get("id"), a.get("task_id"), a.get("type"),
                int(a.get("version") or 1), a.get("created_at"), Json(a),
            ),
        ),
        (
            "reviews",
            "INSERT INTO bb_reviews (id, task_id, target_artifact_id, created_at, payload) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            lambda r: (
                r.get("id"), r.get("task_id"), r.get("target_artifact_id"),
                r.get("created_at"), Json(r),
            ),
        ),
    ):
        rows = [item for item in (raw.get(kind) or []) if isinstance(item, dict)]
        counts["source"] += len(rows)
        if rows and not _ARGS.dry_run:
            for item in rows:
                counts["inserted"] += conn.execute(sql, extract(item)).rowcount
    counts["skipped_intents"] = len(raw.get("intents") or [])
    return counts


def migrate_memory(conn: Any) -> dict[str, int]:
    raw = read_json(MEMORY_FILE) or {}
    items = [item for item in (raw.get("cards") or []) if isinstance(item, dict)]
    inserted = 0
    skipped = 0
    cards: list[MemoryCard] = []
    for item in items:
        try:
            cards.append(MemoryCard(**item))
        except TypeError as error:
            skipped += 1
            failures.append(f"记忆卡片 {item.get('id')} 无法解析：{error}")
    if cards and not _ARGS.dry_run:
        for card in cards:
            owner = card.tenant or DEFAULT_TENANT
            digest = _digest(owner, card.kind, card.title, card.content)
            cursor = conn.execute(
                "INSERT INTO memory_cards (id, tenant, digest, task_id, brand, channel, "
                "industry, kind, language, title, content, revision, hits, created_at, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (
                    card.id, owner, digest, card.task_id, card.brand, card.channel,
                    card.industry, card.kind, card.language, card.title, card.content,
                    card.revision, card.hits, card.created_at,
                    json.dumps(card.to_dict(), ensure_ascii=False),
                ),
            )
            inserted += cursor.rowcount
    return {"source": len(items), "inserted": inserted, "skipped": skipped}


def migrate_evaluations(conn: Any) -> dict[str, int]:
    raw = read_json(EVAL_FILE) or {}
    items = [item for item in (raw.get("records") or []) if isinstance(item, dict)]
    inserted = 0
    if items and not _ARGS.dry_run:
        for record in items:
            cursor = conn.execute(
                "INSERT INTO evaluations (id, tenant, task_id, kind, revision, total, "
                "verdict, created_at, payload) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (
                    record.get("id"), record.get("tenant") or "default", record.get("task_id"),
                    record.get("kind") or "final", int(record.get("revision") or 0),
                    float(record.get("total") or 0), record.get("verdict") or "review",
                    record.get("created_at"), json.dumps(record, ensure_ascii=False),
                ),
            )
            inserted += cursor.rowcount
    return {"source": len(items), "inserted": inserted}


def migrate_digital_human(conn: Any) -> dict[str, int]:
    raw = read_json(DH_FILE) or {}
    items = [item for item in (raw.get("jobs") or []) if isinstance(item, dict) and item.get("id")]
    inserted = 0
    if items and not _ARGS.dry_run:
        for job in items:
            cursor = conn.execute(
                "INSERT INTO digital_human_jobs (id, task_id, tenant, status, created_at, "
                "updated_at, payload) VALUES (%s, %s, %s, %s, %s, now(), %s) "
                "ON CONFLICT DO NOTHING",
                (
                    str(job["id"]), str(job.get("task_id") or ""),
                    str(job.get("tenant") or "default"), str(job.get("status") or "queued"),
                    str(job.get("created_at") or ""), json.dumps(job, ensure_ascii=False),
                ),
            )
            inserted += cursor.rowcount
    return {"source": len(items), "inserted": inserted}


def main() -> int:
    print(f"目标库：{mask_url(database_url())}")
    print(f"源目录：{DATA_DIR}" + ("（dry-run，不写库）" if _ARGS.dry_run else ""))
    ensure_pg_schema()

    report: dict[str, dict[str, int]] = {}
    with pg_pool().connection() as conn:
        report["tasks"] = migrate_tasks(conn)
        report["blackboard"] = migrate_blackboard(conn)
        report["memory_cards"] = migrate_memory(conn)
        report["evaluations"] = migrate_evaluations(conn)
        report["digital_human_jobs"] = migrate_digital_human(conn)
        if not _ARGS.dry_run:
            # 计数核对：库里行数 ≥ 源条数（同库重复迁移或历史数据会只多不少）
            for table in ("tasks", "bb_facts", "bb_activities", "bb_artifacts", "bb_reviews",
                          "memory_cards", "evaluations", "digital_human_jobs"):
                row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
                report.setdefault("_db_counts", {})[table] = int(row[0])

    print("\n==== 迁移报告 ====")
    for name, counts in report.items():
        if name == "_db_counts":
            print(f"库内行数核对：{counts}")
            continue
        extra = ""
        if name == "tasks" and counts.get("interrupted"):
            extra = (
                f"；其中 {counts['interrupted']} 个中断任务（checkpoints.sqlite 不迁移，"
                "pg 模式首次启动时会被明确标记为失败）"
            )
        if name == "blackboard" and counts.get("skipped_intents"):
            extra = f"；跳过 {counts['skipped_intents']} 条 TTL 意图租约（权威源在 Redis，不迁移）"
        print(f"  {name}: {counts}{extra}")

    if failures:
        print(f"\n{len(failures)} 条记录跳过：")
        for failure in failures:
            print(f"  - {failure}")
    if _ARGS.dry_run:
        print("\ndry-run 结束：未写入任何数据。去掉 --dry-run 执行真正迁移。")
    elif failures:
        print("\n迁移完成（含跳过项，请人工核对上方清单）")
    else:
        print("\n迁移完成：全部记录已入库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
