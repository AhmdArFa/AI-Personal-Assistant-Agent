"""State and result types carried through one orchestrator run."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    """Task lifecycle statuses, aligned with Blueprint section 7.2."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(str, Enum):
    """Why a run stopped. Kept separate from the lifecycle status
    so a failed task still says exactly which limit or rule fired."""

    GOAL_ACHIEVED = "goal_achieved"
    APPROVAL_REQUIRED = "approval_required"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    STEP_LIMIT = "step_limit_exceeded"
    VALIDATION_ERROR = "validation_error"
    UNKNOWN_TOOL = "unknown_tool"
    RETRIES_EXHAUSTED = "retries_exhausted"
    GOAL_NOT_ACHIEVED = "goal_not_achieved"
    INTERNAL_ERROR = "internal_error"
    CANCELLED = "cancelled"


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
    status: RunStatus = RunStatus.QUEUED
    task_id: str = ""
    cancellation_token: object | None = None
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
    stop_reason: StopReason | None = None
    error_summary: str = ""
