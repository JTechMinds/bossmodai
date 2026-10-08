"""reconcile_memory_notice_prompt_lines: the stored contract stops asking agents to announce saves.

The system now posts a line in the boss's DM when an agent changes its memory,
so the decision contract's ``remember`` field note and example no longer tell
the agent to say it saved something. Prompt rows are seeded once and never
overwritten, so the two line swaps reach an existing database only through
this line-level, marker-guarded pass. It runs after the remember pass, which
inserts both old lines.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

import db
import db.settings as settings_db
from core import config
from core.default_prompts import load_default_prompt, prompt_file_path

_MARKER = "memory_notice_prompt_lines_reconciled"
_KEY = "runtime_contract_decision"
_EDITS = settings_db._MEMORY_NOTICE_PROMPT_LINE_EDITS
_REMEMBER_EDITS = settings_db._MEMORY_REMEMBER_PROMPT_LINE_EDITS


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _stored(key: str) -> tuple[str, str]:
    row = db.query_one("SELECT value, category FROM settings WHERE key = $1", [key])
    assert row is not None
    return str(row["value"]), str(row["category"])


def _prior_default(key: str) -> str:
    """The shipped default as it was before the notice edit: every new line swapped back."""
    text = load_default_prompt(key)
    for old_line, new_line in _EDITS[key]:
        assert text.count(new_line) == 1, (key, new_line)
        text = text.replace(new_line, old_line)
    return text


def _rerun() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    settings_db.reconcile_memory_notice_prompt_lines()


def test_only_the_decision_contract_is_edited() -> None:
    assert set(_EDITS) == {_KEY}


def test_the_shipped_file_carries_the_new_lines_and_not_the_old() -> None:
    lines = prompt_file_path(_KEY).read_text(encoding="utf-8").splitlines()
    for old_line, new_line in _EDITS[_KEY]:
        assert lines.count(new_line) == 1, new_line
        assert old_line not in lines, old_line
    text = "\n".join(lines)
    assert "Say in a few words that you saved it." not in text
    assert "Saved to memory." not in text
    assert "The boss is told automatically; you don't need to mention it." in text


def test_both_old_lines_are_lines_the_remember_pass_inserts() -> None:
    inserted = {
        line
        for _old, new in _REMEMBER_EDITS[_KEY]
        for line in new.split("\n")
    }
    for old_line, _new_line in _EDITS[_KEY]:
        assert old_line in inserted, old_line


def test_a_row_holding_the_prior_default_gets_the_new_lines_and_keeps_its_category(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _value, category = _stored(_KEY)
    db.set_setting(_KEY, _prior_default(_KEY), category)
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    value, stored_category = _stored(_KEY)
    assert value == load_default_prompt(_KEY)
    assert stored_category == settings_db.get_seed_setting_default(_KEY)[1]
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert _stored(_MARKER)[0] == "true"


def test_an_edited_row_keeps_every_other_byte_and_its_line_ending() -> None:
    (old_line, new_line), _second = _EDITS[_KEY]
    edited = f"Operator preface.\r\n{old_line}\r\nOperator tail without newline"
    db.set_setting(_KEY, edited, "advanced")
    _rerun()
    assert _stored(_KEY)[0] == f"Operator preface.\r\n{new_line}\r\nOperator tail without newline"


def test_a_row_missing_a_line_is_left_for_that_line_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    (first_old, _first_new), (second_old, second_new) = _EDITS[_KEY]
    edited = _prior_default(_KEY).replace(first_old + "\n", "- An operator's own rule.\n")
    assert first_old not in edited
    db.set_setting(_KEY, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    value, _category = _stored(_KEY)
    assert value == edited.replace(second_old + "\n", second_new + "\n", 1)
    assert "- An operator's own rule." in value
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1
    assert _KEY in warned[0] and first_old in warned[0]
    assert "notice" in warned[0]


def test_a_row_equal_to_the_current_default_is_skipped_quietly(caplog: pytest.LogCaptureFixture) -> None:
    before = _stored(_KEY)
    assert before[0] == load_default_prompt(_KEY)
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert _stored(_KEY) == before
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_the_second_run_is_a_no_op() -> None:
    # init_db already recorded the marker; a later pass must not touch a row.
    prior = _prior_default(_KEY)
    db.set_setting(_KEY, prior, "advanced")
    settings_db.reconcile_memory_notice_prompt_lines()
    assert _stored(_KEY)[0] == prior
    _rerun()
    assert _stored(_KEY)[0] == load_default_prompt(_KEY)
    db.set_setting(_KEY, prior, "advanced")
    settings_db.reconcile_memory_notice_prompt_lines()
    assert _stored(_KEY)[0] == prior
