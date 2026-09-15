"""共享黑板的 PostgreSQL + Redis 实现（仅 ``CREATOR_STORAGE=pg`` 时实例化）。

接口契约见 storage_contract.md §3.2 与 :mod:`app.core.blackboard`（file 版）。
存储拆分：

* **事实 / 活动 / 产物 / 评审** → PostgreSQL 四张表（``bb_facts / bb_activities /
  bb_artifacts / bb_reviews``），payload jsonb 存全量；
* **意图租约** → Redis（``SET key value PX ttl``）。file 版是进程内
  ``threading.RLock`` + TTL 清扫，多副本下两进程会各自拿到同一把「锁」——
  Redis TTL 过期天然等价于 ``_sweep_expired_intents``，三态原子判定用 Lua 实现。

与 file 版的已知等价窗口（不追求超越现状的严格性，均有记录）：

* ``next_version`` 在 file 版本就与 ``put_artifact`` 是两次独立加锁调用，
  PG 版用 ``pg_advisory_xact_lock`` 收窄但未消除该窗口；version 重复无害
  （产物主键全局唯一，排序按 created_at）；
* ``add_fact`` 的 verified 升级在极端并发下可能少升级一次（file 版进程内串行，
  PG 版跨副本无法严格串行），后续任一次重复声明都会补上。
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from ..logger import create_logger
from .clock import iso_in, now_iso
from .events import new_id
from .redis_client import redis_client, redis_prefix
from .types import (
    ActivityEntry,
    Artifact,
    BlackboardSnapshot,
    BlackboardStats,
    FactEntry,
    IntentEntry,
    ReviewEntry,
    ReviewItem,
)

log = create_logger("blackboard_pg")

INTENT_TTL_MS = 120_000

#: 意图租约三态原子判定：
#: KEYS[1] = 完整 redis key；ARGV = [新 value JSON, ttl_ms, agent_id]
#: 续期时保留原 created_at（与 file 版语义一致：续期只动 expires_at）。
_ACQUIRE_LUA = """
local cur = redis.call('GET', KEYS[1])
local value = ARGV[1]
if cur then
  local ok, old = pcall(cjson.decode, cur)
  local ok2, new = pcall(cjson.decode, ARGV[1])
  if ok and ok2 and old['created_at'] then
    new['created_at'] = old['created_at']
    value = cjson.encode(new)
  end
end
if not cur then
  redis.call('SET', KEYS[1], value, 'PX', ARGV[2])
  return 'claimed'
elseif cjson.decode(cur)['agent_id'] == ARGV[3] then
  redis.call('SET', KEYS[1], value, 'PX', ARGV[2])
  return 'renewed'
else
  return 'conflict'
end
"""

_FACT_UPSERT = """
INSERT INTO bb_facts (id, task_id, agent_id, claim_digest, status, created_at, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (task_id, claim_digest) DO NOTHING
"""

_ACTIVITY_UPSERT = """
INSERT INTO bb_activities (id, task_id, agent_id, signature, created_at, payload)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (task_id, signature) DO NOTHING
"""

_LUA_SCRIPTS: dict[str, Any] = {}
_LUA_LOCK = threading.RLock()


def _acquire_script(client):
    """注册并缓存 Lua 脚本（SCRIPT LOAD 语义，连接/重启后自动重发）。"""
    with _LUA_LOCK:
        script = _LUA_SCRIPTS.get("acquire")
        if script is None:
            script = client.register_script(_ACQUIRE_LUA)
            _LUA_SCRIPTS["acquire"] = script
        return script


def _intent_key(task_id: str, direction: str) -> str:
    return f"{redis_prefix()}intent:{task_id}:{direction}"


def _entry_key(task_id: str, direction: str) -> str:
    """IntentEntry.key 与 file 版同口径：``task_id::direction``。"""
    return f"{task_id}::{direction}"


def _redis_key_of_entry(key: str) -> str:
    """把 file 版 entry key（``task_id::direction``）映射回 redis key。"""
    task_id, _, direction = key.partition("::")
    return _intent_key(task_id, direction)


def _entry_from_value(key: str, value: str) -> IntentEntry:
    data = json.loads(value)
    return IntentEntry(
        key=key,
        task_id=str(data.get("task_id") or ""),
        agent_id=str(data.get("agent_id") or "A0"),
        direction=str(data.get("direction") or ""),
        expires_at=str(data.get("expires_at") or ""),
        created_at=str(data.get("created_at") or ""),
    )


def _scan_intent_keys(pattern: str) -> list[str]:
    """SCAN 全量收集（租约规模 = 每任务几个方向 × 并发任务，代价可忽略）。"""
    client = redis_client()
    keys: list[str] = []
    cursor = 0
    while True:
        cursor, batch = client.scan(cursor=cursor, match=pattern, count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    return keys


class PgBlackboard:
    """与 :class:`app.core.blackboard.Blackboard` 同接口的 PG + Redis 后端。"""

    # --------------------------- 持久化 --------------------------- #

    def load(self) -> None:
        """PG 模式无需预载（每次查询直达数据库）；保留签名供 lifespan 调用。"""

    def flush(self) -> None:
        """写入即持久（每方法一个短事务）；保留签名兼容退出钩子。"""

    # ----------------------------- 意图 ---------------------------- #

    def declare_intent(
        self, task_id: str, agent_id: str, direction: str, ttl_ms: int = INTENT_TTL_MS
    ) -> IntentEntry | None:
        """声明工作方向并获取租约；未取得租约时返回 None（兼容入口，同 file 版）。"""
        entry, _ = self.acquire_intent(task_id, agent_id, direction, ttl_ms)
        return entry

    def acquire_intent(
        self, task_id: str, agent_id: str, direction: str, ttl_ms: int = INTENT_TTL_MS
    ) -> tuple[IntentEntry | None, str]:
        """原子获取或续期租约，返回 ``(租约, claimed|renewed|conflict)``（同 file 版三态）。"""
        key = _entry_key(task_id, direction)
        value = json.dumps(
            {
                "task_id": task_id,
                "agent_id": agent_id,
                "direction": direction,
                "created_at": now_iso(),
                "expires_at": iso_in(ttl_ms / 1000),
            },
            ensure_ascii=False,
        )
        outcome = _acquire_script(redis_client())(
            keys=[_intent_key(task_id, direction)],
            args=[value, int(ttl_ms), agent_id],
        )
        if outcome == "conflict":
            holder = self._holder_of(task_id, direction)
            return holder, "conflict"
        entry = self._holder_of(task_id, direction) or IntentEntry(
            key=key,
            task_id=task_id,
            agent_id=agent_id,  # type: ignore[arg-type]
            direction=direction,
            expires_at=iso_in(ttl_ms / 1000),
            created_at=now_iso(),
        )
        return entry, str(outcome)

    def _holder_of(self, task_id: str, direction: str) -> IntentEntry | None:
        raw = redis_client().get(_intent_key(task_id, direction))
        if raw is None:
            return None
        return _entry_from_value(_entry_key(task_id, direction), raw)

    def release_task_intents(self, task_id: str) -> int:
        """回收某任务的全部租约，返回释放条数（SCAN 精确匹配任务前缀）。"""
        keys = _scan_intent_keys(f"{redis_prefix()}intent:{task_id}:*")
        if not keys:
            return 0
        return int(redis_client().unlink(*keys))

    def release_intent(self, key: str) -> None:
        redis_client().unlink(_redis_key_of_entry(key))

    def active_intents(self, task_id: str | None = None) -> list[IntentEntry]:
        pattern = (
            f"{redis_prefix()}intent:{task_id}:*"
            if task_id
            else f"{redis_prefix()}intent:*"
        )
        keys = _scan_intent_keys(pattern)
        if not keys:
            return []
        values = redis_client().mget(keys)
        entries = [
            _entry_from_value(_redis_key_of_entry(k), v)
            for k, v in zip(keys, values)
            if v is not None
        ]
        entries.sort(key=lambda e: e.created_at)  # SCAN 顺序不定，按创建时间稳定输出
        return entries

    # ----------------------------- 事实 ---------------------------- #

    def add_fact(
        self,
        *,
        task_id: str,
        agent_id: str,
        claim: str,
        source: str,
        status: str = "candidate",
        confidence: float | None = None,
    ) -> FactEntry:
        normalized = claim.strip()
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
        entry = FactEntry(
            id=new_id("fact"),
            task_id=task_id,
            agent_id=agent_id,  # type: ignore[arg-type]
            claim=normalized,
            source=source,
            status=status,  # type: ignore[arg-type]
            confidence=confidence if confidence is not None else 0.6,
            created_at=now_iso(),
        )
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            inserted = conn.execute(
                _FACT_UPSERT,
                (
                    entry.id,
                    task_id,
                    agent_id,
                    digest,
                    entry.status,
                    entry.created_at,
                    json.dumps(entry.model_dump(mode="json"), ensure_ascii=False),
                ),
            ).rowcount
            if inserted:
                return entry
            # 同一事实已存在：verified 升级或原样返回（同 file 版语义）
            row = conn.execute(
                "SELECT payload FROM bb_facts WHERE task_id = %s AND claim_digest = %s",
                (task_id, digest),
            ).fetchone()
            if row is None:  # 理论不可达（唯一键刚冲突过）；防御性回退
                return entry
            existing = FactEntry.model_validate(row[0])
            if status == "verified" and existing.status != "verified":
                existing.status = "verified"
                existing.source = source
                existing.confidence = max(existing.confidence, confidence or 0.8)
                conn.execute(
                    "UPDATE bb_facts SET status = 'verified', payload = %s "
                    "WHERE task_id = %s AND claim_digest = %s",
                    (
                        json.dumps(existing.model_dump(mode="json"), ensure_ascii=False),
                        task_id,
                        digest,
                    ),
                )
            return existing

    def facts(self, task_id: str) -> list[FactEntry]:
        return self._fetch_payloads(
            "SELECT payload FROM bb_facts WHERE task_id = %s ORDER BY created_at, id",
            (task_id,),
            FactEntry,
        )

    # ----------------------------- 活动 ---------------------------- #

    def has_activity(self, task_id: str, signature: str) -> bool:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            row = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM bb_activities WHERE task_id = %s AND signature = %s)",
                (task_id, signature),
            ).fetchone()
        return bool(row and row[0])

    def add_activity(
        self, task_id: str, agent_id: str, action: str, signature: str
    ) -> ActivityEntry:
        entry = ActivityEntry(
            id=new_id("act"),
            task_id=task_id,
            agent_id=agent_id,  # type: ignore[arg-type]
            action=action,
            signature=signature,
            created_at=now_iso(),
        )
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            conn.execute(
                _ACTIVITY_UPSERT,
                (
                    entry.id,
                    task_id,
                    agent_id,
                    signature,
                    entry.created_at,
                    json.dumps(entry.model_dump(mode="json"), ensure_ascii=False),
                ),
            )
        return entry

    def activities(self, task_id: str) -> list[ActivityEntry]:
        return self._fetch_payloads(
            "SELECT payload FROM bb_activities WHERE task_id = %s ORDER BY created_at, id",
            (task_id,),
            ActivityEntry,
        )

    # ----------------------------- 产物 ---------------------------- #

    def put_artifact(self, artifact: Artifact) -> Artifact:
        from .pg import pg_pool

        payload = artifact.model_dump(mode="json")
        with pg_pool().connection() as conn:
            conn.execute(
                "INSERT INTO bb_artifacts (id, task_id, type, version, created_at, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (
                    artifact.id,
                    artifact.task_id,
                    artifact.type,
                    int(artifact.version or 1),
                    artifact.created_at,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        return artifact

    def artifacts(self, task_id: str) -> list[Artifact]:
        return self._fetch_payloads(
            "SELECT payload FROM bb_artifacts WHERE task_id = %s ORDER BY created_at, id",
            (task_id,),
            Artifact,
        )

    def latest_artifact(self, task_id: str, type_: str) -> Artifact | None:
        found = self._fetch_payloads(
            "SELECT payload FROM bb_artifacts WHERE task_id = %s AND type = %s "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (task_id, type_),
            Artifact,
        )
        return found[0] if found else None

    def next_version(self, task_id: str, type_: str) -> int:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            # 连接池是 autocommit 模式（PostgresSaver 的硬性要求，见 pg.py），
            # 锁与计数必须同处一个事务才有意义 —— 显式包 conn.transaction()。
            with conn.transaction():
                # 事务级咨询锁：同 (task_id, type) 的取号串行化；锁随事务提交自动释放
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"bbv:{task_id}:{type_}",)
                )
                row = conn.execute(
                    "SELECT count(*) FROM bb_artifacts WHERE task_id = %s AND type = %s",
                    (task_id, type_),
                ).fetchone()
        return int(row[0] if row else 0) + 1

    # ----------------------------- 审核 ---------------------------- #

    def add_review(
        self,
        *,
        task_id: str,
        agent_id: str,
        target_artifact_id: str,
        verdict: str,
        score: int,
        items: list[ReviewItem],
    ) -> ReviewEntry:
        entry = ReviewEntry(
            id=new_id("rev"),
            task_id=task_id,
            agent_id=agent_id,  # type: ignore[arg-type]
            target_artifact_id=target_artifact_id,
            verdict=verdict,  # type: ignore[arg-type]
            score=score,
            items=items,
            created_at=now_iso(),
        )
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            conn.execute(
                "INSERT INTO bb_reviews (id, task_id, target_artifact_id, created_at, payload) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (
                    entry.id,
                    task_id,
                    target_artifact_id,
                    entry.created_at,
                    json.dumps(entry.model_dump(mode="json"), ensure_ascii=False),
                ),
            )
        return entry

    def reviews(self, task_id: str) -> list[ReviewEntry]:
        return self._fetch_payloads(
            "SELECT payload FROM bb_reviews WHERE task_id = %s ORDER BY created_at, id",
            (task_id,),
            ReviewEntry,
        )

    # ----------------------------- 快照 ---------------------------- #

    def snapshot(self, task_id: str) -> BlackboardSnapshot:
        facts = self.facts(task_id)
        artifacts = self.artifacts(task_id)
        reviews = self.reviews(task_id)
        intents = self.active_intents(task_id)
        return BlackboardSnapshot(
            facts=facts,
            intents=intents,
            activities=self.activities(task_id),
            reviews=reviews,
            stats=BlackboardStats(
                fact_count=len(facts),
                verified_fact_count=len([f for f in facts if f.status == "verified"]),
                artifact_count=len(artifacts),
                review_count=len(reviews),
                active_intents=len(intents),
            ),
        )

    # ----------------------------- 内部 ---------------------------- #

    @staticmethod
    def _fetch_payloads(sql: str, params: tuple, model: type):
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        items = []
        for (payload,) in rows:
            try:
                items.append(model.model_validate(payload))
            except Exception as error:  # noqa: BLE001 - 字段漂移行跳过不崩溃
                log.warn(f"{model.__name__} 记录无法解析，已跳过", error)
        return items
