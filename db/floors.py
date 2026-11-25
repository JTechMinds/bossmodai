"""BossMod AI — Floor rows.

A floor is a labeled co-mingle domain. Lobby is the one floor every
existing agent and thread lands on, so nothing is left without a home.
"""

from __future__ import annotations

from datetime import datetime

from core.models.floor import Floor
from db.crud import execute, fetch_all, fetch_one, query

LOBBY_ID = "lobby"
LOBBY_NAME = "Lobby"

_COLUMNS = "id, name, created_at, suspended_at"


def ensure_lobby() -> str:
    """Insert Lobby when it is missing. Returns its stable id."""
    existing = fetch_one(
        f"SELECT {_COLUMNS} FROM floors WHERE id = $1",
        [LOBBY_ID],
        Floor,
    )
    if existing is not None:
        return existing.id
    execute(
        "INSERT INTO floors (id, name) VALUES ($1, $2) ON CONFLICT(id) DO NOTHING",
        [LOBBY_ID, LOBBY_NAME],
    )
    return LOBBY_ID


def get_floor(floor_id: str) -> Floor | None:
    """Return one floor by id."""
    token = (floor_id or "").strip()
    if not token:
        return None
    return fetch_one(
        f"SELECT {_COLUMNS} FROM floors WHERE id = $1",
        [token],
        Floor,
    )


def get_floor_by_name(name: str) -> Floor | None:
    """Return one floor by its label, ignoring case and extra space."""
    label = " ".join((name or "").split())
    if not label:
        return None
    return fetch_one(
        f"SELECT {_COLUMNS} FROM floors WHERE lower(name) = lower($1)",
        [label],
        Floor,
    )


def list_floors() -> list[Floor]:
    """Return every floor, Lobby first, then by name.

    Suspended floors are included; each row carries ``suspended_at`` (None
    while active), so a caller splits active from suspended itself.
    """
    ensure_lobby()
    return fetch_all(
        f"""
        SELECT {_COLUMNS}
        FROM floors
        ORDER BY CASE WHEN id = $1 THEN 0 ELSE 1 END, lower(name)
        """,
        [LOBBY_ID],
        Floor,
    )


def set_floor_suspended(floor_id: str, suspended_at: datetime | None) -> Floor:
    """Suspend one floor (``suspended_at`` set) or resume it (``None``).

    Only writes the flag; the clean stop and the resume rules live in
    ``core/floor_suspend.py``.

    Args:
        floor_id: The floor to change.
        suspended_at: The instant it was suspended, or ``None`` to resume.

    Returns:
        The floor as stored after the write.

    Raises:
        ValueError: The floor is Lobby, which must always stay active.
        LookupError: No floor has this id.
    """
    token = (floor_id or "").strip()
    if token == LOBBY_ID:
        raise ValueError("Lobby cannot be suspended")
    updated = fetch_one(
        f"UPDATE floors SET suspended_at = $1 WHERE id = $2 RETURNING {_COLUMNS}",
        [suspended_at, token],
        Floor,
    )
    if updated is None:
        raise LookupError("Floor not found")
    return updated


def suspended_floor_ids() -> frozenset[str]:
    """Return the ids of every suspended floor, in one query."""
    rows = query("SELECT id FROM floors WHERE suspended_at IS NOT NULL")
    return frozenset(str(row["id"]) for row in rows)


class FloorNameTaken(ValueError):
    """Another floor already carries this label, ignoring case."""


FLOOR_NAME_MAX_LENGTH = 80


def _normalize_name(name: str) -> str:
    """Collapse whitespace and validate a floor label.

    Raises ValueError when the label is blank or longer than the cap.
    """
    label = " ".join((name or "").split())
    if not label:
        raise ValueError("Floor name is required")
    if len(label) > FLOOR_NAME_MAX_LENGTH:
        raise ValueError(f"Floor name must be {FLOOR_NAME_MAX_LENGTH} characters or fewer")
    return label


def create_floor(name: str) -> Floor:
    """Create a labeled floor, or return the active one that already has this name.

    The label is normalized and validated (``_normalize_name``). An active
    floor with the same name, ignoring case, is returned unchanged. A
    suspended one is refused instead: returning it would put the operator on
    a floor that is off the list.

    Raises:
        ValueError: The label is blank or too long.
        FloorNameTaken: A suspended floor already uses the label, ignoring case.
        RuntimeError: The insert returned no row.
    """
    label = _normalize_name(name)
    existing = get_floor_by_name(label)
    if existing is not None:
        if existing.suspended_at is not None:
            raise FloorNameTaken("A suspended floor already uses that name. Resume it from Suspended floors.")
        return existing
    import uuid

    floor_id = uuid.uuid4().hex
    created = fetch_one(
        f"""
        INSERT INTO floors (id, name)
        VALUES ($1, $2)
        RETURNING {_COLUMNS}
        """,
        [floor_id, label],
        Floor,
    )
    if created is None:
        raise RuntimeError("Floor insert did not return a row")
    return created


def rename_floor(floor_id: str, name: str) -> Floor:
    """Give one floor a new label. Lobby may be renamed; its id never changes.

    The label is normalized and validated the way ``create_floor`` does it.
    Renaming a floor to its own name (any case) is allowed.

    Raises:
        ValueError: The label is blank or too long.
        LookupError: No floor has this id.
        FloorNameTaken: Another floor already uses the label, ignoring case.
    """
    label = _normalize_name(name)
    floor = get_floor(floor_id)
    if floor is None:
        raise LookupError("Floor not found")
    clash = get_floor_by_name(label)
    if clash is not None and clash.id != floor.id:
        raise FloorNameTaken("Another floor already uses that name")
    updated = fetch_one(
        f"UPDATE floors SET name = $1 WHERE id = $2 RETURNING {_COLUMNS}",
        [label, floor.id],
        Floor,
    )
    if updated is None:
        raise LookupError("Floor not found")
    return updated


def delete_floor_row(floor_id: str) -> None:
    """Remove one floor row. Callers clear its agents and threads first.

    Telegram users on this floor move to Lobby with their chat closed, so a
    stored Telegram floor always names a floor that exists.

    Raises ValueError for Lobby, which every agent and thread falls back on.
    """
    token = (floor_id or "").strip()
    if token == LOBBY_ID:
        raise ValueError("Lobby cannot be deleted")
    execute(
        """
        UPDATE telegram_sessions
        SET floor_id = $1,
            session_type = 'idle',
            target_agent_id = NULL,
            target_channel_id = NULL,
            agent_names_key = NULL,
            updated_at = current_timestamp
        WHERE floor_id = $2
        """,
        [LOBBY_ID, token],
    )
    execute("DELETE FROM floors WHERE id = $1", [token])
