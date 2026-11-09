"""Test helper: store Context Window settings that give exact token budgets.

Every context budget is a share of the usable window (core/context_budget.py),
so a test that needs, say, a 1,000-token history budget sets the usable window
and the shares rather than one absolute number. Writes go straight to the
settings table, like the tests' other setting writes, and the result is read
back through ``read_context_budget`` so a combination that breaks the rules
fails the test instead of passing silently.

The usable window is the context window less the reserve, the largest
instructions any agent's turn measured (0 on a fresh test database). A turn
run afterwards measures its agent and records it, which moves the reserve
and so every share. A test that runs turns and needs its budget to hold
still calls :func:`hold_reserve` first: a stand-in agent that never runs
holds a reserve larger than the test agent's instructions, so the test
agent's own measurements never become the largest and every turn is sized
in one pass.
"""

from __future__ import annotations

import db
from core import config
from core.context_budget import (
    ASKED_SETTING,
    HISTORY_CEILING_SETTING,
    HISTORY_SETTING,
    REPLY_SETTING,
    RESULT_SETTING,
    TASK_LOG_SETTING,
    WINDOW_SETTING,
    WORK_SETTING,
    WORK_SUMMARY_SETTING,
    WORK_TRIM_SETTING,
    ContextBudget,
    read_context_budget,
    reserve_of,
)

_SHARE_KEYS = {
    "reply": REPLY_SETTING,
    "result": RESULT_SETTING,
    "history": HISTORY_SETTING,
    "history_ceiling": HISTORY_CEILING_SETTING,
    "asked": ASKED_SETTING,
    "task_log": TASK_LOG_SETTING,
    "work": WORK_SETTING,
    "work_trim": WORK_TRIM_SETTING,
    "work_summary": WORK_SUMMARY_SETTING,
}


# Larger than a test agent's instructions (about 4,600 tokens for a fresh
# agent on a task), so a held reserve is never overtaken by a test's turn.
HELD_RESERVE_TOKENS = 20_000


def hold_reserve(tokens: int = HELD_RESERVE_TOKENS) -> str:
    """Hold the reserve at ``tokens`` with a stand-in agent that never runs.

    The reserve is the largest stored measurement of any agent; a test
    agent's turn replaces only its own row. While its instructions are
    smaller than ``tokens``, the reserve stays ``tokens`` through every turn,
    so a budget stored with :func:`set_context_budget` afterwards holds and
    each turn is sized in one pass.

    Args:
        tokens: The reserve to hold.

    Returns:
        The stand-in agent's id.
    """
    holder = db.create_agent("Reserve Holder", role="Stand-in")
    db.record_instruction_size(holder.id, tokens)
    return holder.id


def set_context_budget(*, usable: int, **percents: int) -> ContextBudget:
    """Store a usable window of ``usable`` tokens and any share overrides.

    Args:
        usable: The usable window in tokens; the context window becomes the
            current reserve (the largest stored instruction measurement, 0
            before any) plus this.
        **percents: Share overrides by ``ContextShares`` field name
            (``history=50``, ``work_summary=10``); the rest keep their
            stored values.

    Returns:
        The budget the stored settings now give.

    Raises:
        KeyError: A share name is not a ``ContextShares`` percent.
        core.config.ConfigError: The settings break a context rule.
    """
    reserved, _reserved_by = reserve_of(db.largest_instruction_size())
    values = {WINDOW_SETTING: reserved + usable}
    values.update({_SHARE_KEYS[name]: value for name, value in percents.items()})
    for key, value in values.items():
        seeded = db.get_seed_setting_default(key)
        assert seeded is not None, key
        db.set_setting(key, str(value), seeded[1])
    config.reload()
    return read_context_budget()
