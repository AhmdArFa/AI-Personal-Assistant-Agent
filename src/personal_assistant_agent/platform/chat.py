"""Chat service (work package W4): one authenticated, workspace
scoped chat turn through the model gateway.

Conversations and messages persist behind a store interface
(Blueprint section 13). Every assistant message records the
provider, model version, latency, tokens, and cost of the call
that produced it. Usage limits are per user and per workspace and
are enforced before the gateway is called; they are deliberately
separate from the D2 trial BudgetLedger, which caps a one-off
evaluation, not product usage.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from .gateway import ModelGateway, ModelRequest


@dataclass
class Conversation:
    conversation_id: str
    user_id: str
    workspace_id: str


@dataclass
class Message:
    message_id: str
    conversation_id: str
    role: str  # user, assistant
    text: str
    provider: str = ""
    model_version: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    latency_seconds: float = 0.0


class ConversationStore(Protocol):
    def create_conversation(self, user_id: str, workspace_id: str) -> Conversation: ...

    def conversation(self, conversation_id: str) -> Conversation | None: ...

    def add_message(self, message: Message) -> None: ...

    def messages(self, conversation_id: str) -> list[Message]: ...


@dataclass
class InMemoryConversationStore:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _conversations: dict[str, Conversation] = field(default_factory=dict)
    _messages: dict[str, list[Message]] = field(default_factory=dict)

    def create_conversation(self, user_id: str, workspace_id: str) -> Conversation:
        conversation = Conversation(uuid.uuid4().hex, user_id, workspace_id)
        with self._lock:
            self._conversations[conversation.conversation_id] = conversation
            self._messages[conversation.conversation_id] = []
        return conversation

    def conversation(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

    def add_message(self, message: Message) -> None:
        with self._lock:
            self._messages.setdefault(message.conversation_id, []).append(message)

    def messages(self, conversation_id: str) -> list[Message]:
        return list(self._messages.get(conversation_id, []))


@dataclass
class UsageLimits:
    max_tokens_per_user: int | None = None
    max_tokens_per_workspace: int | None = None
    max_cost_per_user_usd: float | None = None
    max_cost_per_workspace_usd: float | None = None


class UsageLimitExceeded(Exception):
    pass


@dataclass
class UsageLedger:
    """All-time usage totals per user and per workspace (windowing
    is a later concern; the enforcement point is what matters)."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _user_tokens: dict[str, int] = field(default_factory=dict)
    _user_cost: dict[str, float] = field(default_factory=dict)
    _workspace_tokens: dict[str, int] = field(default_factory=dict)
    _workspace_cost: dict[str, float] = field(default_factory=dict)

    def record(self, user_id: str, workspace_id: str, tokens: int, cost: float) -> None:
        with self._lock:
            self._user_tokens[user_id] = self._user_tokens.get(user_id, 0) + tokens
            self._user_cost[user_id] = self._user_cost.get(user_id, 0.0) + cost
            self._workspace_tokens[workspace_id] = (
                self._workspace_tokens.get(workspace_id, 0) + tokens
            )
            self._workspace_cost[workspace_id] = (
                self._workspace_cost.get(workspace_id, 0.0) + cost
            )

    def check(self, user_id: str, workspace_id: str, limits: UsageLimits) -> None:
        with self._lock:
            if (
                limits.max_tokens_per_user is not None
                and self._user_tokens.get(user_id, 0) >= limits.max_tokens_per_user
            ):
                raise UsageLimitExceeded("per user token limit reached")
            if (
                limits.max_tokens_per_workspace is not None
                and self._workspace_tokens.get(workspace_id, 0)
                >= limits.max_tokens_per_workspace
            ):
                raise UsageLimitExceeded("per workspace token limit reached")
            if (
                limits.max_cost_per_user_usd is not None
                and self._user_cost.get(user_id, 0.0) >= limits.max_cost_per_user_usd
            ):
                raise UsageLimitExceeded("per user cost limit reached")
            if (
                limits.max_cost_per_workspace_usd is not None
                and self._workspace_cost.get(workspace_id, 0.0)
                >= limits.max_cost_per_workspace_usd
            ):
                raise UsageLimitExceeded("per workspace cost limit reached")


@dataclass
class ChatReply:
    conversation_id: str
    text: str
    provider: str
    model_version: str
    tokens_used: int
    cost_usd: float


class ChatService:
    def __init__(
        self,
        gateway: ModelGateway,
        provider: str,
        conversations: ConversationStore,
        usage: UsageLedger | None = None,
        limits: UsageLimits | None = None,
    ) -> None:
        self.gateway = gateway
        self.provider = provider
        self.conversations = conversations
        self.usage = usage or UsageLedger()
        self.limits = limits or UsageLimits()

    def chat(
        self,
        user_id: str,
        workspace_id: str,
        message: str,
        conversation_id: str | None = None,
    ) -> ChatReply:
        if conversation_id is None:
            conversation = self.conversations.create_conversation(user_id, workspace_id)
        else:
            found = self.conversations.conversation(conversation_id)
            if (
                found is None
                or found.user_id != user_id
                or found.workspace_id != workspace_id
            ):
                raise PermissionError("conversation not found for this tenant")
            conversation = found

        self.usage.check(user_id, workspace_id, self.limits)
        self.conversations.add_message(
            Message(
                message_id=uuid.uuid4().hex,
                conversation_id=conversation.conversation_id,
                role="user",
                text=message,
            )
        )
        response = self.gateway.complete(self.provider, ModelRequest(prompt=message))
        self.conversations.add_message(
            Message(
                message_id=uuid.uuid4().hex,
                conversation_id=conversation.conversation_id,
                role="assistant",
                text=response.text,
                provider=self.provider,
                model_version=response.model_version,
                tokens_used=response.tokens_used,
                cost_usd=response.cost_usd,
                latency_seconds=response.latency_seconds,
            )
        )
        self.usage.record(
            user_id, workspace_id, response.tokens_used, response.cost_usd
        )
        return ChatReply(
            conversation_id=conversation.conversation_id,
            text=response.text,
            provider=self.provider,
            model_version=response.model_version,
            tokens_used=response.tokens_used,
            cost_usd=response.cost_usd,
        )
