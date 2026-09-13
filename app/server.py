"""FastAPI 应用装配（对应 server/index.ts）。

职责：
- 挂载 ``/api`` 路由与生产环境的 ``/dist`` 静态资源
- 启动时载入历史任务/黑板，并对中断任务尝试断点续跑
- 退出时刷盘
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import create_api_router
from .config import ROOT_DIR, ensure_dirs, get_config
from .core.blackboard import blackboard
from .core.orchestrator import orchestrator
from .core.store import task_store
from .llm import resolve_provider
from .logger import create_logger

log = create_logger("server")

DIST_DIR = ROOT_DIR / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ensure_dirs()
    info: dict[str, Any] = task_store.load_all()
    blackboard.load()

    interrupted = list(info.get("interrupted") or [])
    if interrupted:
        resumed = orchestrator.resume_interrupted(interrupted)
        log.info(f"断点续跑：{resumed}/{len(interrupted)} 个中断任务已恢复执行")

    config = get_config()
    provider = resolve_provider()
    log.info(f"Creator Agent Studio 服务端已就绪: http://127.0.0.1:{config.port}")
    log.info(
        f"LLM 提供方: {provider.name} / {provider.model}"
        + ("（内置离线引擎，无需密钥）" if getattr(provider, "simulated", True) else "")
    )
    log.info(
        f"门禁阈值: 质量分 ≥ {config.quality_threshold}｜最大返工 {config.max_revisions} 轮"
        f"｜Turn Budget {config.turn_budget}"
    )

    try:
        yield
    finally:
        log.info("正在优雅退出…")
        task_store.flush_all()
        blackboard.flush()
        log.info("数据已保存，进程退出")


def create_app() -> FastAPI:
    app = FastAPI(title="Creator Agent Studio", version="0.1.0", lifespan=lifespan)
    app.include_router(create_api_router(), prefix="/api")

    dist = DIST_DIR
    assets = dist / "assets"
    if dist.is_dir():
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            # /api 下未命中的路径应保持 404，而不是回落到前端页面
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")

            root = dist.resolve()
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and root in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

        log.info("已挂载前端静态资源 /dist")
    else:
        log.warn("未找到 /dist，开发模式下请使用 Vite 开发服务器（npm run dev:web）")

    return app


__all__ = ["create_app", "DIST_DIR"]
