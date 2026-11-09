"""Test helper: store Context Window settings that give exact token budgets.

Every context budget is a share of the usable window (core/context_budget.py),
so a test that needs, say, a 1,000-token history budget sets the usable window
and the shares rather than one absolute number. Writes go straight to the
settings table, like the tests' other setting writes, and the result is read
back through ``read_context_budget`` so a combination that breaks the rules
fails the test instead of passing silently.
"""

from __future__ import annotations

import db
from core import config
from core.context_budget import (
    ASKED_SETTING,
    FIXED_SETTING,
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


def set_context_budget(*, usable: int, **percents: int) -> ContextBudget:
    """Store a usable window of ``usable`` tokens and any share overrides.

    Args:
        usable: The usable window in tokens; the context window becomes the
            stored fixed prompt room plus this.
        **percents: Share overrides by ``ContextShares`` field name
            (``history=50``, ``work_summary=10``); the rest keep their
            stored values.

    Returns:
        The budget the stored settings now give.

    Raises:
        KeyError: A share name is not a ``ContextShares`` percent.
        core.config.ConfigError: The settings break a context rule.
    """
    values = {WINDOW_SETTING: config.require_int(FIXED_SETTING) + usable}
    values.update({_SHARE_KEYS[name]: value for name, value in percents.items()})
    for key, value in values.items():
        seeded = db.get_seed_setting_default(key)
        assert seeded is not None, key
        db.set_setting(key, str(value), seeded[1])
    config.reload()
    return read_context_budget()
