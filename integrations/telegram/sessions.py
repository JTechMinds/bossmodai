"""BossMod AI — Telegram session state CRUD.

Each Telegram user has at most one active session that determines how
plain-text messages are routed (to a specific agent DM, a group channel,
or nowhere).
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from db.crud import execute, fetch_all, fetch_one, query_one

_COLUMNS = (
    "id, telegram_user_id, session_type, target_agent_id, "
    "target_channel_id, agent_names_key, last_active_at, created_at, updated_at"
)


class TelegramSession(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    telegram_user_id: int
    session_type: str
    target_agent_id: str | None = None
    target_channel_id: str | None = None
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
    target_agent_id: str | None = None,
    target_channel_id: str | None = None,
    agent_names_key: str | None = None,
) -> TelegramSession:
    """Create or replace the session for a Telegram user."""
    now = datetime.now(timezone.utc)
    existing = get_session(telegram_user_id)
    if existing is not None:
        execute(
            """
            UPDATE telegram_sessions
            SET session_type = $1,
                target_agent_id = $2,
                target_channel_id = $3,
                agent_names_key = $4,
                last_active_at = $5,
                updated_at = $5
            WHERE telegram_user_id = $6
            """,
            [session_type, target_agent_id, target_channel_id, agent_names_key, now, telegram_user_id],
        )
        return get_session(telegram_user_id)  # type: ignore[return-value]

    from db.crud import insert_returning
    return insert_returning(
        f"""
        INSERT INTO telegram_sessions
            (telegram_user_id, session_type, target_agent_id, target_channel_id, agent_names_key, last_active_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING {_COLUMNS}
        """,
        [telegram_user_id, session_type, target_agent_id, target_channel_id, agent_names_key, now],
        TelegramSession,
    )


def clear_session(telegram_user_id: int) -> None:
    """Remove the session row for a Telegram user (resets to no session)."""
    execute(
        "DELETE FROM telegram_sessions WHERE telegram_user_id = $1",
        [telegram_user_id],
    )


def list_active_sessions() -> list[TelegramSession]:
    """Return all sessions that are not idle."""
    return fetch_all(
        f"""
        SELECT {_COLUMNS}
        FROM telegram_sessions
        WHERE session_type != 'idle'
        ORDER BY last_active_at DESC
        """,
        [],
        TelegramSession,
    )


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


def find_channel_for_names_key(agent_names_key: str) -> str | None:
    """Return the target_channel_id from any session matching the names key, or None."""
    row = query_one(
        """
        SELECT target_channel_id
        FROM telegram_sessions
        WHERE agent_names_key = $1 AND target_channel_id IS NOT NULL
        LIMIT 1
        """,
        [agent_names_key],
    )
    if row and row.get("target_channel_id"):
        return row["target_channel_id"]
    return None
