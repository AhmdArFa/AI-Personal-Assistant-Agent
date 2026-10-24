"""Create Plan: turn an intent into ordered, tool bound steps."""

from __future__ import annotations

import re
from typing import Protocol

from .state import Intent, Observation, Plan, PlanStep
from .tools import ToolRegistry


class Planner(Protocol):
    def create_plan(
        self,
        intent: Intent,
        registry: ToolRegistry,
        observations: list[Observation],
    ) -> Plan: ...


class StaticPlanner:
    """Returns a fixed plan. Used by tests and by callers that
    already know the exact steps they want executed under limits."""

    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    def create_plan(self, intent, registry, observations) -> Plan:
        return self._plan


class RuleBasedPlanner:
    """Deterministic starter planner.

    Rules, in order:
    1. A calculate request with an arithmetic expression becomes
       one calculator step.
    2. A time or date request becomes one get_current_time step.
    3. Anything else becomes one echo step, so the loop always has
       a safe, observable behaviour while real skills are added.
    """

    _EXPR = re.compile(r"[-+*/().\d\s]+")

    def create_plan(self, intent, registry, observations) -> Plan:
        text = intent.goal
        lower = text.lower()
        steps: list[PlanStep] = []

        if "calculate" in lower:
            expr = text[lower.index("calculate") + len("calculate") :].strip(" :?")
            if expr and self._EXPR.fullmatch(expr) and any(c.isdigit() for c in expr):
                steps.append(
                    PlanStep(
                        id="step-calculate",
                        description=f"Calculate {expr}",
                        tool_name="calculator",
                        tool_args={"expression": expr},
                    )
                )
        if any(word in lower for word in ("time", "date", "today")) and registry.has(
            "get_current_time"
        ):
            steps.append(
                PlanStep(
                    id="step-time",
                    description="Get the current time",
                    tool_name="get_current_time",
                    tool_args={},
                )
            )
        if not steps:
            steps.append(
                PlanStep(
                    id="step-echo",
                    description="Echo the request back as an observed result",
                    tool_name="echo",
                    tool_args={"message": text},
                )
            )
        return Plan(goal=intent.goal, steps=steps)
