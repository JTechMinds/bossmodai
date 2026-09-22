"""BossMod AI — Host-owned Talk / Work / Paused state for one thread.

The row is engine state. It is not a model hint. A missing row is Talk
with empty counters.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from db.crud import execute, query_one

_COLUMNS = (
    "channel_id, paused, pass_streaks, demoted_ids, protected_ids, "
    "ack_streak, work_agent_id, work_task_id, updated_at"
)


def get_channel_host_state(channel_id: str) -> dict[str, Any]:
    """Return host state for one thread. Missing rows are an empty Talk state."""
    token = (channel_id or "").strip()
    empty = _empty(token)
    if not token:
        return empty
    row = query_one(
        f"SELECT {_COLUMNS} FROM channel_host_state WHERE channel_id = $1",
        [token],
    )
    if row is None:
        return empty
    return {
        "channel_id": token,
        "paused": bool(row.get("paused")),
        "pass_streaks": _json_map(row.get("pass_streaks")),
        "demoted_ids": _json_ids(row.get("demoted_ids")),
        "protected_ids": _json_ids(row.get("protected_ids")),
        "ack_streak": _int(row.get("ack_streak")),
        "work_agent_id": _text(row.get("work_agent_id")),
        "work_task_id": _text(row.get("work_task_id")),
    }


def save_channel_host_state(state: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace one thread's host state."""
    channel_id = _text(state.get("channel_id"))
    if not channel_id:
        return _empty("")
    now = datetime.now(timezone.utc)
    paused = 1 if state.get("paused") else 0
    streaks = json.dumps(dict(state.get("pass_streaks") or {}))
    demoted = json.dumps(list(state.get("demoted_ids") or []))
    protected = json.dumps(list(state.get("protected_ids") or []))
    ack = _int(state.get("ack_streak"))
    work_agent = _text(state.get("work_agent_id")) or None
    work_task = _text(state.get("work_task_id")) or None
    existing = query_one(
        "SELECT channel_id FROM channel_host_state WHERE channel_id = $1",
        [channel_id],
    )
    if existing is None:
        execute(
            """
            INSERT INTO channel_host_state (
                channel_id, paused, pass_streaks, demoted_ids, protected_ids,
                ack_streak, work_agent_id, work_task_id, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            [channel_id, paused, streaks, demoted, protected, ack, work_agent, work_task, now],
        )
    else:
        execute(
            """
            UPDATE channel_host_state
            SET paused = $2,
                pass_streaks = $3,
                demoted_ids = $4,
                protected_ids = $5,
                ack_streak = $6,
                work_agent_id = $7,
                work_task_id = $8,
                updated_at = $9
            WHERE channel_id = $1
            """,
            [channel_id, paused, streaks, demoted, protected, ack, work_agent, work_task, now],
        )
    return get_channel_host_state(channel_id)


def _empty(channel_id: str) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "paused": False,
        "pass_streaks": {},
        "demoted_ids": [],
        "protected_ids": [],
        "ack_streak": 0,
        "work_agent_id": "",
        "work_task_id": "",
    }


def _json_ids(raw: object) -> list[str]:
    parsed = _json_value(raw)
    if not isinstance(parsed, list):
        return []
    found: list[str] = []
    for item in parsed:
        token = _text(item)
        if token and token not in found:
            found.append(token)
    return found


def _json_map(raw: object) -> dict[str, int]:
    parsed = _json_value(raw)
    if not isinstance(parsed, dict):
        return {}
    found: dict[str, int] = {}
    for key, value in parsed.items():
        token = _text(key)
        if token:
            found[token] = _int(value)
    return found


def _json_value(raw: object) -> object:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0
