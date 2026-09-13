"""REST + SSE 路由（移植自 server/api/routes.ts）。

**契约冻结**：请求/响应结构与 TS 版逐字段对齐，React 前端无需任何改动。
唯一差异在 SSE 的实现方式——Express 直接 ``res.write``，这里用
``StreamingResponse`` + ``asyncio.Queue``，把工作线程发布的事件安全地
搬到事件循环上（``loop.call_soon_threadsafe``）。
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..agents.registry import PLANNED_AGENTS, all_agent_meta
from ..config import get_config, public_config, update_config
from ..core.blackboard import blackboard
from ..core.clock import now_iso
from ..core.events import event_bus
from ..core.orchestrator import orchestrator
from ..core.store import task_store
from ..core.types import PHASE_LABEL, PHASE_ORDER, Brief, TaskRecord, create_empty_brief
from ..knowledge.compliance import INDUSTRY_RULES, LEXICON_GROUPS
from ..knowledge.industry import CHANNEL_RULES, INDUSTRY_PROFILES
from ..knowledge.memory import CARD_KINDS, KIND_LABEL, memory_store
from ..llm import resolve_provider
from ..logger import create_logger

log = create_logger("api")

router = APIRouter()

PRIORITIES = ("low", "normal", "high", "urgent")

#: 等价于 JS 的 ``str.split(/[\n,，、;；]/)``
_SPLIT_RE = re.compile(r"[\n,，、;；]")


# ------------------------------------------------------------------ #
# 入参解析                                                            #
# ------------------------------------------------------------------ #


def _as_string_array(value: Any) -> list[str]:
    """等价于 TS 的 ``asStringArray``：数组直接取值，字符串按分隔符拆。"""
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [part.strip() for part in _SPLIT_RE.split(value) if part.strip()]
    return []


def _pick(raw: dict[str, Any], key: str, fallback: str) -> str:
    value = raw.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def parse_brief(input_value: Any) -> Brief:
    base = create_empty_brief()
    if not isinstance(input_value, dict):
        return base

    raw: dict[str, Any] = input_value
    priority = raw.get("priority")
    deadline = raw.get("deadline")
    notes = raw.get("notes")

    return Brief(
        brand=_pick(raw, "brand", "未命名品牌"),
        product=_pick(raw, "product", "未命名产品"),
        objective=_pick(raw, "objective", base.objective),
        audience=_pick(raw, "audience", "目标人群"),
        channel=_pick(raw, "channel", base.channel),
        tone=_pick(raw, "tone", base.tone),
        industry=_pick(raw, "industry", base.industry),
        keywords=_as_string_array(raw.get("keywords")),
        constraints=_as_string_array(raw.get("constraints")),
        deliverables=_as_string_array(raw.get("deliverables")),
        notes=notes if isinstance(notes, str) else "",
        priority=priority if priority in PRIORITIES else "normal",
        deadline=deadline if isinstance(deadline, str) and deadline else None,
    )


def task_summary(task: TaskRecord) -> dict[str, Any]:
    return {
        "id": task.id,
        "brief": task.brief.model_dump(mode="json"),
        "status": task.status,
        "phase": task.phase,
        "phase_label": PHASE_LABEL.get(task.phase, task.phase),
        "revision_round": task.revision_round,
        "turn_used": task.turn_used,
        "scorecard": task.scorecard.model_dump(mode="json") if task.scorecard else None,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "finished_at": task.finished_at,
        "tokens": task.tokens.model_dump(mode="json"),
        "artifact_count": len(task.artifacts),
        "approval": task.approval.model_dump(mode="json"),
        "error": task.error,
    }


def _round1(value: float) -> float:
    """等价于 JS ``Math.round(v * 10) / 10``（仅用于非负数）。"""
    return math.floor(value * 10 + 0.5) / 10


def _avg(values: list[float]) -> float:
    if not values:
        return 0
    return _round1(sum(values) / len(values))


def _rate(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0
    return _round1(numerator / denominator * 100)


# ------------------------------------------------------------------ #
# 基础信息                                                            #
# ------------------------------------------------------------------ #


@router.get("/health")
def health() -> dict[str, Any]:
    provider = resolve_provider()
    return {
        "ok": True,
        "time": now_iso(),
        "config": public_config(),
        "provider": {
            "name": provider.name,
            "model": provider.model,
            "simulated": getattr(provider, "simulated", True),
        },
    }


@router.get("/agents")
def agents() -> dict[str, Any]:
    return {
        "implemented": all_agent_meta(),
        "planned": PLANNED_AGENTS,
        "pipeline": [{"phase": phase, "label": PHASE_LABEL[phase]} for phase in PHASE_ORDER],
    }


@router.get("/knowledge")
def knowledge() -> dict[str, Any]:
    return {
        "lexicon": [
            {
                "category": group.category,
                "severity": group.severity,
                "law": group.law,
                "term_count": len(group.terms),
                "terms": list(group.terms[:12]),
            }
            for group in LEXICON_GROUPS
        ],
        "industry_rules": [
            {
                "industry": industry,
                "rule_count": len(rules),
                "categories": [rule.category for rule in rules],
            }
            for industry, rules in INDUSTRY_RULES.items()
        ],
        "channels": [
            {
                "channel": channel,
                "format": rule.format,
                "length_hint": rule.length_hint,
                "blocks": list(rule.blocks),
            }
            for channel, rule in CHANNEL_RULES.items()
        ],
        "industries": list(INDUSTRY_PROFILES.keys()),
    }


@router.get("/memory")
def read_memory(kind: str | None = Query(default=None), limit: int = Query(default=50)) -> dict[str, Any]:
    """A11 记忆库：知识卡片列表 + 容量统计。"""
    cards = memory_store.list_cards(kind=kind, limit=max(1, min(limit, 200)))
    return {
        "stats": memory_store.stats(),
        "kinds": [{"kind": item, "label": KIND_LABEL[item]} for item in CARD_KINDS],
        "cards": [card.to_dict() for card in cards],
    }


@router.post("/memory/search")
def search_memory(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """按自然语言描述检索可复用知识（供其他智能体与人工复用）。"""
    body: dict[str, Any] = payload or {}
    query = str(body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    top_k = body.get("topK")
    top_k = int(top_k) if isinstance(top_k, (int, float)) and not isinstance(top_k, bool) else 5
    hits = memory_store.retrieve(
        query,
        brand=str(body.get("brand") or "").strip(),
        channel=str(body.get("channel") or "").strip(),
        industry=str(body.get("industry") or "").strip(),
        top_k=max(1, min(top_k, 20)),
    )
    return {"query": query, "hits": [hit.to_dict() for hit in hits]}


@router.get("/settings")
def read_settings() -> dict[str, Any]:
    return public_config()


@router.put("/settings")
def write_settings(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    body: dict[str, Any] = payload or {}
    llm_patch: dict[str, Any] = {}
    if body.get("provider") in ("mock", "openai"):
        llm_patch["provider"] = body["provider"]
    for key in ("baseUrl", "model", "apiKey"):
        if isinstance(body.get(key), str):
            llm_patch[key] = body[key]
    for key in ("temperature", "maxTokens", "timeoutMs"):
        value = body.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            llm_patch[key] = value

    patch: dict[str, Any] = {"llm": llm_patch}
    for key in ("turnBudget", "maxRevisions", "qualityThreshold"):
        if isinstance(body.get(key), (int, float)) and not isinstance(body.get(key), bool):
            patch[key] = body[key]
    if isinstance(body.get("autoApprove"), bool):
        patch["autoApprove"] = body["autoApprove"]

    update_config(patch)
    log.info(
        f"运行时配置已更新：provider={get_config().llm.provider} model={get_config().llm.model}"
    )
    return public_config()


# ------------------------------------------------------------------ #
# 任务                                                                #
# ------------------------------------------------------------------ #


@router.get("/tasks")
def list_tasks() -> dict[str, Any]:
    return {"tasks": [task_summary(task) for task in task_store.list()]}


@router.post("/tasks", status_code=201)
def create_task(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    body: dict[str, Any] = payload or {}
    brief_input = body.get("brief")
    if brief_input is None:
        brief_input = body
    brief = parse_brief(brief_input)
    if not brief.brand or brief.brand == "未命名品牌":
        # 允许创建，但在界面上会以「未命名品牌」展示
        log.warn("创建任务时未提供品牌名")

    auto_approve = body.get("autoApprove")
    task = orchestrator.create_task(
        brief, auto_approve=auto_approve if isinstance(auto_approve, bool) else False
    )
    return {"task": task.model_dump(mode="json")}


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task": task.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in event_bus.history(task_id)],
        "blackboard": blackboard.snapshot(task_id).model_dump(mode="json"),
    }


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, Any]:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in ("running", "awaiting_approval"):
        raise HTTPException(status_code=409, detail="任务仍在执行中，无法删除")

    task_store.delete(task_id)
    event_bus.drop(task_id)
    # 顺带回收该任务的断点续跑检查点，避免 checkpoints.sqlite 只增不减
    purged = orchestrator.purge_checkpoints(task_id)
    blackboard.flush()
    return {"ok": True, "id": task_id, "purged_checkpoints": purged}


@router.post("/tasks/{task_id}/decide")
def decide(task_id: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    body: dict[str, Any] = payload or {}
    decision = str(body.get("decision") or "")
    if decision not in ("approve", "revise", "reject"):
        raise HTTPException(status_code=400, detail="decision 必须是 approve / revise / reject")

    try:
        task = orchestrator.decide(task_id, decision, str(body.get("comment") or ""))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"task": task_summary(task)}


@router.get("/tasks/{task_id}/blackboard")
def get_blackboard(task_id: str) -> dict[str, Any]:
    return blackboard.snapshot(task_id).model_dump(mode="json")


# ------------------------------------------------------------------ #
# SSE 实时事件                                                        #
# ------------------------------------------------------------------ #


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    since: int = Query(default=0),
) -> StreamingResponse:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=2000)

    def push(event: Any) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # 慢客户端：丢弃而不是阻塞编排线程
            pass

    def on_event(event: Any) -> None:
        # 事件由工作线程发布，必须切回事件循环再入队
        loop.call_soon_threadsafe(push, event)

    # 与 TS 一致：先回放历史，再订阅增量（前端带 since 重连以补齐缺口）
    for event in event_bus.replay(task_id, since or 0):
        queue.put_nowait(event)
    unsubscribe = event_bus.subscribe(task_id, on_event)

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield (
                    f"id: {event.seq}\n"
                    "event: message\n"
                    f"data: {event.model_dump_json()}\n\n"
                )
        finally:
            unsubscribe()

    # 注意：不要手工设置 Connection 头，交给 uvicorn/h11 管理，
    # 否则会与 HTTP/1.1 的连接复用产生冲突。
    return StreamingResponse(
        stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------ #
# 指标                                                                #
# ------------------------------------------------------------------ #


@router.get("/metrics")
def metrics() -> dict[str, Any]:
    tasks = task_store.list()
    completed = [t for t in tasks if t.status == "completed"]

    gates = [gate for task in tasks for gate in task.gates]
    pass_gates = [gate for gate in gates if gate.verdict == "pass"]

    facts = [fact for task in tasks for fact in blackboard.facts(task.id)]
    verified_facts = [fact for fact in facts if fact.status == "verified"]

    brand_scores: list[float] = []
    for task in tasks:
        artifact = next((a for a in task.artifacts if a.type == "compliance_report"), None)
        if artifact is None:
            continue
        consistency = artifact.content.get("brand_consistency")
        score = consistency.get("score") if isinstance(consistency, dict) else None
        if isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score) and score > 0:
            brand_scores.append(float(score))

    latencies = sorted(
        result.metrics.latency_ms
        for task in tasks
        for result in task.results
        if result.metrics.latency_ms > 0
    )
    p99 = (
        latencies[min(len(latencies) - 1, math.floor(len(latencies) * 0.99))]
        if latencies
        else 0
    )

    tokens = {
        "prompt": sum(task.tokens.prompt for task in tasks),
        "completion": sum(task.tokens.completion for task in tasks),
        "cost_usd": sum(task.tokens.cost_usd for task in tasks),
    }

    all_results = [result for task in tasks for result in task.results]
    simulated = sum(1 for result in all_results if result.metrics.simulated)

    provider = resolve_provider()
    config = get_config()

    return {
        "system": {
            "total_tasks": len(tasks),
            "completed": len(completed),
            "rejected": sum(1 for t in tasks if t.status == "rejected"),
            "failed": sum(1 for t in tasks if t.status == "failed"),
            "running": sum(1 for t in tasks if t.status == "running"),
            "awaiting_approval": sum(1 for t in tasks if t.status == "awaiting_approval"),
            "first_pass_rate": _rate(
                sum(1 for t in tasks if t.revision_round == 0), len(tasks)
            ),
            "avg_revision_rounds": _avg([t.revision_round for t in tasks]),
            "avg_overall_score": _avg(
                [t.scorecard.overall for t in tasks if t.scorecard is not None]
            ),
            "gate_pass_rate": _rate(len(pass_gates), len(gates)),
            "fact_accuracy": _rate(len(verified_facts), len(facts)),
            "brand_consistency": _avg(brand_scores),
            "turn_budget_hit_rate": _rate(
                sum(1 for t in tasks if t.turn_used >= config.turn_budget), len(tasks)
            ),
            "p99_agent_latency_ms": p99,
            "tokens": tokens,
            "simulated_ratio": _rate(simulated, len(all_results)),
        },
        "providers": [
            {
                "name": provider.name,
                "model": provider.model,
                "simulated": getattr(provider, "simulated", True),
            }
        ],
        "agents": [
            {
                "id": meta["id"],
                "name": meta["name"],
                **_agent_row(meta["id"], all_results),
            }
            for meta in all_agent_meta()
        ],
    }


def _agent_row(meta_id: str, all_results: list[Any]) -> dict[str, Any]:
    results = [r for r in all_results if r.agent_id == meta_id]
    return {
        "runs": len(results),
        "avg_confidence": _avg([r.confidence * 100 for r in results]),
        "avg_latency_ms": _avg([r.metrics.latency_ms for r in results]),
        "gate_failures": sum(1 for r in results if r.gate_result and r.gate_result != "pass"),
        "veto_used": sum(1 for r in results if r.needs_human_review),
    }


def create_api_router() -> APIRouter:
    return router


__all__ = ["create_api_router", "router"]
