"""Settings → System Settings → Context Window: the work-context settings.

Work % and When you ask % are shares of the usable window (stored under
``context``); the two fine-tuning percents, Work kept after summarizing and
Work summary size, are shares of Work, stored under ``simulation`` and shown
in Work mode's fine-tuning. Each write is checked at the boundary by the same
rules ``core.context_budget.check_shares`` enforces at run time, with the
Settings labels, so no saved value can stop an execution turn.
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

# (key, Settings label, seeded default, stored category).
WORK_SETTINGS = (
    ("context_work_percent", "Work (% of usable window)", "60", "context"),
    ("context_asked_percent", "When you ask (% of usable window)", "25", "context"),
    ("work_snapshot_trim_percent", "Work kept after summarizing (% of work)", "60", "simulation"),
    ("work_snapshot_summary_percent", "Work summary size (% of work)", "20", "simulation"),
)
_FINE_TUNING = ["work_snapshot_trim_percent", "work_snapshot_summary_percent"]


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


def test_the_work_settings_sit_in_work_mode_with_their_stored_category() -> None:
    payload = _render_system_settings()
    slots = [slot["key"] for slot in payload["panel"]["slots"]]
    # Work mode: Task log, then Work and its fine-tuning.
    work = slots.index("context_work_percent")
    assert slots[work - 1] == "context_task_log_percent"
    assert slots[work + 1:work + 3] == _FINE_TUNING
    assert payload["panel"]["summaries"][-1] == "Summarizing: keep 60%, summary 20%"
    rows = _by_key(payload["context"])
    for key, label, default, category in WORK_SETTINGS:
        row = rows[key]
        assert row["label"] == label
        assert row["value"] == default
        assert row["category"] == category, "the row keeps its stored category"
        description = row["paragraphs"][0]
        assert description.endswith(f"Default {default}."), key
        assert "restart" not in description.lower(), "a saved value applies from the next turn"
    # The Simulation tab, where their stored category would have put them, does not list them.
    assert "tick_interval" in payload["simulationOrder"]
    assert not set(_FINE_TUNING) & set(payload["simulationOrder"])


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


# ─── the shares of the usable window ───


def test_a_work_share_that_lets_a_work_turn_outgrow_the_window_is_refused() -> None:
    res = _put_setting("context_work_percent", "85", "context")
    assert res.status_code == 400
    assert res.json()["detail"] == (
        "A work turn could use 107% of the usable window (Reply 12% + Task log 5% + Work 85% + "
        "Largest result 5%). Lower one so the total is 100% or less."
    )
    config.reload()
    assert config.get("context_work_percent") == "60", "a refused value is never stored"


def test_when_you_ask_must_hold_the_whole_work_summary() -> None:
    res = _put_setting("context_asked_percent", "11", "context")
    assert res.status_code == 400
    assert res.json()["detail"].startswith("When you ask (11%) must be at least 12% (Work 60% × Work summary size 20%)")
    assert _put_setting("context_asked_percent", "12", "context").status_code == 200


# ─── the percents and the cross-key rules ───


_TRIM_RANGE = "Work kept after summarizing must be a whole number from 1 to 99."
_SUMMARY_RANGE = "Work summary size must be a whole number from 1 to 99."


@pytest.mark.parametrize(
    ("earlier", "key", "bad", "detail"),
    [
        ([], "work_snapshot_trim_percent", "0", _TRIM_RANGE),
        ([], "work_snapshot_trim_percent", "100", _TRIM_RANGE),
        ([], "work_snapshot_summary_percent", "0", _SUMMARY_RANGE),
        ([], "work_snapshot_summary_percent", "-1", "Work summary size must be a whole number."),
        # Against the seeded trim (60) and summary (20).
        (
            [],
            "work_snapshot_summary_percent",
            "60",
            "Work summary size (60%) must be lower than Work kept after summarizing (60%).",
        ),
        (
            [],
            "work_snapshot_trim_percent",
            "20",
            "Work summary size (20%) must be lower than Work kept after summarizing (20%).",
        ),
        (
            [("work_snapshot_summary_percent", "1")],
            "context_window_tokens",
            "12100",
            "Work summary size (1%) leaves the work summary no room: "
            "it would be under 1 token of a 100-token usable window.",
        ),
    ],
)
def test_work_values_out_of_range_out_of_order_or_without_room_are_refused(
    earlier: list[tuple[str, str]], key: str, bad: str, detail: str,
) -> None:
    for earlier_key, value in earlier:
        assert _put_setting(earlier_key, value, "simulation").status_code == 200
    before = config.get(key)
    res = _put_setting(key, bad, "simulation" if key.startswith("work_snapshot") else "context")
    assert res.status_code == 400
    assert res.json()["detail"] == detail
    config.reload()
    assert config.get(key) == before, "a refused value is never stored"


def test_a_consistent_work_set_is_saved_and_read_by_the_summary() -> None:
    for key, value in (
        ("work_snapshot_trim_percent", "75"),
        ("work_snapshot_summary_percent", "20"),
    ):
        res = _put_setting(key, value, "simulation")
        assert res.status_code == 200, res.text
        assert res.json()["category"] == "simulation"
    assert _put_setting("context_work_percent", "70", "context").status_code == 200
    limits = read_summary_limits()
    # 70% of Medium's 116,000 usable tokens.
    assert (limits.budget, limits.target, limits.cap) == (81_200, 60_900, 16_240)


def test_each_work_setting_resets_to_its_default() -> None:
    for key, value in (
        ("work_snapshot_trim_percent", "75"),
        ("work_snapshot_summary_percent", "15"),
        ("context_work_percent", "70"),
        ("context_asked_percent", "30"),
    ):
        category = "simulation" if key.startswith("work_snapshot") else "context"
        assert _put_setting(key, value, category).status_code == 200, key
    for key, _label, default, _category in WORK_SETTINGS:
        res = _reset_setting(key)
        assert res.status_code == 200, res.text
        assert res.json()["value"] == default
    config.reload()
    for key, _label, default, _category in WORK_SETTINGS:
        assert config.get(key) == default
