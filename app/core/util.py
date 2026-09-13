"""跨层通用小工具。"""

from __future__ import annotations

import math
import tempfile
import uuid
from pathlib import Path


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
