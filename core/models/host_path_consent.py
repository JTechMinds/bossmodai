"""Host-path and workspace-preference consent request model."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.models.nest_git import (
    NEST_GIT_ADD_LABEL,
    NEST_GIT_BODY,
    NEST_GIT_CARD_COPY,
    NEST_GIT_ENABLE_HINT,
    NEST_GIT_ENABLE_LABEL,
    NEST_GIT_KIND,
    NEST_GIT_TITLE,
)
from pydantic import BaseModel, ConfigDict

WORKSPACE_PREFERENCE_KIND = "workspace_preference"
WORKSPACE_PREFERENCE_TITLE = "Work in your workspace?"
WORKSPACE_PREFERENCE_BODY = (
    "Host paths stay safer if we clone (or branch) into the agent's workspace first. "
    "Editing the host folder directly is allowed but not advised."
)
SHELL_EXECUTOR_KIND = "shell_executor"
SHELL_EXECUTOR_GRANT_ROOT = "cli_shell_enabled"
SHELL_EXECUTOR_TITLE = "Enable Shell Executor?"
SHELL_EXECUTOR_BODY = (
    "Turns on Shell Executor for the company — same as Settings → CLI policy. "
    "CLI policy still applies after (not a blanket allow-all). "
    "Validate-on-clone needs pytest and local git add/commit on the locked workspace copy."
)
SHELL_EXECUTOR_ENABLE_LABEL = "Turn on Shell Executor (company-wide)"
SHELL_EXECUTOR_ENABLE_HINT = "same as Settings. CLI policy still applies after."
SHELL_EXECUTOR_DENY_LABEL = "Deny — Shell Executor stays off"
SHELL_EXECUTOR_ENABLED_NOTE = (
    "Shell Executor on (company-wide). CLI policy still applies."
)
SHELL_EXECUTOR_DENIED_NOTE = (
    "Shell Executor stays off. Validate-on-clone was denied."
)
SHELL_EXECUTOR_CARD_COPY = (
    "needs Shell Executor for validate-on-clone — enable or deny"
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
        from core.bm_cli.host_roots import offers_always_allow_grant

        card: dict[str, Any] = {
            "id": self.id,
            "agent_id": self.agent_id,
            "path": self.path,
            "grant_root": self.grant_root,
            "reason": self.reason,
            "kind": kind,
            "status": self.status,
            "always_allow": offers_always_allow_grant(self.grant_root),
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
        elif kind == SHELL_EXECUTOR_KIND:
            card["title"] = SHELL_EXECUTOR_TITLE
            card["body"] = SHELL_EXECUTOR_BODY
            card["enable_label"] = SHELL_EXECUTOR_ENABLE_LABEL
            card["enable_hint"] = SHELL_EXECUTOR_ENABLE_HINT
            card["deny_label"] = SHELL_EXECUTOR_DENY_LABEL
            card["always_allow"] = False
            if self.command:
                card["command"] = self.command
        elif kind == NEST_GIT_KIND:
            card["title"] = NEST_GIT_TITLE
            card["body"] = NEST_GIT_BODY
            card["enable_label"] = NEST_GIT_ENABLE_LABEL
            card["add_label"] = NEST_GIT_ADD_LABEL
            card["enable_hint"] = NEST_GIT_ENABLE_HINT
            card["always_allow"] = False
            if self.command:
                card["command"] = self.command
        return card


def consent_turn_event(agent_name: str, card: dict[str, Any] | None) -> tuple[str, str]:
    """Return ``(event, detail)`` for an in-chat consent pause."""
    payload = card if isinstance(card, dict) else {}
    kind = str(payload.get("kind") or payload.get("card_kind") or "host_path").strip() or "host_path"
    path = str(payload.get("path") or "host path").strip() or "host path"
    name = (agent_name or "").strip() or "Agent"
    if kind == WORKSPACE_PREFERENCE_KIND:
        return "workspace_preference_required", f"{name} needs a workspace preference: {path}"
    if kind == SHELL_EXECUTOR_KIND:
        return (
            "shell_executor_consent_required",
            f"{name} {SHELL_EXECUTOR_CARD_COPY}",
        )
    if kind == NEST_GIT_KIND:
        return (
            "nest_git_consent_required",
            f"{name} {NEST_GIT_CARD_COPY}",
        )
    return "host_path_consent_required", f"{name} requests host-path access: {path}"
