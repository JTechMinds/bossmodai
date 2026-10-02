"""BossMod AI — CLI policy rule and approval request models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CliPolicyRule(BaseModel):
    """One configurable CLI policy rule."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tier: str
    pattern: str
    match_mode: str = "prefix"
    agent_id: str | None = None
    description: str | None = None
    category: str = "general"
    usage_syntax: str | None = None
    help_text: str | None = None
    enabled: bool = True
    priority: int = 0
    cwd_prefix: str | None = None
    # Floor of a ``/projects/<slug>`` rule. Project paths are virtual per
    # floor, so the rule matches only agents on this floor.
    floor_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CliPolicyRuleCreate(BaseModel):
    """Input payload for creating a CLI policy rule."""

    tier: str
    pattern: str
    match_mode: str = "prefix"
    agent_id: str | None = None
    description: str | None = None
    category: str = "general"
    usage_syntax: str | None = None
    help_text: str | None = None
    enabled: bool = True
    priority: int = 0
    cwd_prefix: str | None = None


CLI_APPROVAL_KIND = "cli_approval"
CLI_APPROVAL_TITLE = "Approve this command?"


class CliApprovalRequest(BaseModel):
    """A pending, approved, or rejected CLI approval request."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    trigger_id: str | None = None
    command: str
    content: str | None = None
    cwd: str | None = None
    matched_rule_id: str | None = None
    channel_id: str | None = None
    status: str = "pending"
    decision_by: str | None = None
    decision_note: str | None = None
    review_note: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    # Opened in a detached turn: the operator's resume runs detached too.
    detached_origin: bool = False

    def as_card(self) -> dict[str, object]:
        """Operator-facing card payload for chat / channel / WebSocket.

        A pending card offers Always allow when the command stays inside one
        scope (``core.bm_cli.cli_always.always_scope_for``) and names it in
        ``always_scope_label``. A decided card offers nothing, so its scope is
        not recomputed against today's disk.

        Raises:
            LookupError: A pending request's agent no longer exists.
        """
        card: dict[str, object] = {
            "id": self.id,
            "agent_id": self.agent_id,
            "kind": CLI_APPROVAL_KIND,
            "title": CLI_APPROVAL_TITLE,
            "command": self.command,
            "status": self.status,
        }
        if self.cwd:
            card["cwd"] = self.cwd
        if self.channel_id:
            card["channel_id"] = self.channel_id
        if self.decision_note:
            card["decision_note"] = self.decision_note
        note = (self.review_note or "").strip()
        if note:
            card["review_note"] = note
        from core.bm_cli.cli_always import always_scope_for_request

        scope = always_scope_for_request(self) if self.status == "pending" else None
        card["always_allow"] = scope is not None
        if scope is not None:
            card["always_scope_label"] = scope.label
        return card
