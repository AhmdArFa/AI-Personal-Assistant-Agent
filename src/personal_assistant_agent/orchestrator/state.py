"""State and result types carried through one orchestrator run."""

from __future__ import annotations

import uuid
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
    BUDGET_EXCEEDED = "budget_exceeded"
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


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


@dataclass
class ApprovalRecord:
    """One approval decision, bound to the exact tool arguments
    that were reviewed, by hash (Blueprint section 14.3)."""

    step_id: str
    tool_name: str
    arguments_hash: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    decided_by: str = ""
    created_at: str = field(default_factory=_now_iso)
    decided_at: str = ""


@dataclass
class AgentState:
    request: str
    tenant: TenantContext | None = None
    status: RunStatus = RunStatus.QUEUED
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancellation_token: object | None = None
    intent: Intent | None = None
    plan: Plan | None = None
    observations: list[Observation] = field(default_factory=list)
    approvals: list[ApprovalRecord] = field(default_factory=list)
    approved_arguments: dict[str, str] = field(default_factory=dict)
    steps_executed: int = 0
    replans_used: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    cost_used: float = 0.0
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
