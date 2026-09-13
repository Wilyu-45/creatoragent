"""数字人视频生成开发样例（plan.md v2.0「数字人」）。

定位（务必如实理解）
--------------------
数字人渲染**不在本系统内实现**——HeyGen / D-ID / 腾讯智影 / 阿里云虚拟数字人等
服务的授权方式、形象库、计费与回调协议差异极大，硬编码任何一家都会变成
「绑死厂商的玩具」。这里提供的是**接入样例**，最终接哪家、要不要接，由使用者决定：

1. **``sample`` 内置样例引擎（默认，零依赖）**：离线确定性模拟
   「排队 → 渲染 → 完成」的完整生命周期，并按 ``video_script`` 产物生成
   **渲染清单**（每镜的台词 / 字幕 / 机位 / 起止时间 / 是否有口播）——
   在购买任何第三方服务之前，就能联调 API、UI 与下游流程；
2. **``http`` 通用适配样例**：对接「POST 建任务 → GET 查状态」这一最小契约的
   任意网关（自建渲染农场、n8n / Zapier 编排的服务均可），配置
   ``DIGITAL_HUMAN_API_URL`` 即启用。各厂商私有协议差异由使用者在自己的
   网关层消化，而不是改本系统。

与发布投递（``core/publisher.py``）同一原则：样例不假装是正式集成；
未配置真实服务时系统行为完整、可预期、可回归。

生命周期是**惰性推进**的：不靠后台线程，状态在读取时按流逝时间 / 远端状态计算，
样例引擎由此获得「零副作用」——服务空转时不产生任何任务。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import DATA_DIR, get_config
from ..logger import create_logger
from .clock import now_iso
from .events import event_bus, new_id
from .tracing import tracer
from .types import TaskRecord

log = create_logger("digital_human")

#: 渲染任务状态机：queued → rendering → done / failed
STATUSES = ("queued", "rendering", "done", "failed")

#: 任务存储文件（与 evaluations.json 同级的独立旁路数据，不是黑板产物）
STORE_FILE = DATA_DIR / "digital_human.json"

#: http 适配样例的两次远端轮询最小间隔（秒）：避免列表刷新打爆远端
_HTTP_POLL_INTERVAL = 2.0

_STORE_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] | None = None
_DIRTY = False


# ------------------------------------------------------------------ #
# 存储：懒加载 + 原子写盘                                             #
# ------------------------------------------------------------------ #


def _load_locked() -> dict[str, dict[str, Any]]:
    global _JOBS, _DIRTY
    if _JOBS is None:
        _JOBS = {}
        try:
            raw = json.loads(STORE_FILE.read_text(encoding="utf-8"))
            for job in raw.get("jobs") or []:
                if isinstance(job, dict) and job.get("id"):
                    _JOBS[str(job["id"])] = job
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            log.warn(f"数字人任务存储读取失败（按空库继续）：{error}")
        _DIRTY = False
    return _JOBS


def _save_locked() -> None:
    global _DIRTY
    if not _DIRTY:
        return
    payload = {"jobs": sorted(_JOBS.values(), key=lambda job: job.get("created_at") or "")}
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_FILE)
    _DIRTY = False


def _mark_dirty() -> None:
    global _DIRTY
    _DIRTY = True


# ------------------------------------------------------------------ #
# 渲染清单：把 video_script 产物翻译成「数字人该演什么」               #
# ------------------------------------------------------------------ #


def _num(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return fallback


def build_manifest(script_content: dict[str, Any]) -> dict[str, Any]:
    """把 ``video_script`` 的内容字典转换成数字人渲染清单。

    分镜时间轴与台词原样透传；数字人只「演」有口播的镜头，无口播分镜会
    生成显式告警（与界面上「存在无口播分镜」的口径一致），而不是静默跳过。
    """
    shots = [item for item in script_content.get("shots") or [] if isinstance(item, dict)]
    segments: list[dict[str, Any]] = []
    warnings: list[str] = []
    covered = 0.0
    for shot in shots:
        voiceover = str(shot.get("voiceover") or "").strip()
        end = _num(shot.get("end_second"), _num(shot.get("start_second")) + _num(shot.get("duration_seconds")))
        start = _num(shot.get("start_second"))
        if not voiceover:
            warnings.append(
                f"第 {shot.get('shot')} 镜（{shot.get('role')}）没有口播台词，数字人仅作画面演出"
            )
        covered += max(0.0, end - start)
        segments.append(
            {
                "shot": int(_num(shot.get("shot"), len(segments) + 1)),
                "role": str(shot.get("role") or ""),
                "start_second": round(start, 2),
                "end_second": round(end, 2),
                "duration_seconds": round(max(0.0, end - start), 2),
                "voiceover": voiceover,
                "subtitle": str(shot.get("subtitle") or voiceover),
                "visual": str(shot.get("visual") or ""),
                "camera": str(shot.get("camera") or ""),
                "spoken": bool(voiceover),
            }
        )

    declared = _num(script_content.get("duration_seconds"))
    if declared > 0 and abs(covered - declared) > 2:
        warnings.append(f"分镜时长合计 {covered:.0f}s 与脚本标称 {declared:.0f}s 偏差超过 2s，请核对脚本")

    return {
        "channel": str(script_content.get("channel") or ""),
        "aspect_ratio": str(script_content.get("aspect_ratio") or ""),
        "duration_seconds": declared or round(covered, 2),
        "shot_count": len(segments),
        "hook": str(script_content.get("hook") or ""),
        "cta": str(script_content.get("cta") or ""),
        "segments": segments,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ #
# 样例引擎（sample）：按流逝时间惰性推进                              #
# ------------------------------------------------------------------ #


def _sample_render_seconds(manifest: dict[str, Any]) -> int:
    """样例渲染时长：随分镜数变化但封顶，让生命周期可见又不拖慢联调。"""
    return min(25, max(6, 2 + int(manifest.get("shot_count") or 1)))


def _advance_sample(job: dict[str, Any], now: datetime) -> None:
    started = _parse_iso(str(job.get("started_at") or ""))
    if started is None:
        return
    elapsed = (now - started).total_seconds()
    render_seconds = int(job.get("render_seconds") or _sample_render_seconds(job.get("manifest") or {}))
    if elapsed < 1.0:
        _transition(job, "queued", "样例引擎已受理（排队中）")
        job["progress"] = 0
    elif elapsed < render_seconds:
        _transition(job, "rendering", f"样例渲染进行中（{render_seconds}s 总时长）")
        job["progress"] = min(99, int(elapsed / render_seconds * 100))
    else:
        _transition(job, "done", "样例渲染完成")
        job["progress"] = 100
        job["finished_at"] = now_iso()
        job["video_url"] = f"sample://digital-human/{job['id']}.mp4"


# ------------------------------------------------------------------ #
# http 适配样例：POST 建任务 → GET 查状态                             #
# ------------------------------------------------------------------ #


def _auth_headers() -> dict[str, str]:
    cfg = get_config().digital_human
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    traceparent = tracer.current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


_STATUS_MAP = {
    "queued": "queued",
    "pending": "queued",
    "processing": "rendering",
    "rendering": "rendering",
    "running": "rendering",
    "done": "done",
    "completed": "done",
    "succeeded": "done",
    "finished": "done",
    "failed": "failed",
    "error": "failed",
}


def _submit_http(job: dict[str, Any]) -> None:
    """向远端网关建任务（样例契约：POST → 2xx + ``{"job_id": "..."}``）。"""
    cfg = get_config().digital_human
    if not cfg.api_url:
        _transition(job, "failed", "未配置 DIGITAL_HUMAN_API_URL，无法提交渲染任务")
        return
    manifest = job.get("manifest") or {}
    try:
        response = httpx.post(
            cfg.api_url,
            json={
                "job_id": job["id"],
                "task_id": job["task_id"],
                "avatar": job.get("avatar") or "",
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
    """查询远端状态（样例契约：GET → ``{"status": "...", "video_url": "..."}``）。"""
    cfg = get_config().digital_human
    last_poll = _parse_iso(str(job.get("last_poll_at") or ""))
    if last_poll is not None and (now - last_poll).total_seconds() < _HTTP_POLL_INTERVAL:
        return  # 轮询太密：保持现状即可，状态由下次读取推进
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
        _transition(job, "done", "远端渲染完成")
        job["progress"] = 100
        job["finished_at"] = now_iso()
        job["video_url"] = str(body.get("video_url") or body.get("url") or "")
    elif status == "failed":
        _transition(job, "failed", str(body.get("error") or body.get("message") or "远端渲染失败"))
    elif status in ("queued", "rendering"):
        _transition(job, status, "远端渲染中")
        job["progress"] = max(int(job.get("progress") or 0), 10)


def _advance_http(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") == "queued" and not job.get("remote_id") and not job.get("submitted"):
        job["submitted"] = True
        _submit_http(job)
        return
    if job.get("status") in ("queued", "rendering"):
        _poll_http(job, now)


# ------------------------------------------------------------------ #
# 公共 API                                                            #
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
    """状态变更：只有真正变化才落历史并发事件（避免重复刷屏）。"""
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
        message=f"数字人渲染（样例）：{note}",
        level="warn" if status == "failed" else "info",
        payload={"job_id": job.get("id"), "status": status, "provider": job.get("provider")},
    )


def _latest_video_script(task: TaskRecord) -> dict[str, Any] | None:
    artifact = next(
        (a for a in reversed(task.artifacts) if a.type == "video_script"), None
    )
    return artifact.content if artifact is not None else None


def create_job(
    task: TaskRecord,
    *,
    avatar: str = "",
    provider: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """为任务创建一个数字人渲染作业（``POST /api/tasks/{id}/digital-human``）。

    任务必须已产出 ``video_script``；渲染由样例引擎或 http 网关异步推进，
    这里只负责受理与记账，绝不阻塞调用方。
    """
    script = _latest_video_script(task)
    if script is None:
        raise ValueError("该任务没有视频脚本产物，数字人渲染没有可执行的分镜（仅短视频形态会产出脚本）")

    cfg = get_config().digital_human
    chosen = provider if provider in ("sample", "http") else cfg.provider
    manifest = build_manifest(script)
    job_id = new_id("dh")
    moment = now or datetime.now(timezone.utc)
    job: dict[str, Any] = {
        "id": job_id,
        "task_id": task.id,
        "tenant": task.tenant,
        "provider": chosen,
        "avatar": avatar.strip() or cfg.avatar or "sample-avatar",
        "status": "queued",
        "progress": 0,
        "script_artifact_id": next(
            (a.id for a in reversed(task.artifacts) if a.type == "video_script"), ""
        ),
        "channel": str(manifest.get("channel") or task.brief.channel),
        "duration_seconds": manifest.get("duration_seconds"),
        "shot_count": manifest.get("shot_count"),
        "render_seconds": _sample_render_seconds(manifest),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": now_iso(),
        "finished_at": "",
        "video_url": "",
        "error": "",
        "attempts": 0,
        "remote_id": "",
        "submitted": False,
        "last_poll_at": "",
        "manifest": manifest,
        "history": [{"ts": now_iso(), "from": "", "to": "queued", "note": "渲染任务已受理"}],
    }
    with _STORE_LOCK:
        jobs = _load_locked()
        jobs[job_id] = job
        _mark_dirty()
        _save_locked()

    with tracer.span_on_task(
        task.id,
        "digitalhuman.render",
        kind="client",
        agent_id="A8",
        attributes={
            "digitalhuman.job_id": job_id,
            "digitalhuman.provider": chosen,
            "digitalhuman.shots": manifest.get("shot_count") or 0,
        },
    ):
        # 事件在 span 内发布，trace_id/span_id 随事件自动对齐
        event_bus.publish(
            task_id=task.id,
            type="log",
            message=f"数字人渲染任务已创建（{chosen} 样例，{manifest.get('shot_count')} 镜）",
            agent_id="A8",
            payload={"job_id": job_id, "avatar": job["avatar"]},
        )
        if chosen == "http":
            # http provider 立即提交一次；失败在作业上记账，不向上抛
            _advance_http(job, moment)
            with _STORE_LOCK:
                _mark_dirty()
                _save_locked()
    log.info(f"数字人渲染任务已创建：{job_id}（task={task.id}, provider={chosen}）")
    return dict(job)


def _refresh(job: dict[str, Any], now: datetime) -> None:
    """惰性推进一个作业的状态（样例按流逝时间，http 按远端状态）。"""
    if job.get("status") in ("done", "failed"):
        return
    before = str(job.get("status"))
    if job.get("provider") == "http":
        with tracer.span_on_task(str(job.get("task_id")), "digitalhuman.poll", kind="client"):
            _advance_http(job, now)
    else:
        _advance_sample(job, now)
    if str(job.get("status")) != before:
        with _STORE_LOCK:
            _mark_dirty()


def list_jobs(
    task_id: str | None = None, *, tenant: str | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """列出渲染作业（读取时惰性推进状态），新→旧排序。

    ``tenant`` 用于鉴权开启时把结果限定在当前租户内。
    推进逻辑（含 http provider 的远端轮询）在**锁外**执行，网络慢不阻塞其他调用方。
    """
    moment = now or datetime.now(timezone.utc)
    with _STORE_LOCK:
        snapshot = [dict(job) for job in _load_locked().values()]
    changed = False
    for job in snapshot:
        before_status = str(job.get("status"))
        _refresh(job, moment)
        if str(job.get("status")) != before_status:
            changed = True
    if changed:
        with _STORE_LOCK:
            live = _load_locked()
            for job in snapshot:  # 推进结果回写（以 id 对齐，保留期间新建的作业）
                if job.get("id") in live:
                    live[str(job["id"])] = job
            _save_locked()
    rows = [
        job
        for job in snapshot
        if (task_id is None or str(job.get("task_id")) == task_id)
        and (tenant is None or str(job.get("tenant")) == tenant)
    ]
    rows.sort(key=lambda job: job.get("created_at") or "", reverse=True)
    return rows


def get_job(job_id: str, *, tenant: str | None = None) -> dict[str, Any] | None:
    """读取单个作业（同样惰性推进）。"""
    rows = list_jobs(tenant=tenant)
    return next((row for row in rows if row.get("id") == job_id), None)


def drop_task(task_id: str) -> int:
    """任务被删除时回收其渲染作业（与 tracer.drop / event_bus.drop 同一时机）。"""
    with _STORE_LOCK:
        jobs = _load_locked()
        doomed = [job_id for job_id, job in jobs.items() if str(job.get("task_id")) == task_id]
        for job_id in doomed:
            jobs.pop(job_id, None)
        if doomed:
            _mark_dirty()
            _save_locked()
    return len(doomed)


__all__ = [
    "STATUSES",
    "STORE_FILE",
    "build_manifest",
    "create_job",
    "drop_task",
    "get_job",
    "list_jobs",
]
