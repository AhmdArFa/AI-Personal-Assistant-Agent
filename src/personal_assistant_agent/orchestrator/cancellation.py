"""Cooperative cancellation for orchestrator runs.

Cancellation is cooperative: the orchestrator checks the token
before every step, before every retry, and while it waits on a
long tool call. Cancelling a run that already finished is a
no-op, because nothing reads the token after a terminal result.
"""

from __future__ import annotations

import threading


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


class RunCancelled(Exception):
    """Raised inside the execution loop when cancellation is seen."""
