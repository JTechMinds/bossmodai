"""BossMod AI — Quiet-period idle-check bookkeeping for one thread.

This module is the only writer of ``channel_idle_checks``. The row is
engine state, kept apart from ``channel_host_state`` because
``save_channel_host_state`` rewrites every column and ``copy_stay`` /
``restore_stay`` snapshot it. A missing row is an unchecked thread.

``woken_agent_ids`` needs no agent-delete cleanup (unlike
``channel_host_state.work_agent_id``): the ids are only compared against
live candidates, so a deleted agent's id is inert.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from db.crud import execute, query_one


def get_channel_idle_check(channel_id: str) -> dict[str, Any]:
    """Return idle-check state for one thread. A missing row returns empty values.

    Returns:
        ``{channel_id, checked_message_id, human_message_id, woken_agent_ids,
        failed_message_id, failed_attempts}`` with string ids, a list of agent
        id strings, and a non-negative attempt count.

    Raises:
        ValueError: ``woken_agent_ids`` in the row is not a JSON list of
            strings, or ``failed_attempts`` is not a non-negative integer.
    """
    token = (channel_id or "").strip()
    empty = _empty(token)
    if not token:
        return empty
    row = query_one(
        """
        SELECT channel_id, checked_message_id, human_message_id, woken_agent_ids,
               failed_message_id, failed_attempts
        FROM channel_idle_checks
        WHERE channel_id = $1
        """,
        [token],
    )
    if row is None:
        return empty
    return {
        "channel_id": token,
        "checked_message_id": _text(row.get("checked_message_id")),
        "human_message_id": _text(row.get("human_message_id")),
        "woken_agent_ids": _ids(row.get("woken_agent_ids")),
        "failed_message_id": _text(row.get("failed_message_id")),
        "failed_attempts": _attempts(row.get("failed_attempts")),
    }


def save_channel_idle_check(state: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace one thread's idle-check row and return the stored state.

    Raises:
        ValueError: ``state`` has no ``channel_id``, or ``failed_attempts`` is
            not a non-negative integer.
    """
    channel_id = _text(state.get("channel_id"))
    if not channel_id:
        raise ValueError("save_channel_idle_check needs a channel_id")
    now = datetime.now(timezone.utc)
    checked = _text(state.get("checked_message_id")) or None
    human = _text(state.get("human_message_id")) or None
    failed_message = _text(state.get("failed_message_id")) or None
    failed_attempts = _attempts(state.get("failed_attempts"))
    woken: list[str] = []
    for agent_id in state.get("woken_agent_ids") or []:
        token = _text(agent_id)
        if token and token not in woken:
            woken.append(token)
    existing = query_one(
        "SELECT channel_id FROM channel_idle_checks WHERE channel_id = $1",
        [channel_id],
    )
    if existing is None:
        execute(
            """
            INSERT INTO channel_idle_checks (
                channel_id, checked_message_id, human_message_id, woken_agent_ids,
                failed_message_id, failed_attempts, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            [channel_id, checked, human, json.dumps(woken), failed_message, failed_attempts, now],
        )
    else:
        execute(
            """
            UPDATE channel_idle_checks
            SET checked_message_id = $2,
                human_message_id = $3,
                woken_agent_ids = $4,
                failed_message_id = $5,
                failed_attempts = $6,
                updated_at = $7
            WHERE channel_id = $1
            """,
            [channel_id, checked, human, json.dumps(woken), failed_message, failed_attempts, now],
        )
    return get_channel_idle_check(channel_id)


def _empty(channel_id: str) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "checked_message_id": "",
        "human_message_id": "",
        "woken_agent_ids": [],
        "failed_message_id": "",
        "failed_attempts": 0,
    }


def _ids(raw: object) -> list[str]:
    # Only this module writes the column, as json.dumps of a list of ids.
    # Anything else is corruption and is raised, not papered over.
    if raw is None or raw == "":
        return []
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"channel_idle_checks.woken_agent_ids is not a list of ids: {raw!r}")
    return [item for item in parsed if item.strip()]


def _attempts(raw: object) -> int:
    # A missing value is zero attempts. Anything that is not a whole
    # non-negative count is corruption and is raised, not papered over.
    if raw is None:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"channel_idle_checks.failed_attempts is not a non-negative count: {raw!r}")
    return raw


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
