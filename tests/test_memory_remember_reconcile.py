"""reconcile_memory_remember_prompt_lines: the ``remember`` contract lines reach stored rows.

Prompt rows are seeded once and never overwritten, so the shipped edits that
teach the decision contract the ``remember`` envelope field reach an existing
database only through this line-level, marker-guarded pass. It runs after
the first memory pass, whose new lines are its old lines.
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

_MARKER = "memory_remember_prompt_lines_reconciled"
_FIRST_MARKER = "memory_prompt_lines_reconciled"
_KEY = "runtime_contract_decision"
_EDITS = settings_db._MEMORY_REMEMBER_PROMPT_LINE_EDITS
_FIRST_EDITS = settings_db._MEMORY_PROMPT_LINE_EDITS


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
    """The shipped default as it was before the remember edit: every new line swapped back."""
    text = load_default_prompt(key)
    for old_line, new_line in _EDITS[key]:
        assert text.count(new_line) == 1, (key, new_line)
        text = text.replace(new_line, old_line)
    return text


def _rerun() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    settings_db.reconcile_memory_remember_prompt_lines()


def test_only_the_decision_contract_is_edited() -> None:
    assert set(_EDITS) == {_KEY}


def test_the_shipped_file_carries_the_new_lines_and_not_the_swapped_old_ones() -> None:
    text = prompt_file_path(_KEY).read_text(encoding="utf-8")
    lines = text.splitlines()
    for old_line, new_line in _EDITS[_KEY]:
        assert text.count(new_line + "\n") == 1, new_line
        if new_line.startswith(old_line + "\n"):
            # An insertion: its anchor line stays in the file.
            assert old_line in lines, old_line
        else:
            assert old_line not in lines, old_line


def test_the_swapped_old_lines_are_the_first_pass_new_lines() -> None:
    first_new = {new_line for _old, new_line in _FIRST_EDITS[_KEY]}
    swapped = [old_line for old_line, new_line in _EDITS[_KEY] if not new_line.startswith(old_line + "\n")]
    assert len(swapped) == 2
    assert set(swapped) == first_new


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


def test_a_row_from_before_both_memory_passes_reaches_the_current_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    oldest = _prior_default(_KEY)
    for old_line, new_line in _FIRST_EDITS[_KEY]:
        assert oldest.count(new_line) == 1, new_line
        oldest = oldest.replace(new_line, old_line)
    db.set_setting(_KEY, oldest, "advanced")
    db.execute("DELETE FROM settings WHERE key = $1", [_FIRST_MARKER])
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        # The order seed_defaults runs them in.
        settings_db.reconcile_memory_prompt_lines()
        settings_db.reconcile_memory_remember_prompt_lines()
    assert _stored(_KEY)[0] == load_default_prompt(_KEY)
    assert [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and _KEY in r.getMessage()
    ] == []


def test_an_edited_row_keeps_every_other_byte_and_its_line_ending() -> None:
    (old_line, new_line) = _EDITS[_KEY][0]
    edited = f"Operator preface.\r\n{old_line}\r\nOperator tail without newline"
    db.set_setting(_KEY, edited, "advanced")
    _rerun()
    assert _stored(_KEY)[0] == f"Operator preface.\r\n{new_line}\r\nOperator tail without newline"


def test_a_row_missing_a_line_is_left_for_that_line_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    (first_old, _first_new), *rest = _EDITS[_KEY]
    edited = _prior_default(_KEY).replace(first_old + "\n", "- An operator's own rule.\n")
    assert first_old not in edited
    db.set_setting(_KEY, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    value, _category = _stored(_KEY)
    expected = edited
    for old_line, new_line in rest:
        expected = expected.replace(old_line + "\n", new_line + "\n", 1)
    assert value == expected
    assert "- An operator's own rule." in value
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1
    assert _KEY in warned[0] and first_old in warned[0]
    assert "remember" in warned[0]


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
    settings_db.reconcile_memory_remember_prompt_lines()
    assert _stored(_KEY)[0] == prior
    _rerun()
    assert _stored(_KEY)[0] == load_default_prompt(_KEY)
    db.set_setting(_KEY, prior, "advanced")
    settings_db.reconcile_memory_remember_prompt_lines()
    assert _stored(_KEY)[0] == prior
