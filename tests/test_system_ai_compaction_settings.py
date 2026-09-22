"""System AI picker and compaction pressure knobs. No compaction runner."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import db
from core import config
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
    assert "queue in the background" in source
    assert "no compaction runner" in source
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
    system = (JS / "settings" / "settings-system.js").read_text(encoding="utf-8")
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
