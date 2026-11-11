"""The task-request reconcile pass: stored prompts learn that a task keeps the message it came from.

reconcile_task_request_prompt_lines: the trigger block shows a task's
request above its severity; the decision contract's accept shape says a plan
child names a teammate, its field notes say what ``desc`` and a child's
``desc`` are for, and its memo example names files; the execution contract
says an assign's ``desc`` is the teammate's whole brief, and its two assign
examples name files. Prompt rows are seeded once and never overwritten, so
the lines reach an existing database only through this line-level,
marker-guarded pass. Its insertions keep their anchor line.

reconcile_task_origin_prompt_lines runs right after it: three of this pass's
new lines (the brief, a child's brief, an assign's brief) are its old lines,
rewritten to say the conversation before the task is saved and shown too.
So the shipped files carry that pass's lines, and this pass's prior default
is the shipped file with the task-origin pass undone first, then this one.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

import db
import db.settings as settings_db
from core import config
from core.default_prompts import load_default_prompt
from core.prompting.runtime_prompt_lint import lint_runtime_prompts

_MARKER = "task_request_prompt_lines_reconciled"
_EDITS = settings_db._TASK_REQUEST_PROMPT_LINE_EDITS
_ORIGIN_MARKER = "task_origin_prompt_lines_reconciled"
_ORIGIN_EDITS = settings_db._TASK_ORIGIN_PROMPT_LINE_EDITS
_ORIGIN_KEYS = ("runtime_contract_decision", "runtime_contract_execution")
_KEYS = ("runtime_block_trigger_event", "runtime_contract_decision", "runtime_contract_execution")
# The pairs that insert lines before an anchor, which they keep as their last line.
_INSERTIONS = {
    ("runtime_block_trigger_event", 0): settings_db._TASK_REQUEST_SEVERITY_ANCHOR,
    ("runtime_contract_execution", 0): settings_db._TASK_REQUEST_ASSIGN_AFTER_ANCHOR,
}


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


def _undo(text: str, pairs: tuple[tuple[str, str], ...], key: str) -> str:
    """``text`` with each pair's new line taken back to its old one."""
    for old_line, new_line in pairs:
        assert text.count(new_line) == 1, (key, new_line)
        text = text.replace(new_line, old_line)
    return text


def _request_pass_default(key: str) -> str:
    """The shipped default as this pass left it: the task-origin pass's lines taken back."""
    return _undo(load_default_prompt(key), _ORIGIN_EDITS.get(key, ()), key)


def _prior_default(key: str) -> str:
    """The shipped default as it was before this pass's lines: the task-origin pass undone, then this one."""
    return _undo(_request_pass_default(key), _EDITS[key], key)


def _rerun() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    settings_db.reconcile_task_request_prompt_lines()


def _rerun_origin() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_ORIGIN_MARKER])
    settings_db.reconcile_task_origin_prompt_lines()


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_three_prompts_are_edited_and_each_insertion_keeps_its_anchor() -> None:
    assert set(_EDITS) == set(_KEYS)
    assert [len(_EDITS[key]) for key in _KEYS] == [1, 3, 3]
    for (key, index), anchor in _INSERTIONS.items():
        old_line, new_line = _EDITS[key][index]
        assert old_line == anchor
        assert new_line.endswith("\n" + anchor)


def test_the_shipped_prompts_carry_each_new_line_once_and_none_of_the_old() -> None:
    # Read as this pass left them: the task-origin pass rewrote three of its lines since.
    for key in _KEYS:
        lines = _request_pass_default(key).split("\n")
        for old_line, new_line in _EDITS[key]:
            for line in new_line.split("\n"):
                assert lines.count(line) == 1, (key, line)
            if (key, _EDITS[key].index((old_line, new_line))) not in _INSERTIONS:
                assert old_line not in lines, (key, old_line)
        assert _prior_default(key) != load_default_prompt(key)


def test_an_untouched_row_gets_the_lines_and_then_equals_the_shipped_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    categories = {}
    for key in _KEYS:
        _value, categories[key] = _stored(key)
        db.set_setting(key, _prior_default(key), categories[key])
    # Both passes, in seed_defaults() order: this one, then the task-origin pass.
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
        _rerun_origin()
    for key in _KEYS:
        value, category = _stored(key)
        assert value == load_default_prompt(key), key
        assert category == categories[key] == settings_db.get_seed_setting_default(key)[1]
    assert _warnings(caplog) == []
    assert _stored(_MARKER)[0] == "true"
    assert _stored(_ORIGIN_MARKER)[0] == "true"


def test_a_row_equal_to_the_current_default_is_skipped_quietly(caplog: pytest.LogCaptureFixture) -> None:
    before = {key: _stored(key) for key in _KEYS}
    assert all(value == load_default_prompt(key) for key, (value, _category) in before.items())
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert {key: _stored(key) for key in _KEYS} == before
    assert _warnings(caplog) == []


def test_an_edited_row_without_the_old_lines_keeps_its_bytes_with_a_warning_each(
    caplog: pytest.LogCaptureFixture,
) -> None:
    key = "runtime_contract_decision"
    edited = _prior_default(key)
    for index, (old_line, _new_line) in enumerate(_EDITS[key]):
        edited = edited.replace(old_line + "\n", f"an operator's own line {index}\n")
    assert all(old_line not in edited for old_line, _new_line in _EDITS[key])
    db.set_setting(key, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert _stored(key)[0] == edited
    warned = _warnings(caplog)
    assert len(warned) == 3
    for (old_line, _new_line), message in zip(_EDITS[key], warned):
        # The warning names the line with %r, so a quote in it is escaped.
        assert key in message and repr(old_line) in message and "task request" in message


def test_a_row_missing_one_old_line_still_gets_the_others(caplog: pytest.LogCaptureFixture) -> None:
    key = "runtime_contract_execution"
    (anchor, inserted), (first_old, first_new), (second_old, second_new) = _EDITS[key]
    edited = _prior_default(key).replace(anchor + "\n", "  - an operator's own after rule\n")
    db.set_setting(key, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert _stored(key)[0] == edited.replace(first_old, first_new).replace(second_old, second_new)
    warned = _warnings(caplog)
    assert len(warned) == 1 and repr(anchor) in warned[0]


def test_the_marker_makes_the_pass_run_once() -> None:
    # init_db already recorded the marker; a later pass must not touch a row.
    key = "runtime_block_trigger_event"
    prior = _prior_default(key)
    db.set_setting(key, prior, "advanced")
    settings_db.reconcile_task_request_prompt_lines()
    assert _stored(key)[0] == prior
    _rerun()
    assert _stored(key)[0] == load_default_prompt(key)
    db.set_setting(key, prior, "advanced")
    settings_db.reconcile_task_request_prompt_lines()
    assert _stored(key)[0] == prior


def test_the_prompt_lint_reports_no_errors_for_the_new_text() -> None:
    report = lint_runtime_prompts()
    errors = [issue for issue in report.issues if issue.severity == "error" and issue.surface_key in _KEYS]
    assert errors == []


# ─── the task-origin pass (reconcile_task_origin_prompt_lines) ───


def test_the_task_origin_pass_rewrites_three_lines_the_request_pass_wrote() -> None:
    assert set(_ORIGIN_EDITS) == set(_ORIGIN_KEYS)
    assert [len(_ORIGIN_EDITS[key]) for key in _ORIGIN_KEYS] == [2, 1]
    for key in _ORIGIN_KEYS:
        written = [line for _old, new in _EDITS[key] for line in new.split("\n")]
        for old_line, new_line in _ORIGIN_EDITS[key]:
            assert old_line in written, (key, old_line)
            assert "\n" not in new_line and new_line != old_line


def test_the_shipped_prompts_carry_each_task_origin_line_once_and_none_of_its_old() -> None:
    for key in _ORIGIN_KEYS:
        lines = load_default_prompt(key).split("\n")
        for old_line, new_line in _ORIGIN_EDITS[key]:
            assert lines.count(new_line) == 1, (key, new_line)
            assert old_line not in lines, (key, old_line)


def test_a_row_with_the_request_pass_lines_gets_all_three_and_equals_the_shipped_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    categories = {}
    for key in _ORIGIN_KEYS:
        _value, categories[key] = _stored(key)
        db.set_setting(key, _request_pass_default(key), categories[key])
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun_origin()
    for key in _ORIGIN_KEYS:
        value, category = _stored(key)
        assert value == load_default_prompt(key), key
        assert category == categories[key]
    assert _warnings(caplog) == []
    assert _stored(_ORIGIN_MARKER)[0] == "true"


def test_a_row_missing_a_task_origin_line_warns_and_keeps_its_bytes(caplog: pytest.LogCaptureFixture) -> None:
    key = "runtime_contract_decision"
    (brief_old, brief_new), (child_old, child_new) = _ORIGIN_EDITS[key]
    edited = _request_pass_default(key).replace(brief_old + "\n", "- an operator's own brief rule\n")
    db.set_setting(key, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun_origin()
    assert _stored(key)[0] == edited.replace(child_old, child_new)
    warned = _warnings(caplog)
    assert len(warned) == 1
    # The warning names the line with %r, so a quote in it is escaped.
    assert key in warned[0] and repr(brief_old) in warned[0] and "task origin" in warned[0]


def test_the_task_origin_marker_makes_the_pass_run_once() -> None:
    # init_db already recorded the marker; a later pass must not touch a row.
    key = "runtime_contract_execution"
    prior = _request_pass_default(key)
    db.set_setting(key, prior, "advanced")
    settings_db.reconcile_task_origin_prompt_lines()
    assert _stored(key)[0] == prior
    _rerun_origin()
    assert _stored(key)[0] == load_default_prompt(key)
    db.set_setting(key, prior, "advanced")
    settings_db.reconcile_task_origin_prompt_lines()
    assert _stored(key)[0] == prior
