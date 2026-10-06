"""视频理解接入样例（开发样例，plan「视频理解通道」）。

定位（务必如实理解）
--------------------
「看懂画面」的推理**不在本系统内实现**——没有哪个进程能凭空调和视觉大模型。本通道
是 [`digest.py`](file:///e:/tool/mytool/creator/app/core/digest.py)（文档版「多模态理解 +
文本创作」方案一）的**画面版镜像**：为 B 站解说号补上「摄取侧」能力——把一集番剧/影视
的画面**看懂**，产出结构化**视觉摘要**，再一键注入 Brief 驱动下游文本创作。

流程（方案一落地）：把**整集视频**交给**原生支持视频输入的理解网关**——**一集=一次调用**，
成本可控，最贴「逐集看完再据内容创作」的节奏。与视频生成（``core/videogen.py``）同一
「POST 建任务 → GET 查状态」最小契约范式：受理时提交整集、读取时惰性轮询远端状态、完成
时取回结构化摘要。**不绑厂商**，厂商私有协议（Gemini 视频输入 / 自建视频理解农场等）由
你的网关层消化，系统只提供可回归的接入样例。

两条通道（``provider`` 可用请求体覆盖）：

* ``sample``（默认，零依赖）：离线确定性推进「受理 → 理解 → 完成」生命周期，产出
  **带 ``simulated`` 标记的占位视觉摘要骨架**（不调网关，保证 API/UI/回归可联调）；
* ``real``：按 ``VIDEOUNDERSTAND_API_URL`` 提交整集视频、轮询取回摘要。**任一前置不满足
  即显式失败，绝不假装看懂**：未配网关 / 视频不是 ``assets/`` 下本地文件 / 文件不存在 / 成本超预算。

诚实边界
--------
* 理解走网关是**真金白银**：受理前按「一集一次调用」保守估算并纳入 ``cost_budget_usd``，
  超预算 **fail-loud**；本地/归零端点计 0 元（同 LLM 引擎口径）。
* 系统**不搬运大文件**：只把 ``assets/`` 内的视频引用交给网关自取（网关需与素材同机/可达），
  沿用「只认 assets/ 本地、不下载公网/内联视频」的安全边界——拒绝对路径与 ``..`` 越界。

接入形态
--------
**旁路异步作业通道**（job store，镜像 videogen），产物**不落黑板**：这样既避开「新增
Artifact 类型要改四处契约」（MEMORY #31），又让「理解→创作」的衔接由一次显式的
「注入 Brief」动作完成（把 ``summary.text_brief`` 追加进 ``brief.constraints``）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import ASSETS_DIR, DATA_DIR, get_config
from ..llm.engine import _is_local_endpoint
from ..llm.json_utils import as_str, as_str_array
from ..logger import create_logger
from .assets import parse_assets
from .clock import now_iso
from .events import event_bus, new_id
from .gen_jobs import FileJobBackend, PgJobBackend, make_backend
from .tracing import tracer
from .types import TaskRecord

log = create_logger("videounderstand")

#: 作业状态机：queued（受理/排队）→ understanding（网关理解中）→ done / failed
STATUSES = ("queued", "understanding", "done", "failed")

#: 作业存储（file 模式 JSON；pg 模式 videounderstand_jobs 表，见 storage_contract.md）
STORE_FILE = DATA_DIR / "videounderstand_jobs.json"
_TABLE = "videounderstand_jobs"

#: 视频扩展名白名单：parse_asset 对 >8MB 本地视频会提前返回、kind 仍默认 image，
#: 故 real/sample 的视频识别一律按扩展名兜底，不依赖被体积闸门截断后的 kind 字段。
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv")

#: real 通道两次远端轮询的最小间隔（秒）：避免列表刷新打爆网关
_HTTP_POLL_INTERVAL = 2.0

#: 每集（=一次调用）的保守成本估算（美元），仅用于预算熔断的**事前**判断——真实计费归网关侧。
#: 与 ``pricing.DEFAULT_PRICE`` 同为「拍脑袋的保守值」，宁可高估触发熔断，不可低估漏算。
_EST_COST_PER_EPISODE_USD = 0.05

#: 远端状态 → 作业状态映射（与 videogen/digital_human 同一契约口径）
_STATUS_MAP = {
    "queued": "queued",
    "pending": "queued",
    "submitted": "queued",
    "processing": "understanding",
    "understanding": "understanding",
    "running": "understanding",
    "analyzing": "understanding",
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
# 视频素材识别与本地路径解析                                            #
# ------------------------------------------------------------------ #


def _iter_video_assets(task: TaskRecord) -> list[Any]:
    """列出 Brief 里被**声明为视频**的素材（按 kind 或扩展名），不校验文件是否存在。

    sample 通道只需要「有一条视频素材」即可产出占位骨架；real 通道再单独解析本地路径。
    """
    found: list[Any] = []
    for asset in parse_assets(task.brief.assets):
        ref = asset.ref or ""
        path_part = (urlparse(ref).path or ref).lower()
        by_ext = any(path_part.endswith(ext) for ext in VIDEO_EXTS)
        if asset.kind == "video" or by_ext:
            found.append(asset)
    return found


def has_video_asset(task: TaskRecord) -> bool:
    """路由层读取列表时用来给前端提示「该任务是否带了视频素材」。"""
    return bool(_iter_video_assets(task))


def _resolve_video_path(ref: str) -> tuple[Path | None, str]:
    """把 ``assets/`` 下的相对视频路径解析成真实文件路径（不读字节、不套 8MB 上限）。

    安全不变量与 ``assets.load_local`` **完全一致**（只认相对路径、拒绝绝对路径与 ``..``
    越界、必须落在 assets/ 目录内），差别只在**不读字节、不限体积**——系统只把这条引用交给
    网关自取，不搬运整集视频（那 8MB 内联上限是给进模型的字节用的，整集番剧远超此值）。
    """
    parts = [seg for seg in ref.replace("\\", "/").split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        return None, "real 通道只理解 assets/ 目录内的相对视频文件（拒绝绝对路径与 .. 越界）"
    try:
        root = ASSETS_DIR.resolve()
        path = ASSETS_DIR.joinpath(*parts).resolve()
        if not path.is_relative_to(root):
            return None, "素材路径越出 assets/ 目录"
    except OSError:
        return None, "素材路径无法解析"
    if not path.is_file():
        return None, f"assets/ 目录下没有视频文件「{ref}」"
    return path, ""


# ------------------------------------------------------------------ #
# 成本估算（一集=一次调用）                                            #
# ------------------------------------------------------------------ #


def _estimate_cost(provider: str) -> float:
    """按「一集一次调用」保守估算理解成本；sample 与本地/归零网关端点归 0（无云单价依据）。

    以**实际生效的 provider**（可能被请求体覆盖）为准，而非全局配置，避免漏算绕过熔断。
    """
    if provider == "sample":
        return 0.0
    cfg = get_config().video_understand
    if not cfg.api_url or _is_local_endpoint(cfg.api_url):
        return 0.0
    return round(_EST_COST_PER_EPISODE_USD, 6)


# ------------------------------------------------------------------ #
# real 通道：POST 提交整集 → GET 惰性轮询取回摘要                      #
# ------------------------------------------------------------------ #


def _auth_headers() -> dict[str, str]:
    cfg = get_config().video_understand
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    traceparent = tracer.current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def _submit_http(job: dict[str, Any]) -> None:
    """向远端网关提交整集视频理解任务（样例契约：POST → 2xx + ``{"job_id": "..."}``）。

    前置任一不满足即显式失败、绝不假装看懂：未配网关 / 视频非 assets/ 本地文件 / 文件不存在。
    """
    cfg = get_config().video_understand
    if not cfg.api_url:
        _transition(job, "failed", "未配置 VIDEOUNDERSTAND_API_URL，无法提交视频理解任务")
        return
    if str(job.get("video_source") or "") != "local":
        _transition(job, "failed", "real 通道只理解 assets/ 下的本地视频文件（公网/内联视频不提交）")
        return
    video_path, path_issue = _resolve_video_path(str(job.get("video_ref") or ""))
    if video_path is None:
        _transition(job, "failed", path_issue)
        return
    try:
        response = httpx.post(
            cfg.api_url,
            json={
                "job_id": job["id"],
                "task_id": job["task_id"],
                "tenant": job.get("tenant") or "",
                "channel": job.get("channel") or "",
                "video_ref": str(job.get("video_ref") or ""),
                "video_path": str(video_path),
                "video_title": str(job.get("video_title") or ""),
                "mode": "whole_video",
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
        _transition(
            job, "failed", f"提交被拒绝：HTTP {response.status_code} {(response.text or '')[:160]}"
        )
        return
    remote = ""
    try:
        body = response.json()
        remote = str(body.get("job_id") or body.get("id") or "")
    except ValueError:
        pass
    job["remote_id"] = remote
    _transition(job, "queued", f"网关已受理整集视频（job_id={remote or '未知'}）")


def _finalize_summary(raw: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """把网关返回的摘要收敛成稳定结构（缺字段用空值兜底，绝不假装非空）。"""
    issues = raw.get("issues")
    issue_list = [str(item) for item in issues] if isinstance(issues, list) else []
    return {
        "theme": as_str(raw.get("theme")),
        "logline": as_str(raw.get("logline")),
        "scenes": as_str_array(raw.get("scenes")),
        "characters": as_str_array(raw.get("characters")),
        "actions": as_str_array(raw.get("actions")),
        "mood": as_str(raw.get("mood")),
        "camera_language": as_str(raw.get("camera_language")),
        "pacing": as_str(raw.get("pacing")),
        "notable_moments": as_str_array(raw.get("notable_moments")),
        "text_brief": as_str(raw.get("text_brief")),
        "simulated": False,
        "sources": [str(job.get("video_ref") or "")],
        "issues": issue_list,
        "stats": {
            "episode_calls": 1,
            "cost_usd": float(job.get("estimated_cost_usd") or 0),
        },
    }


def _poll_http(job: dict[str, Any], now: datetime) -> None:
    """查询远端状态（样例契约：GET → ``{"status": "...", "summary": {...}}``）。"""
    cfg = get_config().video_understand
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
    body = body if isinstance(body, dict) else {}
    status = _STATUS_MAP.get(str(body.get("status") or "").strip().lower(), "")
    if status == "done":
        raw = body.get("summary")
        raw = raw if isinstance(raw, dict) else body
        summary = _finalize_summary(raw, job)
        if not summary["text_brief"] and not summary["scenes"]:
            job["issues"] = list(job.get("issues") or []) + ["网关标记完成但未返回可解析的视觉摘要"]
        job["summary"] = summary
        _transition(job, "done", "网关已产出视觉摘要（整集一次理解）")
        job["progress"] = 100
        job["finished_at"] = now_iso()
    elif status == "failed":
        _transition(
            job, "failed", str(body.get("error") or body.get("message") or "网关理解失败")
        )
    elif status in ("queued", "understanding"):
        _transition(job, status, "网关理解整集视频中")
        job["progress"] = max(int(job.get("progress") or 0), 10)


def _advance_real(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") == "queued" and not job.get("remote_id") and not job.get("submitted"):
        job["submitted"] = True
        _submit_http(job)
        return
    if job.get("status") in ("queued", "understanding"):
        _poll_http(job, now)


# ------------------------------------------------------------------ #
# sample 通道：按流逝时间惰性推进，产出带 simulated 标记的占位骨架        #
# ------------------------------------------------------------------ #


def _sample_summary(job: dict[str, Any]) -> dict[str, Any]:
    title = str(job.get("video_title") or job.get("video_ref") or "未命名视频")
    return {
        "theme": f"《{title}》样例视觉摘要（结构占位）",
        "logline": "样例通道不调用视频理解网关，仅演示「提交整集 → 轮询 → 取回摘要」的产物结构。",
        "scenes": [],
        "characters": [],
        "actions": [],
        "mood": "",
        "camera_language": "",
        "pacing": "",
        "notable_moments": [],
        "text_brief": (
            f"（样例占位，非真实理解）《{title}》：配置 VIDEOUNDERSTAND_API_URL 指向原生视频"
            f"理解网关并选择 real 通道后，此字段将是该集基于画面的可视化摘要，供解说创作引用。"
        ),
        "simulated": True,
        "sources": [str(job.get("video_ref") or "")],
        "issues": ["sample 通道未调用视频理解网关，摘要为占位骨架"],
        "stats": {"episode_calls": 0, "cost_usd": 0},
    }


def _advance_sample(job: dict[str, Any], now: datetime) -> None:
    started = _parse_iso(str(job.get("started_at") or ""))
    if started is None:
        return
    elapsed = (now - started).total_seconds()
    seconds = int(job.get("sample_seconds") or 8)
    if elapsed < 1.0:
        _transition(job, "queued", "样例引擎已受理（排队中）")
        job["progress"] = 0
    elif elapsed < seconds:
        _transition(job, "understanding", "样例理解整集视频中（模拟网关一次调用）")
        job["progress"] = int(min(1.0, elapsed / max(1e-6, seconds)) * 99)
    else:
        _transition(job, "done", "样例视觉摘要已生成（占位骨架，非真实理解）")
        job["progress"] = 100
        job["finished_at"] = now_iso()
        job["summary"] = _sample_summary(job)


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
        message=f"视频理解：{note}",
        level="warn" if status == "failed" else "info",
        payload={"job_id": job.get("id"), "status": status, "provider": job.get("provider")},
    )


def create_job(
    task: TaskRecord, *, provider: str = "", now: datetime | None = None
) -> dict[str, Any]:
    """为任务创建一个视频理解作业（``POST /api/tasks/{id}/video-understanding``）。

    任务必须在 Brief.assets 里带一条视频素材（``kind=video`` 或带视频扩展名的本地文件）；
    否则抛 ``ValueError`` 由路由层转 409（与图片/视频生成一致）。
    """
    videos = _iter_video_assets(task)
    if not videos:
        raise ValueError(
            "该任务没有视频素材（Brief.assets 里需要一条 kind=video 或带视频扩展名的文件），"
            "无从「看懂」"
        )
    cfg = get_config().video_understand
    chosen = provider if provider in ("sample", "real") else cfg.provider
    asset = videos[0]
    moment = now or datetime.now(timezone.utc)
    estimated_cost = _estimate_cost(chosen)
    job_id = new_id("vu")
    job: dict[str, Any] = {
        "id": job_id,
        "task_id": task.id,
        "tenant": task.tenant,
        "provider": chosen,
        "status": "queued",
        "progress": 0,
        "video_ref": asset.ref,
        "video_title": asset.title or asset.ref,
        "video_source": asset.source,
        "channel": task.brief.channel,
        "sample_seconds": 8,
        "estimated_cost_usd": estimated_cost,
        "summary": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": now_iso(),
        "finished_at": "",
        "error": "",
        "attempts": 0,
        "remote_id": "",
        "submitted": False,
        "last_poll_at": "",
        "issues": [],
        "history": [{"ts": now_iso(), "from": "", "to": "queued", "note": "视频理解作业已受理"}],
    }

    # 成本熔断：受理前先按「一集一次调用」估算，超预算直接 fail-loud，绝不提交、绝不假装
    budget = get_config().cost_budget_usd
    if chosen == "real" and budget > 0 and estimated_cost >= budget:
        _transition(
            job,
            "failed",
            f"成本熔断：按整集一次调用预估 ${estimated_cost:.3f} 已达预算 ${budget:.2f}，"
            f"未提交理解（可下调预估口径或调高 COST_BUDGET_USD）",
        )
        _job_backend().create(job)
        return dict(job)

    _job_backend().create(job)

    with tracer.span_on_task(
        task.id,
        "videounderstand.understand",
        kind="client",
        agent_id="A8",
        attributes={
            "videounderstand.job_id": job_id,
            "videounderstand.provider": chosen,
            "videounderstand.estimated_cost_usd": estimated_cost,
        },
    ):
        event_bus.publish(
            task_id=task.id,
            type="log",
            message=f"视频理解作业已创建（{chosen}，整集《{job['video_title']}》）",
            agent_id="A8",
            payload={"job_id": job_id, "estimated_cost_usd": estimated_cost},
        )
        if chosen == "real":
            job["attempts"] = int(job.get("attempts") or 0) + 1
            _advance_real(job, moment)
            _job_backend().write_back([job])
    log.info(f"视频理解作业已创建：{job_id}（task={task.id}, provider={chosen}, status={job['status']}）")
    return dict(job)


def _refresh(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") in ("done", "failed"):
        return
    if job.get("provider") == "real":
        with tracer.span_on_task(str(job.get("task_id")), "videounderstand.poll", kind="client"):
            _advance_real(job, now)
    else:
        _advance_sample(job, now)


def list_jobs(
    task_id: str | None = None, *, tenant: str | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """列出作业（读取时惰性推进状态机），新→旧排序。"""
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
    """任务删除时回收其视频理解作业记录。"""
    return _job_backend().drop_task(task_id)


__all__ = [
    "STATUSES",
    "STORE_FILE",
    "create_job",
    "drop_task",
    "get_job",
    "has_video_asset",
    "list_jobs",
]
