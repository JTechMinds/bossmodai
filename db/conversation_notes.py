"""Conversation notes storage — the only SQL for ``conversation_notes``.

One row per conversation scope: a human↔agent DM (``human_dm``, keyed by the
agent id), an agent↔agent DM (``agent_dm``, keyed ``"<lower id>:<higher id>"``)
or a channel thread (``channel``, keyed by the channel id). A row holds only
the CURRENT notes and the state of the last background run. Transcript rows
are never deleted here.

Policy (when to compact, what to summarize) lives in
``core/agent_loop/conversation_notes.py``; this module only reads and writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from db.connection import transaction
from db.crud import execute, query_one

SCOPE_KINDS = ("human_dm", "agent_dm", "channel")

_COLUMNS = (
    "scope_kind, scope_key, through_message_id, notes, source_tokens, "
    "run_state, run_started_at, last_run_at, last_error, updated_at"
)

# The release reason a commit records when its prior anchor moved under it.
SUPERSEDED = "superseded"


class AnchorMissing(LookupError):
    """The notes' anchor row no longer exists in its conversation.

    Raised by the thread readers when ``after_message_id`` names a row that was
    deleted (rewind, clear, single-row delete) or that is not part of the
    scope's conversation. The notes are then invalid.

    Attributes:
        kind: The scope kind.
        key: The scope key.
        message_id: The anchor id that could not be resolved.
    """

    def __init__(self, kind: str, key: str, message_id: str) -> None:
        self.kind = kind
        self.key = key
        self.message_id = message_id
        super().__init__(f"Anchor {message_id!r} is not in the {kind} conversation {key!r}")


def _require_scope(kind: str, key: str) -> None:
    if kind not in SCOPE_KINDS:
        raise ValueError(f"Unknown conversation scope kind {kind!r}")
    if not str(key or "").strip():
        raise ValueError("A conversation scope needs a non-empty key")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_conversation_notes(kind: str, key: str) -> dict[str, Any] | None:
    """Return the notes row of one conversation scope.

    Args:
        kind: ``human_dm``, ``agent_dm`` or ``channel``.
        key: The scope key (see the module docstring).

    Returns:
        The row as a dict (every column, timestamps as aware UTC datetimes),
        or ``None`` when the scope has never been compacted or claimed.

    Raises:
        ValueError: On an unknown kind or an empty key.
    """
    _require_scope(kind, key)
    return query_one(
        f"SELECT {_COLUMNS} FROM conversation_notes WHERE scope_kind = $1 AND scope_key = $2",
        [kind, key],
    )


def claim_conversation_notes_run(kind: str, key: str, *, stale_after: timedelta) -> bool:
    """Atomically mark one scope's background run as started.

    Succeeds when no run is recorded as in flight, or when the recorded run
    started longer ago than ``stale_after`` (a crashed worker cannot wedge a
    thread). Creates the row when the scope has none.

    Args:
        kind: The scope kind.
        key: The scope key.
        stale_after: How old a ``running`` claim must be before it can be taken over.

    Returns:
        True when this caller now owns the run; False when another run is in flight.

    Raises:
        ValueError: On an unknown kind or an empty key.
    """
    _require_scope(kind, key)
    now = _now()
    with transaction():
        row = query_one(
            "SELECT run_state, run_started_at FROM conversation_notes WHERE scope_kind = $1 AND scope_key = $2",
            [kind, key],
        )
        if row is None:
            execute(
                """
                INSERT INTO conversation_notes (scope_kind, scope_key, run_state, run_started_at)
                VALUES ($1, $2, 'running', $3)
                """,
                [kind, key, now],
            )
            return True
        started = row.get("run_started_at")
        if row.get("run_state") == "running" and started is not None and now - started < stale_after:
            return False
        execute(
            """
            UPDATE conversation_notes
            SET run_state = 'running', run_started_at = $3
            WHERE scope_kind = $1 AND scope_key = $2
            """,
            [kind, key, now],
        )
        return True


def _anchor_exists(kind: str, key: str, message_id: str) -> bool:
    if kind == "channel":
        row = query_one(
            "SELECT 1 AS present FROM channel_messages WHERE id = $1 AND channel_id = $2",
            [message_id, key],
        )
    else:
        row = query_one("SELECT 1 AS present FROM messages WHERE id = $1", [message_id])
    return row is not None


def commit_conversation_notes(
    kind: str,
    key: str,
    *,
    expected_prior_through_id: str | None,
    through_message_id: str,
    notes: str,
    source_tokens: int,
) -> bool:
    """Store new notes, compare-and-swap on the previous anchor.

    The write lands only when the stored ``through_message_id`` still equals
    ``expected_prior_through_id`` (``None`` = the scope had no notes) and the
    new anchor row still exists. Otherwise nothing of the notes is written, the
    run is released as idle with ``last_error='superseded'``, and False is
    returned, so a slow run can never overwrite newer notes or point at a
    deleted row.

    Args:
        kind: The scope kind.
        key: The scope key.
        expected_prior_through_id: The anchor the run read before summarizing.
        through_message_id: The last row folded into ``notes``.
        notes: The new notes text; replaces the old notes.
        source_tokens: Tokens of the input the notes were built from.

    Returns:
        True when the notes were stored; False when the run was superseded.

    Raises:
        ValueError: On an unknown kind, an empty key or empty notes.
    """
    _require_scope(kind, key)
    if not notes.strip():
        raise ValueError("Conversation notes must not be empty")
    now = _now()
    with transaction():
        row = query_one(
            "SELECT through_message_id FROM conversation_notes WHERE scope_kind = $1 AND scope_key = $2",
            [kind, key],
        )
        current = row.get("through_message_id") if row is not None else None
        if row is None or current != expected_prior_through_id or not _anchor_exists(kind, key, through_message_id):
            _upsert_run_state(kind, key, state="idle", error=SUPERSEDED, now=now)
            return False
        execute(
            """
            UPDATE conversation_notes
            SET through_message_id = $3, notes = $4, source_tokens = $5,
                run_state = 'idle', last_run_at = $6, last_error = NULL, updated_at = $6
            WHERE scope_kind = $1 AND scope_key = $2
            """,
            [kind, key, through_message_id, notes, int(source_tokens), now],
        )
        return True


def _upsert_run_state(kind: str, key: str, *, state: str, error: str, now: datetime) -> None:
    execute(
        """
        INSERT INTO conversation_notes (scope_kind, scope_key, run_state, last_run_at, last_error)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT(scope_kind, scope_key) DO UPDATE SET
            run_state = excluded.run_state,
            last_run_at = excluded.last_run_at,
            last_error = excluded.last_error
        """,
        [kind, key, state, now, error],
    )


def fail_conversation_notes_run(kind: str, key: str, *, error: str) -> None:
    """Record that one scope's background run failed.

    The previous notes stay in force; only the run state, the error and
    ``last_run_at`` change. The caller logs the failure.

    Args:
        kind: The scope kind.
        key: The scope key.
        error: Why the run failed, shown to the operator on the transcript.

    Raises:
        ValueError: On an unknown kind, an empty key or an empty error.
    """
    _require_scope(kind, key)
    if not error.strip():
        raise ValueError("A failed run needs an error")
    _upsert_run_state(kind, key, state="failed", error=error, now=_now())


def release_conversation_notes_run(kind: str, key: str, *, reason: str) -> None:
    """End one scope's run without new notes and without a failure.

    Used for "superseded" and "nothing to compact": the run is idle again,
    ``last_error`` says why nothing was written, and the notes are unchanged.

    Args:
        kind: The scope kind.
        key: The scope key.
        reason: Why the run ended without writing.

    Raises:
        ValueError: On an unknown kind, an empty key or an empty reason.
    """
    _require_scope(kind, key)
    if not reason.strip():
        raise ValueError("A released run needs a reason")
    _upsert_run_state(kind, key, state="idle", error=reason, now=_now())


def delete_conversation_notes(kind: str, key: str) -> None:
    """Delete one scope's notes row, if any.

    Called when the conversation's rows are cleared or rewound, and when the
    notes' anchor is found missing, so stale notes never linger.

    Raises:
        ValueError: On an unknown kind or an empty key.
    """
    _require_scope(kind, key)
    execute(
        "DELETE FROM conversation_notes WHERE scope_kind = $1 AND scope_key = $2",
        [kind, key],
    )


def delete_conversation_notes_for_agent(agent_id: str) -> None:
    """Delete every notes row of one agent's DMs: its human DM and its agent DMs.

    An ``agent_dm`` key is ``"<lower id>:<higher id>"``, so the agent's id is
    either the part before or the part after the colon. Matched with exact
    prefix/suffix comparison, not ``LIKE``, so an id is never read as a pattern.
    Channel notes are untouched: a channel outlives one member.

    Raises:
        ValueError: On an empty agent id.
    """
    token = str(agent_id or "").strip()
    if not token:
        raise ValueError("An agent id is required")
    execute(
        """
        DELETE FROM conversation_notes
        WHERE (scope_kind = 'human_dm' AND scope_key = $1)
           OR (scope_kind = 'agent_dm' AND (
                substr(scope_key, 1, length($1) + 1) = $1 || ':'
                OR substr(scope_key, -(length($1) + 1)) = ':' || $1
           ))
        """,
        [token],
    )
