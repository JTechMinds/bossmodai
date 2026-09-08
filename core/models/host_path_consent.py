"""Host-path and workspace-preference consent request model."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

WORKSPACE_PREFERENCE_KIND = "workspace_preference"
WORKSPACE_PREFERENCE_TITLE = "Work in your workspace?"
WORKSPACE_PREFERENCE_BODY = (
    "Host paths stay safer if we clone (or branch) into the agent's workspace first. "
    "Editing the host folder directly is allowed but not advised."
)


class HostPathConsentRequest(BaseModel):
    """A pending or resolved in-chat host-path or workspace-preference card."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    path: str
    grant_root: str
    reason: str
    command: str | None = None
    content: str | None = None
    cwd: str | None = None
    task_id: str | None = None
    channel_id: str | None = None
    card_kind: str = "host_path"
    is_git: bool = False
    clone_dest: str | None = None
    status: str = "pending"
    decision_by: str | None = None
    decision_note: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime

    def as_card(self) -> dict[str, Any]:
        """Operator-facing card payload for chat / WebSocket."""
        kind = (self.card_kind or "host_path").strip() or "host_path"
        card: dict[str, Any] = {
            "id": self.id,
            "agent_id": self.agent_id,
            "path": self.path,
            "grant_root": self.grant_root,
            "reason": self.reason,
            "kind": kind,
            "status": self.status,
        }
        if self.channel_id:
            card["channel_id"] = self.channel_id
        if self.decision_note:
            card["decision_note"] = self.decision_note
        if kind == WORKSPACE_PREFERENCE_KIND:
            card["title"] = WORKSPACE_PREFERENCE_TITLE
            card["body"] = WORKSPACE_PREFERENCE_BODY
            card["git"] = bool(self.is_git)
            if self.clone_dest:
                card["clone_dest"] = self.clone_dest
        return card
