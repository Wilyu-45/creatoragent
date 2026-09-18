"""联网检索与站点监控工具（A1 策略 / A2 创意 / A3 策划 / A6 事实核查 / A10 数据分析）。

《plan.md》2.2.3 的「搜索 Server」在**默认配置下是关闭的**：本系统不内置搜索服务，
而智能体的诚实性规则要求「没查过就不许说查过」。因此本模块的设计是：

* 未配置（``WEB_SEARCH_PROVIDER=none``，默认）时，工具**如实返回「本轮未联网」**，
  并明确要求智能体不得声称已联网核实——离线链路行为与从前完全一致；
* 配置为 ``http`` 时，按 ``WEB_SEARCH_API_URL`` 执行真实检索，把「标题 + 链接 +
  摘要」注入提示词，并允许 ``page_fetch`` 继续抓取前几条结果的正文。

``page_fetch`` 抓取的链接**来自同一次 ``web_search`` 的结果**（智能体无法在中途
发指令，工具是生成前批量执行的）：因此 ``TOOLS`` 里的顺序有意义——
``web_search`` 必须排在 ``page_fetch`` 之前，二者通过任务级缓存传递链接。

``site_monitor`` 是与搜索网关无关的独立能力：用户在运行时设置里配置一组
待爬页面 URL（持久化到 ``data/settings.json``），创作时现场抓取这些页面的
正文注入提示词；未配置时同样如实声明。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import get_config
from ..core.web import (
    WebUnavailable,
    fetch_page,
    search_configured,
    unavailable_reason,
    web_search,
)
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

#: 各阶段关注的检索侧重（工具只能从 ``ctx.phase`` 区分调用者）
_PHASE_FOCUS: dict[str, str] = {
    "STRATEGY": "行业 受众 趋势",
    "CREATIVE": "营销 案例 创意",
    "FACT_CHECK": "参数 数据 来源",
    "ANALYZED": "基准 数据 表现",
}

#: 英文 Brief 时的侧重（多语言链路：中文检索词会污染英文产出）
_PHASE_FOCUS_EN: dict[str, str] = {
    "STRATEGY": "industry audience trends",
    "CREATIVE": "campaign case study",
    "FACT_CHECK": "specs data source",
    "ANALYZED": "benchmark performance data",
}

#: 同一任务内「上一次检索命中的链接」，供 ``page_fetch`` 续抓（用完即清）
_PENDING_URLS: dict[str, list[str]] = {}


def _query_for(ctx: "AgentRunContext") -> str:
    """用 Brief 与当前阶段拼一条检索词（不引入任何外部词表）。"""
    brief = ctx.brief
    focus_map = _PHASE_FOCUS_EN if brief.language.lower().startswith("en") else _PHASE_FOCUS
    parts = [brief.brand, brief.product, brief.industry]
    focus = focus_map.get(ctx.phase, "")
    if focus:
        parts.append(focus)
    return " ".join(part for part in parts if part).strip()


def _not_configured(ctx: "AgentRunContext", tool: str) -> ToolOutcome:
    reason = unavailable_reason()
    return ToolOutcome(
        summary=f"本轮未启用联网（{reason}）",
        detail=(
            f"▍{tool}：{reason}\n"
            "本轮没有任何联网检索结果可用：**不得声称已联网查证、不得引用任何在线链接或"
            "实时数据**。需要外部数据时，按原有规则标注为「未核实 / 假设」并写入 risks。"
        ),
        data={"configured": False, "reason": reason},
    )


def web_search_tool(ctx: "AgentRunContext") -> ToolOutcome:
    """联网检索：返回标题 / 链接 / 摘要，并记下链接供 page_fetch 续抓。"""
    if not search_configured():
        return _not_configured(ctx, "web_search")

    query = _query_for(ctx)
    _PENDING_URLS.pop(ctx.task_id, None)
    try:
        results = web_search(query)
    except WebUnavailable as error:
        _PENDING_URLS.pop(ctx.task_id, None)
        return ToolOutcome(
            summary=f"联网检索失败，本轮按离线处理：{error}",
            detail=(
                f"▍web_search：{error}\n"
                "检索未成功，**不得引用任何在线链接或实时数据**；如需外部信息请标注为未核实。"
            ),
            data={"configured": True, "error": str(error)},
        )

    _PENDING_URLS[ctx.task_id] = [item["url"] for item in results if item.get("url")]
    lines = [f"检索词：{query}"]
    for index, item in enumerate(results, start=1):
        lines.append(f"{index}. {item['title'] or '（无标题）'}")
        if item["url"]:
            lines.append(f"   链接：{item['url']}")
        if item["snippet"]:
            lines.append(f"   摘要：{item['snippet']}")
    lines.append(
        "（来源：外部检索服务，非权威核实。引用时必须带上链接；"
        "只有工具明确给出链接的内容才可写入 source，其余仍按未核实处理）"
    )
    return ToolOutcome(
        summary=f"检索到 {len(results)} 条外部结果（含链接）",
        detail="\n".join(lines),
        data={"configured": True, "query": query, "results": results},
    )


def page_fetch_tool(ctx: "AgentRunContext") -> ToolOutcome:
    """读取本轮检索命中的前几条页面正文（SSRF 防护在 ``app/core/web.py``）。"""
    cfg = get_config().search
    if not search_configured():
        return _not_configured(ctx, "page_fetch")
    if not cfg.fetch_pages:
        return ToolOutcome(
            summary="正文抓取已关闭（WEB_SEARCH_FETCH_PAGES=false）",
            detail="▍page_fetch：本轮只使用检索摘要，不抓取页面正文。",
            data={"configured": True, "enabled": False},
        )

    urls = _PENDING_URLS.pop(ctx.task_id, [])
    if not urls:
        return ToolOutcome(
            summary="本轮没有可续抓的检索结果",
            detail=(
                "▍page_fetch：同轮 web_search 未产出可用链接（未启用、失败或无结果），"
                "因此没有正文可读。"
            ),
            data={"configured": True, "pages": []},
        )

    pages: list[dict[str, Any]] = []
    failures: list[str] = []
    for url in urls[: max(0, cfg.max_pages)]:
        try:
            pages.append(fetch_page(url))
        except WebUnavailable as error:
            failures.append(f"{url}（{error}）")

    if not pages:
        return ToolOutcome(
            summary=f"页面抓取全部失败（{len(failures)} 个链接）",
            detail="▍page_fetch：\n" + "\n".join(f"- {item}" for item in failures),
            data={"configured": True, "pages": [], "failures": failures},
        )

    lines: list[str] = []
    for index, page in enumerate(pages, start=1):
        lines.append(f"{index}. {page['title'] or '（无标题）'}｜{page['url']}")
        lines.append(page["text"][:1_200] + ("…（已截断）" if page["truncated"] else ""))
    if failures:
        lines.append("未抓取成功：" + "；".join(failures))
    lines.append("（来源：外部网页正文，非权威核实；引用时须带链接并标注为外部资料）")
    return ToolOutcome(
        summary=f"读取 {len(pages)} 个页面正文",
        detail="\n".join(lines),
        data={
            "configured": True,
            "pages": [{"url": p["url"], "title": p["title"]} for p in pages],
            "failures": failures,
        },
    )


#: ``site_monitor`` 单次最多抓取的页面数与单页注入的正文字数
#: （用户最多可配 20 个 URL，此处截前 8 页，与 page_fetch 的截断口径一致）
SITE_MONITOR_MAX_PAGES = 8
_SITE_PAGE_CHARS = 1_200


def site_monitor_tool(ctx: "AgentRunContext") -> ToolOutcome:
    """抓取用户在运行时设置里配置的监控页面正文（独立于搜索网关）。"""
    urls = list(get_config().search.site_urls)
    if not urls:
        return ToolOutcome(
            summary="未配置站点监控",
            detail=(
                "▍site_monitor：运行时设置里没有配置任何待爬页面 URL。\n"
                "本轮没有站点监控数据可用：**不得声称已抓取过外部网站、不得引用"
                "任何站点数据**。需要外部信息时按原有规则标注为「未核实 / 假设」"
                "并写入 risks。"
            ),
            data={"configured": False},
        )

    pages: list[dict[str, Any]] = []
    failures: list[str] = []
    for url in urls[:SITE_MONITOR_MAX_PAGES]:
        try:
            pages.append(fetch_page(url))
        except WebUnavailable as error:
            failures.append(f"{url}（{error}）")

    if not pages:
        return ToolOutcome(
            summary=f"站点监控页面抓取全部失败（{len(failures)} 个）",
            detail="▍site_monitor：\n" + "\n".join(f"- {item}" for item in failures),
            data={"configured": True, "pages": [], "failures": failures},
        )

    lines: list[str] = []
    for index, page in enumerate(pages, start=1):
        lines.append(f"{index}. {page['title'] or '（无标题）'}｜{page['url']}")
        lines.append(
            page["text"][:_SITE_PAGE_CHARS] + ("…（已截断）" if page["truncated"] else "")
        )
    if failures:
        lines.append("未抓取成功：" + "；".join(failures))
    lines.append(
        "（来源：用户配置站点的网页正文，非权威核实；引用时须带链接并标注为外部资料）"
    )
    return ToolOutcome(
        summary=f"读取 {len(pages)} 个监控页面正文",
        detail="\n".join(lines),
        data={
            "configured": True,
            "pages": [{"url": p["url"], "title": p["title"]} for p in pages],
            "failures": failures,
        },
    )


TOOLS: list[Tool] = [
    Tool(
        name="web_search",
        description="联网检索行业/受众/案例信息（未配置时如实返回「未联网」）",
        agent_ids=("A1", "A2", "A3", "A6", "A10"),
        handler=web_search_tool,
    ),
    Tool(
        name="page_fetch",
        description="读取本轮检索命中页面的正文（需 web_search 先执行）",
        agent_ids=("A1", "A2", "A3", "A6", "A10"),
        handler=page_fetch_tool,
    ),
    Tool(
        name="site_monitor",
        description="读取运行时设置里配置的监控页面正文（未配置时如实返回）",
        agent_ids=("A1", "A2", "A3", "A6", "A10"),
        handler=site_monitor_tool,
    ),
]

__all__ = ["TOOLS", "page_fetch_tool", "site_monitor_tool", "web_search_tool"]