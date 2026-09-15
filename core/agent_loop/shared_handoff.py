"""Shared-thread handoff: peers must be able to open the Done artifact.

``/me`` is desk-private. Thread-origin work (a multi-party channel) cannot
CLEAR by pointing only at that scratch tree. Prefer ``/projects/...`` or a
host path under the shared grant (often ``.../docs``).
"""

from __future__ import annotations

from typing import Any

from core.agent_loop.next_owner import is_multi_party_channel
from core.models.task import Task

PEER_INVISIBLE_HANDOFF_CODE = "peer_invisible_handoff"
PEER_INVISIBLE_HANDOFF_LINE = "Blocked — handoff needs a shared path"
PEER_INVISIBLE_HANDOFF_MESSAGE = (
    "Thread-origin Done needs a path peers can open "
    "(project/docs/ or a host path under the shared grant). "
    "`/me` is desk-private and is not a handoff."
)


def is_peer_invisible_path(path: str | None) -> bool:
    """Return True when *path* is desk-private ``/me`` scratch."""
    token = (path or "").replace("\\", "/").strip()
    if not token:
        return False
    return token == "/me" or token.startswith("/me/")


def is_shared_thread_origin(task: Task | None) -> bool:
    """Return True when this task was born on a multi-party thread."""
    if task is None:
        return False
    if getattr(task, "source_channel", None) != "channel":
        return False
    channel_id = getattr(task, "notification_channel_id", None)
    if not isinstance(channel_id, str) or not channel_id.strip():
        return False
    return is_multi_party_channel(channel_id.strip())


def peer_invisible_handoff_error(
    *,
    agent_name: str,
    task: Task | None,
    path: str | None,
) -> dict[str, Any] | None:
    """Reject a Done claim whose only artifact is peer-invisible ``/me``."""
    if not is_shared_thread_origin(task):
        return None
    if not is_peer_invisible_path(path):
        return None
    return {
        "event": "world_feedback",
        "feedback_code": PEER_INVISIBLE_HANDOFF_CODE,
        "detail": PEER_INVISIBLE_HANDOFF_MESSAGE,
        "agent_name": agent_name,
        "origin_status_kind": "blocked_peer_handoff",
        "handoff_path": path,
    }
