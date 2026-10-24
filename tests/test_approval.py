import copy

import pytest

from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    InMemoryCheckpointStore,
    PermissionLevel,
    Plan,
    PlanStep,
    RunStatus,
    StaticPlanner,
    StopReason,
    StrictInput,
    TenantContext,
    Tool,
    ToolRegistry,
    arguments_hash,
)

CTX = TenantContext(user_id="user-1", workspace_id="ws-1")
OTHER = TenantContext(user_id="user-2", workspace_id="ws-2")


class SendInput(StrictInput):
    to: str


def _agent(store, sent, to="team@example.com"):
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="send_email",
            description="send mail",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args, context: sent.append(args.to) or f"sent to {args.to}",
            input_model=SendInput,
        )
    )
    plan = Plan(
        goal="send",
        steps=[
            PlanStep(
                id="s1",
                description="send",
                tool_name="send_email",
                tool_args={"to": to},
            )
        ],
    )
    return AgentOrchestrator(
        registry=registry, planner=StaticPlanner(plan), checkpoint_store=store
    )


def test_waiting_approval_creates_bound_record():
    store = InMemoryCheckpointStore()
    sent = []
    result = _agent(store, sent).run("send", context=CTX)
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert sent == []
    assert len(result.state.approvals) == 1
    record = result.state.approvals[0]
    assert record.status == "pending"
    assert record.tool_name == "send_email"
    assert record.arguments_hash == arguments_hash(
        "send_email", {"to": "team@example.com"}
    )
    assert len(record.arguments_hash) == 64


def test_approve_resumes_and_executes_exact_arguments():
    store = InMemoryCheckpointStore()
    sent = []
    agent = _agent(store, sent)
    waiting = agent.run("send", context=CTX)
    result = agent.decide_approval(waiting.state.task_id, context=CTX, approved=True)
    assert result.status is RunStatus.COMPLETED
    assert sent == ["team@example.com"]
    assert result.state.approvals[0].status == "approved"
    assert result.state.approvals[0].decided_by == "user-1"


def test_deny_records_and_never_executes():
    store = InMemoryCheckpointStore()
    sent = []
    agent = _agent(store, sent)
    waiting = agent.run("send", context=CTX)
    result = agent.decide_approval(waiting.state.task_id, context=CTX, approved=False)
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.PERMISSION_DENIED
    assert sent == []
    assert result.state.approvals[0].status == "denied"


def test_changed_arguments_require_new_approval():
    store = InMemoryCheckpointStore()
    sent = []
    agent = _agent(store, sent)
    waiting = agent.run("send", context=CTX)
    task_id = waiting.state.task_id

    snapshot = copy.deepcopy(store._data[task_id])
    snapshot["plan"]["steps"][0]["tool_args"]["to"] = "other@example.com"
    store.save_snapshot(task_id, snapshot)

    result = agent.decide_approval(task_id, context=CTX, approved=True)
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert sent == []
    statuses = [a.status for a in result.state.approvals]
    assert statuses == ["superseded", "pending"]
    assert result.state.approvals[1].arguments_hash == arguments_hash(
        "send_email", {"to": "other@example.com"}
    )

    final = agent.decide_approval(task_id, context=CTX, approved=True)
    assert final.status is RunStatus.COMPLETED
    assert sent == ["other@example.com"]


def test_decide_approval_wrong_tenant_and_wrong_state():
    store = InMemoryCheckpointStore()
    sent = []
    agent = _agent(store, sent)
    waiting = agent.run("send", context=CTX)
    with pytest.raises(PermissionError):
        agent.decide_approval(waiting.state.task_id, context=OTHER, approved=True)
    done = agent.decide_approval(waiting.state.task_id, context=CTX, approved=True)
    assert done.status is RunStatus.COMPLETED
    with pytest.raises(ValueError):
        agent.decide_approval(waiting.state.task_id, context=CTX, approved=True)
