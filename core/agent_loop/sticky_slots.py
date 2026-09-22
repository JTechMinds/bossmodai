"""Pressure-gated sticky slots for the work spine.

Chat fade soft-summarizes older channel turns. The warm window drops
turns that no longer fit. Sticky slots keep four typed facts from those
turns — plan, next owner, verdict path, and blockers — and inject them
into the prompt once a covered turn is in the fetched thread and missing
from the visible window.

The agent turn never waits. A background thread asks System AI. If
System AI is missing, the call fails, or the write fails, the prompt
stays on the warm window and chat fade.

Knobs are the existing compaction settings: mode, task-budget headroom,
min turns between runs, and cooldown. Chat headroom is not used. The
fill gate is separate from chat fade, and it reads the same min-turns
and cooldown knobs. Standing prefs, desk notes, personal notes, and
Soft-block are not read or written. A fill replaces only facts that
come back as text; null and omitted facts stay.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from core import config
from core.agent_loop.chat_fade import FADE_ID_PREFIX
from core.llm.client import count_tokens
from core.llm.system_completion import complete_text, system_ai_is_configured
from db.sticky_slots import (
    FACT_KEYS,
    get_sticky_slot,
    merge_sticky_slot,
    try_claim_sticky_slot_run,
)

logger = logging.getLogger(__name__)

SLOT_ID_PREFIX = "sticky-slot:"
_FACT_MAX_CHARS = 180
_FACT_MIN_CHARS = 2
_SOURCE_MESSAGE_CHARS = 280
_SOURCE_MESSAGE_CAP = 16
_FILL_MAX_TOKENS = 220
_HEADROOM_MAX = 95
_LABELS = {
    "plan": "plan",
    "next_owner": "next owner",
    "verdict_path": "verdict path",
    "blockers": "blockers",
}

_FILL_SYSTEM = (
    "Extract work-spine facts from older turns that are leaving the prompt. "
    "Return one JSON object with only these keys: "
    "plan, next_owner, verdict_path, blockers. "
    "Each value is a short factual string or null. "
    "Use null when the turns do not state that fact. Null leaves a stored fact unchanged. "
    "Do not invent facts. Do not add preferences, notes, or tasks. "
    "JSON only."
)

_scheduler: Callable[[Callable[[], None]], None] | None = None


class StickySlotFill(BaseModel):
    """One System AI fill. Unknown keys are rejected. Null does not clear."""

    model_config = ConfigDict(extra="forbid")

    plan: str | None = None
    next_owner: str | None = None
    verdict_path: str | None = None
    blockers: str | None = None

    @field_validator(*FACT_KEYS, mode="before")
    @classmethod
    def _text_or_null(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("sticky slot fact must be text or null")
        return _clean_fact(value)


def set_sticky_slot_scheduler(scheduler: Callable[[Callable[[], None]], None] | None) -> None:
    """Replace the background scheduler. ``None`` restores the daemon thread."""
    global _scheduler
    _scheduler = scheduler


def note_sticky_slot_turn() -> None:
    """Count this agent turn toward the fill gap. Never raises into the turn."""
    try:
        from db.sticky_slots import note_sticky_slot_turn as _note

        _note()
    except Exception:
        logger.warning("sticky slots could not count an agent turn")


def compose_sticky_slots(
    *,
    scope_kind: str,
    scope_id: str,
    verbatim: list[dict[str, Any]],
    visible: list[dict[str, Any]],
    policy: Any,
    agent_id: str,
    token_model: str | None = None,
) -> list[dict[str, Any]]:
    """Inject a stored pocket, then queue a fill when pressure allows it.

    Inject failures and queue failures leave ``visible`` unchanged. The
    fill is not run here.
    """
    shown = visible
    try:
        shown = _inject(
            scope_kind,
            scope_id,
            verbatim,
            visible,
            policy,
        )
    except Exception:
        logger.warning("sticky slots were not injected; keeping the warm window")
        shown = visible
    try:
        _consider(
            scope_kind,
            scope_id,
            verbatim,
            policy,
            agent_id=agent_id,
            token_model=token_model,
        )
    except Exception:
        logger.warning("sticky slots were not queued")
    return shown


def _inject(
    scope_kind: str,
    scope_id: str,
    verbatim: list[dict[str, Any]],
    visible: list[dict[str, Any]],
    policy: Any,
) -> list[dict[str, Any]]:
    if _mode() != "pressure_only":
        return visible
    if int(getattr(policy, "last_n_histories", 0) or 0) <= 0:
        return visible
    if any(_is_slot_message(message) for message in visible):
        return visible
    row = get_sticky_slot(scope_kind, scope_id)
    if not row:
        return visible
    facts = _facts_from_row(row)
    source_ids = _source_ids(row.get("source_message_ids"))
    if not facts or not source_ids:
        return visible
    verbatim_ids = _message_ids(verbatim)
    known = [source_id for source_id in source_ids if source_id in verbatim_ids]
    if not known:
        return visible
    visible_ids = _message_ids(visible)
    if all(source_id in visible_ids for source_id in known):
        return visible
    return [_slot_message(scope_kind, scope_id, facts), *visible]


def _consider(
    scope_kind: str,
    scope_id: str,
    verbatim: list[dict[str, Any]],
    policy: Any,
    *,
    agent_id: str,
    token_model: str | None,
) -> bool:
    kind = (scope_kind or "").strip()
    ident = (scope_id or "").strip()
    if not kind or not ident or _mode() != "pressure_only":
        return False
    prefix = _pressure_prefix(
        verbatim,
        policy,
        agent_id=agent_id,
        token_model=token_model,
    )
    if prefix is None:
        return False
    if not system_ai_is_configured():
        logger.info("sticky slots skipped: system AI unavailable")
        return False
    gap = _min_turns()
    cooldown = _cooldown()
    if gap is None or cooldown is None:
        return False
    if not try_claim_sticky_slot_run(min_turns=gap, cooldown=cooldown):
        return False
    shots = tuple(_snapshot(message) for message in prefix)

    def job() -> None:
        _run_fill_job(kind, ident, shots)

    _schedule(job)
    logger.info("sticky slots queued for %s %s (%d older turns)", kind, ident, len(shots))
    return True


def _pressure_prefix(
    messages: list[dict[str, Any]],
    policy: Any,
    *,
    agent_id: str,
    token_model: str | None,
) -> list[dict[str, Any]] | None:
    """Return older turns that sit above the task-headroom line."""
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
    verbatim = [message for message in candidate if not _is_synthetic(message)]
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
    if not any(str(message.get("id") or "").strip() for message in prefix):
        return None
    return prefix


def _run_fill_job(
    scope_kind: str,
    scope_id: str,
    shots: tuple[tuple[str, str, str], ...],
) -> None:
    try:
        if _mode() != "pressure_only" or not system_ai_is_configured():
            logger.info("sticky slots skipped: pressure mode or system AI changed")
            return
        if not shots:
            return
        raw = complete_text(
            _fill_messages(shots),
            max_tokens=_FILL_MAX_TOKENS,
        )
        facts = _parse_fill(raw)
        if not facts:
            logger.info("sticky slots skipped: system AI returned no usable facts")
            return
        merge_sticky_slot(
            scope_kind=scope_kind,
            scope_id=scope_id,
            facts=facts,
            source_message_ids=[shot[0] for shot in shots],
        )
    except Exception:
        logger.warning("sticky slot fill failed; warm window stays")


def _fill_messages(shots: tuple[tuple[str, str, str], ...]) -> list[dict[str, str]]:
    chosen = list(shots)
    omitted = 0
    if len(chosen) > _SOURCE_MESSAGE_CAP:
        omitted = len(chosen) - _SOURCE_MESSAGE_CAP
        half = _SOURCE_MESSAGE_CAP // 2
        chosen = [*shots[:half], *shots[-half:]]
    lines: list[str] = []
    for _message_id, name, content in chosen:
        lines.append(f"{name}: {content}")
    if omitted:
        lines.append(f"({omitted} older turns between these excerpts)")
    return [
        {"role": "system", "content": _FILL_SYSTEM},
        {"role": "user", "content": "\n".join(lines)[:4000]},
    ]


def _snapshot(message: dict[str, Any]) -> tuple[str, str, str]:
    message_id = str(message.get("id") or "").strip()
    name = str(message.get("from_name") or "Unknown")
    content = " ".join(str(message.get("content") or "").split())[:_SOURCE_MESSAGE_CHARS]
    return message_id, name, content


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
            logger.warning("sticky slot fill failed; warm window stays")

    threading.Thread(target=_guard, name="sticky-slots", daemon=True).start()


def _parse_fill(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        fill = StickySlotFill.model_validate(payload)
    except ValidationError:
        return None
    return _facts(fill)


def _facts(fill: StickySlotFill) -> dict[str, str]:
    data = fill.model_dump()
    return {
        key: data[key]
        for key in FACT_KEYS
        if isinstance(data.get(key), str) and data[key]
    }


def _facts_from_row(row: dict[str, Any]) -> dict[str, str]:
    payload = {key: row.get(key) for key in FACT_KEYS}
    try:
        fill = StickySlotFill.model_validate(payload)
    except ValidationError:
        return {}
    return _facts(fill)


def _source_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        return []
    if not isinstance(values, list):
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        token = item.strip()
        if not token or token in seen or _is_synthetic_id(token):
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _slot_message(scope_kind: str, scope_id: str, facts: dict[str, str]) -> dict[str, Any]:
    lines = [f"{_LABELS[key]}: {facts[key]}" for key in FACT_KEYS if key in facts]
    payload: dict[str, Any] = {
        "id": f"{SLOT_ID_PREFIX}{scope_kind}:{scope_id}",
        "from_agent": "__system__",
        "from_name": "Work spine",
        "to_agent": None,
        "content": "\n".join(lines),
        "message_type": "sticky_slot",
    }
    if scope_kind == "channel":
        payload["channel_id"] = scope_id
    return payload


def _is_slot_message(message: dict[str, Any]) -> bool:
    return str(message.get("id") or "").startswith(SLOT_ID_PREFIX)


def _is_synthetic(message: dict[str, Any]) -> bool:
    return _is_synthetic_id(str(message.get("id") or ""))


def _is_synthetic_id(message_id: str) -> bool:
    return message_id.startswith(SLOT_ID_PREFIX) or message_id.startswith(FADE_ID_PREFIX)


def _message_ids(messages: list[dict[str, Any]]) -> set[str]:
    return {str(message.get("id") or "") for message in messages if str(message.get("id") or "")}


def _candidate(messages: list[dict[str, Any]], policy: Any) -> list[dict[str, Any]]:
    last_n = int(getattr(policy, "last_n_histories", 0) or 0)
    if last_n <= 0:
        return []
    return list(messages[-last_n:])


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


def _clean_fact(value: str) -> str | None:
    text = " ".join(value.split())
    if len(text) < _FACT_MIN_CHARS:
        return None
    if len(text) > _FACT_MAX_CHARS:
        text = text[:_FACT_MAX_CHARS].rstrip()
    if len(text) < _FACT_MIN_CHARS:
        return None
    return text


def _mode() -> str:
    return config.get_live("compaction_mode") or ""


def _headroom_percent() -> int | None:
    raw = _knob_int("compaction_task_budget_headroom_percent")
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
