"""Platform foundation: tenancy, identity boundary, model gateway.

Built to the D1 and D2 decision brief: the identity provider proves
who a user is, workspace authority lives in our own tables, and
all model calls go through a gateway with budget shares.
"""

from .gateway import (
    BudgetExceeded,
    BudgetLedger,
    ModelGateway,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ToolCall,
)
from .identity import (
    AuthenticationError,
    DevIdentityProvider,
    Identity,
    IdentityProvider,
    get_or_create_user,
)
from .postgres_tenancy import PostgresTenancyStore
from .stub import StubProvider
from .tenancy import (
    InMemoryTenancyStore,
    Invitation,
    TenancyStore,
    User,
    Workspace,
    resolve_membership,
)
from .trial import TRIAL_TASKS, TaskResult, TrialReport, TrialRunner, TrialTask

__all__ = [
    "TRIAL_TASKS",
    "AuthenticationError",
    "BudgetExceeded",
    "BudgetLedger",
    "DevIdentityProvider",
    "Identity",
    "IdentityProvider",
    "InMemoryTenancyStore",
    "Invitation",
    "ModelGateway",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "PostgresTenancyStore",
    "StubProvider",
    "TaskResult",
    "TenancyStore",
    "ToolCall",
    "TrialReport",
    "TrialRunner",
    "TrialTask",
    "User",
    "Workspace",
    "get_or_create_user",
    "resolve_membership",
]
