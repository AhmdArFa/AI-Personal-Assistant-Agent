"""Tenancy tables: users, workspaces, workspace_members, invitations.

Work package W3. Blueprint section 13: a workspace id on every
workspace scoped row; membership is the only access authority.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            identity_subject TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL DEFAULT ''
        )"""
    )
    op.execute(
        """CREATE TABLE workspaces (
            workspace_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_user_id TEXT NOT NULL REFERENCES users (user_id)
        )"""
    )
    op.execute(
        """CREATE TABLE workspace_members (
            workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id),
            user_id TEXT NOT NULL REFERENCES users (user_id),
            PRIMARY KEY (workspace_id, user_id)
        )"""
    )
    op.execute(
        """CREATE TABLE invitations (
            invitation_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces (workspace_id),
            email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )"""
    )
    op.execute(
        "CREATE INDEX invitations_workspace_idx "
        "ON invitations (workspace_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invitations")
    op.execute("DROP TABLE IF EXISTS workspace_members")
    op.execute("DROP TABLE IF EXISTS workspaces")
    op.execute("DROP TABLE IF EXISTS users")
