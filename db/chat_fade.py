"""Chat fade storage.

One summary per channel, plus one global gate so a fade cannot queue on
every agent turn. Transcript rows are never deleted here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from db.connection import transaction
from db.crud import execute, query_one


def note_chat_fade_turn() -> None:
    """Count one agent turn toward the gap between fade runs."""
    with transaction():
        execute(
            """
            INSERT INTO chat_fade_gate (id, turns_since_run)
            VALUES (1, 1)
            ON CONFLICT(id) DO UPDATE SET
                turns_since_run = chat_fade_gate.turns_since_run + 1
            """
        )


def get_chat_fade_gate() -> dict[str, Any]:
    """Return the gate row, creating it at zero turns when missing."""
    with transaction():
        execute(
            """
            INSERT INTO chat_fade_gate (id, turns_since_run)
            VALUES (1, 0)
            ON CONFLICT(id) DO NOTHING
            """
        )
        row = query_one(
            "SELECT id, turns_since_run, last_run_at FROM chat_fade_gate WHERE id = 1"
        )
    return row or {"id": 1, "turns_since_run": 0, "last_run_at": None}


def try_claim_chat_fade_run(*, min_turns: int, cooldown: timedelta) -> bool:
    """Claim the next fade slot. False when the gap or cooldown has not elapsed.

    The first claim (no prior run) is allowed. A successful claim zeros the
    turn gap and stamps ``last_run_at`` so a later turn cannot queue again
    until both knobs allow it.
    """
    now = datetime.now(timezone.utc)
    with transaction():
        execute(
            """
            INSERT INTO chat_fade_gate (id, turns_since_run)
            VALUES (1, 0)
            ON CONFLICT(id) DO NOTHING
            """
        )
        row = query_one(
            "SELECT turns_since_run, last_run_at FROM chat_fade_gate WHERE id = 1"
        )
        if row is None:
            return False
        last = _as_utc(row.get("last_run_at"))
        turns = int(row.get("turns_since_run") or 0)
        if last is not None:
            if turns < min_turns:
                return False
            if now - last < cooldown:
                return False
        execute(
            "UPDATE chat_fade_gate SET turns_since_run = 0, last_run_at = $1 WHERE id = 1",
            [now],
        )
        return True


def get_channel_chat_fade(channel_id: str) -> dict[str, Any] | None:
    """Return the stored fade for one channel, if any."""
    token = (channel_id or "").strip()
    if not token:
        return None
    return query_one(
        """
        SELECT channel_id, through_message_id, summary, updated_at
        FROM channel_chat_fades
        WHERE channel_id = $1
        """,
        [token],
    )


def upsert_channel_chat_fade(
    *,
    channel_id: str,
    through_message_id: str,
    summary: str,
) -> None:
    """Store one channel fade. Replaces any earlier summary for that channel."""
    now = datetime.now(timezone.utc)
    execute(
        """
        INSERT INTO channel_chat_fades (
            channel_id, through_message_id, summary, updated_at
        ) VALUES ($1, $2, $3, $4)
        ON CONFLICT(channel_id) DO UPDATE SET
            through_message_id = excluded.through_message_id,
            summary = excluded.summary,
            updated_at = excluded.updated_at
        """,
        [channel_id, through_message_id, summary, now],
    )


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None
