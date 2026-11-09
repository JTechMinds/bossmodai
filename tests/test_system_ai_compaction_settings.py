"""System AI picker, task sticky-slot knobs, and the conversation-memory knobs."""

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
# AI Connections; the compaction_* knobs (task sticky slots only) stay on
# Settings → System → AI Output.
COMPACTION_SETTINGS = (
    ("system_ai_connection", ""),
    ("compaction_mode", "pressure_only"),
    ("compaction_task_budget_headroom_percent", "25"),
    ("compaction_min_turns_between_runs", "8"),
    ("compaction_cooldown_minutes", "10"),
)

# (key, default). Category is context: Settings → Context Window. The history
# budget itself is History % of the usable window (core/context_budget.py).
CONVERSATION_SETTINGS = (
    ("conversation_compaction_enabled", "true"),
    ("conversation_compaction_start_percent", "80"),
    ("conversation_compaction_keep_percent", "50"),
    ("conversation_notes_max_percent", "10"),
    ("conversation_history_ceiling_percent", "125"),
)

# Keys retired with chat fade and the per-agent history window.
OBSOLETE_KEYS = (
    "compaction_chat_budget_headroom_percent",
    "default_prompt_history_last_n",
    "default_prompt_history_max_tokens",
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
    assert config.get("compaction_min_turns_between_runs") == "8"
    assert config.get("compaction_cooldown_minutes") == "10"
    # Empty means unset. config.get hides blank values.
    assert config.get("system_ai_connection") is None


def test_system_ai_connection_id_round_trips() -> None:
    db.set_setting("system_ai_connection", "conn-1", "llm")
    config.reload()
    assert config.get("system_ai_connection") == "conn-1"


def test_fresh_db_seeds_the_conversation_memory_settings() -> None:
    settings = {row.key: row for row in db.get_settings()}
    for key, default in CONVERSATION_SETTINGS:
        assert get_seed_setting_default(key) == (default, "context")
        assert settings[key].value == default
        assert settings[key].category == "context"


def test_seed_comments_state_the_product_rules() -> None:
    source = (ROOT / "db" / "settings.py").read_text(encoding="utf-8")
    # Task sticky slots: the compaction_* knobs.
    assert "Sticky-slot fill never runs every turn." in source
    assert "It never blocks an agent turn" in source
    assert "queues in the background" in source
    assert "not a" in source and "per-agent identity model" in source
    # Conversation memory: per-thread budget, undisturbed tail, no cliff.
    assert "The history budget is per thread and covers history only" in source
    assert "The recent tail is never disrupted" in source
    assert "each\n    #   new set of notes absorbs the previous notes" in source
    assert "Seamless, no cliff" in source
    assert "never blocks a turn" in source
    assert "never truncated" in source
    assert "chat fade" not in source.lower().replace("chat fade's headroom knob", "").replace(
        "replaced chat fade", ""
    )


def test_obsolete_history_and_chat_fade_keys_are_pruned() -> None:
    # As an older install still has them; set_setting refuses obsolete keys.
    for key in OBSOLETE_KEYS:
        db.execute(
            "INSERT INTO settings (key, value, category) VALUES ($1, '1', 'context')",
            [key],
        )
    assert set(OBSOLETE_KEYS) <= {row.key for row in db.get_settings()}
    db.close_connection()
    db.init_db()
    config.reload()
    stored = {row.key for row in db.get_settings()}
    for key in OBSOLETE_KEYS:
        assert key not in stored
        assert get_seed_setting_default(key) is None


def _budget_payloads() -> str:
    """The real Context Window payloads the harness serves: Medium (the seeds) and Large.

    Large is read by applying its preset through the route, then Medium is
    applied again, so the database is left as the test found it.
    """
    client = _settings_client()
    headers = {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}
    medium = client.get("/api/settings/context-budget", headers=headers)
    assert medium.status_code == 200, medium.text
    large = client.post("/api/settings/context-presets/large", headers=headers)
    assert large.status_code == 200, large.text
    restored = client.post("/api/settings/context-presets/medium", headers=headers)
    assert restored.json() == medium.json()
    return json.dumps({"medium": medium.json(), "large": large.json()})


def _render_system_settings() -> dict:
    harness = Path(__file__).resolve().parent / "js_system_settings_harness.cjs"
    budgets = _budget_payloads()
    result = subprocess.run(
        [
            "node",
            str(harness),
            str(JS / "core" / "format.js"),
            str(JS / "settings" / "settings-system.js"),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "switch.js"),
            str(JS / "settings" / "settings-system-meta.js"),
            str(JS / "settings" / "settings-advanced.js"),
            # A select-type setting's dropdown and its panel.
            str(JS / "core" / "overlay-focus.js"),
            str(JS / "core" / "modal-trail.js"),
            str(JS / "core" / "overlay-actions.js"),
            str(JS / "core" / "overlays.js"),
            str(JS / "core" / "menu.js"),
            str(JS / "core" / "menu-select.js"),
            # The Advanced section's Retention card, loaded before settings-advanced.js.
            str(JS / "settings" / "settings-retention.js"),
            # Context Window: its catalog and its layout, then the budget payloads.
            str(JS / "settings" / "settings-context-meta.js"),
            str(JS / "settings" / "settings-context-panel.js"),
            budgets,
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
    # Only the Context Window tab asks for the budget numbers.
    assert set(payload["fetchesBeforeContext"]) == {"/api/settings"}
    assert set(payload["fetches"]) == {
        "/api/settings", "/api/settings/context-budget", "/api/settings/context-presets/large",
    }

    rows = _by_key(payload["fresh"])
    order = [row["key"] for row in payload["fresh"]]
    assert order == [
        "decision_repair_attempts",
        "max_concurrent_agent_turns",
        "system_ai_max_tokens",
        "system_ai_timeout_seconds",
        "compaction_mode",
        "compaction_task_budget_headroom_percent",
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
    assert "compaction_chat_budget_headroom_percent" not in rows
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


@pytest.mark.parametrize("bad", ["0", "abc"])
def test_settings_put_rejects_a_bad_system_ai_timeout(bad: str) -> None:
    res = _put_setting("system_ai_timeout_seconds", bad, "llm")
    assert res.status_code == 400
    assert res.json()["detail"] == "System AI timeout must be a whole number of at least 1."
    config.reload()
    assert config.require_int("system_ai_timeout_seconds") == 180


def test_settings_put_accepts_a_whole_system_ai_timeout() -> None:
    res = _put_setting("system_ai_timeout_seconds", "180", "llm")
    assert res.status_code == 200, res.text
    res = _put_setting("system_ai_timeout_seconds", "240", "llm")
    assert res.status_code == 200, res.text
    assert config.require_int("system_ai_timeout_seconds") == 240


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
    ("system_ai_timeout_seconds", "llm", "System AI timeout", "180"),
    ("standing_prefs_line_max_chars", "context", "Memory Line Limit", "400"),
    ("standing_prefs_section_max_chars", "context", "Memory Section Limit", "4000"),
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


# Room one full memory line needs beyond the line limit: the longest number
# prefix plus the warm header and its newline.
PREFS_SECTION_ROOM = WARM_PREFIX_MAX_CHARS + len(WARM_SECTION_HEADER) + 1


def _pair_error(section: int, line: int) -> str:
    return (
        f"Memory Section Limit ({section}) must be at least {line + PREFS_SECTION_ROOM}: "
        f"the line limit plus room for one memory's number (Memory Line Limit is {line})."
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


# Every Context Window setting, in the panel's order: the Model card, Chat mode
# (History, its fine-tuning, When you ask), Work mode (Task log, Work, its
# fine-tuning), then what is in every prompt.
CONTEXT_SLOTS = [
    "context_window_tokens",
    "context_fixed_tokens",
    "context_reply_percent",
    "context_result_percent",
    "context_history_percent",
    "conversation_compaction_enabled",
    "conversation_compaction_start_percent",
    "conversation_compaction_keep_percent",
    "conversation_notes_max_percent",
    "conversation_history_ceiling_percent",
    "context_asked_percent",
    "context_task_log_percent",
    "context_work_percent",
    "work_snapshot_trim_percent",
    "work_snapshot_summary_percent",
    "context_recent_work_artifacts",
    "context_recent_completed_tasks",
    "standing_prefs_line_max_chars",
    "standing_prefs_section_max_chars",
]


def test_context_window_fills_every_slot_with_the_standard_row() -> None:
    payload = _render_system_settings()
    panel = payload["panel"]
    assert panel["panels"] == 1
    assert panel["titles"] == ["Model", "Chat mode", "Work mode", "Also in every prompt"]
    assert panel["groups"] == ["Conversation", "Its work", "Conversation", "Its work"]
    assert [slot["key"] for slot in panel["slots"]] == CONTEXT_SLOTS
    assert all(slot["control"] == "standard" and slot["error"] == 1 for slot in panel["slots"])
    # The switch is a switch mount, not a text input, in its own place.
    assert payload["contextOrder"] == CONTEXT_SLOTS
    assert [row["key"] for row in payload["context"]] == [
        key for key in CONTEXT_SLOTS if key != "conversation_compaction_enabled"
    ]
    rows = _by_key(payload["context"])
    line = rows["standing_prefs_line_max_chars"]
    assert line["label"] == "Memory Line Limit (chars)"
    assert line["value"] == "400"
    assert line["category"] == "context"
    assert line["paragraphs"][0].startswith(
        "Longest memory an agent can save. Agents are asked to aim for about 120 characters; "
        "this is the hard stop."
    )
    assert "Default 400." in line["paragraphs"][0]
    section = rows["standing_prefs_section_max_chars"]
    assert section["label"] == "Memory Section Limit (chars)"
    assert section["value"] == "4000"
    assert "Must leave room for at least one full memory line." in section["paragraphs"][0]
    assert "Default 4000." in section["paragraphs"][0]
    for row in (line, section):
        assert "restart" not in row["paragraphs"][0].lower()
    labels = {key: rows[key]["label"] for key, _default in CONVERSATION_SETTINGS if key in rows}
    assert labels == {
        "conversation_compaction_start_percent": "Start summarizing at (% of history)",
        "conversation_compaction_keep_percent": "Keep recent messages (% of history)",
        "conversation_notes_max_percent": "Notes size (% of history)",
        "conversation_history_ceiling_percent": "Hard limit (% of history)",
    }
    for key in labels:
        assert rows[key]["category"] == "context"
    assert rows["context_window_tokens"]["label"] == "Context window (tokens)"
    assert rows["context_window_tokens"]["paragraphs"][0].startswith(
        "The most your agents' model can take in one request, prompt and reply together. "
        "If agents use different models, use the smallest window among them."
    )
    assert rows["context_reply_percent"]["label"] == "Reply (% of usable window)"
    assert rows["context_task_log_percent"]["paragraphs"][0].startswith(
        "While an agent works, how much of the task's log it sees: your comments on the task, "
        "its status lines and teammates' results, newest first."
    )


def test_context_window_shows_the_budget_numbers_and_both_bars() -> None:
    panel = _render_system_settings()["panel"]
    # Medium: 116,000 usable tokens.
    assert panel["derived"] == {
        "context_reply_percent": "= 13,920 tokens",
        "context_result_percent": "= 5,800 tokens",
        "context_history_percent": "= 23,200 tokens (up to 29,000)",
        "context_asked_percent": "= 29,000 tokens",
        "context_task_log_percent": "= 5,800 tokens",
        "context_work_percent": "= 69,600 tokens",
    }
    assert panel["usable"] == ["Usable window: 116,000 tokens (context window minus fixed prompt room)"]
    assert panel["worst"] == ["Worst case: 89,720 tokens (70%)", "Worst case: 107,120 tokens (84%)"]
    assert panel["bars"] == [
        "A chat turn at its largest, out of a 128,000-token window: Fixed prompt room 12,000, Reply 13,920, "
        "History, at its hard limit 29,000, When you ask 29,000, Largest result 5,800, Free 38,280 tokens.",
        "A work turn at its largest, out of a 128,000-token window: Fixed prompt room 12,000, Reply 13,920, "
        "Task log 5,800, Work 69,600, Largest result 5,800, Free 20,880 tokens.",
    ]
    # The legend repeats every number as text: the bar is never colour-only.
    assert panel["legends"] == [
        ["12,000", "13,920", "29,000", "29,000", "5,800", "38,280"],
        ["12,000", "13,920", "5,800", "69,600", "5,800", "20,880"],
    ]
    assert panel["summaries"] == [
        "Summarizing: at 80%, keep 50% + 10% notes, hard limit 125%",
        "Summarizing: keep 60%, summary 20%",
    ]


def test_a_starting_point_is_pressed_and_a_click_applies_it() -> None:
    payload = _render_system_settings()
    panel = payload["panel"]
    assert panel["presets"] == [
        {"id": "small", "label": "Small (32K)", "pressed": "false"},
        {"id": "medium", "label": "Medium (128K)", "pressed": "true"},
        {"id": "large", "label": "Large (256K)", "pressed": "false"},
    ]
    assert panel["custom"] == 0, "a preset is active, so no Custom chip"

    after = payload["afterPreset"]
    assert after["posts"] == [{"url": "/api/settings/context-presets/large", "method": "POST"}]
    assert "/api/settings/context-budget" in after["refetched"], "the tab re-renders with the new numbers"
    assert [preset["pressed"] for preset in after["panel"]["presets"]] == ["false", "false", "true"]
    assert after["panel"]["worst"] == ["Worst case: 175,480 tokens (69%)", "Worst case: 212,080 tokens (83%)"]
    assert after["panel"]["derived"]["context_work_percent"] == "= 146,400 tokens"


def test_the_history_budget_is_a_share_of_the_usable_window() -> None:
    """History is a percent of the usable window; a save that lets a chat turn outgrow it is refused."""
    for bad in ("0", "100"):
        res = _put_setting("context_history_percent", bad, "context")
        assert res.status_code == 400
        assert res.json()["detail"] == "History must be a whole number from 1 to 99."
    res = _put_setting("context_history_percent", "16k", "context")
    assert res.status_code == 400
    assert res.json()["detail"] == "History must be a whole number."
    res = _put_setting("context_history_percent", "50", "context")
    assert res.status_code == 400
    assert res.json()["detail"] == (
        "A chat turn could use 104.5% of the usable window (Reply 12% + History up to 62.5% + "
        "When you ask 25% + Largest result 5%). Lower one so the total is 100% or less."
    )
    res = _put_setting("context_history_percent", "25", "context")
    assert res.status_code == 200, res.text
    assert config.require_int("context_history_percent") == 25
    from core.agent_loop.conversation_notes import read_settings

    assert read_settings().budget_tokens == 116_000 * 25 // 100


def test_conversation_switch_accepts_only_true_or_false() -> None:
    bad = _put_setting("conversation_compaction_enabled", "yes", "context")
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Conversation memory must be true or false."
    assert _put_setting("conversation_compaction_enabled", "false", "context").status_code == 200
    assert config.get("conversation_compaction_enabled") == "false"


@pytest.mark.parametrize(
    ("key", "bad", "detail"),
    [
        ("conversation_compaction_start_percent", "0", "Start summarizing at must be a whole number from 1 to 100."),
        ("conversation_compaction_start_percent", "101", "Start summarizing at must be a whole number from 1 to 100."),
        ("conversation_compaction_keep_percent", "5.5", "Keep recent messages must be a whole number from 1 to 100."),
        ("conversation_notes_max_percent", "-1", "Notes size must be a whole number from 1 to 100."),
        ("conversation_history_ceiling_percent", "99", "Hard limit must be a whole number from 100 to 300."),
        ("conversation_history_ceiling_percent", "301", "Hard limit must be a whole number from 100 to 300."),
        (
            "conversation_compaction_keep_percent",
            "80",
            "Keep recent messages (80%) must be lower than Start summarizing at (80%), "
            "or there would be nothing to summarize.",
        ),
        (
            "conversation_compaction_start_percent",
            "40",
            "Keep recent messages (50%) must be lower than Start summarizing at (40%), "
            "or there would be nothing to summarize.",
        ),
        ("conversation_notes_max_percent", "50", "Notes size (50%) must be lower than Keep recent messages (50%)."),
    ],
)
def test_conversation_percents_are_refused_out_of_range_or_out_of_order(key: str, bad: str, detail: str) -> None:
    before = config.get(key)
    res = _put_setting(key, bad, "context")
    assert res.status_code == 400
    assert res.json()["detail"] == detail
    config.reload()
    assert config.get(key) == before


def test_conversation_percents_accept_a_consistent_set_and_refresh_chat() -> None:
    for key, value in (
        ("conversation_compaction_start_percent", "100"),
        ("conversation_compaction_keep_percent", "30"),
        ("conversation_notes_max_percent", "5"),
        # 200% of History 20% is 40%: with Reply, When you ask and Largest result, 82%.
        ("conversation_history_ceiling_percent", "200"),
    ):
        res = _put_setting(key, value, "context")
        assert res.status_code == 200, res.text
        assert config.get(key) == value
    from api.routes.settings import _operator_surfaces_for_setting

    for key, _default in CONVERSATION_SETTINGS:
        assert _operator_surfaces_for_setting(key, "context") == ["system", "chat"]
    # The numbers that size History change what an open transcript marks too.
    for key in ("context_window_tokens", "context_fixed_tokens", "context_history_percent"):
        assert _operator_surfaces_for_setting(key, "context") == ["system", "chat"]


def test_a_hard_limit_that_lets_a_chat_turn_outgrow_the_window_is_refused() -> None:
    res = _put_setting("conversation_history_ceiling_percent", "300", "context")
    assert res.status_code == 400
    assert res.json()["detail"].startswith("A chat turn could use 102% of the usable window")
    config.reload()
    assert config.get("conversation_history_ceiling_percent") == "125"


THREAD_KEYS = [
    "channel_router_transcript_messages",
    "channel_response_round_cap",
    "channel_idle_check_enabled",
    "channel_idle_check_delay_seconds",
    "channel_idle_check_max_age_minutes",
    "channel_idle_check_max_wakes",
    "channel_idle_check_max_attempts",
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


def test_a_need_opens_context_window_with_its_field_focused() -> None:
    """A work-summary need's button opens System Settings on Context Window at Work summary size.

    ``render(el, {tab, key})`` shows that tab and brings the field into view,
    focused, with its value selected; an unknown key logs an error and
    focuses nothing.
    """
    opened = _render_system_settings()["openedFromNeed"]
    assert opened["heading"] == "Context Window"
    assert opened["openDetails"] == ["Summarizing: keep 60%, summary 20%"], "its fine-tuning is opened"
    assert opened["fields"] == [{
        "key": "work_snapshot_summary_percent",
        "focused": True,
        "selected": True,
        "scrolled": {"block": "center"},
    }]
    assert opened["unknown"] == {
        "errors": ["[settings-system] no field for setting no_such_setting"],
        "fields": [],
    }


_GLOBAL_AUTO_APPROVE_WARNING = (
    "Every thread and DM will let System AI approve commands that need approval, "
    "including installs, deletes inside projects, and remote actions your agents' "
    "tasks ask for. Never-allowed rules, the path jail and whole-project deletes "
    "still stop or ask. Turn on?"
)


def test_the_advanced_tab_has_no_read_limit_control() -> None:
    assert _render_system_settings()["readLimit"] == {"field": False, "labels": []}


def test_global_auto_approve_asks_before_turning_on_only() -> None:
    """Settings → Advanced: Global auto-approve confirms on enable, never on disable.

    A declined confirm saves nothing and puts the pill back. A save tells
    open conversations (`chat`) so their switches grey out or come back. A
    refused save puts the pill back and says why on the card.
    """
    flow = _render_system_settings()["globalAutoApprove"]
    on = "/api/settings/cli_auto_approve_global?value=true&category=advanced"
    off = "/api/settings/cli_auto_approve_global?value=false&category=advanced"
    assert flow["before"] == {"name": "Global auto-approve", "checked": "false"}
    assert flow["declined"] == {
        "asked": [_GLOBAL_AUTO_APPROVE_WARNING],
        "saves": [],
        "invalidations": [],
        "checked": "false",
    }
    assert flow["accepted"] == {
        "asked": [_GLOBAL_AUTO_APPROVE_WARNING],
        "saves": [{"url": on, "method": "PUT"}],
        "invalidations": [["advanced-system", "chat"]],
        "checked": "true",
    }
    assert flow["turnedOff"] == {
        "asked": [],
        "saves": [{"url": off, "method": "PUT"}],
        "invalidations": [["advanced-system", "chat"]],
        "checked": "false",
    }
    assert flow["refused"] == {
        "asked": [_GLOBAL_AUTO_APPROVE_WARNING],
        "saves": [{"url": on, "method": "PUT"}],
        "invalidations": [],
        "checked": "false",
        "error": {"text": "Global auto-approve must be true or false.", "role": "alert"},
    }


# (key, label named by the 400, a valid value)
THREAD_POSITIVE_INT_KEYS = (
    ("channel_response_round_cap", "Round cap per message", "32"),
    ("channel_idle_check_delay_seconds", "Idle check delay", "60"),
    ("channel_idle_check_interval_seconds", "Idle check scan interval", "10"),
    ("channel_idle_check_max_age_minutes", "Idle check max age", "15"),
    ("channel_idle_check_max_wakes", "Idle check max wakes", "1"),
    ("channel_idle_check_max_attempts", "Idle check attempts", "5"),
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
            str(JS / "context" / "agent-fields.js"),
            str(JS / "settings" / "settings-system-ai.js"),
            str(JS / "settings" / "settings-connections.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


SYSTEM_AI_HINT = (
    "Used for compaction, channel routing and auto-approve reviews. "
    "Off or Low thinking is usually enough."
)
DESCRIPTION = "p:Manage your LLM provider API connections."
CONNECTION_OPTIONS = [
    {"value": "conn-plain", "label": "Local (mock-small)"},
    {"value": "conn-quote", "label": 'Bob "fast" <Local> (gpt-4)'},
]


def test_system_ai_is_one_row_under_the_description() -> None:
    payload = _render_connections()
    unset = payload["unset"]
    assert unset["heading"] == "AI Connections"
    # Title, description, then the System AI block, in one column.
    assert unset["order"] == ["h2:AI Connections", DESCRIPTION, "system-ai"]
    assert unset["label"] == "System AI"
    # No native <select>: two menu selects, both the toolbar 'button' look.
    assert unset["nativeSelects"] == 0
    assert unset["menuSelects"] == ["System AI connection", "System AI thinking"]
    assert unset["connection"]["variant"] == "button"
    assert unset["thinking"]["variant"] == "button"
    # Exactly one hint line under the row.
    assert unset["paragraphs"] == [SYSTEM_AI_HINT]
    # The two dropdowns come before Add Connection.
    assert [c["label"] for c in unset["controls"][:2]] == ["System AI connection", "System AI thinking"]
    assert unset["controls"][2]["id"] == "btn-add-connection"
    assert unset["saves"] == []
    assert unset["connection"]["value"] == "conn-plain"
    assert unset["connection"]["options"] == CONNECTION_OPTIONS
    assert set(payload["fetches"]) == {"/api/settings", "/api/connections"}

    assert payload["swapped"] == [
        {
            "url": "/api/settings/system_ai_connection?value=conn-quote&category=llm",
            "method": "PUT",
        },
    ]

    kept = payload["kept"]
    assert kept["saves"] == []
    assert kept["connection"]["value"] == "conn-quote"

    gone = payload["gone"]
    assert gone["saves"] == []
    assert gone["connection"]["value"] == "conn-plain"
    assert gone["connection"]["options"] == CONNECTION_OPTIONS

    # Each state that cannot draw the row is one line and no controls.
    for state, line in (
        ("failedLoad", "AI connections could not be loaded."),
        ("settingsFailed", "System AI could not be loaded."),
        ("emptyList", "No AI connections yet."),
        ("emptyListKept", "No AI connections yet."),
    ):
        view = payload[state]
        assert view["saves"] == [], state
        assert view["order"] == ["h2:AI Connections", DESCRIPTION, "system-ai"], state
        assert view["paragraphs"] == [line], state
        assert view["menuSelects"] == [], state
        assert view["nativeSelects"] == 0, state
        assert view["controls"][0]["id"] == "btn-add-connection", state
    assert "Failed to load connections." in payload["failedLoad"]["pageText"]
    assert "No connections yet" in payload["emptyList"]["pageText"]
    assert "Saved connection unavailable" not in payload["emptyListKept"]["pageText"]


def _choices(options: list[dict]) -> list[tuple[str, str]]:
    return [(opt["value"], opt["label"]) for opt in options]


def test_system_ai_thinking_follows_the_effective_connection() -> None:
    payload = _render_connections()

    # Unset System AI falls back to the first connection, which offers off/low.
    unset = payload["unset"]["thinking"]
    assert unset["label"] == "System AI thinking"
    assert unset["value"] == "default"
    assert _choices(unset["options"]) == [
        ("default", "Thinking: Server default"), ("off", "Thinking: Off"), ("low", "Thinking: Low"),
    ]
    # A saved id uses that connection's levels.
    assert _choices(payload["kept"]["thinking"]["options"]) == [
        ("default", "Thinking: Server default"), ("high", "Thinking: High"),
    ]
    # A stale id falls back to the first connection, as resolve_system_connection does.
    assert _choices(payload["gone"]["thinking"]["options"]) == [
        ("default", "Thinking: Server default"), ("off", "Thinking: Off"), ("low", "Thinking: Low"),
    ]
    # Switching the connection re-options the control.
    assert _choices(payload["swappedThinking"]) == [
        ("default", "Thinking: Server default"), ("high", "Thinking: High"),
    ]
    # A stored level the fallback lacks is shown, marked, not replaced.
    stored = payload["unofferedStored"]["thinking"]
    assert stored["value"] == "high"
    assert stored["options"][-1] == {"value": "high", "label": "Thinking: High (not offered)"}
    assert payload["unofferedStored"]["saves"] == []

    # A missing thinking row keeps the connection control and says so in
    # place of the thinking control, still over the one hint line.
    missing = payload["thinkingMissing"]
    assert missing["thinking"] is None
    assert missing["menuSelects"] == ["System AI connection"]
    assert missing["rowText"] == "System AI System AI thinking could not be loaded."
    assert missing["paragraphs"] == [SYSTEM_AI_HINT]


def test_system_ai_thinking_saves_and_resets_before_a_switch() -> None:
    payload = _render_connections()

    assert payload["levelSaved"] == {
        "saves": [{"url": "/api/settings/system_ai_thinking?value=low&category=llm", "method": "PUT"}],
        "value": "low",
    }
    refused = payload["levelRefused"]
    assert refused["saves"] == [
        {"url": "/api/settings/system_ai_thinking?value=off&category=llm", "method": "PUT"},
    ]
    assert refused["value"] == "low"
    assert refused["errors"] == [
        "System AI thinking could not be saved: "
        "System AI connection 'Local' does not offer thinking level 'off'.",
    ]

    # The stored "low" is not offered by conn-quote: default first, then the connection.
    orphan = payload["orphanSwitch"]
    assert orphan["saves"] == [
        {"url": "/api/settings/system_ai_thinking?value=default&category=llm", "method": "PUT"},
        {"url": "/api/settings/system_ai_connection?value=conn-quote&category=llm", "method": "PUT"},
    ]
    assert orphan["value"] == "default"
    assert _choices(orphan["options"]) == [("default", "Thinking: Server default"), ("high", "Thinking: High")]

    # A refused reset saves no connection and the connection control goes back.
    reset = payload["resetRefused"]
    assert reset["saves"] == [
        {"url": "/api/settings/system_ai_thinking?value=default&category=llm", "method": "PUT"},
    ]
    assert reset["connection"] == "conn-plain"
    assert reset["value"] == "low"
    assert reset["errors"] == ["System AI thinking could not be reset: nope: reset refused"]

    # A refused connection save goes back, and the levels stay the old connection's.
    conn = payload["connectionRefused"]
    assert conn["saves"] == [
        {"url": "/api/settings/system_ai_connection?value=conn-quote&category=llm", "method": "PUT"},
    ]
    assert conn["connection"] == "conn-plain"
    assert _choices(conn["options"]) == [
        ("default", "Thinking: Server default"), ("off", "Thinking: Off"), ("low", "Thinking: Low"),
    ]
    assert conn["errors"] == ["System AI could not be saved: nope: connection refused"]


def test_system_ai_picker_source_is_under_ai_connections() -> None:
    connections = (JS / "settings" / "settings-connections.js").read_text(encoding="utf-8")
    system_ai = (JS / "settings" / "settings-system-ai.js").read_text(encoding="utf-8")
    # The System section is its renderer plus its setting catalog.
    system = (JS / "settings" / "settings-system.js").read_text(encoding="utf-8") + (
        JS / "settings" / "settings-system-meta.js"
    ).read_text(encoding="utf-8")
    completion = (ROOT / "core" / "llm" / "system_completion.py").read_text(encoding="utf-8")
    assert "system_ai_connection" not in system
    h2 = connections.index('<h2 class="text-lg font-semibold">AI Connections</h2>')
    description = connections.index("Manage your LLM provider API connections.")
    block = connections.index("${BossModSystemAi.markup(state)}")
    empty = connections.index("No connections yet")
    edit = connections.index("data-edit-conn")
    assert h2 < description < block < empty
    assert block < edit
    # The System AI save path lives in its own module, not a generic handler.
    assert 'data-setting-key="system_ai_connection"' not in connections
    assert "setting-input" not in connections
    assert "<select" not in system_ai
    assert "variant:" not in system_ai
    assert not (JS / "settings" / "settings-system-ai-thinking.js").exists()
    assert "set_setting" not in completion
    assert "never blocks the agent turn" in system


def test_retention_limits_save_only_whole_numbers_in_range() -> None:
    """Settings → Advanced: the retention limits and prune interval share one card and one save.

    A value that is not a whole number in the field's range is reported
    under the field and never sent. A setting the server does not have is
    shown as not configured, not as a made-up default.
    """
    flow = _render_system_settings()["retention"]
    assert flow["labels"] == [
        "Diagnostics Retention Limit",
        "Diagnostics Retention (days)",
        "Finished Wake-up Retention (days)",
        "Activity Log Retention (days)",
        "Background Process History (days)",
        "History Prune Interval (minutes)",
    ]
    assert flow["values"] == ["5000", "7", "7", "14", "60"]
    for case, message in (
        ("zeroDays", "Enter a whole number, 1 or more."),
        ("fraction", "Enter a whole number, 1 or more."),
        ("text", "Enter a whole number, 1 or more."),
        ("belowRowMin", "Enter a whole number, 100 to 50000."),
        ("negativeInterval", "Enter a whole number, 1 or more."),
    ):
        assert flow[case] == {"saves": [], "error": message, "invalid": "true"}, case
    assert flow["goodDays"] == {
        "saves": ["/api/settings/diagnostics_retention_days?value=14&category=advanced"],
        "error": "",
        "invalid": "false",
    }
    assert flow["goodRows"] == {
        "saves": ["/api/settings/diagnostics_retention_limit?value=8000&category=advanced"],
        "error": "",
        "invalid": "false",
    }
    assert flow["goodInterval"] == {
        "saves": ["/api/settings/history_prune_interval_minutes?value=30&category=advanced"],
        "error": "",
        "invalid": "false",
    }
    assert flow["missing"] == {
        "disabled": True,
        "error": "Not configured. Reset Seed Settings restores it.",
    }
