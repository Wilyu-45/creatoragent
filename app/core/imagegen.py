"""图片生成接入样例（开发样例，plan「图片生成通道」）。

定位（务必如实理解）
--------------------
图片生成**不在本系统内实现**——各家文生图服务的授权、计费与返回结构差异极大，
硬编码任何一家都会变成「绑死厂商的玩具」。与数字人渲染（``core/digital_human.py``）
同一哲学：系统提供的是**可回归的接入样例**，让联调在买任何服务之前就能发生。

三条通道（``provider`` 可用请求体覆盖）：

* ``sample``（默认，零依赖）：离线确定性模拟「排队 → 生成 → 完成」，按 ``visual_brief``
  的 ``image_prompts`` 生成**生成清单**（不产真图，保证 API/UI/回归可联调）；
* ``openai``：对接 OpenAI 协议 ``POST {base_url}/images/generations``，覆盖一切兼容该
  协议的云端网关（即梦 / 通义万相 / Replicate 经网关转换均可）；
* ``local``：对接本地图生网关的 ``POST {base_url}/sdapi/v1/txt2img`` 同步接口
  （A1111 / SD-WebUI；ComfyUI 需前置一个同构的同步网关）。

未配置真实端点时**显式失败，绝不假装成功**。生命周期与数字人一致：``sample`` 按流逝
时间惰性推进，``openai``/``local`` 在受理时同步执行真实请求、成功即完成、失败如实记账。
"""

from __future__ import annotations

import base64
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import ASSETS_DIR, DATA_DIR, get_config
from ..logger import create_logger
from .clock import now_iso
from .events import event_bus, new_id
from .gen_jobs import FileJobBackend, PgJobBackend, make_backend
from .tracing import tracer
from .types import TaskRecord

log = create_logger("imagegen")

#: 作业状态机：queued → generating → done / failed
STATUSES = ("queued", "generating", "done", "failed")

#: 作业存储（file 模式 JSON；pg 模式 imagegen_jobs 表，见 storage_contract.md）
STORE_FILE = DATA_DIR / "imagegen_jobs.json"
_TABLE = "imagegen_jobs"

#: 作业持久化后端（懒建单例；pg 模式用 imagegen_jobs 表，file 模式用 JSON 文件）
_BACKEND: "FileJobBackend | PgJobBackend | None" = None
_BACKEND_LOCK = threading.RLock()


def _job_backend() -> "FileJobBackend | PgJobBackend":
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = make_backend(STORE_FILE, _TABLE)
    return _BACKEND


def _num(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return fallback


# ------------------------------------------------------------------ #
# 生成清单：把 visual_brief 的 image_prompts 翻译成「该生成哪些图」     #
# ------------------------------------------------------------------ #


def build_manifest(visual_content: dict[str, Any]) -> dict[str, Any]:
    """把 ``visual_brief`` 的 image_prompts 转换成图片生成清单。

    只透传 A8 已产出的提示词，**不臆造内容**：缺 prompt 的条目会被显式告警而非静默跳过。
    """
    raw_prompts = [item for item in visual_content.get("image_prompts") or [] if isinstance(item, dict)]
    cfg = get_config().image_gen
    limit = max(1, int(cfg.max_images))
    segments: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, item in enumerate(raw_prompts):
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            warnings.append(f"第 {index + 1} 条 image_prompt 没有 prompt 文本，无法生成")
            continue
        segments.append(
            {
                "index": index + 1,
                "id": str(item.get("id") or f"IMG{index + 1}"),
                "usage": str(item.get("usage") or ""),
                "scene": str(item.get("scene") or ""),
                "prompt": prompt,
                "negative": str(item.get("negative") or ""),
                "aspect_ratio": str(item.get("aspect_ratio") or cfg.size),
            }
        )
    dropped = len(segments) - limit
    if dropped > 0:
        segments = segments[:limit]
        warnings.append(f"受 IMAGEGEN_MAX_IMAGES={limit} 限制，本次仅生成前 {limit} 张，余 {dropped} 条未生成")
    return {
        "provider": cfg.provider,
        "size": cfg.size,
        "requested": len(raw_prompts),
        "segments": segments,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ #
# 样例引擎（sample）：按流逝时间惰性推进                               #
# ------------------------------------------------------------------ #


def _sample_seconds(manifest: dict[str, Any]) -> int:
    return min(20, max(5, 3 + int(len(manifest.get("segments") or []))))


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
        job["images"] = [
            {
                "index": seg["index"],
                "id": seg["id"],
                "url": f"sample://imagegen/{job['id']}/{seg['id']}.png",
            }
            for seg in job.get("manifest", {}).get("segments", [])
        ]


# ------------------------------------------------------------------ #
# openai / local：受理时同步执行真实请求（成功即完成，失败如实记账）     #
# ------------------------------------------------------------------ #


def _auth_headers() -> dict[str, str]:
    cfg = get_config().image_gen
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    traceparent = tracer.current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def _persist_data_image(job_id: str, index: int, b64: str) -> str:
    """把 base64 图片落盘到 ASSETS_DIR，返回 ``assets/`` 相对引用（不返回超大内联串）。"""
    try:
        raw = b64.split(",", 1)[-1]
        blob = base64.b64decode(raw)
    except (ValueError, TypeError):
        return ""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"imagegen-{job_id}-{index}.png"
    (ASSETS_DIR / name).write_bytes(blob)
    return f"assets/{name}"


def _openai_image(job_id: str, seg: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """归一化 OpenAI ``/images/generations`` 单项。

    优先直接用网关返回的 ``url``；只有 ``b64_json``（OpenAI 默认 ``response_format``
    与多数兼容网关的常见返回）时**落盘到 assets 再引用**——否则生成的图会被丢弃、
    前端拿到空 url（local 通道已有落盘，此处与它对齐）。
    """
    url = str(item.get("url") or "").strip()
    had_b64 = bool(item.get("b64_json"))
    if not url and had_b64:
        url = _persist_data_image(job_id, int(seg["index"]), str(item.get("b64_json")))
    return {"index": seg["index"], "id": seg["id"], "url": url, "b64": had_b64}


def _submit_real(job: dict[str, Any]) -> None:
    """按 openai/local 契约同步生成图片；任何异常 → 作业失败（不向上抛、不假装成功）。

    以**作业实际选中的 provider**（可被请求体覆盖）为准分派，而非全局配置：
    否则全局为 sample 时用 openai 覆盖却误走 local 分支（与 videogen 成本 bug 同类）。
    """
    cfg = get_config().image_gen
    chosen = str(job.get("provider") or cfg.provider)
    segments = job.get("manifest", {}).get("segments") or []
    if not cfg.base_url:
        _transition(job, "failed", f"provider={chosen} 未配置 IMAGEGEN_BASE_URL，无法生成")
        return
    if not segments:
        _transition(job, "failed", "没有可生成的 image_prompt（任务需先产出 visual_brief）")
        return
    images: list[dict[str, Any]] = []
    try:
        if chosen == "openai":
            url = cfg.base_url.rstrip("/") + "/images/generations"
            for seg in segments:
                resp = httpx.post(
                    url,
                    json={"prompt": seg["prompt"], "size": cfg.size, "n": 1, **({"model": cfg.model} if cfg.model else {})},
                    headers=_auth_headers(),
                    timeout=max(1.0, cfg.timeout_ms / 1000.0),
                )
                if resp.status_code >= 400:
                    _transition(job, "failed", f"生成被拒绝：HTTP {resp.status_code} {(resp.text or '')[:160]}")
                    return
                body = resp.json()
                data = (body or {}).get("data") or [{}]
                item = data[0] if isinstance(data, list) and data else {}
                images.append(_openai_image(str(job["id"]), seg, item))
        else:  # local：A1111 / SD-WebUI /sdapi/v1/txt2img 同步接口
            width, _, height = cfg.size.partition("x")
            url = cfg.base_url.rstrip("/") + "/sdapi/v1/txt2img"
            for seg in segments:
                payload: dict[str, Any] = {
                    "prompt": seg["prompt"],
                    "negative_prompt": seg.get("negative") or "",
                    "width": int(_num(width, 1024)),
                    "height": int(_num(height, 1024)),
                }
                if cfg.api_key:
                    payload["enable_hr"] = False
                resp = httpx.post(
                    url,
                    json=payload,
                    headers=_auth_headers(),
                    timeout=max(1.0, cfg.timeout_ms / 1000.0),
                )
                if resp.status_code >= 400:
                    _transition(job, "failed", f"本地生成被拒绝：HTTP {resp.status_code} {(resp.text or '')[:160]}")
                    return
                body = resp.json()
                b64_list = (body or {}).get("images") or []
                ref = _persist_data_image(str(job["id"]), int(seg["index"]), str(b64_list[0])) if b64_list else ""
                images.append({"index": seg["index"], "id": seg["id"], "url": ref, "b64": False})
    except Exception as error:  # noqa: BLE001 - 网络/解析异常转成作业失败，不向上抛
        _transition(job, "failed", f"生成失败：{type(error).__name__}: {error}")
        return

    if not images:
        _transition(job, "failed", "网关未返回任何图片")
        return
    _transition(job, "done", f"已生成 {len(images)} 张图")
    job["progress"] = 100
    job["finished_at"] = now_iso()
    job["images"] = images


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
        message=f"图片生成：{note}",
        level="warn" if status == "failed" else "info",
        payload={"job_id": job.get("id"), "status": status, "provider": job.get("provider")},
    )


def _latest_visual_brief(task: TaskRecord) -> dict[str, Any] | None:
    artifact = next((a for a in reversed(task.artifacts) if a.type == "visual_brief"), None)
    return artifact.content if artifact is not None else None


def create_job(task: TaskRecord, *, provider: str = "", now: datetime | None = None) -> dict[str, Any]:
    """为任务创建一个图片生成作业（``POST /api/tasks/{id}/images``）。

    任务必须已产出 ``visual_brief``；否则返回 409 由路由层处理。
    """
    visual = _latest_visual_brief(task)
    if visual is None:
        raise ValueError("该任务没有视觉指导产物（visual_brief），图片生成没有可执行的 image_prompt")

    cfg = get_config().image_gen
    chosen = provider if provider in ("sample", "openai", "local") else cfg.provider
    # 按选中的 provider 重算清单尺寸口径（清单里透传 provider，便于回归断言）
    manifest = build_manifest(visual)
    job_id = new_id("img")
    moment = now or datetime.now(timezone.utc)
    job: dict[str, Any] = {
        "id": job_id,
        "task_id": task.id,
        "tenant": task.tenant,
        "provider": chosen,
        "status": "queued",
        "progress": 0,
        "visual_artifact_id": next(
            (a.id for a in reversed(task.artifacts) if a.type == "visual_brief"), ""
        ),
        "size": manifest.get("size"),
        "requested": manifest.get("requested"),
        "render_seconds": _sample_seconds(manifest),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": now_iso(),
        "finished_at": "",
        "images": [],
        "error": "",
        "attempts": 0,
        "submitted": False,
        "manifest": manifest,
        "history": [{"ts": now_iso(), "from": "", "to": "queued", "note": "图片生成任务已受理"}],
    }
    _job_backend().create(job)

    with tracer.span_on_task(
        task.id,
        "imagegen.render",
        kind="client",
        agent_id="A8",
        attributes={
            "imagegen.job_id": job_id,
            "imagegen.provider": chosen,
            "imagegen.segments": len(manifest.get("segments") or []),
        },
    ):
        event_bus.publish(
            task_id=task.id,
            type="log",
            message=f"图片生成任务已创建（{chosen}，{len(manifest.get('segments') or [])} 条提示词）",
            agent_id="A8",
            payload={"job_id": job_id},
        )
        if chosen in ("openai", "local"):
            job["attempts"] = int(job.get("attempts") or 0) + 1
            job["submitted"] = True
            _submit_real(job)
            _job_backend().write_back([job])
    log.info(f"图片生成任务已创建：{job_id}（task={task.id}, provider={chosen}）")
    return dict(job)


def _refresh(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") in ("done", "failed"):
        return
    # openai/local 在受理时已同步终结；只有 sample 需要按流逝时间推进
    if job.get("provider") == "sample":
        _advance_sample(job, now)


def list_jobs(
    task_id: str | None = None, *, tenant: str | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """列出作业（读取时惰性推进 sample 状态），新→旧排序。"""
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
    """任务删除时回收其图片生成作业。"""
    return _job_backend().drop_task(task_id)


__all__ = [
    "STATUSES",
    "STORE_FILE",
    "build_manifest",
    "create_job",
    "drop_task",
    "get_job",
    "list_jobs",
]
