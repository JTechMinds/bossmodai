"""Settings → System Settings → Context Window: the work-context settings.

The four ``work_snapshot_*`` settings are stored under ``simulation``
(db/settings.py) and shown on the Context Window tab after the
conversation-memory settings. Each is checked at the write boundary, with
the Settings labels, by the same rules ``work_summary.read_summary_limits``
enforces, so no saved value can stop an execution turn.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.work_summary import read_summary_limits
from tests.test_system_ai_compaction_settings import (
    _by_key,
    _put_setting,
    _render_system_settings,
    _reset_setting,
)

# (key, Settings label, seeded default), in the order the tab lists them.
WORK_SETTINGS = (
    ("work_snapshot_max_chars", "Work budget (characters)", "120000"),
    ("work_snapshot_trim_percent", "Work kept after summarizing (% of work budget)", "50"),
    ("work_snapshot_summary_percent", "Work summary size (% of work budget)", "10"),
    ("work_snapshot_chat_view_max_chars", "Work shown when you ask (characters)", "24000"),
)
_WORK_KEYS = [key for key, _label, _default in WORK_SETTINGS]


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


# ─── rendering ───


def test_context_window_lists_the_work_settings_after_the_conversation_settings() -> None:
    payload = _render_system_settings()
    order = [row["key"] for row in payload["context"]]
    assert order[-5:] == ["conversation_history_ceiling_percent", *_WORK_KEYS]
    rows = _by_key(payload["context"])
    for key, label, default in WORK_SETTINGS:
        row = rows[key]
        assert row["label"] == label
        assert row["value"] == default
        assert row["category"] == "simulation", "the row keeps its stored category"
        description = row["paragraphs"][0]
        assert description.endswith(f"Default {default}."), key
        assert "restart" not in description.lower(), "a saved value applies from the next turn"
    # The Simulation tab, where their stored category would have put them, does not list them.
    assert "tick_interval" in payload["simulationOrder"]
    assert not set(_WORK_KEYS) & set(payload["simulationOrder"])


def test_a_refused_work_save_shows_the_server_message_on_its_row() -> None:
    refusal = _render_system_settings()["workRefusal"]
    assert refusal["saves"] == [
        {"url": "/api/settings/work_snapshot_summary_percent?value=50&category=simulation", "method": "PUT"},
    ]
    assert refusal["error"] == {
        "text": "Work summary size (50%) must be lower than Work kept after summarizing (50%).",
        "role": "alert",
    }
    assert refusal["otherError"] == {"text": "", "role": "alert"}


# ─── the character limits ───


@pytest.mark.parametrize(
    ("key", "label"),
    [("work_snapshot_max_chars", "Work budget"), ("work_snapshot_chat_view_max_chars", "Work shown when you ask")],
)
def test_the_work_character_limits_are_whole_numbers_of_at_least_1(key: str, label: str) -> None:
    before = config.get(key)
    for bad in ("0", "-1", "120k", ""):
        res = _put_setting(key, bad, "simulation")
        assert res.status_code == 400, bad
        assert res.json()["detail"] == f"{label} must be a whole number of at least 1."
        config.reload()
        assert config.get(key) == before
    res = _put_setting(key, "400000", "simulation")
    assert res.status_code == 200, res.text
    assert config.require_int(key) == 400000


# ─── the percents and the cross-key rules ───


_TRIM_RANGE = "Work kept after summarizing must be a whole number from 1 to 99."
_SUMMARY_RANGE = "Work summary size must be a whole number from 1 to 99."


@pytest.mark.parametrize(
    ("earlier", "key", "bad", "detail"),
    [
        ([], "work_snapshot_trim_percent", "0", _TRIM_RANGE),
        ([], "work_snapshot_trim_percent", "100", _TRIM_RANGE),
        ([], "work_snapshot_summary_percent", "0", _SUMMARY_RANGE),
        # Against the seeded trim (50) and summary (10).
        (
            [],
            "work_snapshot_summary_percent",
            "50",
            "Work summary size (50%) must be lower than Work kept after summarizing (50%).",
        ),
        (
            [],
            "work_snapshot_trim_percent",
            "10",
            "Work summary size (10%) must be lower than Work kept after summarizing (10%).",
        ),
        (
            [("work_snapshot_summary_percent", "1")],
            "work_snapshot_max_chars",
            "50",
            "Work budget (50) is too small for Work summary size (1%): the summary would have no room.",
        ),
    ],
)
def test_work_values_out_of_range_out_of_order_or_without_room_are_refused(
    earlier: list[tuple[str, str]], key: str, bad: str, detail: str,
) -> None:
    for earlier_key, value in earlier:
        assert _put_setting(earlier_key, value, "simulation").status_code == 200
    before = config.get(key)
    res = _put_setting(key, bad, "simulation")
    assert res.status_code == 400
    assert res.json()["detail"] == detail
    config.reload()
    assert config.get(key) == before, "a refused value is never stored"


def test_a_consistent_work_set_is_saved_and_read_by_the_summary() -> None:
    for key, value in (
        ("work_snapshot_trim_percent", "75"),
        ("work_snapshot_summary_percent", "20"),
        ("work_snapshot_max_chars", "400000"),
    ):
        res = _put_setting(key, value, "simulation")
        assert res.status_code == 200, res.text
        assert res.json()["category"] == "simulation"
    limits = read_summary_limits()
    assert (limits.budget, limits.target, limits.cap) == (400000, 300000, 80000)


def test_each_work_setting_resets_to_its_default() -> None:
    for key, value in (
        ("work_snapshot_trim_percent", "75"),
        ("work_snapshot_summary_percent", "20"),
        ("work_snapshot_max_chars", "400000"),
        ("work_snapshot_chat_view_max_chars", "30000"),
    ):
        assert _put_setting(key, value, "simulation").status_code == 200
    for key, _label, default in WORK_SETTINGS:
        res = _reset_setting(key)
        assert res.status_code == 200, res.text
        assert res.json()["value"] == default
    config.reload()
    for key, _label, default in WORK_SETTINGS:
        assert config.get(key) == default
