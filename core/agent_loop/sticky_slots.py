"""Pressure-gated task-side sticky slots.

Slots are keyed by source ids that already exist: the task id for the
plan, an existing owner id for the next owner, an existing deliverable
path for the verdict, and an existing blocker event id for an open
condition. System AI does not create board cards, owners, paths, or
blocker events.

Chat fade still soft-summarizes older channel turns. When a task-linked
turn falls off that window, or off the task thread's warm window, the
stored slots inject. An open blocker stays on the prompt from the event
or status note already on the task. A fill does not insert or replace
that blockers row, so a clearing reply cannot hide the condition.

The agent turn never waits. A background thread asks System AI. If
System AI is missing, the reply is unusable, or the write fails, stored
rows stay and the prompt keeps the warm window plus chat fade. Standing
prefs, desk notes, personal notes, and Soft-block are not read or written.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

import db
from core import config
from core.agent_loop.chat_fade import FADE_ID_PREFIX
from core.llm.client import count_tokens
from core.llm.system_completion import complete_text, system_ai_is_configured
from db.sticky_slots import (
    SLOT_KINDS,
    list_sticky_slots,
    try_claim_sticky_slot_run,
    upsert_sticky_slots,
)

logger = logging.getLogger(__name__)

SLOT_ID_PREFIX = "sticky-slot:"
SlotKind = Literal["plan", "next_owner", "verdict_path", "blockers"]
_OPEN_STATUSES = frozenset({"blocked", "stalled", "waiting"})
_FACT_MAX_CHARS = 180
_FACT_MIN_CHARS = 2
_SOURCE_ID_MAX = 300
_SOURCE_MESSAGE_CHARS = 280
_SOURCE_MESSAGE_CAP = 16
_FILL_MAX_TOKENS = 280
_HEADROOM_MAX = 95
_KIND_ORDER = {kind: index for index, kind in enumerate(SLOT_KINDS)}
_LABELS = {
    "plan": "plan",
    "next_owner": "next owner",
    "verdict_path": "verdict path",
    "blockers": "blockers",
}

_FILL_SYSTEM = (
    "Extract task-side sticky slots from the task record. "
    'Return one JSON object {"slots":[{"source_id":"string","slot_kind":"plan|next_owner|verdict_path|blockers","body":"string"}]}. '
    "Use only source ids and kinds from the allowed list. "
    "Do not invent tasks, owners, paths, or blockers. "
    "Omit a slot rather than clearing it. "
    "JSON only."
)

_scheduler: Callable[[Callable[[], None]], None] | None = None


class StickySlotItem(BaseModel):
    """One returned fact. The source id must be one the caller already listed."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    slot_kind: SlotKind
    body: str

    @field_validator("source_id")
    @classmethod
    def _source(cls, value: str) -> str:
        token = value.strip()
        if not token or len(token) > _SOURCE_ID_MAX or any(char in token for char in ("\n", "\r", "\x00")):
            raise ValueError("sticky slot source id is invalid")
        return token

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        text = _clean_fact(value)
        if text is None:
            raise ValueError("sticky slot body is empty")
        return text


class StickySlotFill(BaseModel):
    """The only System AI shape. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    slots: list[StickySlotItem]


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


def compose_task_sticky_slots(
    *,
    task_id: str,
    verbatim: list[dict[str, Any]],
    visible: list[dict[str, Any]],
    policy: Any,
    agent_id: str,
    token_model: str | None = None,
) -> list[dict[str, Any]]:
    """Inject task slots when task-thread turns fell off, then maybe queue a fill."""
    shown = visible
    try:
        if _turns_fell_off(verbatim, visible):
            shown = _inject_tasks([task_id], visible, policy, visible_ids=_message_ids(visible))
    except Exception:
        logger.warning("sticky slots were not injected; keeping the warm window")
        shown = visible
    try:
        _consider_task(
            task_id,
            verbatim,
            policy,
            agent_id=agent_id,
            token_model=token_model,
        )
    except Exception:
        logger.warning("sticky slots were not queued")
    return shown


def compose_channel_sticky_slots(
    *,
    verbatim: list[dict[str, Any]],
    visible: list[dict[str, Any]],
    policy: Any,
    agent_id: str,
    token_model: str | None = None,
) -> list[dict[str, Any]]:
    """Inject slots for task-linked channel turns that fade or the window dropped.

    Messages with no task id do not create slots. Chat fade itself is unchanged.
    """
    shown = visible
    try:
        task_ids = _task_ids_on(_dropped_messages(verbatim, visible))
        shown = _inject_tasks(task_ids, visible, policy, visible_ids=_message_ids(visible))
    except Exception:
        logger.warning("sticky slots were not injected; keeping the warm window")
        shown = visible
    try:
        _consider_channel(
            verbatim,
            policy,
            agent_id=agent_id,
            token_model=token_model,
        )
    except Exception:
        logger.warning("sticky slots were not queued")
    return shown


def _inject_tasks(
    task_ids: list[str],
    visible: list[dict[str, Any]],
    policy: Any,
    *,
    visible_ids: set[str],
) -> list[dict[str, Any]]:
    if _mode() != "pressure_only":
        return visible
    if int(getattr(policy, "last_n_histories", 0) or 0) <= 0:
        return visible
    if any(_is_slot_message(message) for message in visible):
        return visible
    ordered = _unique(task_ids)
    if not ordered:
        return visible
    lines: list[str] = []
    open_lines: list[str] = []
    other_lines: list[str] = []
    for task_id in ordered:
        task = db.get_task(task_id)
        if task is None:
            continue
        catalog = _allowed_sources(task)
        stored = {
            (str(row.get("source_id") or ""), str(row.get("slot_kind") or "")): str(row.get("body") or "")
            for row in list_sticky_slots(list(catalog))
        }
        for source_id, kinds in sorted(catalog.items(), key=lambda item: item[0]):
            if source_id in visible_ids:
                continue
            for kind in sorted(kinds, key=lambda item: _KIND_ORDER.get(item, 99)):
                body = stored.get((source_id, kind), "").strip()
                if not body:
                    continue
                line = f"{_LABELS[kind]} ({source_id}): {body}"
                if kind == "blockers":
                    open_lines.append(line)
                else:
                    other_lines.append(line)
        for event_id, content in _open_blocker_events(task):
            if event_id in visible_ids or (event_id, "blockers") in stored:
                continue
            text = _clean_fact(content)
            if text is None:
                continue
            open_lines.append(f"blockers ({event_id}): {text}")
        task_token = str(task.id)
        if (
            "blockers" in catalog.get(task_token, set())
            and (task_token, "blockers") not in stored
            and task_token not in visible_ids
        ):
            note = _clean_fact(str(getattr(task, "status_note", "") or ""))
            if note is not None:
                open_lines.append(f"blockers ({task_token}): {note}")
    lines.extend(open_lines)
    room = max(0, 12 - len(lines))
    lines.extend(other_lines[:room])
    if not lines:
        return visible
    return [_slot_message(ordered, lines), *visible]


def _consider_task(
    task_id: str,
    messages: list[dict[str, Any]],
    policy: Any,
    *,
    agent_id: str,
    token_model: str | None,
) -> bool:
    token = (task_id or "").strip()
    if not token or db.get_task(token) is None:
        return False
    prefix = _pressure_prefix(messages, policy, agent_id=agent_id, token_model=token_model)
    if prefix is None:
        return False
    return _queue((token,))


def _consider_channel(
    messages: list[dict[str, Any]],
    policy: Any,
    *,
    agent_id: str,
    token_model: str | None,
) -> bool:
    prefix = _pressure_prefix(messages, policy, agent_id=agent_id, token_model=token_model)
    if prefix is None:
        return False
    task_ids = _task_ids_on(prefix)
    if not task_ids:
        return False
    return _queue(tuple(task_ids))


def _queue(task_ids: tuple[str, ...]) -> bool:
    if _mode() != "pressure_only":
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

    def job() -> None:
        _run_fill_job(task_ids)

    _schedule(job)
    logger.info("sticky slots queued for %d task(s)", len(task_ids))
    return True


def _run_fill_job(task_ids: tuple[str, ...]) -> None:
    try:
        if _mode() != "pressure_only" or not system_ai_is_configured():
            logger.info("sticky slots skipped: pressure mode or system AI changed")
            return
        allowed: dict[str, set[str]] = {}
        lines: list[str] = []
        preserve: set[str] = set()
        for task_id in task_ids:
            task = db.get_task(task_id)
            if task is None:
                continue
            catalog = _allowed_sources(task)
            for source_id, kinds in catalog.items():
                allowed.setdefault(source_id, set()).update(kinds)
            lines.extend(_task_lines(task))
            if task.status in _OPEN_STATUSES:
                preserve.update(event_id for event_id, _content in _open_blocker_events(task))
                if "blockers" in catalog.get(task.id, set()):
                    preserve.add(task.id)
        if not allowed:
            return
        raw = complete_text(_fill_messages(allowed, lines), max_tokens=_FILL_MAX_TOKENS)
        slots = [
            slot
            for slot in _accepted_slots(raw, allowed)
            if not (slot["slot_kind"] == "blockers" and slot["source_id"] in preserve)
        ]
        if not slots:
            logger.info("sticky slots skipped: system AI returned no usable facts")
            return
        upsert_sticky_slots(slots, preserve_source_ids=preserve)
    except Exception:
        logger.warning("sticky slot fill failed; existing slots stay")


def _allowed_sources(task: Any) -> dict[str, set[str]]:
    """Source ids already on this task. Nothing new is added."""
    allowed: dict[str, set[str]] = {str(task.id): {"plan"}}
    from core.floors import on_floor, task_floor_id

    anchor = task_floor_id(task)
    for agent_id in (getattr(task, "owner_id", None), getattr(task, "assigned_to", None)):
        token = str(agent_id or "").strip()
        if token and anchor and on_floor(token, anchor) and db.get_agent(token) is not None:
            allowed.setdefault(token, set()).add("next_owner")
    contract = getattr(task, "work_contract", None)
    deliverables = getattr(contract, "deliverables", None) or []
    for item in deliverables:
        path = str(getattr(item, "path", "") or "").strip()
        if path and len(path) <= _SOURCE_ID_MAX:
            allowed.setdefault(path, set()).add("verdict_path")
    if task.status in _OPEN_STATUSES:
        blockers = _open_blocker_events(task)
        for event_id, _content in blockers:
            allowed.setdefault(event_id, set()).add("blockers")
        if not blockers and str(getattr(task, "status_note", "") or "").strip():
            allowed[str(task.id)].add("blockers")
    return allowed


def _open_blocker_events(task: Any) -> list[tuple[str, str]]:
    if getattr(task, "status", None) not in _OPEN_STATUSES:
        return []
    events = db.list_task_events(str(task.id), limit=40)
    found: list[tuple[str, str]] = []
    for event in events:
        if getattr(event, "event_type", None) != "blocker":
            continue
        event_id = str(getattr(event, "id", "") or "").strip()
        content = str(getattr(event, "content", "") or "")
        if event_id:
            found.append((event_id, content))
    return found


def _task_lines(task: Any) -> list[str]:
    title = " ".join(str(getattr(task, "title", "") or "").split())
    lines = [f"task {task.id} status={task.status} title={title[:_SOURCE_MESSAGE_CHARS]}"]
    note = " ".join(str(getattr(task, "status_note", "") or "").split())
    if note:
        lines.append(f"status note: {note[:_SOURCE_MESSAGE_CHARS]}")
    events = db.list_task_events(str(task.id), limit=40)
    for event in events:
        name = str(getattr(event, "author_name", "") or "Unknown")
        content = " ".join(str(getattr(event, "content", "") or "").split())
        lines.append(f"{name}: {content[:_SOURCE_MESSAGE_CHARS]}")
    return lines


def _fill_messages(allowed: dict[str, set[str]], lines: list[str]) -> list[dict[str, str]]:
    catalog = [
        f"{source_id} slot_kind={kind}"
        for source_id in sorted(allowed)
        for kind in sorted(allowed[source_id], key=lambda item: _KIND_ORDER.get(item, 99))
    ]
    chosen = list(lines)
    omitted = 0
    if len(chosen) > _SOURCE_MESSAGE_CAP:
        omitted = len(chosen) - _SOURCE_MESSAGE_CAP
        half = _SOURCE_MESSAGE_CAP // 2
        chosen = [*lines[:half], *lines[-half:]]
    body = ["Allowed:", *catalog, "Task record:", *chosen]
    if omitted:
        body.append(f"({omitted} older task lines between these excerpts)")
    return [
        {"role": "system", "content": _FILL_SYSTEM},
        {"role": "user", "content": "\n".join(body)[:4000]},
    ]


def _accepted_slots(raw: Any, allowed: dict[str, set[str]]) -> list[dict[str, str]]:
    fill = _parse_fill(raw)
    if fill is None:
        return []
    accepted: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in fill.slots:
        kinds = allowed.get(item.source_id)
        if not kinds or item.slot_kind not in kinds:
            continue
        key = (item.source_id, item.slot_kind)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(
            {"source_id": item.source_id, "slot_kind": item.slot_kind, "body": item.body}
        )
    return accepted


def _parse_fill(raw: Any) -> StickySlotFill | None:
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
        return StickySlotFill.model_validate(payload)
    except ValidationError:
        return None


def _pressure_prefix(
    messages: list[dict[str, Any]],
    policy: Any,
    *,
    agent_id: str,
    token_model: str | None,
) -> list[dict[str, Any]] | None:
    """Return older turns that sit above the task-headroom line."""
    if _mode() != "pressure_only":
        return None
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
    if not prefix:
        return None
    return prefix


def _task_ids_on(messages: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for message in messages:
        message_id = str(message.get("id") or "").strip()
        if not message_id or _is_synthetic_id(message_id):
            continue
        row = db.get_channel_message(message_id)
        task_id = str(getattr(row, "task_id", "") or "").strip() if row is not None else ""
        if not task_id or task_id in seen or db.get_task(task_id) is None:
            continue
        seen.add(task_id)
        found.append(task_id)
    return found


def _dropped_messages(
    verbatim: list[dict[str, Any]],
    visible: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    visible_ids = _message_ids(visible)
    return [
        message
        for message in verbatim
        if str(message.get("id") or "") and str(message.get("id") or "") not in visible_ids
        and not _is_synthetic(message)
    ]


def _turns_fell_off(verbatim: list[dict[str, Any]], visible: list[dict[str, Any]]) -> bool:
    visible_ids = _message_ids(visible)
    for message in verbatim:
        message_id = str(message.get("id") or "")
        if message_id and not _is_synthetic_id(message_id) and message_id not in visible_ids:
            return True
    return False


def _slot_message(task_ids: list[str], lines: list[str]) -> dict[str, Any]:
    return {
        "id": f"{SLOT_ID_PREFIX}{task_ids[0]}",
        "from_agent": "__system__",
        "from_name": "Work spine",
        "to_agent": None,
        "content": "\n".join(lines),
        "message_type": "sticky_slot",
    }


def _schedule(job: Callable[[], None]) -> None:
    if _scheduler is not None:
        _scheduler(job)
        return

    def _guard() -> None:
        try:
            job()
        except Exception:
            logger.warning("sticky slot fill failed; existing slots stay")

    threading.Thread(target=_guard, name="sticky-slots", daemon=True).start()


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


def _unique(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = (value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _mode() -> str:
    return config.get_live("compaction_mode") or ""


def _headroom_percent() -> int | None:
    raw = _knob_int("compaction_task_budget_headroom_percent")
    if raw is None or raw < 0:
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
