"""Extension prompt blocks in the working prompt, and shutdown with the worker."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import db
from tests._connections import model_connection
from core import config
from core.extensions.paths import extension_data_dir
from core.extensions.registry import get_discovery, set_enabled
from core.llm import context_builder

_BV = "browser-vision"
_MARKER = "## Browser Vision"


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    (extension_data_dir(_BV) / "ready.json").unlink(missing_ok=True)


def teardown_function() -> None:
    (extension_data_dir(_BV) / "ready.json").unlink(missing_ok=True)
    db.close_connection()


def _mark_ready() -> None:
    data_dir = extension_data_dir(_BV)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "ready.json").write_text(json.dumps({"browser": "test", "browser_kind": "chromium"}), encoding="utf-8")


def _context(agent, contract_kind: str) -> list[dict]:
    trigger = (
        {"type": "human_chat", "content": "Open example.com", "from_name": "Human Operator"}
        if contract_kind == "decision"
        else {"type": "activity_resumed", "content": "Continue."}
    )
    turn = context_builder.TurnContext(
        agent=agent,
        state=db.get_agent_state(agent.id),
        trigger=trigger,
        conversation_history=[],
        prompt_notifications=[],
        reference_materials=[],
        contract_kind=contract_kind,
    )
    return context_builder.build_context(turn)


def _has_block(messages: list[dict]) -> bool:
    return any(m["role"] == "system" and m["content"].startswith(_MARKER) for m in messages)


@pytest.mark.parametrize("contract_kind", ["decision", "execution"])
def test_the_block_needs_enabled_ready_and_a_vision_model(contract_kind: str) -> None:
    db.set_supports_images("vision-model", True)
    seer = db.create_agent("Seer", role="Researcher", connection_id=model_connection("vision-model"))
    blind = db.create_agent("Scribe", role="Writer", connection_id=model_connection("text-model"))

    set_enabled(_BV, True)
    assert not _has_block(_context(seer, contract_kind)), "enabled but not set up"

    _mark_ready()
    assert _has_block(_context(seer, contract_kind))
    assert not _has_block(_context(blind, contract_kind)), "model is not image-capable"

    set_enabled(_BV, False)
    assert not _has_block(_context(seer, contract_kind)), "set up but disabled"


def test_the_block_sits_after_file_guidance_and_before_history() -> None:
    db.set_supports_images("vision-model", True)
    seer = db.create_agent("Seer", role="Researcher", connection_id=model_connection("vision-model"))
    _mark_ready()
    set_enabled(_BV, True)
    messages = _context(seer, "decision")
    index = next(i for i, m in enumerate(messages) if m["content"].startswith(_MARKER))
    assert all(m["role"] == "system" for m in messages[:index])
    prompt = (get_discovery().get(_BV).root / "prompt.md").read_text(encoding="utf-8").strip()
    # R30: the static text first, the live state line last (cache-stable prefix).
    assert messages[index]["content"] == prompt + "\n\nYour browser right now: no page open."


def test_asking_for_the_state_line_does_not_start_the_browser() -> None:
    import threading

    from core.extensions.loader import load_extension

    db.set_supports_images("vision-model", True)
    seer = db.create_agent("Seer", role="Researcher", connection_id=model_connection("vision-model"))
    _mark_ready()
    set_enabled(_BV, True)
    block = next(m["content"] for m in _context(seer, "execution") if m["content"].startswith(_MARKER))
    assert block.endswith("\n\nYour browser right now: no page open.")
    instance = load_extension(get_discovery().get(_BV))
    assert instance._host._thread is None and instance._host._loop is None
    assert not any(thread.name == "browser-vision" for thread in threading.enumerate())


def test_an_extension_without_prompt_state_adds_only_its_static_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.extensions import prompt_blocks

    db.set_supports_images("vision-model", True)
    seer = db.create_agent("Seer", role="Researcher", connection_id=model_connection("vision-model"))
    _mark_ready()
    set_enabled(_BV, True)
    monkeypatch.setattr(prompt_blocks, "load_extension", lambda entry: object())
    prompt = (get_discovery().get(_BV).root / "prompt.md").read_text(encoding="utf-8").strip()
    assert prompt_blocks.render_extension_blocks(seer) == prompt


_MAIL = "ms365-mail"
_MAIL_MARKER = "## Email (Microsoft 365 Mailbox)"


@pytest.mark.parametrize("contract_kind", ["decision", "execution"])
def test_the_mail_block_applies_only_to_agents_with_a_stored_mailbox(contract_kind: str) -> None:
    configured = db.create_agent("Iris", role="Researcher")
    bare = db.create_agent("Vera", role="Writer")
    db.set_extension_agent_config(_MAIL, configured.id, {
        "tenant_id": "t", "client_id": "c", "client_secret": "s", "mailbox": "reports@contoso.com",
    })
    set_enabled(_MAIL, True)

    block = next(
        (m["content"] for m in _context(configured, contract_kind) if m["content"].startswith(_MAIL_MARKER)),
        None,
    )
    assert block is not None
    prompt = (get_discovery().get(_MAIL).root / "prompt.md").read_text(encoding="utf-8").strip()
    # Static text first, the state line last (cache-stable prefix).
    assert block == prompt + "\n\nYour mailbox: reports@contoso.com"
    assert not any(m["content"].startswith(_MAIL_MARKER) for m in _context(bare, contract_kind))

    set_enabled(_MAIL, False)
    assert not any(m["content"].startswith(_MAIL_MARKER) for m in _context(configured, contract_kind))


@pytest.mark.asyncio
async def test_worker_shutdown_shuts_loaded_extensions_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.runtime import worker

    calls: list[str] = []

    async def _stop_services(self) -> None:
        calls.append("services")

    monkeypatch.setattr(worker.RuntimeController, "_stop_services", _stop_services)
    monkeypatch.setattr(worker, "shutdown_loaded_extensions", lambda: calls.append("extensions"))

    await worker.RuntimeController().shutdown()

    assert calls == ["services", "extensions"]


def test_shutdown_loaded_extensions_calls_each_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.extensions import loader

    class _Ext:
        def __init__(self) -> None:
            self.count = 0

        def shutdown(self) -> None:
            self.count += 1

    first, second = _Ext(), _Ext()
    monkeypatch.setattr(loader, "_loaded", {"a": first, "b": second})
    loader.shutdown_loaded_extensions()
    assert (first.count, second.count) == (1, 1)
