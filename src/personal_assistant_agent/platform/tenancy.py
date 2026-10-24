"""Tenancy: users, workspaces, memberships, invitations.

Workspace authority lives here, in our own tables, never in a
token claim (Decision brief D1, scope boundary). The server
verifies membership in this store on every request. Removing a
member blocks that workspace immediately while the account and
other memberships remain.

The store interface is storage agnostic; InMemoryTenancyStore is
the reference implementation used by tests and local runs, with
the same semantics a PostgreSQL implementation must keep.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class User:
    user_id: str
    identity_subject: str
    email: str = ""


@dataclass
class Workspace:
    workspace_id: str
    name: str
    owner_user_id: str


@dataclass
class Invitation:
    invitation_id: str
    workspace_id: str
    email: str
    status: str = "pending"  # pending, accepted, revoked


class TenancyStore(Protocol):
    def create_user(self, identity_subject: str, email: str) -> User: ...

    def user_by_subject(self, identity_subject: str) -> User | None: ...

    def user_by_email(self, email: str) -> User | None: ...

    def create_workspace(self, owner: User, name: str) -> Workspace: ...

    def add_member(self, workspace_id: str, user_id: str) -> None: ...

    def remove_member(self, workspace_id: str, user_id: str) -> None: ...

    def is_member(self, workspace_id: str, user_id: str) -> bool: ...

    def members(self, workspace_id: str) -> list[str]: ...

    def invite(self, workspace_id: str, email: str) -> Invitation: ...

    def invitation(self, invitation_id: str) -> Invitation | None: ...

    def accept_invitation(self, invitation_id: str, user: User) -> bool: ...


@dataclass
class InMemoryTenancyStore:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _users: dict[str, User] = field(default_factory=dict)
    _users_by_subject: dict[str, str] = field(default_factory=dict)
    _workspaces: dict[str, Workspace] = field(default_factory=dict)
    _members: dict[str, set[str]] = field(default_factory=dict)
    _invitations: dict[str, Invitation] = field(default_factory=dict)

    # -- users -----------------------------------------------------
    def create_user(self, identity_subject: str, email: str) -> User:
        with self._lock:
            existing = self._users_by_subject.get(identity_subject)
            if existing is not None:
                return self._users[existing]
            user = User(
                user_id=uuid.uuid4().hex,
                identity_subject=identity_subject,
                email=email,
            )
            self._users[user.user_id] = user
            self._users_by_subject[identity_subject] = user.user_id
            return user

    def user_by_subject(self, identity_subject: str) -> User | None:
        user_id = self._users_by_subject.get(identity_subject)
        return self._users.get(user_id) if user_id else None

    def user_by_email(self, email: str) -> User | None:
        for user in self._users.values():
            if user.email == email:
                return user
        return None

    def user_by_id(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    # -- workspaces --------------------------------------------------
    def create_workspace(self, owner: User, name: str) -> Workspace:
        workspace = Workspace(
            workspace_id=uuid.uuid4().hex,
            name=name,
            owner_user_id=owner.user_id,
        )
        with self._lock:
            self._workspaces[workspace.workspace_id] = workspace
            self._members[workspace.workspace_id] = {owner.user_id}
        return workspace

    def workspace(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    def add_member(self, workspace_id: str, user_id: str) -> None:
        with self._lock:
            self._members.setdefault(workspace_id, set()).add(user_id)

    def remove_member(self, workspace_id: str, user_id: str) -> None:
        with self._lock:
            self._members.get(workspace_id, set()).discard(user_id)

    def is_member(self, workspace_id: str, user_id: str) -> bool:
        return user_id in self._members.get(workspace_id, set())

    def members(self, workspace_id: str) -> list[str]:
        return sorted(self._members.get(workspace_id, set()))

    # -- invitations -------------------------------------------------
    def invite(self, workspace_id: str, email: str) -> Invitation:
        invitation = Invitation(
            invitation_id=uuid.uuid4().hex,
            workspace_id=workspace_id,
            email=email,
        )
        with self._lock:
            self._invitations[invitation.invitation_id] = invitation
        return invitation

    def invitation(self, invitation_id: str) -> Invitation | None:
        return self._invitations.get(invitation_id)

    def accept_invitation(self, invitation_id: str, user: User) -> bool:
        """Attach the user to the invited workspace only.

        The invitation is ours: the identity provider only
        authenticated the person; membership is granted here, and
        only when the account email matches the invited email.
        """
        with self._lock:
            invitation = self._invitations.get(invitation_id)
            if invitation is None or invitation.status != "pending":
                return False
            if invitation.email != user.email:
                return False
            invitation.status = "accepted"
            self._members.setdefault(invitation.workspace_id, set()).add(user.user_id)
            return True


def resolve_membership(store: TenancyStore, user_id: str, workspace_id: str) -> None:
    """Raise PermissionError unless the database says the user is
    a member of the workspace. This is the only authority check;
    token claims and client fields are never consulted."""
    if not store.is_member(workspace_id, user_id):
        raise PermissionError("user is not a member of this workspace")
