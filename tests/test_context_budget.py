"""core/context_budget.py: one context window in tokens, its rules, presets and the result cap.

The domain functions are pure (except the two readers); the API section runs
the Settings routes against the isolated test database (conftest.py).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.context_budget import (
    CONTEXT_PRESETS,
    PRESET_SETTINGS,
    SHARE_SETTINGS,
    ContextShares,
    active_preset,
    check_shares,
    compute_budget,
    find_preset,
    fit_command_result,
    read_context_budget,
    read_shares,
)
from core.llm.client import count_text_tokens
from db.crud import query, query_one
from db.settings import get_seed_setting_default

_OLD_KEYS = (
    "work_snapshot_max_chars",
    "work_snapshot_chat_view_max_chars",
    "conversation_history_budget_tokens",
    "default_max_tokens",
    "cli_max_read_chars",
)


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


def _shares(preset_id: str, **changes: int) -> ContextShares:
    """A preset's shares, with some fields changed."""
    values = {key: value for key, value in find_preset(preset_id).values.items() if key in SHARE_SETTINGS}
    shares = read_shares(values)
    return ContextShares(**{**{name: getattr(shares, name) for name in shares.__slots__}, **changes})


# ─── compute_budget: the D3 numbers ───


@pytest.mark.parametrize(
    ("preset_id", "expected"),
    [
        ("small", (32_000, 12_000, 20_000, 3_000, 1_000, 3_000, 3_750, 5_000, 1_000, 11_000, 24_750, 28_000)),
        ("medium", (128_000, 12_000, 116_000, 13_920, 5_800, 23_200, 29_000, 29_000, 5_800, 69_600, 89_720, 107_120)),
        ("large", (256_000, 12_000, 244_000, 29_280, 12_200, 48_800, 61_000, 61_000, 12_200, 146_400, 175_480, 212_080)),
    ],
)
def test_each_preset_computes_the_planned_numbers(preset_id: str, expected: tuple[int, ...]) -> None:
    budget = compute_budget(_shares(preset_id))
    assert (
        budget.window, budget.fixed, budget.usable, budget.reply, budget.result, budget.history,
        budget.history_cap, budget.asked, budget.task_log, budget.work, budget.worst_chat_turn,
        budget.worst_work_turn,
    ) == expected


def test_compute_budget_refuses_a_window_with_no_usable_room() -> None:
    with pytest.raises(ValueError, match="must be larger than Fixed prompt room"):
        compute_budget(_shares("medium", window=12_000))


# ─── check_shares: the never-too-big rule ───


def test_every_preset_passes_every_rule() -> None:
    for preset in CONTEXT_PRESETS:
        assert check_shares(_shares(preset.id)) == [], preset.id


def test_every_preset_keeps_the_conversation_memory_order() -> None:
    for preset in CONTEXT_PRESETS:
        start = preset.values["conversation_compaction_start_percent"]
        keep = preset.values["conversation_compaction_keep_percent"]
        notes = preset.values["conversation_notes_max_percent"]
        assert 1 <= notes < keep < start <= 100, preset.id
        assert set(preset.values) == set(PRESET_SETTINGS), preset.id


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"window": 12_000}, "Context window (12,000) must be larger than Fixed prompt room (12,000)."),
        ({"fixed": 0}, "Fixed prompt room must be a whole number of at least 1."),
        ({"reply": 0}, "Reply must be a whole number from 1 to 99."),
        ({"result": 100}, "Largest command result must be a whole number from 1 to 99."),
        ({"history_ceiling": 99}, "Hard limit must be a whole number from 100 to 300."),
        ({"work_trim": 100}, "Work kept after summarizing must be a whole number from 1 to 99."),
        ({"work_summary": 60}, "Work summary size (60%) must be lower than Work kept after summarizing (60%)."),
        (
            {"history": 50},
            "A chat turn could use 104.5% of the usable window (Reply 12% + History up to 62.5% + "
            "When you ask 25% + Largest result 5%). Lower one so the total is 100% or less.",
        ),
        (
            {"work": 85},
            "A work turn could use 107% of the usable window (Reply 12% + Task log 5% + Work 85% + "
            "Largest result 5%). Lower one so the total is 100% or less.",
        ),
        (
            {"asked": 11},
            "When you ask (11%) must be at least 12% (Work 60% × Work summary size 20%), "
            "so an agent's whole summary fits when you message it.",
        ),
        (
            {"window": 12_009, "work_summary": 1},
            "Work summary size (1%) leaves the work summary no room: it would be under 1 token of a 9-token usable window.",
        ),
    ],
)
def test_each_rule_names_the_settings_it_breaks(changes: dict[str, int], problem: str) -> None:
    assert problem in check_shares(_shares("medium", **changes))


def test_a_total_of_exactly_100_percent_is_allowed() -> None:
    assert check_shares(_shares("medium", work=78)) == []  # 12 + 5 + 78 + 5 = 100


# ─── reading the stored settings ───


def test_the_seeds_are_the_medium_preset() -> None:
    medium = find_preset("medium")
    for key in PRESET_SETTINGS:
        seeded = get_seed_setting_default(key)
        assert seeded is not None and seeded[0] == str(medium.values[key]), key
    assert read_context_budget() == compute_budget(_shares("medium"))


def test_read_shares_takes_overrides_and_refuses_unknown_keys() -> None:
    assert read_shares({"context_work_percent": 70}).work == 70
    with pytest.raises(ValueError, match="conversation_compaction_start_percent"):
        read_shares({"conversation_compaction_start_percent": 70})


def test_invalid_stored_settings_raise_with_every_problem() -> None:
    db.set_setting("context_work_percent", "95", "context")  # 0 + 5 + 95 + 5 = 105
    db.set_setting("context_reply_percent", "0", "context")
    config.reload()
    with pytest.raises(config.ConfigError) as excinfo:
        read_context_budget()
    message = str(excinfo.value)
    assert "Reply must be a whole number from 1 to 99." in message
    assert "A work turn could use" in message


def test_a_missing_setting_raises_naming_it() -> None:
    db.execute("DELETE FROM settings WHERE key = 'context_window_tokens'")
    config.reload()
    with pytest.raises(config.ConfigError, match="context_window_tokens"):
        read_context_budget()


# ─── presets ───


def test_find_preset_and_active_preset() -> None:
    assert find_preset("large").label == "Large (256K)"
    with pytest.raises(KeyError):
        find_preset("huge")
    values = dict(find_preset("large").values)
    assert active_preset(values) == "large"
    values["context_work_percent"] = 59
    assert active_preset(values) is None, "one edit makes it Custom"
    assert active_preset({}) is None


# ─── fit_command_result ───


def test_a_result_that_fits_is_unchanged() -> None:
    text = "BOSSMOD CLI RESULT\ncommand: status\n\nOUTPUT:\nall good\n"
    assert fit_command_result(text, limit_tokens=count_text_tokens(text)) == text


def test_a_long_result_keeps_whole_leading_lines_and_says_how_much_was_cut() -> None:
    lines = [f"line {index}: the build step printed a long and fairly boring message" for index in range(200)]
    text = "\n".join(lines)
    total = count_text_tokens(text)

    fitted = fit_command_result(text, limit_tokens=300)

    body, note = fitted.rsplit("\n\n", 1)
    assert count_text_tokens(fitted) <= 300
    kept = body.split("\n")
    assert kept == lines[: len(kept)], "only whole leading lines are kept"
    assert 1 < len(kept) < 200
    cut = total - count_text_tokens(body)
    assert note == (
        f"(About {cut:,} more tokens of this output were cut to fit the agent's context. "
        "Narrow the command, or read a file in parts with rr.)"
    )


def test_an_oversized_first_line_keeps_its_longest_fitting_prefix() -> None:
    text = "x=1;" * 5000 + "\nsecond line"

    fitted = fit_command_result(text, limit_tokens=200)

    body, note = fitted.rsplit("\n\n", 1)
    assert count_text_tokens(fitted) <= 200
    assert body and text.startswith(body) and "\n" not in body
    assert note.startswith("(About ") and note.endswith("read a file in parts with rr.)")


def test_a_limit_that_cannot_hold_the_note_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot hold the note"):
        fit_command_result("word " * 500, limit_tokens=10)


# ─── API ───


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def test_the_budget_route_returns_the_numbers_and_the_presets() -> None:
    client, headers = _client()

    res = client.get("/api/settings/context-budget", headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert {key: body[key] for key in (
        "window", "fixed", "usable", "reply", "result", "history", "history_cap", "asked", "task_log", "work",
        "worst_chat_turn", "worst_work_turn", "worst_chat_percent", "worst_work_percent",
    )} == {
        "window": 128_000, "fixed": 12_000, "usable": 116_000, "reply": 13_920, "result": 5_800,
        "history": 23_200, "history_cap": 29_000, "asked": 29_000, "task_log": 5_800, "work": 69_600,
        "worst_chat_turn": 89_720, "worst_work_turn": 107_120, "worst_chat_percent": 70, "worst_work_percent": 84,
    }
    assert body["active_preset"] == "medium"
    assert body["presets"] == [
        {"id": "small", "label": "Small (32K)"},
        {"id": "medium", "label": "Medium (128K)"},
        {"id": "large", "label": "Large (256K)"},
    ]
    assert body["values"] == dict(find_preset("medium").values)


def test_the_large_preset_writes_every_value_and_becomes_active() -> None:
    client, headers = _client()

    res = client.post("/api/settings/context-presets/large", headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["active_preset"] == "large"
    assert (body["worst_chat_turn"], body["worst_chat_percent"]) == (175_480, 69)
    assert (body["worst_work_turn"], body["worst_work_percent"]) == (212_080, 83)
    config.reload()
    for key, value in find_preset("large").values.items():
        assert config.require_int(key) == value, key
    categories = {row["key"]: row["category"] for row in query("SELECT key, category FROM settings")}
    assert categories["work_snapshot_trim_percent"] == "simulation", "each row keeps its category"
    assert categories["context_window_tokens"] == "context"

    # Every value is an ordinary setting afterwards; one edit makes it Custom.
    edit = client.put(
        "/api/settings/context_work_percent", params={"value": "59", "category": "context"}, headers=headers,
    )
    assert edit.status_code == 200, edit.text
    assert client.get("/api/settings/context-budget", headers=headers).json()["active_preset"] is None


def test_an_unknown_preset_is_404() -> None:
    client, headers = _client()
    res = client.post("/api/settings/context-presets/huge", headers=headers)
    assert res.status_code == 404
    assert config.require_int("context_window_tokens") == 128_000


def test_a_put_that_makes_the_work_total_101_percent_is_refused() -> None:
    client, headers = _client()

    res = client.put(
        "/api/settings/context_work_percent", params={"value": "79", "category": "context"}, headers=headers,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == (
        "A work turn could use 101% of the usable window (Reply 12% + Task log 5% + Work 79% + "
        "Largest result 5%). Lower one so the total is 100% or less."
    )
    config.reload()
    assert config.require_int("context_work_percent") == 60


def test_the_old_budget_keys_are_pruned() -> None:
    for key in _OLD_KEYS:
        db.execute("INSERT INTO settings (key, value, category) VALUES ($1, '1', 'context')", [key])
    db.close_connection()
    db.init_db()
    for key in _OLD_KEYS:
        assert query_one("SELECT key FROM settings WHERE key = $1", [key]) is None, key
        assert get_seed_setting_default(key) is None, key
        with pytest.raises(ValueError, match="obsolete"):
            db.set_setting(key, "1", "context")


def test_a_character_sized_overflow_table_is_dropped_and_recreated() -> None:
    db.execute("DROP TABLE work_summary_overflows")
    db.execute(
        "CREATE TABLE work_summary_overflows (agent_id VARCHAR PRIMARY KEY, model VARCHAR NOT NULL, "
        "summary_chars INTEGER NOT NULL, limit_chars INTEGER NOT NULL, budget_chars INTEGER NOT NULL, "
        "created_at TIMESTAMP NOT NULL)"
    )
    db.execute(
        "INSERT INTO work_summary_overflows VALUES ('agent-1', 'm', 49587, 35000, 350000, current_timestamp)"
    )
    db.close_connection()

    db.init_db()

    columns = {row["name"] for row in query("PRAGMA table_info(work_summary_overflows)")}
    assert columns == {"agent_id", "model", "summary_tokens", "limit_tokens", "budget_tokens", "created_at"}
    assert db.list_work_summary_overflows() == [], "the character-sized rows went with the table"

    # A table already in tokens is left alone, rows included.
    agent = db.create_agent("Ada", role="Eng")
    db.record_work_summary_overflow(agent.id, model="m", summary_tokens=40_000, limit_tokens=29_280, budget_tokens=146_400)
    db.close_connection()
    db.init_db()
    assert [row.summary_tokens for row in db.list_work_summary_overflows()] == [40_000]


# ─── UI: settings-context-panel.js on its own ───


def _render_panel() -> dict:
    """Run the panel harness on the real payloads: Medium, and Medium with one edit (Custom)."""
    import json
    import subprocess

    client, headers = _client()
    medium = client.get("/api/settings/context-budget", headers=headers).json()
    edit = client.put(
        "/api/settings/context_work_percent", params={"value": "59", "category": "context"}, headers=headers,
    )
    assert edit.status_code == 200, edit.text
    custom = client.get("/api/settings/context-budget", headers=headers).json()
    root = Path(__file__).resolve().parents[1]
    js = root / "ui" / "static" / "js"
    result = subprocess.run(
        [
            "node",
            str(Path(__file__).resolve().parent / "js_context_panel_harness.cjs"),
            str(js / "core" / "format.js"),
            str(js / "settings" / "settings-context-panel.js"),
            json.dumps({"medium": medium, "custom": custom}),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_panel_lays_out_both_modes_with_every_slot_and_number() -> None:
    medium = _render_panel()["medium"]
    assert medium["slots"] == [
        "context_window_tokens", "context_fixed_tokens", "context_reply_percent", "context_result_percent",
        "context_history_percent", "conversation_compaction_enabled", "conversation_compaction_start_percent",
        "conversation_compaction_keep_percent", "conversation_notes_max_percent",
        "conversation_history_ceiling_percent", "context_asked_percent", "context_task_log_percent",
        "context_work_percent", "work_snapshot_trim_percent", "work_snapshot_summary_percent",
        "context_recent_work_artifacts", "context_recent_completed_tasks", "standing_prefs_line_max_chars",
        "standing_prefs_section_max_chars",
    ]
    assert medium["derived"] == [
        "= 13,920 tokens", "= 5,800 tokens", "= 23,200 tokens (up to 29,000)", "= 29,000 tokens",
        "= 5,800 tokens", "= 69,600 tokens",
    ]
    assert len(medium["bars"]) == 2
    assert medium["bars"][0].startswith("A chat turn at its largest, out of a 128,000-token window:")
    assert medium["bars"][1].endswith("Largest result 5,800, Free 20,880 tokens.")
    assert medium["details"] == 2 and len(medium["summaries"]) == 2
    assert medium["alert"] == 1, "the starting points have their own alert line"


def test_the_panel_marks_the_active_preset_or_shows_custom() -> None:
    panel = _render_panel()
    assert panel["medium"]["pressed"] == ["false", "true", "false"]
    assert panel["medium"]["custom"] == 0
    assert panel["custom"]["pressed"] == ["false", "false", "false"]
    assert panel["custom"]["custom"] == 1
    assert panel["custom"]["derived"][-1] == "= 68,440 tokens", "Work 59% of 116,000"


def test_the_panel_escapes_what_it_shows() -> None:
    hostile = _render_panel()["hostile"]
    assert hostile == {"rawQuote": False, "rawTag": False, "escapedTag": True}


def test_a_starting_point_click_posts_and_a_refusal_is_shown() -> None:
    panel = _render_panel()
    accepted = panel["accepted"]
    assert accepted["calls"] == [{"url": "/api/settings/context-presets/large", "method": "POST"}]
    assert accepted["applied"] == 1 and accepted["error"] == ""

    refused = panel["refused"]
    assert refused["applied"] == 0
    assert refused["error"] == (
        "Couldn't apply Large (256K). The Large (256K) preset breaks the context rules: "
        "Reply must be a whole number from 1 to 99."
    )
    assert refused["disabled"] == [False, False, False], "the buttons come back after a refusal"
    assert panel["noBody"]["error"] == "Couldn't apply Large (256K). The server answered 502."
