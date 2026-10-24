import pytest
from fastapi.testclient import TestClient

from personal_assistant_agent.api import create_app
from personal_assistant_agent.orchestrator import InMemoryCheckpointStore
from personal_assistant_agent.platform import (
    ChatService,
    InMemoryConversationStore,
    ModelGateway,
    StubProvider,
    UsageLimits,
)

HEADERS = {"X-User-Id": "user-1", "X-Workspace-Id": "ws-1"}


def _service(limits=None):
    store = InMemoryConversationStore()
    service = ChatService(
        gateway=ModelGateway({"stub": StubProvider()}),
        provider="stub",
        conversations=store,
        limits=limits,
    )
    return service, store


def test_chat_roundtrip_records_usage_and_messages():
    service, store = _service()
    reply = service.chat("user-1", "ws-1", "ما هي خطة المشروع")
    assert "ما هي خطة المشروع" in reply.text  # Arabic round-trips uncorrupted
    assert reply.model_version == "stub-1"
    messages = store.messages(reply.conversation_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].text == "ما هي خطة المشروع"
    assert messages[1].tokens_used > 0
    assert messages[1].provider == "stub"
    second = service.chat(
        "user-1", "ws-1", "and in English please", conversation_id=reply.conversation_id
    )
    assert second.conversation_id == reply.conversation_id
    assert len(store.messages(reply.conversation_id)) == 4


def test_chat_conversation_of_another_tenant_is_hidden():
    service, _store = _service()
    reply = service.chat("user-1", "ws-1", "hello")
    with pytest.raises(PermissionError):
        service.chat("user-2", "ws-1", "hi", conversation_id=reply.conversation_id)
    with pytest.raises(PermissionError):
        service.chat("user-1", "ws-2", "hi", conversation_id=reply.conversation_id)


def test_usage_limit_stops_further_calls():
    service, _store = _service(limits=UsageLimits(max_tokens_per_user=1))
    service.chat("user-1", "ws-1", "a fairly long first message to spend tokens")
    from personal_assistant_agent.platform import UsageLimitExceeded

    with pytest.raises(UsageLimitExceeded):
        service.chat("user-1", "ws-1", "second message")
    # Another user is unaffected
    service.chat("user-2", "ws-1", "second message")


def test_chat_endpoint_member_and_limits():
    checkpoints = InMemoryCheckpointStore()
    app = create_app(checkpoints, usage_limits=UsageLimits(max_tokens_per_user=1))
    client = TestClient(app)
    first = client.post(
        "/api/v1/chat", json={"message": "اشرح خطة المشروع باختصار"}, headers=HEADERS
    )
    assert first.status_code == 200
    assert "اشرح خطة المشروع" in first.json()["reply"]
    second = client.post("/api/v1/chat", json={"message": "more"}, headers=HEADERS)
    assert second.status_code == 429


def test_chat_endpoint_mixed_and_english_roundtrip():
    client = TestClient(create_app(InMemoryCheckpointStore()))
    for text in ("Explain the RAG pipeline", "اشرح ال RAG pipeline بسرعة"):
        response = client.post("/api/v1/chat", json={"message": text}, headers=HEADERS)
        assert response.status_code == 200
        assert text[:20] in response.json()["reply"]
