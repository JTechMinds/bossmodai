"""The context window budget: what one agent request may hold, in tokens.

The one owner of the context arithmetic, its rules and its presets, shared by
the runtime (every budget a turn applies) and the Settings route (the
never-too-big rule on save, the presets, the numbers the Context Window tab
shows). Every budget is a share of one number, the model's context window:

    usable    = Context window - Fixed prompt room
    Shared:     reply    = usable x Reply %             -> max_tokens of every agent model call
                result   = usable x Largest result %    -> most one command result may add
    Chat mode:  history  = usable x History %           -> DM / thread / meeting history
                                                           (hard cap = history x Hard limit %)
                asked    = usable x When you ask %      -> summary + newest steps, when you
                                                           message a working agent
    Work mode:  task_log = usable x Task log %          -> the task's event log in a work turn
                work     = usable x Work %              -> saved work: summary + steps + what
                                                           you told it during the work
    worst chat turn = fixed + reply + history cap + asked + result
    worst work turn = fixed + reply + task_log + work + result

The Hard limit is a share of History; Work kept after summarizing and Work
summary size are shares of Work. :func:`check_shares` is the single source of
the rule that both worst cases fit the usable window, so a saved combination
can never make a request too big.

Tokens are counted with the shared generic tokenizer
(``core.llm.client.count_text_tokens``). This module imports only the
standard library at module level: ``core.config`` imports ``db``, whose
package import loads ``core.bm_cli``, which imports this module, so the
settings reader and the counter are imported inside the functions that use
them.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType

WINDOW_SETTING = "context_window_tokens"
FIXED_SETTING = "context_fixed_tokens"
REPLY_SETTING = "context_reply_percent"
RESULT_SETTING = "context_result_percent"
HISTORY_SETTING = "context_history_percent"
ASKED_SETTING = "context_asked_percent"
TASK_LOG_SETTING = "context_task_log_percent"
WORK_SETTING = "context_work_percent"
HISTORY_CEILING_SETTING = "conversation_history_ceiling_percent"
WORK_TRIM_SETTING = "work_snapshot_trim_percent"
WORK_SUMMARY_SETTING = "work_snapshot_summary_percent"
COMPACTION_START_SETTING = "conversation_compaction_start_percent"
COMPACTION_KEEP_SETTING = "conversation_compaction_keep_percent"
NOTES_MAX_SETTING = "conversation_notes_max_percent"

# ContextShares field -> the setting it is read from.
_SHARE_FIELDS: Mapping[str, str] = MappingProxyType({
    "window": WINDOW_SETTING,
    "fixed": FIXED_SETTING,
    "reply": REPLY_SETTING,
    "result": RESULT_SETTING,
    "history": HISTORY_SETTING,
    "history_ceiling": HISTORY_CEILING_SETTING,
    "asked": ASKED_SETTING,
    "task_log": TASK_LOG_SETTING,
    "work": WORK_SETTING,
    "work_trim": WORK_TRIM_SETTING,
    "work_summary": WORK_SUMMARY_SETTING,
})

# Every key check_shares governs: a write to one of these is checked against
# the never-too-big rule with the other stored values.
SHARE_SETTINGS: frozenset[str] = frozenset(_SHARE_FIELDS.values())

# Every key a preset writes: the shares plus the conversation-memory percents,
# whose own rules (keep < start, notes < keep) live with the Settings route.
PRESET_SETTINGS: tuple[str, ...] = (
    *_SHARE_FIELDS.values(),
    COMPACTION_START_SETTING,
    COMPACTION_KEEP_SETTING,
    NOTES_MAX_SETTING,
)

# The Settings labels (ui/static/js/settings/settings-context-meta.js) the
# problems name, so the operator finds each setting by what it is called.
SETTING_LABELS: Mapping[str, str] = MappingProxyType({
    WINDOW_SETTING: "Context window",
    FIXED_SETTING: "Fixed prompt room",
    REPLY_SETTING: "Reply",
    RESULT_SETTING: "Largest command result",
    HISTORY_SETTING: "History",
    HISTORY_CEILING_SETTING: "Hard limit",
    ASKED_SETTING: "When you ask",
    TASK_LOG_SETTING: "Task log",
    WORK_SETTING: "Work",
    WORK_TRIM_SETTING: "Work kept after summarizing",
    WORK_SUMMARY_SETTING: "Work summary size",
})

# The shares of the usable window that must each be a whole percent from 1 to 99.
_USABLE_SHARES = ("reply", "result", "history", "asked", "task_log", "work")

_CUT_SEPARATOR = "\n\n"
_CUT_NOTE = (
    "(About {tokens:,} more tokens of this output were cut to fit the agent's context. "
    "Narrow the command, or read a file in parts with rr.)"
)


@dataclass(frozen=True, slots=True)
class ContextShares:
    """The stored context settings, as whole numbers.

    Attributes:
        window: Context window, in tokens.
        fixed: Fixed prompt room, in tokens.
        reply: Reply, as a percent of the usable window.
        result: Largest command result, as a percent of the usable window.
        history: History, as a percent of the usable window.
        history_ceiling: Hard limit, as a percent of History.
        asked: When you ask, as a percent of the usable window.
        task_log: Task log, as a percent of the usable window.
        work: Work, as a percent of the usable window.
        work_trim: Work kept after summarizing, as a percent of Work.
        work_summary: Work summary size, as a percent of Work.
    """

    window: int
    fixed: int
    reply: int
    result: int
    history: int
    history_ceiling: int
    asked: int
    task_log: int
    work: int
    work_trim: int
    work_summary: int


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Every context budget, in tokens, computed from :class:`ContextShares`.

    With ``pct(p) = usable * p // 100``:

    - ``usable = window - fixed``;
    - ``reply``, ``result``, ``history``, ``asked``, ``task_log`` and ``work``
      are ``pct`` of their share;
    - ``history_cap = history * history_ceiling // 100``, the most history a
      chat turn can show while conversation memory catches up;
    - ``worst_chat_turn = fixed + reply + history_cap + asked + result``;
    - ``worst_work_turn = fixed + reply + task_log + work + result``.

    Attributes:
        window: The model's context window.
        fixed: Room set aside for what every turn starts with.
        usable: What the shares divide.
        reply: The ``max_tokens`` of every agent model call.
        result: The most one command result may add.
        history: A chat turn's DM, thread or meeting history.
        history_cap: History at its hard limit.
        asked: The paused-work view a working agent sees when messaged.
        task_log: The task's event log in a work turn.
        work: Saved work in a work turn: summary, steps and what the
            operator told it during the work.
        worst_chat_turn: The biggest chat-turn request.
        worst_work_turn: The biggest work-turn request.
    """

    window: int
    fixed: int
    usable: int
    reply: int
    result: int
    history: int
    history_cap: int
    asked: int
    task_log: int
    work: int
    worst_chat_turn: int
    worst_work_turn: int


@dataclass(frozen=True, slots=True)
class ContextPreset:
    """A starting point that writes every context setting in one save.

    Attributes:
        id: ``small``, ``medium`` or ``large``.
        label: The button text in Settings.
        values: One whole number per key in :data:`PRESET_SETTINGS`.
    """

    id: str
    label: str
    values: Mapping[str, int]


def compute_budget(shares: ContextShares) -> ContextBudget:
    """Turn the stored shares into token budgets (the formulas on :class:`ContextBudget`).

    Pure integer arithmetic; it does not judge the shares. Callers check them
    with :func:`check_shares` first, as :func:`read_context_budget` does.

    Args:
        shares: The context settings.

    Returns:
        Every budget, in tokens.

    Raises:
        ValueError: ``window`` is not larger than ``fixed``, so there is no
            usable window to share.
    """
    usable = shares.window - shares.fixed
    if usable < 1:
        raise ValueError(
            f"Context window ({shares.window}) must be larger than Fixed prompt room ({shares.fixed})."
        )

    def pct(percent: int) -> int:
        return usable * percent // 100

    reply = pct(shares.reply)
    result = pct(shares.result)
    history = pct(shares.history)
    history_cap = history * shares.history_ceiling // 100
    asked = pct(shares.asked)
    task_log = pct(shares.task_log)
    work = pct(shares.work)
    return ContextBudget(
        window=shares.window,
        fixed=shares.fixed,
        usable=usable,
        reply=reply,
        result=result,
        history=history,
        history_cap=history_cap,
        asked=asked,
        task_log=task_log,
        work=work,
        worst_chat_turn=shares.fixed + reply + history_cap + asked + result,
        worst_work_turn=shares.fixed + reply + task_log + work + result,
    )


def check_shares(shares: ContextShares) -> list[str]:
    """List every way the shares break the context rules, in Settings words.

    The single source of the never-too-big rule: the Settings route refuses
    a save that would break it, and :func:`read_context_budget` refuses to
    run on stored values that do. The rules:

    - ``window > fixed >= 1``;
    - Reply, Largest command result, History, When you ask, Task log and
      Work are each 1-99;
    - Hard limit is 100-300;
    - ``1 <= work_summary < work_trim <= 99``;
    - chat: ``reply + history * history_ceiling / 100 + asked + result <= 100``;
    - work: ``reply + task_log + work + result <= 100``;
    - ``asked >= ceil(work * work_summary / 100)``, so the whole work summary
      fits the paused-work view;
    - ``usable * work // 100 * work_summary // 100 >= 1``, so the summary
      has room.

    Args:
        shares: The values to check, stored or about to be.

    Returns:
        The operator-facing problems, most basic first; empty when the shares
        are valid.
    """
    problems: list[str] = []
    if shares.fixed < 1:
        problems.append(f"{SETTING_LABELS[FIXED_SETTING]} must be a whole number of at least 1.")
    if shares.window <= shares.fixed:
        problems.append(
            f"Context window ({shares.window:,}) must be larger than Fixed prompt room ({shares.fixed:,})."
        )
    for name in _USABLE_SHARES:
        if not 1 <= getattr(shares, name) <= 99:
            problems.append(f"{SETTING_LABELS[_SHARE_FIELDS[name]]} must be a whole number from 1 to 99.")
    if not 100 <= shares.history_ceiling <= 300:
        problems.append(f"{SETTING_LABELS[HISTORY_CEILING_SETTING]} must be a whole number from 100 to 300.")
    trim_label = SETTING_LABELS[WORK_TRIM_SETTING]
    summary_label = SETTING_LABELS[WORK_SUMMARY_SETTING]
    if not 1 <= shares.work_trim <= 99:
        problems.append(f"{trim_label} must be a whole number from 1 to 99.")
    if not 1 <= shares.work_summary <= 99:
        problems.append(f"{summary_label} must be a whole number from 1 to 99.")
    elif shares.work_summary >= shares.work_trim:
        problems.append(
            f"{summary_label} ({shares.work_summary}%) must be lower than {trim_label} ({shares.work_trim}%)."
        )

    # In hundredths of a percent: History at its hard limit need not be whole.
    history_at_cap = shares.history * shares.history_ceiling
    chat_total = 100 * (shares.reply + shares.asked + shares.result) + history_at_cap
    if chat_total > 100 * 100:
        problems.append(
            f"A chat turn could use {_hundredths(chat_total)}% of the usable window "
            f"(Reply {shares.reply}% + History up to {_hundredths(history_at_cap)}% + "
            f"When you ask {shares.asked}% + Largest result {shares.result}%). "
            "Lower one so the total is 100% or less."
        )
    work_total = shares.reply + shares.task_log + shares.work + shares.result
    if work_total > 100:
        problems.append(
            f"A work turn could use {work_total}% of the usable window "
            f"(Reply {shares.reply}% + Task log {shares.task_log}% + Work {shares.work}% + "
            f"Largest result {shares.result}%). Lower one so the total is 100% or less."
        )
    needed_asked = math.ceil(shares.work * shares.work_summary / 100)
    if shares.asked < needed_asked:
        problems.append(
            f"When you ask ({shares.asked}%) must be at least {needed_asked}% (Work {shares.work}% × "
            f"Work summary size {shares.work_summary}%), so an agent's whole summary fits when you message it."
        )
    usable = shares.window - shares.fixed
    if usable >= 1 and usable * shares.work // 100 * shares.work_summary // 100 < 1:
        problems.append(
            f"Work summary size ({shares.work_summary}%) leaves the work summary no room: "
            f"it would be under 1 token of a {usable:,}-token usable window."
        )
    return problems


def _hundredths(value: int) -> str:
    """A percentage held in hundredths, as plain text: ``7200`` -> ``"72"``, ``1875`` -> ``"18.75"``."""
    whole, part = divmod(value, 100)
    return str(whole) if part == 0 else f"{whole}.{part:02d}".rstrip("0")


def read_shares(overrides: Mapping[str, int] | None = None) -> ContextShares:
    """Read the stored context settings, optionally with some values replaced.

    Args:
        overrides: Setting key -> value to use instead of the stored one, for
            checking a write before it is saved. Keys must be in
            :data:`SHARE_SETTINGS`.

    Returns:
        The shares.

    Raises:
        ValueError: An override names a key that is not a context share.
        core.config.ConfigError: A stored key is missing or not an integer.
    """
    from core import config

    replaced = dict(overrides or {})
    unknown = set(replaced) - SHARE_SETTINGS
    if unknown:
        raise ValueError(f"Not context share settings: {', '.join(sorted(unknown))}")
    values = {
        field.name: replaced[_SHARE_FIELDS[field.name]]
        if _SHARE_FIELDS[field.name] in replaced
        else config.require_int(_SHARE_FIELDS[field.name])
        for field in fields(ContextShares)
    }
    return ContextShares(**values)


def read_context_budget() -> ContextBudget:
    """Read, check and compute the current context budget.

    Returns:
        Every budget, in tokens, from the stored settings.

    Raises:
        core.config.ConfigError: A setting is missing or not an integer, or
            the stored values break a rule of :func:`check_shares`; the
            message lists every problem in Settings words.
    """
    from core import config

    shares = read_shares()
    problems = check_shares(shares)
    if problems:
        raise config.ConfigError("The Context Window settings are invalid: " + " ".join(problems))
    return compute_budget(shares)


# ─── Presets ───

# Shared by every preset (D3); each preset sets the rest.
_PRESET_COMMON: Mapping[str, int] = MappingProxyType({
    FIXED_SETTING: 12_000,
    RESULT_SETTING: 5,
    HISTORY_CEILING_SETTING: 125,
    ASKED_SETTING: 25,
    TASK_LOG_SETTING: 5,
    WORK_TRIM_SETTING: 60,
    WORK_SUMMARY_SETTING: 20,
    COMPACTION_START_SETTING: 80,
    COMPACTION_KEEP_SETTING: 50,
    NOTES_MAX_SETTING: 10,
})


def _preset(preset_id: str, label: str, *, window: int, reply: int, history: int, work: int) -> ContextPreset:
    values = {
        **_PRESET_COMMON,
        WINDOW_SETTING: window,
        REPLY_SETTING: reply,
        HISTORY_SETTING: history,
        WORK_SETTING: work,
    }
    return ContextPreset(id=preset_id, label=label, values=MappingProxyType(values))


CONTEXT_PRESETS: tuple[ContextPreset, ...] = (
    _preset("small", "Small (32K)", window=32_000, reply=15, history=15, work=55),
    _preset("medium", "Medium (128K)", window=128_000, reply=12, history=20, work=60),
    _preset("large", "Large (256K)", window=256_000, reply=12, history=20, work=60),
)


def find_preset(preset_id: str) -> ContextPreset:
    """Return the preset with this id.

    Raises:
        KeyError: No preset has this id.
    """
    for preset in CONTEXT_PRESETS:
        if preset.id == preset_id:
            return preset
    raise KeyError(f"Unknown context preset {preset_id!r}")


def active_preset(values: Mapping[str, int]) -> str | None:
    """Return the id of the preset the settings match exactly, if any.

    Args:
        values: Setting key -> stored value, for at least every key in
            :data:`PRESET_SETTINGS`.

    Returns:
        The matching preset's id, or ``None`` when any value differs from
        every preset (the Settings tab then shows "Custom").
    """
    for preset in CONTEXT_PRESETS:
        if all(values.get(key) == value for key, value in preset.values.items()):
            return preset.id
    return None


# ─── Command results ───


def longest_fitting_prefix(text: str, *, max_tokens: int, count: Callable[[str], int], suffix: str = "") -> str:
    """Return the longest character prefix ``p`` of ``text`` with ``count(p + suffix) <= max_tokens``.

    The probe grows exponentially before it bisects, so a very long text is
    never counted whole: ``count`` is linear in its input, and only prefixes
    up to about twice the fitting length are measured.

    Args:
        text: The text to cut.
        max_tokens: The budget the prefix and ``suffix`` must fit together.
        count: The token counter.
        suffix: Text that will follow the prefix and is counted with it.

    Returns:
        The prefix; ``""`` when not even one character fits.
    """
    # Invariant: text[:low] fits (or low == 0), text[:high] does not (or high > len).
    low, high = 0, 1
    while high <= len(text) and count(text[:high] + suffix) <= max_tokens:
        low, high = high, high * 2
    high = min(high, len(text) + 1)
    while high - low > 1:
        middle = (low + high) // 2
        if count(text[:middle] + suffix) <= max_tokens:
            low = middle
        else:
            high = middle
    return text[:low]


def _cut_note(tokens: int) -> str:
    return _CUT_NOTE.format(tokens=tokens)


def fit_command_result(text: str, *, limit_tokens: int) -> str:
    """Fit one command's rendered result inside the Largest command result budget.

    Unchanged when ``text`` fits. Otherwise the leading whole lines that fit
    together with a cut note are kept (a first line too long on its own keeps
    its longest fitting character prefix), then the note: "(About N more
    tokens of this output were cut to fit the agent's context. Narrow the
    command, or read a file in parts with rr.)", where N is the total minus
    what was kept. ``cat`` and ``rr`` size their reads to fit whole, so this
    never cuts a read.

    Args:
        text: The rendered command result.
        limit_tokens: The most tokens it may add (``ContextBudget.result``).

    Returns:
        ``text``, or its leading part followed by the cut note.

    Raises:
        ValueError: ``limit_tokens`` cannot hold even the cut note.
    """
    from core.llm.client import count_text_tokens

    total = count_text_tokens(text)
    if total <= limit_tokens:
        return text
    # Reserved at the largest N the note can carry, so the real note fits too.
    room = limit_tokens - count_text_tokens(_CUT_SEPARATOR + _cut_note(total))
    if room < 1:
        raise ValueError(
            f"A command result limit of {limit_tokens} tokens cannot hold the note that says the output was cut."
        )
    lines = text.split("\n")
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = count_text_tokens(line + "\n")
        if used + cost > room:
            break
        kept.append(line)
        used += cost
    if not kept:
        kept = [longest_fitting_prefix(lines[0], max_tokens=room, count=count_text_tokens, suffix="\n")]
    body = "\n".join(kept)
    note = _cut_note(total - count_text_tokens(body))
    return f"{body}{_CUT_SEPARATOR}{note}" if body else note
