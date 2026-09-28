"""Extension prompt blocks in the working prompt, and shutdown with the worker."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import db
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
    (data_dir / "ready.json").write_text(json.dumps({"browser": "test"}), encoding="utf-8")


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
    seer = db.create_agent("Seer", role="Researcher", model_work="vision-model")
    blind = db.create_agent("Scribe", role="Writer", model_work="text-model")

    set_enabled(_BV, True)
    assert not _has_block(_context(seer, contract_kind)), "enabled but not set up"

    _mark_ready()
    assert _has_block(_context(seer, contract_kind))
    assert not _has_block(_context(blind, contract_kind)), "model is not image-capable"

    set_enabled(_BV, False)
    assert not _has_block(_context(seer, contract_kind)), "set up but disabled"


def test_the_block_sits_after_file_guidance_and_before_history() -> None:
    db.set_supports_images("vision-model", True)
    seer = db.create_agent("Seer", role="Researcher", model_work="vision-model")
    _mark_ready()
    set_enabled(_BV, True)
    messages = _context(seer, "decision")
    index = next(i for i, m in enumerate(messages) if m["content"].startswith(_MARKER))
    assert all(m["role"] == "system" for m in messages[:index])
    prompt = (get_discovery().get(_BV).root / "prompt.md").read_text(encoding="utf-8").strip()
    assert messages[index]["content"] == prompt


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
