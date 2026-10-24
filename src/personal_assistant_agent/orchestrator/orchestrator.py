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
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .intent import IntentUnderstanding, RuleBasedIntentUnderstanding
from .limits import OrchestratorLimits
from .permissions import PermissionPolicy
from .planner import Planner, RuleBasedPlanner
from .state import AgentResult, AgentState, Observation, PlanStep, RunStatus
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
    def run(self, request: str) -> AgentResult:
        started = time.monotonic()
        state = AgentState(request=request)
        state.log("request_received")

        # 1. Understand Intent
        state.intent = self.intent_understanding.understand(request)
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
                return AgentResult(
                    status=RunStatus.COMPLETED,
                    answer=verification.answer,
                    state=state,
                    reason=verification.reason,
                )

            # No -> Replan if budget remains, otherwise Ask the User
            if state.replans_used < self.limits.max_replans:
                state.replans_used += 1
                state.log(f"replanning attempt={state.replans_used}")
                continue
            return AgentResult(
                status=RunStatus.ASK_USER,
                answer=None,
                state=state,
                reason=("goal not achieved after replans: " + verification.reason),
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
                return AgentResult(
                    status=RunStatus.TIMEOUT,
                    answer=None,
                    state=state,
                    reason="execution time limit exceeded",
                    pending_step=step,
                )

            # Limit: number of steps
            if state.steps_executed >= self.limits.max_steps:
                state.log("limit_hit max_steps")
                return AgentResult(
                    status=RunStatus.STEP_LIMIT_EXCEEDED,
                    answer=None,
                    state=state,
                    reason="step limit exceeded",
                    pending_step=step,
                )

            # 3. Choose Tool
            try:
                tool = self.registry.get(step.tool_name)
            except UnknownToolError as exc:
                state.log(f"tool_unknown name={step.tool_name}")
                return AgentResult(
                    status=RunStatus.ASK_USER,
                    answer=None,
                    state=state,
                    reason=str(exc),
                    pending_step=step,
                )
            state.log(f"tool_chosen step={step.id} tool={tool.name}")

            # Limit: tool permissions
            if self.policy.requires_approval(tool.permission):
                approved = (
                    self.approval_callback(step, tool)
                    if self.approval_callback is not None
                    else False
                )
                if self.approval_callback is None:
                    state.log(f"approval_required tool={tool.name}")
                    return AgentResult(
                        status=RunStatus.ASK_USER,
                        answer=None,
                        state=state,
                        reason=(
                            f"tool {tool.name} needs human approval "
                            f"({tool.permission.name}) and no approval "
                            "callback is configured"
                        ),
                        pending_step=step,
                    )
                if not approved:
                    state.log(f"approval_denied tool={tool.name}")
                    return AgentResult(
                        status=RunStatus.PERMISSION_DENIED,
                        answer=None,
                        state=state,
                        reason=f"approval denied for tool {tool.name}",
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
        max_attempts = 1 + self.limits.max_retries_per_step
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            if self._elapsed(started) > self.limits.max_execution_seconds:
                state.log("limit_hit execution_time during step")
                return AgentResult(
                    status=RunStatus.TIMEOUT,
                    answer=None,
                    state=state,
                    reason="execution time limit exceeded",
                    pending_step=step,
                )
            t0 = time.monotonic()
            try:
                result = tool.handler(step.tool_args)
                duration = time.monotonic() - t0
                if duration > self.limits.per_tool_timeout_seconds:
                    raise TimeoutError(
                        f"tool {tool.name} exceeded per tool timeout "
                        f"({duration:.3f}s > "
                        f"{self.limits.per_tool_timeout_seconds}s)"
                    )
                if self._elapsed(started) > (self.limits.max_execution_seconds):
                    state.log("limit_hit execution_time after tool")
                    return AgentResult(
                        status=RunStatus.TIMEOUT,
                        answer=None,
                        state=state,
                        reason="execution time limit exceeded",
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
        # Retries exhausted -> Ask the User, never fail silently.
        return AgentResult(
            status=RunStatus.ASK_USER,
            answer=None,
            state=state,
            reason=(
                f"step {step.id} failed after {max_attempts} attempts: {last_error}"
            ),
            pending_step=step,
        )

    @staticmethod
    def _elapsed(started: float) -> float:
        return time.monotonic() - started
