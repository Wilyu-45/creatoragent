"""带颜色与作用域的控制台日志（移植自 server/logger.ts）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

_COLOR = {
    "debug": "\u001b[90m",
    "info": "\u001b[36m",
    "warn": "\u001b[33m",
    "error": "\u001b[31m",
}
_RESET = "\u001b[0m"


def _ts() -> str:
    """等价于 TS 的 toISOString().slice(11, 23)，即 HH:MM:SS.mmm（UTC）。"""
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:12]


def _fmt_extra(extra: Any) -> str:
    if isinstance(extra, str):
        return extra
    try:
        return json.dumps(extra, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(extra)


class Logger:
    def __init__(self, scope: str) -> None:
        self.scope = scope

    def _write(self, level: str, message: str, extra: Any = None) -> None:
        head = (
            f"{_COLOR[level]}[{_ts()}] {level.upper():<5} [{self.scope}]{_RESET}"
        )
        if extra is None:
            print(f"{head} {message}", flush=True)
        else:
            print(f"{head} {message} {_fmt_extra(extra)}", flush=True)

    def debug(self, message: str, extra: Any = None) -> None:
        self._write("debug", message, extra)

    def info(self, message: str, extra: Any = None) -> None:
        self._write("info", message, extra)

    def warn(self, message: str, extra: Any = None) -> None:
        self._write("warn", message, extra)

    def error(self, message: str, extra: Any = None) -> None:
        self._write("error", message, extra)


def create_logger(scope: str) -> Logger:
    return Logger(scope)


logger = create_logger("app")
