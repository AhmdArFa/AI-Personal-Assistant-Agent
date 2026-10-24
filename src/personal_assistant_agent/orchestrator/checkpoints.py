"""Checkpointing: persist agent state so a run can resume.

The store sits behind one small interface. State is saved after
every observation, so a crash loses at most the step that was
running. Counters are part of the snapshot, so resuming never
resets the limits.

Stores shipped here:
- InMemoryCheckpointStore, for tests and single process runs.
- JsonFileCheckpointStore, durable on disk, one file per task.
- PostgresCheckpointStore, for production, taking a DB-API
  connection factory (for example psycopg). Its SQL is standard
  and its logic is tested against a fake DB-API connection.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .state import (
    AgentState,
    ApprovalRecord,
    Intent,
    Observation,
    Plan,
    PlanStep,
    RunStatus,
    TenantContext,
)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return {"__repr__": repr(value)}


def serialize_state(state: AgentState) -> dict[str, Any]:
    return {
        "task_id": state.task_id,
        "request": state.request,
        "tenant": (
            {
                "user_id": state.tenant.user_id,
                "workspace_id": state.tenant.workspace_id,
            }
            if state.tenant is not None
            else None
        ),
        "status": state.status.value,
        "intent": (
            {
                "goal": state.intent.goal,
                "kind": state.intent.kind,
                "raw_request": state.intent.raw_request,
            }
            if state.intent is not None
            else None
        ),
        "plan": (
            {
                "goal": state.plan.goal,
                "steps": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "tool_name": s.tool_name,
                        "tool_args": _json_safe(s.tool_args),
                    }
                    for s in state.plan.steps
                ],
            }
            if state.plan is not None
            else None
        ),
        "observations": [
            {
                "step_id": o.step_id,
                "tool_name": o.tool_name,
                "success": o.success,
                "result": _json_safe(o.result),
                "error": o.error,
                "attempt": o.attempt,
                "duration_seconds": o.duration_seconds,
            }
            for o in state.observations
        ],
        "approvals": [
            {
                "id": a.id,
                "step_id": a.step_id,
                "tool_name": a.tool_name,
                "tool_args": _json_safe(a.tool_args),
                "arguments_hash": a.arguments_hash,
                "status": a.status,
                "decided_by": a.decided_by,
                "created_at": a.created_at,
                "decided_at": a.decided_at,
            }
            for a in state.approvals
        ],
        "approved_arguments": dict(state.approved_arguments),
        "tool_calls": state.tool_calls,
        "tokens_used": state.tokens_used,
        "cost_used": state.cost_used,
        "steps_executed": state.steps_executed,
        "replans_used": state.replans_used,
        "trace": list(state.trace),
    }


def deserialize_state(data: dict[str, Any]) -> AgentState:
    tenant = data.get("tenant")
    intent = data.get("intent")
    plan = data.get("plan")
    state = AgentState(
        request=data["request"],
        tenant=TenantContext(**tenant) if tenant else None,
        status=RunStatus(data.get("status", "queued")),
        task_id=data.get("task_id", ""),
        intent=Intent(**intent) if intent else None,
        plan=(
            Plan(
                goal=plan["goal"],
                steps=[PlanStep(**s) for s in plan["steps"]],
            )
            if plan
            else None
        ),
        observations=[Observation(**o) for o in data.get("observations", [])],
        approvals=[ApprovalRecord(**a) for a in data.get("approvals", [])],
        approved_arguments=dict(data.get("approved_arguments", {})),
        steps_executed=data.get("steps_executed", 0),
        tool_calls=data.get("tool_calls", 0),
        tokens_used=data.get("tokens_used", 0),
        cost_used=data.get("cost_used", 0.0),
        replans_used=data.get("replans_used", 0),
        trace=list(data.get("trace", [])),
    )
    return state


class CheckpointStore(Protocol):
    def save(self, state: AgentState) -> None: ...

    def save_snapshot(self, task_id: str, data: dict[str, Any]) -> None: ...

    def load(self, task_id: str) -> AgentState | None: ...

    def delete(self, task_id: str) -> None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}

    def save(self, state: AgentState) -> None:
        self.save_snapshot(state.task_id, serialize_state(state))

    def save_snapshot(self, task_id: str, data: dict[str, Any]) -> None:
        snapshot = json.loads(json.dumps(data))
        with self._lock:
            self._data[task_id] = snapshot

    def load(self, task_id: str) -> AgentState | None:
        with self._lock:
            data = self._data.get(task_id)
        return deserialize_state(data) if data else None

    def delete(self, task_id: str) -> None:
        with self._lock:
            self._data.pop(task_id, None)


class JsonFileCheckpointStore:
    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
        return self._dir / f"{safe}.json"

    def save(self, state: AgentState) -> None:
        self.save_snapshot(state.task_id, serialize_state(state))

    def save_snapshot(self, task_id: str, data: dict[str, Any]) -> None:
        path = self._path(task_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def load(self, task_id: str) -> AgentState | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        return deserialize_state(json.loads(path.read_text(encoding="utf-8")))

    def delete(self, task_id: str) -> None:
        self._path(task_id).unlink(missing_ok=True)


class PostgresCheckpointStore:
    """DB-API backed store. `connect` returns a new connection
    (for example psycopg.connect with the deployment DSN)."""

    _DDL = (
        "CREATE TABLE IF NOT EXISTS agent_checkpoints ("
        "task_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)"
    )
    _UPSERT = (
        "INSERT INTO agent_checkpoints (task_id, state_json) "
        "VALUES (%s, %s) ON CONFLICT (task_id) DO UPDATE SET "
        "state_json = EXCLUDED.state_json"
    )
    _SELECT = "SELECT state_json FROM agent_checkpoints WHERE task_id = %s"
    _DELETE = "DELETE FROM agent_checkpoints WHERE task_id = %s"

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect
        self._ready = False

    def _cursor(self):
        conn = self._connect()
        cur = conn.cursor()
        if not self._ready:
            cur.execute(self._DDL)
            conn.commit()
            self._ready = True
        return conn, cur

    def save(self, state: AgentState) -> None:
        self.save_snapshot(state.task_id, serialize_state(state))

    def save_snapshot(self, task_id: str, data: dict[str, Any]) -> None:
        conn, cur = self._cursor()
        try:
            cur.execute(self._UPSERT, (task_id, json.dumps(data)))
            conn.commit()
        finally:
            conn.close()

    def load(self, task_id: str) -> AgentState | None:
        conn, cur = self._cursor()
        try:
            cur.execute(self._SELECT, (task_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        return deserialize_state(json.loads(row[0])) if row else None

    def delete(self, task_id: str) -> None:
        conn, cur = self._cursor()
        try:
            cur.execute(self._DELETE, (task_id,))
            conn.commit()
        finally:
            conn.close()
