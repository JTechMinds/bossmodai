"""Host-path consent request model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HostPathConsentRequest(BaseModel):
    """A pending or resolved in-chat host-path consent request."""

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
    status: str = "pending"
    decision_by: str | None = None
    decision_note: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime

    def as_card(self) -> dict[str, str]:
        """Operator-facing card payload for chat / WebSocket."""
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "path": self.path,
            "grant_root": self.grant_root,
            "reason": self.reason,
            "status": self.status,
        }
