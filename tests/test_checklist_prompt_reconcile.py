"""reconcile_checklist_prompt_lines: the stored execution contract learns the checklist.

The execution contract gains three checklist rules after the ``taskmsg`` rule
and two examples after the ``taskmsg`` example. Prompt rows are seeded once
and never overwritten, so the lines reach an existing database only through
this line-level, marker-guarded pass, which keeps each anchor line and
inserts the new lines after it.
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
from core.prompting.runtime_prompt_lint import lint_runtime_prompts

_MARKER = "checklist_prompt_lines_reconciled"
_KEY = "runtime_contract_execution"
_EDITS = settings_db._CHECKLIST_PROMPT_LINE_EDITS


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
    """The shipped default as it was before the checklist lines: each insert taken back out."""
    text = load_default_prompt(key)
    for anchor, inserted in _EDITS[key]:
        assert text.count(inserted) == 1, (key, inserted)
        text = text.replace(inserted, anchor)
    return text


def _rerun() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    settings_db.reconcile_checklist_prompt_lines()


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_only_the_execution_contract_is_edited_and_each_edit_keeps_its_anchor() -> None:
    assert set(_EDITS) == {_KEY}
    for anchor, inserted in _EDITS[_KEY]:
        assert inserted.startswith(anchor + "\n")
        assert "checklist" in inserted[len(anchor):]


def test_the_shipped_file_carries_the_new_lines_exactly_once() -> None:
    text = prompt_file_path(_KEY).read_text(encoding="utf-8")
    lines = text.splitlines()
    for anchor, inserted in _EDITS[_KEY]:
        assert text.count(inserted) == 1, inserted
        assert lines.count(anchor) == 1, anchor
        for line in inserted.split("\n")[1:]:
            assert lines.count(line) == 1, line
    # The add example's \n is a literal backslash-n (a JSON escape in the
    # prompt text), never a line break inside the example.
    assert '"body":"Audit core/tasking\\nAudit db/tasks.py\\nWrite the findings summary"' in text
    assert _prior_default(_KEY).count("checklist") == 0


def test_an_untouched_row_gets_the_lines_and_then_equals_the_shipped_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _value, category = _stored(_KEY)
    db.set_setting(_KEY, _prior_default(_KEY), category)
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    value, stored_category = _stored(_KEY)
    assert value == load_default_prompt(_KEY)
    assert stored_category == settings_db.get_seed_setting_default(_KEY)[1]
    assert _warnings(caplog) == []
    assert _stored(_MARKER)[0] == "true"


def test_a_row_equal_to_the_current_default_is_skipped_quietly(caplog: pytest.LogCaptureFixture) -> None:
    before = _stored(_KEY)
    assert before[0] == load_default_prompt(_KEY)
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert _stored(_KEY) == before
    assert _warnings(caplog) == []


def test_an_edited_row_without_the_anchors_keeps_its_bytes_with_a_warning_each(
    caplog: pytest.LogCaptureFixture,
) -> None:
    (rule_anchor, _rules), (example_anchor, _examples) = _EDITS[_KEY]
    edited = (
        _prior_default(_KEY)
        .replace(rule_anchor + "\n", "  - an operator's own taskmsg rule\n")
        .replace(example_anchor + "\n", "  an operator's own taskmsg example\n")
    )
    assert rule_anchor not in edited and example_anchor not in edited
    db.set_setting(_KEY, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert _stored(_KEY)[0] == edited
    warned = _warnings(caplog)
    assert len(warned) == 2
    for anchor, message in zip((rule_anchor, example_anchor), warned):
        assert _KEY in message and anchor in message and "checklist" in message


def test_a_row_missing_one_anchor_still_gets_the_other_lines(caplog: pytest.LogCaptureFixture) -> None:
    (rule_anchor, _rules), (example_anchor, examples) = _EDITS[_KEY]
    edited = _prior_default(_KEY).replace(rule_anchor + "\n", "  - an operator's own taskmsg rule\n")
    db.set_setting(_KEY, edited, "advanced")
    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun()
    assert _stored(_KEY)[0] == edited.replace(example_anchor, examples)
    warned = _warnings(caplog)
    assert len(warned) == 1 and rule_anchor in warned[0]


def test_the_marker_makes_the_pass_run_once() -> None:
    # init_db already recorded the marker; a later pass must not touch a row.
    prior = _prior_default(_KEY)
    db.set_setting(_KEY, prior, "advanced")
    settings_db.reconcile_checklist_prompt_lines()
    assert _stored(_KEY)[0] == prior
    _rerun()
    assert _stored(_KEY)[0] == load_default_prompt(_KEY)
    db.set_setting(_KEY, prior, "advanced")
    settings_db.reconcile_checklist_prompt_lines()
    assert _stored(_KEY)[0] == prior


def test_the_prompt_lint_reports_no_errors_for_the_new_text() -> None:
    report = lint_runtime_prompts()
    errors = [
        issue for issue in report.issues
        if issue.severity == "error"
        and issue.surface_key in {_KEY, "internal_loop_execution_checklist_handoff"}
    ]
    assert errors == []
