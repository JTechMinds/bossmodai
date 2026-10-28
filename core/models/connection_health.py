"""BossMod AI — per-connection provider health (``ai_connection_health``)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# What a counted provider failure said about the connection. ``transient``:
# the provider could not be reached, failed (5xx) or rate-limited, so trying
# again later can work. ``rejected``: the provider refused the request (any
# other 4xx, or an extension provider's usage limit), so only the operator
# can change the answer.
ConnectionFailureKind = Literal["transient", "rejected"]


class ConnectionHealth(BaseModel):
    """One AI connection's recent provider failures and, once paused, its pause.

    A row exists only while the connection's latest counted model calls
    failed; a success deletes it. The pause policy that writes it lives in
    ``core/llm/connection_health.py``; ``db/ai_connection_health.py`` only
    stores it.

    Attributes:
        connection_id: The ``ai_connections`` row this describes.
        failure_count: Counted provider failures in a row, with no success
            in between. Failed test calls on a paused connection keep adding
            to it.
        failure_kind: What the latest counted failure was.
        last_error: The latest failure's message, whitespace collapsed and
            never shortened: the Needs card shows it to the operator in full.
        suspended_at: When the connection was paused, refreshed on every
            failed test call. ``None`` while failures are still being counted
            below the threshold: the connection is not paused and every call
            goes through. A connection is "paused" exactly when this is set;
            no model call reaches the provider then, except one test call.
        retry_at: When a paused connection may make its next test call. The
            first caller that finds it due takes the test call and moves it
            forward by the longest that whole call may take (every attempt it
            makes within itself), which is the lease: nobody else sees it as
            due while the test runs, and a test caller that dies simply lets
            it expire. Every outcome of the test call then sets it again: a
            success deletes the row, a counted failure starts the next wait,
            and a call with no verdict re-arms the current wait (for a
            ``rejected`` pause, both return to ``None``); none makes it due
            at once. So a paused connection is contacted at most once
            per wait (``transient``) or once per operator "Retry now"
            (``rejected``). ``None`` means no automatic test call: the
            provider refused the request and the connection waits for the
            operator (Retry now, or a saved change to what its calls send).
        backoff_seconds: The current wait between automatic test calls of a
            ``transient`` pause; it doubles after each failed test, up to
            ``llm_connection_backoff_max_seconds``. ``None`` otherwise.
        updated_at: When the row was last written.
    """

    model_config = ConfigDict(from_attributes=True)

    connection_id: str
    failure_count: int
    failure_kind: ConnectionFailureKind
    last_error: str
    suspended_at: datetime | None = None
    retry_at: datetime | None = None
    backoff_seconds: int | None = None
    updated_at: datetime

    @property
    def is_suspended(self) -> bool:
        """Whether the connection is paused (``suspended_at`` is set)."""
        return self.suspended_at is not None
