"""BossMod AI — Shared meeting session CRUD and live roster helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from core.models import MeetingSession, MeetingSessionMessage
from core.world.tilemap import get_room_at
from db.crud import execute, fetch_all, fetch_one, insert_returning, query

_SESSION_COLUMNS = "id, room_id, title, status, created_by_agent_id, created_at, updated_at, ended_at"
_MESSAGE_COLUMNS = "id, session_id, author_type, author_agent_id, author_name, content, source_channel, created_at"


def create_meeting_session(
    room_id: str,
    *,
    title: str,
    created_by_agent_id: str | None = None,
) -> MeetingSession:
    """Create a new active meeting session."""
    return insert_returning(
        f"""
        INSERT INTO meeting_sessions (room_id, title, status, created_by_agent_id)
        VALUES ($1, $2, 'active', $3)
        RETURNING {_SESSION_COLUMNS}
        """,
        [room_id, title, created_by_agent_id],
        MeetingSession,
    )


def get_meeting_session(session_id: str) -> MeetingSession | None:
    """Return one meeting session by id."""
    return fetch_one(
        f"SELECT {_SESSION_COLUMNS} FROM meeting_sessions WHERE id = $1",
        [session_id],
        MeetingSession,
    )


def get_active_meeting_session_by_room(room_id: str) -> MeetingSession | None:
    """Return the active meeting session anchored to a room."""
    return fetch_one(
        f"""
        SELECT {_SESSION_COLUMNS}
        FROM meeting_sessions
        WHERE room_id = $1 AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [room_id],
        MeetingSession,
    )


def update_meeting_session(
    session_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    ended_at: datetime | None = None,
) -> MeetingSession | None:
    """Update one meeting session."""
    fields: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if title is not None:
        fields["title"] = title
    if status is not None:
        fields["status"] = status
    if ended_at is not None:
        fields["ended_at"] = ended_at

    if len(fields) == 1:
        return get_meeting_session(session_id)

    assignments = ", ".join(f"{key} = ${index + 1}" for index, key in enumerate(fields.keys()))
    params = list(fields.values()) + [session_id]
    return fetch_one(
        f"""
        UPDATE meeting_sessions
        SET {assignments}
        WHERE id = ${len(params)}
        RETURNING {_SESSION_COLUMNS}
        """,
        params,
        MeetingSession,
    )


def end_meeting_session(session_id: str) -> MeetingSession | None:
    """Mark one meeting session ended."""
    now = datetime.now(timezone.utc)
    return update_meeting_session(session_id, status="ended", ended_at=now)


def create_meeting_session_message(
    *,
    session_id: str,
    author_type: str,
    author_name: str,
    content: str,
    source_channel: str,
    author_agent_id: str | None = None,
) -> MeetingSessionMessage:
    """Append one message to the shared meeting transcript."""
    return insert_returning(
        f"""
        INSERT INTO meeting_session_messages (
            session_id, author_type, author_agent_id, author_name, content, source_channel, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_MESSAGE_COLUMNS}
        """,
        [
            session_id,
            author_type,
            author_agent_id,
            author_name,
            content,
            source_channel,
            datetime.now(timezone.utc),
        ],
        MeetingSessionMessage,
    )


def list_meeting_session_messages(session_id: str, *, limit: int = 50) -> list[MeetingSessionMessage]:
    """Return recent meeting transcript entries, oldest first."""
    rows = fetch_all(
        f"""
        SELECT {_MESSAGE_COLUMNS}
        FROM meeting_session_messages
        WHERE session_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        [session_id, limit],
        MeetingSessionMessage,
    )
    rows.reverse()
    return rows


def list_active_meeting_participants(room_id: str) -> list[dict[str, object]]:
    """Return agents currently in an active meeting in the given room."""
    rows = query(
        """
        SELECT
            a.id,
            a.name,
            a.role,
            s.x,
            s.y,
            act.id AS activity_id,
            act.title AS activity_title,
            act.detail AS activity_detail
        FROM activities act
        JOIN agents a ON a.id = act.agent_id
        JOIN agent_state s ON s.agent_id = act.agent_id
        WHERE act.kind = 'meeting' AND act.status = 'active'
        ORDER BY a.name
        """
    )
    participants: list[dict[str, object]] = []
    for row in rows:
        room = get_room_at(int(row["x"]), int(row["y"]))
        if not room or room["id"] != room_id:
            continue
        participants.append(
            {
                "id": row["id"],
                "name": row["name"],
                "role": row.get("role"),
                "activity_id": row["activity_id"],
                "activity_title": row.get("activity_title"),
                "activity_detail": row.get("activity_detail"),
            }
        )
    return participants


def ensure_room_meeting_session(
    room_id: str,
    *,
    title: str,
    created_by_agent_id: str | None = None,
) -> MeetingSession:
    """Return an active session for a room, creating one when needed."""
    existing = get_active_meeting_session_by_room(room_id)
    if existing is None:
        return create_meeting_session(room_id, title=title, created_by_agent_id=created_by_agent_id)
    if title and title.strip() and existing.title != title:
        if existing.title in {"Meeting Room Session", "In-person meeting"}:
            updated = update_meeting_session(existing.id, title=title)
            return updated or existing
    return existing


def get_active_meeting_session_for_agent(agent_id: str) -> MeetingSession | None:
    """Return the active meeting session the agent is currently participating in."""
    from db.activities import get_active_activity
    from db.agents import get_agent_state

    activity = get_active_activity(agent_id)
    if activity is None or activity.kind != "meeting":
        return None

    state = get_agent_state(agent_id)
    if state is None:
        return None
    room = get_room_at(state.x, state.y)
    if not room or room["room_type"] != "meeting":
        return None

    session = get_active_meeting_session_by_room(room["id"])
    if session is None:
        title = activity.title or "Meeting Room Session"
        return create_meeting_session(room["id"], title=title, created_by_agent_id=agent_id)

    if not list_active_meeting_participants(session.room_id):
        ended = end_meeting_session(session.id)
        return None if ended else None
    return session


def get_formatted_meeting_session_messages(session_id: str, *, limit: int = 50) -> list[dict[str, object]]:
    """Return meeting transcript rows in the same shape as prompt/chat history entries."""
    return [
        {
            "id": item.id,
            "from_agent": item.author_agent_id or ("__human__" if item.author_type == "human" else "__system__"),
            "from_name": item.author_name,
            "to_agent": None,
            "content": item.content,
            "message_type": "meeting",
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "session_id": session_id,
        }
        for item in list_meeting_session_messages(session_id, limit=limit)
    ]


def delete_meeting_session_messages(session_id: str) -> None:
    """Delete all messages for one meeting session."""
    execute("DELETE FROM meeting_session_messages WHERE session_id = $1", [session_id])
