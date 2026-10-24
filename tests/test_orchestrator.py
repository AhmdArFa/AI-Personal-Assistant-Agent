import time

import pytest

from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    AgentState,
    CancellationToken,
    OrchestratorLimits,
    PermissionLevel,
    PermissionPolicy,
    Plan,
    PlanStep,
    RunStatus,
    StaticPlanner,
    StopReason,
    StrictInput,
    TenantContext,
    Tool,
    ToolRegistry,
    default_registry,
)

CTX = TenantContext(user_id="user-1", workspace_id="ws-1")
OTHER_CTX = TenantContext(user_id="user-2", workspace_id="ws-2")


def orchestrator(**kwargs):
    return AgentOrchestrator(registry=default_registry(), **kwargs)


def test_happy_path_calculate():
    result = orchestrator().run("calculate 19 * 3 + 1", context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "58"
    assert result.state.steps_executed == 1
    assert "verification achieved=True" in result.state.trace


def test_happy_path_time():
    result = orchestrator().run("what time is it?", context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert result.answer  # an ISO timestamp from the tool, observed


def test_step_limit_is_enforced():
    plan = Plan(
        goal="many steps",
        steps=[
            PlanStep(
                id=f"s{i}",
                description="echo",
                tool_name="echo",
                tool_args={"message": str(i)},
            )
            for i in range(5)
        ],
    )
    agent = orchestrator(
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(max_steps=2),
    )
    result = agent.run("many steps", context=CTX)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.STEP_LIMIT
    assert result.pending_step is not None


def test_execution_time_limit_is_enforced():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="slow",
            description="sleeps",
            permission=PermissionLevel.READ,
            handler=lambda args, context: time.sleep(0.3) or "done",
        )
    )
    plan = Plan(
        goal="slow", steps=[PlanStep(id="s1", description="slow", tool_name="slow")]
    )
    agent = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(max_execution_seconds=0.05, max_retries_per_step=0),
    )
    result = agent.run("slow", context=CTX)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.TIMEOUT


def test_external_tool_without_approval_callback_asks_user():
    registry = default_registry()
    registry.register(
        Tool(
            name="send_email",
            description="send mail",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args, context: "sent",
        )
    )
    plan = Plan(
        goal="send",
        steps=[PlanStep(id="s1", description="send", tool_name="send_email")],
    )
    result = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan)).run(
        "send it", context=CTX
    )
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "approval" in result.reason


def test_external_tool_with_approval_runs():
    registry = default_registry()
    registry.register(
        Tool(
            name="send_email",
            description="send mail",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args, context: "sent",
        )
    )
    plan = Plan(
        goal="send",
        steps=[PlanStep(id="s1", description="send", tool_name="send_email")],
    )
    result = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        approval_callback=lambda step, tool: True,
    ).run("send it", context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "sent"


def test_external_tool_with_denial_is_permission_denied():
    registry = default_registry()
    registry.register(
        Tool(
            name="send_email",
            description="send mail",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args, context: "sent",
        )
    )
    plan = Plan(
        goal="send",
        steps=[PlanStep(id="s1", description="send", tool_name="send_email")],
    )
    result = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        approval_callback=lambda step, tool: False,
    ).run("send it", context=CTX)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.PERMISSION_DENIED


def test_sensitive_tool_needs_approval_even_under_loose_policy():
    # Policy only auto allows up to READ here, so a sensitive tool
    # must still stop for approval.
    registry = default_registry()
    registry.register(
        Tool(
            name="delete_everything",
            description="danger",
            permission=PermissionLevel.SENSITIVE,
            handler=lambda args, context: "deleted",
        )
    )
    plan = Plan(
        goal="danger",
        steps=[PlanStep(id="s1", description="danger", tool_name="delete_everything")],
    )
    result = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        policy=PermissionPolicy(auto_allow_up_to=PermissionLevel.READ),
    ).run("danger", context=CTX)
    assert result.status is RunStatus.WAITING_FOR_APPROVAL


def test_retry_then_success():
    calls = {"n": 0}

    def flaky(args, context):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("temporary failure")
        return "recovered"

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="flaky",
            description="flaky",
            permission=PermissionLevel.READ,
            handler=flaky,
        )
    )
    plan = Plan(
        goal="flaky", steps=[PlanStep(id="s1", description="flaky", tool_name="flaky")]
    )
    result = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(max_retries_per_step=2),
    ).run("flaky", context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "recovered"
    assert calls["n"] == 3


def test_retries_exhausted_asks_user():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="broken",
            description="always fails",
            permission=PermissionLevel.READ,
            handler=lambda args, context: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    )
    plan = Plan(
        goal="broken",
        steps=[PlanStep(id="s1", description="broken", tool_name="broken")],
    )
    result = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(max_retries_per_step=1),
    ).run("broken", context=CTX)
    assert result.status is RunStatus.FAILED
    assert "boom" in result.reason


def test_unknown_tool_asks_user():
    plan = Plan(
        goal="ghost",
        steps=[PlanStep(id="s1", description="ghost", tool_name="does_not_exist")],
    )
    result = orchestrator(planner=StaticPlanner(plan)).run("ghost", context=CTX)
    assert result.status is RunStatus.FAILED
    assert "unknown tool" in result.reason


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        OrchestratorLimits(max_steps=0)


class SearchInput(StrictInput):
    query: str
    top_k: int = 5


def test_validation_missing_field_never_runs():
    registry = ToolRegistry()
    called = []
    registry.register(
        Tool(
            name="search",
            description="search",
            permission=PermissionLevel.READ,
            handler=lambda args, context: called.append(args) or "found",
            input_model=SearchInput,
        )
    )
    plan = Plan(
        goal="search",
        steps=[
            PlanStep(
                id="s1",
                description="search",
                tool_name="search",
                tool_args={"top_k": 3},
            )
        ],
    )
    result = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan)).run(
        "search", context=CTX
    )
    assert result.status is RunStatus.FAILED
    assert "validation" in result.reason
    assert called == []
    assert any(
        o.error and o.error.startswith("ValidationError")
        for o in result.state.observations
    )


def test_validation_wrong_type_rejected():
    registry = ToolRegistry()
    called = []
    registry.register(
        Tool(
            name="search",
            description="search",
            permission=PermissionLevel.READ,
            handler=lambda args, context: called.append(args) or "found",
            input_model=SearchInput,
        )
    )
    plan = Plan(
        goal="search",
        steps=[
            PlanStep(
                id="s1",
                description="search",
                tool_name="search",
                tool_args={"query": "x", "top_k": "not-a-number"},
            )
        ],
    )
    result = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan)).run(
        "search", context=CTX
    )
    assert result.status is RunStatus.FAILED
    assert called == []


def test_validation_extra_field_rejected():
    registry = ToolRegistry()
    called = []
    registry.register(
        Tool(
            name="search",
            description="search",
            permission=PermissionLevel.READ,
            handler=lambda args, context: called.append(args) or "found",
            input_model=SearchInput,
        )
    )
    plan = Plan(
        goal="search",
        steps=[
            PlanStep(
                id="s1",
                description="search",
                tool_name="search",
                tool_args={"query": "x", "user_id": "spoofed"},
            )
        ],
    )
    result = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan)).run(
        "search", context=CTX
    )
    assert result.status is RunStatus.FAILED
    assert "validation" in result.reason
    assert called == []


def test_validation_success_passes_model_to_handler():
    registry = ToolRegistry()
    seen = []
    registry.register(
        Tool(
            name="search",
            description="search",
            permission=PermissionLevel.READ,
            handler=lambda args, context: (
                seen.append(args) or f"{args.query}:{args.top_k}"
            ),
            input_model=SearchInput,
        )
    )
    plan = Plan(
        goal="search",
        steps=[
            PlanStep(
                id="s1",
                description="search",
                tool_name="search",
                tool_args={"query": "report", "top_k": 2},
            )
        ],
    )
    result = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan)).run(
        "search", context=CTX
    )
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "report:2"
    assert isinstance(seen[0], SearchInput)


class ReadDocumentInput(StrictInput):
    document_id: str


def test_run_requires_authenticated_context():
    with pytest.raises(ValueError):
        orchestrator().run("calculate 1 + 1")
    with pytest.raises(ValueError):
        orchestrator().run("calculate 1 + 1", context=None)


def test_state_carries_tenant_context():
    result = orchestrator().run("calculate 1 + 1", context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert result.state.tenant == CTX
    assert result.state.tenant.user_id == "user-1"
    assert result.state.tenant.workspace_id == "ws-1"


def test_cross_workspace_tool_access_fails():
    owners = {"doc-1": "ws-1"}

    def read_document(args, context):
        if owners[args.document_id] != context.workspace_id:
            raise PermissionError("cross workspace access denied")
        return "document content"

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="read_document",
            description="read a document",
            permission=PermissionLevel.READ,
            handler=read_document,
            input_model=ReadDocumentInput,
        )
    )
    plan = Plan(
        goal="read",
        steps=[
            PlanStep(
                id="s1",
                description="read",
                tool_name="read_document",
                tool_args={"document_id": "doc-1"},
            )
        ],
    )
    agent = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(max_retries_per_step=0),
    )

    own = agent.run("read", context=CTX)
    assert own.status is RunStatus.COMPLETED
    assert own.answer == "document content"

    other = agent.run("read", context=OTHER_CTX)
    assert other.status is RunStatus.FAILED
    assert any(
        o.error and "cross workspace" in o.error for o in other.state.observations
    )


def test_identity_cannot_be_spoofed_via_tool_args():
    registry = ToolRegistry()
    called = []
    registry.register(
        Tool(
            name="read_document",
            description="read a document",
            permission=PermissionLevel.READ,
            handler=lambda args, context: called.append(args) or "content",
            input_model=ReadDocumentInput,
        )
    )
    plan = Plan(
        goal="read",
        steps=[
            PlanStep(
                id="s1",
                description="read",
                tool_name="read_document",
                tool_args={"document_id": "doc-1", "workspace_id": "ws-9"},
            )
        ],
    )
    result = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan)).run(
        "read", context=CTX
    )
    assert result.status is RunStatus.FAILED
    assert "validation" in result.reason
    assert called == []


def test_internal_error_becomes_failed_with_safe_summary():
    class ExplodingPlanner:
        def create_plan(self, intent, registry, observations):
            raise RuntimeError("secret-db-password-123")

    result = AgentOrchestrator(
        registry=default_registry(), planner=ExplodingPlanner()
    ).run("anything", context=CTX)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.INTERNAL_ERROR
    assert result.error_summary
    assert "secret-db-password-123" not in result.error_summary
    assert "secret-db-password-123" not in result.reason
    assert result.state.status is RunStatus.FAILED


def test_state_status_lifecycle():
    fresh = AgentState(request="x")
    assert fresh.status is RunStatus.QUEUED
    result = orchestrator().run("calculate 2 + 2", context=CTX)
    assert result.state.status is RunStatus.COMPLETED
    assert result.stop_reason is StopReason.GOAL_ACHIEVED


def test_per_tool_timeout_interrupts_never_returning_tool():
    import threading as _threading

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="hangs",
            description="never returns",
            permission=PermissionLevel.READ,
            handler=lambda args, context: _threading.Event().wait(),
        )
    )
    plan = Plan(
        goal="hangs", steps=[PlanStep(id="s1", description="hangs", tool_name="hangs")]
    )
    agent = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(
            per_tool_timeout_seconds=0.2,
            max_retries_per_step=1,
            max_execution_seconds=30,
        ),
    )
    t0 = time.monotonic()
    result = agent.run("hangs", context=CTX)
    elapsed = time.monotonic() - t0
    assert elapsed < 3
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.RETRIES_EXHAUSTED
    assert any(o.error and "TimeoutError" in o.error for o in result.state.observations)


def test_tool_within_timeout_still_succeeds():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="slowish",
            description="sleeps briefly",
            permission=PermissionLevel.READ,
            handler=lambda args, context: time.sleep(0.05) or "done",
        )
    )
    plan = Plan(
        goal="slowish",
        steps=[PlanStep(id="s1", description="slowish", tool_name="slowish")],
    )
    result = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(per_tool_timeout_seconds=2),
    ).run("slowish", context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "done"


def test_cancel_before_start():
    token = CancellationToken()
    token.cancel()
    result = orchestrator().run(
        "calculate 1 + 1", context=CTX, cancellation_token=token
    )
    assert result.status is RunStatus.CANCELLED
    assert result.stop_reason is StopReason.CANCELLED
    assert result.state.observations == []


def test_cancel_between_steps():
    token = CancellationToken()

    def first(args, context):
        token.cancel()
        return "one"

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="first",
            description="first",
            permission=PermissionLevel.READ,
            handler=first,
        )
    )
    registry.register(
        Tool(
            name="echo",
            description="echo",
            permission=PermissionLevel.READ,
            handler=lambda args, context: args.message,
            input_model=default_registry().get("echo").input_model,
        )
    )
    plan = Plan(
        goal="two",
        steps=[
            PlanStep(id="s1", description="first", tool_name="first"),
            PlanStep(
                id="s2",
                description="echo",
                tool_name="echo",
                tool_args={"message": "two"},
            ),
        ],
    )
    result = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan)).run(
        "two", context=CTX, cancellation_token=token
    )
    assert result.status is RunStatus.CANCELLED
    assert result.pending_step is not None and result.pending_step.id == "s2"
    assert [o.result for o in result.state.observations if o.success] == ["one"]


def test_cancel_during_retry():
    token = CancellationToken()
    attempts = {"n": 0}

    def flaky_cancel(args, context):
        attempts["n"] += 1
        token.cancel()
        raise RuntimeError("fail once")

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="flaky",
            description="flaky",
            permission=PermissionLevel.READ,
            handler=flaky_cancel,
        )
    )
    plan = Plan(
        goal="flaky", steps=[PlanStep(id="s1", description="flaky", tool_name="flaky")]
    )
    result = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(max_retries_per_step=3),
    ).run("flaky", context=CTX, cancellation_token=token)
    assert result.status is RunStatus.CANCELLED
    assert attempts["n"] == 1


def test_cancel_during_long_tool_call():
    import threading as _threading

    token = CancellationToken()
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="slow",
            description="slow",
            permission=PermissionLevel.READ,
            handler=lambda args, context: time.sleep(5) or "done",
        )
    )
    plan = Plan(
        goal="slow", steps=[PlanStep(id="s1", description="slow", tool_name="slow")]
    )
    agent = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(per_tool_timeout_seconds=30),
    )
    holder = {}
    worker = _threading.Thread(
        target=lambda: holder.setdefault(
            "result", agent.run("slow", context=CTX, cancellation_token=token)
        )
    )
    t0 = time.monotonic()
    worker.start()
    time.sleep(0.3)
    token.cancel()
    worker.join(timeout=5)
    elapsed = time.monotonic() - t0
    assert not worker.is_alive()
    assert elapsed < 4
    assert holder["result"].status is RunStatus.CANCELLED


def test_cancelling_finished_run_is_noop():
    token = CancellationToken()
    result = orchestrator().run(
        "calculate 2 + 2", context=CTX, cancellation_token=token
    )
    assert result.status is RunStatus.COMPLETED
    token.cancel()
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "4"


def _budget_agent(registry, plan, **limits_kwargs):
    return AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        limits=OrchestratorLimits(**limits_kwargs),
    )


def test_tool_call_budget_stops_run():
    registry = default_registry()
    plan = Plan(
        goal="three",
        steps=[
            PlanStep(
                id=f"s{i}",
                description="echo",
                tool_name="echo",
                tool_args={"message": str(i)},
            )
            for i in range(3)
        ],
    )
    result = _budget_agent(registry, plan, max_tool_calls=2).run("three", context=CTX)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.BUDGET_EXCEEDED
    assert result.state.tool_calls == 2
    assert len([o for o in result.state.observations if o.success]) == 2


def test_output_size_truncation():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="big",
            description="big output",
            permission=PermissionLevel.READ,
            handler=lambda args, context: "x" * 5000,
        )
    )
    plan = Plan(
        goal="big", steps=[PlanStep(id="s1", description="big", tool_name="big")]
    )
    result = _budget_agent(registry, plan, max_output_chars=100).run("big", context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert result.answer is not None
    assert len(result.answer) < 200
    assert "truncated" in result.answer


def test_token_budget_from_usage_reports():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="meter",
            description="reports usage",
            permission=PermissionLevel.READ,
            handler=lambda args, context: {"usage": {"tokens": 500}, "data": "ok"},
        )
    )
    plan = Plan(
        goal="meter", steps=[PlanStep(id="s1", description="meter", tool_name="meter")]
    )
    result = _budget_agent(registry, plan, max_tokens=100).run("meter", context=CTX)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.BUDGET_EXCEEDED
    assert result.state.tokens_used == 500


def test_cost_budget_from_usage_reports():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="meter",
            description="reports usage",
            permission=PermissionLevel.READ,
            handler=lambda args, context: {"usage": {"cost": 0.5}, "data": "ok"},
        )
    )
    plan = Plan(
        goal="meter", steps=[PlanStep(id="s1", description="meter", tool_name="meter")]
    )
    result = _budget_agent(registry, plan, max_cost=0.10).run("meter", context=CTX)
    assert result.status is RunStatus.FAILED
    assert "cost budget" in result.reason
    assert result.state.cost_used == 0.5


def test_budget_limits_validated():
    with pytest.raises(ValueError):
        OrchestratorLimits(max_tool_calls=0)
    with pytest.raises(ValueError):
        OrchestratorLimits(max_output_chars=0)
    with pytest.raises(ValueError):
        OrchestratorLimits(max_tokens=0)
    with pytest.raises(ValueError):
        OrchestratorLimits(max_cost=0)
