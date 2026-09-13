"""跨层通用小工具。"""

from __future__ import annotations

import math
import socket
import tempfile
import uuid
from pathlib import Path


def free_port(preferred: int | None = None) -> int:
    """返回一个可绑定的端口。

    为什么不写死端口：Windows/Hyper-V 会保留成片的端口区间（``netsh int ipv4
    show excludedportrange protocol=tcp``），写死的端口在别的机器上可能直接
    ``PermissionError: [Errno 13] ... bind``。测试脚本因此吃过亏（CI 通过、
    本机失败，或反之）。

    做法是先试 ``preferred``，失败就交给操作系统分配（bind 0）。
    **注意**：拿到的端口在真正 bind 之前仍可能被别人抢走，
    因此脚本里把它当作「大概率可用」而非保证。
    """
    if preferred:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def force_offline_provider(env: dict | None = None, *, override_var: str = "CREATOR_VERIFY_PROVIDER") -> str:
    """把验证脚本固定到内置离线引擎，返回最终生效的提供方名。

    **为什么验证脚本必须强制离线**：它们断言的是契约与逻辑（span 树层级、
    黄金判定器、租户隔离、跨租户 404…），这些必须在**秒级、可复现、无外部依赖**
    且不花钱的条件下成立。若本机 ``.env`` 指向真实网关，验证脚本会跟着走真实模型，
    于是同时踩三个坑（实测）：11 条黄金用例跑不完就超时、同一条断言今天过明天不过、
    每次验证都真的产生费用。

    需要在真实链路上验证时用 ``scripts/real_check.py``，
    或显式设 ``CREATOR_VERIFY_PROVIDER=openai``（会明显变慢且花钱）。

    * ``env`` 传入时会就地写入（供 ``subprocess`` 启动服务端用）；
      不传则写进程环境变量（供直接 import ``app.*`` 的脚本用）。
    * 必须在导入 ``app.config`` **之前**调用 —— 它在 import 时读取环境变量。
    """
    import os

    requested = (os.environ.get(override_var) or "").strip().lower()
    provider = requested if requested in ("mock", "openai") else "mock"
    target = env if env is not None else os.environ
    target["LLM_PROVIDER"] = provider
    return provider


def js_round(value: float) -> int:
    """等价于 JS 的 ``Math.round``（.5 一律向上取整）。

    Python 内建 ``round`` 用的是银行家舍入（round-half-even），
    在 ``0.5``、``1.5`` 这类边界上会与旧 Node 实现产生 1 分差异，
    而质量分是要给用户看的，必须逐分对齐。
    """
    return int(math.floor(float(value) + 0.5))


def make_temp_dir(prefix: str = "creator-", *, base: Path | None = None) -> Path:
    """创建一个**写入可用**的临时目录。

    为什么不直接用 ``tempfile.mkdtemp``：它以 ``mode=0o700`` 建目录，在
    Windows 上会落成一条「仅创建者可访问」的 ACL。受限沙箱（以及部分企业
    安全策略）下，子进程随后往里写文件会直接 ``PermissionError`` ——
    表现为「目录明明存在、就是写不进去」，极难定位（本项目自检就曾被它骗过：
    记忆库落盘静默失败、SQLite 检查点退回内存实现，而自检依然全绿）。

    这里改为逐级 ``mkdir``（继承父目录 ACL）并加上随机后缀，
    既保留隔离性，又不引入那条刁钻的 ACL。
    """
    root = Path(base) if base is not None else Path(tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(16):
        candidate = root / f"{prefix}{uuid.uuid4().hex[:8]}"
        try:
            candidate.mkdir()  # 已存在则抛 FileExistsError，换一个名字重试
        except FileExistsError:
            continue
        return candidate
    raise OSError(f"无法在 {root} 下创建临时目录（重试 16 次均冲突）")


__all__ = ["force_offline_provider", "free_port", "js_round", "make_temp_dir"]
