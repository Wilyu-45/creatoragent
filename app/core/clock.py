"""时间工具：统一产出与 JavaScript ``new Date().toISOString()`` 一致的 ISO 串。

前端直接展示这些字符串，格式必须与旧 Node 后端逐字一致：
``2026-09-12T03:04:05.678Z``（UTC、毫秒、以 Z 结尾）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now_iso() -> str:
    return to_iso(datetime.now(timezone.utc))


def to_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_in(seconds: int | float) -> str:
    """返回 seconds 秒之后的时间戳（用于 intent 过期时间）。"""
    return to_iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))
