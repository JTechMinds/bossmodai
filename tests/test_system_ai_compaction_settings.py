"""System AI picker and compaction pressure knobs. Chat fade uses them."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.standing_prefs import WARM_PREFIX_MAX_CHARS, WARM_SECTION_HEADER
from core.llm.system_completion import resolve_system_connection
from db.settings import get_seed_setting_default, seed_defaults


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ui" / "static" / "js"

# (key, default). Category is llm. The System AI picker is rendered under
# AI Connections; compaction knobs stay on Settings → System → AI Output.
COMPACTION_SETTINGS = (
    ("system_ai_connection", ""),
    ("compaction_mode", "pressure_only"),
    ("compaction_task_budget_headroom_percent", "25"),
    ("compaction_chat_budget_headroom_percent", "35"),
    ("compaction_min_turns_between_runs", "8"),
    ("compaction_cooldown_minutes", "10"),
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


def test_fresh_db_seeds_system_ai_and_compaction_knobs() -> None:
    settings = {row.key: row for row in db.get_settings()}
    for key, default in COMPACTION_SETTINGS:
        seeded = get_seed_setting_default(key)
        assert seeded == (default, "llm")
        assert settings[key].value == default
        assert settings[key].category == "llm"
    assert config.get("compaction_mode") == "pressure_only"
    assert config.get("compaction_task_budget_headroom_percent") == "25"
    assert config.get("compaction_chat_budget_headroom_percent") == "35"
    assert config.get("compaction_min_turns_between_runs") == "8"
    assert config.get("compaction_cooldown_minutes") == "10"
    # Empty means unset. config.get hides blank values.
    assert config.get("system_ai_connection") is None


def test_system_ai_connection_id_round_trips() -> None:
    db.set_setting("system_ai_connection", "conn-1", "llm")
    config.reload()
    assert config.get("system_ai_connection") == "conn-1"


def test_seed_comments_state_the_compaction_product_rules() -> None:
    source = (ROOT / "db" / "settings.py").read_text(encoding="utf-8")
    assert "never runs every turn" in source
    assert "never blocks an agent turn" in source
    assert "queues in the background" in source
    assert "chat fade" in source.lower()
    assert "sticky-slot" in source.lower()
    assert "not a" in source and "per-agent identity model" in source


def test_no_compaction_runner_module() -> None:
    runners = []
    for folder in ("core", "api", "db"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and "compact" in path.name.lower():
                runners.append(path.relative_to(ROOT).as_posix())
    assert runners == []


def _render_system_settings() -> dict:
    harness = Path(__file__).resolve().parent / "js_system_settings_harness.cjs"
    result = subprocess.run(
        [
            "node",
            str(harness),
            str(JS / "core" / "format.js"),
            str(JS / "settings" / "settings-system.js"),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "switch.js"),
            str(JS / "settings" / "settings-system-meta.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def _by_key(rows: list[dict]) -> dict[str, dict]:
    return {row["key"]: row for row in rows}


def test_ai_output_renders_compaction_knobs_without_system_ai() -> None:
    payload = _render_system_settings()
    assert payload["openedOnSimulation"] is True
    assert payload["savesBeforeOpen"] == 0
    assert payload["heading"] == "AI Output"
    assert any("compaction pressure" in line for line in payload["intro"])
    assert not any("system processes" in line for line in payload["intro"])
    assert set(payload["fetches"]) == {"/api/settings"}

    rows = _by_key(payload["fresh"])
    order = [row["key"] for row in payload["fresh"]]
    assert order == [
        "decision_repair_attempts",
        "max_concurrent_agent_turns",
        "system_ai_max_tokens",
        "compaction_mode",
        "compaction_task_budget_headroom_percent",
        "compaction_chat_budget_headroom_percent",
        "compaction_min_turns_between_runs",
        "compaction_cooldown_minutes",
    ]
    assert "max_concurrent_llm_calls" not in rows
    assert "max_concurrent_llm_calls" not in order
    assert "system_ai_connection" not in rows
    for row in rows.values():
        assert row["category"] == "llm"

    turns = rows["max_concurrent_agent_turns"]
    assert turns["label"] == "Max concurrent model calls"
    assert turns["value"] == "2"
    assert "System AI routes" in turns["paragraphs"][0]
    assert "repairs" in turns["paragraphs"][0]
    assert "one turn" in turns["paragraphs"][0]
    assert "health warning" in turns["paragraphs"][0]

    max_tokens = rows["system_ai_max_tokens"]
    assert max_tokens["label"] == "System AI Max Output Tokens"
    assert max_tokens["value"] == "6144"
    assert "reasoning" in max_tokens["paragraphs"][0]

    mode = rows["compaction_mode"]
    assert mode["label"] == "Compaction Mode"
    assert "never runs every turn" in mode["paragraphs"][0]
    assert "never blocks the agent turn" in mode["paragraphs"][0]
    assert "queues in the background" in mode["paragraphs"][0]
    assert mode["value"] == "pressure_only"
    assert [opt["label"] for opt in mode["options"]] == ["Off", "Pressure-only"]
    assert mode["options"][1]["selected"] is True

    assert rows["compaction_task_budget_headroom_percent"]["label"] == "Task Budget Headroom (%)"
    assert rows["compaction_task_budget_headroom_percent"]["value"] == "25"
    assert rows["compaction_chat_budget_headroom_percent"]["value"] == "35"
    assert "never runs every turn" in rows["compaction_min_turns_between_runs"]["paragraphs"][0]
    assert rows["compaction_min_turns_between_runs"]["value"] == "8"
    assert "never blocks the agent turn" in rows["compaction_cooldown_minutes"]["paragraphs"][0]
    assert "queues in the background" in rows["compaction_cooldown_minutes"]["paragraphs"][0]
    assert rows["compaction_cooldown_minutes"]["value"] == "10"

    assert payload["saves"] == [
        {
            "url": "/api/settings/compaction_mode?value=off&category=llm",
            "method": "PUT",
        },
    ]

    degraded = _by_key(payload["degraded"])
    assert "system_ai_connection" not in degraded
    custom = degraded["compaction_mode"]
    assert custom["options"][-1]["value"] == "custom_mode"
    assert custom["options"][-1]["selected"] is True
    assert custom["value"] == "custom_mode"


def _settings_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _put_max_tokens(value: str):
    return _settings_client().put(
        "/api/settings/system_ai_max_tokens",
        params={"value": value, "category": "llm"},
        headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()},
    )


@pytest.mark.parametrize("bad", ["6k", "0", "-5"])
def test_settings_put_rejects_a_bad_system_ai_max_tokens(bad: str) -> None:
    res = _put_max_tokens(bad)
    assert res.status_code == 400
    assert res.json()["detail"] == "System AI max output tokens must be a whole number of at least 1."
    config.reload()
    assert config.get("system_ai_max_tokens") == "6144"
    assert config.require_int("system_ai_max_tokens") == 6144


def test_settings_put_accepts_a_whole_system_ai_max_tokens() -> None:
    res = _put_max_tokens("4096")
    assert res.status_code == 200, res.text
    assert config.require_int("system_ai_max_tokens") == 4096


def _put_setting(key: str, value: str, category: str):
    return _settings_client().put(
        f"/api/settings/{key}",
        params={"value": value, "category": category},
        headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()},
    )


# (key, category, label named by the 400, seeded default)
POSITIVE_INT_KEYS = (
    ("system_ai_max_tokens", "llm", "System AI max output tokens", "6144"),
    ("standing_prefs_line_max_chars", "context", "Standing Pref Line Limit", "400"),
    ("standing_prefs_section_max_chars", "context", "Standing Prefs Section Limit", "4000"),
)


@pytest.mark.parametrize("bad", ["6k", "0", "-5"])
@pytest.mark.parametrize(("key", "category", "label", "default"), POSITIVE_INT_KEYS)
def test_settings_put_rejects_a_bad_positive_int_for_every_key(
    key: str, category: str, label: str, default: str, bad: str
) -> None:
    res = _put_setting(key, bad, category)
    assert res.status_code == 400
    assert res.json()["detail"] == f"{label} must be a whole number of at least 1."
    config.reload()
    assert config.get(key) == default


# Room one full pref line needs beyond the line limit: the longest label plus
# the warm header and its newline.
PREFS_SECTION_ROOM = WARM_PREFIX_MAX_CHARS + len(WARM_SECTION_HEADER) + 1


def _pair_error(section: int, line: int) -> str:
    return (
        f"Standing Prefs Section Limit ({section}) must be at least {line + PREFS_SECTION_ROOM}: "
        f"the line limit plus room for one pref's label (Standing Pref Line Limit is {line})."
    )


def test_prefs_section_below_the_minimum_is_rejected_in_both_directions() -> None:
    lowered_section = _put_setting("standing_prefs_section_max_chars", str(400 + PREFS_SECTION_ROOM - 1), "context")
    assert lowered_section.status_code == 400
    assert lowered_section.json()["detail"] == _pair_error(400 + PREFS_SECTION_ROOM - 1, 400)
    raised_line = _put_setting("standing_prefs_line_max_chars", str(4000 - PREFS_SECTION_ROOM + 1), "context")
    assert raised_line.status_code == 400
    assert raised_line.json()["detail"] == _pair_error(4000, 4000 - PREFS_SECTION_ROOM + 1)
    config.reload()
    assert config.require_int("standing_prefs_line_max_chars") == 400
    assert config.require_int("standing_prefs_section_max_chars") == 4000


def test_prefs_limits_accept_section_at_exactly_the_minimum() -> None:
    res = _put_setting("standing_prefs_section_max_chars", str(400 + PREFS_SECTION_ROOM), "context")
    assert res.status_code == 200, res.text
    assert config.require_int("standing_prefs_section_max_chars") == 400 + PREFS_SECTION_ROOM
    res = _put_setting("standing_prefs_section_max_chars", "4000", "context")
    assert res.status_code == 200, res.text
    res = _put_setting("standing_prefs_line_max_chars", str(4000 - PREFS_SECTION_ROOM), "context")
    assert res.status_code == 200, res.text
    assert config.require_int("standing_prefs_line_max_chars") == 4000 - PREFS_SECTION_ROOM


def test_the_default_prefs_limits_satisfy_the_pair_rule() -> None:
    line = int(get_seed_setting_default("standing_prefs_line_max_chars")[0])
    section = int(get_seed_setting_default("standing_prefs_section_max_chars")[0])
    assert section >= line + PREFS_SECTION_ROOM
    assert _put_setting("standing_prefs_line_max_chars", str(line), "context").status_code == 200
    assert _put_setting("standing_prefs_section_max_chars", str(section), "context").status_code == 200


def _reset_setting(key: str):
    return _settings_client().post(
        f"/api/settings/{key}/reset",
        headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()},
    )


def test_reset_of_the_section_limit_is_rejected_when_the_raised_line_needs_more() -> None:
    assert _put_setting("standing_prefs_section_max_chars", "6000", "context").status_code == 200
    assert _put_setting("standing_prefs_line_max_chars", "5000", "context").status_code == 200
    res = _reset_setting("standing_prefs_section_max_chars")
    assert res.status_code == 400
    assert res.json()["detail"] == _pair_error(4000, 5000)
    config.reload()
    assert config.require_int("standing_prefs_section_max_chars") == 6000
    assert config.require_int("standing_prefs_line_max_chars") == 5000


def test_reset_of_a_valid_prefs_pair_restores_the_defaults() -> None:
    assert _put_setting("standing_prefs_section_max_chars", "6000", "context").status_code == 200
    assert _put_setting("standing_prefs_line_max_chars", "1000", "context").status_code == 200
    res = _reset_setting("standing_prefs_section_max_chars")
    assert res.status_code == 200, res.text
    assert res.json()["value"] == "4000"
    res = _reset_setting("standing_prefs_line_max_chars")
    assert res.status_code == 200, res.text
    assert res.json()["value"] == "400"
    config.reload()
    assert config.require_int("standing_prefs_section_max_chars") == 4000
    assert config.require_int("standing_prefs_line_max_chars") == 400


def test_reset_of_other_keys_is_unchanged() -> None:
    assert _put_max_tokens("4096").status_code == 200
    res = _reset_setting("system_ai_max_tokens")
    assert res.status_code == 200, res.text
    assert res.json()["value"] == "6144"
    config.reload()
    assert config.require_int("system_ai_max_tokens") == 6144
    unseeded = _reset_setting("no_such_setting")
    assert unseeded.status_code == 400
    assert unseeded.json()["detail"] == "Setting 'no_such_setting' has no seeded default"


def test_fresh_db_seeds_the_standing_prefs_limits() -> None:
    settings = {row.key: row for row in db.get_settings()}
    for key, default in (
        ("standing_prefs_line_max_chars", "400"),
        ("standing_prefs_section_max_chars", "4000"),
    ):
        assert get_seed_setting_default(key) == (default, "context")
        assert settings[key].value == default
        assert settings[key].category == "context"


def test_context_window_renders_the_standing_prefs_limits_in_order() -> None:
    payload = _render_system_settings()
    order = [row["key"] for row in payload["context"]]
    assert order == [
        "context_recent_work_artifacts",
        "context_recent_completed_tasks",
        "standing_prefs_line_max_chars",
        "standing_prefs_section_max_chars",
    ]
    rows = _by_key(payload["context"])
    line = rows["standing_prefs_line_max_chars"]
    assert line["label"] == "Standing Pref Line Limit (chars)"
    assert line["value"] == "400"
    assert line["category"] == "context"
    assert line["paragraphs"][0].startswith(
        "Longest standing pref text an agent can save; that text is always shown whole in the prompt."
    )
    assert "Default 400." in line["paragraphs"][0]
    section = rows["standing_prefs_section_max_chars"]
    assert section["label"] == "Standing Prefs Section Limit (chars)"
    assert section["value"] == "4000"
    assert section["category"] == "context"
    assert "Must leave room for at least one full pref line." in section["paragraphs"][0]
    assert "Default 4000." in section["paragraphs"][0]
    for row in (line, section):
        assert "restart" not in row["paragraphs"][0].lower()


THREAD_KEYS = [
    "channel_router_transcript_messages",
    "channel_response_round_cap",
    "channel_idle_check_enabled",
    "channel_idle_check_delay_seconds",
    "channel_idle_check_max_age_minutes",
    "channel_idle_check_max_wakes",
    "channel_idle_check_interval_seconds",
]


def test_threads_tab_lists_thread_settings_in_order() -> None:
    payload = _render_system_settings()
    assert payload["threadsHeading"] == "Threads"
    assert payload["threadsOrder"] == THREAD_KEYS
    for row in payload["threadsInputs"]:
        # Grouped under Threads, still stored and saved as llm.
        assert row["category"] == "llm"
    inputs = _by_key(payload["threadsInputs"])
    assert inputs["channel_router_transcript_messages"]["label"] == "Router Transcript Lines"
    assert "0 sends no history" in inputs["channel_router_transcript_messages"]["paragraphs"][0]
    assert inputs["channel_idle_check_max_age_minutes"]["label"] == "Idle Check Max Age (minutes)"
    assert inputs["channel_idle_check_delay_seconds"]["value"] == "45"
    idle = payload["idle"]
    assert idle["before"]["role"] == "switch"
    assert idle["before"]["checked"] == "true"
    assert idle["before"]["name"] == "Idle Check"
    # The switch row is the label; the card repeats no <label> heading.
    assert idle["before"]["cardLabels"] == 0
    assert idle["before"]["paragraphs"][0].startswith("When a thread goes quiet")
    assert idle["toggleSaves"] == [
        {
            "url": "/api/settings/channel_idle_check_enabled?value=false&category=llm",
            "method": "PUT",
        },
    ]
    assert idle["afterToggle"] == "false"


def test_failed_save_shows_the_server_message() -> None:
    payload = _render_system_settings()
    assert payload["delayError"] == {
        "text": "Idle check delay must be a whole number of at least 1.",
        "role": "alert",
    }
    # Only the refused row shows the message.
    assert payload["otherError"] == {"text": "", "role": "alert"}
    idle = payload["idle"]
    assert idle["beforeRefused"] == "false"
    assert idle["afterRefused"] == "false"
    assert idle["error"] == {"text": "Idle check must be true or false.", "role": "alert"}
    # The next accepted save clears the line.
    assert payload["delayErrorAfterFix"] == {"text": "", "role": "alert"}


# (key, label named by the 400, a valid value)
THREAD_POSITIVE_INT_KEYS = (
    ("channel_response_round_cap", "Round cap per message", "32"),
    ("channel_idle_check_delay_seconds", "Idle check delay", "60"),
    ("channel_idle_check_interval_seconds", "Idle check scan interval", "10"),
    ("channel_idle_check_max_age_minutes", "Idle check max age", "15"),
    ("channel_idle_check_max_wakes", "Idle check max wakes", "1"),
)


@pytest.mark.parametrize("bad", ["0", "abc", "45s", "-1"])
@pytest.mark.parametrize(("key", "label", "_good"), THREAD_POSITIVE_INT_KEYS)
def test_thread_positive_int_settings_reject_a_bad_value(key: str, label: str, _good: str, bad: str) -> None:
    before = config.get(key)
    res = _put_setting(key, bad, "llm")
    assert res.status_code == 400
    assert res.json()["detail"] == f"{label} must be a whole number of at least 1."
    config.reload()
    assert config.get(key) == before


@pytest.mark.parametrize(("key", "_label", "good"), THREAD_POSITIVE_INT_KEYS)
def test_thread_positive_int_settings_accept_a_whole_number(key: str, _label: str, good: str) -> None:
    res = _put_setting(key, good, "llm")
    assert res.status_code == 200, res.text
    assert config.get(key) == good


def test_router_transcript_lines_accept_zero_and_reject_negative() -> None:
    bad = _put_setting("channel_router_transcript_messages", "-1", "llm")
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Router transcript lines must be a whole number of 0 or more."
    abc = _put_setting("channel_router_transcript_messages", "abc", "llm")
    assert abc.status_code == 400
    config.reload()
    assert config.get("channel_router_transcript_messages") == "10"
    zero = _put_setting("channel_router_transcript_messages", "0", "llm")
    assert zero.status_code == 200, zero.text
    assert config.get("channel_router_transcript_messages") == "0"
    twenty = _put_setting("channel_router_transcript_messages", "20", "llm")
    assert twenty.status_code == 200, twenty.text
    assert config.get("channel_router_transcript_messages") == "20"


def test_idle_check_flag_accepts_only_true_or_false() -> None:
    for bad in ("yes", "True", "1", ""):
        res = _put_setting("channel_idle_check_enabled", bad, "llm")
        assert res.status_code == 400
        assert res.json()["detail"] == "Idle check must be true or false."
    config.reload()
    assert config.get("channel_idle_check_enabled") == "true"
    off = _put_setting("channel_idle_check_enabled", "false", "llm")
    assert off.status_code == 200, off.text
    assert config.get("channel_idle_check_enabled") == "false"
    on = _put_setting("channel_idle_check_enabled", "true", "llm")
    assert on.status_code == 200, on.text
    assert config.get("channel_idle_check_enabled") == "true"


def _stored_system_ai() -> str:
    return next(row.value for row in db.get_settings() if row.key == "system_ai_connection")


def _connection(name: str, model: str | None) -> str:
    created = db.create_connection(
        name=name,
        api_base_url="http://127.0.0.1:9/v1",
        model=model,
    )
    return created.id


def test_seed_defaults_does_not_wipe_an_existing_system_ai_pick() -> None:
    chosen = _connection("Zed", "mock-small")
    _connection("Alpha", "mock-small")
    db.set_setting("system_ai_connection", chosen, "llm")
    seed_defaults()
    config.reload()
    assert config.get("system_ai_connection") == chosen
    assert _stored_system_ai() == chosen
    assert resolve_system_connection() is not None
    assert resolve_system_connection().id == chosen
    assert _stored_system_ai() == chosen


def test_seed_defaults_does_not_fill_an_unset_system_ai_pick() -> None:
    first = _connection("Alpha", "mock-small")
    seed_defaults()
    config.reload()
    assert _stored_system_ai() == ""
    assert config.get("system_ai_connection") is None
    resolved = resolve_system_connection()
    assert resolved is not None
    assert resolved.id == first
    assert _stored_system_ai() == ""


def test_missing_system_ai_pick_uses_the_first_connection_without_writing() -> None:
    first = _connection("Alpha", "mock-small")
    _connection("Zed", "mock-small")
    db.set_setting("system_ai_connection", "gone-id", "llm")
    config.reload()
    resolved = resolve_system_connection()
    assert resolved is not None
    assert resolved.id == first
    assert config.get("system_ai_connection") == "gone-id"
    assert _stored_system_ai() == "gone-id"


def test_saved_system_ai_without_a_model_is_not_replaced() -> None:
    bare = _connection("Alpha", None)
    _connection("Zed", "mock-small")
    db.set_setting("system_ai_connection", bare, "llm")
    config.reload()
    assert resolve_system_connection() is None
    assert _stored_system_ai() == bare


def test_unset_system_ai_does_not_skip_a_model_less_first_connection() -> None:
    _connection("Alpha", None)
    _connection("Zed", "mock-small")
    config.reload()
    assert resolve_system_connection() is None
    assert _stored_system_ai() == ""


def _render_connections() -> dict:
    harness = Path(__file__).resolve().parent / "js_system_ai_connections_harness.cjs"
    result = subprocess.run(
        [
            "node",
            str(harness),
            str(JS / "core" / "format.js"),
            str(JS / "settings" / "settings-connections.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_system_ai_is_the_first_control_under_ai_connections() -> None:
    payload = _render_connections()
    unset = payload["unset"]
    assert unset["heading"] == "AI Connections"
    assert unset["underHeading"] is True
    assert unset["directlyUnderHeading"] is True
    assert unset["label"] == "System AI"
    assert unset["paragraphs"][0] == (
        "Choose the AI used for system processes (compaction, channel router, etc.)."
    )
    assert unset["controls"][0]["key"] == "system_ai_connection"
    assert unset["controls"][1]["id"] == "btn-add-connection"
    assert unset["saves"] == []
    assert unset["value"] == "conn-plain"
    assert [opt["value"] for opt in unset["options"]] == ["conn-plain", "conn-quote"]
    assert unset["options"][0]["selected"] is True
    assert unset["options"][0]["label"] == "Local (mock-small)"
    quoted = unset["options"][1]
    assert quoted["label"] == 'Bob "fast" <Local> (gpt-4)'
    assert quoted["title"] == 'Bob "fast" <Local> (gpt-4)'
    assert quoted["selected"] is False
    assert "" not in [opt["value"] for opt in unset["options"]]
    assert set(payload["fetches"]) == {"/api/settings", "/api/connections"}

    assert payload["swapped"] == [
        {
            "url": "/api/settings/system_ai_connection?value=conn-quote&category=llm",
            "method": "PUT",
        },
    ]

    kept = payload["kept"]
    assert kept["saves"] == []
    assert kept["value"] == "conn-quote"
    assert kept["options"][1]["selected"] is True
    assert kept["options"][0]["selected"] is False
    assert kept["controls"][0]["key"] == "system_ai_connection"

    gone = payload["gone"]
    assert gone["saves"] == []
    assert gone["value"] == "conn-plain"
    assert [opt["value"] for opt in gone["options"]] == ["conn-plain", "conn-quote"]
    assert gone["options"][0]["selected"] is True
    assert "Saved connection unavailable" not in gone["pageText"]

    failed = payload["failedLoad"]
    assert failed["saves"] == []
    assert failed["underHeading"] is True
    assert failed["value"] == "conn-quote"
    assert failed["options"] == [
        {
            "value": "conn-quote",
            "label": "Saved connection unavailable",
            "title": "Saved connection unavailable",
            "selected": True,
        },
    ]
    assert any("could not be loaded" in line for line in failed["paragraphs"])
    assert "Failed to load connections." in failed["pageText"]

    blocked = payload["settingsFailed"]
    assert blocked["saves"] == []
    assert blocked["underHeading"] is True
    assert blocked["value"] is None
    assert blocked["paragraphs"][0].startswith("Choose the AI used for system processes")
    assert any("could not be loaded" in line for line in blocked["paragraphs"])
    assert blocked["controls"][0]["id"] == "btn-add-connection"

    empty = payload["emptyList"]
    assert empty["saves"] == []
    assert empty["controls"][0]["key"] == "system_ai_connection"
    assert empty["options"] == []
    assert empty["disabled"] is True
    assert any("No AI connections yet." in line for line in empty["paragraphs"])
    assert "No connections yet" in empty["pageText"]

    kept_stale = payload["emptyListKept"]
    assert kept_stale["saves"] == []
    assert kept_stale["value"] == "stale-id"
    assert kept_stale["options"][0]["label"] == "Saved connection unavailable"
    assert kept_stale["options"][0]["selected"] is True


def test_system_ai_picker_source_is_under_ai_connections() -> None:
    connections = (JS / "settings" / "settings-connections.js").read_text(encoding="utf-8")
    # The System section is its renderer plus its setting catalog.
    system = (JS / "settings" / "settings-system.js").read_text(encoding="utf-8") + (
        JS / "settings" / "settings-system-meta.js"
    ).read_text(encoding="utf-8")
    completion = (ROOT / "core" / "llm" / "system_completion.py").read_text(encoding="utf-8")
    assert "system_ai_connection" not in system
    h2 = connections.index('<h2 class="text-lg font-semibold">AI Connections</h2>')
    block = connections.index("${systemAiBlock(state)}")
    empty = connections.index("No connections yet")
    edit = connections.index("data-edit-conn")
    assert h2 < block < empty
    assert block < edit
    assert 'data-setting-key="system_ai_connection"' in connections
    assert "Choose the AI used for system processes (compaction, channel router, etc.)." in connections
    assert "set_setting" not in completion
    assert "never blocks the agent turn" in system
