"""Built in starter tools: safe, read only, and real.

These exist so the orchestrator can be run and tested end to end
on day one. Real tool families (calendar, mail, tasks, web, files,
meetings, memory) register the same way in later steps.
"""

from __future__ import annotations

import ast
import operator
from datetime import UTC, datetime

from pydantic import Field

from .permissions import PermissionLevel
from .tools import EmptyInput, StrictInput, Tool, ToolRegistry

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
}


class EchoInput(StrictInput):
    message: str = ""


class CalculatorInput(StrictInput):
    expression: str = Field(min_length=1)


def _eval_expr(node):
    if isinstance(node, ast.Expression):
        return _eval_expr(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_expr(node.left), _eval_expr(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_expr(node.operand))
    raise ValueError("unsupported expression")


def _calculator(args: CalculatorInput, context):
    return _eval_expr(ast.parse(args.expression, mode="eval"))


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo",
            description="Return the given message unchanged.",
            permission=PermissionLevel.READ,
            handler=lambda args, context: str(args.message),
            input_model=EchoInput,
        )
    )
    registry.register(
        Tool(
            name="get_current_time",
            description="Return the current UTC date and time in ISO format.",
            permission=PermissionLevel.READ,
            handler=lambda args, context: datetime.now(UTC).isoformat(),
            input_model=EmptyInput,
        )
    )
    registry.register(
        Tool(
            name="calculator",
            description="Safely evaluate a basic arithmetic expression.",
            permission=PermissionLevel.READ,
            handler=_calculator,
            input_model=CalculatorInput,
        )
    )
    return registry
