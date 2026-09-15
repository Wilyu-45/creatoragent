"""PostgreSQL 连接池（仅 ``CREATOR_STORAGE=pg`` 时使用）。

零依赖策略
----------
本模块只在 pg 模式下被 import（各存储单例的条件分支内，函数体延迟 import），
默认 file 模式（JSON + SQLite）下 psycopg 永不加载 —— 未安装也能跑。

fail-loud
---------
pg 模式下「数据库连不上应该起不来」而不是悄悄丢持久化：池在首次获取时创建，
创建/连通失败直接抛异常。orchestrator 的 checkpointer 与 lifespan 的
``ensure_pg_schema()`` 都会触发这条路径，进程启动即失败并留下明确报错。

环境变量
--------
* ``CREATOR_DATABASE_URL``       连接串，缺省连本地 docker compose 的库
* ``CREATOR_PG_POOL_MIN``        池最小连接数（默认 1）
* ``CREATOR_PG_POOL_MAX``        池最大连接数（默认 8）
* ``CREATOR_PG_CONNECT_TIMEOUT`` 建连/取连接超时秒数（默认 5）
"""

from __future__ import annotations

import os
import threading
from urllib.parse import urlparse, urlunparse

from ..config import DATABASE_URL

#: 未配置连接串时的本地默认值（与 docker-compose --profile pg 的服务对齐）
DEFAULT_DATABASE_URL = "postgresql://creator:creator@localhost:5432/creator"

_pool = None
_pool_lock = threading.RLock()


def database_url() -> str:
    """返回有效连接串（env 优先，缺省本地库）。"""
    return DATABASE_URL or DEFAULT_DATABASE_URL


def mask_url(url: str) -> str:
    """掩码连接串中的密码，供日志与报错使用（绝不回传明文密钥）。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "***"
    if not parsed.password:
        return url
    netloc = f"{parsed.username or ''}:***@{parsed.hostname or ''}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _pool_settings() -> dict:
    min_size = max(0, int(os.environ.get("CREATOR_PG_POOL_MIN") or 1))
    max_size = max(1, int(os.environ.get("CREATOR_PG_POOL_MAX") or 8))
    timeout = max(1, int(os.environ.get("CREATOR_PG_CONNECT_TIMEOUT") or 5))
    return {"min_size": min_size, "max_size": max_size, "timeout": timeout}


def pg_pool():
    """进程级 ``psycopg_pool.ConnectionPool`` 单例（懒建；连不上直接抛异常）。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        from psycopg_pool import ConnectionPool

        settings = _pool_settings()
        pool = ConnectionPool(
            conninfo=database_url(),
            name="creator-pg",
            min_size=settings["min_size"],
            max_size=settings["max_size"],
            timeout=settings["timeout"],
            # autocommit=True 是 langgraph PostgresSaver 的硬性要求：其 setup()
            # 含 CREATE INDEX CONCURRENTLY，不允许运行在事务块内（否则启动即失败）。
            # 代价是多语句原子性不再「隐式」成立 —— 需要原子的语句序列必须显式
            # 用 conn.transaction() 包裹（见 blackboard_pg.next_version）。
            # prepare_threshold=0 禁用预编译语句，兼容 pgbouncer 等连接池中间件。
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "connect_timeout": settings["timeout"],
            },
            # 归还连接时做一次轻量校验，坏连接（被服务端断开）直接丢弃重建
            check=ConnectionPool.check_connection,
        )
        pool.wait(timeout=settings["timeout"])  # 至少一条连接就绪才算成功 —— fail-loud
        _pool = pool
        return _pool
