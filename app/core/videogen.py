"""视频生成接入样例（开发样例，plan「视频生成通道」）。

定位（务必如实理解）
--------------------
视频生成**不在本系统内实现**——可灵 / 即梦 / Runway 等文生视频·图生视频服务，以及
本地 ComfyUI 视频工作流，其授权、计费与回调协议差异极大，硬编码任何一家都会变成
「绑死厂商的玩具」。与数字人渲染（``core/digital_human.py``）同一哲学：系统提供的
是**可回归的接入样例**，让联调在买任何服务之前就能发生。

本通道**只按 ``video_script`` 的分镜产出画面段（B-roll / 分镜画面）**，不做多镜
拼接合成——合成归视频工场 / 人工，与项目既有分工一致。

两条通道（``provider`` 可用请求体覆盖）：

* ``sample``（默认，零依赖）：离线确定性模拟「排队 → 生成 → 完成」，复用
  ``digital_human.build_manifest`` 把分镜翻译成「每镜该生成什么画面」的**渲染清单**
  （不产真片，保证 API/UI/回归可联调）；
* ``http``：对接「POST 建任务 → GET 查状态」这一最小契约的任意网关（云端可灵/即梦/
  Runway 经网关转换、或自建本地 ComfyUI 视频渲染农场均可）。本地私网端点按
  ``_is_local_endpoint`` 计 0 元，与 LLM 引擎同一成本口径。

未配置真实端点时**显式失败，绝不假装成功**。视频生成昂贵，受理时按渲染清单估算
成本并纳入 ``cost_budget_usd`` 预算：超预算即 **fail-loud**（作业直接置失败并说明
原因），不静默降级成「假装已生成」。生命周期与数字人一致：``sample`` 按流逝时间惰性
推进，``http`` 受理时提交、读取时轮询远端状态。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import DATA_DIR, get_config
from ..llm.engine import _is_local_endpoint
from ..logger import create_logger
from .clock import now_iso
from .digital_human import build_manifest
from .events import event_bus, new_id
from .gen_jobs import FileJobBackend, PgJobBackend, make_backend
from .tracing import tracer
from .types import TaskRecord

log = create_logger("videogen")

#: 作业状态机：queued → generating → done / failed
STATUSES = ("queued", "generating", "done", "failed")

#: 作业存储（file 模式 JSON；pg 模式 videogen_jobs 表，见 storage_contract.md）
STORE_FILE = DATA_DIR / "videogen_jobs.json"
_TABLE = "videogen_jobs"

#: http 适配样例的两次远端轮询最小间隔（秒）：避免列表刷新打爆远端
_HTTP_POLL_INTERVAL = 2.0

#: 每镜的保守成本估算（美元），仅用于预算熔断的**事前**判断——真实计费归网关侧。
#: 与 ``pricing.DEFAULT_PRICE`` 同为「拍脑袋的保守值」，宁可高估触发熔断，不可低估漏算。
_EST_COST_PER_SHOT_USD = 0.1

#: 远端状态 → 作业状态映射（与 digital_human 同一契约口径）
_STATUS_MAP = {
    "queued": "queued",
    "pending": "queued",
    "processing": "generating",
    "generating": "generating",
    "running": "generating",
    "done": "done",
    "completed": "done",
    "succeeded": "done",
    "finished": "done",
    "failed": "failed",
    "error": "failed",
}

_BACKEND: "FileJobBackend | PgJobBackend | None" = None
_BACKEND_LOCK = threading.RLock()


def _job_backend() -> "FileJobBackend | PgJobBackend":
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = make_backend(STORE_FILE, _TABLE)
    return _BACKEND


# ------------------------------------------------------------------ #
# 清单与成本                                                           #
# ------------------------------------------------------------------ #


def _estimate_cost(manifest: dict[str, Any], provider: str) -> float:
    """按渲染清单的镜数保守估算成本；本地端点与 sample 归 0（无云单价依据）。

    以**实际生效的 provider**（可能被请求体覆盖）为准，而非全局配置：
    否则用 http 出片却因全局是 sample 而漏算成本、绕过熔断。
    """
    cfg = get_config().video_gen
    if provider == "sample" or not cfg.api_url or _is_local_endpoint(cfg.api_url):
        return 0.0
    shots = int(manifest.get("shot_count") or 0)
    return round(shots * _EST_COST_PER_SHOT_USD, 6)


# ------------------------------------------------------------------ #
# 样例引擎（sample）：按流逝时间惰性推进                               #
# ------------------------------------------------------------------ #


def _sample_seconds(manifest: dict[str, Any]) -> int:
    """样例生成时长：随分镜数变化但封顶，让生命周期可见又不拖慢联调。"""
    return min(30, max(6, 3 + int(manifest.get("shot_count") or 1) * 2))


def _advance_sample(job: dict[str, Any], now: datetime) -> None:
    started = _parse_iso(str(job.get("started_at") or ""))
    if started is None:
        return
    elapsed = (now - started).total_seconds()
    seconds = int(job.get("render_seconds") or _sample_seconds(job.get("manifest") or {}))
    if elapsed < 1.0:
        _transition(job, "queued", "样例引擎已受理（排队中）")
        job["progress"] = 0
    elif elapsed < seconds:
        _transition(job, "generating", f"样例生成进行中（{seconds}s 总时长）")
        job["progress"] = min(99, int(elapsed / seconds * 100))
    else:
        _transition(job, "done", "样例生成完成")
        job["progress"] = 100
        job["finished_at"] = now_iso()
        job["segments_out"] = [
            {
                "shot": seg["shot"],
                "role": seg.get("role") or "",
                "duration_seconds": seg.get("duration_seconds") or 0,
                "url": f"sample://videogen/{job['id']}/shot-{seg['shot']}.mp4",
            }
            for seg in job.get("manifest", {}).get("segments", [])
        ]


# ------------------------------------------------------------------ #
# http 适配样例：POST 建任务 → GET 查状态                             #
# ------------------------------------------------------------------ #


def _auth_headers() -> dict[str, str]:
    cfg = get_config().video_gen
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    traceparent = tracer.current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def _submit_http(job: dict[str, Any]) -> None:
    """向远端网关建任务（样例契约：POST → 2xx + ``{"job_id": "..."}``）。"""
    cfg = get_config().video_gen
    if not cfg.api_url:
        _transition(job, "failed", "未配置 VIDEOGEN_API_URL，无法提交视频生成任务")
        return
    manifest = job.get("manifest") or {}
    try:
        response = httpx.post(
            cfg.api_url,
            json={
                "job_id": job["id"],
                "task_id": job["task_id"],
                "channel": manifest.get("channel") or "",
                "aspect_ratio": manifest.get("aspect_ratio") or "",
                "duration_seconds": manifest.get("duration_seconds") or 0,
                "segments": manifest.get("segments") or [],
            },
            headers=_auth_headers(),
            timeout=max(1.0, cfg.timeout_ms / 1000.0),
        )
    except Exception as error:  # noqa: BLE001 - 网络异常转成任务失败，不向上抛
        job["attempts"] = int(job.get("attempts") or 0) + 1
        _transition(job, "failed", f"提交失败：{type(error).__name__}: {error}")
        return
    job["attempts"] = int(job.get("attempts") or 0) + 1
    if response.status_code >= 400:
        _transition(job, "failed", f"提交被拒绝：HTTP {response.status_code} {(response.text or '')[:160]}")
        return
    remote = ""
    try:
        body = response.json()
        remote = str(body.get("job_id") or body.get("id") or "")
    except ValueError:
        pass
    job["remote_id"] = remote
    _transition(job, "queued", f"远端已受理（job_id={remote or '未知'}）")


def _poll_http(job: dict[str, Any], now: datetime) -> None:
    """查询远端状态（样例契约：GET → ``{"status": "...", "segments": [...]}``）。"""
    cfg = get_config().video_gen
    last_poll = _parse_iso(str(job.get("last_poll_at") or ""))
    if last_poll is not None and (now - last_poll).total_seconds() < _HTTP_POLL_INTERVAL:
        return
    job["last_poll_at"] = now_iso()
    remote_id = str(job.get("remote_id") or job["id"])
    try:
        response = httpx.get(
            f"{cfg.api_url.rstrip('/')}/{remote_id}",
            headers=_auth_headers(),
            timeout=max(1.0, cfg.timeout_ms / 1000.0),
        )
    except Exception as error:  # noqa: BLE001
        job["error"] = f"轮询失败：{type(error).__name__}: {error}"
        return
    if response.status_code >= 400:
        job["error"] = f"轮询失败：HTTP {response.status_code}"
        return
    try:
        body = response.json()
    except ValueError:
        job["error"] = "轮询响应不是 JSON"
        return
    status = _STATUS_MAP.get(str(body.get("status") or "").strip().lower(), "")
    if status == "done":
        _transition(job, "done", "远端生成完成")
        job["progress"] = 100
        job["finished_at"] = now_iso()
        job["segments_out"] = [
            {
                "shot": int(seg.get("shot") or index + 1),
                "role": str(seg.get("role") or ""),
                "duration_seconds": seg.get("duration_seconds") or 0,
                "url": str(seg.get("url") or seg.get("video_url") or ""),
            }
            for index, seg in enumerate(body.get("segments") or [] if isinstance(body, dict) else [])
            if isinstance(seg, dict)
        ]
        if not job["segments_out"]:
            single = str(body.get("video_url") or body.get("url") or "")
            if single:
                job["segments_out"] = [{"shot": 0, "role": "full", "duration_seconds": 0, "url": single}]
    elif status == "failed":
        _transition(job, "failed", str(body.get("error") or body.get("message") or "远端生成失败"))
    elif status in ("queued", "generating"):
        _transition(job, status, "远端生成中")
        job["progress"] = max(int(job.get("progress") or 0), 10)


def _advance_http(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") == "queued" and not job.get("remote_id") and not job.get("submitted"):
        job["submitted"] = True
        _submit_http(job)
        return
    if job.get("status") in ("queued", "generating"):
        _poll_http(job, now)


# ------------------------------------------------------------------ #
# 状态推进与公共 API                                                   #
# ------------------------------------------------------------------ #


def _parse_iso(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _transition(job: dict[str, Any], status: str, note: str) -> None:
    if job.get("status") == status:
        job["error"] = note if status == "failed" else job.get("error", "")
        return
    previous = str(job.get("status") or "queued")
    job["status"] = status
    job["error"] = note if status == "failed" else ""
    job["history"] = list(job.get("history") or []) + [
        {"ts": now_iso(), "from": previous, "to": status, "note": note[:200]}
    ]
    job["updated_at"] = now_iso()
    event_bus.publish(
        task_id=str(job.get("task_id") or ""),
        type="log",
        message=f"视频生成：{note}",
        level="warn" if status == "failed" else "info",
        payload={"job_id": job.get("id"), "status": status, "provider": job.get("provider")},
    )


def _latest_video_script(task: TaskRecord) -> dict[str, Any] | None:
    artifact = next((a for a in reversed(task.artifacts) if a.type == "video_script"), None)
    return artifact.content if artifact is not None else None


def create_job(
    task: TaskRecord, *, provider: str = "", now: datetime | None = None
) -> dict[str, Any]:
    """为任务创建一个视频生成作业（``POST /api/tasks/{id}/videos``）。

    任务必须已产出 ``video_script``；否则抛 ``ValueError`` 由路由层转 409（与数字人一致）。
    """
    script = _latest_video_script(task)
    if script is None:
        raise ValueError("该任务没有视频脚本产物（video_script），视频生成没有可执行的分镜（仅短视频形态会产出脚本）")

    cfg = get_config().video_gen
    chosen = provider if provider in ("sample", "http") else cfg.provider
    manifest = build_manifest(script)
    moment = now or datetime.now(timezone.utc)
    estimated_cost = _estimate_cost(manifest, chosen)
    job_id = new_id("vid")
    job: dict[str, Any] = {
        "id": job_id,
        "task_id": task.id,
        "tenant": task.tenant,
        "provider": chosen,
        "status": "queued",
        "progress": 0,
        "script_artifact_id": next(
            (a.id for a in reversed(task.artifacts) if a.type == "video_script"), ""
        ),
        "channel": str(manifest.get("channel") or task.brief.channel),
        "aspect_ratio": str(manifest.get("aspect_ratio") or ""),
        "duration_seconds": manifest.get("duration_seconds"),
        "shot_count": manifest.get("shot_count"),
        "render_seconds": _sample_seconds(manifest),
        "estimated_cost_usd": estimated_cost,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": now_iso(),
        "finished_at": "",
        "segments_out": [],
        "error": "",
        "attempts": 0,
        "remote_id": "",
        "submitted": False,
        "last_poll_at": "",
        "manifest": manifest,
        "history": [{"ts": now_iso(), "from": "", "to": "queued", "note": "视频生成任务已受理"}],
    }

    # 成本熔断：受理前先按清单估算，超预算直接 fail-loud，绝不提交、绝不假装
    budget = get_config().cost_budget_usd
    if chosen == "http" and budget > 0 and estimated_cost >= budget:
        _transition(
            job,
            "failed",
            f"成本熔断：预估 ${estimated_cost:.3f} 已达预算 ${budget:.2f}，"
            f"未提交生成（可分镜拆单或调高 COST_BUDGET_USD）",
        )
        _job_backend().create(job)
        return dict(job)

    _job_backend().create(job)

    with tracer.span_on_task(
        task.id,
        "videogen.render",
        kind="client",
        agent_id="A8",
        attributes={
            "videogen.job_id": job_id,
            "videogen.provider": chosen,
            "videogen.shots": manifest.get("shot_count") or 0,
            "videogen.estimated_cost_usd": estimated_cost,
        },
    ):
        event_bus.publish(
            task_id=task.id,
            type="log",
            message=f"视频生成任务已创建（{chosen}，{manifest.get('shot_count')} 镜）",
            agent_id="A8",
            payload={"job_id": job_id, "estimated_cost_usd": estimated_cost},
        )
        if chosen == "http":
            job["attempts"] = int(job.get("attempts") or 0) + 1
            _advance_http(job, moment)
            _job_backend().write_back([job])
    log.info(f"视频生成任务已创建：{job_id}（task={task.id}, provider={chosen}）")
    return dict(job)


def _refresh(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") in ("done", "failed"):
        return
    if job.get("provider") == "http":
        with tracer.span_on_task(str(job.get("task_id")), "videogen.poll", kind="client"):
            _advance_http(job, now)
    else:
        _advance_sample(job, now)


def list_jobs(
    task_id: str | None = None, *, tenant: str | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """列出作业（读取时惰性推进状态），新→旧排序。"""
    backend = _job_backend()
    moment = now or datetime.now(timezone.utc)
    snapshot = [dict(job) for job in backend.snapshot()]
    changed: list[dict[str, Any]] = []
    for job in snapshot:
        before = str(job.get("status"))
        _refresh(job, moment)
        if str(job.get("status")) != before:
            changed.append(job)
    if changed:
        backend.write_back(changed)
    rows = [
        job
        for job in snapshot
        if (task_id is None or str(job.get("task_id")) == task_id)
        and (tenant is None or str(job.get("tenant")) == tenant)
    ]
    rows.sort(key=lambda job: job.get("created_at") or "", reverse=True)
    return rows


def get_job(job_id: str, *, tenant: str | None = None) -> dict[str, Any] | None:
    return next((row for row in list_jobs(tenant=tenant) if row.get("id") == job_id), None)


def drop_task(task_id: str) -> int:
    """任务删除时回收其视频生成作业。"""
    return _job_backend().drop_task(task_id)


__all__ = [
    "STATUSES",
    "STORE_FILE",
    "create_job",
    "drop_task",
    "get_job",
    "list_jobs",
]
