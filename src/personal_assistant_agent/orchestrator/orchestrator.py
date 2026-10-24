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

from .approvals import arguments_hash
from .audit import AuditEvent, AuditSink, Redactor
from .cancellation import CancellationToken, RunCancelled
from .checkpoints import CheckpointStore, serialize_state
from .intent import IntentUnderstanding, RuleBasedIntentUnderstanding
from .limits import OrchestratorLimits
from .permissions import PermissionPolicy
from .planner import Planner, RuleBasedPlanner
from .state import (
    AgentResult,
    AgentState,
    ApprovalRecord,
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
        checkpoint_store: CheckpointStore | None = None,
        audit_sink: AuditSink | None = None,
        redactor: Redactor | None = None,
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
        self.checkpoint_store = checkpoint_store
        self.audit_sink = audit_sink
        self.redactor = redactor or Redactor()

    # ---------------------------------------------------------------
    def run(
        self,
        request: str,
        context: TenantContext | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> AgentResult:
        if context is None:
            raise ValueError(
                "an authenticated TenantContext is required: identity "
                "is never taken from the request text"
            )
        started = time.monotonic()
        state = AgentState(
            request=request, tenant=context, cancellation_token=cancellation_token
        )
        state.status = RunStatus.RUNNING
        state.log("request_received")
        if cancellation_token is not None and cancellation_token.is_cancelled:
            state.log("cancelled_before_start")
            return self._finish(
                state,
                RunStatus.CANCELLED,
                StopReason.CANCELLED,
                "cancelled before the run started",
            )
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
    def _run(
        self,
        state: AgentState,
        started: float,
        reuse_existing_plan: bool = False,
    ) -> AgentResult:
        # 1. Understand Intent (skipped when resuming a state that
        # already understood it: the snapshot is authoritative)
        assert state.tenant is not None
        if state.intent is None:
            state.intent = self.intent_understanding.understand(state.request)
            state.log(f"intent_understood kind={state.intent.kind}")

        # Plan, execute, verify loop. Replans are bounded.
        first_pass = True
        while True:
            # 2. Create Plan. On a resume, the first pass reuses the
            # checkpointed plan: its arguments are the ones that were
            # checkpointed (and possibly approved). Replanning first
            # would silently swap the reviewed arguments for new ones.
            if first_pass and reuse_existing_plan and state.plan is not None:
                state.log(f"plan_restored steps={len(state.plan.steps)}")
            else:
                state.plan = self.planner.create_plan(
                    state.intent, self.registry, state.observations
                )
                state.log(f"plan_created steps={len(state.plan.steps)}")
            first_pass = False
            self._save_checkpoint(state)

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
            if self._cancelled(state):
                state.log(f"cancelled_before_step step={step.id}")
                return self._finish(
                    state,
                    RunStatus.CANCELLED,
                    StopReason.CANCELLED,
                    "cancelled by the user",
                    pending_step=step,
                )
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
            self.redactor.register_arguments(step.tool_args)
            self._audit(state, "tool_chosen", tool_name=tool.name, step_id=step.id)

            # Limit: tool permissions, bound to exact arguments
            if self.policy.requires_approval(tool.permission):
                current_hash = arguments_hash(tool.name, step.tool_args)
                if state.approved_arguments.get(step.id) == current_hash:
                    state.log(f"approval_previously_granted step={step.id}")
                elif self.approval_callback is None:
                    pending = next(
                        (
                            a
                            for a in state.approvals
                            if a.step_id == step.id
                            and a.arguments_hash == current_hash
                            and a.status == "pending"
                        ),
                        None,
                    )
                    if pending is None:
                        state.approvals.append(
                            ApprovalRecord(
                                step_id=step.id,
                                tool_name=tool.name,
                                arguments_hash=current_hash,
                                tool_args=dict(step.tool_args),
                            )
                        )
                    state.log(f"approval_required tool={tool.name}")
                    self._audit(
                        state,
                        "approval_required",
                        tool_name=tool.name,
                        step_id=step.id,
                    )
                    return self._finish(
                        state,
                        RunStatus.WAITING_FOR_APPROVAL,
                        StopReason.APPROVAL_REQUIRED,
                        f"tool {tool.name} needs human approval "
                        f"({tool.permission.name}): approve the task "
                        "to resume it with these exact arguments",
                        pending_step=step,
                    )
                elif not self.approval_callback(step, tool):
                    state.approvals.append(
                        ApprovalRecord(
                            step_id=step.id,
                            tool_name=tool.name,
                            arguments_hash=current_hash,
                            tool_args=dict(step.tool_args),
                            status="denied",
                        )
                    )
                    state.log(f"approval_denied tool={tool.name}")
                    self._audit(
                        state,
                        "approval_denied",
                        tool_name=tool.name,
                        step_id=step.id,
                    )
                    return self._finish(
                        state,
                        RunStatus.FAILED,
                        StopReason.PERMISSION_DENIED,
                        f"approval denied for tool {tool.name}",
                        pending_step=step,
                    )
                else:
                    state.approvals.append(
                        ApprovalRecord(
                            step_id=step.id,
                            tool_name=tool.name,
                            arguments_hash=current_hash,
                            tool_args=dict(step.tool_args),
                            status="approved",
                        )
                    )
                    state.approved_arguments[step.id] = current_hash
                    state.log(f"approval_granted tool={tool.name}")
                    self._audit(
                        state,
                        "approval_granted",
                        tool_name=tool.name,
                        step_id=step.id,
                    )

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
            self._observe(
                state,
                Observation(
                    step_id=step.id,
                    tool_name=tool.name,
                    success=False,
                    error=error,
                    attempt=0,
                    duration_seconds=0.0,
                ),
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
            if self._cancelled(state):
                state.log(f"cancelled_before_retry step={step.id}")
                return self._finish(
                    state,
                    RunStatus.CANCELLED,
                    StopReason.CANCELLED,
                    "cancelled by the user",
                    pending_step=step,
                )
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
                state.steps_executed += 1
                self._observe(
                    state,
                    Observation(
                        step_id=step.id,
                        tool_name=tool.name,
                        success=True,
                        result=result,
                        attempt=attempt,
                        duration_seconds=duration,
                    ),
                )
                state.log(f"tool_executed step={step.id} attempt={attempt} ok")
                return None
            except RunCancelled:
                state.log(f"cancelled_during_tool step={step.id}")
                return self._finish(
                    state,
                    RunStatus.CANCELLED,
                    StopReason.CANCELLED,
                    "cancelled by the user",
                    pending_step=step,
                )
            except Exception as exc:  # noqa: BLE001, tool errors are observed
                duration = time.monotonic() - t0
                last_error = f"{type(exc).__name__}: {exc}"
                self._observe(
                    state,
                    Observation(
                        step_id=step.id,
                        tool_name=tool.name,
                        success=False,
                        error=last_error,
                        attempt=attempt,
                        duration_seconds=duration,
                    ),
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
        deadline = time.monotonic() + self.limits.per_tool_timeout_seconds
        while thread.is_alive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._cancelled(state):
                raise RunCancelled()
            thread.join(min(0.02, remaining))
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
    def _observe(self, state: AgentState, observation: Observation) -> None:
        state.observations.append(observation)
        self._audit(
            state,
            "tool_executed" if observation.success else "tool_failed",
            tool_name=observation.tool_name,
            step_id=observation.step_id,
            outcome="ok" if observation.success else "failed",
            details={"attempt": observation.attempt, "error": observation.error},
        )
        self._save_checkpoint(state)

    def _audit(
        self,
        state: AgentState,
        event_type: str,
        tool_name: str = "",
        step_id: str = "",
        outcome: str = "",
        details: dict | None = None,
    ) -> None:
        if self.audit_sink is None:
            return
        tenant = state.tenant
        self.audit_sink.record(
            AuditEvent(
                task_id=state.task_id,
                user_id=tenant.user_id if tenant else "",
                workspace_id=tenant.workspace_id if tenant else "",
                event_type=event_type,
                tool_name=tool_name,
                step_id=step_id,
                outcome=outcome,
                details=self.redactor.redact(details or {}),
            )
        )

    def _save_checkpoint(self, state: AgentState) -> None:
        if self.checkpoint_store is None:
            return
        try:
            snapshot = self.redactor.redact(serialize_state(state))
            save_snapshot = getattr(self.checkpoint_store, "save_snapshot", None)
            if save_snapshot is not None:
                save_snapshot(state.task_id, snapshot)
            else:
                self.checkpoint_store.save(state)
        except Exception as exc:  # noqa: BLE001, checkpointing must not kill a run
            state.log(f"checkpoint_failed type={type(exc).__name__}")

    # ---------------------------------------------------------------
    def decide_approval(
        self,
        task_id: str,
        context: TenantContext | None = None,
        approved: bool = True,
        approval_id: str | None = None,
    ) -> AgentResult:
        """Decide a pending approval and resume the task.

        The run resumes only if the arguments of the pending step
        still hash to the value that was approved. Changed
        arguments supersede the old record and produce a fresh
        approval request instead of executing.
        """
        if self.checkpoint_store is None:
            raise ValueError("decide_approval requires a checkpoint store")
        if context is None:
            raise ValueError("an authenticated TenantContext is required")
        state = self.checkpoint_store.load(task_id)
        if state is None:
            raise ValueError(f"no checkpoint found for task {task_id}")
        if state.tenant != context:
            raise PermissionError("checkpoint belongs to a different user or workspace")
        if state.status is not RunStatus.WAITING_FOR_APPROVAL:
            raise ValueError("task is not waiting for approval")
        record = next(
            (
                a
                for a in state.approvals
                if a.status == "pending"
                and (approval_id is None or a.id == approval_id)
            ),
            None,
        )
        if record is None:
            raise ValueError("no pending approval record on this task")
        assert state.plan is not None
        step = next((s for s in state.plan.steps if s.id == record.step_id), None)
        if step is None:
            raise ValueError("the approved step is no longer in the plan")
        from .state import _now_iso

        record.decided_by = context.user_id
        record.decided_at = _now_iso()
        if not approved:
            record.status = "denied"
            self._audit(
                state,
                "approval_denied",
                tool_name=record.tool_name,
                step_id=record.step_id,
            )
            return self._finish(
                state,
                RunStatus.FAILED,
                StopReason.PERMISSION_DENIED,
                f"approval denied for tool {record.tool_name}",
                pending_step=step,
            )
        current_hash = arguments_hash(record.tool_name, step.tool_args)
        if current_hash != record.arguments_hash:
            record.status = "superseded"
            state.approvals.append(
                ApprovalRecord(
                    step_id=step.id,
                    tool_name=record.tool_name,
                    arguments_hash=current_hash,
                    tool_args=dict(step.tool_args),
                )
            )
            self._audit(
                state,
                "approval_required",
                tool_name=record.tool_name,
                step_id=record.step_id,
            )
            return self._finish(
                state,
                RunStatus.WAITING_FOR_APPROVAL,
                StopReason.APPROVAL_REQUIRED,
                "arguments changed since approval was requested: "
                "a new approval is required",
                pending_step=step,
            )
        record.status = "approved"
        state.approved_arguments[record.step_id] = record.arguments_hash
        self._audit(
            state,
            "approval_granted",
            tool_name=record.tool_name,
            step_id=record.step_id,
        )
        state.status = RunStatus.RUNNING
        state.log("resumed_after_approval")
        try:
            return self._run(state, time.monotonic(), reuse_existing_plan=True)
        except Exception as exc:  # noqa: BLE001
            state.log(f"internal_error type={type(exc).__name__}")
            return self._finish(
                state,
                RunStatus.FAILED,
                StopReason.INTERNAL_ERROR,
                "the task failed with an internal error",
            )

    # ---------------------------------------------------------------
    def resume(
        self,
        task_id: str,
        context: TenantContext | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> AgentResult:
        """Resume a run from its last checkpoint.

        Steps that already succeeded are not executed again, and
        the step and replan counters continue from the snapshot,
        so limits are never reset by a crash.
        """
        if self.checkpoint_store is None:
            raise ValueError("resume requires a checkpoint store")
        if context is None:
            raise ValueError("an authenticated TenantContext is required")
        state = self.checkpoint_store.load(task_id)
        if state is None:
            raise ValueError(f"no checkpoint found for task {task_id}")
        if state.tenant != context:
            raise PermissionError("checkpoint belongs to a different user or workspace")
        terminal = {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.WAITING_FOR_APPROVAL,
        }
        if state.status in terminal:
            return AgentResult(
                status=state.status,
                answer=(
                    "\n".join(str(r) for r in state.successful_results())
                    if state.status is RunStatus.COMPLETED
                    else None
                ),
                state=state,
                reason="resumed from a terminal checkpoint, nothing re-executed",
                stop_reason=(
                    StopReason.GOAL_ACHIEVED
                    if state.status is RunStatus.COMPLETED
                    else None
                ),
            )
        state.cancellation_token = cancellation_token
        state.status = RunStatus.RUNNING
        state.log("resumed_from_checkpoint")
        started = time.monotonic()
        try:
            return self._run(state, started, reuse_existing_plan=True)
        except Exception as exc:  # noqa: BLE001
            state.log(f"internal_error type={type(exc).__name__}")
            return self._finish(
                state,
                RunStatus.FAILED,
                StopReason.INTERNAL_ERROR,
                "the task failed with an internal error",
            )

    # ---------------------------------------------------------------
    def _finish(
        self,
        state: AgentState,
        status: RunStatus,
        stop_reason: StopReason,
        reason: str,
        answer: str | None = None,
        pending_step: PlanStep | None = None,
    ) -> AgentResult:
        state.status = status
        if answer:
            answer = self.redactor.redact_text(answer)
        state.trace = [self.redactor.redact_text(line) for line in state.trace]
        for obs in state.observations:
            if obs.error:
                obs.error = self.redactor.redact_text(obs.error)
            if isinstance(obs.result, str):
                obs.result = self.redactor.redact_text(obs.result)
        self._audit(
            state,
            "run_stopped",
            outcome=status.value,
            details={"stop_reason": stop_reason.value if stop_reason else ""},
        )
        self._save_checkpoint(state)
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
    def _cancelled(state: AgentState) -> bool:
        token = state.cancellation_token
        return token is not None and bool(token.is_cancelled)

    @staticmethod
    def _elapsed(started: float) -> float:
        return time.monotonic() - started
