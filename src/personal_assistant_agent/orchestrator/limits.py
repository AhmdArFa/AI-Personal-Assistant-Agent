"""Execution limits for the Agent Orchestrator.

The orchestrator is the controller, so it must never run forever,
never take unlimited steps, and never call a tool without bounds.
These limits are enforced before and during every step.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrchestratorLimits:
    """Hard bounds on one orchestrator run."""

    max_steps: int = 12
    max_execution_seconds: float = 60.0
    max_retries_per_step: int = 2
    max_replans: int = 2
    per_tool_timeout_seconds: float = 15.0
    max_tool_calls: int = 50
    max_output_chars: int = 20_000
    max_tokens: int | None = None
    max_cost: float | None = None

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if self.max_execution_seconds <= 0:
            raise ValueError("max_execution_seconds must be positive")
        if self.max_retries_per_step < 0:
            raise ValueError("max_retries_per_step cannot be negative")
        if self.max_replans < 0:
            raise ValueError("max_replans cannot be negative")
        if self.per_tool_timeout_seconds <= 0:
            raise ValueError("per_tool_timeout_seconds must be positive")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if self.max_output_chars < 1:
            raise ValueError("max_output_chars must be at least 1")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1 when set")
        if self.max_cost is not None and self.max_cost <= 0:
            raise ValueError("max_cost must be positive when set")
