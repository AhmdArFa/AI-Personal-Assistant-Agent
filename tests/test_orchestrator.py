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
    Tool,
    ToolRegistry,
    default_registry,
)


def orchestrator(**kwargs):
    return AgentOrchestrator(registry=default_registry(), **kwargs)


def test_happy_path_calculate():
    result = orchestrator().run("calculate 19 * 3 + 1")
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "58"
    assert result.state.steps_executed == 1
    assert "verification achieved=True" in result.state.trace


def test_happy_path_time():
    result = orchestrator().run("what time is it?")
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
    result = agent.run("many steps")
    assert result.status is RunStatus.STEP_LIMIT_EXCEEDED
    assert result.pending_step is not None


def test_execution_time_limit_is_enforced():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="slow",
            description="sleeps",
            permission=PermissionLevel.READ,
            handler=lambda args: time.sleep(0.3) or "done",
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
    result = agent.run("slow")
    assert result.status in (RunStatus.TIMEOUT, RunStatus.ASK_USER)


def test_external_tool_without_approval_callback_asks_user():
    registry = default_registry()
    registry.register(
        Tool(
            name="send_email",
            description="send mail",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args: "sent",
        )
    )
    plan = Plan(
        goal="send",
        steps=[PlanStep(id="s1", description="send", tool_name="send_email")],
    )
    result = AgentOrchestrator(registry=registry, planner=StaticPlanner(plan)).run(
        "send it"
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
            handler=lambda args: "sent",
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
    ).run("send it")
    assert result.status is RunStatus.COMPLETED
    assert result.answer == "sent"


def test_external_tool_with_denial_is_permission_denied():
    registry = default_registry()
    registry.register(
        Tool(
            name="send_email",
            description="send mail",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args: "sent",
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
    ).run("send it")
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
            handler=lambda args: "deleted",
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
    ).run("danger")
    assert result.status is RunStatus.ASK_USER


def test_retry_then_success():
    calls = {"n": 0}

    def flaky(args):
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
    ).run("flaky")
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
            handler=lambda args: (_ for _ in ()).throw(RuntimeError("boom")),
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
    ).run("broken")
    assert result.status is RunStatus.ASK_USER
    assert "boom" in result.reason


def test_unknown_tool_asks_user():
    plan = Plan(
        goal="ghost",
        steps=[PlanStep(id="s1", description="ghost", tool_name="does_not_exist")],
    )
    result = orchestrator(planner=StaticPlanner(plan)).run("ghost")
    assert result.status is RunStatus.ASK_USER
    assert "unknown tool" in result.reason


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        OrchestratorLimits(max_steps=0)
