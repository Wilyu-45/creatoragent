"""Redis 客户端（仅 ``CREATOR_STORAGE=pg`` 时使用）：意图租约的权威存储。

零依赖策略与 :mod:`app.core.pg` 相同：只在 pg 模式的条件分支内被 import。

租约语义
--------
黑板的意图租约在多副本下必须跨进程互斥（file 版是进程内 ``threading.RLock``，
多副本下两进程会各自拿到同一把「锁」）。Redis ``SET key value PX ttl`` 的
TTL 过期天然就是文件版 ``_sweep_expired_intents`` 的等价物 —— 到期自动消失，
无需清扫线程。三态原子判定用 Lua 脚本实现（见 ``blackboard_pg.py``）。

环境变量
--------
* ``CREATOR_REDIS_URL``    连接串，缺省连本地 docker compose 的 Redis
* ``CREATOR_REDIS_PREFIX`` key 前缀（默认 ``creator:``），多套环境共用 Redis 时区分
"""

from __future__ import annotations

import os
import threading

from ..config import REDIS_URL

#: 未配置连接串时的本地默认值（与 docker-compose --profile pg 的服务对齐）
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

_CLIENT = None
_CLIENT_LOCK = threading.RLock()


def redis_url() -> str:
    """返回有效连接串（env 优先，缺省本地 Redis）。"""
    return REDIS_URL or DEFAULT_REDIS_URL


def redis_prefix() -> str:
    """key 前缀；非空时保证以 ``:`` 结尾，拼 key 时直接 f-string 连接。"""
    raw = (os.environ.get("CREATOR_REDIS_PREFIX") or "creator:").strip()
    if not raw:
        return ""
    return raw if raw.endswith(":") else f"{raw}:"


def redis_client():
    """进程级 redis 客户端单例（懒建；不在此处 ping —— 连通性由调用方 fail-loud 校验）。"""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            import redis as redis_lib

            _CLIENT = redis_lib.Redis.from_url(
                redis_url(),
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return _CLIENT


def ping_redis() -> None:
    """连通性自检：lifespan 启动时调用，连不上直接抛异常（fail-loud）。"""
    redis_client().ping()
