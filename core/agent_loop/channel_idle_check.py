"""BossMod AI — Thread idle check: privately re-wake members who owe work.

Routing answers "who responds to this line". It cannot answer "does anyone
in this thread owe work they are not doing". This check runs once per
quiet period. A thread is due when its newest line is older than the
delay, nothing is in flight for it (no active round, no queued or claimed
trigger aimed at it, no live work on a task that reports to it), it is not
paused, and that newest line was not already checked. Any
new line re-arms it; there are no timers to cancel. A thread quiet longer
than the max age is dormant and is not judged.

A quiet period is recorded as checked only on a definitive result: no
candidate, System AI unset, or a parsed judge payload. No completion or a
rejected payload is a failed attempt, retried on a later scan up to the
attempts setting. A full model-call budget is not an attempt. After a
slow judge call the thread is re-read before anyone is woken.

Deterministic gates run before the model: only members who spoke in the
transcript window, have no task being worked on (accepted/active, or a live
work activity), no open trigger, and no live turn
are candidates. No candidate is no System AI call. The judge must quote
the transcript verbatim for every wake, and one bad quote rejects the
whole payload. Each member is woken at most once per human snapshot.

A wake is a private channel round (``channel_rounds.open_idle_check_round``)
whose current message is a system note quoting the member's own words.
Nothing is posted to the thread unless the woken agent acts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.channel_host import is_thread_paused
from core.agent_loop.channel_rounds import (
    open_idle_check_round,
    ordered_channel_members,
    router_transcript,
)
from core.agent_loop.channel_router import (
    RouterLine,
    format_member_line,
    format_transcript,
    unwrap_json,
)
from core.agent_loop.dispatcher import dispatcher
from core.llm.call_budget import budget, max_concurrent_model_calls
from core.llm.system_completion import complete_text, system_ai_is_configured
from core.time import ensure_utc
from db import channel_idle_checks as idle_db

logger = logging.getLogger(__name__)

# Keep equal to _SEED_SETTINGS in db/settings.py.
IDLE_CHECK_ENABLED_FALLBACK = True
IDLE_CHECK_DELAY_SECONDS_FALLBACK = 45
IDLE_CHECK_INTERVAL_SECONDS_FALLBACK = 5.0
IDLE_CHECK_MAX_WAKES_FALLBACK = 2
IDLE_CHECK_MAX_AGE_MINUTES_FALLBACK = 30
IDLE_CHECK_MAX_ATTEMPTS_FALLBACK = 3

IDLE_CHECK_KEYS = frozenset({"wake"})
_WAKE_ITEM_KEYS = frozenset({"member", "quote"})
_NOTE_QUOTE_CHARS = 200
# Busy means working now. Waiting, stalled, blocked, pending and delegated
# tasks are open but not being worked on; agents carry them for days.
WORKING_TASK_STATUSES = frozenset({"accepted", "active"})


def idle_check_enabled() -> bool:
    """Return whether the idle check runs. A missing key uses the seeded ``true``."""
    value = config.get("channel_idle_check_enabled")
    if value is None:
        return IDLE_CHECK_ENABLED_FALLBACK
    return value == "true"


def idle_check_delay_seconds() -> int:
    """Return how long a thread must be silent. Missing or negative uses the seed (45)."""
    value = config.get_int("channel_idle_check_delay_seconds")
    if value is None or value < 0:
        return IDLE_CHECK_DELAY_SECONDS_FALLBACK
    return value


def idle_check_interval_seconds() -> float:
    """Return the worker scan interval. Missing or non-positive uses the seed (5)."""
    value = config.get_float("channel_idle_check_interval_seconds")
    if value is None or value <= 0:
        return IDLE_CHECK_INTERVAL_SECONDS_FALLBACK
    return value


def idle_check_max_wakes() -> int:
    """Return the cap on members one check wakes, at least 1. Missing uses the seed (2)."""
    value = config.get_int("channel_idle_check_max_wakes")
    if value is None:
        return IDLE_CHECK_MAX_WAKES_FALLBACK
    return max(value, 1)


def idle_check_max_age_minutes() -> int:
    """Return how long a quiet thread stays eligible. Missing or below 1 uses the seed (30)."""
    value = config.get_int("channel_idle_check_max_age_minutes")
    if value is None or value < 1:
        return IDLE_CHECK_MAX_AGE_MINUTES_FALLBACK
    return value


def idle_check_max_attempts() -> int:
    """Return failed judge attempts allowed per quiet period. Missing or below 1 uses the seed (3)."""
    value = config.get_int("channel_idle_check_max_attempts")
    if value is None or value < 1:
        return IDLE_CHECK_MAX_ATTEMPTS_FALLBACK
    return value


@dataclass(frozen=True, slots=True)
class IdleWake:
    """One member the judge says owes work, with the transcript words that show it."""

    agent_id: str
    quote: str


def build_idle_check_messages(
    *,
    transcript: list[RouterLine],
    candidates: list[dict[str, str]],
    busy: list[tuple[str, str]],
    max_wakes: int,
) -> tuple[list[dict[str, str]], dict[int, str]]:
    """Build the idle-check prompt and its per-call candidate number map.

    Candidates are numbered ``1…N`` with the router's member line, so no
    agent id reaches the prompt. ``busy`` is ``(name, task_title)`` pairs
    for members working now; an empty title renders ``Name — working``.
    for members already on live work.

    Returns:
        ``(messages, number_map)``. ``number_map`` maps each number to its
        agent id and is only valid for this one completion; pass it to
        :func:`parse_idle_check_payload`.
    """
    number_map: dict[int, str] = {}
    candidate_lines: list[str] = []
    for member in candidates:
        agent_id = str(member.get("id") or "").strip()
        if not agent_id or agent_id in number_map.values():
            continue
        number = len(number_map) + 1
        number_map[number] = agent_id
        candidate_lines.append(format_member_line(member, number))
    busy_lines = [f'{name} — working on "{title}"' if title else f"{name} — working" for name, title in busy]
    user = "\n".join(
        [
            "Recent thread (oldest first):",
            format_transcript(transcript),
            "",
            "Idle candidates:",
            "\n".join(candidate_lines) or "(none)",
            "",
            "Busy members:",
            "\n".join(busy_lines) or "(none)",
        ]
    )
    system = (
        "You check a quiet team thread for owed work. Idle candidates spoke in this thread "
        "and have no task running. Wake a candidate only when the thread shows they committed "
        "to do something, or were given the go-ahead for something they proposed, and it is "
        "neither done nor started. Do not wake for: waiting on someone else, an open question "
        "to the operator, thanks or acknowledgements, work another member took, or work a "
        "status line shows as Accepted, Writing or Done. Reply with only one JSON object: "
        '{"wake": [{"member": <number>, "quote": "<exact words from Recent thread>"}]}. '
        "quote is copied verbatim from a Recent thread line that shows the commitment or the "
        f"go-ahead. At most {max_wakes} entries. An empty wake array means nobody owes work."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return messages, number_map


def parse_idle_check_payload(
    raw: str,
    number_map: dict[int, str],
    transcript_text: str,
) -> list[IdleWake] | None:
    """Parse a fail-closed ``{"wake": [{"member", "quote"}]}`` object.

    Args:
        raw: Completion text. One code fence around the JSON is unwrapped.
        number_map: The per-call number to agent id map from
            :func:`build_idle_check_messages`.
        transcript_text: The Recent thread text the judge was shown.

    Returns:
        The wakes in the model's order, or ``None`` when the payload is
        rejected: not JSON, not an object, keys other than ``wake``, a
        non-list, an item that is not exactly ``{member, quote}``, a
        non-integer (``bool`` included) or unknown member, a duplicate
        member, an empty quote, or any quote that is not in the transcript
        after whitespace is normalized.
    """
    try:
        payload = json.loads(unwrap_json(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or set(payload) != IDLE_CHECK_KEYS:
        return None
    items = payload.get("wake")
    if not isinstance(items, list):
        return None
    haystack = _flat(transcript_text)
    wakes: list[IdleWake] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != _WAKE_ITEM_KEYS:
            return None
        member = item.get("member")
        # bool is an int subclass; true/false is not a member number.
        if isinstance(member, bool) or not isinstance(member, int):
            return None
        if member not in number_map or member in seen:
            return None
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            return None
        if _flat(quote) not in haystack:
            return None
        seen.add(member)
        wakes.append(IdleWake(agent_id=number_map[member], quote=quote.strip()))
    return wakes


def check_channel(channel_id: str, *, now: datetime) -> list[dict[str, Any]]:
    """Judge one quiet thread and return the private wake triggers to enqueue.

    Returns ``[]`` with nothing written when the thread is not due: no line,
    paused, something in flight for it (see :func:`_thread_in_flight`),
    delay not elapsed, dormant past the max age, newest line already checked,
    or no free model-call lane.

    The newest line is recorded as checked on a definitive result: no
    member is an idle candidate (spoke in the window, not already woken, no
    task being worked on (accepted/active, or a live work activity), no open
    trigger, not mid-turn), System AI is unset, or the judge's payload
    parsed. No completion or a rejected payload is a failed attempt; after
    ``idle_check_max_attempts()`` of them on the same newest line it is
    recorded as checked with one warning.

    After the judge returns wakes the thread is re-read. A newer line or
    new in-flight work returns ``[]`` without recording, so the new state is
    judged on its own. A woken member who started working, got a trigger, or
    is mid-turn is dropped.
    """
    # Cheapest gates first: a dormant thread costs one read, an already
    # checked one two, before any member or in-flight work is looked at.
    latest = db.get_latest_channel_message(channel_id)
    if latest is None:
        return []
    quiet_for = now - ensure_utc(latest.created_at)
    if quiet_for > timedelta(minutes=idle_check_max_age_minutes()):
        return []
    if quiet_for < timedelta(seconds=idle_check_delay_seconds()):
        return []
    state = idle_db.get_channel_idle_check(channel_id)
    if state["checked_message_id"] == latest.id:
        return []
    if is_thread_paused(channel_id):
        return []
    members = ordered_channel_members(channel_id, set())
    member_ids = [member["id"] for member in members]
    if _thread_in_flight(channel_id, member_ids):
        return []
    human_id = ""
    for row in reversed(db.list_channel_messages(channel_id, limit=80)):
        if row.author_type == "human":
            human_id = row.id
            break
    if human_id != state["human_message_id"]:
        state["woken_agent_ids"] = []
        state["human_message_id"] = human_id
    transcript = router_transcript(channel_id, exclude_message_id=None)
    spoke = {line.author_agent_id for line in transcript if not line.status and line.author_agent_id}
    woken = set(state["woken_agent_ids"])
    candidates = [
        member
        for member in members
        if member["id"] in spoke
        and member["id"] not in woken
        and not _is_working(member["id"])
        and not db.has_open_trigger(member["id"])
        and not dispatcher.is_active(member["id"])
    ]
    if not candidates:
        _record_checked(state, latest.id)
        return []
    if not system_ai_is_configured():
        _record_checked(state, latest.id)
        logger.warning("channel idle check skipped: System AI is not configured")
        return []
    candidate_ids = {member["id"] for member in candidates}
    busy = [
        (member["name"], _busy_title(member["id"]))
        for member in members
        if member["id"] not in candidate_ids and _is_working(member["id"])
    ]
    max_wakes = idle_check_max_wakes()
    messages, number_map = build_idle_check_messages(
        transcript=transcript,
        candidates=candidates,
        busy=busy,
        max_wakes=max_wakes,
    )
    # No free lane is not an attempt: the thread is re-evaluated next scan.
    if budget.inflight() >= max_concurrent_model_calls():
        return []
    raw = complete_text(messages)
    wakes = None if raw is None else parse_idle_check_payload(raw, number_map, format_transcript(transcript))
    if wakes is None:
        _record_failed_attempt(state, latest.id, "no_completion" if raw is None else "rejected payload")
        return []
    if not wakes:
        _record_checked(state, latest.id)
        return []
    wakes = wakes[:max_wakes]
    # The judge call can be slow. Act on the thread as it is now.
    current = db.get_latest_channel_message(channel_id)
    if current is None or current.id != latest.id or _thread_in_flight(channel_id, member_ids):
        return []
    wakes = [
        wake
        for wake in wakes
        if not _is_working(wake.agent_id)
        and not db.has_open_trigger(wake.agent_id)
        and not dispatcher.is_active(wake.agent_id)
    ]
    if not wakes:
        _record_checked(state, latest.id)
        return []
    names = {member["id"]: member["name"] for member in candidates}
    note = (
        "Thread check: "
        + "; ".join(f'{names[wake.agent_id]}, you said "{_clip_quote(wake.quote)}"' for wake in wakes)
        + ". Nothing is running for you. If this is yours, start it now; if not, reply in the thread with why."
    )
    triggers = open_idle_check_round(
        channel_id=channel_id,
        source_message_id=latest.id,
        agent_ids=[wake.agent_id for wake in wakes],
        note=note,
    )
    if triggers:
        state["woken_agent_ids"] = list(state["woken_agent_ids"]) + [wake.agent_id for wake in wakes]
        logger.info("channel idle check woke %s in %s", [wake.agent_id for wake in wakes], channel_id)
    _record_checked(state, latest.id)
    return triggers


def scan_idle_channels(now: datetime) -> list[dict[str, Any]]:
    """Run :func:`check_channel` on every active thread and return all triggers.

    Disabled returns ``[]`` without reading any thread. A failure on one
    thread is logged with its traceback and that thread is skipped, so one
    bad thread cannot stop the others.
    """
    if not idle_check_enabled():
        return []
    triggers: list[dict[str, Any]] = []
    for channel in db.list_channels(status="active"):
        try:
            triggers.extend(check_channel(channel.id, now=now))
        except Exception:
            logger.exception("channel idle check failed for %s", channel.id)
    return triggers


class ChannelIdleWatch:
    """Scans threads for quiet periods and enqueues idle-check wakes."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the scan loop. A second call while running does nothing."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Channel idle watch started")

    async def stop(self) -> None:
        """Stop the scan loop and wait for it to exit."""
        self._running = False
        loop_task = self._task
        self._task = None
        if loop_task:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task
        logger.info("Channel idle watch stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                config.refresh_if_changed()
                # complete_text blocks; keep it off the worker's event loop.
                triggers = await asyncio.to_thread(scan_idle_channels, datetime.now(timezone.utc))
                for trigger in triggers:
                    dispatcher.enqueue_trigger(
                        agent_id=trigger["agent_id"],
                        trigger_type=trigger["trigger_type"],
                        source_channel=trigger["source_channel"],
                        payload=trigger["payload"],
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Channel idle watch loop error")
            await asyncio.sleep(idle_check_interval_seconds())


def _thread_in_flight(channel_id: str, member_ids: list[str]) -> bool:
    """Whether anything is still in flight for this thread.

    True when the thread has an active response round, any queued or
    claimed trigger aimed at it, or a member with a live work activity on a
    task that reports to it (that work will post its outcome here). Work on
    other threads' tasks does not count.
    """
    if db.list_channel_response_rounds(channel_id, status="active"):
        return True
    if db.channel_has_open_trigger(channel_id):
        return True
    # One read for every member's newest active activity; only work counts,
    # as in activity_runtime.get_active_work_activity.
    for activity in db.get_active_activities(member_ids).values():
        if activity.kind != "work" or not activity.task_id:
            continue
        task = db.get_task(activity.task_id)
        if task is not None and task.notification_channel_id == channel_id:
            return True
    return False


def _record_checked(state: dict[str, Any], message_id: str) -> None:
    """Mark ``message_id`` judged and clear any failed-attempt count."""
    state["checked_message_id"] = message_id
    state["failed_message_id"] = ""
    state["failed_attempts"] = 0
    idle_db.save_channel_idle_check(state)


def _record_failed_attempt(state: dict[str, Any], message_id: str, reason: str) -> None:
    """Count one failed judge attempt; give up (record checked) at the attempts limit."""
    if state["failed_message_id"] == message_id:
        attempts = int(state["failed_attempts"]) + 1
    else:
        attempts = 1
    limit = idle_check_max_attempts()
    if attempts >= limit:
        _record_checked(state, message_id)
        logger.warning("channel idle check gave up after %s attempts (%s)", attempts, reason)
        return
    state["failed_message_id"] = message_id
    state["failed_attempts"] = attempts
    idle_db.save_channel_idle_check(state)
    logger.info("channel idle check attempt %s of %s failed (%s)", attempts, limit, reason)


def _working_tasks(agent_id: str) -> list[Any]:
    """Tasks assigned to ``agent_id`` that are being worked on (accepted/active), oldest first."""
    return [task for task in db.list_tasks(assigned_to=agent_id) if task.status in WORKING_TASK_STATUSES]


def _is_working(agent_id: str) -> bool:
    """Whether ``agent_id`` is working now: a live work activity or an accepted/active task."""
    return activity_runtime.get_active_work_activity(agent_id) is not None or bool(_working_tasks(agent_id))


def _busy_title(agent_id: str) -> str:
    """The newest working task's title, else the live work activity's title, else empty."""
    working = _working_tasks(agent_id)
    if working:
        return working[-1].title
    activity = activity_runtime.get_active_work_activity(agent_id)
    return str(activity.title or "") if activity is not None else ""


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _clip_quote(quote: str) -> str:
    clean = _flat(quote)
    if len(clean) <= _NOTE_QUOTE_CHARS:
        return clean
    return clean[: _NOTE_QUOTE_CHARS - 3].rstrip() + "..."


channel_idle_watch = ChannelIdleWatch()
