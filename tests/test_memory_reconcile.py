"""reconcile_memory_prompt_lines: the three agent-memory prompt lines reach stored rows.

Prompt rows are seeded once and never overwritten, so the shipped edits to
runtime_contract_decision.md and system_prompt.md reach an existing database
only through this line-level, marker-guarded pass.
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

_MARKER = "memory_prompt_lines_reconciled"
_EDITS = settings_db._MEMORY_PROMPT_LINE_EDITS


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
    """The shipped default as it was before the edit: every new line swapped back."""
    text = load_default_prompt(key)
    for old_line, new_line in _EDITS[key]:
        assert text.count(new_line) == 1, (key, new_line)
        text = text.replace(new_line, old_line)
    return text


def _rerun() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    settings_db.reconcile_memory_prompt_lines()


def test_the_shipped_files_carry_the_new_lines_and_not_the_old() -> None:
    for key, pairs in _EDITS.items():
        lines = prompt_file_path(key).read_text(encoding="utf-8").splitlines()
        for old_line, new_line in pairs:
            assert new_line in lines, (key, new_line)
            assert old_line not in lines, (key, old_line)


def test_a_row_holding_the_prior_default_gets_the_new_lines_and_keeps_its_category(
    caplog: pytest.LogCaptureFixture,
) -> None:
    for key in _EDITS:
        _value, category = _stored(key)
        db.set_setting(key, _prior_default(key), category)
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    for key in _EDITS:
        value, category = _stored(key)
        assert value == load_default_prompt(key)
        assert category == settings_db.get_seed_setting_default(key)[1]
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert _stored(_MARKER)[0] == "true"


def test_an_edited_row_keeps_every_other_byte_and_its_line_ending() -> None:
    key = "system_prompt_template"
    (old_line, new_line), = _EDITS[key]
    edited = f"Operator preface.\r\n{old_line}\r\nOperator tail without newline"
    db.set_setting(key, edited, "advanced")
    _rerun()
    assert _stored(key)[0] == f"Operator preface.\r\n{new_line}\r\nOperator tail without newline"


def test_a_row_missing_a_line_is_left_for_that_line_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    key = "runtime_contract_decision"
    (first_old, _first_new), (second_old, second_new) = _EDITS[key]
    edited = _prior_default(key).replace(first_old + "\n", "- An operator's own rule.\n")
    assert first_old not in edited
    db.set_setting(key, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    value, _category = _stored(key)
    assert value == edited.replace(second_old, second_new)
    assert "- An operator's own rule." in value
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1
    assert key in warned[0] and first_old in warned[0]


def test_a_row_equal_to_the_current_default_is_skipped_quietly(caplog: pytest.LogCaptureFixture) -> None:
    before = {key: _stored(key) for key in _EDITS}
    assert all(value == load_default_prompt(key) for key, (value, _category) in before.items())
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert {key: _stored(key) for key in _EDITS} == before
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_the_second_run_is_a_no_op() -> None:
    # init_db already recorded the marker; a later pass must not touch a row.
    key = "system_prompt_template"
    prior = _prior_default(key)
    db.set_setting(key, prior, "advanced")
    settings_db.reconcile_memory_prompt_lines()
    assert _stored(key)[0] == prior
    _rerun()
    applied = _stored(key)[0]
    assert applied == load_default_prompt(key)
    db.set_setting(key, prior, "advanced")
    settings_db.reconcile_memory_prompt_lines()
    assert _stored(key)[0] == prior
