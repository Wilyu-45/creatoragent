"""联网检索适配层（plan.md 2.2.3「搜索 Server」的可选真实实现）。

对应 A1/A2/A6/A10 的 ``web_search`` / ``page_fetch`` 工具。与「数字人渲染」同一
取舍：**不内置任何搜索服务**，只定义最小契约，由使用者指向自己的网关。

契约
----
检索（``POST WEB_SEARCH_API_URL``）::

    请求：{"query": "...", "max_results": 5}
    响应：{"results": [{"title": "...", "url": "https://...", "snippet": "..."}]}

响应解析是**宽容**的：``results`` / ``items`` / ``data`` / ``organic_results``
或直接返回数组都能识别，字段名兼容 ``url``/``link``、``snippet``/``description``，
避免使用者为了适配本系统而改写自己的网关。

抓取（``GET <url>``）：只接受 http/https 的公开页面，返回标题 + 去标签正文。

两条硬规则
----------
* **未配置即诚实失败**——``provider=none`` 或缺少 ``api_url`` 时抛
  ``WebUnavailable``，工具据此如实告诉智能体「本轮没有联网」，绝不返回编造结果；
* **抓取做 SSRF 防护**——URL 来自模型输出，因此必须限制协议并拒绝私网/环回/
  链路本地地址（含云元数据地址），否则这个工具会变成内网探测入口。
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import get_config
from .tracing import tracer

#: 单页正文进入提示词的最大字符数（检索是旁路情报，不能挤占创作预算）
_MAX_PAGE_CHARS = 2_000
#: 结果标题/摘要的长度上限
_MAX_FIELD_CHARS = 200

_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

#: 响应里可能承载结果数组的字段名（按优先级尝试）
_RESULT_KEYS = ("results", "items", "data", "organic_results", "hits")
#: 单条结果里 URL / 标题 / 摘要的候选字段名
_URL_KEYS = ("url", "link", "href")
_TITLE_KEYS = ("title", "name", "heading")
_SNIPPET_KEYS = ("snippet", "description", "summary", "text", "content")


class WebUnavailable(RuntimeError):
    """联网检索不可用（未配置 / 请求失败 / 响应不可解析）。

    工具层捕获它并转成「如实说明」的产出，而不是把它当成创作链路的失败。
    """


# ------------------------------------------------------------------ #
# 配置与可用性                                                        #
# ------------------------------------------------------------------ #


def search_configured() -> bool:
    """是否已具备真实联网检索能力（``provider=http`` 且配置了端点）。"""
    cfg = get_config().search
    return cfg.provider == "http" and bool(cfg.api_url)


def unavailable_reason() -> str:
    """返回「为什么不能联网」的可读说明，供智能体如实转述。"""
    cfg = get_config().search
    if cfg.provider != "http":
        return "未配置联网检索（WEB_SEARCH_PROVIDER=none）"
    if not cfg.api_url:
        return "已选择 http 检索但未配置 WEB_SEARCH_API_URL"
    return "联网检索不可用"


# ------------------------------------------------------------------ #
# 检索                                                                #
# ------------------------------------------------------------------ #


def _headers() -> dict[str, str]:
    cfg = get_config().search
    headers: dict[str, str] = {"Accept": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    traceparent = tracer.current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def _clip(value: Any, limit: int = _MAX_FIELD_CHARS) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    """从各种常见响应形状里取出结果数组。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in _RESULT_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    # 有些网关是 {"data": {"results": [...]}} 的两层嵌套
    for key in _RESULT_KEYS:
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _extract_items(nested)
            if found:
                return found
    return []


def web_search(query: str, *, max_results: int | None = None) -> list[dict[str, str]]:
    """执行一次检索，返回 ``[{title, url, snippet}]``。

    未配置、请求失败或响应不可解析时抛 ``WebUnavailable``。
    """
    cfg = get_config().search
    if not search_configured():
        raise WebUnavailable(unavailable_reason())

    limit = max_results if max_results is not None else cfg.max_results
    try:
        response = httpx.post(
            cfg.api_url,
            json={"query": query, "max_results": limit},
            headers=_headers(),
            timeout=max(1.0, cfg.timeout_ms / 1000.0),
        )
    except Exception as error:  # noqa: BLE001 - 网络异常统一转成「不可用」
        raise WebUnavailable(f"检索请求失败：{type(error).__name__}: {error}") from error

    if response.status_code >= 400:
        raise WebUnavailable(
            f"检索被拒绝：HTTP {response.status_code} {(response.text or '')[:120]}"
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise WebUnavailable("检索响应不是合法 JSON") from error

    items = _extract_items(payload)
    if not items:
        raise WebUnavailable("检索响应中没有可识别的结果数组（见 app/core/web.py 契约）")

    results: list[dict[str, str]] = []
    for item in items[:limit]:
        url = _first(item, _URL_KEYS)
        title = _first(item, _TITLE_KEYS)
        snippet = _first(item, _SNIPPET_KEYS)
        if not (url or title):
            continue
        results.append({"title": _clip(title), "url": _clip(url), "snippet": _clip(snippet)})
    if not results:
        raise WebUnavailable("检索结果缺少可用的标题与链接字段")
    return results


# ------------------------------------------------------------------ #
# 页面抓取（含 SSRF 防护）                                            #
# ------------------------------------------------------------------ #


def _ensure_public_url(url: str) -> str:
    """校验 URL 可安全抓取：仅 http/https，且主机解析后不是私网/环回/链路本地地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebUnavailable(f"仅支持 http/https 链接，收到：{parsed.scheme or '（空）'}")
    host = (parsed.hostname or "").strip()
    if not host:
        raise WebUnavailable("链接缺少主机名")

    candidates: list[str] = []
    try:
        candidates = [info[4][0] for info in socket.getaddrinfo(host, parsed.port or None)]
    except socket.gaierror:
        # 解析不出来就直接拒绝：无法确认目标是否在私网
        raise WebUnavailable(f"域名无法解析：{host}") from None

    for raw in candidates or [host]:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise WebUnavailable(f"拒绝抓取内网/保留地址：{host} → {address}")
    return url


def _html_to_text(raw: str) -> str:
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub("\n", text)
    text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def fetch_page(url: str) -> dict[str, Any]:
    """抓取单个公开页面，返回 ``{url, title, text, truncated}``。"""
    cfg = get_config().search
    if not search_configured():
        raise WebUnavailable(unavailable_reason())

    target = _ensure_public_url(url)
    try:
        response = httpx.get(
            target,
            headers={"Accept": "text/html,application/xhtml+xml,text/plain", **_headers()},
            timeout=max(1.0, cfg.timeout_ms / 1000.0),
            follow_redirects=True,
        )
    except Exception as error:  # noqa: BLE001
        raise WebUnavailable(f"页面抓取失败：{type(error).__name__}: {error}") from error

    if response.status_code >= 400:
        raise WebUnavailable(f"页面返回 HTTP {response.status_code}")
    content_type = (response.headers.get("content-type") or "").lower()
    if content_type and not any(
        kind in content_type for kind in ("text/html", "text/plain", "application/xhtml")
    ):
        raise WebUnavailable(f"仅抓取文本页面，收到 content-type={content_type.split(';')[0]}")

    raw = response.text or ""
    title_match = _TITLE_RE.search(raw)
    title = html.unescape(title_match.group(1)).strip() if title_match else ""
    text = _html_to_text(raw)
    truncated = len(text) > _MAX_PAGE_CHARS
    return {
        "url": target,
        "title": _clip(title),
        "text": text[:_MAX_PAGE_CHARS],
        "truncated": truncated,
    }


__all__ = [
    "WebUnavailable",
    "fetch_page",
    "search_configured",
    "unavailable_reason",
    "web_search",
]