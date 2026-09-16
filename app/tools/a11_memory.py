"""A11 记忆与知识库智能体的工具集。

记忆运维类工具：知识库统计（容量/类型分布/新鲜度）与本次任务的归档统计。
检索与写入（retrieve/remember）仍由 A11 主流程与编排层负责，不在此重复。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..knowledge.memory import memory_store
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def library_stats(ctx: "AgentRunContext") -> ToolOutcome:
    """知识库统计：容量占用、类型分布、新鲜度（按当前租户隔离）。"""
    stats = memory_store.stats(tenant=ctx.tenant)
    by_kind = "、".join(
        f"{item['label']} {item['count']} 张"
        for item in stats.get("by_kind", [])
        if item.get("count")
    )
    lines = [
        f"库容量：{stats.get('total', 0)}/{stats.get('capacity', 0)} 张"
        f"（全局 {stats.get('global_total', 0)} 张）",
        f"类型分布：{by_kind or '（空）'}",
        f"新鲜度：{stats.get('fresh', 0)} 张新鲜（≤{stats.get('fresh_days', 30)} 天）、"
        f"{stats.get('stale', 0)} 张陈旧（>{stats.get('max_age_days', 180)} 天过期下线）",
        f"复用情况：{stats.get('reused', 0)} 张曾被召回复用",
        "（来源：A11 记忆库实时统计——沉淀与淘汰决策请以此为据）",
    ]
    return ToolOutcome(
        summary=f"知识库 {stats.get('total', 0)} 张卡片，{stats.get('reused', 0)} 张被复用过",
        detail="\n".join(lines),
        data={key: stats.get(key) for key in ("total", "global_total", "capacity", "by_kind", "fresh", "stale", "reused")},
    )


def archive_stats(ctx: "AgentRunContext") -> ToolOutcome:
    """本次任务的归档统计：产物数量、类型、返工轮次（以黑板真实数据为准）。"""
    artifact_types = sorted({item.type for item in ctx.artifacts})
    lines = [
        f"本次任务产物 {len(ctx.artifacts)} 件：{'、'.join(artifact_types) or '（无）'}",
        f"返工轮次：{ctx.revision}",
        "（来源：黑板产物快照——archive 字段以该统计为准，不要自述数字）",
    ]
    return ToolOutcome(
        summary=f"归档 {len(ctx.artifacts)} 件产物、返工 {ctx.revision} 轮",
        detail="\n".join(lines),
        data={
            "artifact_count": len(ctx.artifacts),
            "artifact_types": artifact_types,
            "revision_rounds": ctx.revision,
        },
    )


TOOLS: list[Tool] = [
    Tool(
        name="library_stats",
        description="知识库统计（容量/类型分布/新鲜度，按租户隔离）",
        agent_ids=("A11",),
        handler=library_stats,
    ),
    Tool(
        name="archive_stats",
        description="本次任务的归档统计（产物数/类型/返工轮次）",
        agent_ids=("A11",),
        handler=archive_stats,
    ),
]

__all__ = ["TOOLS", "library_stats", "archive_stats"]
