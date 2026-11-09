"""core/context_budget.py: one context window in tokens, its rules, presets and the result cap.

The domain functions are pure (except the two readers); the API section runs
the Settings routes against the isolated test database (conftest.py). The
reserve is measured, never set: on a fresh database nothing is measured and
it is 0; a test that needs one records an agent's instruction size.
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
    reserve_of,
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
    # Fixed prompt room: the reserve is measured now.
    "context_fixed_tokens",
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


# ─── compute_budget: the shares of what the reserve leaves ───


@pytest.mark.parametrize(
    ("preset_id", "reserved", "expected"),
    [
        # Nothing measured yet: the shares divide the whole window.
        ("small", 0, (32_000, 0, 32_000, 4_800, 1_600, 4_800, 6_000, 8_000, 1_600, 17_600, 20_400, 25_600)),
        ("medium", 0, (128_000, 0, 128_000, 15_360, 6_400, 25_600, 32_000, 32_000, 6_400, 76_800, 85_760, 104_960)),
        ("large", 0, (256_000, 0, 256_000, 30_720, 12_800, 51_200, 64_000, 64_000, 12_800, 153_600, 171_520, 209_920)),
        # The plan's example (D15): Large with a 10,000-token reserve.
        (
            "large", 10_000,
            (256_000, 10_000, 246_000, 29_520, 12_300, 49_200, 61_500, 61_500, 12_300, 147_600, 174_820, 211_720),
        ),
        ("small", 10_000, (32_000, 10_000, 22_000, 3_300, 1_100, 3_300, 4_125, 5_500, 1_100, 12_100, 24_025, 27_600)),
    ],
)
def test_each_preset_computes_its_numbers_around_the_reserve(
    preset_id: str, reserved: int, expected: tuple[int, ...],
) -> None:
    budget = compute_budget(_shares(preset_id), reserved=reserved)
    assert (
        budget.window, budget.reserved, budget.usable, budget.reply, budget.result, budget.history,
        budget.history_cap, budget.asked, budget.task_log, budget.work, budget.worst_chat_turn,
        budget.worst_work_turn,
    ) == expected


def test_the_message_being_answered_has_the_free_room() -> None:
    """D14: Large with a 10,000-token reserve leaves 44,280 free on the worst work turn, 81,180 on chat."""
    budget = compute_budget(_shares("large"), reserved=10_000)
    assert budget.window - budget.worst_work_turn == 44_280
    assert budget.window - budget.worst_chat_turn == 81_180


def test_compute_budget_refuses_a_window_with_no_usable_room() -> None:
    with pytest.raises(ValueError, match=r"Context window \(12,000\) must be larger than the reserved instructions"):
        compute_budget(_shares("medium", window=12_000), reserved=12_000)
    with pytest.raises(ValueError, match="cannot be negative"):
        compute_budget(_shares("medium"), reserved=-1)


# ─── check_shares: the never-too-big rule ───


def test_every_preset_passes_every_rule() -> None:
    for preset in CONTEXT_PRESETS:
        assert check_shares(_shares(preset.id), reserved=0, reserved_by=None) == [], preset.id


def test_every_preset_holds_a_10000_token_reserve() -> None:
    for preset in CONTEXT_PRESETS:
        assert check_shares(_shares(preset.id), reserved=10_000, reserved_by="Eng-A") == [], preset.id


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
        (
            {"window": 12_000},
            "Context window (12,000) must be larger than Eng-A's instructions (12,000 tokens), "
            "the largest of any agent's.",
        ),
        ({"window": 0}, "Context window must be a whole number of at least 1."),
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
    """Checked at a 12,000-token reserve measured for Eng-A."""
    assert problem in check_shares(_shares("medium", **changes), reserved=12_000, reserved_by="Eng-A")


def test_a_window_of_zero_is_named_once() -> None:
    assert check_shares(_shares("medium", window=0), reserved=12_000, reserved_by="Eng-A")[:1] == [
        "Context window must be a whole number of at least 1."
    ]
    assert not any("instructions" in problem for problem in check_shares(
        _shares("medium", window=0), reserved=12_000, reserved_by="Eng-A",
    ))


def test_nothing_measured_has_no_reserve_rule() -> None:
    # 1 token of window: every share rounds to nothing, but no agent is named.
    problems = check_shares(_shares("medium", window=1), reserved=0, reserved_by=None)
    assert not any("instructions" in problem for problem in problems)


def test_a_reserve_must_name_its_agent_and_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="must name the agent"):
        check_shares(_shares("medium"), reserved=10_000, reserved_by=None)
    with pytest.raises(ValueError, match="cannot be negative"):
        check_shares(_shares("medium"), reserved=-1, reserved_by=None)


def test_a_total_of_exactly_100_percent_is_allowed() -> None:
    assert check_shares(_shares("medium", work=78), reserved=0, reserved_by=None) == []  # 12 + 5 + 78 + 5 = 100


# ─── reading the stored settings ───


def test_the_seeds_are_the_medium_preset() -> None:
    medium = find_preset("medium")
    for key in PRESET_SETTINGS:
        seeded = get_seed_setting_default(key)
        assert seeded is not None and seeded[0] == str(medium.values[key]), key
    assert get_seed_setting_default("context_fixed_tokens") is None, "the reserve is measured, not seeded"
    assert read_context_budget() == compute_budget(_shares("medium"), reserved=0)


# ─── the measured reserve ───


def test_an_instruction_size_is_one_row_per_agent_replaced_each_time() -> None:
    agent = db.create_agent("Eng-A", role="Eng")
    db.record_instruction_size(agent.id, 9_052)
    first = db.largest_instruction_size()
    db.record_instruction_size(agent.id, 9_937)
    rows = query("SELECT agent_id, tokens FROM agent_instruction_sizes")
    assert rows == [{"agent_id": agent.id, "tokens": 9_937}]
    latest = db.largest_instruction_size()
    assert latest is not None and first is not None
    assert (latest.agent_id, latest.agent_name, latest.tokens) == (agent.id, "Eng-A", 9_937)
    assert latest.measured_at >= first.measured_at
    assert latest.measured_at.tzinfo is not None, "stored and read back in UTC"


def test_a_bad_instruction_size_is_refused() -> None:
    agent = db.create_agent("Eng-A", role="Eng")
    with pytest.raises(ValueError, match="cannot be negative"):
        db.record_instruction_size(agent.id, -1)
    with pytest.raises(ValueError, match="needs the agent"):
        db.record_instruction_size("  ", 10)
    assert db.largest_instruction_size() is None


def test_the_reserve_is_the_largest_measurement_of_an_existing_agent() -> None:
    assert db.largest_instruction_size() is None
    assert reserve_of(None) == (0, None)
    small = db.create_agent("Ada", role="Eng")
    large = db.create_agent("Eng-A", role="Eng")
    db.record_instruction_size(small.id, 4_000)
    db.record_instruction_size(large.id, 9_937)
    # A row left by an agent that no longer exists never counts.
    db.record_instruction_size("agent-gone", 50_000)

    largest = db.largest_instruction_size()
    assert largest is not None
    assert reserve_of(largest) == (9_937, "Eng-A")
    budget = read_context_budget()
    assert (budget.reserved, budget.usable) == (9_937, 128_000 - 9_937)
    assert budget == compute_budget(_shares("medium"), reserved=9_937)


def test_instructions_that_outgrow_the_window_stop_the_budget_naming_the_agent() -> None:
    agent = db.create_agent("Eng-A", role="Eng")
    db.record_instruction_size(agent.id, 130_000)
    with pytest.raises(config.ConfigError) as excinfo:
        read_context_budget()
    assert str(excinfo.value) == (
        "The Context Window settings are invalid: Context window (128,000) must be larger than "
        "Eng-A's instructions (130,000 tokens), the largest of any agent's."
    )


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
    assert "fixed" not in body
    assert body["problems"] == []
    # Nothing measured yet: the shares divide the whole window.
    assert {key: body[key] for key in (
        "window", "reserved", "reserved_by", "usable", "reply", "result", "history", "history_cap", "asked",
        "task_log", "work", "worst_chat_turn", "worst_work_turn", "worst_chat_percent", "worst_work_percent",
    )} == {
        "window": 128_000, "reserved": 0, "reserved_by": None, "usable": 128_000, "reply": 15_360,
        "result": 6_400, "history": 25_600, "history_cap": 32_000, "asked": 32_000, "task_log": 6_400,
        "work": 76_800, "worst_chat_turn": 85_760, "worst_work_turn": 104_960, "worst_chat_percent": 67,
        "worst_work_percent": 82,
    }
    assert body["active_preset"] == "medium"
    assert body["presets"] == [
        {"id": "small", "label": "Small (32K)"},
        {"id": "medium", "label": "Medium (128K)"},
        {"id": "large", "label": "Large (256K)"},
    ]
    assert body["values"] == dict(find_preset("medium").values)


def test_the_budget_route_names_the_agent_the_reserve_is_based_on() -> None:
    client, headers = _client()
    agent = db.create_agent("Eng-A", role="Eng")
    db.record_instruction_size(agent.id, 9_937)
    measured = db.largest_instruction_size()
    assert measured is not None

    body = client.get("/api/settings/context-budget", headers=headers).json()

    assert body["reserved"] == 9_937
    assert body["reserved_by"] == {
        "agent_id": agent.id, "agent_name": "Eng-A", "measured_at": measured.measured_at.isoformat(),
    }
    assert body["usable"] == 128_000 - 9_937
    assert body["reply"] == (128_000 - 9_937) * 12 // 100


_BUDGET_KEYS = (
    "window", "usable", "reply", "result", "history", "history_cap", "asked", "task_log", "work",
    "worst_chat_turn", "worst_work_turn", "worst_chat_percent", "worst_work_percent", "values",
)


def test_an_outgrown_window_is_reported_not_a_server_error_and_a_larger_window_fixes_it() -> None:
    """Instructions measured after the window was saved: the tab gets the problem, the presets and the reserve."""
    client, headers = _client()
    agent = db.create_agent("Eng-A", role="Eng")
    db.record_instruction_size(agent.id, 40_000)
    # Straight to the table: the route would refuse it, which is the point.
    db.set_setting("context_window_tokens", "32000", "context")
    config.reload()

    res = client.get("/api/settings/context-budget", headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["problems"] == [
        "Context window (32,000) must be larger than Eng-A's instructions (40,000 tokens), "
        "the largest of any agent's."
    ]
    assert [preset["id"] for preset in body["presets"]] == ["small", "medium", "large"]
    assert body["reserved"] == 40_000
    assert body["reserved_by"]["agent_name"] == "Eng-A"
    assert body["active_preset"] is None
    assert not set(_BUDGET_KEYS) & set(body), "no number is worked out from settings that break a rule"

    # Small cannot hold the reserve either; nothing is written.
    small = client.post("/api/settings/context-presets/small", headers=headers)
    assert small.status_code == 400
    assert "Eng-A's instructions (40,000 tokens)" in small.json()["detail"]

    fixed = client.put(
        "/api/settings/context_window_tokens", params={"value": "128000", "category": "context"}, headers=headers,
    )
    assert fixed.status_code == 200, fixed.text
    after = client.get("/api/settings/context-budget", headers=headers).json()
    assert after["problems"] == []
    assert (after["window"], after["reserved"], after["usable"]) == (128_000, 40_000, 88_000)
    assert after["work"] == 88_000 * 60 // 100
    assert after["active_preset"] == "medium"


@pytest.mark.parametrize(
    "key",
    [
        "context_work_percent",  # a share: read_shares
        "conversation_compaction_start_percent",  # a preset value outside the shares
    ],
)
def test_a_missing_row_is_reported_and_a_preset_restores_it(key: str) -> None:
    client, headers = _client()
    db.execute("DELETE FROM settings WHERE key = $1", [key])
    config.reload()

    res = client.get("/api/settings/context-budget", headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["problems"]) == 1 and key in body["problems"][0], body["problems"]
    assert [preset["id"] for preset in body["presets"]] == ["small", "medium", "large"]
    assert (body["reserved"], body["reserved_by"], body["active_preset"]) == (0, None, None)
    assert not set(_BUDGET_KEYS) & set(body)

    applied = client.post("/api/settings/context-presets/large", headers=headers)

    assert applied.status_code == 200, applied.text
    restored = applied.json()
    assert restored["problems"] == []
    assert restored["active_preset"] == "large"
    assert (restored["worst_work_turn"], restored["worst_work_percent"]) == (209_920, 82)
    assert restored["values"][key] == find_preset("large").values[key]


def test_a_window_at_or_below_the_reserve_is_refused_naming_the_agent() -> None:
    client, headers = _client()
    agent = db.create_agent("Eng-A", role="Eng")
    db.record_instruction_size(agent.id, 9_937)

    for value in ("9937", "9000"):
        res = client.put(
            "/api/settings/context_window_tokens", params={"value": value, "category": "context"}, headers=headers,
        )
        assert res.status_code == 400, value
        assert res.json()["detail"] == (
            f"Context window ({int(value):,}) must be larger than Eng-A's instructions (9,937 tokens), "
            "the largest of any agent's."
        )
    config.reload()
    assert config.require_int("context_window_tokens") == 128_000
    res = client.put(
        "/api/settings/context_window_tokens", params={"value": "64000", "category": "context"}, headers=headers,
    )
    assert res.status_code == 200, res.text


def test_a_preset_that_cannot_hold_the_reserve_is_refused_naming_the_agent() -> None:
    client, headers = _client()
    agent = db.create_agent("Eng-A", role="Eng")
    db.record_instruction_size(agent.id, 33_000)

    res = client.post("/api/settings/context-presets/small", headers=headers)

    assert res.status_code == 400
    assert res.json()["detail"] == (
        "Context window (32,000) must be larger than Eng-A's instructions (33,000 tokens), "
        "the largest of any agent's."
    )
    config.reload()
    assert config.require_int("context_window_tokens") == 128_000, "nothing was written"
    applied = client.post("/api/settings/context-presets/large", headers=headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["reserved"] == 33_000


def test_a_shipped_preset_that_breaks_a_share_rule_stays_a_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checked with nothing reserved, so it is the preset's own values that are wrong, not the window."""
    from api.routes import settings as settings_route
    from core.context_budget import ContextPreset

    broken = ContextPreset(
        id="large", label="Large (256K)",
        values={**find_preset("large").values, "context_reply_percent": 0},
    )
    monkeypatch.setattr(settings_route, "find_preset", lambda _preset_id: broken)
    client, headers = _client()

    res = client.post("/api/settings/context-presets/large", headers=headers)

    assert res.status_code == 500
    assert res.json()["detail"] == (
        "The Large (256K) preset breaks the context rules: Reply must be a whole number from 1 to 99."
    )


def test_the_large_preset_writes_every_value_and_becomes_active() -> None:
    client, headers = _client()

    res = client.post("/api/settings/context-presets/large", headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["active_preset"] == "large"
    assert (body["worst_chat_turn"], body["worst_chat_percent"]) == (171_520, 67)
    assert (body["worst_work_turn"], body["worst_work_percent"]) == (209_920, 82)
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
    """Run the panel harness on the real payloads.

    Medium with nothing measured; Medium with one edit (Custom); Medium
    once Eng-A's instructions are measured at 9,937 tokens; and, last,
    settings that break two rules (Eng-A's instructions grown to 40,000
    against a 32,000 window, and Reply at 0), so the tab is the recovery card.
    """
    import json
    import subprocess

    client, headers = _client()
    medium = client.get("/api/settings/context-budget", headers=headers).json()
    edit = client.put(
        "/api/settings/context_work_percent", params={"value": "59", "category": "context"}, headers=headers,
    )
    assert edit.status_code == 200, edit.text
    custom = client.get("/api/settings/context-budget", headers=headers).json()
    restore = client.put(
        "/api/settings/context_work_percent", params={"value": "60", "category": "context"}, headers=headers,
    )
    assert restore.status_code == 200, restore.text
    agent = db.create_agent("Eng-A", role="Eng")
    db.record_instruction_size(agent.id, 9_937)
    measured = client.get("/api/settings/context-budget", headers=headers).json()
    db.record_instruction_size(agent.id, 40_000)
    # Straight to the table: the route would refuse both.
    db.set_setting("context_window_tokens", "32000", "context")
    db.set_setting("context_reply_percent", "0", "context")
    config.reload()
    recovery = client.get("/api/settings/context-budget", headers=headers).json()
    root = Path(__file__).resolve().parents[1]
    js = root / "ui" / "static" / "js"
    result = subprocess.run(
        [
            "node",
            str(Path(__file__).resolve().parent / "js_context_panel_harness.cjs"),
            str(js / "core" / "format.js"),
            str(js / "settings" / "settings-context-panel.js"),
            json.dumps({"medium": medium, "custom": custom, "measured": measured, "recovery": recovery}),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_panel_lays_out_both_modes_with_every_slot_and_number() -> None:
    medium = _render_panel()["medium"]
    # No Fixed prompt room: the reserve is a read-only line, not a setting.
    assert medium["slots"] == [
        "context_window_tokens", "context_reply_percent", "context_result_percent",
        "context_history_percent", "conversation_compaction_enabled", "conversation_compaction_start_percent",
        "conversation_compaction_keep_percent", "conversation_notes_max_percent",
        "conversation_history_ceiling_percent", "context_asked_percent", "context_task_log_percent",
        "context_work_percent", "work_snapshot_trim_percent", "work_snapshot_summary_percent",
        "context_recent_work_artifacts", "context_recent_completed_tasks", "standing_prefs_line_max_chars",
        "standing_prefs_section_max_chars",
    ]
    assert medium["derived"] == [
        "= 15,360 tokens", "= 6,400 tokens", "= 25,600 tokens (up to 32,000)", "= 32,000 tokens",
        "= 6,400 tokens", "= 76,800 tokens",
    ]
    assert len(medium["bars"]) == 2
    assert medium["bars"][0].startswith("A chat turn at its largest, out of a 128,000-token window: Instructions 0,")
    assert medium["bars"][1].endswith("Largest result 6,400, Free 23,040 tokens.")
    assert medium["legend"][0] == "Instructions", "the first segment is the instructions"
    assert medium["details"] == 2 and len(medium["summaries"]) == 2
    assert medium["alert"] == 1, "the starting points have their own alert line"


def test_the_panel_marks_the_active_preset_or_shows_custom() -> None:
    panel = _render_panel()
    assert panel["medium"]["pressed"] == ["false", "true", "false"]
    assert panel["medium"]["custom"] == 0
    assert panel["custom"]["pressed"] == ["false", "false", "false"]
    assert panel["custom"]["custom"] == 1
    assert panel["custom"]["derived"][-1] == "= 75,520 tokens", "Work 59% of 128,000"


def test_the_model_card_says_the_reserve_is_not_measured_yet() -> None:
    medium = _render_panel()["medium"]
    assert medium["reserved"] == [
        "Reserved for instructions: not measured yet. Each agent's instructions are measured on its next turn."
    ]
    assert medium["usable"] == ["Usable window: 128,000 tokens (context window minus reserved instructions)"]


def test_the_model_card_names_the_agent_the_reserve_is_based_on() -> None:
    panel = _render_panel()
    measured = panel["measured"]
    assert measured["reserved"] == [
        "Reserved for instructions: 9,937 tokens. Based on Eng-A, the agent with the largest instructions "
        "(system prompt, role, memory and task), measured just now. Everything below is a share of what's left."
    ]
    assert panel["threeMinutesAgo"]["reserved"][0].endswith(
        "measured 3m ago. Everything below is a share of what's left."
    )
    assert measured["usable"] == ["Usable window: 118,063 tokens (context window minus reserved instructions)"]
    # The Instructions segment carries the reserve, in the bar's name and its legend.
    assert measured["bars"][0].startswith(
        "A chat turn at its largest, out of a 128,000-token window: Instructions 9,937, Reply 14,167,"
    )
    assert measured["bars"][1].startswith(
        "A work turn at its largest, out of a 128,000-token window: Instructions 9,937, Reply 14,167,"
    )


def test_the_panel_escapes_what_it_shows() -> None:
    panel = _render_panel()
    assert panel["hostile"] == {"rawQuote": False, "rawTag": False, "escapedTag": True}
    assert panel["hostileRecovery"] == {"rawTag": False, "escapedTag": True}, "the problems are escaped too"


def test_settings_that_break_a_rule_get_the_recovery_model_card() -> None:
    panel = _render_panel()
    recovery = panel["recovery"]
    assert recovery["titles"] == ["Model"]
    assert recovery["problem"] == [
        "Agents can't start a turn with these settings. Context window (32,000) must be larger than "
        "Eng-A's instructions (40,000 tokens), the largest of any agent's. Reply must be a whole number "
        "from 1 to 99. Change a value below, or pick a starting point."
    ]
    assert recovery["pressed"] == ["false", "false", "false"]
    assert recovery["custom"] == 0
    assert recovery["alert"] == 1, "a refused starting point still has its alert line"
    assert recovery["slots"] == ["context_window_tokens", "context_reply_percent", "context_result_percent"]
    assert recovery["reserved"] == [
        "Reserved for instructions: 40,000 tokens. Based on Eng-A, the agent with the largest instructions "
        "(system prompt, role, memory and task), measured just now. Everything below is a share of what's left."
    ]
    # Nothing that needs a budget: no bars, mode columns, derived numbers, Usable line or fine-tuning.
    assert (recovery["bars"], recovery["modes"], recovery["derived"], recovery["usable"]) == ([], 0, [], [])
    assert (recovery["details"], recovery["legend"]) == (0, [])
    # Without problems the layout is the full tab.
    assert panel["medium"]["problem"] == []
    assert panel["medium"]["titles"] == ["Model", "Chat mode", "Work mode", "Also in every prompt"]
    assert panel["medium"]["modes"] == 2


def test_a_starting_point_click_from_the_recovery_card_posts_and_re_renders() -> None:
    accepted = _render_panel()["recoveryAccepted"]
    assert accepted["calls"] == [{"url": "/api/settings/context-presets/large", "method": "POST"}]
    assert accepted["applied"] == 1 and accepted["error"] == ""


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
