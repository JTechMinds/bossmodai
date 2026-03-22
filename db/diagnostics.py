"""BossMod AI — Diagnostics CRUD."""

from __future__ import annotations

from typing import Any

from core import config
from db.crud import execute, insert_returning_dict, query, query_one

# Summary columns returned by list queries (excludes large blobs)
_SUMMARY_COLUMNS = (
    "id, agent_id, agent_name, trigger_type, status, mode, model, "
    "model_source, action_name, prompt_tokens, completion_tokens, "
    "total_tokens, error, duration_ms, created_at"
)

# All columns for single-entry detail
_ALL_COLUMNS = (
    "id, agent_id, agent_name, trigger_type, trigger_data, status, mode, "
    "model, model_source, context, raw_response, action_name, parsed_action, "
    "result, prompt_tokens, completion_tokens, total_tokens, error, "
    "duration_ms, created_at"
)


def create_diagnostic(
    agent_id: str,
    agent_name: str,
    trigger_type: str,
    trigger_data: str,
    status: str = "success",
    mode: str | None = None,
    model: str | None = None,
    model_source: str | None = None,
    context: str | None = None,
    raw_response: str | None = None,
    action_name: str | None = None,
    parsed_action: str | None = None,
    result: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    error: str | None = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Insert a diagnostic entry and auto-purge old rows if over limit."""
    row = insert_returning_dict(
        f"""
        INSERT INTO diagnostics (
            agent_id, agent_name, trigger_type, trigger_data, status,
            mode, model, model_source, context, raw_response,
            action_name, parsed_action, result,
            prompt_tokens, completion_tokens, total_tokens,
            error, duration_ms
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
        RETURNING {_SUMMARY_COLUMNS}
        """,
        [
            agent_id, agent_name, trigger_type, trigger_data, status,
            mode, model, model_source, context, raw_response,
            action_name, parsed_action, result,
            prompt_tokens, completion_tokens, total_tokens,
            error, duration_ms,
        ],
    )
    _auto_purge()
    return row


def get_diagnostics(
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent diagnostic summaries (no blobs), newest first."""
    if agent_id:
        return query(
            f"SELECT {_SUMMARY_COLUMNS} FROM diagnostics "
            "WHERE agent_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2",
            [agent_id, limit],
        )
    return query(
        f"SELECT {_SUMMARY_COLUMNS} FROM diagnostics "
        "ORDER BY created_at DESC, id DESC LIMIT $1",
        [limit],
    )


def get_diagnostic(diagnostic_id: str) -> dict[str, Any] | None:
    """Return a single diagnostic entry with all fields."""
    return query_one(
        f"SELECT {_ALL_COLUMNS} FROM diagnostics WHERE id = $1",
        [diagnostic_id],
    )


def _auto_purge() -> None:
    """Delete oldest rows if count exceeds retention limit."""
    limit = config.get_int("diagnostics_retention_limit") or 5000
    count_row = query_one("SELECT COUNT(*) AS cnt FROM diagnostics")
    if count_row and count_row["cnt"] > limit:
        excess = count_row["cnt"] - limit
        execute(
            "DELETE FROM diagnostics WHERE id IN ("
            "  SELECT id FROM diagnostics ORDER BY created_at ASC, id ASC LIMIT $1"
            ")",
            [excess],
        )
