"""Shared runtime core: where the agent keeps what it is told.

Memory (shown every turn), the project's project_knowledge.md, and /me/notes.
The hard limit in the guidance is read live from Settings.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.runtime_core import (
    MEMORY_SOFT_TARGET_CHARS,
    format_memory_guidance,
    preview_runtime_core,
)
from core.llm import context_preview


_REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_memory_guidance_names_the_three_homes() -> None:
    guidance = format_memory_guidance()
    lines = guidance.splitlines()
    assert lines[0].startswith("Memory: the few things you must never forget. Your memory is shown to you on every turn")
    assert lines[0].endswith("Not project details.")
    assert lines[1] == (
        f"- Keep each memory to 1–2 short sentences (about {MEMORY_SOFT_TARGET_CHARS} characters; at most 400)."
    )
    # A reply saves through the decision's `remember`; work turns use `memory add`.
    assert lines[2] == (
        "- When someone tells you something like that, save it in this turn: in a reply, put the "
        "sentence in `remember`; while working, `memory add` with the sentence in the body. "
        "The system numbers it."
    )
    assert "`memory replace <n>`" in lines[3] and "`memory remove <n>`" in lines[3]
    assert "`memory list` shows everything." in lines[3]
    # Project facts go to the shared project doc, in any turn.
    assert "/projects/<project>/project_knowledge.md" in lines[4]
    assert "This is not task work; do it in any turn." in lines[4]
    # Personal how-to stays cold.
    assert lines[5].startswith("- /me/notes is for things you need rarely but long term")
    assert "Not shown automatically; open it when you need it." in lines[5]
    # The system tells the boss about a save; the agent does not announce it.
    assert (
        "The boss is told automatically when you save; you don't need to mention it." in lines[6]
    )
    assert "say so in a few words" not in guidance
    assert lines[6].endswith(
        "Saving is never task progress or Done. A correction to how you do your work, "
        "including a recurring job, is lasting, not a one-off."
    )
    assert len(lines) == 7
    # The retired command and its ceremony are gone.
    for retired in ("`pref", "Standing prefs", "kind", "sources", "Pointers-first"):
        assert retired not in guidance


def test_the_hard_limit_follows_the_setting_live() -> None:
    assert "at most 400)" in format_memory_guidance()
    db.set_setting("standing_prefs_line_max_chars", "250", "context")
    config.reload()
    assert "at most 250)" in format_memory_guidance()
    assert f"(about {MEMORY_SOFT_TARGET_CHARS} characters; at most 250)" in preview_runtime_core(name="Ada")


def test_an_invalid_hard_limit_fails_loudly() -> None:
    db.set_setting("standing_prefs_line_max_chars", "0", "context")
    config.reload()
    with pytest.raises(config.ConfigError, match="standing_prefs_line_max_chars"):
        format_memory_guidance()


def test_prompt_assembly_includes_the_memory_guidance_once() -> None:
    """Hire preview and the representative prompt bundle both carry the block."""
    preview = preview_runtime_core(name="Ada", role="Writer")
    assert format_memory_guidance() in preview

    bundle = context_preview.preview_prompt_bundle("execution", "activity_resumed")
    core_msgs = [
        str(message.get("content") or "")
        for message in bundle["messages"]
        if "# Runtime core" in str(message.get("content") or "").splitlines()
    ]
    # The runtime core reaches the model once, through the system prompt slot.
    assert len(core_msgs) == 1
    assert str(core_msgs[0]).splitlines().count("# Runtime core") == 1
    assert format_memory_guidance() in core_msgs[0]

    system_prompt = (_REPO_ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
    assert "{{runtime_core}}" in system_prompt
    assert "/me/notes" not in system_prompt
