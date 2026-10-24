"""Agent Orchestrator: The Controller.

Manages the complete execution cycle:

    User Request
        -> Understand Intent
        -> Create Plan
        -> Choose Tool
        -> Execute Tool
        -> Observe Result
        -> Is the Goal Achieved?
            No  -> Continue, Retry, or Ask the User
            Yes -> Verify and Return Result

And enforces, on every run:
    - a limit on total execution time,
    - a limit on the number of steps,
    - tool permission checks, with human approval for anything
      above the auto allowed level.

Run statuses follow the task state model in Blueprint section 7.2:
queued, running, waiting_for_approval, completed, failed, cancelled.
Why a run stopped lives in StopReason, separate from the status.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from pydantic import ValidationError

from .intent import IntentUnderstanding, RuleBasedIntentUnderstanding
from .limits import OrchestratorLimits
from .permissions import PermissionPolicy
from .planner import Planner, RuleBasedPlanner
from .state import (
    AgentResult,
    AgentState,
    Observation,
    PlanStep,
    RunStatus,
    StopReason,
    TenantContext,
)
from .tools import Tool, ToolRegistry, UnknownToolError
from .verifier import DefaultVerifier, Verifier

ApprovalCallback = Callable[[PlanStep, Tool], bool]


class AgentOrchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        limits: OrchestratorLimits | None = None,
        policy: PermissionPolicy | None = None,
        planner: Planner | None = None,
        intent_understanding: IntentUnderstanding | None = None,
        verifier: Verifier | None = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        self.registry = registry
        self.limits = limits or OrchestratorLimits()
        self.policy = policy or PermissionPolicy()
        self.planner = planner or RuleBasedPlanner()
        self.intent_understanding = (
            intent_understanding or RuleBasedIntentUnderstanding()
        )
        self.verifier = verifier or DefaultVerifier()
        self.approval_callback = approval_callback

    # ---------------------------------------------------------------
    def run(self, request: str, context: TenantContext | None = None) -> AgentResult:
        if context is None:
            raise ValueError(
                "an authenticated TenantContext is required: identity "
                "is never taken from the request text"
            )
        started = time.monotonic()
        state = AgentState(request=request, tenant=context)
        state.status = RunStatus.RUNNING
        state.log("request_received")
        try:
            return self._run(state, started)
        except Exception as exc:  # noqa: BLE001, internal errors become FAILED
            state.log(f"internal_error type={type(exc).__name__}")
            return self._finish(
                state,
                RunStatus.FAILED,
                StopReason.INTERNAL_ERROR,
                "the task failed with an internal error",
            )

    # ---------------------------------------------------------------
    def _run(self, state: AgentState, started: float) -> AgentResult:
        # 1. Understand Intent
        assert state.tenant is not None
        state.intent = self.intent_understanding.understand(state.request)
        state.log(f"intent_understood kind={state.intent.kind}")

        # Plan, execute, verify loop. Replans are bounded.
        while True:
            # 2. Create Plan
            state.plan = self.planner.create_plan(
                state.intent, self.registry, state.observations
            )
            state.log(f"plan_created steps={len(state.plan.steps)}")

            blocked = self._execute_plan(state, started)
            if blocked is not None:
                return blocked

            # 6. Observe Result and 7. Is the Goal Achieved?
            verification = self.verifier.verify(state)
            state.log(f"verification achieved={verification.achieved}")

            if verification.achieved:
                # Yes -> Verify and Return Result
                return self._finish(
                    state,
                    RunStatus.COMPLETED,
                    StopReason.GOAL_ACHIEVED,
                    verification.reason,
                    answer=verification.answer,
                )

            # No -> Replan if budget remains, otherwise stop as failed
            if state.replans_used < self.limits.max_replans:
                state.replans_used += 1
                state.log(f"replanning attempt={state.replans_used}")
                continue
            return self._finish(
                state,
                RunStatus.FAILED,
                StopReason.GOAL_NOT_ACHIEVED,
                "goal not achieved after replans: " + verification.reason,
            )

    # ---------------------------------------------------------------
    def _execute_plan(self, state: AgentState, started: float) -> AgentResult | None:
        """Run every planned step. Returns a blocking result if the
        run must stop (limits, permissions, unknown tool, failure),
        or None when the whole plan executed."""
        assert state.plan is not None
        for step in state.plan.steps:
            # A step that already succeeded in an earlier plan does
            # not need to run again after a replan.
            if any(o.step_id == step.id and o.success for o in state.observations):
                continue

            # Limit: total execution time
            if self._elapsed(started) > self.limits.max_execution_seconds:
                state.log("limit_hit execution_time")
                return self._finish(
                    state,
                    RunStatus.FAILED,
                    StopReason.TIMEOUT,
                    "execution time limit exceeded",
                    pending_step=step,
                )

            # Limit: number of steps
            if state.steps_executed >= self.limits.max_steps:
                state.log("limit_hit max_steps")
                return self._finish(
                    state,
                    RunStatus.FAILED,
                    StopReason.STEP_LIMIT,
                    "step limit exceeded",
                    pending_step=step,
                )

            # 3. Choose Tool
            try:
                tool = self.registry.get(step.tool_name)
            except UnknownToolError as exc:
                state.log(f"tool_unknown name={step.tool_name}")
                return self._finish(
                    state,
                    RunStatus.FAILED,
                    StopReason.UNKNOWN_TOOL,
                    str(exc),
                    pending_step=step,
                )
            state.log(f"tool_chosen step={step.id} tool={tool.name}")

            # Limit: tool permissions
            if self.policy.requires_approval(tool.permission):
                if self.approval_callback is None:
                    state.log(f"approval_required tool={tool.name}")
                    return self._finish(
                        state,
                        RunStatus.WAITING_FOR_APPROVAL,
                        StopReason.APPROVAL_REQUIRED,
                        f"tool {tool.name} needs human approval "
                        f"({tool.permission.name}) and no approval "
                        "callback is configured",
                        pending_step=step,
                    )
                if not self.approval_callback(step, tool):
                    state.log(f"approval_denied tool={tool.name}")
                    return self._finish(
                        state,
                        RunStatus.FAILED,
                        StopReason.PERMISSION_DENIED,
                        f"approval denied for tool {tool.name}",
                        pending_step=step,
                    )
                state.log(f"approval_granted tool={tool.name}")

            # 4. Execute Tool, with bounded retries, and 5. Observe
            failure = self._execute_step(state, step, tool, started)
            if failure is not None:
                return failure
        return None

    # ---------------------------------------------------------------
    def _execute_step(
        self,
        state: AgentState,
        step: PlanStep,
        tool: Tool,
        started: float,
    ) -> AgentResult | None:
        # Validate arguments before anything runs. A validation
        # error is recorded and the tool is never executed.
        try:
            validated = tool.validate(step.tool_args)
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            )
            error = f"ValidationError: {detail}"
            state.observations.append(
                Observation(
                    step_id=step.id,
                    tool_name=tool.name,
                    success=False,
                    error=error,
                    attempt=0,
                    duration_seconds=0.0,
                )
            )
            state.log(f"tool_validation_failed step={step.id} error={error}")
            return self._finish(
                state,
                RunStatus.FAILED,
                StopReason.VALIDATION_ERROR,
                f"tool arguments for {tool.name} failed validation: {detail}",
                pending_step=step,
            )
        max_attempts = 1 + self.limits.max_retries_per_step
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            if self._elapsed(started) > self.limits.max_execution_seconds:
                state.log("limit_hit execution_time during step")
                return self._finish(
                    state,
                    RunStatus.FAILED,
                    StopReason.TIMEOUT,
                    "execution time limit exceeded",
                    pending_step=step,
                )
            t0 = time.monotonic()
            try:
                result = self._call_tool_with_timeout(tool, validated, state)
                duration = time.monotonic() - t0
                if self._elapsed(started) > self.limits.max_execution_seconds:
                    state.log("limit_hit execution_time after tool")
                    return self._finish(
                        state,
                        RunStatus.FAILED,
                        StopReason.TIMEOUT,
                        "execution time limit exceeded",
                        pending_step=step,
                    )
                state.observations.append(
                    Observation(
                        step_id=step.id,
                        tool_name=tool.name,
                        success=True,
                        result=result,
                        attempt=attempt,
                        duration_seconds=duration,
                    )
                )
                state.steps_executed += 1
                state.log(f"tool_executed step={step.id} attempt={attempt} ok")
                return None
            except Exception as exc:  # noqa: BLE001, tool errors are observed
                duration = time.monotonic() - t0
                last_error = f"{type(exc).__name__}: {exc}"
                state.observations.append(
                    Observation(
                        step_id=step.id,
                        tool_name=tool.name,
                        success=False,
                        error=last_error,
                        attempt=attempt,
                        duration_seconds=duration,
                    )
                )
                state.log(
                    f"tool_failed step={step.id} attempt={attempt} error={last_error}"
                )
        state.steps_executed += 1
        # Retries exhausted -> stop as failed, never fail silently.
        return self._finish(
            state,
            RunStatus.FAILED,
            StopReason.RETRIES_EXHAUSTED,
            f"step {step.id} failed after {max_attempts} attempts: {last_error}",
            pending_step=step,
        )

    # ---------------------------------------------------------------
    def _call_tool_with_timeout(self, tool: Tool, validated, state: AgentState):
        """Execute a tool handler preemptively bounded by the per
        tool timeout.

        Mechanism: the handler runs in a daemon thread and the
        orchestrator waits at most per_tool_timeout_seconds for it.
        Documented limit: Python cannot kill a thread, so a handler
        that ignores the timeout keeps running in the background as
        a daemon and dies with the process. The orchestrator itself
        never blocks on it: it records a timeout observation and
        moves on through the normal retry path. Handlers with side
        effects must therefore be idempotent; a process based
        executor is the upgrade path if hard kills are ever needed.
        """
        outcome: dict = {}

        def target() -> None:
            try:
                outcome["result"] = tool.handler(validated, state.tenant)
            except BaseException as exc:  # noqa: BLE001, ferried to caller
                outcome["error"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(self.limits.per_tool_timeout_seconds)
        if thread.is_alive():
            raise TimeoutError(
                f"tool {tool.name} exceeded per tool timeout "
                f"({self.limits.per_tool_timeout_seconds}s) and was "
                "interrupted"
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("result")

    # ---------------------------------------------------------------
    @staticmethod
    def _finish(
        state: AgentState,
        status: RunStatus,
        stop_reason: StopReason,
        reason: str,
        answer: str | None = None,
        pending_step: PlanStep | None = None,
    ) -> AgentResult:
        state.status = status
        return AgentResult(
            status=status,
            answer=answer,
            state=state,
            reason=reason,
            pending_step=pending_step,
            stop_reason=stop_reason,
            error_summary=reason if status is RunStatus.FAILED else "",
        )

    @staticmethod
    def _elapsed(started: float) -> float:
        return time.monotonic() - started
