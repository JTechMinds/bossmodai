"""BossMod AI — Telegram session state CRUD.

Each Telegram user has at most one active session that determines how
plain-text messages are routed (to a specific agent DM, a group channel,
or nowhere).
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from db.crud import execute, fetch_all, fetch_one
from db.floors import LOBBY_ID

_COLUMNS = (
    "id, telegram_user_id, session_type, target_agent_id, "
    "target_channel_id, floor_id, agent_names_key, last_active_at, created_at, updated_at"
)


class TelegramSession(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    telegram_user_id: int
    session_type: str
    target_agent_id: str | None = None
    target_channel_id: str | None = None
    # Required: a row with no floor is a bug and fails validation here.
    floor_id: str
    agent_names_key: str | None = None
    last_active_at: datetime
    created_at: datetime
    updated_at: datetime


def get_session(telegram_user_id: int) -> TelegramSession | None:
    """Look up the current session for a Telegram user."""
    return fetch_one(
        f"SELECT {_COLUMNS} FROM telegram_sessions WHERE telegram_user_id = $1",
        [telegram_user_id],
        TelegramSession,
    )


def upsert_session(
    telegram_user_id: int,
    *,
    session_type: str,
    floor_id: str,
    target_agent_id: str | None = None,
    target_channel_id: str | None = None,
    agent_names_key: str | None = None,
) -> TelegramSession:
    """Create or replace the session for a Telegram user.

    A conversation is always opened on a known floor, so ``floor_id`` is
    required and written on both the update and the insert.

    Args:
        telegram_user_id: The Telegram user (also their private chat id).
        session_type: ``'dm'``, ``'group'`` or ``'idle'``.
        floor_id: The floor the user is on after this write.
        target_agent_id: The DM's agent, for ``'dm'``.
        target_channel_id: The thread, for ``'group'``.
        agent_names_key: The lowercased roster label of the open chat.

    Returns:
        The session row as stored after the write.
    """
    now = datetime.now(timezone.utc)
    existing = get_session(telegram_user_id)
    if existing is not None:
        execute(
            """
            UPDATE telegram_sessions
            SET session_type = $1,
                target_agent_id = $2,
                target_channel_id = $3,
                floor_id = $4,
                agent_names_key = $5,
                last_active_at = $6,
                updated_at = $6
            WHERE telegram_user_id = $7
            """,
            [session_type, target_agent_id, target_channel_id, floor_id, agent_names_key, now, telegram_user_id],
        )
        return get_session(telegram_user_id)  # type: ignore[return-value]

    from db.crud import insert_returning
    return insert_returning(
        f"""
        INSERT INTO telegram_sessions
            (telegram_user_id, session_type, target_agent_id, target_channel_id, floor_id,
             agent_names_key, last_active_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_COLUMNS}
        """,
        [telegram_user_id, session_type, target_agent_id, target_channel_id, floor_id, agent_names_key, now],
        TelegramSession,
    )


def clear_session(telegram_user_id: int) -> None:
    """Closes the open chat or thread; the floor is kept.

    The row is the user's Telegram state, so it is set idle with no targets
    instead of deleted: deleting it would forget the floor.
    """
    execute(
        """
        UPDATE telegram_sessions
        SET session_type = 'idle',
            target_agent_id = NULL,
            target_channel_id = NULL,
            agent_names_key = NULL,
            updated_at = $1
        WHERE telegram_user_id = $2
        """,
        [datetime.now(timezone.utc), telegram_user_id],
    )


def set_floor(telegram_user_id: int, floor_id: str) -> TelegramSession:
    """Put a Telegram user on ``floor_id`` with no chat open.

    Moving floors closes the open chat, because its target lives on the old
    floor; in the app each floor has its own open chat too.

    Args:
        telegram_user_id: The Telegram user.
        floor_id: The floor to move to. The caller resolves it to a real floor.

    Returns:
        The idle session row on the new floor.
    """
    return upsert_session(telegram_user_id, session_type="idle", floor_id=floor_id)


def current_floor_id(telegram_user_id: int) -> str:
    """The floor this Telegram user is on.

    Lobby is the starting floor: a user with no row yet is on Lobby, the
    same rule as the app's ``floor-scope.js`` ``visibleFloorId``.
    """
    session = get_session(telegram_user_id)
    if session is None:
        return LOBBY_ID
    return session.floor_id


def list_sessions_for_agent(agent_id: str) -> list[TelegramSession]:
    """Return sessions currently in a DM with a specific agent."""
    return fetch_all(
        f"""
        SELECT {_COLUMNS}
        FROM telegram_sessions
        WHERE session_type = 'dm' AND target_agent_id = $1
        """,
        [agent_id],
        TelegramSession,
    )


def list_sessions_for_channel(channel_id: str) -> list[TelegramSession]:
    """Return sessions currently in a specific channel."""
    return fetch_all(
        f"""
        SELECT {_COLUMNS}
        FROM telegram_sessions
        WHERE session_type = 'group' AND target_channel_id = $1
        """,
        [channel_id],
        TelegramSession,
    )


def touch_session(telegram_user_id: int) -> None:
    """Bump ``last_active_at`` for the session."""
    now = datetime.now(timezone.utc)
    execute(
        "UPDATE telegram_sessions SET last_active_at = $1 WHERE telegram_user_id = $2",
        [now, telegram_user_id],
    )
