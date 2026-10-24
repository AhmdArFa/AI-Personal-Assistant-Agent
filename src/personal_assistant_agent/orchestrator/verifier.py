"""Verify: the gate before any result is returned to the user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .state import AgentState


@dataclass
class VerificationResult:
    achieved: bool
    answer: str | None = None
    reason: str = ""


class Verifier(Protocol):
    def verify(self, state: AgentState) -> VerificationResult: ...


class DefaultVerifier:
    """Goal is achieved when every planned step has a successful
    observation. The answer is built only from observed tool results,
    never from model memory."""

    def verify(self, state: AgentState) -> VerificationResult:
        if state.plan is None or not state.plan.steps:
            return VerificationResult(False, reason="no plan steps to verify")
        succeeded = {o.step_id for o in state.observations if o.success}
        missing = [s.id for s in state.plan.steps if s.id not in succeeded]
        if missing:
            return VerificationResult(
                False, reason=f"steps without a successful result: {missing}"
            )
        results = state.successful_results()
        answer = "\n".join(str(r) for r in results)
        return VerificationResult(True, answer=answer, reason="all steps observed")
