"""Turn-local host-path consent scope.

Set only on the agent CLI path so allow-once grants apply to that agent.
Operator Company Files and Settings must not enter this scope.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConsentScope:
    """Agent + task (or turn) that may use allow-once host roots."""

    agent_id: str
    task_id: str | None = None


host_path_consent_scope: ContextVar[ConsentScope | None] = ContextVar(
    "host_path_consent_scope",
    default=None,
)


def current_consent_scope() -> ConsentScope | None:
    """Return the active agent CLI consent scope, if any."""
    return host_path_consent_scope.get()
