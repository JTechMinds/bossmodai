"""BossMod AI — Agent snapshot storage: Add agent's Recent.

One current, secret-free copy of each agent's setup, so an agent can be made
again after it is deleted — a delete is a hard delete, and before this nothing
could bring a tuned agent back. db/agents.py captures on create, on every save
and on a prompt-history policy change, and one last time on delete, stamped
deleted. Each capture overwrites the agent's one row (there is no version
history) and then trims the table to the newest ``recent_agents_limit``.

NEVER A SECRET. What is written is an explicit column list, not the agent row:
``api_key``, ``api_base_url`` and ``extra_body`` come from an AI connection and
may carry credentials, so they are never read here and the table has no column
to hold them. A recreate re-links a connection by the model names kept.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.agent_loop.communication_contract import dump_communication_json
from core.models import Agent, AgentPromptHistoryPolicy, AgentSnapshot
from db.crud import fetch_all, insert_returning, query

_SNAPSHOT_COLUMNS = (
    "id, agent_id, name, role, description, done_fail_bar, communication, "
    "prompt_template, color, model_social, model_work, model_reasoning, "
    "model_extraction, model_self_queue, desk_x, desk_y, prompt_history_policy, "
    "captured_at, deleted_at"
)

# The four policy fields the create form edits. Its row's agent_id and its
# timestamps belong to the agent that had it, not to one made from it.
_POLICY_FIELDS = {
    "last_n_histories",
    "max_allowed_history_tokens",
    "earliest_ts_allowed",
    "include_notifications",
}

# Newest first. `id` breaks a tie between two captures in one clock tick, so
# the order — and with it which rows a prune keeps — is never arbitrary.
_NEWEST_FIRST = "ORDER BY captured_at DESC, id DESC"


def _encode_policy(policy: AgentPromptHistoryPolicy | None) -> str | None:
    if policy is None:
        return None
    return json.dumps(policy.model_dump(mode="json", include=_POLICY_FIELDS), sort_keys=True)


def capture_agent_snapshot(
    agent: Agent,
    policy: AgentPromptHistoryPolicy | None,
    *,
    deleted: bool,
) -> AgentSnapshot:
    """Write the one snapshot row for ``agent`` and trim the table.

    Upserts by ``agent_id`` from an explicit, non-secret column list —
    ``api_key``, ``api_base_url`` and ``extra_body`` are never read off the
    agent — so the row keeps its ``id`` across captures. ``captured_at`` is
    now (UTC); ``deleted_at`` is now when ``deleted`` and NULL otherwise.
    Then prunes to the ``recent_agents_limit`` setting.

    Args:
        agent: The agent as it stands after the write that changed it — or,
            for a delete, before its rows go.
        policy: Its prompt-history policy, or ``None`` when it has no row;
            stored as the four fields the create form edits.
        deleted: Whether this is the last capture a delete makes.

    Returns:
        The stored snapshot, as written — before the prune, which may remove it
        when the limit is 0.

    Raises:
        core.config.ConfigError: When ``recent_agents_limit`` is missing or is
            not an integer. It is seeded, so its absence is a bug to surface,
            not a default to guess.
        ValueError: When ``recent_agents_limit`` is negative
            (``prune_agent_snapshots``).
    """
    now = datetime.now(timezone.utc)
    snapshot = insert_returning(
        f"""
        INSERT INTO agent_snapshots (
            agent_id, name, role, description, done_fail_bar, communication,
            prompt_template, color, model_social, model_work, model_reasoning,
            model_extraction, model_self_queue, desk_x, desk_y,
            prompt_history_policy, captured_at, deleted_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
        ON CONFLICT (agent_id) DO UPDATE SET
            name = excluded.name,
            role = excluded.role,
            description = excluded.description,
            done_fail_bar = excluded.done_fail_bar,
            communication = excluded.communication,
            prompt_template = excluded.prompt_template,
            color = excluded.color,
            model_social = excluded.model_social,
            model_work = excluded.model_work,
            model_reasoning = excluded.model_reasoning,
            model_extraction = excluded.model_extraction,
            model_self_queue = excluded.model_self_queue,
            desk_x = excluded.desk_x,
            desk_y = excluded.desk_y,
            prompt_history_policy = excluded.prompt_history_policy,
            captured_at = excluded.captured_at,
            deleted_at = excluded.deleted_at
        RETURNING {_SNAPSHOT_COLUMNS}
        """,
        [
            agent.id,
            agent.name,
            agent.role,
            agent.description,
            agent.done_fail_bar,
            dump_communication_json(agent.communication, specialty=agent.role),
            agent.prompt_template,
            agent.color,
            agent.model_social,
            agent.model_work,
            agent.model_reasoning,
            agent.model_extraction,
            agent.model_self_queue,
            agent.desk_x,
            agent.desk_y,
            _encode_policy(policy),
            now,
            now if deleted else None,
        ],
        AgentSnapshot,
    )
    # Imported here, not at module top: core/config.py imports the whole db
    # package at ITS top, so a module-level import here would be a cycle.
    from core import config

    prune_agent_snapshots(config.require_int("recent_agents_limit"))
    return snapshot


def list_agent_snapshots() -> list[AgentSnapshot]:
    """Return every snapshot, newest ``captured_at`` first.

    Deleted agents' snapshots are included — they are what Recent is for — and
    tell themselves apart by ``deleted_at``. An empty table is an empty list.
    """
    return fetch_all(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM agent_snapshots {_NEWEST_FIRST}",
        [],
        AgentSnapshot,
    )


def prune_agent_snapshots(keep: int) -> int:
    """Delete every snapshot but the newest ``keep``.

    Args:
        keep: How many to keep, newest by ``captured_at``. ``0`` keeps none.

    Returns:
        How many rows were deleted.

    Raises:
        ValueError: When ``keep`` is negative. A negative limit is a setting
            nobody can mean, and SQLite reads ``LIMIT -1`` as "no limit" — a
            prune that silently kept everything.
    """
    if keep < 0:
        raise ValueError(f"recent_agents_limit must be 0 or more, got {keep}")
    removed = query(
        f"""
        DELETE FROM agent_snapshots
        WHERE id NOT IN (
            SELECT id FROM agent_snapshots {_NEWEST_FIRST} LIMIT $1
        )
        RETURNING id
        """,
        [keep],
    )
    return len(removed)
