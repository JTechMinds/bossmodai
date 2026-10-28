"""Per-connection pause with backoff: a failing AI connection stops being called.

Every model call checks :func:`admit` immediately before it contacts a
provider and reports its outcome afterwards (:func:`record_success`,
:func:`record_failure`, or :func:`end_undecided_test` when a test call ended
with no verdict). The two callers are ``core.llm.client.completion`` and
``core.llm.system_completion.complete_text``, the only code that reaches a
provider, so "a paused connection is never called" holds for every caller.

The invariant: a paused connection is contacted at most once per wait
(``transient``) or once per operator "Retry now" (``rejected``), whatever
its test call's outcome. Only one caller can take a test call (its lease
on ``retry_at`` covers the whole call), and every outcome sets the next
``retry_at``: a success resumes the connection, a counted failure starts
the next wait (or waits for the operator), and a call with no verdict
re-arms the current wait (or returns to waiting for the operator). No
outcome makes the next test call due at once.

The state is one ``ai_connection_health`` row per connection, shared by the
app process and the runtime worker the way the call budget is. Every
read-modify-write runs inside ``db.connection.transaction()`` (``BEGIN
IMMEDIATE``), which makes it atomic across both processes.

Policy:

- ``llm_connection_failure_threshold`` counted failures in a row, with no
  success in between, pause the connection.
- A ``transient`` pause allows one automatic test call after
  ``llm_connection_backoff_seconds``. Each failed test call doubles the wait,
  up to ``llm_connection_backoff_max_seconds``.
- A ``rejected`` pause has no timer: it waits for the operator (Retry now, or
  a saved change to what the connection's calls send, which deletes the row
  in ``db.update_connection``).
- Any success deletes the row, which resumes a paused connection.

What counts as a failure, and of which kind, is decided by the caller
(``core.llm.client.classify_connection_failure``). This module does not
import the client, which imports it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import db
from core import config
from core.models import ConnectionFailureKind, ConnectionHealth
from db.connection import transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Admission:
    """Whether one model call may contact the provider now.

    Attributes:
        allowed: ``True`` when the call may go ahead.
        probe: ``True`` when the call is the single test call of a paused
            connection. Its outcome decides whether the connection resumes.
        lease_until: For a test call, the ``retry_at`` it wrote: its lease,
            which covers the whole call, so no second caller can test the
            connection while it runs. A test call that ends with no verdict
            passes it to :func:`end_undecided_test`, which re-arms the wait
            only while this lease is still in place. ``None`` otherwise.
        health: The row that denied the call, for its message; ``None`` when
            the call is allowed.
    """

    allowed: bool
    probe: bool
    lease_until: datetime | None
    health: ConnectionHealth | None


_ALLOWED = Admission(allowed=True, probe=False, lease_until=None, health=None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_probe_ready(health: ConnectionHealth, now: datetime) -> bool:
    """Return whether a paused connection may make its test call now.

    Args:
        health: The connection's health row.
        now: The current time (UTC, timezone-aware).

    Returns:
        ``True`` when the connection is paused and its ``retry_at`` is set
        and not in the future. ``False`` for a connection that is not paused,
        one waiting for the operator (``retry_at`` is ``None``), or one whose
        wait (or a running test call's lease) has not ended.
    """
    return health.is_suspended and health.retry_at is not None and health.retry_at <= now


def admit(connection_id: str, *, lease_seconds: float) -> Admission:
    """Decide whether a model call on ``connection_id`` may contact the provider.

    A healthy connection pays one indexed read and no write lock: the first
    read is outside any transaction, and only a paused connection whose test
    call is due enters one, re-reads, and takes the test call by moving
    ``retry_at`` forward by ``lease_seconds``. Only one caller can find it
    due; a caller that dies lets the lease expire.

    Args:
        connection_id: The AI connection the call is for.
        lease_seconds: The longest the whole call may take, including any
            retries it makes within itself.

    Returns:
        ``allowed=True`` for a connection that is not paused, or for the one
        caller that takes a due test call (``probe=True``, with
        ``lease_until``). Otherwise ``allowed=False`` with the row that
        denied it.

    Raises:
        sqlite3.Error: The health row could not be read or written.
    """
    health = db.get_connection_health(connection_id)
    if health is None or not health.is_suspended:
        return _ALLOWED
    if not is_probe_ready(health, _now()):
        return Admission(allowed=False, probe=False, lease_until=None, health=health)
    with transaction():
        # Re-read under the write lock: another caller may have taken the
        # test call, or a result may have resumed the connection.
        health = db.get_connection_health(connection_id)
        now = _now()
        if health is None or not health.is_suspended:
            return _ALLOWED
        if not is_probe_ready(health, now):
            return Admission(allowed=False, probe=False, lease_until=None, health=health)
        lease_until = now + timedelta(seconds=lease_seconds)
        if not db.save_connection_health(health.model_copy(update={"retry_at": lease_until})):
            # The connection was deleted: nothing is left to protect.
            return _ALLOWED
    return Admission(allowed=True, probe=True, lease_until=lease_until, health=None)


def record_success(connection_id: str) -> None:
    """Record that a model call on ``connection_id`` succeeded: its row is deleted.

    A success resets the failure count, and on a paused connection it resumes
    every caller. Logged at INFO when the connection was paused. A healthy
    connection (no row) pays one read and no write.

    Args:
        connection_id: The AI connection the call was for.

    Raises:
        sqlite3.Error: The health row could not be read or deleted.
    """
    if db.get_connection_health(connection_id) is None:
        return
    with transaction():
        previous = db.get_connection_health(connection_id)
        if previous is None:
            return
        db.delete_connection_health(connection_id)
    if previous.is_suspended:
        logger.info("AI connection %s is working again", connection_id)


def record_failure(
    connection_id: str,
    *,
    kind: ConnectionFailureKind,
    error: str,
) -> ConnectionHealth | None:
    """Count one provider failure on ``connection_id``, pausing it at the threshold.

    Recorded once per model call, after that call's own transient retries.

    - Not paused: the count goes up by one and the kind and message are this
      failure's. At ``llm_connection_failure_threshold`` the connection is
      paused: a ``transient`` pause allows a test call after
      ``llm_connection_backoff_seconds``; a ``rejected`` one waits for the
      operator (``retry_at`` is ``None``).
    - Paused (a test call failed): the count goes up and ``suspended_at`` is
      refreshed. ``transient``: the wait doubles when the previous kind was
      transient (else it starts at the first wait), up to
      ``llm_connection_backoff_max_seconds``. ``rejected``: no timer.

    Every pause and every failed test call is logged at WARNING with the
    connection, the kind and the next test time.

    Args:
        connection_id: The AI connection the call was for.
        kind: What the provider's failure said (``transient`` or ``rejected``).
        error: The failure's message. Whitespace is collapsed; it is never
            shortened, because the Needs card shows it in full.

    Returns:
        The row as saved, or ``None`` when the connection no longer exists.

    Raises:
        config.ConfigError: A pause setting is missing, not an integer, or
            below 1.
        ValueError: A paused ``transient`` row has no ``backoff_seconds`` (a
            row this module never writes).
        sqlite3.Error: The health row could not be read or written.
    """
    # require_positive_int is require_int plus a ConfigError below 1: a bad
    # value stops the recording rather than falling back to a default.
    threshold = config.require_positive_int("llm_connection_failure_threshold")
    first_wait = config.require_positive_int("llm_connection_backoff_seconds")
    longest_wait = config.require_positive_int("llm_connection_backoff_max_seconds")
    message = " ".join(error.split())
    with transaction():
        previous = db.get_connection_health(connection_id)
        now = _now()
        test_call_failed = previous is not None and previous.is_suspended
        count = (previous.failure_count if previous is not None else 0) + 1
        if test_call_failed:
            suspended_at: datetime | None = now
            backoff = _next_backoff(previous, kind, first_wait, longest_wait)
        elif count >= threshold:
            suspended_at = now
            backoff = first_wait if kind == "transient" else None
        else:
            suspended_at = None
            backoff = None
        retry_at = now + timedelta(seconds=backoff) if backoff is not None else None
        saved = db.save_connection_health(
            ConnectionHealth(
                connection_id=connection_id,
                failure_count=count,
                failure_kind=kind,
                last_error=message,
                suspended_at=suspended_at,
                retry_at=retry_at,
                backoff_seconds=backoff,
                updated_at=now,
            )
        )
        if not saved:
            return None
        health = db.get_connection_health(connection_id)
    if suspended_at is not None:
        logger.warning(
            "AI connection %s %s after %d failed model call(s) (%s); next test call: %s",
            connection_id,
            "failed its test call" if test_call_failed else "is paused",
            count,
            kind,
            retry_at.isoformat() if retry_at is not None else "waiting for the operator",
        )
    return health


def _next_backoff(
    previous: ConnectionHealth,
    kind: ConnectionFailureKind,
    first_wait: int,
    longest_wait: int,
) -> int | None:
    """Return the wait after a failed test call, or ``None`` for a ``rejected`` one."""
    if kind == "rejected":
        return None
    if previous.failure_kind != "transient":
        return first_wait
    if previous.backoff_seconds is None:
        raise ValueError(
            f"AI connection {previous.connection_id} is paused for transient errors with no backoff"
        )
    return min(previous.backoff_seconds * 2, longest_wait)


def end_undecided_test(connection_id: str, *, lease_until: datetime) -> None:
    """End a test call that gave no verdict: the connection goes back to waiting.

    For a test call that timed out, stalled, got an empty reply, was refused
    for that one request only (for example a context-window error), or was
    cancelled. None of those says anything about the connection, so it is
    neither resumed nor counted against. But the call did contact the
    provider, so the next test call is never made due at once: a turn that
    keeps failing this way would otherwise be claimed again as the next
    test straight away and call the provider in a loop.

    - ``transient``: ``retry_at = now + backoff_seconds``, the current wait,
      not doubled.
    - ``rejected``: ``retry_at = None``, back to waiting for the operator.

    A compare-and-set: it acts only while the row is still paused with
    ``retry_at == lease_until``. Otherwise a result or the operator already
    moved it, and nothing changes.

    Args:
        connection_id: The AI connection the test call was for.
        lease_until: The ``Admission.lease_until`` the test call was given.

    Raises:
        ValueError: A paused ``transient`` row has no ``backoff_seconds`` (a
            row this module never writes).
        sqlite3.Error: The health row could not be read or written.
    """
    with transaction():
        health = db.get_connection_health(connection_id)
        if health is None or not health.is_suspended or health.retry_at != lease_until:
            return
        # rejected: no timer, so the connection waits for the operator again.
        retry_at: datetime | None = None
        if health.failure_kind == "transient":
            if health.backoff_seconds is None:
                raise ValueError(
                    f"AI connection {connection_id} is paused for transient errors with no backoff"
                )
            retry_at = _now() + timedelta(seconds=health.backoff_seconds)
        db.save_connection_health(health.model_copy(update={"retry_at": retry_at}))


def retry_now(connection_id: str) -> bool:
    """Let a paused connection make one test call now: the operator's "Retry now".

    Args:
        connection_id: The AI connection to retry.

    Returns:
        ``True`` when the connection was paused and its test call is now due;
        ``False`` when it was not paused (nothing changed).

    Raises:
        sqlite3.Error: The health row could not be read or written.
    """
    with transaction():
        health = db.get_connection_health(connection_id)
        if health is None or not health.is_suspended:
            return False
        return db.save_connection_health(health.model_copy(update={"retry_at": _now()}))


def suspended_connections() -> dict[str, ConnectionHealth]:
    """Return every paused connection's row, keyed by connection id, in one query."""
    return {health.connection_id: health for health in db.list_suspended_connection_health()}


def is_suspended(connection_id: str | None) -> bool:
    """Return whether ``connection_id`` names a paused connection; ``None`` is never paused."""
    if connection_id is None:
        return False
    health = db.get_connection_health(connection_id)
    return health is not None and health.is_suspended
