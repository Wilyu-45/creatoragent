"""FastAPI 应用装配（对应 server/index.ts）。

职责：
- 挂载 ``/api`` 路由与生产环境的 ``/dist`` 静态资源
- 启动时载入历史任务/黑板，并对中断任务尝试断点续跑
- 退出时刷盘
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import create_api_router
from .config import API_TOKENS, ROOT_DIR, STORAGE_MODE, ensure_dirs, get_config
from .core import otel
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

    # pg 模式启动自检（fail-loud）：schema 建齐 + Redis 连通。任一失败直接终止
    # 启动 —— 多副本下静默降级会让各副本状态分裂，比单机不可用更危险
    # （契约 storage_contract.md「移除静默退回」）。
    if STORAGE_MODE == "pg":
        from .core.pg_schema import ensure_pg_schema
        from .core.redis_client import ping_redis

        try:
            ensure_pg_schema()
            ping_redis()
        except Exception as error:  # noqa: BLE001
            log.error("存储层启动自检失败，拒绝启动（fail-loud）", error)
            raise
        log.info("存储层自检通过：PostgreSQL schema 就绪、Redis 连通")

    info: dict[str, Any] = task_store.load_all()
    blackboard.load()

    # 追踪：只在配置了 OTLP_ENDPOINT 时才加载 OTel SDK（默认零外部依赖）
    tracing_cfg = get_config().tracing
    if tracing_cfg.otlp_endpoint:
        if not otel.setup(
            endpoint=tracing_cfg.otlp_endpoint,
            service_name=tracing_cfg.service_name,
            headers=tracing_cfg.otlp_headers,
        ):
            log.warn("OTLP_ENDPOINT 已配置但初始化失败，继续使用进程内追踪")
    else:
        log.info("OTLP 导出未启用（进程内 span 树仍完整可用；配置 OTLP_ENDPOINT 即可转发）")

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

    # 发布自动投递：仅在显式开启时启动后台巡检，默认关闭以保持进程「零副作用」
    stop = asyncio.Event()
    ticker: asyncio.Task[None] | None = None
    if config.publish.auto_dispatch:
        interval = max(5, config.publish.tick_seconds)

        async def _publish_loop() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    result = await asyncio.to_thread(orchestrator.dispatch_due)
                    if result["dispatched"] or result["failed"]:
                        log.info(
                            f"自动投递：成功 {len(result['dispatched'])}｜失败 {len(result['failed'])}"
                        )
                except Exception as error:  # noqa: BLE001 - 巡检失败不能拖垮服务
                    log.warn("自动投递巡检失败", error)

        ticker = asyncio.create_task(_publish_loop())
        log.info(f"发布自动投递已启用：每 {interval}s 巡检一次到期排期")

    try:
        yield
    finally:
        stop.set()
        if ticker is not None:
            ticker.cancel()
        log.info("正在优雅退出…")
        # 先 flush OTLP 再落盘：BatchSpanProcessor 里可能还有未发送的 span
        otel.shutdown()
        task_store.flush_all()
        blackboard.flush()
        log.info("数据已保存，进程退出")


def _install_auth(app: FastAPI) -> None:
    """按 ``CREATOR_API_TOKENS`` 开启 API 鉴权（plan.md D17「安全加固」）。

    未配置 token 时不挂载中间件，保持单机零配置体验；
    配置后 ``/api/*``（``/api/health`` 除外）必须携带
    ``Authorization: Bearer <token>`` 或 ``X-API-Token: <token>``，
    并把 token 对应的租户写入 ``request.state.tenant`` 供路由做数据隔离。
    """
    if not API_TOKENS:
        return

    @app.middleware("http")
    async def api_token_guard(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if path != "/api/health" and path.startswith("/api/"):
            header = request.headers.get("authorization") or ""
            token = header[7:].strip() if header.lower().startswith("bearer ") else ""
            if not token:
                token = (request.headers.get("x-api-token") or "").strip()
            if not token:
                # SSE（EventSource）无法自定义请求头，允许用 query 参数携带
                token = (request.query_params.get("token") or "").strip()
            tenant = API_TOKENS.get(token)
            if tenant is None:
                return JSONResponse({"detail": "无效或缺失的 API Token"}, status_code=401)
            request.state.tenant = tenant
        return await call_next(request)

    log.info(f"API 鉴权已启用：{len(API_TOKENS)} 个 token（/api/health 免鉴权）")


def create_app() -> FastAPI:
    app = FastAPI(title="Creator Agent Studio", version="0.1.0", lifespan=lifespan)
    _install_auth(app)
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
