"""Creator Agent Studio —— Python 后端（FastAPI + LangGraph）。

目录结构：
    app/config.py      运行时配置
    app/core/          共享黑板、事件总线、持久化、门禁状态机、LangGraph 编排
    app/llm/           模型接入层（OpenAI 兼容 + 离线 Mock 引擎）
    app/knowledge/     知识层（渠道规范 / 行业洞察 / 广告法词库）
    app/agents/        智能体 A1-A7、A10
    app/api/           REST + SSE 路由
"""

__version__ = "1.0.0"
