"""长文档素材研读通路（「文档素材进流水线」内化人工精读）。

Brief 里 ``kind=document`` 的本地/内联文本文档（小说原文、往期成稿、风格
skill 等）过去只进「素材清单」——内容要靠人在流水线外精读后手工写进
``brief.constraints``。本模块把它内化为一条 map-reduce 研读通路：

1. **取文**：复用 ``assets.load_local`` 的安全边界（只认 ``assets/`` 下的
   相对路径），内联 data URL 就地解码；远端文档**不自动下载**（与
   ``core/web.py`` 的 SSRF 取舍一致），如实记入 issues；
2. **map**：按块逐次调用（purpose ``DOC.digest.map``），每块提炼
   summary / key_facts / quotes / style_notes；
3. **reduce**：一次调用合并为面向创作团队的素材包（purpose
   ``DOC.digest.reduce``），**只合并不新增事实**。

三条硬规则
----------
* **没有可用文档时零调用、零提示词变化**——不带文档素材的任务与历史行为
  逐字一致（黄金基线的前提）；
* **失败与截断如实入档**：单块研读失败、块数超出上限、成本熔断导致的
  离线降级，都写进产物的 ``issues``，绝不静默丢弃——静默会让下游智能体
  误以为「素材就这么多」；
* **研读失败不阻断任务**：digest 是情报增强，不是依赖项，全程不抛异常。
"""

from __future__ import annotations

import base64
import json
import math
from typing import TYPE_CHECKING, Any, Callable

from ..config import get_config
from ..core.tracing import tracer
from ..logger import create_logger
from ..llm.engine import chat
from ..llm.json_utils import as_num, as_obj_array, as_str, as_str_array, extract_json
from ..llm.types import ChatMessage, LLMRequest
from .assets import load_local, parse_assets

if TYPE_CHECKING:  # 仅类型标注，避免运行时反向依赖
    from .types import Brief

log = create_logger("digest")

#: 单块研读的字符上限（约 3 万 token 输入，64k 上下文网关可安全承载）
MAX_CHUNK_CHARS = 40_000
#: 单块下限：太小的块没有研读价值，也避免超短文档被切出大量碎片
MIN_CHUNK_CHARS = 2_000
#: 超过该字符数时在研读前发出 token 预算告警（默认 5 万预算必然不够）
TOKEN_WARN_CHARS = 100_000

MAP_SYSTEM = (
    "你是资料研读员。只依据给定的文档节选提炼要点，不得编造节选之外的内容，"
    "不得引用任何外部知识或联网信息。原文摘录必须逐字来自节选。"
    "只输出 JSON，不输出任何解释性文字。"
)

MAP_SCHEMA = """{
  "summary": "（≤300 字，本块内容概要）",
  "key_facts": ["（≤15 条，含原文数字/时间/名称/关系的事实）"],
  "quotes": ["（≤8 条，可引用的原文逐字摘录）"],
  "style_notes": ["（≤5 条，行文风格、叙事视角、语气特征）"]
}"""

REDUCE_SYSTEM = (
    "你是资料研读汇总员。只合并各块研读结果，不新增任何事实，不改写原文摘录。"
    "creative_brief 面向创作团队说明「这些素材能支撑什么选题与写法」，≤5000 字。"
    "只输出 JSON，不输出任何解释性文字。"
)

REDUCE_SCHEMA = """{
  "documents": [{"title": "", "summary": "", "key_facts": [""], "quotes": [""], "style_notes": [""]}],
  "creative_brief": ""
}"""


def _decode_text(data: bytes) -> str:
    """按 utf-8 → gbk 顺序解码；都失败时抛 ``UnicodeDecodeError``。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk")


def _text_of(asset: Any, issues: list[str]) -> str:
    """取一个 document 素材的文本；取不到时把原因写进 issues 并返回空串。"""
    ref = asset.ref
    if asset.source == "local":
        data, issue = load_local(ref)
        if data is None:
            issues.append(f"{ref}：{issue}")
            return ""
    elif asset.source == "inline":
        _, _, body = ref.partition(",")
        try:
            data = base64.b64decode(body, validate=False)
        except (ValueError, TypeError):
            issues.append(f"{ref}：data URL 无法解码")
            return ""
    else:
        issues.append(f"{ref}：远端文档不自动下载，请改为 assets/ 下的本地文件或内联 data URL")
        return ""
    try:
        return _decode_text(data)
    except (UnicodeDecodeError, LookupError):
        issues.append(f"{ref}：不是可解码的文本文档（utf-8/gbk 均失败），已跳过")
        return ""


def collect_documents(brief: "Brief", issues: list[str]) -> list[tuple[str, str]]:
    """收集可用文档素材为 ``(标题, 正文)`` 列表；问题写进 issues。"""
    pairs: list[tuple[str, str]] = []
    for asset in parse_assets(brief.assets):
        if asset.kind != "document":
            continue
        if not asset.usable:
            issues.append(f"{asset.ref}：{asset.issue}")
            continue
        text = _text_of(asset, issues).strip()
        if text:
            pairs.append((asset.title or asset.ref, text))
    return pairs


def _small_brief(brief: "Brief") -> dict[str, Any]:
    return {
        "brand": brief.brand,
        "product": brief.product,
        "objective": brief.objective,
        "channel": brief.channel,
        "audience": brief.audience,
        "keywords": brief.keywords,
        "constraints": brief.constraints,
    }


def _chunks_for(docs: list[tuple[str, str]], max_calls: int) -> tuple[list[dict[str, Any]], int]:
    """把所有文档切块，总块数不超过 ``max_calls``；返回 (块列表, 截断块数)。

    配额分配：每份文档保底 1 块，剩余配额按字符数比例（最大余数法）分配。
    按「全文统一块长 + 超出即按文档顺序截断」会让长文档吃光全部配额、
    把排在后面的短文档（往期成稿、风格 skill）整份挤出研读——真实链路踩过。
    """
    if not docs:
        return [], 0
    sizes = [len(text) for _, text in docs]
    doc_count = len(docs)
    if max_calls <= doc_count:
        # 配额比文档数还少：按字符数从大到小保长文档，其余整份落选（如实计数）
        quotas = [0] * doc_count
        for index in sorted(range(doc_count), key=lambda i: -sizes[i])[:max_calls]:
            quotas[index] = 1
    else:
        raw = [
            (size * (max_calls - doc_count) / sum(sizes)) if sum(sizes) else 0.0 for size in sizes
        ]
        quotas = [1 + int(basis) for basis in raw]
        leftover = max_calls - sum(quotas)
        if leftover > 0:
            by_remainder = sorted(range(doc_count), key=lambda i: raw[i] - int(raw[i]), reverse=True)
            for index in by_remainder[:leftover]:
                quotas[index] += 1

    chunks: list[dict[str, Any]] = []
    dropped = 0
    for (title, text), quota in zip(docs, quotas):
        if quota <= 0:
            dropped += max(1, math.ceil(len(text) / MAX_CHUNK_CHARS))
            continue
        chunk_size = min(MAX_CHUNK_CHARS, max(MIN_CHUNK_CHARS, math.ceil(len(text) / quota)))
        pieces = [text[start : start + chunk_size] for start in range(0, len(text), chunk_size)]
        # 块数封顶在文档配额内：长文档多出的尾部如实计数，不再挤占其他文档
        if len(pieces) > quota:
            dropped += len(pieces) - quota
            pieces = pieces[:quota]
        for index, piece in enumerate(pieces, start=1):
            chunks.append(
                {"title": title, "chunk_index": index, "chunk_total": len(pieces), "text": piece}
            )
    return chunks, dropped


def _normalize_map(data: dict[str, Any], chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": as_str(chunk.get("title"), "未命名文档"),
        "chunk_index": int(as_num(chunk.get("chunk_index"), 1)),
        "chunk_total": int(as_num(chunk.get("chunk_total"), 1)),
        "summary": as_str(data.get("summary"))[:400],
        "key_facts": as_str_array(data.get("key_facts"))[:15],
        "quotes": as_str_array(data.get("quotes"))[:8],
        "style_notes": as_str_array(data.get("style_notes"))[:5],
        # 内部标记：该块是否来自离线引擎（_jsonish 会剥掉，不进提示词）
        "_simulated": False,
    }


def _fallback_documents(maps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """reduce 不可用时的兜底：按文档聚合逐块要点（只合并不新增）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in maps:
        grouped.setdefault(item["title"], []).append(item)
    documents: list[dict[str, Any]] = []
    for title, items in grouped.items():
        facts: list[str] = []
        quotes: list[str] = []
        for item in items:
            for fact in item["key_facts"]:
                if fact not in facts:
                    facts.append(fact)
            for quote in item["quotes"]:
                if quote not in quotes:
                    quotes.append(quote)
        documents.append(
            {
                "title": title,
                "summary": "；".join(item["summary"] for item in items if item["summary"])[:800],
                "key_facts": facts[:40],
                "quotes": quotes[:10],
                "style_notes": items[0]["style_notes"][:8],
            }
        )
    return documents


def digest_documents(
    brief: "Brief",
    emit: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """研读 Brief 附带的文档素材，返回可直接写入黑板的 ``document_digest`` content。

    ``emit(message, payload, level)`` 用于过程事件。研读全程不抛异常，
    所有问题落在返回值的 ``issues`` 里。
    """
    issues: list[str] = []

    def notify(message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
        if emit is None:
            return
        try:
            emit(message, payload or {}, level)
        except Exception:  # noqa: BLE001 - 事件失败不影响研读
            log.warn(f"digest 事件发送失败：{message}")

    assets = parse_assets(brief.assets)
    docs = collect_documents(brief, issues)
    total_chars = sum(len(text) for _, text in docs)
    documents: list[dict[str, Any]] = []
    creative_brief = ""
    map_calls = 0
    reduce_calls = 0
    degraded_from = 0
    degraded_reason = ""
    stats: dict[str, Any] = {"docs": len(docs), "chars": total_chars, "degraded": False}

    span = tracer.start_span(
        "doc.digest",
        attributes={"doc.digest.docs": len(docs), "doc.digest.chars": total_chars},
    )
    try:
        if not docs:
            return {
                "documents": [],
                "creative_brief": "",
                "sources": [],
                "issues": issues,
                "stats": {"docs": 0, "chars": 0, "map_calls": 0, "reduce_calls": 0, "degraded": False},
            }

        cfg = get_config()
        if total_chars > TOKEN_WARN_CHARS:
            estimate = int(total_chars / 1.6)
            notify(
                f"素材文档共 {total_chars // 1000}k 字，研读输入约 {estimate // 1000}k token，"
                f"可能触发 TOKEN_BUDGET（当前 {cfg.token_budget}）熔断；建议先调高 tokenBudget 再继续",
                "warn",
            )

        chunks, dropped = _chunks_for(docs, max(1, cfg.digest_max_calls))
        if dropped:
            issues.append(
                f"文档总量 {total_chars} 字超出单任务 {cfg.digest_max_calls} 次研读上限，"
                f"已截断 {dropped} 块（其余内容未进入本次研读）"
            )

        maps: list[dict[str, Any]] = []
        for chunk in chunks:
            map_calls += 1
            try:
                response = chat(
                    LLMRequest(
                        purpose="DOC.digest.map",
                        messages=[
                            ChatMessage(role="system", content=MAP_SYSTEM),
                            ChatMessage(
                                role="user",
                                content=(
                                    f"【文档】{chunk['title']}（第 {chunk['chunk_index']}/"
                                    f"{chunk['chunk_total']} 块）\n\n【节选】\n{chunk['text']}"
                                    f"\n\n请提炼本节选要点，严格要求 JSON 结构如下：\n{MAP_SCHEMA}"
                                ),
                            ),
                        ],
                        context={
                            "documents": [
                                {
                                    "title": chunk["title"],
                                    "chunk_index": chunk["chunk_index"],
                                    "chunk_total": chunk["chunk_total"],
                                    "text": chunk["text"],
                                }
                            ],
                            "brief": _small_brief(brief),
                        },
                        json=True,
                    )
                )
                parsed = extract_json(response.content)
                if not isinstance(parsed, dict):
                    raise ValueError("返回内容无法解析为 JSON")
                item = _normalize_map(parsed, chunk)
                item["_simulated"] = bool(response.simulated)
                maps.append(item)
                # 降级判定只认 degraded_reason：mock 提供方本身就是离线引擎，
                # simulated=True 是常态而非「降级」，不能因此给产物打降级标记
                if response.degraded_reason and degraded_from == 0:
                    degraded_from = int(chunk["chunk_index"])
                    degraded_reason = response.degraded_reason
            except Exception as error:  # noqa: BLE001 - 单块失败如实记录，不阻断
                issues.append(
                    f"{chunk['title']} 第 {chunk['chunk_index']} 块研读失败："
                    f"{type(error).__name__}: {error}"
                )

        if degraded_from:
            issues.append(
                f"第 {degraded_from} 块起研读已离线降级"
                f"（{degraded_reason or '成本熔断或引擎回退'}），其要点为占位内容"
            )

        if maps:
            try:
                response = chat(
                    LLMRequest(
                        purpose="DOC.digest.reduce",
                        messages=[
                            ChatMessage(role="system", content=REDUCE_SYSTEM),
                            ChatMessage(
                                role="user",
                                content=(
                                    f"【各块研读结果】\n{_jsonish(maps)}\n\n"
                                    f"请合并为面向创作团队的素材包，"
                                    f"严格要求 JSON 结构如下：\n{REDUCE_SCHEMA}"
                                ),
                            ),
                        ],
                        context={"maps": [_slim(m) for m in maps], "brief": _small_brief(brief)},
                        json=True,
                    )
                )
                parsed = extract_json(response.content)
                if not isinstance(parsed, dict):
                    raise ValueError("返回内容无法解析为 JSON")
                documents = [
                    {
                        "title": as_str(item.get("title"), "未命名文档"),
                        "summary": as_str(item.get("summary"))[:800],
                        "key_facts": as_str_array(item.get("key_facts"))[:40],
                        "quotes": as_str_array(item.get("quotes"))[:10],
                        "style_notes": as_str_array(item.get("style_notes"))[:8],
                    }
                    for item in as_obj_array(parsed.get("documents"))
                ]
                creative_brief = as_str(parsed.get("creative_brief"))[:5000]
                reduce_calls = 1
            except Exception as error:  # noqa: BLE001 - 汇总失败退回逐块结果
                issues.append(f"研读汇总失败（退回逐块要点）：{type(error).__name__}: {error}")
        if not documents and maps:
            documents = _fallback_documents(_slim_maps(maps))

        refs = {(asset.title or asset.ref): asset.ref for asset in assets}
        sources = [
            {
                "ref": refs.get(title, title),
                "title": title,
                "chars": len(text),
                "chunks": sum(1 for m in maps if m["title"] == title),
                "simulated": (
                    all(m["_simulated"] for m in maps if m["title"] == title)
                    if any(m["title"] == title for m in maps)
                    else False
                ),
            }
            for title, text in docs
        ]

        stats = {
            "docs": len(docs),
            "chars": total_chars,
            "map_calls": map_calls,
            "reduce_calls": reduce_calls,
            "degraded": degraded_from > 0,
        }
        notify(
            f"文档研读完成：{len(docs)} 份文档共 {total_chars} 字，{map_calls} 次块研读"
            + ("（部分离线降级）" if degraded_from else "")
            + (f"；{len(issues)} 条问题需关注" if issues else ""),
            "warn" if issues else "info",
            {"issues": issues[:5]},
        )
        return {
            "documents": documents,
            "creative_brief": creative_brief,
            "sources": sources,
            "issues": issues,
            "stats": stats,
        }
    finally:
        tracer.finish_span(
            span,
            status="error" if map_calls == 0 and reduce_calls == 0 and docs else "ok",
            attributes={
                "doc.digest.calls": map_calls + reduce_calls,
                "doc.digest.degraded": degraded_from > 0,
                "doc.digest.issues": len(issues),
            },
        )


def _slim(item: dict[str, Any]) -> dict[str, Any]:
    """剥掉 ``_`` 开头的内部标记字段。"""
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _slim_maps(maps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_slim(item) for item in maps]


def _jsonish(maps: list[dict[str, Any]]) -> str:
    """把逐块结果压成紧凑 JSON 文本（剥掉内部标记），供 reduce 提示词引用。"""
    return json.dumps(_slim_maps(maps), ensure_ascii=False)


__all__ = [
    "collect_documents",
    "digest_documents",
    "MAX_CHUNK_CHARS",
    "TOKEN_WARN_CHARS",
]
