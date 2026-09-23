"""BossMod AI — Agent and AgentState CRUD.

Every write that changes an agent's setup also captures its snapshot for Add
agent's Recent (db/agent_snapshots.py): after a create or a save, and once
more before a delete removes anything, stamped deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import config
from core.agent_loop.communication_contract import dump_communication_json
from core.models import Agent, AgentState
from db.connection import transaction
from db.crud import (
    build_update,
    build_update_returning,
    execute,
    fetch_all,
    fetch_one,
    insert_returning_dict,
    query,
)
from db.agent_storage_identities import delete_agent_storage_identity, ensure_agent_storage_identity
from db.secret_store import decrypt_secret, encrypt_secret

_AGENT_COLUMNS = (
    "agents.id, agent_storage_identities.storage_key, agents.name, agents.role, "
    "agents.description, agents.done_fail_bar, agents.communication, "
    "agents.prompt_template, agents.color, "
    "agents.model_social, agents.model_work, "
    "agents.model_reasoning, agents.model_extraction, agents.model_self_queue, "
    "agents.api_base_url, agents.api_key, agents.extra_body, agents.desk_x, agents.desk_y, "
    "agents.guardian_token_limit, agents.guardian_velocity_limit, "
    "agents.guardian_repetition_threshold, agents.guardian_no_progress_threshold, "
    "agents.floor_id, agents.created_at"
)

_AGENT_VALID_COLUMNS = {
    "name", "role", "description", "done_fail_bar", "communication",
    "prompt_template", "color",
    "model_social", "model_work", "model_reasoning",
    "model_extraction", "model_self_queue",
    "api_base_url", "api_key", "extra_body", "desk_x", "desk_y",
    "guardian_token_limit", "guardian_velocity_limit",
    "guardian_repetition_threshold", "guardian_no_progress_threshold",
    "floor_id",
}

_STATE_COLUMNS = "agent_id, x, y, status, last_active_at, idle_since"

_STATE_VALID_COLUMNS = {"x", "y", "status", "last_active_at", "idle_since"}


def _decrypt_agent(agent: Agent | None) -> Agent | None:
    if agent is None or not agent.api_key:
        return agent
    plain = decrypt_secret(agent.api_key)
    if plain == agent.api_key:
        return agent
    return agent.model_copy(update={"api_key": plain})


def _capture_snapshot(agent: Agent, *, deleted: bool) -> None:
    """Capture ``agent``'s setup, with its prompt-history policy, for Recent.

    Raises whatever ``capture_agent_snapshot`` raises; a caller inside a
    transaction rolls its own write back with it.
    """
    # Imported here, not at module top, the way delete_agent imports
    # db.host_path_consent: db.agent_prompt_history_policies imports this
    # module back to re-capture after a policy change, and keeping the
    # snapshot hooks' imports inside functions keeps the db package's load
    # order free of cycles.
    from db.agent_prompt_history_policies import get_agent_prompt_history_policy
    from db.agent_snapshots import capture_agent_snapshot

    capture_agent_snapshot(agent, get_agent_prompt_history_policy(agent.id), deleted=deleted)


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------

def create_agent(
    name: str,
    role: str | None = None,
    description: str | None = None,
    done_fail_bar: str | None = None,
    communication: dict[str, str] | None = None,
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
    floor_id: str | None = None,
) -> Agent:
    """Insert a new agent, its companion state rows and its snapshot atomically.

    Home floor defaults to Lobby. A named floor must already exist.
    """
    from db.floors import LOBBY_ID, ensure_lobby, get_floor

    ensure_lobby()
    home = (floor_id or "").strip() or LOBBY_ID
    if get_floor(home) is None:
        raise ValueError("Floor not found")
    with transaction():
        created = insert_returning_dict(
            """
            INSERT INTO agents (
                name, role, description, done_fail_bar, communication, prompt_template, color,
                model_social, model_work, model_reasoning, model_extraction, model_self_queue,
                api_base_url, api_key, extra_body, desk_x, desk_y,
                guardian_token_limit, guardian_velocity_limit,
                guardian_repetition_threshold, guardian_no_progress_threshold,
                floor_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)
            RETURNING id
            """,
            [
                name, role, description, done_fail_bar,
                dump_communication_json(communication, specialty=role),
                prompt_template, color,
                model_social, model_work, model_reasoning, model_extraction, model_self_queue,
                api_base_url, encrypt_secret(api_key), extra_body, desk_x, desk_y,
                guardian_token_limit, guardian_velocity_limit,
                guardian_repetition_threshold, guardian_no_progress_threshold,
                home,
            ],
        )
        agent_id = str(created["id"])
        ensure_agent_storage_identity(agent_id)

        spawn_x = desk_x if desk_x is not None else config.get_int("default_spawn_x")
        spawn_y = desk_y if desk_y is not None else config.get_int("default_spawn_y")
        execute(
            "INSERT INTO agent_state (agent_id, x, y) VALUES ($1, $2, $3)",
            [agent_id, spawn_x, spawn_y],
        )
        execute(
            "INSERT INTO agent_cli_state (agent_id, cwd) VALUES ($1, $2)",
            [agent_id, "/me"],
        )
        history_n = config.get_int("default_prompt_history_last_n")
        history_tokens = config.get_int("default_prompt_history_max_tokens")
        execute(
            """
            INSERT INTO agent_prompt_history_policies (
                agent_id, last_n_histories, max_allowed_history_tokens, include_notifications
            ) VALUES ($1, $2, $3, $4)
            """,
            [agent_id, history_n, history_tokens, True],
        )

        agent = get_agent(agent_id)
        if agent is None:
            raise RuntimeError(f"Failed to reload created agent {agent_id}")
        # Inside the transaction: an agent whose snapshot could not be written
        # is not created either, so the operator's retry cannot make a second.
        _capture_snapshot(agent, deleted=False)
    return agent


def get_agent(agent_id: str) -> Agent | None:
    """Fetch a single agent by ID."""
    return _decrypt_agent(
        fetch_one(
            f"""
            SELECT {_AGENT_COLUMNS}
            FROM agents
            JOIN agent_storage_identities ON agent_storage_identities.agent_id = agents.id
            WHERE agents.id = $1
            """,
            [agent_id],
            Agent,
        )
    )


def list_agents() -> list[Agent]:
    """Return all agents ordered by creation time."""
    return [
        decrypted
        for agent in fetch_all(
            f"""
            SELECT {_AGENT_COLUMNS}
            FROM agents
            JOIN agent_storage_identities ON agent_storage_identities.agent_id = agents.id
            ORDER BY agents.created_at
            """,
            model_cls=Agent,
        )
        if (decrypted := _decrypt_agent(agent)) is not None
    ]


def update_agent(agent_id: str, **fields: Any) -> Agent | None:
    """Update an agent's fields. Returns the updated Agent or None.

    A write that applied anything re-captures the agent's snapshot.
    """
    if "api_key" in fields:
        fields = {**fields, "api_key": encrypt_secret(fields["api_key"])}
    if "communication" in fields:
        role = fields.get("role")
        if role is None:
            current = get_agent(agent_id)
            role = current.role if current is not None else None
        fields = {
            **fields,
            "communication": dump_communication_json(
                fields["communication"], specialty=role,
            ),
        }
    applied = build_update("agents", "id", agent_id, fields, _AGENT_VALID_COLUMNS)
    agent = get_agent(agent_id)
    if applied and agent is not None:
        _capture_snapshot(agent, deleted=False)
    return agent


def delete_agent(agent_id: str) -> bool:
    """Delete an agent and all dependent rows, keeping its setup as a snapshot.

    The snapshot is captured, stamped deleted, BEFORE the first row goes: its
    prompt-history policy is one of the rows this removes. Returns False when
    there is no such agent. Raises ``RuntimeError`` when the agent row exists
    but cannot be read whole, since deleting it then would lose the setup the
    snapshot exists to keep.
    """
    result = query("SELECT id FROM agents WHERE id = $1", [agent_id])
    if not result:
        return False
    agent = get_agent(agent_id)
    if agent is None:
        raise RuntimeError(
            f"Agent {agent_id} exists but could not be read for its last snapshot; "
            "refusing to delete it"
        )
    _capture_snapshot(agent, deleted=True)
    # notification_links -> notifications
    notification_ids = [r["id"] for r in query(
        "SELECT id FROM notifications WHERE agent_id = $1", [agent_id],
    )]
    for nid in notification_ids:
        execute("DELETE FROM notification_links WHERE notification_id = $1", [nid])
    execute("DELETE FROM notifications WHERE agent_id = $1", [agent_id])
    # clear shared-channel authored references and queue membership
    execute("UPDATE channel_messages SET author_agent_id = NULL WHERE author_agent_id = $1", [agent_id])
    execute("DELETE FROM channel_response_candidates WHERE agent_id = $1", [agent_id])
    execute("DELETE FROM channel_members WHERE agent_id = $1", [agent_id])
    # clear shared-meeting authored references and queue membership
    execute("UPDATE meeting_session_messages SET author_agent_id = NULL WHERE author_agent_id = $1", [agent_id])
    execute("UPDATE meeting_sessions SET created_by_agent_id = NULL WHERE created_by_agent_id = $1", [agent_id])
    execute("DELETE FROM meeting_response_candidates WHERE agent_id = $1", [agent_id])
    # activities (clear self-referential parent_activity_id first)
    execute(
        "UPDATE activities SET parent_activity_id = NULL WHERE agent_id = $1 AND parent_activity_id IS NOT NULL",
        [agent_id],
    )
    execute("DELETE FROM activities WHERE agent_id = $1", [agent_id])
    # tasks & task events
    execute("UPDATE task_events SET author_agent_id = NULL WHERE author_agent_id = $1", [agent_id])
    execute("UPDATE tasks SET owner_id = NULL WHERE owner_id = $1", [agent_id])
    execute("UPDATE tasks SET requester_id = NULL WHERE requester_id = $1", [agent_id])
    # host-path consent (grants first — FK to requests)
    from db.host_path_consent import delete_agent_consent

    delete_agent_consent(agent_id)
    # bm_cli_events.approval_request_id references cli_approval_requests.
    # Drop this agent's audit rows before those requests, and before the agent.
    execute("DELETE FROM bm_cli_events WHERE agent_id = $1", [agent_id])
    execute("DELETE FROM cli_approval_requests WHERE agent_id = $1", [agent_id])
    # CLI policy rules (nullable agent_id)
    execute("UPDATE cli_policy_rules SET agent_id = NULL WHERE agent_id = $1", [agent_id])
    # Telegram sessions (nullable target_agent_id)
    execute("UPDATE telegram_sessions SET target_agent_id = NULL WHERE target_agent_id = $1", [agent_id])
    # remaining FK dependents
    execute("DELETE FROM artifacts WHERE agent_id = $1", [agent_id])
    execute("DELETE FROM agent_triggers WHERE agent_id = $1", [agent_id])
    # companion tables
    execute("DELETE FROM agent_prompt_history_policies WHERE agent_id = $1", [agent_id])
    execute("DELETE FROM agent_cli_state WHERE agent_id = $1", [agent_id])
    execute("DELETE FROM agent_state WHERE agent_id = $1", [agent_id])
    delete_agent_storage_identity(agent_id)
    execute("DELETE FROM agents WHERE id = $1", [agent_id])
    return True


def get_agents_by_ids(agent_ids: list[str]) -> dict[str, Agent]:
    """Batch-fetch agents by IDs. Returns a dict keyed by agent ID."""
    if not agent_ids:
        return {}
    placeholders = ", ".join(f"${i + 1}" for i in range(len(agent_ids)))
    agents = fetch_all(
        f"""
        SELECT {_AGENT_COLUMNS}
        FROM agents
        JOIN agent_storage_identities ON agent_storage_identities.agent_id = agents.id
        WHERE agents.id IN ({placeholders})
        """,
        agent_ids,
        Agent,
    )
    return {
        agent.id: decrypted
        for agent in agents
        if (decrypted := _decrypt_agent(agent)) is not None
    }


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
