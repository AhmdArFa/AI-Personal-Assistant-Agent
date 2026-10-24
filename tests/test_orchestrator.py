import time

import pytest

from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    OrchestratorLimits,
    PermissionLevel,
    PermissionPolicy,
    Plan,
    PlanStep,
    RunStatus,
    StaticPlanner,
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
    assert result.status is RunStatus.STEP_LIMIT_EXCEEDED
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
    assert result.status in (RunStatus.TIMEOUT, RunStatus.ASK_USER)


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
    assert result.status is RunStatus.ASK_USER
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
    assert result.status is RunStatus.PERMISSION_DENIED


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
    assert result.status is RunStatus.ASK_USER


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
    assert result.status is RunStatus.ASK_USER
    assert "boom" in result.reason


def test_unknown_tool_asks_user():
    plan = Plan(
        goal="ghost",
        steps=[PlanStep(id="s1", description="ghost", tool_name="does_not_exist")],
    )
    result = orchestrator(planner=StaticPlanner(plan)).run("ghost", context=CTX)
    assert result.status is RunStatus.ASK_USER
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
    assert result.status is RunStatus.ASK_USER
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
    assert result.status is RunStatus.ASK_USER
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
    assert result.status is RunStatus.ASK_USER
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
    assert other.status is not RunStatus.COMPLETED
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
    assert result.status is RunStatus.ASK_USER
    assert "validation" in result.reason
    assert called == []
