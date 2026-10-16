"""影视制作工厂 —— 真实服务商通道（图片 / 视频 / 语音 / 大模型）。

与既有旁路作业同一口径：**没配端点就不假装成功**。每个函数在未配置时返回
``None``，调用方（岗位节点）随即走离线示意通道并在产物上标注 ``simulated``，
UI 上如实显示「示意 / 真机」。配置项全部复用 ``app/config.py`` 里已有的
``image_gen`` / ``video_gen`` / ``dubbing`` / ``llm`` 设置，不新增环境变量。
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from ..config import get_config
from ..llm import chat
from ..llm.json_utils import extract_json
from ..llm.types import ChatMessage, LLMRequest
from ..logger import create_logger

log = create_logger("factory.media")


# ------------------------------------------------------------------ #
# 能力可用性探测                                                        #
# ------------------------------------------------------------------ #


def image_provider() -> tuple[bool, str]:
    settings = get_config().image_gen
    ready = settings.provider in {"openai", "local"} and bool(settings.base_url)
    return ready, settings.provider if ready else "sample"


def video_provider() -> tuple[bool, str]:
    settings = get_config().video_gen
    ready = settings.provider == "http" and bool(settings.api_url)
    return ready, settings.provider if ready else "sample"


def voice_provider() -> tuple[bool, str]:
    settings = get_config().dubbing
    key = settings.elevenlabs_api_key or settings.api_key
    ready = settings.provider in {"elevenlabs", "real", "auto"} and bool(key)
    return ready, "elevenlabs" if ready else "sample"


def llm_ready() -> bool:
    """真实大模型是否可用（内置离线引擎不算：它的用途注册表不含工厂岗位）。"""
    settings = get_config().llm
    return bool(settings.base_url) and (settings.provider or "").lower() not in {"", "mock", "simulated"}


# ------------------------------------------------------------------ #
# 大模型：岗位的专业判断可以升级为真模型                                 #
# ------------------------------------------------------------------ #


def ask_json(purpose: str, system: str, user: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """要求模型返回 JSON 并解析；失败返回 ``None``（调用方回落规则通道）。"""
    if not llm_ready():
        return None
    try:
        response = chat(
            LLMRequest(
                purpose=f"factory.{purpose}",
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user),
                ],
                context=context or {},
                json=True,
                temperature=0.7,
            )
        )
    except Exception as error:  # noqa: BLE001 - 真模型不可用不影响产线
        log.warn(f"工厂 LLM 调用失败（回落规则通道）：{error}")
        return None
    if getattr(response, "simulated", False):
        return None
    parsed = extract_json(response.content or "")
    return parsed if isinstance(parsed, dict) else None


def ask_text(purpose: str, system: str, user: str) -> str:
    if not llm_ready():
        return ""
    try:
        response = chat(
            LLMRequest(
                purpose=f"factory.{purpose}",
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user),
                ],
                temperature=0.8,
            )
        )
    except Exception as error:  # noqa: BLE001
        log.warn(f"工厂 LLM 文本调用失败：{error}")
        return ""
    return "" if getattr(response, "simulated", False) else (response.content or "").strip()


# ------------------------------------------------------------------ #
# 图片：openai 兼容的图片生成端点（含本地 ComfyUI/SD 网关）              #
# ------------------------------------------------------------------ #


def generate_image(prompt: str, size: str = "1024x1024", seed: int = 0) -> dict[str, Any] | None:
    """返回 ``{"bytes": png, "provider": ...}``；未配置或失败返回 None。"""
    ready, provider = image_provider()
    if not ready:
        return None
    settings = get_config().image_gen
    payload: dict[str, Any] = {
        "model": settings.model or "gpt-image-1",
        "prompt": prompt,
        "size": size or settings.size or "1024x1024",
        "n": 1,
    }
    if seed:
        payload["seed"] = seed
    try:
        response = httpx.post(
            f"{settings.base_url.rstrip('/')}/images/generations",
            headers={"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {},
            json=payload,
            timeout=max(5.0, settings.timeout_ms / 1000.0),
        )
        response.raise_for_status()
        data = response.json()
    except Exception as error:  # noqa: BLE001 - 真机失败要如实回落，不能整条线崩
        log.warn(f"图片生成失败（回落示意帧）：{error}")
        return None
    items = data.get("data") if isinstance(data, dict) else None
    first = items[0] if isinstance(items, list) and items else None
    if not isinstance(first, dict):
        return None
    raw = first.get("b64_json") or ""
    if raw:
        try:
            return {"bytes": base64.b64decode(raw), "provider": provider, "url": ""}
        except Exception:  # noqa: BLE001
            return None
    url = str(first.get("url") or "")
    if url.startswith("http"):
        try:
            blob = httpx.get(url, timeout=60.0)
            blob.raise_for_status()
            return {"bytes": blob.content, "provider": provider, "url": url}
        except Exception as error:  # noqa: BLE001
            log.warn(f"图片下载失败（回落示意帧）：{error}")
    return None


# ------------------------------------------------------------------ #
# 视频：统一异步 http 契约（受理 → 查询），本地 ComfyUI 农场指向内网即可  #
# ------------------------------------------------------------------ #


def submit_video(prompt: str, image_b64: str, duration_s: float, aspect: str, seed: int) -> dict[str, Any] | None:
    ready, provider = video_provider()
    if not ready:
        return None
    settings = get_config().video_gen
    timeout = max(5.0, settings.timeout_ms / 1000.0)
    body = {
        "prompt": prompt,
        "duration": round(duration_s, 2),
        "aspect_ratio": aspect,
        "seed": seed,
        "fps": 24,
    }
    if image_b64:
        body["image"] = image_b64
    headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    try:
        response = httpx.post(settings.api_url, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
        raw = response.json()
    except Exception as error:  # noqa: BLE001
        log.warn(f"视频受理失败（回落示意片段）：{error}")
        return None
    payload = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    remote_id = str(payload.get("id") or payload.get("task_id") or payload.get("job_id") or "")
    return {"provider": provider, "remote_id": remote_id, "raw": payload}


def poll_video(remote_id: str) -> dict[str, Any] | None:
    """查询一次远端任务；返回 ``{status, url}``。"""
    ready, _ = video_provider()
    if not ready or not remote_id:
        return None
    settings = get_config().video_gen
    base = settings.api_url.rstrip("/")
    url = base if base.endswith(f"/{remote_id}") else f"{base}/{remote_id}"
    headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    try:
        response = httpx.get(url, headers=headers, timeout=max(5.0, settings.timeout_ms / 1000.0))
        response.raise_for_status()
        raw = response.json()
    except Exception as error:  # noqa: BLE001
        log.warn(f"视频查询失败：{error}")
        return None
    payload = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    return {
        "status": str(payload.get("status") or ""),
        "url": str(payload.get("video_url") or payload.get("url") or ""),
        "progress": payload.get("progress"),
    }


def download(url: str, limit_mb: int = 64) -> bytes | None:
    try:
        response = httpx.get(url, timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        if len(response.content) > limit_mb * 1024 * 1024:
            log.warn(f"远端文件超过 {limit_mb}MB 上限，放弃下载")
            return None
        return response.content
    except Exception as error:  # noqa: BLE001
        log.warn(f"远端文件下载失败：{error}")
        return None


# ------------------------------------------------------------------ #
# 语音：elevenlabs REST（其余 TTS 通道仍归 dubbing 引擎的任务态作业）     #
# ------------------------------------------------------------------ #


def synthesize_voice(text: str, voice_id: str = "", speed: float = 1.0) -> bytes | None:
    ready, provider = voice_provider()
    if not ready:
        return None
    settings = get_config().dubbing
    key = settings.elevenlabs_api_key or settings.api_key
    base = (settings.elevenlabs_base_url or "https://api.elevenlabs.io/v1").rstrip("/")
    selected = voice_id or "JBFqnCBsd6RMkjVDRZzb"
    try:
        response = httpx.post(
            f"{base}/text-to-speech/{selected}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": settings.elevenlabs_model_id or "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.7, "speed": max(0.5, min(2.0, speed))},
            },
            timeout=max(10.0, settings.timeout_ms / 1000.0),
        )
        response.raise_for_status()
    except Exception as error:  # noqa: BLE001
        log.warn(f"TTS 失败（回落示意人声）：{error}")
        return None
    return response.content if response.content else None


def json_preview(value: Any, limit: int = 400) -> str:
    """日志用的有界 JSON 摘要。"""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"
