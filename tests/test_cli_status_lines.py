"""CLI status lines and live-view nudges: what a command's result tells the operator."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.turn_helpers import announce_extension_result, post_cli_status_lines
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking.service import create_or_bind_task


class _RecordingSink(NullRuntimeEventSink):
    def __init__(self) -> None:
        self.chat: list[dict[str, Any]] = []
        self.channel: list[dict[str, Any]] = []
        self.live: list[tuple[str | None, str | None]] = []

    async def broadcast_chat_message(self, **kwargs: Any) -> None:
        self.chat.append(kwargs)

    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        self.channel.append(kwargs)

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        self.live.append((extension_id, agent_id))


@pytest.fixture()
def sink() -> _RecordingSink:
    recording = _RecordingSink()
    runtime_events.set_sink(recording)
    yield recording
    runtime_events.set_sink(NullRuntimeEventSink())


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _task(agent_id: str, *, channel_id: str | None):
    return create_or_bind_task(
        title="Research the pricing page",
        description="Look at it in the browser.",
        project=None,
        assigned_to=agent_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel" if channel_id else "chat",
        notification_policy="completion_blocked",
        notification_channel_id=channel_id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task


def _channel_lines(channel_id: str) -> list[str]:
    return [m.content for m in db.list_channel_messages(channel_id) if m.author_type == "system"]


def _chat_lines(agent_id: str) -> list[str]:
    # Agent-chat status lines are stored as notifications (see notifications.py).
    return [note.content for note in db.list_notifications(agent_id=agent_id, limit=50)]


@pytest.mark.asyncio
async def test_a_bound_task_posts_to_its_origin_and_broadcasts_once(sink, monkeypatch) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    channel = db.create_channel(name="Pricing", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    task = _task(agent.id, channel_id=channel.id)
    other = db.create_channel(name="Elsewhere", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)

    import core.agent_loop.turn_helpers as helpers

    calls: list[dict[str, Any]] = []
    real = helpers.persist_origin_status_line
    monkeypatch.setattr(helpers, "persist_origin_status_line", lambda **kw: calls.append(kw) or real(**kw))

    # The task's origin wins over the turn's channel.
    await post_cli_status_lines(agent, task_id=task.id, channel_id=other.id,
                                lines=["Browsing example.com"], command="bv open example.com")

    assert [call["content"] for call in calls] == ["Iris Browsing example.com"]
    assert calls[0]["kind"] == "progress" and calls[0]["task"].id == task.id
    assert "Iris Browsing example.com" in _channel_lines(channel.id)
    assert _channel_lines(other.id) == []
    assert [item["content"] for item in sink.channel] == ["Iris Browsing example.com"]
    assert sink.chat == []


@pytest.mark.asyncio
async def test_without_a_task_a_channel_gets_the_line(sink) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    channel = db.create_channel(name="Pricing", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    await post_cli_status_lines(agent, task_id=None, channel_id=channel.id,
                                lines=["Browsing example.com", "Downloaded x.pdf to /me/downloads"],
                                command="bv click 3")
    assert _channel_lines(channel.id)[-2:] == ["Iris Browsing example.com", "Iris Downloaded x.pdf to /me/downloads"]
    assert [item["channel_id"] for item in sink.channel] == [channel.id, channel.id]


@pytest.mark.asyncio
async def test_without_task_or_channel_the_agents_chat_gets_the_line(sink) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    await post_cli_status_lines(agent, task_id=None, channel_id=None,
                                lines=["Browsing example.com"], command="bv open example.com")
    assert "Iris Browsing example.com" in _chat_lines(agent.id)
    assert [item["content"] for item in sink.chat] == ["Iris Browsing example.com"]
    assert sink.channel == []


@pytest.mark.asyncio
async def test_an_identical_recent_line_is_not_posted_twice(sink) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    for _ in range(2):
        await post_cli_status_lines(agent, task_id=None, channel_id=None,
                                    lines=["Browsing example.com"], command="bv open example.com")
    assert _chat_lines(agent.id).count("Iris Browsing example.com") == 1
    assert len(sink.chat) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["Browsing example.com", ["ok", ""], ["ok", 3], None, ["   "]])
async def test_invalid_status_lines_are_logged_and_nothing_is_posted(sink, caplog, bad) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    with caplog.at_level(logging.ERROR):
        await post_cli_status_lines(agent, task_id=None, channel_id=None, lines=bad, command="bv open x")
    assert "CLI command 'bv open x' returned invalid status_lines" in caplog.text
    assert _chat_lines(agent.id) == [] and sink.chat == [] and sink.channel == []


@pytest.mark.asyncio
async def test_no_lines_posts_nothing(sink) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    await post_cli_status_lines(agent, task_id=None, channel_id=None, lines=[], command="bv view")
    assert _chat_lines(agent.id) == [] and sink.chat == []


# ─── both turn kinds post a command's lines ───


def _bv_result(command: str, lines: list[str]):
    from core.bm_cli.types import BossModCliResult

    return BossModCliResult(
        command=command, ok=True, detail="browsed", prompt_content="BOSSMOD CLI RESULT\nbrowsed",
        kind="browser", data={"status_lines": lines},
    )


def _script(monkeypatch, contents: list[str]) -> None:
    from core.llm.client import LLMResponse

    queue = list(contents)

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        return LLMResponse(content=queue.pop(0), model="test/mock", prompt_tokens=8,
                           completion_tokens=4, total_tokens=12)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)


@pytest.mark.asyncio
async def test_a_decision_turn_bv_open_posts_the_browsing_line_to_its_thread(sink, monkeypatch) -> None:
    from core.agent_loop.loop import run_turn

    agent = db.create_agent("Iris", role="Researcher", model_work="test/mock")
    peer = db.create_agent("Peer")
    channel = db.create_channel(name="Pricing", member_agent_ids=[agent.id, peer.id], created_by=agent.id)
    seen: dict[str, Any] = {}

    def _fake_cli(agent_obj, state_obj, command, content=None, **kwargs):
        seen["channel_id"] = kwargs.get("channel_id")
        return _bv_result(command, ["Browsing example.com"])

    monkeypatch.setattr("core.agent_loop.decision_turn.execute_bm_cli", _fake_cli)
    _script(monkeypatch, [
        '{"act":"cli","data":{"cmd":"bv open example.com"},"th":"look"}',
        '{"act":"reply","work_commit":false,"intent":"question","msg":"Opened it.","th":"answer"}',
    ])
    trigger = {
        "type": "human_chat", "content": "Open example.com", "from_name": "Human",
        "from_id": HUMAN_SENDER_ID, "source_channel": "channel", "channel_id": channel.id,
        "author_type": "human",
    }
    outcome = await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert outcome.trigger_status == "completed"
    assert seen["channel_id"] == channel.id
    assert "Iris Browsing example.com" in _channel_lines(channel.id)
    assert any(item["content"] == "Iris Browsing example.com" for item in sink.channel)


@pytest.mark.asyncio
async def test_an_execution_turn_bv_open_posts_the_browsing_line_to_the_task_origin(sink, monkeypatch) -> None:
    from core.agent_loop import activity_runtime
    from core.agent_loop.loop import run_turn

    agent = db.create_agent("Iris", role="Researcher", model_work="test/mock")
    task = _task(agent.id, channel_id=None)
    activity_runtime.activate_work_activity(agent.id, task, task_status="active")

    monkeypatch.setattr(
        "core.agent_loop.actions_cli.execute_bm_cli",
        lambda agent_obj, state_obj, command, content=None, **kwargs: _bv_result(command, ["Browsing example.com"]),
    )
    _script(monkeypatch, [
        '{"act":"cli","data":{"cmd":"bv open example.com"},"th":"look"}',
        '{"act":"idle","data":{},"th":"looked"}',
    ])
    trigger = {"type": "activity_resumed", "task_id": task.id, "content": "Resume.", "source_channel": "work"}
    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert "Iris Browsing example.com" in _chat_lines(agent.id)
    assert any(item["content"] == "Iris Browsing example.com" for item in sink.chat)



# ─── live-view nudges (R37) ───


@pytest.mark.asyncio
async def test_a_live_view_extension_result_is_announced_once(sink) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    await announce_extension_result(agent, {"extension_id": "browser-vision", "status_lines": []})
    assert sink.live == [("browser-vision", agent.id)]


@pytest.mark.asyncio
async def test_an_extension_without_a_live_view_is_not_announced(sink, monkeypatch, tmp_path) -> None:
    import json

    from core.bm_cli.command_registry import CORE_COMMAND_NAMES
    from core.extensions.registry import discover

    folder = tmp_path / "plain-ext"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "id": "plain-ext", "name": "Plain", "version": "0.1.0", "description": "No live view.",
        "command": {"name": "plain", "summary": "s", "usage": "plain go", "help": "h"},
        "setup": {"required": False},
    }), encoding="utf-8")
    (folder / "__init__.py").write_text("def create(ctx):\n    raise AssertionError('not loaded')\n", encoding="utf-8")
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    assert found.get("plain-ext").valid
    monkeypatch.setattr("core.agent_loop.turn_helpers.get_discovery", lambda: found)

    agent = db.create_agent("Iris", role="Researcher")
    await announce_extension_result(agent, {"extension_id": "plain-ext"})
    assert sink.live == []


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, {}, {"status_lines": ["Browsing example.com"]}, {"extension_id": None}])
async def test_non_extension_data_is_not_announced(sink, data) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    await announce_extension_result(agent, data)
    assert sink.live == []


@pytest.mark.asyncio
async def test_an_unknown_stamped_extension_is_logged_and_not_announced(sink, caplog) -> None:
    agent = db.create_agent("Iris", role="Researcher")
    with caplog.at_level(logging.ERROR):
        await announce_extension_result(agent, {"extension_id": "no-such-extension"})
    assert "CLI result stamped with unknown extension 'no-such-extension'" in caplog.text
    assert sink.live == []


# Both turn kinds below run the REAL `bv` command with Browser Vision off (the
# default): the CLI bridge stamps its EXTENSION_DISABLED result, which is the
# disable path the header must hear about. No browser is started.


@pytest.mark.asyncio
async def test_a_decision_turn_bv_command_announces_the_live_view(sink, monkeypatch) -> None:
    from core.agent_loop.loop import run_turn

    agent = db.create_agent("Iris", role="Researcher", model_work="test/mock")
    _script(monkeypatch, [
        '{"act":"cli","data":{"cmd":"bv open example.com"},"th":"look"}',
        '{"act":"reply","work_commit":false,"intent":"question","msg":"It is off.","th":"answer"}',
    ])
    trigger = {
        "type": "human_chat", "content": "Open example.com", "from_name": "Human",
        "from_id": HUMAN_SENDER_ID, "source_channel": "chat", "author_type": "human",
    }
    outcome = await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert outcome.trigger_status == "completed"
    assert sink.live == [("browser-vision", agent.id)]


@pytest.mark.asyncio
async def test_an_execution_turn_bv_command_announces_the_live_view(sink, monkeypatch) -> None:
    from core.agent_loop import activity_runtime
    from core.agent_loop.loop import run_turn

    agent = db.create_agent("Iris", role="Researcher", model_work="test/mock")
    task = _task(agent.id, channel_id=None)
    activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    _script(monkeypatch, [
        '{"act":"cli","data":{"cmd":"bv open example.com"},"th":"look"}',
        '{"act":"idle","data":{},"th":"looked"}',
    ])
    trigger = {"type": "activity_resumed", "task_id": task.id, "content": "Resume.", "source_channel": "work"}
    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert sink.live == [("browser-vision", agent.id)]


@pytest.mark.asyncio
async def test_a_core_command_in_a_turn_announces_nothing(sink, monkeypatch) -> None:
    from core.agent_loop.loop import run_turn

    agent = db.create_agent("Iris", role="Researcher", model_work="test/mock")
    _script(monkeypatch, [
        '{"act":"cli","data":{"cmd":"pwd"},"th":"where"}',
        '{"act":"reply","work_commit":false,"intent":"question","msg":"Here.","th":"answer"}',
    ])
    trigger = {
        "type": "human_chat", "content": "Where are you?", "from_name": "Human",
        "from_id": HUMAN_SENDER_ID, "source_channel": "chat", "author_type": "human",
    }
    await run_turn(agent, db.get_agent_state(agent.id), trigger)
    assert sink.live == []
