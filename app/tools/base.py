"""智能体工具层基础设施：工具注册表、执行器与调用报告。

对应《plan.md》2.2.3「MCP 工具接入层」的**内置替代实现**：每个创作智能体
声明一组自己的工具，在生成（LLM 调用）前批量执行，结果注入提示词与上下文。

设计原则（与项目工程原则一致）：

* **离线优先**——默认配置下所有工具基于内置知识库（``app/knowledge/*``）、A11 记忆库与
  可测量的文本特征，零外部依赖，同一输入结果可复现（黄金数据集的前提）；
  唯一的外部能力是**可选的联网检索**（``web_search``/``page_fetch``，默认关闭），
  未配置时如实返回「本轮未联网」而不是编造结果；
* **失败隔离**——单个工具抛异常只记录进报告，绝不阻断创作链路；
  「旁路能力不能成为创作链路的新失败面」；
* **如实标注**——工具结果必须带来源说明：内置知识不冒充联网核实，
  经验基准不冒充平台数据（与 A6/A7 的诚实性规则一致）；
* **一次执行**——工具在智能体 ``run()`` 内调用一次，多段生成（如 A8 的视频脚本）
  共用同一份报告，避免重复计算与事件噪声。

Mock 离线引擎只消费 context 中的特定 key（brief/memory 等），工具数据进入
context 后不会改变离线产出——因此本层对黄金基线零影响。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..core.tracing import tracer

if TYPE_CHECKING:  # 仅类型标注，避免运行时反向依赖
    from ..agents.base import AgentRunContext
    from ..core.types import AgentMeta


# ------------------------------------------------------------------ #
# 数据结构                                                            #
# ------------------------------------------------------------------ #


@dataclass
class ToolOutcome:
    """单个工具的执行产出。

    ``summary`` 是一句话摘要；``detail`` 是提示词就绪的渲染文本（工具自行控制
    展示粒度与截断）；``data`` 是结构化结果，进入 LLM context 供追踪与调试。
    """

    summary: str = ""
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


Handler = Callable[["AgentRunContext"], ToolOutcome]


@dataclass(frozen=True)
class Tool:
    """一个可被智能体调用的工具。

    ``agent_ids`` 声明该工具服务哪些智能体（plan.md 2.2.3 的映射关系），
    注册表据此为每个智能体组装工具集。
    """

    name: str
    description: str
    agent_ids: tuple[str, ...]
    handler: Handler


@dataclass
class ToolInvocation:
    """一次工具调用（含失败）。"""

    tool: str
    description: str
    ok: bool
    duration_ms: int
    summary: str = ""
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class ToolReport:
    """一个智能体一次执行的工具调用报告。"""

    agent_id: str
    invocations: list[ToolInvocation] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.invocations

    def prompt_block(self) -> str:
        """渲染进用户提示词的「工具情报」块；全部失败时返回空串。"""
        oks = [inv for inv in self.invocations if inv.ok]
        if not oks:
            return ""
        lines = [
            "【工具情报】以下结果由本智能体的工具在生成前检索/计算得出"
            "（来源说明见各工具条目，引用时须如实标注来源与口径）："
        ]
        for inv in oks:
            lines.append(f"▍工具 {inv.tool}：{inv.summary}")
            if inv.detail:
                lines.append(inv.detail)
        return "\n".join(lines) + "\n\n"

    def context(self) -> dict[str, Any]:
        """进入 LLMRequest.context 的结构化摘要（含失败记录，便于排障）。"""
        return {
            "calls": [
                {
                    "tool": inv.tool,
                    "ok": inv.ok,
                    "duration_ms": inv.duration_ms,
                    "summary": inv.summary,
                    **({"error": inv.error} if inv.error else {}),
                }
                for inv in self.invocations
            ],
            "data": {inv.tool: inv.data for inv in self.invocations if inv.ok and inv.data},
        }


# ------------------------------------------------------------------ #
# 注册表                                                              #
# ------------------------------------------------------------------ #


def _all_tools() -> list[Tool]:
    """汇总各智能体工具模块声明的工具（延迟导入避免循环依赖）。"""
    from . import (  # noqa: F401  局部导入
        a10_analyst,
        a11_memory,
        a1_strategy,
        a2_creative,
        a3_planner,
        a4_copywriter,
        a5_editor,
        a6_factchecker,
        a7_compliance,
        a8_art_director,
        a9_channel_seo,
        compute,
        web_research,
    )

    modules = (
        a1_strategy,
        a2_creative,
        a3_planner,
        a4_copywriter,
        a5_editor,
        a6_factchecker,
        a7_compliance,
        a8_art_director,
        a9_channel_seo,
        a10_analyst,
        a11_memory,
        # 确定性计算与联网检索排在最后：各智能体原有的离线工具保持既有顺序，
        # 保证「未配置联网」时提示词里的情报块顺序与历史一致
        compute,
        web_research,
    )
    tools: list[Tool] = []
    for module in modules:
        tools.extend(getattr(module, "TOOLS", []))
    return tools


_TOOLS_CACHE: list[Tool] | None = None


def all_tools() -> list[Tool]:
    global _TOOLS_CACHE
    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = _all_tools()
    return _TOOLS_CACHE


def tools_for(agent_id: "str | AgentMeta") -> list[Tool]:
    """返回声明给该智能体的工具集（保持声明顺序）。

    ``agent_id`` 可传 ``AgentMeta`` 对象（智能体模块里的 ``META``）或其 id 字符串，
    这里统一归一化，避免调用方误传对象时工具集静默为空。
    """
    normalized = getattr(agent_id, "id", agent_id)
    if not isinstance(normalized, str):
        normalized = str(normalized)
    return [tool for tool in all_tools() if normalized in tool.agent_ids]


def tool_catalog() -> list[dict[str, Any]]:
    """全部工具的目录（供自检/接口展示用）。"""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "agent_ids": list(tool.agent_ids),
        }
        for tool in all_tools()
    ]


# ------------------------------------------------------------------ #
# 执行器                                                              #
# ------------------------------------------------------------------ #


def recommended_draft(
    ctx: "AgentRunContext",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """取上游「文案草稿」的主推版本与全部版本，供多个智能体的工具复用。

    A5/A7/A8/A9/A10 都要在「同一处语义」上取主推版本；口径散落在各工具模块里
    容易悄悄跑偏（例如一处默认 ``V1``、另一处默认 ``V2``），因此统一在此定义。
    """
    from ..llm.json_utils import as_obj_array, as_str

    draft = ctx.upstream_of("draft")
    recommended = as_str(draft.get("recommended_version"), "V1")
    versions = as_obj_array(draft.get("versions"))
    target = next((item for item in versions if as_str(item.get("id")) == recommended), {})
    return target, versions


def run_agent_tools(ctx: "AgentRunContext", agent_id: "str | AgentMeta") -> ToolReport:
    """执行声明给 ``agent_id`` 的全部工具，返回调用报告。

    ``agent_id`` 可传 ``AgentMeta`` 对象（智能体模块里的 ``META``）或其 id 字符串。
    每个工具独立 try/except + 计时；失败记录在报告中但不抛出。
    整组调用包在一个 ``tools.*`` span 里，并在事件流里发一条聚合进度，
    不逐工具刷屏。
    """
    tools = tools_for(agent_id)
    agent_key = str(getattr(agent_id, "id", agent_id))
    report = ToolReport(agent_id=agent_key)
    if not tools:
        return report

    with tracer.span(
        f"tools.{agent_key}",
        agent_id=agent_key,
        attributes={"tools.planned": [tool.name for tool in tools]},
    ) as span:
        for tool in tools:
            started = time.perf_counter()
            try:
                outcome = tool.handler(ctx)
                invocation = ToolInvocation(
                    tool=tool.name,
                    description=tool.description,
                    ok=True,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    summary=outcome.summary,
                    detail=outcome.detail,
                    data=outcome.data,
                )
            except Exception as error:  # noqa: BLE001  工具失败绝不阻断创作
                invocation = ToolInvocation(
                    tool=tool.name,
                    description=tool.description,
                    ok=False,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error=f"{type(error).__name__}: {error}",
                )
            report.invocations.append(invocation)
        ok_count = sum(1 for inv in report.invocations if inv.ok)
        tracer.finish_span(
            span,
            status="ok" if ok_count == len(report.invocations) else "warn",
            attributes={"tools.ok": ok_count, "tools.failed": len(report.invocations) - ok_count},
        )

    failed = [inv for inv in report.invocations if not inv.ok]
    ctx.emit(
        "已调用工具：" + "、".join(inv.tool for inv in report.invocations)
        + (f"（{len(failed)} 个失败已降级）" if failed else ""),
        {
            "agent_id": agent_key,
            "tools": [
                {"tool": inv.tool, "ok": inv.ok, "duration_ms": inv.duration_ms}
                for inv in report.invocations
            ],
        },
    )
    return report


__all__ = [
    "Tool",
    "ToolOutcome",
    "ToolInvocation",
    "ToolReport",
    "all_tools",
    "tools_for",
    "tool_catalog",
    "recommended_draft",
    "run_agent_tools",
]
