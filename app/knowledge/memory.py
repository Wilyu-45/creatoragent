"""A11 记忆库：知识卡片的持久化与检索（RAG-lite）。

对应《creator.md》A11 的两项核心职责：

* **存储**——把 A11 沉淀的知识卡片与模板固化到 ``data/memory.json``，跨任务存活；
* **为其他智能体提供 RAG 检索**——``retrieve()`` 按「文本相关度 + 品牌/渠道/行业元数据」
  打分召回，供 A1/A2/A4 在下一次创作中复用品牌调性、有效表达与历史案例。

为什么不用向量库
----------------
MVP 不引入 embedding 依赖：中文按 2-gram、英文/数字按词切分做 TF 加权重叠，
再叠加品牌/渠道/行业命中加成即可覆盖「同品牌历史资产复用」这一主场景。
接口刻意保持 ``retrieve(query, …, top_k) -> list[MemoryHit]`` 的形状，
日后替换为向量检索时上层无需改动（与 plan.md 2.2.3 的 RAG Server 位置一致）。

知识新鲜度
----------
``MAX_CARDS`` 限制库容量，超出后淘汰最久未更新的卡片（等价于「过期知识」下线），
避免长期运行后检索被陈年噪声淹没。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import MEMORY_FILE
from ..core.clock import now_iso
from ..core.events import new_id
from ..logger import create_logger

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

#: 文本三项权重之和为 0.6，乘以 _W_TEXT_GAIN 后归一——「文本完全命中」单项即可触顶；
#: 元数据（品牌 0.25 / 渠道 0.1 / 行业 0.05）作为加成，合计上限 0.4。
_W_TAG = 0.3
_W_TITLE = 0.2
_W_BODY = 0.1
_W_TEXT_GAIN = 2.0
_W_BRAND = 0.25
_W_CHANNEL = 0.1
_W_INDUSTRY = 0.05

_FIELD_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokens(text: str) -> set[str]:
    """中文取 2-gram（不足 2 字时退回单字），英文/数字取词。"""
    lowered = (text or "").lower()
    tokens = set(_FIELD_WORD_RE.findall(lowered))
    cjk = _CJK_RE.findall(lowered)
    if len(cjk) >= 2:
        tokens.update("".join(pair) for pair in zip(cjk, cjk[1:]))
    else:
        tokens.update(cjk)
    return tokens


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryHit:
    """一次召回的命中结果，带分数与命中理由（便于前端与调试解释）。"""

    card: MemoryCard
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
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
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
        }


class MemoryStore:
    """单进程记忆库：内存索引 + JSON 落盘。"""

    def __init__(self) -> None:
        self._cards: list[MemoryCard] = []
        self._index: dict[str, MemoryCard] = {}
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
                    self._cards.append(card)
                    self._index[_digest(card.kind, card.title, card.content)] = card
                if self._cards:
                    log.info(f"已加载 {len(self._cards)} 条跨任务知识卡片")
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
    ) -> int:
        """写入一批知识卡片，返回新增条数（重复内容自动跳过）。"""
        self.load()
        stamp = now_iso()
        drafts: list[dict[str, Any]] = []

        for item in cards:
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

        added = 0
        with self._lock:
            for draft in drafts:
                if draft["kind"] not in CARD_KINDS:
                    draft["kind"] = "lesson"
                if not draft["title"] or not draft["content"]:
                    continue
                key = _digest(draft["kind"], draft["title"], draft["content"])
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
                )
                self._cards.append(card)
                self._index[key] = card
                added += 1

            if added:
                self._evict()

        if added:
            self.flush()
            log.info(f"任务 {task_id} 沉淀 {added} 条新知识（库容量 {len(self._cards)}）")
        return added

    def _evict(self) -> None:
        """超出容量上限时淘汰最早的卡片（调用方需持锁）。"""
        overflow = len(self._cards) - MAX_CARDS
        if overflow <= 0:
            return
        dropped = self._cards[:overflow]
        self._cards = self._cards[overflow:]
        for card in dropped:
            self._index.pop(_digest(card.kind, card.title, card.content), None)
        log.warn(f"记忆库超出上限，淘汰 {len(dropped)} 条最旧知识")

    # ---------------------------- 检索 ---------------------------- #

    def retrieve(
        self,
        query: str,
        *,
        brand: str = "",
        channel: str = "",
        industry: str = "",
        top_k: int = 5,
        exclude_task: str | None = None,
    ) -> list[MemoryHit]:
        """按相关度召回知识卡片。

        ``exclude_task`` 用于排除当前任务自己刚写入的卡片，
        保证「召回的是历史资产」而不是自我循环。
        """
        self.load()
        query_tokens = _tokens(query)
        hits: list[MemoryHit] = []

        with self._lock:
            cards = list(self._cards)

        for card in cards:
            if exclude_task and card.task_id == exclude_task:
                continue

            score = 0.0
            reasons: list[str] = []

            tag_score = max((_overlap(query_tokens, tag) for tag in card.tags), default=0.0)
            title_score = _overlap(query_tokens, card.title)
            body_score = _overlap(query_tokens, card.content)
            text_score = _W_TAG * tag_score + _W_TITLE * title_score + _W_BODY * body_score
            if text_score > 0:
                score += text_score * _W_TEXT_GAIN
                reasons.append("文本相关")

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
            hits.append(MemoryHit(card=card, score=min(score, 1.0), reasons=reasons))

        hits.sort(key=lambda hit: (hit.score, hit.card.created_at), reverse=True)
        top = hits[: max(0, top_k)]

        if top:
            with self._lock:
                for hit in top:
                    hit.card.hits += 1

        return top

    # ---------------------------- 查询 ---------------------------- #

    def list_cards(
        self, *, kind: str | None = None, brand: str | None = None, limit: int = 50
    ) -> list[MemoryCard]:
        self.load()
        with self._lock:
            cards = list(self._cards)
        if kind:
            cards = [card for card in cards if card.kind == kind]
        if brand:
            cards = [card for card in cards if card.brand == brand]
        return sorted(cards, key=lambda card: card.created_at, reverse=True)[: max(0, limit)]

    def stats(self) -> dict[str, Any]:
        self.load()
        with self._lock:
            cards = list(self._cards)
        by_kind: dict[str, int] = {}
        for card in cards:
            by_kind[card.kind] = by_kind.get(card.kind, 0) + 1
        return {
            "total": len(cards),
            "capacity": MAX_CARDS,
            "by_kind": [
                {"kind": kind, "label": KIND_LABEL[kind], "count": by_kind.get(kind, 0)}
                for kind in CARD_KINDS
            ],
            "brands": sorted({card.brand for card in cards if card.brand}),
            "tasks": len({card.task_id for card in cards}),
            "reused": sum(1 for card in cards if card.hits > 0),
        }


def render_hits(hits: list[dict[str, Any]], *, limit: int = 5) -> str:
    """把召回结果渲染成提示词片段；无召回时返回空串。

    真实模型与离线引擎共用同一段文案，保证「有记忆」这件事对两条路径可见。
    """
    lines: list[str] = []
    for index, hit in enumerate((hits or [])[:limit], start=1):
        title = str(hit.get("title") or "").strip()
        content = str(hit.get("content") or "").strip()
        if not title and not content:
            continue
        label = str(hit.get("kind_label") or KIND_LABEL.get(str(hit.get("kind")), "历史资产"))
        task_id = str(hit.get("task_id") or "")
        reasons = "、".join(str(reason) for reason in (hit.get("reasons") or []))
        meta = "｜".join(part for part in (f"来源任务 {task_id}" if task_id else "", reasons) if part)
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


memory_store = MemoryStore()

__all__ = [
    "CARD_KINDS",
    "KIND_LABEL",
    "MAX_CARDS",
    "MemoryCard",
    "MemoryHit",
    "MemoryStore",
    "evidence_from_hits",
    "memory_store",
    "render_hits",
]
