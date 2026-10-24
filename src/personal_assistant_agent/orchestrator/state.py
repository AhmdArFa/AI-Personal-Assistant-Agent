"""State and result types carried through one orchestrator run."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    COMPLETED = "completed"
    ASK_USER = "ask_user"
    FAILED = "failed"
    TIMEOUT = "timeout"
    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"
    PERMISSION_DENIED = "permission_denied"


@dataclass(frozen=True)
class TenantContext:
    """Authenticated identity for a run, supplied by the server
    side caller. It is never taken from the request text or from
    tool arguments (Blueprint section 6.2)."""

    user_id: str
    workspace_id: str


@dataclass
class Intent:
    """What the orchestrator understood from the user request."""

    goal: str
    kind: str = "task"
    raw_request: str = ""


@dataclass
class PlanStep:
    id: str
    description: str
    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)


@dataclass
class Observation:
    """The observed result of one tool execution attempt."""

    step_id: str
    tool_name: str
    success: bool
    result: Any = None
    error: str | None = None
    attempt: int = 1
    duration_seconds: float = 0.0


@dataclass
class AgentState:
    request: str
    tenant: TenantContext | None = None
    intent: Intent | None = None
    plan: Plan | None = None
    observations: list[Observation] = field(default_factory=list)
    steps_executed: int = 0
    replans_used: int = 0
    trace: list[str] = field(default_factory=list)

    def log(self, event: str) -> None:
        self.trace.append(event)

    def successful_results(self) -> list[Any]:
        return [o.result for o in self.observations if o.success]


@dataclass
class AgentResult:
    status: RunStatus
    answer: str | None
    state: AgentState
    reason: str = ""
    pending_step: PlanStep | None = None
