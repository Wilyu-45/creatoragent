"""A11 记忆库：知识卡片的持久化与检索（RAG-lite）。

对应《creator.md》A11 的两项核心职责：

* **存储**——把 A11 沉淀的知识卡片与模板固化到 ``data/memory.json``，跨任务存活；
* **为其他智能体提供 RAG 检索**——``retrieve()`` 按「文本相关度 + 品牌/渠道/行业元数据」
  打分召回，供 A1/A2/A4 在下一次创作中复用品牌调性、有效表达与历史案例。

混合检索（关键词 + 向量）
------------------------
* **关键词分量**：中文按 2-gram、英文/数字按词切分做重叠度打分，叠加品牌/渠道/行业加成，
  负责「精确命中」；
* **语义分量**：``embedding.embed_texts`` 把查询与卡片映射为向量后算余弦，
  负责「换个说法也能召回」。

两者按 ``cfg.embedding.weight`` 线性混合；权重为 0 时退化为纯关键词检索，
与历史行为完全一致。默认向量是本地 hashing embedding（零依赖、可离线），
配置 ``EMBEDDING_PROVIDER=openai`` 即可切到任意 OpenAI 兼容 ``/embeddings`` 端点，
远端不可用时自动回退本地向量，检索永不因 embedding 服务故障而中断。

知识新鲜度
----------
``MAX_CARDS`` 限制库容量，超出后淘汰最久未更新的卡片；``MAX_AGE_DAYS`` 让超过半年的
知识「过期下线」，避免长期运行后检索被陈年噪声淹没（creator.md A11「管理版本、过期知识」）。

多租户隔离（plan.md D17 / creator.md A11「管理权限」）
----------------------------------------------------
每张卡片带 ``tenant`` 归属，检索、列表与统计均可按租户过滤，避免 A 品牌的调性基线
被 B 品牌的智能体当作「团队资产」复用。同一租户内的去重键是
``sha1(tenant|kind|title|content)``——不同租户的相同标题不会互相顶替，
因此「各租户各自沉淀一份」是期望行为，而不是重复数据。
未启用 ``CREATOR_API_TOKENS`` 时全部归属 ``default``，行为与历史版本一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import MEMORY_FILE, get_config
from ..core.clock import now_iso
from ..core.events import new_id
from ..logger import create_logger
from .embedding import cosine, describe as describe_embedding, embed_texts, tokenize

log = create_logger("memory")

#: 卡片类型（与 A11 / 前端分组一致）
CARD_KINDS: tuple[str, ...] = ("brand", "case", "template", "lesson")

KIND_LABEL: dict[str, str] = {
    "brand": "品牌资产",
    "case": "案例沉淀",
    "template": "可复用模板",
    "lesson": "经验教训",
}

#: 库容量上限，超出后淘汰最久未更新的卡片
MAX_CARDS = 500

#: 低于该分数的候选不返回，避免「什么都召回一点」的虚假命中
MIN_SCORE = 0.2

#: 知识存活上限（天）：超过即「过期下线」（creator.md A11「管理版本、过期知识」）
MAX_AGE_DAYS = 180

#: 新鲜阈值（天）：用于「知识新鲜度」统计与同分时的优先次序
FRESH_DAYS = 30

#: 陈旧阈值（天）：超过则在统计里标记为待更新（不影响召回）
STALE_DAYS = 120

#: 文本三项权重之和为 0.6，乘以 _W_TEXT_GAIN 后归一——「文本完全命中」单项即可触顶；
#: 元数据（品牌 0.25 / 渠道 0.1 / 行业 0.05）作为加成，合计上限 0.4。
_W_TAG = 0.3
_W_TITLE = 0.2
_W_BODY = 0.1
_W_TEXT_GAIN = 2.0
_W_BRAND = 0.25
_W_CHANNEL = 0.1
_W_INDUSTRY = 0.05

#: 向量余弦超过该值即认为是「语义相近」，写进命中理由
_VECTOR_REASON = 0.35

#: 未启用鉴权时的默认租户（与 ``TaskRecord.tenant`` 口径一致）
DEFAULT_TENANT = "default"


def _tokens(text: str) -> set[str]:
    """中文取 2-gram（不足 2 字时退回单字），英文/数字取词（与向量口径一致）。"""
    return set(tokenize(text))


def _overlap(query: set[str], text: str) -> float:
    """召回率口径：查询词里有多少比例命中了该字段。"""
    if not query:
        return 0.0
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    return len(query & tokens) / len(query)


def _digest(*parts: str) -> str:
    joined = "\x00".join(part.strip() for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _card_key(card: MemoryCard) -> str:
    """租户内的内容指纹：租户隔离后，不同租户的同名知识必须各自存活。"""
    return _digest(card.tenant or DEFAULT_TENANT, card.kind, card.title, card.content)


#: 记忆库默认语言（与 ``language.DEFAULT_LANGUAGE`` 一致；此处独立定义避免循环导入）
DEFAULT_MEMORY_LANGUAGE = "zh"


def _language_key(value: str | None) -> str:
    """记忆库的语言分区键（plan.md v2.0「多语言本地化」）。

    英文资产**不该**被中文任务当作「品牌调性基线」复用 ——
    复用一份语言不对的资产比不复用更糟（会被模型当成本次任务的语气参照）。
    旧卡片没有该字段时按 ``zh`` 读取，与历史行为一致。
    """
    from .language import normalize_language

    return normalize_language(value or DEFAULT_MEMORY_LANGUAGE)


def _build_drafts(
    cards: list[dict[str, Any]] | None, templates: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """把 A11 产出的知识卡片与模板整理成入库草稿（file / PG 两个后端共用）。"""
    drafts: list[dict[str, Any]] = []
    for item in cards or []:
        drafts.append(
            {
                "kind": str(item.get("type") or "lesson").lower(),
                "title": str(item.get("title") or "").strip(),
                "content": str(item.get("content") or "").strip(),
                "tags": [str(tag) for tag in (item.get("tags") or []) if str(tag).strip()],
                "reuse_hint": str(item.get("reuse_hint") or "").strip(),
            }
        )
    # 模板本身就是最值得复用的知识，统一转成 template 卡片入库
    for item in templates or []:
        name = str(item.get("name") or "").strip()
        body = str(item.get("body") or "").strip()
        if not name or not body:
            continue
        drafts.append(
            {
                "kind": "template",
                "title": name,
                "content": f"{str(item.get('usage') or '').strip()}\n{body}".strip(),
                "tags": ["模板"],
                "reuse_hint": "可直接套用后替换产品信息",
            }
        )
    return drafts


@dataclass
class MemoryCard:
    """一条可跨任务复用的知识。"""

    id: str
    task_id: str
    brand: str
    channel: str
    industry: str
    kind: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    reuse_hint: str = ""
    revision: int = 0
    created_at: str = ""
    #: 同一条知识被多次命中时累加，用于「复用率」统计
    hits: int = 0
    #: 归属租户（plan.md D17）。旧数据缺该字段时按 ``default`` 处理。
    tenant: str = DEFAULT_TENANT
    #: 内容语言（plan.md v2.0）。旧数据缺该字段时按 ``zh`` 处理，
    #: 因此英文资产不会与中文资产互相召回。
    language: str = DEFAULT_MEMORY_LANGUAGE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _age_days(card: MemoryCard) -> int:
    """卡片年龄（天）。``created_at`` 缺失或不可解析时返回 0（按新卡片处理）。"""
    try:
        created = datetime.fromisoformat(str(card.created_at))
    except (TypeError, ValueError):
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - created).total_seconds()
    return max(0, int(seconds // 86400))


@dataclass
class MemoryHit:
    """一次召回的命中结果，带分数与命中理由（便于前端与调试解释）。"""

    card: MemoryCard
    score: float
    reasons: list[str] = field(default_factory=list)
    #: 卡片年龄（天）与新鲜度；用于「知识新鲜度」统计与同分时的优先次序
    age_days: int = 0
    fresh: bool = True
    #: 语义分量：查询向量与卡片向量的余弦（0 表示未启用向量或维度不可比）
    vector: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "age_days": self.age_days,
            "fresh": self.fresh,
            "id": self.card.id,
            "task_id": self.card.task_id,
            "brand": self.card.brand,
            "channel": self.card.channel,
            "industry": self.card.industry,
            "kind": self.card.kind,
            "kind_label": KIND_LABEL.get(self.card.kind, self.card.kind),
            "title": self.card.title,
            "content": self.card.content,
            "tags": list(self.card.tags),
            "reuse_hint": self.card.reuse_hint,
            "created_at": self.card.created_at,
            "tenant": self.card.tenant or DEFAULT_TENANT,
            "score": round(self.score, 4),
            "vector_score": round(self.vector, 4),
            "reasons": list(self.reasons),
        }


def _score_cards(
    *,
    cards: list[MemoryCard],
    query_tokens: set[str],
    query_vector: list[float] | None,
    weight: float,
    brand: str,
    channel: str,
    industry: str,
    exclude_task: str | None,
    vector_of,
) -> list[MemoryHit]:
    """对候选卡片逐条打分并排序（file / PG 两个后端共用，保证排序语义同源）。

    ``vector_of`` 是 ``card_id → 向量`` 的取值函数（由各后端注入自己的进程内缓存）。
    返回**未截断**的命中列表，由调用方做 top_k 截断与 hits 计数。
    """
    hits: list[MemoryHit] = []
    for card in cards:
        if exclude_task and card.task_id == exclude_task:
            continue

        tag_score = max((_overlap(query_tokens, tag) for tag in card.tags), default=0.0)
        title_score = _overlap(query_tokens, card.title)
        body_score = _overlap(query_tokens, card.content)
        text_score = _W_TAG * tag_score + _W_TITLE * title_score + _W_BODY * body_score

        # 关键词分量先归一化到 0..1，再与语义分量按权重线性混合
        keyword_score = min(1.0, text_score * _W_TEXT_GAIN)
        vector_sim = 0.0
        if query_vector is not None:
            vector_sim = max(0.0, cosine(query_vector, vector_of(card.id) or []))
        score = keyword_score * (1.0 - weight) + vector_sim * weight

        reasons: list[str] = []
        if text_score > 0:
            reasons.append("文本相关")
        if weight > 0 and vector_sim >= _VECTOR_REASON:
            reasons.append("语义相近")

        if brand and card.brand == brand:
            score += _W_BRAND
            reasons.append("同品牌")
        if channel and card.channel == channel:
            score += _W_CHANNEL
            reasons.append("同渠道")
        if industry and card.industry == industry:
            score += _W_INDUSTRY
            reasons.append("同行业")

        if score < MIN_SCORE:
            continue
        age = _age_days(card)
        hits.append(
            MemoryHit(
                card=card,
                score=min(score, 1.0),
                reasons=reasons,
                age_days=age,
                fresh=age <= FRESH_DAYS,
                vector=vector_sim,
            )
        )

    # 同分时优先新鲜知识（creator.md 把「知识新鲜度」列为知识层 KPI）
    hits.sort(key=lambda hit: (hit.score, -hit.age_days), reverse=True)
    return hits


class MemoryStore:
    """单进程记忆库：内存索引 + JSON 落盘。"""

    def __init__(self) -> None:
        self._cards: list[MemoryCard] = []
        self._index: dict[str, MemoryCard] = {}
        #: card_id → 向量（懒计算、进程内缓存；落盘只存原文，向量可随时重建）
        self._vectors: dict[str, list[float]] = {}
        #: 向量缓存对应的提供方指纹；配置变更时整体失效
        self._vector_key: tuple[str, str, int] = ("", "", 0)
        self._lock = threading.RLock()
        self._loaded = False

    # --------------------------- 持久化 --------------------------- #

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                raw = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
                cards = raw.get("cards") if isinstance(raw, dict) else None
                for item in cards or []:
                    try:
                        card = MemoryCard(**item)
                    except TypeError:  # 字段漂移（例如旧版本少字段）时逐条跳过
                        continue
                    if card.kind not in CARD_KINDS:
                        card.kind = "lesson"
                    card.tenant = (card.tenant or DEFAULT_TENANT).strip() or DEFAULT_TENANT
                    card.language = _language_key(getattr(card, "language", None))
                    self._cards.append(card)
                    self._index[_card_key(card)] = card
                if self._cards:
                    log.info(f"已加载 {len(self._cards)} 条跨任务知识卡片")
                    # 启动即清理过期知识，避免旧卡片参与本轮召回
                    if self._sweep_expired():
                        self.flush()
            except FileNotFoundError:
                pass
            except Exception as error:  # noqa: BLE001 - 记忆库损坏不应阻断服务
                log.warn("记忆库文件损坏，以空库启动", error)

    def flush(self) -> None:
        with self._lock:
            payload = {
                "cards": [card.to_dict() for card in self._cards],
                "updated_at": now_iso(),
            }
        try:
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = MEMORY_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, MEMORY_FILE)
        except OSError as error:
            log.error("记忆库落盘失败", error)

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
        with self._lock:
            for draft in drafts:
                if draft["kind"] not in CARD_KINDS:
                    draft["kind"] = "lesson"
                if not draft["title"] or not draft["content"]:
                    continue
                key = _digest(owner, draft["kind"], draft["title"], draft["content"])
                if key in self._index:
                    continue
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
                self._cards.append(card)
                self._index[key] = card
                added += 1

            if added:
                self._evict(owner)
                self._sweep_expired()

        if added:
            self.flush()
            log.info(
                f"任务 {task_id}（租户 {owner}）沉淀 {added} 条新知识（库容量 {len(self._cards)}）"
            )
        return added

    def _evict(self, tenant: str) -> None:
        """超出容量上限时淘汰最早的卡片（调用方需持锁）。

        **按租户计量**：容量是「每个租户各自 500 条」，否则多租户下先入库的租户
        会把后入库租户的名额挤掉（creator.md A11 要求管理权限与容量）。
        """
        scoped = [card for card in self._cards if card.tenant == tenant]
        overflow = len(scoped) - MAX_CARDS
        if overflow <= 0:
            return
        dropped = scoped[:overflow]
        dropped_ids = {card.id for card in dropped}
        self._cards = [card for card in self._cards if card.id not in dropped_ids]
        for card in dropped:
            self._index.pop(_card_key(card), None)
            self._vectors.pop(card.id, None)
        log.warn(f"租户 {tenant} 记忆库超出上限，淘汰 {len(dropped)} 条最旧知识")

    def _sweep_expired(self) -> int:
        """下线超过 ``MAX_AGE_DAYS`` 的过期知识，返回下线条数（调用方需持锁）。

        ``_evict`` 解决的是「库太满」，这里解决的是「知识太旧」：平台玩法与
        品牌口径变化很快，过期卡片继续参与召回会污染新任务的判断
        （creator.md 要求 A11 管理版本、标签、权限与过期知识）。
        """
        kept: list[MemoryCard] = []
        dropped: list[MemoryCard] = []
        for card in self._cards:
            (dropped if _age_days(card) > MAX_AGE_DAYS else kept).append(card)
        if not dropped:
            return 0
        self._cards = kept
        for card in dropped:
            self._index.pop(_card_key(card), None)
            self._vectors.pop(card.id, None)
        log.warn(f"记忆库下线 {len(dropped)} 条超过 {MAX_AGE_DAYS} 天的过期知识")
        return len(dropped)

    # ---------------------------- 检索 ---------------------------- #

    def _ensure_vectors(self, cards: list[MemoryCard]) -> None:
        """懒计算并缓存卡片向量；提供方 / 模型 / 维度变更时整体失效重算。

        只计算**传入的这一批**卡片（调用方已按租户过滤）：多租户部署下，
        给 A 租户做一次检索不应该替 B/C/D 租户把向量也算一遍。
        """
        if not cards:
            return
        cfg = get_config().embedding
        key = (cfg.provider, cfg.model if cfg.provider == "openai" else "local", cfg.dim)
        with self._lock:
            if key != self._vector_key:
                self._vectors.clear()
                self._vector_key = key
            missing = [card for card in cards if card.id not in self._vectors]
        if not missing:
            return
        texts = [f"{card.title}\n{' '.join(card.tags)}\n{card.content}" for card in missing]
        vectors = embed_texts(texts)
        with self._lock:
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
        """按相关度召回知识卡片。

        ``exclude_task`` 用于排除当前任务自己刚写入的卡片，
        保证「召回的是历史资产」而不是自我循环。
        ``tenant`` 非空时只在该租户的卡片中召回（plan.md D17「数据隔离」）；
        传 ``None`` 表示不限租户（供离线自检等场景使用）。
        ``language`` 非空时只召回该语言的卡片：**英文资产不该被中文任务
        当作语气基线**，反之亦然。
        """
        self.load()
        query_tokens = _tokens(query)
        weight = max(0.0, min(1.0, get_config().embedding.weight))
        query_vector: list[float] | None = None
        if weight > 0 and query.strip():
            query_vector = embed_texts([query])[0]

        owner = (tenant or "").strip()
        lang = _language_key(language) if language else ""
        with self._lock:
            cards = [
                card
                for card in self._cards
                if (not owner or card.tenant == owner)
                and (not lang or _language_key(card.language) == lang)
            ]
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
            with self._lock:
                for hit in top:
                    hit.card.hits += 1

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
        self.load()
        owner = (tenant or "").strip()
        with self._lock:
            cards = [
                card for card in self._cards if not owner or card.tenant == owner
            ]
        if kind:
            cards = [card for card in cards if card.kind == kind]
        if brand:
            cards = [card for card in cards if card.brand == brand]
        return sorted(cards, key=lambda card: card.created_at, reverse=True)[: max(0, limit)]

    def stats(self, tenant: str | None = None) -> dict[str, Any]:
        """库统计。

        ``tenant`` 非空时只统计该租户的卡片，并额外给出全局容量占用
        （``global_total``）——「我的库还剩多少额度」是租户最关心的信息，
        而全局总量不构成数据泄露（只有数量）。
        """
        self.load()
        owner = (tenant or "").strip()
        with self._lock:
            all_cards = list(self._cards)
        cards = [card for card in all_cards if not owner or card.tenant == owner]
        by_kind: dict[str, int] = {}
        for card in cards:
            by_kind[card.kind] = by_kind.get(card.kind, 0) + 1
        return {
            "total": len(cards),
            "global_total": len(all_cards),
            "capacity": MAX_CARDS,
            "tenant": owner or None,
            "tenants": sorted({card.tenant or DEFAULT_TENANT for card in all_cards}),
            "by_kind": [
                {"kind": kind, "label": KIND_LABEL[kind], "count": by_kind.get(kind, 0)}
                for kind in CARD_KINDS
            ],
            "brands": sorted({card.brand for card in cards if card.brand}),
            "tasks": len({card.task_id for card in cards}),
            "reused": sum(1 for card in cards if card.hits > 0),
            # 知识新鲜度：过期下线阈值 / 最旧卡片 / 新鲜与陈旧数量
            "max_age_days": MAX_AGE_DAYS,
            "fresh_days": FRESH_DAYS,
            "oldest_days": max((_age_days(card) for card in cards), default=0),
            "fresh": sum(1 for card in cards if _age_days(card) <= FRESH_DAYS),
            "stale": sum(1 for card in cards if _age_days(card) > STALE_DAYS),
            # 检索方式：纯关键词（weight=0）还是关键词 + 向量混合。
            # 索引条数是**懒计算**的结果：只统计本租户里真正被检索过（因而建过向量）
            # 的卡片，因此刚启动时为 0 是正常的，首次检索后才会涨上来。
            "embedding": describe_embedding(),
            "vector_indexed": sum(1 for card in cards if card.id in self._vectors),
        }


def render_hits(hits: list[dict[str, Any]], *, limit: int = 5) -> str:
    """把召回结果渲染成提示词片段；无召回时返回空串。

    真实模型与离线引擎共用同一段文案，保证「有记忆」这件事对两条路径可见。
    多租户下会标注资产归属租户，便于排查「为什么召回了这条」。
    """
    lines: list[str] = []
    owners = {str(hit.get("tenant") or DEFAULT_TENANT) for hit in (hits or [])[:limit]}
    multi_tenant = len(owners) > 1
    for index, hit in enumerate((hits or [])[:limit], start=1):
        title = str(hit.get("title") or "").strip()
        content = str(hit.get("content") or "").strip()
        if not title and not content:
            continue
        label = str(hit.get("kind_label") or KIND_LABEL.get(str(hit.get("kind")), "历史资产"))
        task_id = str(hit.get("task_id") or "")
        owner = str(hit.get("tenant") or DEFAULT_TENANT)
        reasons = "、".join(str(reason) for reason in (hit.get("reasons") or []))
        meta = "｜".join(
            part
            for part in (
                f"来源任务 {task_id}" if task_id else "",
                f"租户 {owner}" if multi_tenant else "",
                reasons,
            )
            if part
        )
        block = f"{index}. [{label}] {title}"
        if meta:
            block += f"（{meta}）"
        if content:
            block += f"\n   {content}"
        hint = str(hit.get("reuse_hint") or "").strip()
        if hint:
            block += f"\n   复用建议：{hint}"
        lines.append(block)

    if not lines:
        return ""
    return (
        "【历史可复用资产（团队知识库召回，供对齐调性与复用句式，不得当作本次事实依据）】\n"
        + "\n".join(lines)
        + "\n\n"
    )


def evidence_from_hits(hits: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    """把召回结果转成 evidence 条目，使「复用历史资产」在结果里可追溯。"""
    items: list[dict[str, Any]] = []
    for hit in (hits or [])[:limit]:
        title = str(hit.get("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "claim": f"复用团队知识库资产「{title}」",
                "source": f"记忆库/{str(hit.get('task_id') or '未知任务')}",
                "reliability": 0.7,
            }
        )
    return items


def _build_memory_store():
    """按 ``CREATOR_STORAGE`` 选择后端；PG 实现延迟 import（file 模式零依赖）。"""
    from ..config import STORAGE_MODE

    if STORAGE_MODE == "pg":
        from .memory_pg import PgMemoryStore

        return PgMemoryStore()
    return MemoryStore()


memory_store = _build_memory_store()

__all__ = [
    "CARD_KINDS",
    "DEFAULT_TENANT",
    "KIND_LABEL",
    "MAX_CARDS",
    "MemoryCard",
    "MemoryHit",
    "MemoryStore",
    "evidence_from_hits",
    "memory_store",
    "render_hits",
]
