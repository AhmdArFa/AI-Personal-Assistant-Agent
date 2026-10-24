from fastapi.testclient import TestClient

from personal_assistant_agent.api import create_app
from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    InMemoryCheckpointStore,
    Plan,
    PlanStep,
    StaticPlanner,
    TenantContext,
    default_registry,
)

CTX = TenantContext(user_id="user-1", workspace_id="ws-1")
HEADERS = {"X-User-Id": "user-1", "X-Workspace-Id": "ws-1"}
OTHER = {"X-User-Id": "user-2", "X-Workspace-Id": "ws-1"}


def _client_with_task():
    store = InMemoryCheckpointStore()
    plan = Plan(goal="calc then echo", steps=[
        PlanStep(id="s1", description="calc", tool_name="calculator",
                 tool_args={"expression": "2 + 2"}),
        PlanStep(id="s2", description="echo", tool_name="echo",
                 tool_args={"message": "done"})])
    result = AgentOrchestrator(
        registry=default_registry(), planner=StaticPlanner(plan),
        checkpoint_store=store).run("calc then echo", context=CTX)
    return TestClient(create_app(store)), result.state.task_id


def test_status_for_own_task():
    client, task_id = _client_with_task()
    response = client.get(f"/api/v1/tasks/{task_id}", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["completed_steps"] == ["s1", "s2"]
    assert body["current_step"] is None
    assert body["error_summary"] == ""
    # Nothing sensitive is ever exposed
    for key in ("trace", "observations", "request", "approvals"):
        assert key not in body


def test_other_tenant_gets_404():
    client, task_id = _client_with_task()
    response = client.get(f"/api/v1/tasks/{task_id}", headers=OTHER)
    assert response.status_code == 404


def test_unknown_task_gets_404():
    client, _ = _client_with_task()
    response = client.get("/api/v1/tasks/does-not-exist", headers=HEADERS)
    assert response.status_code == 404
