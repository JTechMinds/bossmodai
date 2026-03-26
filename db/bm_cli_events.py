"""BossMod AI — BossMod CLI audit event storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from db.crud import insert_returning_dict, query


def create_bm_cli_event(
    *,
    agent_id: str,
    command: str,
    content_present: bool,
    executor: str,
    cwd_before: str | None,
    cwd_after: str | None,
    policy_tier: str,
    decision: str,
    exit_code: int,
    result_kind: str | None,
    stdout_preview: str | None,
    stderr_preview: str | None,
    changed_paths: str | None,
    trigger_type: str | None = None,
) -> dict[str, Any]:
    """Insert a single BossMod CLI audit event row."""
    return insert_returning_dict(
        """
        INSERT INTO bm_cli_events (
            agent_id, command, content_present, executor,
            cwd_before, cwd_after, policy_tier, decision,
            exit_code, result_kind, stdout_preview, stderr_preview,
            changed_paths, trigger_type, created_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        RETURNING
            id, agent_id, command, content_present, executor,
            cwd_before, cwd_after, policy_tier, decision,
            exit_code, result_kind, stdout_preview, stderr_preview,
            changed_paths, trigger_type, created_at
        """,
        [
            agent_id,
            command,
            content_present,
            executor,
            cwd_before,
            cwd_after,
            policy_tier,
            decision,
            exit_code,
            result_kind,
            stdout_preview,
            stderr_preview,
            changed_paths,
            trigger_type,
            datetime.now(timezone.utc),
        ],
    )


def list_bm_cli_events(agent_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Return recent BossMod CLI audit events, newest first."""
    if agent_id:
        return query(
            """
            SELECT
                id, agent_id, command, content_present, executor,
                cwd_before, cwd_after, policy_tier, decision,
                exit_code, result_kind, stdout_preview, stderr_preview,
                changed_paths, trigger_type, created_at
            FROM bm_cli_events
            WHERE agent_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            [agent_id, limit],
        )
    return query(
        """
        SELECT
            id, agent_id, command, content_present, executor,
            cwd_before, cwd_after, policy_tier, decision,
            exit_code, result_kind, stdout_preview, stderr_preview,
            changed_paths, trigger_type, created_at
        FROM bm_cli_events
        ORDER BY created_at DESC, id DESC
        LIMIT $1
        """,
        [limit],
    )


def has_bm_cli_write_for_path(
    agent_id: str,
    path: str,
    *,
    since: datetime,
) -> bool:
    """Return whether the agent wrote/appended the exact path after the given time."""
    rows = query(
        """
        SELECT changed_paths
        FROM bm_cli_events
        WHERE agent_id = $1
          AND result_kind IN ('write', 'append')
          AND created_at >= $2
        ORDER BY created_at DESC, id DESC
        LIMIT 200
        """,
        [agent_id, since],
    )
    for row in rows:
        changed_paths = row.get("changed_paths")
        if not changed_paths:
            continue
        try:
            values = json.loads(changed_paths)
        except json.JSONDecodeError:
            continue
        if isinstance(values, list) and path in values:
            return True
    return False
