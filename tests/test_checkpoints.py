import json

import pytest

from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    InMemoryCheckpointStore,
    JsonFileCheckpointStore,
    OrchestratorLimits,
    PermissionLevel,
    Plan,
    PlanStep,
    PostgresCheckpointStore,
    RunStatus,
    StaticPlanner,
    TenantContext,
    Tool,
    ToolRegistry,
)

CTX = TenantContext(user_id="user-1", workspace_id="ws-1")
OTHER = TenantContext(user_id="user-2", workspace_id="ws-2")


def _counting_registry(calls, crash):
    registry = ToolRegistry()

    def counted(args, context):
        calls.append(args.message)
        if crash["on"] and args.message == "two":
            raise KeyboardInterrupt("simulated crash")
        return args.message

    from personal_assistant_agent.orchestrator import default_registry

    echo_model = default_registry().get("echo").input_model
    registry.register(
        Tool(
            name="counted",
            description="counted echo",
            permission=PermissionLevel.READ,
            handler=counted,
            input_model=echo_model,
        )
    )
    return registry


def _three_step_plan():
    return Plan(
        goal="three",
        steps=[
            PlanStep(
                id=f"s{i}",
                description=f"step {i}",
                tool_name="counted",
                tool_args={"message": m},
            )
            for i, m in enumerate(["one", "two", "three"], start=1)
        ],
    )


def test_checkpoint_saved_with_observations_and_counters():
    store = InMemoryCheckpointStore()
    agent = AgentOrchestrator(
        registry=_counting_registry([], {"on": False}),
        planner=StaticPlanner(_three_step_plan()),
        checkpoint_store=store,
    )
    result = agent.run("three", context=CTX)
    assert result.status is RunStatus.COMPLETED
    loaded = store.load(result.state.task_id)
    assert loaded is not None
    assert loaded.steps_executed == 3
    assert len([o for o in loaded.observations if o.success]) == 3
    assert loaded.tenant == CTX


def test_resume_after_crash_skips_succeeded_steps():
    store = InMemoryCheckpointStore()
    calls = []
    crash = {"on": True}
    agent = AgentOrchestrator(
        registry=_counting_registry(calls, crash),
        planner=StaticPlanner(_three_step_plan()),
        checkpoint_store=store,
        limits=OrchestratorLimits(max_retries_per_step=0),
    )
    with pytest.raises(KeyboardInterrupt):
        agent.run("three", context=CTX)
    saved_ids = list(store._data.keys())
    assert len(saved_ids) == 1
    task_id = saved_ids[0]
    mid = store.load(task_id)
    assert mid.steps_executed == 1

    crash["on"] = False
    calls.clear()
    agent2 = AgentOrchestrator(
        registry=_counting_registry(calls, crash),
        planner=StaticPlanner(_three_step_plan()),
        checkpoint_store=store,
        limits=OrchestratorLimits(max_retries_per_step=0),
    )
    result = agent2.resume(task_id, context=CTX)
    assert result.status is RunStatus.COMPLETED
    assert calls == ["two", "three"]  # step one was not re-executed
    assert result.state.steps_executed == 3


def test_resume_wrong_tenant_rejected():
    store = InMemoryCheckpointStore()
    agent = AgentOrchestrator(
        registry=_counting_registry([], {"on": False}),
        planner=StaticPlanner(_three_step_plan()),
        checkpoint_store=store,
    )
    result = agent.run("three", context=CTX)
    with pytest.raises(PermissionError):
        agent.resume(result.state.task_id, context=OTHER)


def test_resume_unknown_task_and_missing_store():
    store = InMemoryCheckpointStore()
    agent = AgentOrchestrator(
        registry=_counting_registry([], {"on": False}), checkpoint_store=store
    )
    with pytest.raises(ValueError):
        agent.resume("nope", context=CTX)
    bare = AgentOrchestrator(registry=_counting_registry([], {"on": False}))
    with pytest.raises(ValueError):
        bare.resume("nope", context=CTX)


def test_resume_terminal_checkpoint_reexecutes_nothing():
    store = InMemoryCheckpointStore()
    calls = []
    agent = AgentOrchestrator(
        registry=_counting_registry(calls, {"on": False}),
        planner=StaticPlanner(_three_step_plan()),
        checkpoint_store=store,
    )
    result = agent.run("three", context=CTX)
    calls.clear()
    again = agent.resume(result.state.task_id, context=CTX)
    assert again.status is RunStatus.COMPLETED
    assert calls == []


def test_file_store_roundtrip(tmp_path):
    store = JsonFileCheckpointStore(tmp_path)
    agent = AgentOrchestrator(
        registry=_counting_registry([], {"on": False}),
        planner=StaticPlanner(_three_step_plan()),
        checkpoint_store=store,
    )
    result = agent.run("three", context=CTX)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["task_id"] == result.state.task_id
    loaded = JsonFileCheckpointStore(tmp_path).load(result.state.task_id)
    assert loaded.steps_executed == 3
    store.delete(result.state.task_id)
    assert store.load(result.state.task_id) is None


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._row = None

    def execute(self, sql, params=()):
        if sql.startswith("CREATE TABLE"):
            return
        if sql.startswith("INSERT"):
            self.db[params[0]] = params[1]
            return
        if sql.startswith("SELECT"):
            value = self.db.get(params[0])
            self._row = (value,) if value is not None else None
            return
        if sql.startswith("DELETE"):
            self.db.pop(params[0], None)
            return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        pass

    def close(self):
        pass


def test_postgres_store_against_fake_dbapi():
    db = {}
    store = PostgresCheckpointStore(connect=lambda: _FakeConnection(db))
    agent = AgentOrchestrator(
        registry=_counting_registry([], {"on": False}),
        planner=StaticPlanner(_three_step_plan()),
        checkpoint_store=store,
    )
    result = agent.run("three", context=CTX)
    assert result.state.task_id in db
    loaded = store.load(result.state.task_id)
    assert loaded is not None and loaded.steps_executed == 3
    store.delete(result.state.task_id)
    assert store.load(result.state.task_id) is None
