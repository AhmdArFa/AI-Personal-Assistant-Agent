"""Tool permission levels and the policy that enforces them.

The model proposes tool calls. The policy decides whether they may run.
That separation is a founding rule of this project.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class PermissionLevel(IntEnum):
    """How much effect a tool call has outside the agent."""

    READ = 0
    WRITE_REVERSIBLE = 1
    EXTERNAL_EFFECT = 2
    SENSITIVE = 3


@dataclass(frozen=True)
class PermissionPolicy:
    """Decides if a permission level runs automatically or needs approval.

    Defaults match the technical design:
    read and reversible writes run automatically,
    external effects need approval,
    sensitive actions need explicit approval every single time.
    """

    auto_allow_up_to: PermissionLevel = PermissionLevel.WRITE_REVERSIBLE

    def requires_approval(self, level: PermissionLevel) -> bool:
        return level > self.auto_allow_up_to
