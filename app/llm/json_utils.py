"""从模型输出中稳健地抽取/规整数据（移植自 server/llm/json.ts）。

真实模型常见的三种脏输出：
  1. ```json ... ``` 代码块
  2. 前后带解释性文字
  3. 结尾多余逗号

注意：TS 里的 ``str`` 在 Python 与内建同名，故重命名为 ``as_str``；
模块内所有宽松取值函数统一以 ``as_`` 前缀命名，便于调用方识别。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..core.util import js_round

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_START_RE = re.compile(r"[\[{]")
_NUMERIC_RE = re.compile(r"[^\d.\-]")


def extract_json(raw: str) -> Any:
    """尽力从文本中解析出 JSON 对象/数组；失败返回 None。"""
    if not raw:
        return None

    candidates: list[str] = []
    fence = _FENCE_RE.search(raw)
    if fence and fence.group(1):
        candidates.append(fence.group(1))
    candidates.append(raw)

    for candidate in candidates:
        trimmed = candidate.strip()
        direct = _try_parse(trimmed)
        if direct is not None:
            return direct

        start_match = _START_RE.search(trimmed)
        if start_match is None:
            continue
        start = start_match.start()
        end = _find_matching_end(trimmed, start)
        if end == -1:
            continue
        parsed = _try_parse(trimmed[start : end + 1])
        if parsed is not None:
            return parsed
    return None


def _try_parse(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        return json.loads(_TRAILING_COMMA_RE.sub(r"\1", text))
    except (json.JSONDecodeError, ValueError):
        return None


def _find_matching_end(text: str, start: int) -> int:
    """按括号配对找到与 text[start] 匹配的结束位置，跳过字符串内部。"""
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


# ------------------------------------------------------------------ #
# 宽松取值工具：真实模型字段名/类型可能漂移                            #
# ------------------------------------------------------------------ #


def as_str(value: Any, fallback: str = "") -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return _num_to_str(value)
    return fallback


def _num_to_str(value: float) -> str:
    """整数型浮点去掉多余 .0，与 JS 的 String(number) 对齐。"""
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return repr(value)


def as_num(value: Any, fallback: float = 0) -> float:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = _NUMERIC_RE.sub("", value)
        if cleaned in ("", "-", ".", "-."):
            return fallback
        try:
            return float(cleaned)
        except ValueError:
            return fallback
    return fallback


_STR_SPLIT_RE = re.compile(r"[\n,，、;；]")


def as_str_array(value: Any) -> list[str]:
    if isinstance(value, list):
        return [s for s in (as_str(v) for v in value) if s]
    if isinstance(value, str) and value.strip():
        return [s for s in (part.strip() for part in _STR_SPLIT_RE.split(value)) if s]
    return []


def as_obj_array(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def as_obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return value != 0
    return fallback


def clamp(value: float, min_: float, max_: float) -> float:
    return min(max_, max(min_, value))


def normalize_score(value: Any, fallback: float) -> float:
    """钳制到 [0, 100]，并处理 0-1 与 0-100 两套量纲混用。"""
    n = as_num(value, fallback)
    if 0 < n <= 1:
        return clamp(n * 100, 0, 100)
    return clamp(n, 0, 100)


def normalize_score_int(value: Any, fallback: float) -> int:
    return js_round(normalize_score(value, fallback))


def normalize_confidence(value: Any, fallback: float = 0.75) -> float:
    n = as_num(value, fallback)
    if n > 1:
        return clamp(n / 100, 0, 1)
    return clamp(n, 0, 1)
