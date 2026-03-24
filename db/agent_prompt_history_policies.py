"""BossMod AI — Per-agent prompt-history policy storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.models.prompt_history import AgentPromptHistoryPolicy
from db.crud import execute, fetch_one, insert_returning, query

_POLICY_COLUMNS = (
    "agent_id, last_n_histories, max_allowed_history_tokens, "
    "earliest_ts_allowed, include_notifications, created_at, updated_at"
)


def get_agent_prompt_history_policy(agent_id: str) -> AgentPromptHistoryPolicy | None:
    """Return the configured prompt-history policy for one agent."""
    return fetch_one(
        f"SELECT {_POLICY_COLUMNS} FROM agent_prompt_history_policies WHERE agent_id = $1",
        [agent_id],
        AgentPromptHistoryPolicy,
    )


def create_agent_prompt_history_policy(
    agent_id: str,
    *,
    last_n_histories: int = 30,
    max_allowed_history_tokens: int = 2000,
    earliest_ts_allowed: datetime | None = None,
    include_notifications: bool = True,
) -> AgentPromptHistoryPolicy:
    """Create one prompt-history policy row for an agent."""
    return insert_returning(
        f"""
        INSERT INTO agent_prompt_history_policies (
            agent_id, last_n_histories, max_allowed_history_tokens,
            earliest_ts_allowed, include_notifications
        ) VALUES ($1, $2, $3, $4, $5)
        RETURNING {_POLICY_COLUMNS}
        """,
        [
            agent_id,
            last_n_histories,
            max_allowed_history_tokens,
            earliest_ts_allowed,
            include_notifications,
        ],
        AgentPromptHistoryPolicy,
    )


def ensure_agent_prompt_history_policy(agent_id: str) -> AgentPromptHistoryPolicy:
    """Return the prompt-history policy, creating the default row if needed."""
    existing = get_agent_prompt_history_policy(agent_id)
    if existing is not None:
        return existing
    return create_agent_prompt_history_policy(agent_id)


def update_agent_prompt_history_policy(agent_id: str, **fields: Any) -> AgentPromptHistoryPolicy:
    """Patch the prompt-history policy for one agent."""
    policy = ensure_agent_prompt_history_policy(agent_id)
    valid_fields = {
        key: value
        for key, value in fields.items()
        if key in {
            "last_n_histories",
            "max_allowed_history_tokens",
            "earliest_ts_allowed",
            "include_notifications",
        }
    }
    if not valid_fields:
        return policy
    valid_fields["updated_at"] = datetime.now(timezone.utc)
    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(valid_fields))
    params = list(valid_fields.values()) + [agent_id]
    execute(
        f"UPDATE agent_prompt_history_policies SET {assignments} WHERE agent_id = ${len(params)}",
        params,
    )
    refreshed = get_agent_prompt_history_policy(agent_id)
    if refreshed is None:
        raise RuntimeError(f"Failed to reload prompt-history policy for agent {agent_id}")
    return refreshed


def delete_agent_prompt_history_policy(agent_id: str) -> None:
    """Delete one prompt-history policy row."""
    execute("DELETE FROM agent_prompt_history_policies WHERE agent_id = $1", [agent_id])


def list_agent_prompt_history_policies(limit: int = 500) -> list[AgentPromptHistoryPolicy]:
    """Return all prompt-history policies for inspection."""
    return query(
        f"""
        SELECT {_POLICY_COLUMNS}
        FROM agent_prompt_history_policies
        ORDER BY created_at
        LIMIT $1
        """,
        [limit],
    )
