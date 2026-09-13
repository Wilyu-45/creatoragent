"""共享黑板（移植自 server/core/blackboard.ts）。

参照《plan.md》2.2.2 的「五类共享状态」设计，用单进程内存 + JSON 落盘实现：

    Fact     事实   —— 候选/已验证结论，防止把猜想当事实
    Intent   意图   —— 带租约的工作声明，防止重复劳动
    Artifact 产物   —— 文案、报告等交付物，带版本
    Activity 活动   —— 已完成动作的去重签名，防止重复执行
    Review   审核   —— 审核记录与整改指令

生产环境可替换为 PostgreSQL + Redis，接口保持不变。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from ..config import BLACKBOARD_FILE
from .clock import iso_in, now_iso
from .events import new_id
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

FLUSH_DEBOUNCE_SECONDS = 0.3
INTENT_TTL_MS = 120_000


def _parse_iso(value: str) -> float:
    """把 ISO 串解析为 epoch 秒；解析失败返回 0（视作已过期）。"""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


class Blackboard:
    def __init__(self) -> None:
        self._facts: list[FactEntry] = []
        self._intents: list[IntentEntry] = []
        self._activities: list[ActivityEntry] = []
        self._reviews: list[ReviewEntry] = []
        self._artifacts: list[Artifact] = []

        self._dirty = False
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()

    # --------------------------- 持久化 --------------------------- #

    def load(self) -> None:
        try:
            raw = json.loads(BLACKBOARD_FILE.read_text(encoding="utf-8"))
            self._facts = [FactEntry.model_validate(x) for x in raw.get("facts", [])]
            self._intents = [IntentEntry.model_validate(x) for x in raw.get("intents", [])]
            self._activities = [ActivityEntry.model_validate(x) for x in raw.get("activities", [])]
            self._reviews = [ReviewEntry.model_validate(x) for x in raw.get("reviews", [])]
            self._artifacts = [Artifact.model_validate(x) for x in raw.get("artifacts", [])]
        except FileNotFoundError:
            pass  # 首次运行无文件，忽略
        except Exception:  # noqa: BLE001
            pass  # 文件损坏时以空黑板启动，不阻断服务
        self._sweep_expired_intents()

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self._timer is not None:
            return
        self._timer = threading.Timer(FLUSH_DEBOUNCE_SECONDS, self._fire_flush)
        self._timer.daemon = True
        self._timer.start()

    def _fire_flush(self) -> None:
        with self._lock:
            self._timer = None
        self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            payload = {
                "facts": [f.model_dump(mode="json") for f in self._facts],
                "intents": [i.model_dump(mode="json") for i in self._intents],
                "activities": [a.model_dump(mode="json") for a in self._activities],
                "reviews": [r.model_dump(mode="json") for r in self._reviews],
                "artifacts": [a.model_dump(mode="json") for a in self._artifacts],
            }
        BLACKBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        BLACKBOARD_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ----------------------------- 意图 ---------------------------- #

    def declare_intent(
        self, task_id: str, agent_id: str, direction: str, ttl_ms: int = INTENT_TTL_MS
    ) -> IntentEntry | None:
        """声明工作方向并获取租约；未取得租约时返回 None。

        兼容入口，只关心「有没有拿到」；需要区分首次获取 / 续期 / 冲突时用
        :meth:`acquire_intent`。注意续期场景（同一智能体重复声明）也会返回租约，
        这与旧实现「重复声明返回 None」不同，旧调用方未依赖该差异。
        """
        entry, _ = self.acquire_intent(task_id, agent_id, direction, ttl_ms)
        return entry

    def acquire_intent(
        self, task_id: str, agent_id: str, direction: str, ttl_ms: int = INTENT_TTL_MS
    ) -> tuple[IntentEntry | None, str]:
        """原子地获取或续期工作租约（plan.md 2.2.2 / 4.5「租约机制 + 冲突重试」）。

        返回 ``(租约, 结果)``，结果是三者之一：

        * ``claimed``  —— 方向空闲，抢占成功；
        * ``renewed``  —— 同一智能体在该方向上已有租约（节点重放、断点续跑），
          原地续期而不是把自己锁死；这是相比旧实现唯一的行为变化；
        * ``conflict`` —— 已被其它智能体持有，调用方应退避重试。
        """
        with self._lock:
            self._sweep_expired_intents()
            key = f"{task_id}::{direction}"
            holder = next((i for i in self._intents if i.key == key), None)
            if holder is not None:
                if holder.agent_id == agent_id:
                    holder.expires_at = iso_in(ttl_ms / 1000)
                    self._mark_dirty()
                    return holder, "renewed"
                return holder, "conflict"
            entry = IntentEntry(
                key=key,
                task_id=task_id,
                agent_id=agent_id,  # type: ignore[arg-type]
                direction=direction,
                expires_at=iso_in(ttl_ms / 1000),
                created_at=now_iso(),
            )
            self._intents.append(entry)
            self._mark_dirty()
            return entry, "claimed"

    def release_task_intents(self, task_id: str) -> int:
        """回收某任务的全部租约，返回释放条数。

        任务进入终态（交付 / 驳回 / 失败）时调用，避免租约残留到 TTL 到期；
        对同一任务反复推进 revision 的场景，也能防止旧方向的租约长期占位。
        """
        with self._lock:
            before = len(self._intents)
            self._intents = [i for i in self._intents if i.task_id != task_id]
            released = before - len(self._intents)
            if released:
                self._mark_dirty()
            return released

    def release_intent(self, key: str) -> None:
        with self._lock:
            before = len(self._intents)
            self._intents = [i for i in self._intents if i.key != key]
            if len(self._intents) != before:
                self._mark_dirty()

    def active_intents(self, task_id: str | None = None) -> list[IntentEntry]:
        with self._lock:
            self._sweep_expired_intents()
            if task_id is None:
                return list(self._intents)
            return [i for i in self._intents if i.task_id == task_id]

    def _sweep_expired_intents(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        before = len(self._intents)
        self._intents = [i for i in self._intents if _parse_iso(i.expires_at) > now]
        if len(self._intents) != before:
            self._mark_dirty()

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
        with self._lock:
            for existing in self._facts:
                if existing.task_id == task_id and existing.claim.strip() == normalized:
                    # 同一事实被更高可信来源再次确认 → 升级状态
                    if status == "verified" and existing.status != "verified":
                        existing.status = "verified"
                        existing.source = source
                        existing.confidence = max(existing.confidence, confidence or 0.8)
                        self._mark_dirty()
                    return existing

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
            self._facts.append(entry)
            self._mark_dirty()
            return entry

    def facts(self, task_id: str) -> list[FactEntry]:
        with self._lock:
            return [f for f in self._facts if f.task_id == task_id]

    # ----------------------------- 活动 ---------------------------- #

    def has_activity(self, task_id: str, signature: str) -> bool:
        """若该签名活动已存在则返回 True（表示重复，应跳过）。"""
        with self._lock:
            return any(a.task_id == task_id and a.signature == signature for a in self._activities)

    def add_activity(
        self, task_id: str, agent_id: str, action: str, signature: str
    ) -> ActivityEntry:
        with self._lock:
            entry = ActivityEntry(
                id=new_id("act"),
                task_id=task_id,
                agent_id=agent_id,  # type: ignore[arg-type]
                action=action,
                signature=signature,
                created_at=now_iso(),
            )
            self._activities.append(entry)
            self._mark_dirty()
            return entry

    def activities(self, task_id: str) -> list[ActivityEntry]:
        with self._lock:
            return [a for a in self._activities if a.task_id == task_id]

    # ----------------------------- 产物 ---------------------------- #

    def put_artifact(self, artifact: Artifact) -> Artifact:
        with self._lock:
            self._artifacts.append(artifact)
            self._mark_dirty()
            return artifact

    def artifacts(self, task_id: str) -> list[Artifact]:
        with self._lock:
            found = [a for a in self._artifacts if a.task_id == task_id]
        return sorted(found, key=lambda a: a.created_at)

    def latest_artifact(self, task_id: str, type_: str) -> Artifact | None:
        found = [a for a in self.artifacts(task_id) if a.type == type_]
        return found[-1] if found else None

    def next_version(self, task_id: str, type_: str) -> int:
        return len([a for a in self.artifacts(task_id) if a.type == type_]) + 1

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
        with self._lock:
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
            self._reviews.append(entry)
            self._mark_dirty()
            return entry

    def reviews(self, task_id: str) -> list[ReviewEntry]:
        with self._lock:
            return [r for r in self._reviews if r.task_id == task_id]

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


blackboard = Blackboard()
