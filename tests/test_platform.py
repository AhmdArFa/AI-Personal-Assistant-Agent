import pytest
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
from personal_assistant_agent.platform import (
    AuthenticationError,
    BudgetExceeded,
    BudgetLedger,
    DevIdentityProvider,
    Identity,
    InMemoryTenancyStore,
    ModelGateway,
    ModelRequest,
    ModelResponse,
    StubProvider,
    ToolCall,
    TrialRunner,
    get_or_create_user,
    resolve_membership,
)


@pytest.fixture
def store():
    return InMemoryTenancyStore()


@pytest.fixture
def provider():
    return DevIdentityProvider()


def _user(store, provider, subject="sub-1", email="a@example.com"):
    token = provider.issue(subject, email)
    return get_or_create_user(store, provider.authenticate(token))


# Brief D1 checks 3 and 4: provisioning and revocation
def test_first_login_creates_user_keyed_by_subject(store, provider):
    first = _user(store, provider)
    second = _user(store, provider)
    assert first.user_id == second.user_id


def test_revoked_subject_is_rejected(store, provider):
    token = provider.issue("sub-9", "x@example.com")
    provider.revoke("sub-9")
    with pytest.raises(AuthenticationError):
        provider.authenticate(token)


# Brief D1 check 6: invitation attaches to the invited workspace only
def test_invitation_flow(store, provider):
    owner = _user(store, provider, "owner", "owner@example.com")
    mine = store.create_workspace(owner, "mine")
    other = store.create_workspace(owner, "other")
    invitation = store.invite(mine.workspace_id, "guest@example.com")
    guest = _user(store, provider, "guest", "guest@example.com")
    assert store.accept_invitation(invitation.invitation_id, guest)
    assert store.is_member(mine.workspace_id, guest.user_id)
    assert not store.is_member(other.workspace_id, guest.user_id)
    # wrong email cannot accept someone else's invitation
    invitation2 = store.invite(mine.workspace_id, "someone@example.com")
    assert not store.accept_invitation(invitation2.invitation_id, guest)


# Brief D1 checks 7 and 8: two workspaces, removal blocks at once
def test_two_workspaces_and_immediate_removal(store, provider):
    owner = _user(store, provider, "owner", "owner@example.com")
    member = _user(store, provider, "member", "m@example.com")
    ws_a = store.create_workspace(owner, "a")
    ws_b = store.create_workspace(owner, "b")
    store.add_member(ws_a.workspace_id, member.user_id)
    store.add_member(ws_b.workspace_id, member.user_id)
    resolve_membership(store, member.user_id, ws_a.workspace_id)
    resolve_membership(store, member.user_id, ws_b.workspace_id)
    store.remove_member(ws_a.workspace_id, member.user_id)
    with pytest.raises(PermissionError):
        resolve_membership(store, member.user_id, ws_a.workspace_id)
    resolve_membership(store, member.user_id, ws_b.workspace_id)


# Brief D1 checks 9 and 10: authority is the database, never a claim
def test_cross_workspace_access_denied(store, provider):
    owner = _user(store, provider, "owner", "owner@example.com")
    stranger = _user(store, provider, "stranger", "s@example.com")
    ws = store.create_workspace(owner, "private")
    with pytest.raises(PermissionError):
        resolve_membership(store, stranger.user_id, ws.workspace_id)


# Gateway budget shares (brief D2 cost control)
class _PricedProvider:
    name = "priced"
    model_version = "priced-1"

    def __init__(self, cost):
        self.cost = cost

    def complete(self, request):
        return ModelResponse(text="ok", tokens_used=10, cost_usd=self.cost)


def test_budget_share_aborts_at_80_percent_and_shares_are_independent():
    ledger = BudgetLedger(shares={"a": 10.0, "b": 10.0, "reserve": 5.0})
    gateway = ModelGateway(
        {
            "a": _PricedProvider(5.0),
            "b": _PricedProvider(1.0),
            "reserve": _PricedProvider(1.0),
        },
        ledger=ledger,
    )
    gateway.complete("a", ModelRequest(prompt="one"))  # spent 5 of limit 8
    gateway.complete("a", ModelRequest(prompt="two"))  # check passes, spent 10
    with pytest.raises(BudgetExceeded):
        gateway.complete("a", ModelRequest(prompt="three"))
    gateway.complete("b", ModelRequest(prompt="still fine"))
    assert ledger.spent_by("reserve") == 0.0  # reserve untouched


def test_stub_provider_through_gateway():
    gateway = ModelGateway({"stub": StubProvider()})
    response = gateway.complete("stub", ModelRequest(prompt="hello world"))
    assert response.model_version == "stub-1"
    assert response.text.startswith("[stub response]")
    assert response.cost_usd == 0.0


# Trial harness mechanics
class _SmartFake:
    name = "fake"
    model_version = "fake-1"
    ANSWER = (
        "بيانات اجتماع النوم الرياض أسبوع الذكاء قهوة تقرير الثلاثاء المبيعات "
        "light meeting Paris exercise SQL virus RAG embedding sprint API "
        "sales Tuesday"
    )

    def complete(self, request):
        for tool in ("search_documents", "calculator", "echo", "send_email"):
            if f"use the {tool} tool" in request.prompt:
                return ModelResponse(
                    tool_calls=[ToolCall(name=tool, arguments={})],
                    tokens_used=10,
                    cost_usd=0.01,
                    model_version="fake-1",
                )
        return ModelResponse(
            text=self.ANSWER, tokens_used=10, cost_usd=0.01, model_version="fake-1"
        )


def test_trial_runner_scores_all_30_tasks():
    gateway = ModelGateway({"fake": _SmartFake()})
    report = TrialRunner(gateway).run("fake")
    assert len(report.results) == 30
    groups = {}
    for result in report.results:
        groups[result.group] = groups.get(result.group, 0) + 1
    assert groups == {
        "arabic": 8,
        "english": 6,
        "mixed": 4,
        "tool_call": 6,
        "cross_language": 4,
        "injection": 2,
    }
    assert report.success_rate == 1.0
    assert report.total_cost == pytest.approx(0.30)
    assert report.cost_per_successful_task == pytest.approx(0.01)
    assert report.latency_percentile(95) >= report.latency_percentile(50)


def test_review_pack_is_blind_and_arabic_focused():
    gateway = ModelGateway({"fake": _SmartFake()})
    pack = TrialRunner(gateway).run("fake").review_pack()
    assert len(pack) == 16  # arabic 8 + mixed 4 + cross language 4
    assert all("provider" not in item for item in pack)
    assert all(item["group"] in ("arabic", "mixed", "cross_language") for item in pack)


# API in platform mode
def _platform_client():
    tenancy = InMemoryTenancyStore()
    idp = DevIdentityProvider()
    user = get_or_create_user(tenancy, Identity(subject="sub-1", email="a@example.com"))
    ws = tenancy.create_workspace(user, "ws")
    other_ws = tenancy.create_workspace(
        get_or_create_user(tenancy, Identity(subject="sub-2", email="b@example.com")),
        "other",
    )
    checkpoints = InMemoryCheckpointStore()
    plan = Plan(
        goal="echo",
        steps=[
            PlanStep(
                id="s1",
                description="echo",
                tool_name="echo",
                tool_args={"message": "hi"},
            )
        ],
    )
    result = AgentOrchestrator(
        registry=default_registry(),
        planner=StaticPlanner(plan),
        checkpoint_store=checkpoints,
    ).run(
        "echo",
        context=TenantContext(user_id=user.user_id, workspace_id=ws.workspace_id),
    )
    client = TestClient(create_app(checkpoints, tenancy, idp))
    token = idp.issue("sub-1", "a@example.com")
    return (
        client,
        result.state.task_id,
        ws.workspace_id,
        other_ws.workspace_id,
        token,
        idp,
    )


def test_api_platform_mode_happy_path():
    client, task_id, ws_id, _other, token, _idp = _platform_client()
    response = client.get(
        f"/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws_id},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_api_platform_mode_forged_workspace_is_404():
    client, task_id, _ws, other_ws, token, _idp = _platform_client()
    response = client.get(
        f"/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": other_ws},
    )
    assert response.status_code == 404


def test_api_platform_mode_missing_and_revoked_token():
    client, task_id, ws_id, _o, token, idp = _platform_client()
    missing = client.get(f"/api/v1/tasks/{task_id}", headers={"X-Workspace-Id": ws_id})
    assert missing.status_code == 401
    idp.revoke("sub-1")
    revoked = client.get(
        f"/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws_id},
    )
    assert revoked.status_code == 401
