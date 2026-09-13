"""跨层通用小工具。"""

from __future__ import annotations

import math


def js_round(value: float) -> int:
    """等价于 JS 的 ``Math.round``（.5 一律向上取整）。

    Python 内建 ``round`` 用的是银行家舍入（round-half-even），
    在 ``0.5``、``1.5`` 这类边界上会与旧 Node 实现产生 1 分差异，
    而质量分是要给用户看的，必须逐分对齐。
    """
    return int(math.floor(float(value) + 0.5))
