"""Shared runtime core states notes store/retrieve and pointers-first citation."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.agent_loop.runtime_core import NOTES_STORE_RETRIEVE, preview_runtime_core
from core.agent_loop.standing_prefs import TEXT_MAX_CHARS
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


def test_runtime_core_states_notes_store_retrieve_and_pointers_first() -> None:
    block = preview_runtime_core(name="Ada", role="Writer")
    assert NOTES_STORE_RETRIEVE in block
    assert "Notes (cold):" in block
    assert "Personal: /me/notes." in block
    assert "Project: /projects/<project>/." in block
    assert "/projects/<project>/notes" not in block
    assert "Open on demand for how-to." in block
    assert "Pointers-first." in block
    assert "Never dump." in block
    assert "Never invent Board/Done from note text." in block
    assert "Standing prefs (warm):" in block
    assert "record it with `pref set <id> <kind> <source>`" in block
    assert f"the rule as one line (≤{TEXT_MAX_CHARS} chars) in the body." in block
    assert "Kinds: preference / constraint / style / tool_bias." in block
    assert "Replace by reusing the id; remove with `pref remove <id>`; see all with `pref list`." in block
    assert "The engine injects them every work turn" in block
    assert "the agent does not re-open prefs for inject." in block
    assert "Supersede only when the operator replaces." in block
    assert "Optional note pointer for prose" in block
    assert "warm inject does not scrape notes." in block
    assert "Invent-key / Board fakes still fail-closed." in block
    assert "standing personal context go in /me/notes" not in block
    assert "short sticky plus a path pointer under /me/notes" not in block
    assert "sticky-slot" not in block.lower()
    # The store is system-owned: the guidance names the command, never a file.
    assert "sticky" not in NOTES_STORE_RETRIEVE.lower()
    assert "/me/standing_prefs.json" not in block
    assert "file" not in NOTES_STORE_RETRIEVE.split("Standing prefs (warm):", 1)[1]
    assert "chat fade" not in block.lower()
    lowered = block.lower()
    assert "memory dump" not in lowered
    assert "paste the note" not in lowered
    assert "include the full note" not in lowered


def test_prompt_assembly_includes_notes_rules_without_a_second_store() -> None:
    """Hire preview and the representative prompt bundle both carry the block."""
    preview = preview_runtime_core(name="Ada", role="Writer")
    assert NOTES_STORE_RETRIEVE in preview

    bundle = context_preview.preview_prompt_bundle("execution", "activity_resumed")
    core_msgs = [
        str(message.get("content") or "")
        for message in bundle["messages"]
        if str(message.get("content") or "").startswith("# Runtime core")
    ]
    assert core_msgs
    assert NOTES_STORE_RETRIEVE in core_msgs[0]
    joined = "\n".join(str(message.get("content") or "") for message in bundle["messages"])
    assert "Pointers-first." in joined
    assert "Never dump." in joined
    assert "warm inject does not scrape notes." in joined

    system_prompt = (_REPO_ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
    assert "{{runtime_core}}" in system_prompt
    assert "/me/notes" not in system_prompt
    assert "pointers-first" not in system_prompt
