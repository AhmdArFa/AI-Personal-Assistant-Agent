"""Approval argument binding.

An approval is valid only for the exact action and arguments that
were reviewed. The binding is a SHA-256 hash over the canonical
JSON of the tool name and its arguments. If the arguments change
after the approval was requested, the hashes differ and a new
approval is required.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def arguments_hash(tool_name: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "args": args},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
