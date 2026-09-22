"""Sticky-slot storage.

One typed work-spine pocket per conversation scope, plus one gate so a
fill cannot queue on every agent turn. Transcript rows are never deleted
here. A merge writes only the facts it is given; other columns stay.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from db.connection import transaction
from db.crud import execute, query_one

FACT_KEYS = ("plan", "next_owner", "verdict_path", "blockers")
_SCOPE_KINDS = frozenset({"channel", "task", "human", "peer", "meeting"})
_SCOPE_ID_MAX = 200
_SOURCE_ID_CAP = 24


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
    turn gap and stamps ``last_run_at`` so a later turn cannot queue again
    until both knobs allow it.
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


def get_sticky_slot(scope_kind: str, scope_id: str) -> dict[str, Any] | None:
    """Return the stored pocket for one scope, if any."""
    scoped = _scope(scope_kind, scope_id)
    if scoped is None:
        return None
    kind, ident = scoped
    return query_one(
        """
        SELECT scope_kind, scope_id, plan, next_owner, verdict_path, blockers,
               source_message_ids, updated_at
        FROM sticky_slots
        WHERE scope_kind = $1 AND scope_id = $2
        """,
        [kind, ident],
    )


def merge_sticky_slot(
    *,
    scope_kind: str,
    scope_id: str,
    facts: dict[str, str],
    source_message_ids: list[str],
) -> None:
    """Store returned facts. Keys absent from ``facts`` keep their current text.

    Empty ``facts`` or an empty source-id list does not write. Invalid
    scope raises so the caller can fail closed without a partial row.
    """
    scoped = _scope(scope_kind, scope_id)
    if scoped is None:
        raise ValueError("sticky slot scope is invalid")
    kind, ident = scoped
    incoming = {
        key: facts[key]
        for key in FACT_KEYS
        if isinstance(facts.get(key), str) and str(facts.get(key)).strip()
    }
    new_ids = _clean_ids(source_message_ids)
    if not incoming or not new_ids:
        return
    now = datetime.now(timezone.utc)
    with transaction():
        row = query_one(
            """
            SELECT plan, next_owner, verdict_path, blockers, source_message_ids
            FROM sticky_slots
            WHERE scope_kind = $1 AND scope_id = $2
            """,
            [kind, ident],
        )
        merged = {key: None for key in FACT_KEYS}
        existing_ids: list[str] = []
        if row:
            for key in FACT_KEYS:
                value = row.get(key)
                merged[key] = value if isinstance(value, str) and value.strip() else None
            existing_ids = _clean_ids(_load_ids(row.get("source_message_ids")))
        for key, value in incoming.items():
            merged[key] = value.strip()
        ids = _union_ids(existing_ids, new_ids)
        if not any(merged.values()):
            return
        execute(
            """
            INSERT INTO sticky_slots (
                scope_kind, scope_id, plan, next_owner, verdict_path, blockers,
                source_message_ids, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT(scope_kind, scope_id) DO UPDATE SET
                plan = excluded.plan,
                next_owner = excluded.next_owner,
                verdict_path = excluded.verdict_path,
                blockers = excluded.blockers,
                source_message_ids = excluded.source_message_ids,
                updated_at = excluded.updated_at
            """,
            [
                kind,
                ident,
                merged["plan"],
                merged["next_owner"],
                merged["verdict_path"],
                merged["blockers"],
                json.dumps(ids),
                now,
            ],
        )


def _scope(scope_kind: str, scope_id: str) -> tuple[str, str] | None:
    kind = (scope_kind or "").strip()
    ident = (scope_id or "").strip()
    if kind not in _SCOPE_KINDS or not ident or len(ident) > _SCOPE_ID_MAX:
        return None
    if any(char in ident for char in ("\n", "\r", "\x00")):
        return None
    return kind, ident


def _load_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        return []
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, str)]


def _clean_ids(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = item.strip()
        if not token or token in seen:
            continue
        if len(token) > 80 or any(char in token for char in ("\n", "\r", "\x00")):
            continue
        seen.add(token)
        ordered.append(token)
    if len(ordered) > _SOURCE_ID_CAP:
        ordered = ordered[-_SOURCE_ID_CAP:]
    return ordered


def _union_ids(existing: list[str], incoming: list[str]) -> list[str]:
    return _clean_ids([*existing, *incoming])


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
