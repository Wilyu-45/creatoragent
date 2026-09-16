"""智能体工具层：每个创作智能体一组生成前可调用的工具。

用法（智能体 ``run()`` 内）::

    from ..tools import run_agent_tools

    tools = run_agent_tools(ctx, META)
    user = f"...{tools.prompt_block()}请输出..."
    result = call_with_prompts(..., {**context, "tools": tools.context()})

设计原则见 ``base.py`` 模块注释：离线优先、失败隔离、如实标注、一次执行。
"""

from .base import (  # noqa: F401
    Tool,
    ToolInvocation,
    ToolOutcome,
    ToolReport,
    all_tools,
    run_agent_tools,
    tool_catalog,
    tools_for,
)

__all__ = [
    "Tool",
    "ToolOutcome",
    "ToolInvocation",
    "ToolReport",
    "all_tools",
    "tools_for",
    "tool_catalog",
    "run_agent_tools",
]
