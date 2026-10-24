"""Stub model adapter.

This is the adapter the decision brief's fallback path names: if
no provider passes the data gate, Slice 1 proceeds with a stub
adapter and test fixtures, with no private data. The stub is
deterministic, reports zero cost, and answers with canned fixture
text. It exists so development never blocks on D2, and it must
never be presented as a real model.
"""

from __future__ import annotations

from .gateway import ModelRequest, ModelResponse


class StubProvider:
    name = "stub"
    model_version = "stub-1"

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text=f"[stub response] {request.prompt[:80]}",
            tokens_used=max(1, len(request.prompt) // 4),
            cost_usd=0.0,
            model_version=self.model_version,
        )
