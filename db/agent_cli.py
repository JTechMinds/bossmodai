"""BossMod AI — Persistent BossMod CLI session state."""

from __future__ import annotations

from datetime import datetime, timezone

from core.models.cli import AgentCliState
from db.crud import build_update_returning, fetch_one, insert_returning

_CLI_COLUMNS = "agent_id, cwd, updated_at"
_CLI_VALID_COLUMNS = {"cwd", "updated_at"}
DEFAULT_CLI_CWD = "/me"


def get_agent_cli_state(agent_id: str) -> AgentCliState | None:
    """Return the saved CLI session state for an agent."""
    return fetch_one(
        f"SELECT {_CLI_COLUMNS} FROM agent_cli_state WHERE agent_id = $1",
        [agent_id],
        AgentCliState,
    )


def ensure_agent_cli_state(agent_id: str, *, default_cwd: str = DEFAULT_CLI_CWD) -> AgentCliState:
    """Ensure an agent has a CLI session row and return it."""
    existing = get_agent_cli_state(agent_id)
    if existing is not None:
        return existing
    return insert_returning(
        f"""
        INSERT INTO agent_cli_state (agent_id, cwd)
        VALUES ($1, $2)
        RETURNING {_CLI_COLUMNS}
        """,
        [agent_id, default_cwd],
        AgentCliState,
    )


def update_agent_cli_state(agent_id: str, *, cwd: str) -> AgentCliState:
    """Persist the current CLI working directory for an agent."""
    ensure_agent_cli_state(agent_id)
    return build_update_returning(
        "agent_cli_state",
        "agent_id",
        agent_id,
        {"cwd": cwd, "updated_at": datetime.now(timezone.utc)},
        _CLI_VALID_COLUMNS,
        _CLI_COLUMNS,
        AgentCliState,
    )
