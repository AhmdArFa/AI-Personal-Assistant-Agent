import json

from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    InMemoryAuditSink,
    InMemoryCheckpointStore,
    OrchestratorLimits,
    PermissionLevel,
    Plan,
    PlanStep,
    Redactor,
    RunStatus,
    StaticPlanner,
    StrictInput,
    TenantContext,
    Tool,
    ToolRegistry,
)

CTX = TenantContext(user_id="user-1", workspace_id="ws-1")
SECRET = "sk_live_1234567890"


class KeyInput(StrictInput):
    api_key: str
    query: str = "report"


def test_redactor_masks_sensitive_keys_and_registered_values():
    redactor = Redactor(secret_values=(SECRET,))
    out = redactor.redact(
        {
            "password": "hunter2",
            "note": f"the key is {SECRET} ok",
            "nested": {"line": "token=abc12345 done"},
        }
    )
    assert out["password"] == "***"
    assert SECRET not in out["note"]
    assert "abc12345" not in out["nested"]["line"]


def test_secret_never_appears_in_stored_trace_or_audit():
    store = InMemoryCheckpointStore()
    sink = InMemoryAuditSink()

    def failing(args, context):
        raise RuntimeError(f"upstream rejected key {args.api_key}")

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="call_api",
            description="call an api",
            permission=PermissionLevel.READ,
            handler=failing,
            input_model=KeyInput,
        )
    )
    plan = Plan(
        goal="call",
        steps=[
            PlanStep(
                id="s1",
                description="call",
                tool_name="call_api",
                tool_args={"api_key": SECRET, "query": "sales"},
            )
        ],
    )
    agent = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        checkpoint_store=store,
        audit_sink=sink,
        limits=OrchestratorLimits(max_retries_per_step=0),
    )
    result = agent.run("call", context=CTX)
    assert result.status is RunStatus.FAILED

    stored = json.dumps(store._data)
    assert SECRET not in stored
    audited = json.dumps([vars(e) for e in sink.events], default=str)
    assert SECRET not in audited
    returned = json.dumps([vars(o) for o in result.state.observations], default=str)
    assert SECRET not in returned
    assert SECRET not in "\n".join(result.state.trace)


def test_audit_events_cover_choice_approval_execution_and_stop():
    sink = InMemoryAuditSink()
    registry = ToolRegistry()
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
    agent = AgentOrchestrator(
        registry=registry,
        planner=StaticPlanner(plan),
        audit_sink=sink,
        approval_callback=lambda step, tool: True,
    )
    result = agent.run("send", context=CTX)
    assert result.status is RunStatus.COMPLETED
    types = sink.types()
    for expected in ("tool_chosen", "approval_granted", "tool_executed", "run_stopped"):
        assert expected in types
    stop = sink.events[-1]
    assert stop.event_type == "run_stopped"
    assert stop.user_id == "user-1"
    assert stop.workspace_id == "ws-1"
    assert stop.task_id == result.state.task_id
    assert stop.details["stop_reason"] == "goal_achieved"


def test_audit_records_denied_approval():
    sink = InMemoryAuditSink()
    registry = ToolRegistry()
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
        audit_sink=sink,
        approval_callback=lambda step, tool: False,
    ).run("send", context=CTX)
    assert result.status is RunStatus.FAILED
    assert "approval_denied" in sink.types()
    assert "tool_executed" not in sink.types()
