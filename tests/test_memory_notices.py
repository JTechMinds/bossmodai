"""The boss's DM line when an agent changes its memory (core/agent_loop/memory_notices.py).

Add and replace link the line to the memory's number, so the chat can open
the desk's Memory layer on that row; remove shows the dropped text and links
nothing. Every change gets its own line: no duplicate suppression.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.memory_notices import memory_change_line, note_memory_change
from core.agent_loop.standing_prefs import Memory, add_memory, remove_memory, replace_memory
from core.bm_cli import filesystem


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
    monkeypatch.setattr(filesystem, "_SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(tmp_path / "company"))
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    yield
    db.close_connection()


def _agent():
    return db.create_agent("Tyler", role="Analyst")


def _links_for(agent_id: str) -> dict[str, tuple[str, str, str]]:
    notes = db.list_notifications(agent_id=agent_id, limit=20)
    links = db.list_notification_links([note.id for note in notes])
    return {note.content: (links[note.id].target_kind, links[note.id].target_path, links[note.id].label)
            for note in notes if note.id in links}


def test_each_change_has_its_sentence() -> None:
    agent = _agent()
    memory = Memory(id=3, text="The boss wants tables, not paragraphs.")
    assert memory_change_line(agent, "add", memory) == "Tyler saved a memory"
    assert memory_change_line(agent, "replace", memory) == "Tyler updated a memory"
    assert memory_change_line(agent, "remove", memory) == (
        "Tyler removed a memory: “The boss wants tables, not paragraphs.”"
    )


def test_an_unknown_change_raises() -> None:
    unknown: Any = "forget"
    with pytest.raises(ValueError, match="unknown memory change"):
        memory_change_line(_agent(), unknown, Memory(id=1, text="x"))


def test_add_and_replace_link_the_number_and_remove_links_nothing() -> None:
    agent = _agent()
    added = add_memory(agent.storage_key, "Acme's contact is Dana Lee.")
    replaced = replace_memory(agent.storage_key, added.id, "Acme's contact is now Sam Ortiz.")
    removed = remove_memory(agent.storage_key, added.id)

    posted_add = note_memory_change(agent, "add", added)["chat_message"]
    posted_replace = note_memory_change(agent, "replace", replaced)["chat_message"]
    posted_remove = note_memory_change(agent, "remove", removed)["chat_message"]

    assert posted_add["memory_id"] == added.id
    assert posted_replace["memory_id"] == added.id
    assert posted_remove["memory_id"] is None
    for posted in (posted_add, posted_replace, posted_remove):
        assert posted["agent_id"] == agent.id
        assert posted["from_type"] == "system"
        assert posted["notification_kind"] == "memory"

    assert _links_for(agent.id) == {
        "Tyler saved a memory": ("memory", str(added.id), "Memory"),
        "Tyler updated a memory": ("memory", str(added.id), "Memory"),
    }
    notes = {note.content: note for note in db.list_notifications(agent_id=agent.id, limit=20)}
    assert set(notes) == {
        "Tyler saved a memory",
        "Tyler updated a memory",
        "Tyler removed a memory: “Acme's contact is now Sam Ortiz.”",
    }
    for note in notes.values():
        assert note.kind == "memory"
        assert note.prompt_visibility is False
        assert note.chat_visible is True
        assert note.policy == "all"
        assert note.source_channel == "chat"


def test_two_identical_adds_in_a_row_post_two_lines() -> None:
    agent = _agent()
    first = add_memory(agent.storage_key, "Ship on Fridays only.")
    second = add_memory(agent.storage_key, "Invoices go out on the 1st.")
    note_memory_change(agent, "add", first)
    note_memory_change(agent, "add", second)
    lines = [note.content for note in db.list_notifications(agent_id=agent.id, limit=20)]
    assert lines == ["Tyler saved a memory", "Tyler saved a memory"]


def test_the_dm_history_carries_the_memory_number_on_the_note() -> None:
    agent = _agent()
    added = add_memory(agent.storage_key, "The boss wants plain English.")
    note_memory_change(agent, "add", added)
    removed = remove_memory(agent.storage_key, added.id)
    note_memory_change(agent, "remove", removed)

    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    client = TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})
    response = client.get(f"/api/agents/{agent.id}/messages")

    assert response.status_code == 200
    rows = {row["content"]: row for row in response.json()}
    saved = rows["Tyler saved a memory"]
    assert saved["memory_id"] == added.id
    assert saved["from"] == "system"
    assert saved["notification_kind"] == "memory"
    assert rows["Tyler removed a memory: “The boss wants plain English.”"]["memory_id"] is None
