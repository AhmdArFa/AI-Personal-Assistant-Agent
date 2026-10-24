"""Redaction and audit events.

Anything that gets stored or returned (checkpoints, audit events)
passes through the Redactor first. Secrets come from two places:
values under sensitive argument keys, and key value patterns
inside free text such as tool error messages.

Audit events record what the agent did, with the actor and the
workspace attached, and never with raw secrets.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

_KV_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|credential)"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;\]]+)"
)

REDACTED = "***"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


class Redactor:
    def __init__(self, secret_values: tuple[str, ...] = ()) -> None:
        self._secrets: set[str] = set()
        for value in secret_values:
            self.register_secret(value)

    def register_secret(self, value: Any) -> None:
        if isinstance(value, str) and len(value) >= 4:
            self._secrets.add(value)

    def register_arguments(self, args: dict[str, Any]) -> None:
        """Register values held under sensitive argument keys.

        Only sensitive keys qualify: registering every argument
        value would redact ordinary data (a search query, a file
        name) out of legitimate answers.
        """
        for key, value in args.items():
            if isinstance(value, dict):
                self.register_arguments(value)
            elif is_sensitive_key(str(key)):
                if isinstance(value, str):
                    self.register_secret(value)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        if isinstance(item, str):
                            self.register_secret(item)

    def redact_text(self, text: str) -> str:
        out = _KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        for secret in self._secrets:
            out = out.replace(secret, REDACTED)
        return out

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (REDACTED if is_sensitive_key(str(key)) else self.redact(item))
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value


@dataclass
class AuditEvent:
    task_id: str
    user_id: str
    workspace_id: str
    event_type: str
    tool_name: str = ""
    step_id: str = ""
    outcome: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self.events.append(event)

    def types(self) -> list[str]:
        return [event.event_type for event in self.events]
