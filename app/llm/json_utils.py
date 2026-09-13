"""从模型输出中稳健地抽取/规整数据（移植自 server/llm/json.ts）。

真实模型常见的四种脏输出：
  1. ```json ... ``` 代码块
  2. 前后带解释性文字
  3. 结尾多余逗号
  4. **被 max_tokens 截断**（JSON 未闭合）——真实网关下最常见的失败

第 4 种是本项目接真实模型后暴露的关键缺陷：输出被截断时括号永远配不平，
只做「配对 + json.loads」会直接放弃解析，整个智能体以「无法解析为 JSON」失败，
而**实际内容已经产出了大半**。因此这里补一条修复路径：
按未闭合的容器逐层补全，并丢弃最后一个不完整的元素。

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
    """尽力从文本中解析出 JSON 对象/数组；失败返回 None。

    依次尝试：代码块 → 原文 → 首个 ``{``/``[`` 起的配对片段 → **截断修复**。
    """
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
        if end != -1:
            parsed = _try_parse(trimmed[start : end + 1])
            if parsed is not None:
                return parsed
            continue

        # 括号配不平 ⇒ 大概率是被截断的。尝试修复而不是直接放弃。
        repaired = repair_truncated_json(trimmed[start:])
        if repaired is not None:
            return repaired
    return None


def repair_truncated_json(text: str) -> Any | None:
    """修复被截断的 JSON 片段（补全未闭合的字符串/对象/数组）。

    做法（从保守到激进）：
    1. 逐字符扫描，记录容器栈与字符串状态；
    2. 若结束于字符串内部 → 补上收尾引号；
    3. 丢弃最后一个**不完整**的元素（尾部多余逗号 / 悬空的 ``"key":``）；
    4. 按栈逆序补全 ``}`` / ``]`` 并解析。

    任何一步失败都返回 ``None``（调用方仍按解析失败处理），
    因此这不改变「失败就报错」的语义，只是**多给一次机会**。
    """
    snippet = (text or "").strip()
    if not snippet or snippet[0] not in "{[":
        return None

    stack: list[str] = []
    in_string = False
    escaped = False
    last_complete = -1  # 最后一个「安全截断点」（闭合的 } 或 ] 之后）

    for index, ch in enumerate(snippet):
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
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            last_complete = index

    if not stack and not in_string:
        # 结构本身是平衡的，交给常规解析（这里不重复处理）
        return None

    head = snippet
    if in_string:
        # 截断在字符串中间：补上收尾引号，避免把半句话当成完整值
        head = head + '"'
    # 丢弃尾部不完整的元素（悬空逗号 / 悬空 key 或冒号）
    head = re.sub(r",\s*$", "", head.rstrip())
    head = re.sub(r',\s*"[^"]*"\s*:?\s*$', "", head.rstrip())
    head = re.sub(r'[:,]\s*$', "", head.rstrip())

    # 逆序补全容器
    for opener in reversed(stack):
        head += "}" if opener == "{" else "]"

    parsed = _try_parse(head)
    if parsed is not None:
        return parsed

    # 兜底：回退到最后一个已闭合的位置，再补全
    if last_complete > 0:
        fallback = snippet[: last_complete + 1]
        fallback = re.sub(r",\s*$", "", fallback.rstrip())
        stack2: list[str] = []
        in_str = False
        esc = False
        for ch in fallback:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack2.append(ch)
            elif ch in "}]" and stack2:
                stack2.pop()
        for opener in reversed(stack2):
            fallback += "}" if opener == "{" else "]"
        return _try_parse(fallback)
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


def schema_keys(schema: str) -> list[str]:
    """从 schema 文本里抽出字段名。

    这里不引入 jsonschema：我们只需要「有哪些顶层字段名」这一件事，
    用正则抽 ``"key":`` 比引一个运行时依赖更轻。
    副作用是枚举值（``"type": "brand|case"`` 里的 ``brand`` 不带冒号）不会被抽到，
    这正是我们想要的 —— 只要字段名。
    """
    if not schema:
        return []
    return re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', schema)


#: 无论 schema 怎么写都必须保留的字段。
#: 它们是**智能体结果契约的一部分**（``build_result`` 会用 ``read_confidence`` /
#: ``read_risks`` / ``read_evidence`` 读取），个别智能体的 SCHEMA 里没列全 ——
#: 若按 schema 一刀切裁剪，会静默丢掉置信度与风险提示，而这类丢失很难被察觉。
ALWAYS_KEEP_KEYS: frozenset[str] = frozenset(
    {"confidence", "risks", "evidence", "verdict", "verdict_reason", "needs_human_review"}
)


def strip_unknown_keys(data: Any, schema: str) -> Any:
    """按 schema 裁掉模型**多输出**的顶层字段。

    真实模型经常额外附送 ``reasoning`` / ``explanation`` / ``notes`` /
    ``summary_of_changes`` 之类的字段，它们不会被任何智能体消费，
    却会**实打实计入输出 token** —— 实测某任务 completion 达 6.5 万 token，
    其中相当一部分是这类冗余，而且正是它把输出顶到 max_tokens 截断。

    只裁**顶层**：嵌套结构里多出的键通常被下游宽松读取，递归删除反而有丢信息风险；
    顶层裁剪已能覆盖主要浪费。``ALWAYS_KEEP_KEYS`` 里的契约字段永不被裁。
    schema 抽不出字段名时原样返回 —— 这是优化，不是校验。
    """
    allowed = set(schema_keys(schema)) | ALWAYS_KEEP_KEYS
    if not isinstance(data, dict) or not allowed:
        return data
    return {key: value for key, value in data.items() if key in allowed}


__all__ = [
    "ALWAYS_KEEP_KEYS",
    "as_bool",
    "as_num",
    "as_obj",
    "as_obj_array",
    "as_str",
    "as_str_array",
    "clamp",
    "extract_json",
    "normalize_confidence",
    "normalize_score",
    "normalize_score_int",
    "repair_truncated_json",
    "schema_keys",
    "strip_unknown_keys",
]


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
