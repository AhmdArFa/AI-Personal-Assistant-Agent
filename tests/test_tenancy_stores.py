"""The tenancy behaviour suite, run unchanged against the in
memory store, the Postgres store over a fake DB-API engine, and
(in CI) a real PostgreSQL service. Work package W3 done-when:
identical semantics on both stores, and removal blocks access
immediately while the account and other memberships remain.
"""

import os
import re

import pytest

from personal_assistant_agent.platform import (
    InMemoryTenancyStore,
    get_or_create_user,
    resolve_membership,
)
from personal_assistant_agent.platform.identity import Identity
from personal_assistant_agent.platform.postgres_tenancy import (
    PostgresTenancyStore,
)


def exercise_tenancy(store) -> None:
    owner = get_or_create_user(
        store, Identity(subject="owner", email="owner@example.com")
    )
    again = get_or_create_user(
        store, Identity(subject="owner", email="owner@example.com")
    )
    assert again.user_id == owner.user_id  # sub keyed provisioning

    member = get_or_create_user(
        store, Identity(subject="member", email="m@example.com")
    )
    ws_a = store.create_workspace(owner, "a")
    ws_b = store.create_workspace(owner, "b")
    assert store.is_member(ws_a.workspace_id, owner.user_id)

    # Invitation: attaches to the invited workspace only, email must match
    invitation = store.invite(ws_a.workspace_id, "guest@example.com")
    guest = get_or_create_user(
        store, Identity(subject="guest", email="guest@example.com")
    )
    assert store.accept_invitation(invitation.invitation_id, guest)
    assert store.is_member(ws_a.workspace_id, guest.user_id)
    assert not store.is_member(ws_b.workspace_id, guest.user_id)
    wrong = store.invite(ws_a.workspace_id, "someone@example.com")
    assert not store.accept_invitation(wrong.invitation_id, guest)

    # Dual membership, then immediate removal from one workspace
    store.add_member(ws_a.workspace_id, member.user_id)
    store.add_member(ws_b.workspace_id, member.user_id)
    resolve_membership(store, member.user_id, ws_a.workspace_id)
    resolve_membership(store, member.user_id, ws_b.workspace_id)
    store.remove_member(ws_a.workspace_id, member.user_id)
    with pytest.raises(PermissionError):
        resolve_membership(store, member.user_id, ws_a.workspace_id)
    resolve_membership(store, member.user_id, ws_b.workspace_id)
    assert store.user_by_id(member.user_id) is not None  # account remains

    # A stranger is never a member
    stranger = get_or_create_user(
        store, Identity(subject="stranger", email="s@example.com")
    )
    with pytest.raises(PermissionError):
        resolve_membership(store, stranger.user_id, ws_b.workspace_id)
    assert set(store.members(ws_b.workspace_id)) == {owner.user_id, member.user_id}


def test_behaviour_in_memory():
    exercise_tenancy(InMemoryTenancyStore())


# ---------------------------------------------------------------------
# A tiny in-memory DB-API engine covering the exact SQL shapes the
# Postgres store issues, so its SQL is exercised locally with no server.
class FakeEngine:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}

    def connect(self):
        return _FakeConnection(self)


class _FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    def cursor(self):
        return _FakeCursor(self.engine)

    def commit(self):
        pass

    def close(self):
        pass


class _FakeCursor:
    def __init__(self, engine):
        self.engine = engine
        self._rows: list[tuple] = []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        params = list(params)
        if sql.startswith("CREATE TABLE IF NOT EXISTS"):
            name = sql.split("EXISTS ")[1].split(" ")[0]
            self.engine.tables.setdefault(name, [])
            return
        m = re.match(r"INSERT INTO (\w+) \(([^)]+)\) VALUES \(([^)]+)\)(.*)", sql)
        if m:
            table, cols, _vals, tail = m.groups()
            cols = [c.strip() for c in cols.split(",")]
            row = dict(zip(cols, params, strict=True))
            rows = self.engine.tables.setdefault(table, [])
            pk = {
                "users": "user_id",
                "workspaces": "workspace_id",
                "workspace_members": None,
                "invitations": "invitation_id",
            }[table]
            if table == "workspace_members":
                dup = any(
                    r["workspace_id"] == row["workspace_id"]
                    and r["user_id"] == row["user_id"]
                    for r in rows
                )
            elif pk:
                dup = any(r[pk] == row[pk] for r in rows)
            else:
                dup = False
            if dup and "ON CONFLICT DO NOTHING" in tail:
                return
            rows.append(row)
            return
        m = re.match(
            r"SELECT (.+?) FROM (\w+)(?: WHERE (.+?))?( ORDER BY (\w+))?$", sql
        )
        if m:
            cols, table, where, _ob, order = m.groups()
            rows = list(self.engine.tables.get(table, []))
            if where:
                conditions = [c.strip() for c in where.split(" AND ")]
                for condition in conditions:
                    column = condition.split(" = ")[0].strip()
                    value = params.pop(0)
                    rows = [r for r in rows if r.get(column) == value]
            if order:
                rows.sort(key=lambda r: r[order])
            if cols.strip() == "1":
                self._rows = [(1,) for _ in rows]
            else:
                wanted = [c.strip() for c in cols.split(",")]
                self._rows = [tuple(r[c] for c in wanted) for r in rows]
            return
        m = re.match(r"DELETE FROM (\w+) WHERE (.+)$", sql)
        if m:
            table, where = m.groups()
            rows = self.engine.tables.get(table, [])
            conditions = [c.strip() for c in where.split(" AND ")]
            targets = []
            for condition in conditions:
                column = condition.split(" = ")[0].strip()
                targets.append((column, params.pop(0)))
            self.engine.tables[table] = [
                r
                for r in rows
                if not all(r.get(col) == val for col, val in targets)
            ]
            return
        m = re.match(r"UPDATE (\w+) SET (\w+) = %s WHERE (\w+) = %s$", sql)
        if m:
            table, set_col, where_col = m.groups()
            value, key = params
            for row in self.engine.tables.get(table, []):
                if row.get(where_col) == key:
                    row[set_col] = value
            return
        raise AssertionError(f"unsupported SQL: {sql}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def test_behaviour_postgres_over_fake_engine():
    engine = FakeEngine()
    exercise_tenancy(PostgresTenancyStore(connect=engine.connect))


@pytest.mark.skipif(
    not os.environ.get("TENANCY_TEST_DSN"),
    reason="TENANCY_TEST_DSN not set (CI provides a Postgres service)",
)
def test_behaviour_postgres_real():
    import psycopg

    dsn = os.environ["TENANCY_TEST_DSN"]
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS invitations, workspace_members, workspaces, users"
        )
        conn.commit()
    exercise_tenancy(PostgresTenancyStore(connect=lambda: psycopg.connect(dsn)))
