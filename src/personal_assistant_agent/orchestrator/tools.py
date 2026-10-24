"""Tool registry: every tool the orchestrator may choose from.

A tool is real code that does one thing. It is registered with a name,
a description the planner can read, an input schema, and a permission
level. The orchestrator never calls anything that is not registered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .permissions import PermissionLevel

ToolHandler = Callable[[dict[str, Any]], Any]


class UnknownToolError(KeyError):
    """Raised when a plan names a tool that is not registered."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    permission: PermissionLevel
    handler: ToolHandler
    input_schema: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool: {name}") from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[name] for name in self.names()]
