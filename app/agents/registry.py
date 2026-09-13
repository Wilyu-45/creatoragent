"""智能体注册表（移植自 server/agents/registry.ts）。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .a1_strategy import a1_strategy
from .a2_creative import a2_creative
from .a3_planner import a3_planner
from .a4_copywriter import a4_copywriter
from .a5_editor import a5_editor
from .a6_factchecker import a6_factchecker
from .a7_compliance import a7_compliance
from .a8_art_director import a8_art_director
from .a9_channel_seo import a9_channel_seo
from .a10_analyst import a10_analyst
from .a11_memory import a11_memory
from .base import AgentDefinition

#: 已实现的智能体，顺序与流水线执行顺序一致（UI 依赖该顺序展示）。
AGENTS: dict[str, AgentDefinition] = {
    "A1": a1_strategy,
    "A2": a2_creative,
    "A3": a3_planner,
    "A4": a4_copywriter,
    "A5": a5_editor,
    "A6": a6_factchecker,
    "A7": a7_compliance,
    "A8": a8_art_director,
    "A9": a9_channel_seo,
    "A10": a10_analyst,
    "A11": a11_memory,
}

#: 规划中但尚未实现的智能体，用于在界面上展示路线图。
PLANNED_AGENTS: list[dict[str, str]] = [
    {
        "id": "A0",
        "name": "总控编排智能体",
        "role": "项目经理 / 总控",
        "description": "拆解任务、调度智能体、控制流程与门禁（由编排引擎承担）",
    },
]


def get_agent(agent_id: str) -> AgentDefinition | None:
    return AGENTS.get(agent_id)


def all_agent_meta() -> list[dict[str, Any]]:
    return [{**asdict(agent.meta), "implemented": True} for agent in AGENTS.values()]


__all__ = ["AGENTS", "PLANNED_AGENTS", "get_agent", "all_agent_meta"]
