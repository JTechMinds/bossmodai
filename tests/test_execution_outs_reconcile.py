"""reconcile_execution_outs_prompt_line: the stored execution contract learns the outs shape.

The execution contract's ``assign`` line used to say only "data.task.outs
optional", so execution turns guessed the item shape. Prompt rows are seeded
once and never overwritten, so the line swap reaches an existing database only
through this line-level, marker-guarded pass.
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

_MARKER = "execution_outs_prompt_line_reconciled"
_KEY = "runtime_contract_execution"
_EDITS = settings_db._EXECUTION_OUTS_PROMPT_LINE_EDITS


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
    """The shipped default as it was before the outs edit: the new line swapped back."""
    text = load_default_prompt(key)
    for old_line, new_line in _EDITS[key]:
        assert text.count(new_line) == 1, (key, new_line)
        text = text.replace(new_line, old_line)
    return text


def _rerun() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    settings_db.reconcile_execution_outs_prompt_line()


def test_only_the_execution_contract_is_edited() -> None:
    assert set(_EDITS) == {_KEY}


def test_the_shipped_file_carries_the_new_line_and_not_the_old() -> None:
    lines = prompt_file_path(_KEY).read_text(encoding="utf-8").splitlines()
    for old_line, new_line in _EDITS[_KEY]:
        assert lines.count(new_line) == 1, new_line
        assert old_line not in lines, old_line
        assert "file deliverables only" in new_line


def test_an_untouched_row_gets_the_new_line_and_keeps_its_category(
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


def test_an_edited_row_without_the_old_line_is_left_alone_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ((old_line, _new_line),) = _EDITS[_KEY]
    edited = _prior_default(_KEY).replace(old_line + "\n", "  - assign: an operator's own rule\n")
    assert old_line not in edited
    db.set_setting(_KEY, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert _stored(_KEY)[0] == edited
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1
    assert _KEY in warned[0] and old_line in warned[0]
    assert "execution outs" in warned[0]


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
    settings_db.reconcile_execution_outs_prompt_line()
    assert _stored(_KEY)[0] == prior
    _rerun()
    assert _stored(_KEY)[0] == load_default_prompt(_KEY)
    db.set_setting(_KEY, prior, "advanced")
    settings_db.reconcile_execution_outs_prompt_line()
    assert _stored(_KEY)[0] == prior
