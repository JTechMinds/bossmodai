"""Shared runtime core states notes store/retrieve and pointers-first citation."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.agent_loop.runtime_core import NOTES_STORE_RETRIEVE, preview_runtime_core
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
    assert "/me/notes" in block
    assert "soft-empty is normal" in block
    assert "/projects/<project>/" in block
    assert "/projects/<project>/notes" not in block
    assert "project facts, decisions, and evidence pointers" in block
    assert "Operator prefs and standing personal context" in block
    assert "standing preference or style constraint" in block
    assert "short sticky plus a path pointer under /me/notes" in block
    assert "not only in chat" in block
    assert "Short sticky bullets" in block
    assert "open and read those project notes" in block
    assert "style, tool choice, or a quality bar" in block
    assert "Do not treat standing prefs as disposable chat tone" in block
    assert "pointers-first (path + short sticky)" in block
    assert "standing_pref" not in block
    assert "sticky-slot" not in block.lower()
    assert "chat fade" not in block.lower()
    assert "Soft-cap any quote" in block
    assert "Never dump a whole note file into the turn" in block
    assert "Fail and Done still need real evidence paths" in block
    assert "invent Board state" in block
    assert "Blocked to Done" in block
    assert "drop open conditions" in block
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
    assert "pointers-first (path + short sticky)" in joined
    assert "Never dump a whole note file into the turn" in joined

    system_prompt = (_REPO_ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
    assert "{{runtime_core}}" in system_prompt
    assert "/me/notes" not in system_prompt
    assert "pointers-first" not in system_prompt
