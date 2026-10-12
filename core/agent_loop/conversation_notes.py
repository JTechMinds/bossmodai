"""Conversation notes: background compaction of DM and thread history.

Scope: human↔agent DMs, agent↔agent DMs and channel threads. Meetings, task
threads, work transcripts and sticky slots are not handled here.

An agent's context is ``[instructions] + [memory] + [conversation notes] +
[recent messages]``. This module owns only the history segment: it never
reads or writes instructions, standing prefs, memory, the communication
snapshot or the trigger.

One per-thread state (``conversation_notes`` table, one row per scope) is read
by both the prompt builder and the transcript API through
:func:`load_thread_window`, so what the operator sees is what the agent sees.

Thresholds, with ``B`` = the history budget and ``T`` = rendered tokens:

* start a background run when ``T(notes) + T(tail) >= B * start%`` and no run
  is in flight for the scope (the only gate: no turn counters, no cooldowns);
* a run keeps the newest rows totalling ``>= B * keep%`` verbatim and folds
  everything older, plus the previous notes, into new notes of at most
  ``B * notes%`` tokens (one shorter retry, never truncated);
* the loader shows at most ``B * ceiling%`` of tail tokens; reaching that is
  a safety net for a failing or disabled compaction and logs a WARNING.

The agent turn never waits: the System AI call runs in a worker thread from
an event-loop task. This module holds policy and orchestration only; the SQL
is in ``db/conversation_notes.py`` and the thread readers in ``db/``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import db
import db.channels as channel_rows
import db.messages as dm_rows
from core import config
from core.boss import boss_label
from core.config import ConfigError
from core.llm.client import count_history_tokens
from core.llm.system_completion import complete_text, system_ai_is_configured
from core.runtime.events import runtime_events

logger = logging.getLogger(__name__)

ScopeKind = Literal["human_dm", "agent_dm", "channel"]
_SCOPE_KINDS: frozenset[str] = frozenset({"human_dm", "agent_dm", "channel"})

NOTES_ID_PREFIX = "conversation-notes:"
NOTES_FROM_AGENT = "__system__"
NOTES_FROM_NAME = "Conversation notes"
NOTES_LEAD = "Summary of earlier messages in this conversation (they are no longer shown):\n"

# Rows read per page when walking a thread newest-first.
_PAGE_ROWS = 100
# Senders that are never an agent: rows by them have no floor to check.
_NON_AGENT_SENDERS = frozenset({"__human__", "__system__"})

_NOTES_SYSTEM = (
    "Update the conversation notes for a chat. "
    "The input is the previous notes (may be empty), then the older messages being folded in. "
    "Output plain bullet points, using people's names (never \"I\" or \"you\"). Cover: "
    "decisions made; open questions; commitments (who owes what); "
    "facts, file paths and links mentioned; preferences people stated. "
    "Carry forward everything still relevant from the previous notes. "
    "Do not invent anything. Stay under {cap} tokens. Output only the bullets."
)

_SHORTER_REQUEST = (
    "Those notes are {tokens} tokens; the limit is {cap}. "
    "Rewrite them shorter, keeping the most important points. Output only the bullets."
)

# Failure text stored as ``last_error`` and shown to the operator on the
# transcript divider; technical detail goes to the WARNING log line only.
RUN_FAILED_EMPTY = "The summarizer returned nothing."
RUN_FAILED_EMPTY_RETRY = "The summarizer returned nothing when asked for a shorter version."
RUN_FAILED_TOO_LONG = "The summary came out too long, even after asking for a shorter one."
RUN_FAILED_UNEXPECTED = "Something went wrong while summarizing. The details are in the log."


# ─── Types ───


@dataclass(frozen=True, slots=True)
class ThreadScope:
    """One conversation whose history is compacted as a unit.

    Attributes:
        kind: ``human_dm`` (key = the agent id), ``agent_dm`` (key =
            ``"<lower id>:<higher id>"``, so both agents share one state) or
            ``channel`` (key = the channel id).
        key: The scope key.

    Raises:
        ValueError: On an unknown kind or an empty key.
    """

    kind: ScopeKind
    key: str

    def __post_init__(self) -> None:
        if self.kind not in _SCOPE_KINDS:
            raise ValueError(f"Unknown conversation scope kind {self.kind!r}")
        if not str(self.key or "").strip():
            raise ValueError("A conversation scope needs a non-empty key")

    @classmethod
    def human_dm(cls, agent_id: str) -> ThreadScope:
        """The operator's DM with one agent."""
        return cls("human_dm", agent_id)

    @classmethod
    def agent_dm(cls, agent_a: str, agent_b: str) -> ThreadScope:
        """The DM between two agents, the same scope from either side."""
        low, high = sorted((agent_a, agent_b))
        return cls("agent_dm", f"{low}:{high}")

    @classmethod
    def channel(cls, channel_id: str) -> ThreadScope:
        """One channel thread."""
        return cls("channel", channel_id)


@dataclass(frozen=True, slots=True)
class CompactionSettings:
    """The ``conversation_*`` settings, validated, read in one place.

    Attributes:
        enabled: Whether background compaction runs.
        budget_tokens: ``B``, the per-thread history budget.
        start_percent: Start a run at this share of ``B``.
        keep_percent: A run keeps at least this share of ``B`` verbatim.
        notes_percent: The notes cap as a share of ``B``.
        ceiling_percent: The loader's hard limit on tail tokens.
    """

    enabled: bool
    budget_tokens: int
    start_percent: int
    keep_percent: int
    notes_percent: int
    ceiling_percent: int

    @property
    def start_tokens(self) -> int:
        return self.budget_tokens * self.start_percent // 100

    @property
    def keep_tokens(self) -> int:
        return self.budget_tokens * self.keep_percent // 100

    @property
    def notes_cap_tokens(self) -> int:
        return self.budget_tokens * self.notes_percent // 100

    @property
    def ceiling_tokens(self) -> int:
        return self.budget_tokens * self.ceiling_percent // 100


@dataclass(slots=True)
class ThreadWindow:
    """What one conversation shows an agent right now.

    Attributes:
        notes_entry: The ``[Conversation notes]`` history entry, or ``None``
            when the scope has no usable notes.
        rows: The verbatim rows after the notes' anchor, oldest first, in
            prompt-history shape.
        notes_row: The stored notes row (including the last run's state and
            error), or ``None`` when there is none.
        tail_tokens: Rendered tokens of ``rows``.
        notes_tokens: Rendered tokens of ``notes_entry`` (0 without one).
        visible_from_message_id: The oldest row shown verbatim, or ``None``
            when ``rows`` is empty.
        ceiling_hit: True when older rows were dropped at the hard limit.
    """

    notes_entry: dict[str, Any] | None
    rows: list[dict[str, Any]]
    notes_row: dict[str, Any] | None
    tail_tokens: int
    notes_tokens: int
    visible_from_message_id: str | None
    ceiling_hit: bool


Scheduler = Callable[[Callable[[], Awaitable[None]]], None]

_scheduler: Scheduler | None = None
# Strong references to running compactions: the loop holds tasks weakly.
_tasks: set[asyncio.Task[None]] = set()
# Scopes already warned about a missing System AI, so the log is not spammed.
_unconfigured_warned: set[tuple[str, str]] = set()


def set_conversation_notes_scheduler(scheduler: Scheduler | None) -> None:
    """Replace how a claimed run is started. ``None`` restores the event-loop task.

    Args:
        scheduler: Called with a zero-argument coroutine function that runs
            one compaction; tests capture it and await it themselves.
    """
    global _scheduler
    _scheduler = scheduler


# ─── Settings ───


def _setting_int(key: str, low: int, high: int | None) -> int:
    raw = config.require(key)
    try:
        value = int(str(raw).strip(), 10)
    except ValueError as exc:
        raise ConfigError(f"Setting '{key}' must be a whole number, got {raw!r}") from exc
    if value < low or (high is not None and value > high):
        bound = f"{low}..{high}" if high is not None else f"at least {low}"
        raise ConfigError(f"Setting '{key}' must be {bound}, got {value}")
    return value


def read_settings() -> CompactionSettings:
    """Read and validate the conversation-compaction settings.

    Returns:
        The current settings.

    Raises:
        ConfigError: A key is missing, not a whole number, outside its range,
            or the percents break ``keep < start`` or ``notes < keep``. The
            message names the key.
    """
    raw_enabled = config.require("conversation_compaction_enabled")
    if raw_enabled not in {"true", "false"}:
        raise ConfigError(f"Setting 'conversation_compaction_enabled' must be true or false, got {raw_enabled!r}")
    budget = _setting_int("conversation_history_budget_tokens", 1, None)
    start = _setting_int("conversation_compaction_start_percent", 1, 100)
    keep = _setting_int("conversation_compaction_keep_percent", 1, 100)
    notes = _setting_int("conversation_notes_max_percent", 1, 100)
    ceiling = _setting_int("conversation_history_ceiling_percent", 100, 300)
    if keep >= start:
        raise ConfigError(
            "Setting 'conversation_compaction_keep_percent' must be below "
            f"'conversation_compaction_start_percent' ({keep} >= {start})"
        )
    if notes >= keep:
        raise ConfigError(
            "Setting 'conversation_notes_max_percent' must be below "
            f"'conversation_compaction_keep_percent' ({notes} >= {keep})"
        )
    return CompactionSettings(
        enabled=raw_enabled == "true",
        budget_tokens=budget,
        start_percent=start,
        keep_percent=keep,
        notes_percent=notes,
        ceiling_percent=ceiling,
    )


# ─── Rendering and counting ───


def render_history_entry(message: dict[str, Any], agent_id: str) -> str:
    """Render one history row the way the prompt shows it to ``agent_id``.

    The agent's own rows are bare content (they become ``assistant`` turns);
    every other row is ``[from_name]: content``, matching
    ``core/llm/context_builder.py``.
    """
    if message.get("from_agent") == agent_id:
        return str(message.get("content") or "")
    return _entry_text(message)


def _entry_text(message: dict[str, Any]) -> str:
    """The perspective-free ``[Name]: content`` form every window count uses."""
    sender = str(message.get("from_name") or "Unknown")
    content = str(message.get("content") or "")
    return f"[{sender}]: {content}"


@functools.lru_cache(maxsize=8192)
def _count(text: str) -> int:
    # Strings hash once; an edited row (a queue-visibility line) has new text
    # and so is counted again.
    return count_history_tokens(text)


def entry_tokens(text: str) -> int:
    """Tokens of one rendered history entry, via the shared cached counter.

    Raises:
        Exception: Whatever the tokenizer raises; never mapped to 0.
    """
    return _count(text)


def notes_history_entry(scope: ThreadScope, notes_row: dict[str, Any]) -> dict[str, Any]:
    """Build the prompt history entry that carries one scope's notes.

    It renders as ``[Conversation notes]: Summary of earlier messages …`` with
    no context-builder change, and its text tells the agent what it is.

    Args:
        scope: The conversation.
        notes_row: The stored row; its ``notes`` must be non-empty.

    Returns:
        A history row in prompt-history shape.

    Raises:
        ValueError: When the row carries no notes text.
    """
    notes = str(notes_row.get("notes") or "").strip()
    if not notes:
        raise ValueError(f"Conversation notes for {scope.kind}:{scope.key} are empty")
    return {
        "id": f"{NOTES_ID_PREFIX}{scope.kind}:{scope.key}",
        "from_agent": NOTES_FROM_AGENT,
        "from_name": NOTES_FROM_NAME,
        "to_agent": None,
        "content": NOTES_LEAD + notes,
        "message_type": scope.kind,
    }


# ─── Window ───


def _rows_newest_first(
    scope: ThreadScope,
    *,
    after_message_id: str | None,
    before: tuple[float, int] | None,
) -> list[tuple[Any, float, int]]:
    reader = channel_rows if scope.kind == "channel" else dm_rows
    return reader.list_thread_rows_newest_first(
        scope.kind,
        scope.key,
        after_message_id=after_message_id,
        before=before,
        limit=_PAGE_ROWS,
    )


def _format_page(scope: ThreadScope, rows: list[Any]) -> list[dict[str, Any]]:
    if scope.kind == "channel":
        return channel_rows.format_channel_messages(scope.key, rows, human_label=boss_label())
    return db.get_formatted_messages(rows, human_label=boss_label())


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _collect_tail(
    scope: ThreadScope,
    *,
    after_message_id: str | None,
    earliest_ts: datetime | None,
    exclude_message_id: str | None,
    ceiling_tokens: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Walk newest-first until the rows run out or the ceiling is reached.

    Returns the kept rows newest first, their tokens, and whether the ceiling
    dropped older rows. The newest row is always kept, even alone over the
    ceiling, so a turn never loses the message it is answering.
    """
    floor = _as_utc(earliest_ts) if earliest_ts is not None else None
    kept: list[dict[str, Any]] = []
    tokens = 0
    cursor: tuple[float, int] | None = None
    while True:
        page = _rows_newest_first(scope, after_message_id=after_message_id, before=cursor)
        if not page:
            return kept, tokens, False
        formatted = _format_page(scope, [row for row, _day, _rid in page])
        for (row, day, rid), entry in zip(page, formatted):
            cursor = (day, rid)
            if floor is not None and _as_utc(row.created_at) < floor:
                # Newest-first: every remaining row is older still.
                return kept, tokens, False
            if exclude_message_id and entry.get("id") == exclude_message_id:
                continue
            size = _count(_entry_text(entry))
            if kept and tokens + size > ceiling_tokens:
                return kept, tokens, True
            kept.append(entry)
            tokens += size
        if len(page) < _PAGE_ROWS:
            return kept, tokens, False


def load_thread_window(
    scope: ThreadScope,
    *,
    earliest_ts: datetime | None = None,
    exclude_message_id: str | None = None,
    perspective_agent_id: str | None,
) -> ThreadWindow:
    """Compute what one conversation shows: its notes plus the verbatim tail.

    The prompt builder and the transcript API both call this, so the operator
    sees exactly the agent's window. Rows after the notes' anchor are read
    newest-first until the ceiling; if the anchor row is gone (rewind, clear,
    delete) the notes are invalid, are deleted, and the window is read
    without them (logged at INFO).

    Args:
        scope: The conversation.
        earliest_ts: Verbatim rows created before this are not shown (the
            agent's ``earliest_ts_allowed``). It never filters the notes.
        exclude_message_id: A row left out of the window and its counts (the
            trigger's own message, which the prompt shows as the trigger).
        perspective_agent_id: The agent reading, or ``None`` for the
            transcript API. Counts never depend on it: every row is counted in
            its ``[Name]: content`` form so agent and UI agree exactly. It
            decides whether the hard-limit WARNING is logged: only on an
            agent turn (not ``None``), so transcript reads and background
            runs do not repeat it; the UI shows ``ceiling_hit`` instead.

    Returns:
        The window.

    Raises:
        ConfigError: The conversation settings are invalid.
    """
    settings = read_settings()
    notes_row = db.get_conversation_notes(scope.kind, scope.key)
    anchor = _usable_anchor(notes_row)
    try:
        rows, tail_tokens, ceiling_hit = _collect_tail(
            scope,
            after_message_id=anchor,
            earliest_ts=earliest_ts,
            exclude_message_id=exclude_message_id,
            ceiling_tokens=settings.ceiling_tokens,
        )
    except db.AnchorMissing as exc:
        logger.info(
            "conversation notes for %s:%s dropped: anchor %s no longer exists",
            scope.kind, scope.key, exc.message_id,
        )
        db.delete_conversation_notes(scope.kind, scope.key)
        notes_row = None
        anchor = None
        rows, tail_tokens, ceiling_hit = _collect_tail(
            scope,
            after_message_id=None,
            earliest_ts=earliest_ts,
            exclude_message_id=exclude_message_id,
            ceiling_tokens=settings.ceiling_tokens,
        )
    if ceiling_hit and perspective_agent_id is not None:
        logger.warning(
            "conversation history for %s:%s hit the hard limit (%d tail tokens kept, limit %d, reader %s); "
            "older messages are not in context",
            scope.kind, scope.key, tail_tokens, settings.ceiling_tokens, perspective_agent_id,
        )
    rows.reverse()
    notes_entry = notes_history_entry(scope, notes_row) if anchor is not None and notes_row is not None else None
    notes_tokens = _count(_entry_text(notes_entry)) if notes_entry is not None else 0
    return ThreadWindow(
        notes_entry=notes_entry,
        rows=rows,
        notes_row=notes_row,
        tail_tokens=tail_tokens,
        notes_tokens=notes_tokens,
        visible_from_message_id=str(rows[0]["id"]) if rows else None,
        ceiling_hit=ceiling_hit,
    )


def _usable_anchor(notes_row: dict[str, Any] | None) -> str | None:
    """The anchor of stored notes, or ``None`` when there are no notes yet.

    A row can exist with no notes: a scope whose first run is in flight or
    failed. That row still carries the run state for the UI.
    """
    if notes_row is None:
        return None
    anchor = str(notes_row.get("through_message_id") or "").strip()
    notes = str(notes_row.get("notes") or "").strip()
    return anchor if anchor and notes else None


def window_entries(window: ThreadWindow) -> list[dict[str, Any]]:
    """The prompt history of one window: the notes entry (if any), then the rows."""
    if window.notes_entry is None:
        return list(window.rows)
    return [window.notes_entry, *window.rows]


# ─── Scheduling ───


def maybe_schedule_compaction(scope: ThreadScope, window: ThreadWindow) -> bool:
    """Start a background compaction when this window is over the start line.

    Order: the enabled switch and the threshold, then System AI (a missing
    one logs a WARNING once per scope per process), then an event loop (or
    an injected scheduler), then the per-scope claim, then the task. Never
    blocks and never runs the compaction inline.

    Args:
        scope: The conversation.
        window: The window the turn just loaded for it.

    Returns:
        True when a run was claimed and started.

    Raises:
        ConfigError: The conversation or System AI timeout settings are invalid.
    """
    settings = read_settings()
    if not settings.enabled:
        return False
    if window.notes_tokens + window.tail_tokens < settings.start_tokens:
        return False
    if not system_ai_is_configured():
        marker = (scope.kind, scope.key)
        if marker not in _unconfigured_warned:
            _unconfigured_warned.add(marker)
            logger.warning(
                "conversation %s:%s is over its compaction start line but no System AI is configured; "
                "it will not be compacted",
                scope.kind, scope.key,
            )
        return False
    loop: asyncio.AbstractEventLoop | None = None
    if _scheduler is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.info("conversation compaction for %s:%s not scheduled: no event loop", scope.kind, scope.key)
            return False
    stale_after = timedelta(seconds=config.require_positive_int("system_ai_timeout_seconds") * 2)
    if not db.claim_conversation_notes_run(scope.kind, scope.key, stale_after=stale_after):
        return False

    async def run() -> None:
        await _run_compaction(scope)

    if _scheduler is not None:
        _scheduler(run)
    else:
        assert loop is not None
        task = loop.create_task(run(), name=f"conversation-notes:{scope.kind}:{scope.key}")
        _tasks.add(task)
        task.add_done_callback(_task_done)
    logger.info(
        "conversation compaction queued for %s:%s (%d notes + %d tail tokens)",
        scope.kind, scope.key, window.notes_tokens, window.tail_tokens,
    )
    return True


def _task_done(task: asyncio.Task[None]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        logger.info("conversation compaction task %s was cancelled", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        logger.error("conversation compaction task %s failed", task.get_name(), exc_info=exc)


# ─── The run ───


def _cut_index(rows: list[dict[str, Any]], sizes: list[int], keep_tokens: int) -> int:
    """Index of the first row kept verbatim; everything before it is summarized.

    Keeps the newest whole rows until they total at least ``keep_tokens``.
    When the first kept row's author differs from the row just before it,
    that one row is kept too, so the tail starts with the message the first
    kept reply answered.
    """
    kept = 0
    first = len(rows)
    for index in range(len(rows) - 1, -1, -1):
        if kept >= keep_tokens:
            break
        kept += sizes[index]
        first = index
    if 0 < first < len(rows) and rows[first].get("from_agent") != rows[first - 1].get("from_agent"):
        first -= 1
    return first


def _summarizer_input(scope: ThreadScope, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows the summarizer may read. A channel skips authors not on its floor."""
    if scope.kind != "channel":
        return rows
    from core.floors import channel_floor_id, on_floor

    floor_id = channel_floor_id(scope.key)
    kept = []
    for row in rows:
        author_id = str(row.get("from_agent") or "").strip()
        if author_id and author_id not in _NON_AGENT_SENDERS and (not floor_id or not on_floor(author_id, floor_id)):
            continue
        kept.append(row)
    return kept


def _summarizer_messages(prior_notes: str, rows: list[dict[str, Any]], cap: int) -> list[dict[str, str]]:
    lines = [_entry_text(row) for row in rows]
    body = (
        "Previous notes:\n"
        + (prior_notes or "(none)")
        + "\n\nOlder messages to fold in:\n"
        + ("\n".join(lines) if lines else "(none)")
    )
    return [
        {"role": "system", "content": _NOTES_SYSTEM.format(cap=cap)},
        {"role": "user", "content": body},
    ]


class _RunFailed(Exception):
    """A run ended without usable notes; the message is shown to the operator.

    ``tokens`` and ``cap`` are set for an over-cap result and only appear in
    the WARNING log line, never in the operator text.
    """

    def __init__(self, message: str, *, tokens: int | None = None, cap: int | None = None) -> None:
        super().__init__(message)
        self.tokens = tokens
        self.cap = cap


async def _summarize(messages: list[dict[str, str]], cap: int) -> str:
    """Ask System AI for notes within ``cap`` tokens, with one shorter retry.

    Raises:
        _RunFailed: No answer, an empty answer, or still over the cap after
            the retry. Notes are never truncated.
    """
    raw = await asyncio.to_thread(complete_text, messages)
    notes = (raw or "").strip()
    if not notes:
        raise _RunFailed(RUN_FAILED_EMPTY)
    tokens = _count(notes)
    if tokens <= cap:
        return notes
    retry = [
        *messages,
        {"role": "assistant", "content": notes},
        {"role": "user", "content": _SHORTER_REQUEST.format(tokens=tokens, cap=cap)},
    ]
    raw = await asyncio.to_thread(complete_text, retry)
    shorter = (raw or "").strip()
    if not shorter:
        raise _RunFailed(RUN_FAILED_EMPTY_RETRY)
    tokens = _count(shorter)
    if tokens > cap:
        raise _RunFailed(RUN_FAILED_TOO_LONG, tokens=tokens, cap=cap)
    return shorter


async def _run_compaction(scope: ThreadScope) -> None:
    """Compact one conversation once, then tell open transcripts.

    The window is reloaded from the database, never taken from the turn that
    scheduled the run. Commit is compare-and-swap on the previous anchor. Any
    failure is stored on the scope (``run_state='failed'``) and logged; the
    previous notes stay in force and the next turn above the start line
    retries.
    """
    try:
        changed = await _compact(scope)
    except _RunFailed as exc:
        size = f" ({exc.tokens} tokens, limit {exc.cap})" if exc.tokens is not None else ""
        logger.warning("conversation compaction for %s:%s failed: %s%s", scope.kind, scope.key, exc, size)
        db.fail_conversation_notes_run(scope.kind, scope.key, error=str(exc))
        changed = True
    except Exception:
        logger.warning("conversation compaction for %s:%s failed", scope.kind, scope.key, exc_info=True)
        db.fail_conversation_notes_run(scope.kind, scope.key, error=RUN_FAILED_UNEXPECTED)
        changed = True
    if changed:
        await runtime_events.broadcast_conversation_notes(scope.kind, scope.key)


async def _compact(scope: ThreadScope) -> bool:
    """One run. Returns True when notes were committed, False when released."""
    settings = read_settings()
    if not settings.enabled:
        db.release_conversation_notes_run(scope.kind, scope.key, reason="compaction is turned off")
        return False
    window = load_thread_window(scope, perspective_agent_id=None)
    prior_anchor = _usable_anchor(window.notes_row) if window.notes_entry is not None else None
    prior_notes = str(window.notes_row.get("notes") or "").strip() if prior_anchor and window.notes_row else ""
    rows = window.rows
    sizes = [_count(_entry_text(row)) for row in rows]
    cut = _cut_index(rows, sizes, settings.keep_tokens)
    folded = _summarizer_input(scope, rows[:cut])
    if cut <= 0 or (not folded and not prior_notes):
        logger.info("conversation compaction for %s:%s: nothing to compact", scope.kind, scope.key)
        db.release_conversation_notes_run(scope.kind, scope.key, reason="nothing to compact")
        return False
    cap = settings.notes_cap_tokens
    notes = await _summarize(_summarizer_messages(prior_notes, folded, cap), cap)
    source_tokens = sum(sizes[:cut]) + (_count(prior_notes) if prior_notes else 0)
    committed = db.commit_conversation_notes(
        scope.kind,
        scope.key,
        expected_prior_through_id=prior_anchor,
        through_message_id=str(rows[cut - 1]["id"]),
        notes=notes,
        source_tokens=source_tokens,
    )
    if not committed:
        logger.info("conversation compaction for %s:%s superseded; notes unchanged", scope.kind, scope.key)
        return False
    logger.info(
        "conversation notes stored for %s:%s (%d rows, %d source tokens, through %s)",
        scope.kind, scope.key, cut, source_tokens, rows[cut - 1]["id"],
    )
    return True
