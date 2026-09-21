"""System AI picker and compaction pressure knobs. No compaction runner."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import db
from core import config
from db.settings import get_seed_setting_default


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ui" / "static" / "js"

# (key, default). Category is llm — Settings → System → AI Output.
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


def test_ai_output_renders_system_ai_and_compaction_knobs() -> None:
    payload = _render_system_settings()
    assert payload["openedOnSimulation"] is True
    assert payload["savesBeforeOpen"] == 0
    assert payload["heading"] == "AI Output"
    assert any("system processes" in line and "compaction pressure" in line for line in payload["intro"])
    assert set(payload["fetches"]) == {"/api/settings", "/api/connections"}

    rows = _by_key(payload["fresh"])
    order = [row["key"] for row in payload["fresh"]]
    assert order == [
        "decision_repair_attempts",
        "max_concurrent_agent_turns",
        "system_ai_connection",
        "compaction_mode",
        "compaction_task_budget_headroom_percent",
        "compaction_chat_budget_headroom_percent",
        "compaction_min_turns_between_runs",
        "compaction_cooldown_minutes",
        "max_concurrent_llm_calls",
    ]
    for row in rows.values():
        assert row["category"] == "llm"

    turns = rows["max_concurrent_agent_turns"]
    assert turns["label"] == "Max Concurrent Agent Turns"
    assert turns["value"] == "2"
    assert "local LLM" in turns["paragraphs"][0]
    assert "one turn" in turns["paragraphs"][0]

    system_ai = rows["system_ai_connection"]
    assert system_ai["label"] == "System AI"
    assert system_ai["paragraphs"][0] == (
        "Choose the AI used for system processes. "
        "Channel rounds ask it for one short route. "
        "Compaction uses the same connection and stays in the background."
    )
    assert "No AI connections yet" not in " ".join(system_ai["paragraphs"])
    assert system_ai["value"] == ""
    assert system_ai["options"][0] == {
        "value": "",
        "label": "None",
        "title": None,
        "selected": False,
    }
    assert system_ai["options"][1]["value"] == "conn-plain"
    assert system_ai["options"][1]["label"] == "Local (mock-small)"
    quoted = system_ai["options"][2]
    assert quoted["value"] == "conn-quote"
    assert quoted["label"] == 'Bob "fast" <Local> (gpt-4)'
    assert quoted["title"] == 'Bob "fast" <Local> (gpt-4)'

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
            "url": "/api/settings/system_ai_connection?value=conn-quote&category=llm",
            "method": "PUT",
        },
        {
            "url": "/api/settings/compaction_mode?value=off&category=llm",
            "method": "PUT",
        },
    ]

    degraded = _by_key(payload["degraded"])
    stale = degraded["system_ai_connection"]
    assert any("could not be loaded" in line for line in stale["paragraphs"])
    assert stale["options"][-1]["value"] == "stale-id"
    assert stale["options"][-1]["label"] == "Saved connection unavailable"
    assert stale["options"][-1]["selected"] is True
    assert stale["value"] == "stale-id"
    custom = degraded["compaction_mode"]
    assert custom["options"][-1]["value"] == "custom_mode"
    assert custom["options"][-1]["selected"] is True
    assert custom["value"] == "custom_mode"
