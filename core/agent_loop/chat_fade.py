"""Pressure-gated chat fade for channel warm windows.

Today's warm window keeps the last N channel turns and then drops older
ones once ``max_allowed_history_tokens`` is full. That drop is a hard
cliff. Chat fade, once those turns no longer leave the configured chat
headroom free, queues one System AI summary of the older turns and
leaves the recent tail verbatim.

The agent turn never waits. A background thread runs the summary. If
System AI is missing, the call fails, or the stored summary does not
match the thread, the history view stays on the hard window.

Knobs are the existing compaction settings: mode, chat headroom, min
turns between runs, and cooldown. Task budget headroom is not used.
Standing prefs, desk notes, and ``/me/notes`` are not read or written.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import db
from core import config
from core.llm.client import count_tokens
from core.llm.system_completion import complete_text, system_ai_is_configured
from db.chat_fade import (
    get_channel_chat_fade,
    try_claim_chat_fade_run,
    upsert_channel_chat_fade,
)

logger = logging.getLogger(__name__)

FADE_ID_PREFIX = "chat-fade:"
_SUMMARY_NAME = "Earlier"
# The length the fade prompt asks the model for.
_SUMMARY_TARGET_CHARS = 400
# Backstop clip at 2x the target, so an ordinary overrun is kept, not cut.
_SUMMARY_MAX_CHARS = _SUMMARY_TARGET_CHARS * 2
_SOURCE_MESSAGE_CHARS = 280
_SOURCE_MESSAGE_CAP = 16
_HEADROOM_MAX = 95

_FADE_SYSTEM = (
    "Summarize older channel turns into one short factual note. "
    f"Keep it under {_SUMMARY_TARGET_CHARS} characters. "
    "Keep decisions, names, and open questions that are in the turns. "
    "Do not add tasks, preferences, or notes. "
    "Plain text only."
)

_scheduler: Callable[[Callable[[], None]], None] | None = None


def set_chat_fade_scheduler(scheduler: Callable[[Callable[[], None]], None] | None) -> None:
    """Replace the background scheduler. ``None`` restores the daemon thread."""
    global _scheduler
    _scheduler = scheduler


def note_agent_turn() -> None:
    """Count this agent turn toward the fade gap. Never raises into the turn."""
    try:
        from db.chat_fade import note_chat_fade_turn

        note_chat_fade_turn()
    except Exception:
        logger.warning("chat fade could not count an agent turn")


def apply_channel_chat_fade(
    channel_id: str,
    messages: list[dict[str, Any]],
    policy: Any,
) -> list[dict[str, Any]]:
    """Replace a faded prefix with one summary. Unusable fades return the input."""
    try:
        return _apply_channel_chat_fade(channel_id, messages, policy)
    except Exception:
        logger.warning("chat fade apply failed; keeping the hard warm window")
        return messages


def consider_channel_chat_fade(
    channel_id: str,
    messages: list[dict[str, Any]],
    policy: Any,
    *,
    agent_id: str,
    token_model: str | None = None,
) -> bool:
    """Queue a background fade when pressure and the knobs allow it.

    Returns True only when a job was queued. The job is not run here.
    """
    try:
        return _consider_channel_chat_fade(
            channel_id,
            messages,
            policy,
            agent_id=agent_id,
            token_model=token_model,
        )
    except Exception:
        logger.warning("chat fade was not queued")
        return False


def _apply_channel_chat_fade(
    channel_id: str,
    messages: list[dict[str, Any]],
    policy: Any,
) -> list[dict[str, Any]]:
    if _mode() != "pressure_only":
        return messages
    candidate = _candidate(messages, policy)
    if len(candidate) < 2:
        return messages
    fade = get_channel_chat_fade(channel_id)
    if not fade:
        return messages
    summary = _clean_summary(fade.get("summary"))
    through = str(fade.get("through_message_id") or "").strip()
    if summary is None or not through:
        return messages
    ids = [str(message.get("id") or "") for message in candidate]
    if through not in ids:
        return messages
    index = ids.index(through)
    if index >= len(candidate) - 1:
        return messages
    rest = [message for message in candidate[index + 1 :] if not _is_fade_message(message)]
    if not rest:
        return messages
    return [_summary_message(channel_id, summary), *rest]


def _consider_channel_chat_fade(
    channel_id: str,
    messages: list[dict[str, Any]],
    policy: Any,
    *,
    agent_id: str,
    token_model: str | None,
) -> bool:
    token = (channel_id or "").strip()
    if not token or _mode() != "pressure_only":
        return False
    prefix = _pressure_prefix(
        messages,
        policy,
        agent_id=agent_id,
        token_model=token_model,
    )
    if prefix is None:
        return False
    if not system_ai_is_configured():
        logger.info("chat fade skipped: system AI unavailable")
        return False
    gap = _min_turns()
    cooldown = _cooldown()
    if gap is None or cooldown is None:
        return False
    if not try_claim_chat_fade_run(min_turns=gap, cooldown=cooldown):
        return False
    message_ids = tuple(prefix["message_ids"])
    prior = prefix["prior_summary"]

    def job() -> None:
        _run_fade_job(token, message_ids, prior)

    _schedule(job)
    logger.info("chat fade queued for channel %s (%d older turns)", token, len(message_ids))
    return True


def _pressure_prefix(
    messages: list[dict[str, Any]],
    policy: Any,
    *,
    agent_id: str,
    token_model: str | None,
) -> dict[str, Any] | None:
    """Return older verbatim turns that sit above the chat headroom line."""
    budget = int(getattr(policy, "max_allowed_history_tokens", 0) or 0)
    if budget <= 0:
        return None
    headroom = _headroom_percent()
    if headroom is None:
        return None
    keep_limit = int(budget * (100 - headroom) / 100)
    if keep_limit < 1:
        keep_limit = 1
    candidate = _candidate(messages, policy)
    verbatim = [message for message in candidate if not _is_fade_message(message)]
    if len(verbatim) < 2:
        return None
    sizes = [_entry_tokens(message, agent_id, token_model) for message in verbatim]
    if sum(sizes) <= keep_limit:
        return None
    kept = 0
    keep_from = len(verbatim)
    for index in range(len(verbatim) - 1, -1, -1):
        size = sizes[index]
        if index < len(verbatim) - 1 and kept + size > keep_limit:
            break
        kept += size
        keep_from = index
    if keep_from <= 0:
        return None
    prefix = verbatim[:keep_from]
    message_ids = [str(message.get("id") or "").strip() for message in prefix]
    message_ids = [message_id for message_id in message_ids if message_id]
    if not message_ids:
        return None
    prior = None
    for message in candidate:
        if _is_fade_message(message):
            text = str(message.get("content") or "").strip()
            prior = text or None
            break
    return {"message_ids": message_ids, "prior_summary": prior}


def _run_fade_job(
    channel_id: str,
    message_ids: tuple[str, ...],
    prior_summary: str | None,
) -> None:
    try:
        if _mode() != "pressure_only" or not system_ai_is_configured():
            logger.info("chat fade skipped: pressure mode or system AI changed")
            return
        rows = []
        for message_id in message_ids:
            row = db.get_channel_message(message_id)
            if row is None or row.channel_id != channel_id:
                logger.info("chat fade skipped: transcript row missing")
                return
            rows.append(row)
        from core.floors import channel_floor_id, on_floor

        floor_id = channel_floor_id(channel_id)
        kept = []
        for row in rows:
            author_id = str(getattr(row, "author_agent_id", None) or "").strip()
            if author_id and (not floor_id or not on_floor(author_id, floor_id)):
                continue
            kept.append(row)
        rows = kept
        if not rows:
            return
        raw = complete_text(_fade_messages(rows, prior_summary))
        summary = _clean_summary(raw)
        if summary is None:
            logger.info("chat fade skipped: system AI returned no usable summary")
            return
        upsert_channel_chat_fade(
            channel_id=channel_id,
            through_message_id=rows[-1].id,
            summary=summary,
        )
    except Exception:
        logger.warning("chat fade job failed; hard warm window stays")


def _fade_messages(rows: list[Any], prior_summary: str | None) -> list[dict[str, str]]:
    chosen = list(rows)
    omitted = 0
    if len(chosen) > _SOURCE_MESSAGE_CAP:
        omitted = len(chosen) - _SOURCE_MESSAGE_CAP
        half = _SOURCE_MESSAGE_CAP // 2
        chosen = [*rows[:half], *rows[-half:]]
    lines: list[str] = []
    if prior_summary:
        lines.append(f"Earlier summary: {prior_summary[:_SUMMARY_MAX_CHARS]}")
    for row in chosen:
        name = str(getattr(row, "author_name", "") or "Unknown")
        content = " ".join(str(getattr(row, "content", "") or "").split())
        lines.append(f"{name}: {content[:_SOURCE_MESSAGE_CHARS]}")
    if omitted:
        lines.append(f"({omitted} older turns between these excerpts)")
    return [
        {"role": "system", "content": _FADE_SYSTEM},
        {"role": "user", "content": "\n".join(lines)[:4000]},
    ]


def _schedule(job: Callable[[], None]) -> None:
    if _scheduler is not None:
        _scheduler(job)
        return
    _default_schedule(job)


def _default_schedule(job: Callable[[], None]) -> None:
    def _guard() -> None:
        try:
            job()
        except Exception:
            logger.warning("chat fade job failed; hard warm window stays")

    threading.Thread(target=_guard, name="chat-fade", daemon=True).start()


def _candidate(messages: list[dict[str, Any]], policy: Any) -> list[dict[str, Any]]:
    last_n = int(getattr(policy, "last_n_histories", 0) or 0)
    if last_n <= 0:
        return []
    return list(messages[-last_n:])


def _summary_message(channel_id: str, summary: str) -> dict[str, Any]:
    return {
        "id": f"{FADE_ID_PREFIX}{channel_id}",
        "from_agent": "__system__",
        "from_name": _SUMMARY_NAME,
        "to_agent": None,
        "content": summary,
        "message_type": "channel",
        "channel_id": channel_id,
    }


def _is_fade_message(message: dict[str, Any]) -> bool:
    return str(message.get("id") or "").startswith(FADE_ID_PREFIX)


def _entry_tokens(message: dict[str, Any], agent_id: str, token_model: str | None) -> int:
    try:
        count = count_tokens(_render_entry(message, agent_id), model=token_model)
    except Exception:
        return 0
    if not isinstance(count, int) or count < 0:
        return 0
    return count


def _render_entry(message: dict[str, Any], agent_id: str) -> str:
    if message.get("from_agent") == agent_id:
        return str(message.get("content") or "")
    sender = str(message.get("from_name") or "Unknown")
    content = str(message.get("content") or "")
    return f"[{sender}]: {content}"


def _clean_summary(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    if len(text) < 8:
        return None
    if len(text) > _SUMMARY_MAX_CHARS:
        logger.warning("chat fade summary clipped: %d > %d chars", len(text), _SUMMARY_MAX_CHARS)
        text = text[:_SUMMARY_MAX_CHARS].rstrip()
    return text or None


def _mode() -> str:
    return config.get_live("compaction_mode") or ""


def _headroom_percent() -> int | None:
    raw = _knob_int("compaction_chat_budget_headroom_percent")
    if raw is None:
        return None
    if raw < 0:
        return None
    return min(raw, _HEADROOM_MAX)


def _min_turns() -> int | None:
    raw = _knob_int("compaction_min_turns_between_runs")
    if raw is None or raw < 0:
        return None
    return max(1, raw)


def _cooldown() -> timedelta | None:
    raw = _knob_int("compaction_cooldown_minutes")
    if raw is None or raw < 0:
        return None
    return timedelta(minutes=raw)


def _knob_int(key: str) -> int | None:
    raw = config.get_live(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
