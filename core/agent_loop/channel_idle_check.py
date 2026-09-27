"""BossMod AI — Thread idle check: privately re-wake members who owe work.

Routing answers "who responds to this line". It cannot answer "does anyone
in this thread owe work they are not doing". This check runs once per
quiet period. A thread is due when its newest line is older than the
delay, it has no active round, it is not paused or held by work, and that
newest line was not already checked. Any new line re-arms it; there are no
timers to cancel. A thread quiet longer than the max age is dormant and is not judged.

Deterministic gates run before the model: only members who spoke in the
transcript window, have no open task, no open trigger, and no live turn
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
from core.agent_loop.channel_host import talk_closed
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
from core.llm.system_completion import complete_text, system_ai_is_configured
from core.tasking.transitions import is_terminal_task_status
from core.time import ensure_utc
from db import channel_idle_checks as idle_db

logger = logging.getLogger(__name__)

# Keep equal to _SEED_SETTINGS in db/settings.py.
IDLE_CHECK_ENABLED_FALLBACK = True
IDLE_CHECK_DELAY_SECONDS_FALLBACK = 45
IDLE_CHECK_INTERVAL_SECONDS_FALLBACK = 5.0
IDLE_CHECK_MAX_WAKES_FALLBACK = 2
IDLE_CHECK_MAX_AGE_MINUTES_FALLBACK = 30

IDLE_CHECK_KEYS = frozenset({"wake"})
_WAKE_ITEM_KEYS = frozenset({"member", "quote"})
_NOTE_QUOTE_CHARS = 200


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
    busy_lines = [f'{name} — working on "{title}"' for name, title in busy]
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

    Returns ``[]`` when the thread is not due (no line, closed to Talk, an
    active round, delay not elapsed, dormant past the max age, newest line
    already checked), when no
    member is an idle candidate, when System AI is unset or fails, when the
    payload is rejected or empty, or when the round cannot open. Once the
    gates up to the checked-line test pass, the newest line is recorded as
    checked so one quiet period is judged once.
    """
    latest = db.get_latest_channel_message(channel_id)
    if latest is None:
        return []
    if talk_closed(channel_id):
        return []
    if db.list_channel_response_rounds(channel_id, status="active"):
        return []
    if now - ensure_utc(latest.created_at) < timedelta(seconds=idle_check_delay_seconds()):
        return []
    if now - ensure_utc(latest.created_at) > timedelta(minutes=idle_check_max_age_minutes()):
        return []
    state = idle_db.get_channel_idle_check(channel_id)
    if state["checked_message_id"] == latest.id:
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
    members = ordered_channel_members(channel_id, set())
    woken = set(state["woken_agent_ids"])
    open_tasks = {member["id"]: _open_tasks(member["id"]) for member in members}
    candidates = [
        member
        for member in members
        if member["id"] in spoke
        and member["id"] not in woken
        and not open_tasks[member["id"]]
        and not db.has_open_trigger(member["id"])
        and not dispatcher.is_active(member["id"])
    ]
    state["checked_message_id"] = latest.id
    idle_db.save_channel_idle_check(state)
    if not candidates:
        return []
    if not system_ai_is_configured():
        logger.warning("channel idle check skipped: System AI is not configured")
        return []
    candidate_ids = {member["id"] for member in candidates}
    busy = [
        (member["name"], open_tasks[member["id"]][-1].title)
        for member in members
        if member["id"] not in candidate_ids and open_tasks[member["id"]]
    ]
    max_wakes = idle_check_max_wakes()
    messages, number_map = build_idle_check_messages(
        transcript=transcript,
        candidates=candidates,
        busy=busy,
        max_wakes=max_wakes,
    )
    raw = complete_text(messages)
    if raw is None:
        logger.warning("channel idle check fell back: no_completion")
        return []
    wakes = parse_idle_check_payload(raw, number_map, format_transcript(transcript))
    if wakes is None:
        logger.warning("channel idle check rejected payload")
        return []
    if not wakes:
        return []
    wakes = wakes[:max_wakes]
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
        idle_db.save_channel_idle_check(state)
        logger.info("channel idle check woke %s in %s", [wake.agent_id for wake in wakes], channel_id)
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


def _open_tasks(agent_id: str) -> list[Any]:
    """Non-terminal tasks assigned to ``agent_id``, oldest first."""
    return [task for task in db.list_tasks(assigned_to=agent_id) if not is_terminal_task_status(task.status)]


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _clip_quote(quote: str) -> str:
    clean = _flat(quote)
    if len(clean) <= _NOTE_QUOTE_CHARS:
        return clean
    return clean[: _NOTE_QUOTE_CHARS - 3].rstrip() + "..."


channel_idle_watch = ChannelIdleWatch()
