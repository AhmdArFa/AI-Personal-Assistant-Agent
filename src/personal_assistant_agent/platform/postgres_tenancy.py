"""PostgreSQL tenancy store (work package W3).

Same semantics as InMemoryTenancyStore, same DB-API connection
factory style as PostgresCheckpointStore, so any DB-API driver
(psycopg in production) can be injected. The store creates its
tables lazily for development and tests; production schema is
owned by the Alembic migrations in migrations/.

Tables (Blueprint section 13): users, workspaces,
workspace_members, invitations. Every workspace scoped row carries
its workspace_id, and membership in workspace_members is the only
authority for access.

Row-Level Security was evaluated as a second barrier: see
migrations/README.md. It is provided as opt-in SQL and is not
enabled by default, because the connection-per-operation pattern
here must first set the app.workspace_id setting per transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from .tenancy import Invitation, User, Workspace

_DDL = (
    """CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        identity_subject TEXT UNIQUE NOT NULL,
        email TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS workspaces (
        workspace_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        owner_user_id TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS workspace_members (
        workspace_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        PRIMARY KEY (workspace_id, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS invitations (
        invitation_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        email TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
    )""",
)


class PostgresTenancyStore:
    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect
        self._ready = False

    # -- plumbing ----------------------------------------------------
    def _cursor(self):
        conn = self._connect()
        cur = conn.cursor()
        if not self._ready:
            for statement in _DDL:
                cur.execute(statement)
            conn.commit()
            self._ready = True
        return conn, cur

    def _query(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn, cur = self._cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        conn, cur = self._cursor()
        try:
            cur.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    # -- users ---------------------------------------------------------
    def create_user(self, identity_subject: str, email: str) -> User:
        existing = self.user_by_subject(identity_subject)
        if existing is not None:
            return existing
        user = User(
            user_id=uuid.uuid4().hex,
            identity_subject=identity_subject,
            email=email,
        )
        self._execute(
            "INSERT INTO users (user_id, identity_subject, email) VALUES (%s, %s, %s)",
            (user.user_id, user.identity_subject, user.email),
        )
        return user

    def user_by_subject(self, identity_subject: str) -> User | None:
        rows = self._query(
            "SELECT user_id, identity_subject, email FROM users "
            "WHERE identity_subject = %s",
            (identity_subject,),
        )
        return User(*rows[0]) if rows else None

    def user_by_email(self, email: str) -> User | None:
        rows = self._query(
            "SELECT user_id, identity_subject, email FROM users WHERE email = %s",
            (email,),
        )
        return User(*rows[0]) if rows else None

    def user_by_id(self, user_id: str) -> User | None:
        rows = self._query(
            "SELECT user_id, identity_subject, email FROM users WHERE user_id = %s",
            (user_id,),
        )
        return User(*rows[0]) if rows else None

    # -- workspaces ----------------------------------------------------
    def create_workspace(self, owner: User, name: str) -> Workspace:
        workspace = Workspace(
            workspace_id=uuid.uuid4().hex,
            name=name,
            owner_user_id=owner.user_id,
        )
        conn, cur = self._cursor()
        try:
            cur.execute(
                "INSERT INTO workspaces (workspace_id, name, owner_user_id) "
                "VALUES (%s, %s, %s)",
                (workspace.workspace_id, workspace.name, workspace.owner_user_id),
            )
            cur.execute(
                "INSERT INTO workspace_members (workspace_id, user_id) VALUES (%s, %s)",
                (workspace.workspace_id, owner.user_id),
            )
            conn.commit()
        finally:
            conn.close()
        return workspace

    def workspace(self, workspace_id: str) -> Workspace | None:
        rows = self._query(
            "SELECT workspace_id, name, owner_user_id FROM workspaces "
            "WHERE workspace_id = %s",
            (workspace_id,),
        )
        return Workspace(*rows[0]) if rows else None

    def add_member(self, workspace_id: str, user_id: str) -> None:
        self._execute(
            "INSERT INTO workspace_members (workspace_id, user_id) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (workspace_id, user_id),
        )

    def remove_member(self, workspace_id: str, user_id: str) -> None:
        self._execute(
            "DELETE FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
            (workspace_id, user_id),
        )

    def is_member(self, workspace_id: str, user_id: str) -> bool:
        rows = self._query(
            "SELECT 1 FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
            (workspace_id, user_id),
        )
        return bool(rows)

    def members(self, workspace_id: str) -> list[str]:
        rows = self._query(
            "SELECT user_id FROM workspace_members "
            "WHERE workspace_id = %s ORDER BY user_id",
            (workspace_id,),
        )
        return [row[0] for row in rows]

    # -- invitations -----------------------------------------------------
    def invite(self, workspace_id: str, email: str) -> Invitation:
        invitation = Invitation(
            invitation_id=uuid.uuid4().hex,
            workspace_id=workspace_id,
            email=email,
        )
        self._execute(
            "INSERT INTO invitations "
            "(invitation_id, workspace_id, email, status) "
            "VALUES (%s, %s, %s, %s)",
            (
                invitation.invitation_id,
                invitation.workspace_id,
                invitation.email,
                invitation.status,
            ),
        )
        return invitation

    def invitation(self, invitation_id: str) -> Invitation | None:
        rows = self._query(
            "SELECT invitation_id, workspace_id, email, status "
            "FROM invitations WHERE invitation_id = %s",
            (invitation_id,),
        )
        return Invitation(*rows[0]) if rows else None

    def accept_invitation(self, invitation_id: str, user: User) -> bool:
        conn, cur = self._cursor()
        try:
            cur.execute(
                "SELECT invitation_id, workspace_id, email, status "
                "FROM invitations WHERE invitation_id = %s",
                (invitation_id,),
            )
            row = cur.fetchone()
            if row is None:
                return False
            invitation = Invitation(*row)
            if invitation.status != "pending" or invitation.email != user.email:
                return False
            cur.execute(
                "UPDATE invitations SET status = %s WHERE invitation_id = %s",
                ("accepted", invitation_id),
            )
            cur.execute(
                "INSERT INTO workspace_members (workspace_id, user_id) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (invitation.workspace_id, user.user_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()
