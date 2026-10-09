"""BossMod AI — Task reference rules: which documents a task may point its assignee at.

A reference is input the assignee reads before starting (a bug write-up, a
screenshot, a log), as opposed to a deliverable, which is output the
assignee must produce. References belong to every task, not only backlog
items, so these rules live apart from ``core/tasking/backlog.py``.

Only ``/projects`` files can be referenced: ``/projects`` is the floor's
shared folder and resolves to the same files for every agent on that floor
(``virtual_fs.resolve_cli_path``), while ``/me`` is private to its author,
so an assignee could never open a ``/me`` reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.bm_cli.host_roots import PathOutsideRootsError
from core.bm_cli.virtual_fs import normalize_cli_path, resolve_cli_path
from core.models import Agent
from core.models.task import TaskReference

_SHARED_PREFIX = "/projects/"
NOT_SHARED_REASON = "only /projects files can be shared; /me is private to you"
NO_SUCH_FILE_REASON = "no such file"


@dataclass(frozen=True)
class RejectedReference:
    """One reference that was not linked, and why, in words the agent can act on."""

    path: str
    reason: str


def check_references(
    agent: Agent, refs: list[TaskReference],
) -> tuple[list[TaskReference], list[RejectedReference]]:
    """Split ``refs`` into the ones a task may carry and the ones it may not.

    A reference is kept only when its normalized path is under
    ``/projects/``, resolves inside ``agent``'s roots, and names an existing
    file. Kept references carry the normalized virtual path. A repeated path
    is the same reference, so later copies are dropped without a rejection.

    Args:
        agent: The agent naming the references; paths resolve as it sees them.
        refs: The references as given.

    Returns:
        ``(kept, rejected)``, each in the order given.
    """
    kept: list[TaskReference] = []
    rejected: list[RejectedReference] = []
    seen: set[str] = set()
    for ref in refs:
        virtual_path = normalize_cli_path("/", ref.path)
        if virtual_path in seen:
            continue
        seen.add(virtual_path)
        reason = _rejection_reason(agent, virtual_path)
        if reason is not None:
            rejected.append(RejectedReference(path=ref.path, reason=reason))
            continue
        kept.append(TaskReference(path=virtual_path, description=ref.description))
    return kept, rejected


def _rejection_reason(agent: Agent, virtual_path: str) -> str | None:
    """Why ``virtual_path`` cannot be referenced, or None when it can."""
    if not virtual_path.startswith(_SHARED_PREFIX):
        return NOT_SHARED_REASON
    try:
        resolved = resolve_cli_path(agent.storage_key, "/", virtual_path)
    except (PathOutsideRootsError, ValueError, LookupError) as exc:
        return str(exc)
    real = resolved.real_path
    if real is None or not resolved.exists or not real.is_file():
        return NO_SUCH_FILE_REASON
    return None


def format_references_for_context(refs: list[TaskReference] | list[dict[str, Any]]) -> list[str]:
    """Prompt lines for a task's references: ``- <path> — <description>`` (path alone without one).

    Accepts the models (a ``Task``) or their dumps (the turn's current-task
    dict), so the trigger block and the current-task block read the same.
    """
    lines: list[str] = []
    for ref in refs:
        item = ref if isinstance(ref, TaskReference) else TaskReference.model_validate(ref)
        lines.append(f"- {item.path} — {item.description}" if item.description else f"- {item.path}")
    return lines
