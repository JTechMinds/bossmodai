"""Task-side sticky slot storage.

Each row is one fact keyed by a real source id: a task id, an owner id,
a verdict path, or a blocker event id. Rows are not deleted here.
``preserve_source_ids`` keeps an existing blockers body, so an open
condition is not replaced. A failed caller transaction leaves the
previous rows in place.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from db.connection import transaction
from db.crud import execute, query, query_one

SLOT_KINDS = ("plan", "next_owner", "verdict_path", "blockers")
_SOURCE_ID_MAX = 300


def note_sticky_slot_turn() -> None:
    """Count one agent turn toward the gap between sticky-slot fills."""
    with transaction():
        execute(
            """
            INSERT INTO sticky_slot_gate (id, turns_since_run)
            VALUES (1, 1)
            ON CONFLICT(id) DO UPDATE SET
                turns_since_run = sticky_slot_gate.turns_since_run + 1
            """
        )


def get_sticky_slot_gate() -> dict[str, Any]:
    """Return the gate row, creating it at zero turns when missing."""
    with transaction():
        execute(
            """
            INSERT INTO sticky_slot_gate (id, turns_since_run)
            VALUES (1, 0)
            ON CONFLICT(id) DO NOTHING
            """
        )
        row = query_one(
            "SELECT id, turns_since_run, last_run_at FROM sticky_slot_gate WHERE id = 1"
        )
    return row or {"id": 1, "turns_since_run": 0, "last_run_at": None}


def try_claim_sticky_slot_run(*, min_turns: int, cooldown: timedelta) -> bool:
    """Claim the next fill slot. False when the gap or cooldown has not elapsed.

    The first claim (no prior run) is allowed. A successful claim zeros the
    turn gap and stamps ``last_run_at``.
    """
    now = datetime.now(timezone.utc)
    with transaction():
        execute(
            """
            INSERT INTO sticky_slot_gate (id, turns_since_run)
            VALUES (1, 0)
            ON CONFLICT(id) DO NOTHING
            """
        )
        row = query_one(
            "SELECT turns_since_run, last_run_at FROM sticky_slot_gate WHERE id = 1"
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
            "UPDATE sticky_slot_gate SET turns_since_run = 0, last_run_at = $1 WHERE id = 1",
            [now],
        )
        return True


def get_sticky_slot(source_id: str, slot_kind: str) -> dict[str, Any] | None:
    """Return one stored fact, if the key is well formed."""
    key = _key(source_id, slot_kind)
    if key is None:
        return None
    source, kind = key
    return query_one(
        """
        SELECT source_id, slot_kind, body, updated_at
        FROM sticky_slots
        WHERE source_id = $1 AND slot_kind = $2
        """,
        [source, kind],
    )


def list_sticky_slots(source_ids: list[str]) -> list[dict[str, Any]]:
    """Return stored facts for these source ids. Unknown ids contribute nothing."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for source_id in source_ids:
        token = (source_id or "").strip()
        if not token or token in seen or len(token) > _SOURCE_ID_MAX:
            continue
        seen.add(token)
        cleaned.append(token)
    if not cleaned:
        return []
    placeholders = ", ".join(f"${index + 1}" for index in range(len(cleaned)))
    return query(
        f"""
        SELECT source_id, slot_kind, body, updated_at
        FROM sticky_slots
        WHERE source_id IN ({placeholders})
        ORDER BY source_id, slot_kind
        """,
        cleaned,
    )


def upsert_sticky_slots(
    slots: list[dict[str, str]],
    *,
    preserve_source_ids: set[str] | None = None,
) -> None:
    """Insert or replace returned facts. Other rows stay.

    An existing blockers row whose source id is in ``preserve_source_ids``
    is left as stored. Empty input does not write. Invalid keys raise so
    the caller can fail closed without a partial pocket.
    """
    preserve = preserve_source_ids or set()
    incoming: list[tuple[str, str, str]] = []
    for slot in slots:
        key = _key(str(slot.get("source_id") or ""), str(slot.get("slot_kind") or ""))
        body = str(slot.get("body") or "").strip()
        if key is None or not body:
            continue
        incoming.append((key[0], key[1], body))
    if not incoming:
        return
    now = datetime.now(timezone.utc)
    with transaction():
        for source_id, slot_kind, body in incoming:
            existing = query_one(
                """
                SELECT body FROM sticky_slots
                WHERE source_id = $1 AND slot_kind = $2
                """,
                [source_id, slot_kind],
            )
            if existing and slot_kind == "blockers" and source_id in preserve:
                continue
            execute(
                """
                INSERT INTO sticky_slots (source_id, slot_kind, body, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT(source_id, slot_kind) DO UPDATE SET
                    body = excluded.body,
                    updated_at = excluded.updated_at
                """,
                [source_id, slot_kind, body, now],
            )


def _key(source_id: str, slot_kind: str) -> tuple[str, str] | None:
    source = (source_id or "").strip()
    kind = (slot_kind or "").strip()
    if kind not in SLOT_KINDS or not source or len(source) > _SOURCE_ID_MAX:
        return None
    if any(char in source for char in ("\n", "\r", "\x00")):
        return None
    return source, kind


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
