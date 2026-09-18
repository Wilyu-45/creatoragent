"""确定性计算沙箱：受限表达式求值 + 指标公式表。

《plan.md》2.2.3 要求智能体「能自行使用工具完成计算」。但**开放任意 Python
执行会把创作链路变成远程代码执行面**：提示词里既有用户 Brief，也有联网抓回的
网页正文，任何一处都能注入代码。因此这里只提供受限求值：

* **允许**：数字字面量、变量引用、``+ - * / // % **``、一元正负、括号，
  以及白名单数学函数（min/max/abs/round/ceil/floor/sqrt/cbrt/exp/log/log2/log10/pow）；
* **禁止**：属性访问、下标、推导式、lambda、赋值、字符串操作、``import`` ——
  一律按 AST 节点类型白名单判定（不用正则黑名单：正则挡不住 ``getattr``
  这类间接绕过，白名单被绕过的前提是「多出一种节点类型」）；
* **规模上限**：表达式长度、AST 节点数、幂次与结果量级都有硬上限，
  避免 ``9**9**9`` 这类写法把进程算死。

失败一律抛 ``SandboxError``（``ValueError`` 子类），调用方按「工具失败」降级。
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

#: 表达式字符数上限
MAX_EXPRESSION_CHARS = 600

#: AST 节点数上限（复杂度闸门）
MAX_NODES = 200

#: 幂运算的指数与底数上限（防「大数连乘」把 CPU 吃满）
MAX_EXPONENT = 24.0
MAX_POW_BASE = 1e9

#: 结果量级上限
MAX_RESULT = 1e18


class SandboxError(ValueError):
    """沙箱拒绝执行或求值失败。"""


#: 允许的二元运算符 -> 实现（白名单之外的一律拒绝）
_BINOP_FUNCS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

#: 允许调用的数学函数（只认名字，不认属性调用）
_ALLOWED_FUNCS: dict[str, Any] = {
    "abs": abs,
    "cbrt": math.cbrt,
    "ceil": math.ceil,
    "exp": math.exp,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "max": max,
    "min": min,
    "pow": pow,
    "round": round,
    "sqrt": math.sqrt,
}

#: 投放/效果指标的公式表：名字 -> 只依赖沙箱语法的表达式。
#: 智能体据此换算时口径统一，避免各处「各算各的」。
FORMULAS: dict[str, str] = {
    "impressions": "cost / cpm * 1000",
    "clicks": "impressions * ctr",
    "conversions": "clicks * cvr",
    "revenue": "conversions * aov",
    "ctr": "clicks / impressions",
    "cvr": "conversions / clicks",
    "cpm": "cost / impressions * 1000",
    "cpc": "cost / clicks",
    "cpa": "cost / conversions",
    "roi": "(revenue - cost) / cost",
    "roas": "revenue / cost",
    "growth": "(current - previous) / previous",
}


def _normalize_variables(variables: dict[str, Any] | None) -> dict[str, float]:
    scope: dict[str, float] = {}
    for key, value in (variables or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SandboxError(f"变量 {key} 不是数值")
        number = float(value)
        if not math.isfinite(number):
            raise SandboxError(f"变量 {key} 不是有限实数")
        scope[str(key)] = number
    return scope


def _eval(node: ast.AST, scope: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise SandboxError(f"不支持的常量类型：{type(node.value).__name__}")
        return float(node.value)

    if isinstance(node, ast.Name):
        if node.id not in scope:
            raise SandboxError(f"未定义变量：{node.id}")
        return scope[node.id]

    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise SandboxError(f"不支持的一元运算：{type(node.op).__name__}")
        operand = _eval(node.operand, scope)
        return operand if isinstance(node.op, ast.UAdd) else -operand

    if isinstance(node, ast.BinOp):
        func = _BINOP_FUNCS.get(type(node.op))
        if func is None:
            raise SandboxError(f"不支持的运算符：{type(node.op).__name__}")
        left = _eval(node.left, scope)
        right = _eval(node.right, scope)
        if isinstance(node.op, ast.Pow) and (
            abs(right) > MAX_EXPONENT or abs(left) > MAX_POW_BASE
        ):
            raise SandboxError("幂运算超出安全范围")
        try:
            return float(func(left, right))
        except ZeroDivisionError as error:
            raise SandboxError("除数（或取模数）为零") from error
        except (OverflowError, ValueError) as error:
            raise SandboxError(f"运算失败：{error}") from error

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise SandboxError("只允许调用白名单数学函数，不支持属性调用")
        name = node.func.id
        if name not in _ALLOWED_FUNCS:
            raise SandboxError(f"函数不在白名单内：{name}")
        if node.keywords:
            raise SandboxError(f"{name} 不支持关键字参数")
        args = [_eval(arg, scope) for arg in node.args]
        try:
            return float(_ALLOWED_FUNCS[name](*args))
        except (TypeError, ValueError) as error:
            raise SandboxError(f"{name} 调用失败：{error}") from error

    raise SandboxError(f"不支持的语法：{type(node).__name__}")


def evaluate(expression: str, variables: dict[str, Any] | None = None) -> float:
    """在受限语法内求值，返回浮点结果。违规语法/未定义变量/非法结果一律抛错。"""
    text = (expression or "").strip()
    if not text:
        raise SandboxError("表达式为空")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise SandboxError(f"表达式过长（{len(text)} > {MAX_EXPRESSION_CHARS} 字符）")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as error:
        raise SandboxError(f"表达式语法错误：{error.msg}") from error

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise SandboxError(f"表达式过于复杂（{len(nodes)} > {MAX_NODES} 个节点）")

    result = _eval(tree.body, _normalize_variables(variables))
    if not math.isfinite(result):
        raise SandboxError("表达式结果不是有限实数")
    if abs(result) > MAX_RESULT:
        raise SandboxError(f"表达式结果超出量级上限（|结果| > {MAX_RESULT:.0e}）")
    return result


def apply_formula(name: str, variables: dict[str, Any]) -> float:
    """按公式表求值；公式名未知时抛 ``SandboxError``。"""
    expression = FORMULAS.get(name)
    if expression is None:
        raise SandboxError(f"未知公式：{name}（可用：{'、'.join(sorted(FORMULAS))}）")
    return evaluate(expression, variables)


def formula_names() -> list[str]:
    return sorted(FORMULAS)


__all__ = [
    "FORMULAS",
    "MAX_EXPRESSION_CHARS",
    "MAX_NODES",
    "SandboxError",
    "apply_formula",
    "evaluate",
    "formula_names",
]