"""A11 记忆库的 PostgreSQL 实现（仅 ``CREATOR_STORAGE=pg`` 时实例化）。

接口契约见 storage_contract.md §3.3 与 :mod:`app.knowledge.memory`（file 版）。
不变量逐条保留：内容摘要幂等去重、租户隔离、语言分区、自我排除、
容量逐出、TTL 过期清扫、混合检索（关键词 + 向量 + 元数据加成）。

与 file 版的实现分工：

* **评分逻辑全部复用** ``memory.py`` 的模块级函数与常量
  （``_build_drafts / _score_cards / _overlap / _tokens / _digest`` 及权重表），
  两种后端的排序语义**同源**，杜绝「PG 版召回顺序不一样」这类漂移；
* **PG 只做存储、过滤与聚合**：候选卡片按租户/语言过滤后全量取出
  （每租户 ``MAX_CARDS`` = 500 封顶），打分在 Python 完成；
* **向量不落库**：每进程懒计算缓存（与 file 版 ``_ensure_vectors`` 语义一致）。
  pgvector 不引入的理由：embedding 维度可配（16+，openai 1536），固定维度列
  不适配；且混合评分含元数据加成与关键词分量，无法用 ``<=>`` 单独表达。
"""

from __future__ import annotations

import json
from typing import Any

from ..config import get_config
from ..core.clock import now_iso
from ..core.events import new_id
from ..logger import create_logger
from .embedding import describe as describe_embedding, embed_texts
from .memory import (
    CARD_KINDS,
    DEFAULT_MEMORY_LANGUAGE,
    DEFAULT_TENANT,
    FRESH_DAYS,
    KIND_LABEL,
    MAX_AGE_DAYS,
    MAX_CARDS,
    STALE_DAYS,
    MemoryCard,
    MemoryHit,
    _build_drafts,
    _digest,
    _language_key,
    _score_cards,
)
from ..logger import create_logger as _create_logger

log = _create_logger("memory_pg")


class PgMemoryStore:
    """与 :class:`app.knowledge.memory.MemoryStore` 同接口的 PostgreSQL 后端。"""

    def __init__(self) -> None:
        # card_id → 向量（进程内懒缓存；落库只存原文，向量可随时重建 —— 同 file 版）
        self._vectors: dict[str, list[float]] = {}
        self._vector_key: tuple[str, str, int] = ("", "", 0)

    # --------------------------- 持久化 --------------------------- #

    def load(self) -> None:
        """连接自检 + 过期知识清扫（幂等；file 版在 load 时做同一件事）。"""
        from app.core.pg import pg_pool

        with pg_pool().connection() as conn:
            dropped = conn.execute(
                "DELETE FROM memory_cards WHERE created_at < now() - make_interval(days => %s)",
                (MAX_AGE_DAYS,),
            ).rowcount
        if dropped:
            log.warn(f"记忆库下线 {dropped} 条超过 {MAX_AGE_DAYS} 天的过期知识")

    def flush(self) -> None:
        """写入即持久；保留签名兼容退出钩子。"""

    # ---------------------------- 写入 ---------------------------- #

    def remember(
        self,
        *,
        task_id: str,
        brand: str,
        channel: str,
        industry: str,
        cards: list[dict[str, Any]],
        templates: list[dict[str, Any]] | None = None,
        revision: int = 0,
        tenant: str = DEFAULT_TENANT,
        language: str = DEFAULT_MEMORY_LANGUAGE,
    ) -> int:
        """写入一批知识卡片，返回新增条数（租户内重复内容自动跳过）。"""
        self.load()
        stamp = now_iso()
        owner = (tenant or DEFAULT_TENANT).strip() or DEFAULT_TENANT
        lang = _language_key(language)
        drafts = _build_drafts(cards, templates)

        added = 0
        from app.core.pg import pg_pool

        with pg_pool().connection() as conn:
            for draft in drafts:
                if draft["kind"] not in CARD_KINDS:
                    draft["kind"] = "lesson"
                if not draft["title"] or not draft["content"]:
                    continue
                digest = _digest(owner, draft["kind"], draft["title"], draft["content"])
                card = MemoryCard(
                    id=new_id("mem"),
                    task_id=task_id,
                    brand=brand,
                    channel=channel,
                    industry=industry,
                    kind=draft["kind"],
                    title=draft["title"],
                    content=draft["content"],
                    tags=draft["tags"],
                    reuse_hint=draft["reuse_hint"],
                    revision=revision,
                    created_at=stamp,
                    tenant=owner,
                    language=lang,
                )
                result = conn.execute(
                    "INSERT INTO memory_cards (id, tenant, digest, task_id, brand, channel, "
                    "industry, kind, language, title, content, revision, hits, created_at, payload) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s) "
                    "ON CONFLICT (tenant, digest) DO NOTHING",
                    (
                        card.id,
                        owner,
                        digest,
                        task_id,
                        brand,
                        channel,
                        industry,
                        card.kind,
                        lang,
                        card.title,
                        card.content,
                        revision,
                        card.created_at,
                        json.dumps(card.to_dict(), ensure_ascii=False),
                    ),
                )
                added += result.rowcount

            if added:
                self._evict(conn, owner)
                self._sweep_expired(conn)

        if added:
            log.info(f"任务 {task_id}（租户 {owner}）沉淀 {added} 条新知识")
        return added

    @staticmethod
    def _evict(conn: Any, tenant: str) -> None:
        """超出容量上限时淘汰最早的卡片（每租户各自 ``MAX_CARDS``，同 file 版）。"""
        row = conn.execute(
            "SELECT count(*) FROM memory_cards WHERE tenant = %s", (tenant,)
        ).fetchone()
        overflow = int(row[0] if row else 0) - MAX_CARDS
        if overflow <= 0:
            return
        dropped = conn.execute(
            "DELETE FROM memory_cards WHERE id IN ("
            "  SELECT id FROM memory_cards WHERE tenant = %s"
            "  ORDER BY created_at DESC, id DESC OFFSET %s"
            ")",
            (tenant, MAX_CARDS),
        ).rowcount
        # 上面 OFFSET 语义不对齐时的兜底：按「保留最新 MAX_CARDS 条」精确删除
        if dropped != overflow:
            conn.execute(
                "DELETE FROM memory_cards c USING memory_cards s "
                "WHERE s.id = c.id AND c.tenant = %s AND c.id IN ("
                "  SELECT id FROM memory_cards WHERE tenant = %s"
                "  ORDER BY created_at ASC, id ASC LIMIT %s"
                ")",
                (tenant, tenant, max(0, dropped - overflow)),
            )
        log.warn(f"租户 {tenant} 记忆库超出上限，淘汰 {max(0, dropped)} 条最旧知识")

    @staticmethod
    def _sweep_expired(conn: Any) -> None:
        """下线超过 ``MAX_AGE_DAYS`` 的过期知识（同 file 版 ``_sweep_expired``）。"""
        dropped = conn.execute(
            "DELETE FROM memory_cards WHERE created_at < now() - make_interval(days => %s)",
            (MAX_AGE_DAYS,),
        ).rowcount
        if dropped:
            log.warn(f"记忆库下线 {dropped} 条超过 {MAX_AGE_DAYS} 天的过期知识")

    # ---------------------------- 检索 ---------------------------- #

    def _ensure_vectors(self, cards: list[MemoryCard]) -> None:
        """懒计算并缓存卡片向量；提供方 / 模型 / 维度变更时整体失效重算（同 file 版）。"""
        if not cards:
            return
        cfg = get_config().embedding
        key = (cfg.provider, cfg.model if cfg.provider == "openai" else "local", cfg.dim)
        if key != self._vector_key:
            self._vectors.clear()
            self._vector_key = key
        missing = [card for card in cards if card.id not in self._vectors]
        if not missing:
            return
        texts = [f"{card.title}\n{' '.join(card.tags)}\n{card.content}" for card in missing]
        vectors = embed_texts(texts)
        for card, vector in zip(missing, vectors):
            self._vectors[card.id] = vector

    def retrieve(
        self,
        query: str,
        *,
        brand: str = "",
        channel: str = "",
        industry: str = "",
        top_k: int = 5,
        exclude_task: str | None = None,
        tenant: str | None = None,
        language: str | None = None,
    ) -> list[MemoryHit]:
        """按相关度召回知识卡片（语义与过滤条件同 file 版 retrieve 的 docstring）。"""
        from app.core.pg import pg_pool

        from .memory import _tokens, _W_TAG, _W_TITLE, _W_BODY  # noqa: PLC0415

        query_tokens = _tokens(query)
        weight = max(0.0, min(1.0, get_config().embedding.weight))
        query_vector: list[float] | None = None
        if weight > 0 and query.strip():
            query_vector = embed_texts([query])[0]

        owner = (tenant or "").strip()
        lang = _language_key(language) if language else ""
        clauses: list[str] = []
        params: list[Any] = []
        if owner:
            clauses.append("tenant = %s")
            params.append(owner)
        if lang:
            clauses.append("language = %s")
            params.append(lang)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with pg_pool().connection() as conn:
            rows = conn.execute(
                f"SELECT payload FROM memory_cards {where} ORDER BY created_at, id",
                tuple(params),
            ).fetchall()
        cards: list[MemoryCard] = []
        for (payload,) in rows:
            try:
                cards.append(MemoryCard(**payload))
            except TypeError:  # 字段漂移时逐条跳过（同 file 版 load）
                continue
        if query_vector is not None:
            self._ensure_vectors(cards)

        hits = _score_cards(
            cards=cards,
            query_tokens=query_tokens,
            query_vector=query_vector,
            weight=weight,
            brand=brand,
            channel=channel,
            industry=industry,
            exclude_task=exclude_task,
            vector_of=self._vectors.get,
        )
        top = hits[: max(0, top_k)]

        if top:
            with pg_pool().connection() as conn:
                conn.execute(
                    "UPDATE memory_cards SET hits = hits + 1 WHERE id = ANY(%s)",
                    ([hit.card.id for hit in top],),
                )
        return top

    # ---------------------------- 查询 ---------------------------- #

    def list_cards(
        self,
        *,
        kind: str | None = None,
        brand: str | None = None,
        limit: int = 50,
        tenant: str | None = None,
    ) -> list[MemoryCard]:
        from app.core.pg import pg_pool

        clauses: list[str] = []
        params: list[Any] = []
        owner = (tenant or "").strip()
        if owner:
            clauses.append("tenant = %s")
            params.append(owner)
        if kind:
            clauses.append("kind = %s")
            params.append(kind)
        if brand:
            clauses.append("brand = %s")
            params.append(brand)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(0, limit))
        with pg_pool().connection() as conn:
            rows = conn.execute(
                f"SELECT payload FROM memory_cards {where} "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                tuple(params),
            ).fetchall()
        cards: list[MemoryCard] = []
        for (payload,) in rows:
            try:
                cards.append(MemoryCard(**payload))
            except TypeError:
                continue
        return cards

    #: 「brand 非空」过滤条件（供 stats 拼接，避免 f-string 内嵌引号）
    NONEMPTY_BRAND = "brand <> ''"

    def stats(self, tenant: str | None = None) -> dict[str, Any]:
        """库统计（输出字段与 file 版 stats 完全一致；SQL 聚合实现）。"""
        from app.core.pg import pg_pool

        owner = (tenant or "").strip()
        scoped = "WHERE tenant = %s" if owner else ""
        params: tuple = (owner,) if owner else ()

        def _cond(condition: str) -> str:
            # 有租户过滤时拼 AND，否则自身构成 WHERE（空 scoped 时不能出现悬空 AND）
            return f"{scoped} AND {condition}" if scoped else f"WHERE {condition}"

        with pg_pool().connection() as conn:
            global_total = int(
                conn.execute("SELECT count(*) FROM memory_cards").fetchone()[0]
            )
            total = int(
                conn.execute(
                    f"SELECT count(*) FROM memory_cards {scoped}", params
                ).fetchone()[0]
            )
            tenants = sorted(
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT tenant FROM memory_cards ORDER BY tenant"
                ).fetchall()
            )
            by_kind_raw = {
                str(r[0]): int(r[1])
                for r in conn.execute(
                    f"SELECT kind, count(*) FROM memory_cards {scoped} GROUP BY kind", params
                ).fetchall()
            }
            brands = sorted(
                str(r[0])
                for r in conn.execute(
                    f"SELECT DISTINCT brand FROM memory_cards {_cond(self.NONEMPTY_BRAND)} "
                    "ORDER BY brand",
                    params,
                ).fetchall()
            )
            tasks = int(
                conn.execute(
                    f"SELECT count(DISTINCT task_id) FROM memory_cards {scoped}", params
                ).fetchone()[0]
            )
            reused = int(
                conn.execute(
                    f"SELECT count(*) FROM memory_cards {_cond('hits > 0')}", params
                ).fetchone()[0]
            )
            ages = conn.execute(
                "SELECT COALESCE(max(age_days), 0), "
                "COALESCE(sum(CASE WHEN age_days <= %s THEN 1 ELSE 0 END), 0), "
                "COALESCE(sum(CASE WHEN age_days > %s THEN 1 ELSE 0 END), 0) "
                f"FROM (SELECT extract(epoch FROM (now() - created_at))::bigint / 86400 "
                f"AS age_days FROM memory_cards {scoped}) t",
                (FRESH_DAYS, STALE_DAYS, *params),
            ).fetchone()
            scoped_ids = [
                str(r[0])
                for r in conn.execute(
                    f"SELECT id FROM memory_cards {scoped}", params
                ).fetchall()
            ]
        oldest_days = int(ages[0]) if ages else 0
        fresh = int(ages[1]) if ages else 0
        stale = int(ages[2]) if ages else 0
        return {
            "total": total,
            "global_total": global_total,
            "capacity": MAX_CARDS,
            "tenant": owner or None,
            "tenants": tenants,
            "by_kind": [
                {"kind": kind, "label": KIND_LABEL[kind], "count": by_kind_raw.get(kind, 0)}
                for kind in CARD_KINDS
            ],
            "brands": brands,
            "tasks": tasks,
            "reused": reused,
            "max_age_days": MAX_AGE_DAYS,
            "fresh_days": FRESH_DAYS,
            "oldest_days": oldest_days,
            "fresh": fresh,
            "stale": stale,
            "embedding": describe_embedding(),
            "vector_indexed": sum(1 for card_id in scoped_ids if card_id in self._vectors),
        }
