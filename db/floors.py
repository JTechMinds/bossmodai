"""BossMod AI — Floor rows.

A floor is a labeled co-mingle domain. Lobby is the one floor every
existing agent and thread lands on, so nothing is left without a home.
"""

from __future__ import annotations

from core.models.floor import Floor
from db.crud import execute, fetch_all, fetch_one

LOBBY_ID = "lobby"
LOBBY_NAME = "Lobby"

_COLUMNS = "id, name, created_at"


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
    """Return every floor, Lobby first, then by name."""
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


def create_floor(name: str) -> Floor:
    """Create a labeled floor, or return the one that already has this name."""
    label = " ".join((name or "").split())
    if not label:
        raise ValueError("Floor name is required")
    if len(label) > 80:
        raise ValueError("Floor name must be 80 characters or fewer")
    existing = get_floor_by_name(label)
    if existing is not None:
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
