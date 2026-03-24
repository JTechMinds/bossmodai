"""BossMod AI — Persistent session helpers for virtual BossMod CLI."""

from __future__ import annotations

import db

DEFAULT_CLI_CWD = "/me"


def get_cli_cwd(agent_id: str) -> str:
    """Return the persisted working directory for an agent CLI session."""
    state = db.ensure_agent_cli_state(agent_id, default_cwd=DEFAULT_CLI_CWD)
    return state.cwd or DEFAULT_CLI_CWD


def set_cli_cwd(agent_id: str, cwd: str) -> str:
    """Persist a new working directory for an agent CLI session."""
    return db.update_agent_cli_state(agent_id, cwd=cwd).cwd
