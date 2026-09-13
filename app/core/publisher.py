"""发布排期的时段解析与 webhook 投递（plan.md v2.0「自动发布」）。

系统**不内置各平台私有 SDK**——小红书 / 抖音 / 公众号的开放接口授权方式与
限流策略差异极大，硬编码极易失效。这里提供一个平台无关的投递通道：

1. 把排期里的「建议时段」（如 ``07:30-08:30 通勤时段``）解析为具体到期时间 ``due_at``；
2. 到点后把「该投什么」POST 给 ``PUBLISH_WEBHOOK_URL``（可对接 n8n / Zapier /
   自建发布网关，由网关再去调用平台开放接口）；
3. 失败按 ``publish.retry`` 次数指数退避重试，结果与错误回写到排期项上。

未配置 webhook 时，``dispatch`` 返回失败但调用方会退化为「登记发布」，
因此离线环境（含 smoke 测试）语义完整、行为可预期。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..logger import create_logger
from .clock import to_iso

log = create_logger("publisher")

#: 从时段文案里抽第一个 ``HH:MM``（兼容 ``07:30`` / ``7：30``）
_SLOT_TIME_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{1,2})")
#: 重试退避基数（秒），第 n 次失败后等待 ``_BACKOFF * 2**(n-1)``
_BACKOFF_SECONDS = 0.6


def parse_slot_time(slot: str) -> tuple[int, int] | None:
    """从「07:30-08:30 通勤时段」这类文案里取出第一个时间点 ``(时, 分)``。"""
    match = _SLOT_TIME_RE.search(slot or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= minute < 60) or not (0 <= hour <= 24):
        return None
    return hour % 24, minute


def compute_due_at(slot: str, now: datetime | None = None) -> str | None:
    """把建议时段换算成**最近一次**的到期时间（ISO8601 / UTC）。

    排期是「每天到点」的语义：若今天的该时刻已过，则顺延到明天。
    这样后台自动投递不会因为「建任务时已经过了投放点」而永远不触发。
    文案里没有可解析时间时返回 ``None``，此时该条目只能手动投递。
    """
    parsed = parse_slot_time(slot)
    if parsed is None:
        return None
    hour, minute = parsed
    current = now or datetime.now(timezone.utc)
    due = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= current:
        due += timedelta(days=1)
    return to_iso(due)


def _parse_iso(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def is_due(item: dict[str, Any], now: datetime | None = None) -> bool:
    """排期项是否已到期（无 ``due_at`` 视为未到期，需手动触发）。"""
    due = _parse_iso(str(item.get("due_at") or ""))
    if due is None:
        return False
    return due <= (now or datetime.now(timezone.utc))


def dispatch(
    url: str,
    payload: dict[str, Any],
    *,
    retry: int = 2,
    timeout_ms: int = 15_000,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """把一次投递推给 webhook。

    返回 ``(是否成功, 摘要)``：成功时摘要是响应体前 200 字，失败时是最后一次错误。
    ``url`` 为空直接失败（调用方据此退化为「登记发布」）。
    ``headers`` 可携带 W3C ``traceparent``（见 ``core/tracing.py``），让发布网关
    把这次投递与任务的调用轨迹对齐。
    """
    if not url:
        return False, "未配置 PUBLISH_WEBHOOK_URL（已退化为登记发布）"

    last_error = ""
    attempts = max(1, retry + 1)
    for attempt in range(attempts):
        try:
            response = httpx.post(
                url,
                json=payload,
                headers=headers or None,
                timeout=max(1.0, timeout_ms / 1000.0),
            )
            if response.status_code < 400:
                log.info(f"发布投递成功 HTTP {response.status_code}", {"channel": payload.get("channel")})
                return True, (response.text or "")[:200]
            last_error = f"HTTP {response.status_code}: {(response.text or '')[:160]}"
        except Exception as error:  # noqa: BLE001 - 网络异常统统转化为重试
            last_error = f"{type(error).__name__}: {error}"
        if attempt < attempts - 1:
            time.sleep(_BACKOFF_SECONDS * (2**attempt))
    log.warn("发布投递失败", {"channel": payload.get("channel"), "error": last_error})
    return False, last_error
