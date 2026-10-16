"""岗位节点包：导入即注册。

新增加一个部门模块时，把模块名补进 ``_MODULES``——这里用显式列表而不是
动态扫描，保证导入顺序固定（注册表重名会直接报错，顺序不稳定会让报错随机）。
"""

from __future__ import annotations

from importlib import import_module

_MODULES = (
    "source",
    "dev",
    "writing",
    "directing",
    "art",
    "camera",
    "sound",
    "gen",
    "post",
    "qc",
)

for _name in _MODULES:
    import_module(f"{__name__}.{_name}")
