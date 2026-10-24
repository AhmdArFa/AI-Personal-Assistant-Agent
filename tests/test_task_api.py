import copy
import threading
import time

from fastapi.testclient import TestClient

from personal_assistant_agent.api import create_app
from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    InMemoryCheckpointStore,
    PermissionLevel,
    Plan,
    PlanStep,
    StaticPlanner,
    StrictInput,
    Tool,
    ToolRegistry,
    default_registry,
)
from personal_assistant_agent.platform import (
    DevIdentityProvider,
    Identity,
    InMemoryTenancyStore,
    get_or_create_user,
)

HEADERS = {"X-User-Id": "user-1", "X-Workspace-Id": "ws-1"}
OTHER = {"X-User-Id": "user-2", "X-Workspace-Id": "ws-1"}


def _client(orchestrator=None, store=None):
    store = store or InMemoryCheckpointStore()
    return TestClient(create_app(store, orchestrator=orchestrator)), store


def test_health():
    client, _ = _client()
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_create_and_status_roundtrip():
    client, _ = _client()
    created = client.post("/api/v1/tasks", json={"request": "hello"}, headers=HEADERS)
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "completed"
    status = client.get(f"/api/v1/tasks/{body['task_id']}", headers=HEADERS)
    assert status.status_code == 200
    assert status.json()["status"] == "completed"


def test_other_tenant_404_on_every_route():
    client, _ = _client()
    created = client.post("/api/v1/tasks", json={"request": "hello"}, headers=HEADERS)
    task_id = created.json()["task_id"]
    assert client.get(f"/api/v1/tasks/{task_id}", headers=OTHER).status_code == 404
    assert (
        client.post(f"/api/v1/tasks/{task_id}/cancel", headers=OTHER).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/tasks/{task_id}/approve", json={"approved": True}, headers=OTHER
        ).status_code
        == 404
    )


class SendInput(StrictInput):
    to: str


def _approval_orchestrator(store, sent):
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="send_email",
            description="send mail",
            permission=PermissionLevel.EXTERNAL_EFFECT,
            handler=lambda args, context: sent.append(args.to) or "sent",
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
                tool_args={"to": "team@example.com"},
            )
        ],
    )
    return AgentOrchestrator(
        registry=registry, planner=StaticPlanner(plan), checkpoint_store=store
    )


def test_approve_endpoint_completes_task():
    store = InMemoryCheckpointStore()
    sent = []
    client, _ = _client(_approval_orchestrator(store, sent), store)
    created = client.post("/api/v1/tasks", json={"request": "send it"}, headers=HEADERS)
    assert created.json()["status"] == "waiting_for_approval"
    task_id = created.json()["task_id"]
    approved = client.post(
        f"/api/v1/tasks/{task_id}/approve", json={"approved": True}, headers=HEADERS
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"
    assert sent == ["team@example.com"]


def test_approve_with_changed_arguments_yields_new_request():
    store = InMemoryCheckpointStore()
    sent = []
    client, _ = _client(_approval_orchestrator(store, sent), store)
    created = client.post("/api/v1/tasks", json={"request": "send it"}, headers=HEADERS)
    task_id = created.json()["task_id"]
    snapshot = copy.deepcopy(store._data[task_id])
    snapshot["plan"]["steps"][0]["tool_args"]["to"] = "other@example.com"
    store.save_snapshot(task_id, snapshot)
    again = client.post(
        f"/api/v1/tasks/{task_id}/approve", json={"approved": True}, headers=HEADERS
    )
    assert again.json()["status"] == "waiting_for_approval"
    assert sent == []
    final = client.post(
        f"/api/v1/tasks/{task_id}/approve", json={"approved": True}, headers=HEADERS
    )
    assert final.json()["status"] == "completed"
    assert sent == ["other@example.com"]


def test_approve_denied_and_wrong_state_conflict():
    store = InMemoryCheckpointStore()
    sent = []
    client, _ = _client(_approval_orchestrator(store, sent), store)
    created = client.post("/api/v1/tasks", json={"request": "send it"}, headers=HEADERS)
    task_id = created.json()["task_id"]
    denied = client.post(
        f"/api/v1/tasks/{task_id}/approve", json={"approved": False}, headers=HEADERS
    )
    assert denied.json()["status"] == "failed"
    assert denied.json()["stop_reason"] == "permission_denied"
    conflict = client.post(
        f"/api/v1/tasks/{task_id}/approve", json={"approved": True}, headers=HEADERS
    )
    assert conflict.status_code == 409


def test_cancel_stops_a_running_task():
    store = InMemoryCheckpointStore()
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
    orchestrator = AgentOrchestrator(
        registry=registry, planner=StaticPlanner(plan), checkpoint_store=store
    )
    app = create_app(store, orchestrator=orchestrator)
    outcome = {}

    def _post():
        with TestClient(app) as client:
            outcome["response"] = client.post(
                "/api/v1/tasks",
                json={"request": "go", "task_id": "task-cancel-1"},
                headers=HEADERS,
            )

    thread = threading.Thread(target=_post)
    thread.start()
    deadline = time.monotonic() + 3
    while store.load("task-cancel-1") is None and time.monotonic() < deadline:
        time.sleep(0.05)
    with TestClient(app) as client:
        cancelled = client.post("/api/v1/tasks/task-cancel-1/cancel", headers=HEADERS)
    assert cancelled.json()["status"] == "cancelling"
    thread.join(timeout=4)
    assert not thread.is_alive()
    assert outcome["response"].json()["status"] == "cancelled"


def test_platform_mode_non_member_rejected():
    tenancy = InMemoryTenancyStore()
    idp = DevIdentityProvider()
    owner = get_or_create_user(
        tenancy, Identity(subject="owner", email="o@example.com")
    )
    ws = tenancy.create_workspace(owner, "ws")
    checkpoints = InMemoryCheckpointStore()
    orchestrator = AgentOrchestrator(
        registry=default_registry(), checkpoint_store=checkpoints
    )
    client = TestClient(create_app(checkpoints, tenancy, idp, orchestrator))
    stranger_token = idp.issue("stranger", "s@example.com")
    headers = {
        "Authorization": f"Bearer {stranger_token}",
        "X-Workspace-Id": ws.workspace_id,
    }
    assert (
        client.post(
            "/api/v1/tasks", json={"request": "hello"}, headers=headers
        ).status_code
        == 404
    )
    owner_token = idp.issue("owner", "o@example.com")
    ok = client.post(
        "/api/v1/tasks",
        json={"request": "hello"},
        headers={
            "Authorization": f"Bearer {owner_token}",
            "X-Workspace-Id": ws.workspace_id,
        },
    )
    assert ok.status_code == 201
