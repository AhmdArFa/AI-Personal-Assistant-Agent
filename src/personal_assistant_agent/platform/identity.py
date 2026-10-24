"""Identity boundary (Decision D1).

An identity provider proves who a user is and nothing more. The
provider is replaceable behind this protocol as long as it speaks
standard OIDC claims: a stable subject and an email.

DevIdentityProvider exists for local development and tests only.
Its token format is dev:<subject>:<email>, it performs no real
verification, and it must never be wired in production. It does,
however, honour the same contract as a real provider, including
revocation: a revoked subject fails authentication immediately,
which is brief check 4 exercised end to end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .tenancy import TenancyStore, User


@dataclass
class Identity:
    subject: str
    email: str = ""


class AuthenticationError(Exception):
    pass


class IdentityProvider(Protocol):
    def authenticate(self, token: str) -> Identity: ...


class DevIdentityProvider:
    """Development only identity provider. Not for production."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()

    def issue(self, subject: str, email: str) -> str:
        return f"dev:{subject}:{email}"

    def revoke(self, subject: str) -> None:
        self._revoked.add(subject)

    def authenticate(self, token: str) -> Identity:
        if not token.startswith("dev:"):
            raise AuthenticationError("unrecognised token")
        parts = token.split(":", 2)
        if len(parts) != 3 or not parts[1]:
            raise AuthenticationError("malformed dev token")
        subject, email = parts[1], parts[2]
        if subject in self._revoked:
            raise AuthenticationError("subject revoked")
        return Identity(subject=subject, email=email)


def get_or_create_user(store: TenancyStore, identity: Identity) -> User:
    """First login creates the users row keyed by subject (brief
    check 3); later logins return the same row."""
    existing = store.user_by_subject(identity.subject)
    if existing is not None:
        return existing
    return store.create_user(identity.subject, identity.email)
