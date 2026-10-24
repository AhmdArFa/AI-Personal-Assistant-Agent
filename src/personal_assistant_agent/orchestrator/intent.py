"""Understand Intent: the first step of the execution cycle."""

from __future__ import annotations

from typing import Protocol

from .state import Intent


class IntentUnderstanding(Protocol):
    def understand(self, request: str) -> Intent: ...


class RuleBasedIntentUnderstanding:
    """Deterministic starter implementation.

    This is intentionally simple and testable. An LLM backed
    implementation can replace it later behind the same protocol,
    without touching the orchestrator loop.
    """

    def understand(self, request: str) -> Intent:
        text = request.strip()
        lower = text.lower()
        if lower.endswith("?") or lower.startswith(("what", "when", "who", "how")):
            kind = "question"
        elif any(
            word in lower for word in ("remind", "schedule", "send", "book", "plan")
        ):
            kind = "task"
        else:
            kind = "task"
        return Intent(goal=text, kind=kind, raw_request=request)
