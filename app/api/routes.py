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
from fastapi.responses import FileResponse, StreamingResponse

from ..agents.registry import PLANNED_AGENTS, all_agent_meta
from ..config import (
    DATA_DIR,
    EXPORTS_DIR,
    RUBRIC_VERSION,
    get_config,
    llm_settings_for,
    public_config,
    save_persisted_settings,
    update_config,
)
from ..core.blackboard import blackboard
from ..core.clock import now_iso
from ..core.digital_human import drop_task as drop_digital_human_jobs
from ..core.digital_human import list_jobs as list_digital_human_jobs
from ..core.digital_human import create_job as create_digital_human_job
from ..core.evaluations import build_record, evaluation_store
from ..core.events import event_bus
from ..core.golden import DEFAULT_TOLERANCE, compare, coverage, load_baseline, load_dataset
from ..core.golden_runner import reset_state as reset_golden_state
from ..core.golden_runner import start_background_run, state as golden_state
from ..core.judge import evaluate as judge_evaluate
from ..core.orchestrator import orchestrator
from ..core.store import task_store
from ..core.tracing import EXPORT_DIR, parse_traceparent, tracer
from ..core import otel
from ..core.types import (
    PHASE_LABEL,
    PHASE_ORDER,
    Brief,
    BriefAsset,
    TaskRecord,
    create_empty_brief,
)
from ..knowledge.compliance import INDUSTRY_RULES, LEXICON_GROUPS
from ..knowledge.industry import CHANNEL_RULES, INDUSTRY_PROFILES
from ..knowledge.language import (
    compliance_coverage,
    language_options,
    normalize_language,
)
from ..knowledge.memory import CARD_KINDS, KIND_LABEL, memory_store
from ..llm import resolve_provider
from ..llm.cache import response_cache
from ..llm.cost import cost_guard
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


def _parse_assets(value: Any) -> list[BriefAsset]:
    """解析 brief.assets：接受字符串（一行一条）或对象数组。"""
    if isinstance(value, str):
        items: list[Any] = [part.strip() for part in value.splitlines() if part.strip()]
    elif isinstance(value, list):
        items = value
    else:
        return []
    assets: list[BriefAsset] = []
    for item in items:
        if isinstance(item, str):
            assets.append(BriefAsset(ref=item))
        elif isinstance(item, dict):
            kind = str(item.get("kind") or "image").strip().lower()
            assets.append(
                BriefAsset(
                    kind=kind if kind in ("image", "video", "document", "link") else "image",  # type: ignore[arg-type]
                    ref=str(item.get("ref") or item.get("url") or "").strip(),
                    title=str(item.get("title") or "").strip(),
                    note=str(item.get("note") or "").strip(),
                )
            )
    return assets


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
        # 语言做归一化：用户可能写 en-US / English / 英文，统一收敛到主语言子标签
        language=normalize_language(str(raw.get("language") or base.language)),
        keywords=_as_string_array(raw.get("keywords")),
        constraints=_as_string_array(raw.get("constraints")),
        deliverables=_as_string_array(raw.get("deliverables")),
        notes=notes if isinstance(notes, str) else "",
        priority=priority if priority in PRIORITIES else "normal",
        deadline=deadline if isinstance(deadline, str) and deadline else None,
        # 素材（多模态输入）：宽容解析，非法条目在 assets.py 里带 issue 如实标注，
        # 不在这里静默丢弃——「素材不可用」本身是需要让智能体知道的信息
        assets=_parse_assets(raw.get("assets")),
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
        "intent_conflicts": task.intent_conflicts,
        "published_at": task.published_at,
        "tenant": task.tenant,
        "scorecard": task.scorecard.model_dump(mode="json") if task.scorecard else None,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "finished_at": task.finished_at,
        "tokens": task.tokens.model_dump(mode="json"),
        "artifact_count": len(task.artifacts),
        "approval": task.approval.model_dump(mode="json"),
        "error": task.error,
    }


# ------------------------------------------------------------------ #
# 租户隔离（plan.md D17）                                             #
# ------------------------------------------------------------------ #


def _tenant_of(request: Request) -> str:
    """鉴权中间件写入的租户标识；未启用鉴权时统一为 ``default``。"""
    return str(getattr(request.state, "tenant", "default") or "default")


def _require_task(task_id: str, request: Request) -> TaskRecord:
    """取任务并做租户校验：跨租户一律按 404 处理，避免探测他人任务是否存在。"""
    task = task_store.get(task_id)
    if task is None or task.tenant != _tenant_of(request):
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _tenant_tasks(request: Request) -> list[TaskRecord]:
    """当前租户可见的任务列表。"""
    tenant = _tenant_of(request)
    return [task for task in task_store.list() if task.tenant == tenant]


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
            # 有覆盖时部分智能体的实际提供方与全局默认不同（明细见 /api/metrics）
            "agentOverrides": len(get_config().llm.agent_models),
        },
        # 断点续跑是否真的可用：sqlite = 可跨重启；memory = 重启后任务无法续跑
        "checkpointer": {
            "kind": orchestrator.checkpointer_kind,
            "error": orchestrator.checkpointer_error,
        },
        # OTLP 导出状态：未配置时进程内追踪仍完整可用
        "otlp": {**otel.stats(), "endpoint": get_config().tracing.otlp_endpoint},
        # 采样与跨进程传播：采样只作用于导出面，进程内轨迹始终完整
        "tracing": {
            "sampler": get_config().tracing.sampler,
            "sampleRatio": get_config().tracing.sample_ratio,
            "propagation": "W3C traceparent（入站 POST /api/tasks / 出站 webhook）",
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
        # 多语言本地化：可选语言与各语言的合规覆盖情况（未覆盖的必须显式说明）
        "languages": language_options(),
        "language_compliance": {
            code: compliance_coverage(code)
            for code in [item["code"] for item in language_options()]
        },
    }


@router.get("/memory")
def read_memory(
    request: Request, kind: str | None = Query(default=None), limit: int = Query(default=50)
) -> dict[str, Any]:
    """A11 记忆库：知识卡片列表 + 容量统计（按当前租户隔离）。"""
    tenant = _tenant_of(request)
    cards = memory_store.list_cards(kind=kind, limit=max(1, min(limit, 200)), tenant=tenant)
    return {
        "stats": memory_store.stats(tenant),
        "kinds": [{"kind": item, "label": KIND_LABEL[item]} for item in CARD_KINDS],
        "cards": [card.to_dict() for card in cards],
    }


@router.post("/memory/search")
def search_memory(
    request: Request, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    """按自然语言描述检索可复用知识（与智能体召回同源，限当前租户）。"""
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
        tenant=_tenant_of(request),
    )
    return {"query": query, "hits": [hit.to_dict() for hit in hits]}


@router.get("/evaluations")
def read_evaluations(
    request: Request,
    taskId: str | None = Query(default=None),
    limit: int = Query(default=20),
) -> dict[str, Any]:
    """LLM-as-a-Judge 评估历史（plan.md 4.3 D14）。

    不传 ``taskId`` 时返回当前租户的最近评估记录，用于跨任务的 Prompt 回归对比。
    """
    records = evaluation_store.list(
        task_id=taskId, tenant=_tenant_of(request), limit=max(1, min(limit, 200))
    )
    return {
        "stats": evaluation_store.stats(tenant=_tenant_of(request)),
        "config": {
            "mode": get_config().judge.mode,
            "provider": get_config().judge.provider,
            "passThreshold": get_config().judge.pass_threshold,
            "weight": get_config().judge.weight,
            "rubric": RUBRIC_VERSION,
        },
        "records": [record.to_dict() for record in records],
    }


@router.post("/tasks/{task_id}/evaluate")
def evaluate_task(
    task_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    """对当前任务跑一次按需评估（人工触发，写入手工触发记录）。

    这是「评分台」：改完 Prompt 或人工改稿后，不必重跑整条流水线就能拿到新分数。
    """
    task = _require_task(task_id, request)
    body: dict[str, Any] = payload or {}
    provider = body.get("provider")
    if provider not in (None, "offline", "llm"):
        raise HTTPException(status_code=400, detail="provider 必须是 offline / llm")

    upstream = _judge_upstream(task)
    if not upstream:
        raise HTTPException(status_code=409, detail="该任务还没有可评估的文案产物")

    cfg = get_config()
    report = judge_evaluate(
        task.brief,
        upstream,
        mode=provider or cfg.judge.provider,
        pass_threshold=cfg.judge.pass_threshold,
        kind="manual",
        revision=task.revision_round,
    )
    record = evaluation_store.record(
        build_record(
            task_id=task.id,
            tenant=task.tenant,
            report=report,
            brand=task.brief.brand,
            channel=task.brief.channel,
            industry=task.brief.industry,
            trigger="manual",
        )
    )
    return {"task_id": task.id, "record": record.to_dict()}


def _judge_upstream(task: TaskRecord) -> dict[str, dict[str, Any]]:
    """装配按需评估所需的上游产物（复用编排器的取值口径）。"""
    return orchestrator._upstream_for_judge(task)  # noqa: SLF001 - 同一进程内的既定复用


# ------------------------------------------------------------------ #
# 黄金数据集（plan.md 4.3 D13「黄金数据集构建」）                      #
# ------------------------------------------------------------------ #


@router.get("/golden")
def read_golden() -> dict[str, Any]:
    """黄金数据集：用例清单 + 基线 + 覆盖矩阵 + 最近一次运行状态。

    数据集与基线都是**随仓库版本化的代码资产**（放在 ``golden/`` 而非 ``data/``），
    因此不做租户过滤 —— 它们是测试资产，不是某个租户的业务数据。
    """
    cases = load_dataset()
    baseline = load_baseline()
    current = golden_state()
    payload: dict[str, Any] = {
        "cases": [case.to_dict() for case in cases],
        "baseline": {
            "updated_at": baseline.get("updated_at"),
            "rubric": baseline.get("rubric"),
            "engine": baseline.get("engine"),
            "note": baseline.get("note"),
            "cases": baseline.get("cases") or {},
        },
        "coverage": coverage(),
        "rubric": RUBRIC_VERSION,
        "tolerance": DEFAULT_TOLERANCE,
        "state": current.to_dict(),
    }
    # 跑完（或有历史结果）时顺带给出与基线的对比结论，界面无需自己算
    if current.results and not current.running:
        payload["comparison"] = compare(current.results, scope=current.scope)
    return payload


@router.post("/golden/run", status_code=202)
def run_golden(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """在后台启动一次黄金数据集回归（不阻塞请求，立即返回 202）。

    跑完 10 条用例是分钟级的，**不能**把 HTTP 请求挂在上面（浏览器与 fetch 都有超时）。
    因此这里只负责启动；调用方用 ``GET /api/golden`` 轮询 ``state`` 看进度与结果。
    """
    body: dict[str, Any] = payload or {}
    limit = body.get("limit")
    limit = int(limit) if isinstance(limit, (int, float)) and not isinstance(limit, bool) else None
    raw_ids = body.get("caseIds")
    case_ids = [str(item) for item in raw_ids] if isinstance(raw_ids, list) else None

    started, snapshot = start_background_run(limit=limit, case_ids=case_ids)
    if not started:
        raise HTTPException(status_code=409, detail="黄金数据集正在运行中，请等待完成或先 reset")
    return {"started": True, "state": snapshot.to_dict()}


@router.post("/golden/reset")
def reset_golden() -> dict[str, Any]:
    """清空上一次运行结果（仅在未运行时允许）。"""
    if golden_state().running:
        raise HTTPException(status_code=409, detail="黄金数据集正在运行中，无法重置")
    return {"ok": True, "state": reset_golden_state().to_dict()}


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
    for key in ("costBudgetUsd", "tokenBudget", "digestMaxCalls"):
        value = body.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            patch[key] = value
    if isinstance(body.get("llmCache"), bool):
        patch["llmCache"] = body["llmCache"]

    # 记忆库向量检索（plan.md 2.2.3）与发布投递（plan.md v2.0）
    for key in ("embeddingProvider", "embeddingBaseUrl", "embeddingApiKey", "embeddingModel"):
        if isinstance(body.get(key), str):
            patch[key] = body[key]
    for key in ("embeddingDim", "embeddingWeight"):
        value = body.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            patch[key] = value
    if isinstance(body.get("publishWebhookUrl"), str):
        patch["publishWebhookUrl"] = body["publishWebhookUrl"]
    for key in ("publishRetry", "publishTickSeconds"):
        value = body.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            patch[key] = value
    if isinstance(body.get("publishAutoDispatch"), bool):
        patch["publishAutoDispatch"] = body["publishAutoDispatch"]

    # LLM-as-a-Judge 评估（plan.md 4.3 D14）
    if body.get("judgeMode") in ("off", "advisory", "blocking"):
        patch["judgeMode"] = body["judgeMode"]
    if body.get("judgeProvider") in ("offline", "llm"):
        patch["judgeProvider"] = body["judgeProvider"]
    if isinstance(body.get("judgeModel"), str):
        patch["judgeModel"] = body["judgeModel"]
    for key in ("judgePassThreshold", "judgeWeight"):
        value = body.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            patch[key] = value

    # 站点监控（site_monitor 工具的待爬页面 URL，持久化到 data/settings.json）：
    # 逐项校验 http(s) 前缀（SSRF 的私网拦截仍由 core/web.py 抓取时兜底）、strip、去重、上限 20
    site_urls_changed = False
    raw_site_urls = body.get("siteUrls")
    if isinstance(raw_site_urls, list):
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in raw_site_urls:
            if not isinstance(item, str):
                continue
            url = item.strip()
            low = url.lower()
            if not low.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            cleaned.append(url)
            if len(cleaned) >= 20:
                break
        patch["siteUrls"] = cleaned
        site_urls_changed = True

    # 每智能体模型覆盖（持久化到 data/settings.json）：键大写归一并限定 A<数字>
    # 形态；apiKey 缺失 = 保留已存值、显式 "" = 清除（继承全局），仅收字符串字段
    agent_models_changed = False
    raw_agent_models = body.get("agentModels")
    if isinstance(raw_agent_models, dict):
        cleaned_models: dict[str, dict[str, str]] = {}
        for raw_id, raw_spec in raw_agent_models.items():
            if not isinstance(raw_spec, dict):
                continue
            aid = str(raw_id).strip().upper()
            if not (aid.startswith("A") and aid[1:].isdigit()):
                continue
            spec: dict[str, str] = {}
            for src, dst in (("model", "model"), ("baseUrl", "baseUrl"), ("apiKey", "apiKey")):
                if isinstance(raw_spec.get(src), str):
                    spec[dst] = raw_spec[src]
            cleaned_models[aid] = spec
        patch["agentModels"] = cleaned_models
        agent_models_changed = True

    update_config(patch)
    if site_urls_changed or agent_models_changed:
        save_persisted_settings()
    log.info(
        f"运行时配置已更新：provider={get_config().llm.provider} model={get_config().llm.model}"
    )
    return public_config()


# ------------------------------------------------------------------ #
# 任务                                                                #
# ------------------------------------------------------------------ #


@router.get("/tasks")
def list_tasks(request: Request) -> dict[str, Any]:
    return {"tasks": [task_summary(task) for task in _tenant_tasks(request)]}


@router.post("/tasks", status_code=201)
def create_task(
    request: Request, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
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
        brief,
        auto_approve=auto_approve if isinstance(auto_approve, bool) else False,
        tenant=_tenant_of(request),
        # W3C 跨进程传播：上游编排系统带来的 trace 上下文在服务端延续
        trace_parent=parse_traceparent(request.headers.get("traceparent")),
    )
    return {"task": task.model_dump(mode="json")}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict[str, Any]:
    _require_task(task_id, request)
    task = task_store.get(task_id)
    assert task is not None  # _require_task 已保证存在
    return {
        "task": task.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in event_bus.history(task_id)],
        "blackboard": blackboard.snapshot(task_id).model_dump(mode="json"),
    }


@router.get("/tasks/{task_id}/export")
def export_delivery(task_id: str, request: Request) -> FileResponse:
    """下载审批通过后的成品 txt（全部版本，连载类内容不丢篇）。

    只信任 final_delivery 产物里由交付环节写入的 ``export_path``，且解析后
    必须落在 ``EXPORTS_DIR`` 根目录下——不放开任意路径，避免越权读文件。
    """
    _require_task(task_id, request)
    artifact = blackboard.latest_artifact(task_id, "final_delivery")
    relative = ""
    if artifact is not None:
        relative = str((artifact.content or {}).get("export_path") or "").strip()
    if not relative:
        raise HTTPException(status_code=404, detail="任务尚无成品导出文件")
    path = (DATA_DIR / relative).resolve()
    if path.parent != EXPORTS_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="成品导出文件不存在")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, request: Request) -> dict[str, Any]:
    task = _require_task(task_id, request)
    if task.status in ("running", "awaiting_approval"):
        raise HTTPException(status_code=409, detail="任务仍在执行中，无法删除")

    task_store.delete(task_id)
    event_bus.drop(task_id)
    tracer.drop(task_id)
    # 顺带回收该任务的断点续跑检查点与数字人渲染作业，避免持久文件只增不减
    purged = orchestrator.purge_checkpoints(task_id)
    purged_jobs = drop_digital_human_jobs(task_id)
    blackboard.flush()
    return {
        "ok": True,
        "id": task_id,
        "purged_checkpoints": purged,
        "purged_digital_human_jobs": purged_jobs,
    }


@router.post("/tasks/{task_id}/decide")
def decide(
    task_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    _require_task(task_id, request)
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


@router.get("/tasks/{task_id}/publish")
def read_publish_plan(task_id: str, request: Request) -> dict[str, Any]:
    """读取任务的发布排期（审批通过后由编排层自动生成）。"""
    _require_task(task_id, request)
    try:
        return orchestrator.publish_schedule(task_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/tasks/{task_id}/publish")
def mark_task_published(
    task_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    """登记发布：把某个渠道（不传 ``channel`` 表示全部）标记为已发布。"""
    _require_task(task_id, request)
    body: dict[str, Any] = payload or {}
    try:
        return orchestrator.mark_published(
            task_id, str(body.get("channel") or ""), str(body.get("url") or "")
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/tasks/{task_id}/feedback")
def submit_feedback(
    task_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    """回填发布后的真实效果数据，触发 A10 复盘并形成 A/B 结论。

    请求体形如 ``{"channel": "小红书", "window": "发布后 72 小时",
    "metrics": {"exposure": 12000, "clicks": 480, "interactions": 260, "conversions": 24}}``。
    """
    _require_task(task_id, request)
    body: dict[str, Any] = payload or {}
    try:
        outcome = orchestrator.record_feedback(task_id, body)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    artifact = outcome["artifact"]
    return {
        "task": task_summary(outcome["task"]),
        "actuals": outcome["actuals"],
        "result": outcome["result"].model_dump(mode="json"),
        "artifact_id": artifact.id if artifact else None,
    }


@router.post("/tasks/{task_id}/publish/dispatch")
def dispatch_task_publish(
    task_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    """按排期把内容投递给发布 webhook（plan.md v2.0「自动发布」）。

    未配置 ``PUBLISH_WEBHOOK_URL`` 时退化为「登记发布」，因此离线也可用。
    请求体可带 ``{"channel": "小红书", "force": true}``；``force=false`` 时只投递已到期的条目。
    """
    _require_task(task_id, request)
    body: dict[str, Any] = payload or {}
    force = body.get("force")
    try:
        return orchestrator.dispatch_publish(
            task_id,
            str(body.get("channel") or ""),
            force=force if isinstance(force, bool) else True,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/publish/queue")
def read_publish_queue(request: Request, dueOnly: bool = Query(default=False)) -> dict[str, Any]:
    """跨任务的待发布队列，按到期时间升序；``dueOnly=true`` 只看已到期条目。"""
    return orchestrator.publish_queue(due_only=dueOnly, tenant=_tenant_of(request))


@router.post("/publish/tick")
def tick_publish(request: Request) -> dict[str, Any]:
    """手动驱动一次「到期自动投递」，供外部 cron / 定时器调用。"""
    return orchestrator.dispatch_due(tenant=_tenant_of(request))


# ------------------------------------------------------------------ #
# 数字人渲染（开发样例，plan.md v2.0「数字人」）                       #
# ------------------------------------------------------------------ #


@router.post("/tasks/{task_id}/digital-human", status_code=201)
def create_digital_human(
    task_id: str, request: Request, payload: dict[str, Any] | None = Body(default=None)
) -> dict[str, Any]:
    """为任务创建一个数字人渲染作业（**开发样例**）。

    任务必须已产出 ``video_script``。样例引擎（默认）离线模拟
    「排队 → 渲染 → 完成」并生成渲染清单；配置 ``DIGITAL_HUMAN_API_URL``
    后走 http 适配样例对接自建渲染网关。请求体可带
    ``{"avatar": "形象ID", "provider": "sample|http"}``。
    """
    task = _require_task(task_id, request)
    body: dict[str, Any] = payload or {}
    avatar = body.get("avatar")
    provider = body.get("provider")
    try:
        job = create_digital_human_job(
            task,
            avatar=avatar if isinstance(avatar, str) else "",
            provider=provider if isinstance(provider, str) else "",
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"task_id": task.id, "job": job}


@router.get("/tasks/{task_id}/digital-human")
def list_digital_human(task_id: str, request: Request) -> dict[str, Any]:
    """读取任务的数字人渲染作业列表（读取时惰性推进样例状态机）。"""
    task = _require_task(task_id, request)
    jobs = list_digital_human_jobs(task_id=task.id, tenant=task.tenant)
    return {
        "task_id": task.id,
        "has_video_script": any(a.type == "video_script" for a in task.artifacts),
        "jobs": jobs,
    }


@router.get("/tasks/{task_id}/blackboard")
def get_blackboard(task_id: str, request: Request) -> dict[str, Any]:
    _require_task(task_id, request)
    return blackboard.snapshot(task_id).model_dump(mode="json")


@router.get("/tasks/{task_id}/trace")
def get_trace(task_id: str, request: Request) -> dict[str, Any]:
    """任务的调用轨迹（span 树 + 按 span 名的耗时聚合）。

    span 树回答的是事件流回答不了的问题：这次任务的耗时与花费**具体分布在哪一层**。
    """
    _require_task(task_id, request)
    trace = tracer.trace(task_id)
    if trace is None:
        # 任务可能早于本功能创建，或已被删除；返回空骨架而不是 404，
        # 让前端可以统一渲染「暂无轨迹」而不用区分错误类型
        return {
            "task_id": task_id,
            "trace_id": None,
            "spans": [],
            "summary": {"span_count": 0, "duration_ms": 0, "by_name": []},
            "notes": "该任务没有轨迹记录（可能创建于追踪功能上线之前）",
        }

    payload = trace.to_dict()
    # 按 span 名聚合，供前端做「哪一层最贵」的排行
    by_name: dict[str, dict[str, Any]] = {}
    for span in trace.spans:
        row = by_name.setdefault(
            span.name,
            {"name": span.name, "kind": span.kind, "count": 0, "total_ms": 0, "max_ms": 0},
        )
        row["count"] += 1
        row["total_ms"] += span.duration_ms
        row["max_ms"] = max(row["max_ms"], span.duration_ms)
    rows = sorted(by_name.values(), key=lambda item: -item["total_ms"])
    for row in rows:
        row["avg_ms"] = round(row["total_ms"] / row["count"], 1) if row["count"] else 0
        row["share"] = (
            round(row["total_ms"] / trace.duration_ms, 4) if trace.duration_ms else 0.0
        )

    payload["summary"] = {
        "span_count": len(trace.spans),
        "duration_ms": trace.duration_ms,
        "by_name": rows,
    }
    payload["export"] = {
        "path": str(EXPORT_DIR),
        "format": "otel-shaped-json",
        "file": f"{task_id}.json",
    }
    return payload


# ------------------------------------------------------------------ #
# SSE 实时事件                                                        #
# ------------------------------------------------------------------ #


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    since: int = Query(default=0),
) -> StreamingResponse:
    _require_task(task_id, request)
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
def metrics(request: Request) -> dict[str, Any]:
    tasks = _tenant_tasks(request)
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
    cached_calls = sum(1 for result in all_results if result.metrics.cached)
    cut_off_tasks = sum(1 for task in tasks if task.tokens.cut_off)
    total_cost = sum(task.tokens.cost_usd for task in tasks)

    provider = resolve_provider()
    config = get_config()

    # 提供方清单：全局默认 + 每个智能体覆盖的**实际生效**值（按 name+model 去重），
    # agents 标注哪些智能体的覆盖指向这一行——否则面板只显示全局默认，
    # 「A4 其实跑在本地 Ollama 上」就从界面上消失了
    provider_rows: dict[tuple[str, str], dict[str, Any]] = {
        (provider.name, provider.model): {
            "name": provider.name,
            "model": provider.model,
            "simulated": getattr(provider, "simulated", True),
            "agents": [],
        }
    }
    for aid in sorted(config.llm.agent_models):
        eff = resolve_provider(llm_settings_for(aid))
        row = provider_rows.setdefault(
            (eff.name, eff.model),
            {
                "name": eff.name,
                "model": eff.model,
                "simulated": getattr(eff, "simulated", True),
                "agents": [],
            },
        )
        row["agents"].append(aid)

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
            # 成本相关指标（plan.md 2.4「单篇内容成本」「成本熔断」）
            "cost": {
                "total_cost_usd": round(total_cost, 6),
                "avg_cost_per_task_usd": (
                    round(total_cost / len(completed), 6) if completed else 0
                ),
                "budget_usd": config.cost_budget_usd,
                "token_budget": config.token_budget,
                "cut_off_tasks": cut_off_tasks,
                "cached_calls": cached_calls,
                **(cost_guard.metrics()),
            },
            # 缓存与租约：缓存命中率体现省钱效率，租约冲突体现黑板并发压力
            "cache": response_cache.stats(),
            "leases": {
                "active": len(blackboard.active_intents()),
                "conflicts": sum(task.intent_conflicts for task in tasks),
            },
            # LLM-as-a-Judge 评估（plan.md 4.3 D14）：平均分 / 通过率 / 维度均分
            "judge": {**evaluation_store.stats(tenant=_tenant_of(request)), "mode": config.judge.mode},
            # 追踪（plan.md 2.4）：按 span 名聚合的耗时排行，定位「哪一层最贵」
            "tracing": {**tracer.stats(tenant=_tenant_of(request)), "otlp": otel.stats()},
        },
        "providers": list(provider_rows.values()),
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
