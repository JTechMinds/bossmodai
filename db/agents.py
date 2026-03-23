"""BossMod AI — Agent and AgentState CRUD."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.models import Agent, AgentState
from db.crud import (
    build_update,
    build_update_returning,
    execute,
    fetch_all,
    fetch_one,
    insert_returning,
    query,
)

_AGENT_COLUMNS = (
    "id, name, role, prompt_template, color, "
    "model_social, model_work, model_reasoning, model_extraction, model_self_queue, "
    "api_base_url, api_key, extra_body, desk_x, desk_y, "
    "guardian_token_limit, guardian_velocity_limit, "
    "guardian_repetition_threshold, guardian_no_progress_threshold, "
    "created_at"
)

_AGENT_VALID_COLUMNS = {
    "name", "role", "prompt_template", "color",
    "model_social", "model_work", "model_reasoning",
    "model_extraction", "model_self_queue",
    "api_base_url", "api_key", "extra_body", "desk_x", "desk_y",
    "guardian_token_limit", "guardian_velocity_limit",
    "guardian_repetition_threshold", "guardian_no_progress_threshold",
}

_STATE_COLUMNS = "agent_id, x, y, status, last_active_at, idle_since"

_STATE_VALID_COLUMNS = {"x", "y", "status", "last_active_at", "idle_since"}


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------

def create_agent(
    name: str,
    role: str | None = None,
    prompt_template: str | None = None,
    color: str = "#3b82f6",
    model_social: str | None = None,
    model_work: str | None = None,
    model_reasoning: str | None = None,
    model_extraction: str | None = None,
    model_self_queue: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    extra_body: str | None = None,
    desk_x: int | None = None,
    desk_y: int | None = None,
    guardian_token_limit: int = 30_000,
    guardian_velocity_limit: int = 10,
    guardian_repetition_threshold: float = 0.85,
    guardian_no_progress_threshold: int = 30,
) -> Agent:
    """Insert a new agent and its companion state row."""
    agent = insert_returning(
        f"""
        INSERT INTO agents (
            name, role, prompt_template, color,
            model_social, model_work, model_reasoning, model_extraction, model_self_queue,
            api_base_url, api_key, extra_body, desk_x, desk_y,
            guardian_token_limit, guardian_velocity_limit,
            guardian_repetition_threshold, guardian_no_progress_threshold
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
        RETURNING {_AGENT_COLUMNS}
        """,
        [
            name, role, prompt_template, color,
            model_social, model_work, model_reasoning, model_extraction, model_self_queue,
            api_base_url, api_key, extra_body, desk_x, desk_y,
            guardian_token_limit, guardian_velocity_limit,
            guardian_repetition_threshold, guardian_no_progress_threshold,
        ],
        Agent,
    )

    # Unassigned agents spawn in the hallway (14, 9) instead of void (0, 0)
    spawn_x = desk_x if desk_x is not None else 14
    spawn_y = desk_y if desk_y is not None else 9
    execute(
        "INSERT INTO agent_state (agent_id, x, y) VALUES ($1, $2, $3)",
        [agent.id, spawn_x, spawn_y],
    )

    return agent


def get_agent(agent_id: str) -> Agent | None:
    """Fetch a single agent by ID."""
    return fetch_one(
        f"SELECT {_AGENT_COLUMNS} FROM agents WHERE id = $1",
        [agent_id],
        Agent,
    )


def list_agents() -> list[Agent]:
    """Return all agents ordered by creation time."""
    return fetch_all(
        f"SELECT {_AGENT_COLUMNS} FROM agents ORDER BY created_at",
        model_cls=Agent,
    )


def update_agent(agent_id: str, **fields: Any) -> Agent | None:
    """Update an agent's fields. Returns the updated Agent or None."""
    build_update("agents", "id", agent_id, fields, _AGENT_VALID_COLUMNS)
    return get_agent(agent_id)


def delete_agent(agent_id: str) -> bool:
    """Delete an agent and its companion state row."""
    execute("DELETE FROM agent_state WHERE agent_id = $1", [agent_id])
    result = query("SELECT id FROM agents WHERE id = $1", [agent_id])
    if not result:
        return False
    execute("DELETE FROM agents WHERE id = $1", [agent_id])
    return True


def get_agents_by_ids(agent_ids: list[str]) -> dict[str, Agent]:
    """Batch-fetch agents by IDs. Returns a dict keyed by agent ID."""
    if not agent_ids:
        return {}
    placeholders = ", ".join(f"${i + 1}" for i in range(len(agent_ids)))
    agents = fetch_all(
        f"SELECT {_AGENT_COLUMNS} FROM agents WHERE id IN ({placeholders})",
        agent_ids,
        Agent,
    )
    return {a.id: a for a in agents}


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------

def get_agent_state(agent_id: str) -> AgentState | None:
    """Fetch the runtime state for an agent."""
    return fetch_one(
        f"SELECT {_STATE_COLUMNS} FROM agent_state WHERE agent_id = $1",
        [agent_id],
        AgentState,
    )


def update_agent_state(agent_id: str, **fields: Any) -> AgentState | None:
    """Update agent state. Auto-timestamps idle/active transitions."""
    valid = {k: v for k, v in fields.items() if k in _STATE_VALID_COLUMNS}

    new_status = valid.get("status")
    if new_status is not None:
        now = datetime.now(timezone.utc)
        if new_status == "idle":
            valid.setdefault("idle_since", now)
        else:
            valid.setdefault("last_active_at", now)

    if not valid:
        return get_agent_state(agent_id)

    return build_update_returning(
        "agent_state", "agent_id", agent_id,
        valid, _STATE_VALID_COLUMNS,
        _STATE_COLUMNS, AgentState,
    )
