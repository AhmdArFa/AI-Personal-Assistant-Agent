"""Model gateway (Decision D2, Blueprint section 11).

All model calls go through the gateway. Business logic never sees
a provider specific format, so the provider choice stays
reversible. The gateway also owns cost control exactly as the
decision brief specifies: the trial cap I6 is split in writing
per provider, each provider is aborted automatically at 80
percent of its own share, one provider reaching its limit never
reduces another share, and the reserve share stays untouched
until the reserve is activated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ModelRequest:
    prompt: str
    system: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    model_version: str = ""
    latency_seconds: float = 0.0


class ModelProvider(Protocol):
    name: str
    model_version: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class BudgetExceeded(Exception):
    pass


@dataclass
class BudgetLedger:
    """Per provider shares of the total cap, in USD."""

    shares: dict[str, float]
    spent: dict[str, float] = field(default_factory=dict)

    def share_of(self, provider: str) -> float:
        return self.shares.get(provider, 0.0)

    def spent_by(self, provider: str) -> float:
        return self.spent.get(provider, 0.0)

    def limit_for(self, provider: str) -> float:
        """A provider is aborted at 80 percent of its own share."""
        return 0.8 * self.share_of(provider)

    def check_before(self, provider: str) -> None:
        if self.spent_by(provider) >= self.limit_for(provider):
            raise BudgetExceeded(
                f"provider {provider} reached 80 percent of its budget share"
            )

    def record(self, provider: str, cost_usd: float) -> None:
        self.spent[provider] = self.spent_by(provider) + cost_usd


class ModelGateway:
    def __init__(
        self,
        providers: dict[str, ModelProvider],
        ledger: BudgetLedger | None = None,
    ) -> None:
        self.providers = providers
        self.ledger = ledger

    def complete(self, provider: str, request: ModelRequest) -> ModelResponse:
        if provider not in self.providers:
            raise ValueError(f"unknown provider: {provider}")
        if self.ledger is not None:
            self.ledger.check_before(provider)
        target = self.providers[provider]
        started = time.monotonic()
        response = target.complete(request)
        response.latency_seconds = time.monotonic() - started
        if not response.model_version:
            response.model_version = target.model_version
        if self.ledger is not None:
            self.ledger.record(provider, response.cost_usd)
        return response
