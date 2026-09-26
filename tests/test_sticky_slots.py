"""Task-side sticky slots keyed by real source ids. Open conditions stay."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.loop import run_turn
from core.agent_loop.prompt_history import build_prompt_history_view
from core.agent_loop.standing_prefs import standing_prefs_file
from core.agent_loop.sticky_slots import (
    _FILL_SYSTEM,
    SLOT_ID_PREFIX,
    _clean_fact,
    note_sticky_slot_turn,
    set_sticky_slot_scheduler,
)
from core.bm_cli import filesystem
from core.llm import context_builder
from core.models.message import HUMAN_SENDER_ID
from core.models.work_contract import DeliverableSpec, WorkContract
from db.chat_fade import get_chat_fade_gate, upsert_channel_chat_fade
from db.crud import execute
from db.sticky_slots import SLOT_KINDS, get_sticky_slot, get_sticky_slot_gate, upsert_sticky_slots
from db.task_work_contracts import set_task_work_contract


ROOT = Path(__file__).resolve().parents[1]
PLAN = "Ship the patch."
BLOCKERS = "Budget still open."
VERDICT = "/projects/ops/launch.md"
SUMMARY = "Older turns settled the schedule and left the budget open."


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    set_sticky_slot_scheduler(None)


def teardown_function() -> None:
    set_sticky_slot_scheduler(None)
    db.close_connection()


def _tokens(text: str, model: str | None = None) -> int:
    return 100


def _patch_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.agent_loop.sticky_slots.count_tokens", _tokens)
    monkeypatch.setattr("core.agent_loop.prompt_history.count_tokens", _tokens)
    monkeypatch.setattr(
        "core.agent_loop.chat_fade.count_tokens",
        lambda text, model=None: 1,
    )


def _capture_scheduler() -> list:
    jobs: list = []

    def _capture(job) -> None:
        jobs.append(job)

    set_sticky_slot_scheduler(_capture)
    return jobs


def _agent_channel():
    agent = db.create_agent("Ada", role="QA", desk_x=1, desk_y=1)
    channel = db.create_channel(name="Ops", member_agent_ids=[agent.id], created_by=agent.id)
    db.update_agent_prompt_history_policy(
        agent.id,
        last_n_histories=30,
        max_allowed_history_tokens=1000,
    )
    return agent, channel


def _task(agent, *, title: str = "Launch"):
    task = db.create_task(title=title, assigned_to=agent.id, owner_id=agent.id)
    db.update_task(task.id, status="blocked", status_note=BLOCKERS)
    set_task_work_contract(
        task.id,
        WorkContract(deliverables=[DeliverableSpec(type="file", path=VERDICT, description="launch")]),
    )
    blocker = db.create_task_event(
        task_id=task.id,
        author_type="system",
        author_name="BossMod",
        event_type="blocker",
        content=BLOCKERS,
    )
    return db.get_task(task.id), blocker


def _events(task_id: str, count: int = 8) -> list[str]:
    ids = []
    for index in range(count):
        if index == 0:
            content = f"OLD-TURN-{index} {BLOCKERS}"
            event_type = "comment"
        elif index < 3:
            content = f"OLD-TURN-{index} launch note"
            event_type = "comment"
        else:
            content = f"TAIL-KEEP-{index} current note"
            event_type = "comment"
        event = db.create_task_event(
            task_id=task_id,
            author_type="human",
            author_name="Human Operator",
            event_type=event_type,
            content=content,
        )
        ids.append(event.id)
    return ids


def _say(channel_id: str, content: str, *, task_id: str | None = None) -> str:
    message = db.create_channel_message(
        channel_id=channel_id,
        author_type="human",
        author_name="Human Operator",
        content=content,
        source_channel="channel",
        task_id=task_id,
    )
    return message.id


def _task_history(agent, task_id: str) -> list[dict]:
    view = build_prompt_history_view(
        agent,
        {"type": "task_assigned", "task_id": task_id},
    )
    return view.conversation_history


def _channel_history(agent, channel_id: str) -> list[dict]:
    view = build_prompt_history_view(
        agent,
        {"type": "channel_message", "channel_id": channel_id},
    )
    return view.conversation_history


def _enable_system_ai() -> None:
    db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )


def _set_gate(*, turns: int, minutes_ago: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    execute(
        """
        INSERT INTO sticky_slot_gate (id, turns_since_run, last_run_at)
        VALUES (1, $1, $2)
        ON CONFLICT(id) DO UPDATE SET
            turns_since_run = excluded.turns_since_run,
            last_run_at = excluded.last_run_at
        """,
        [turns, when],
    )


def _knob(key: str, value: str) -> None:
    db.set_setting(key, value, "llm")


def _fill_json(task_id: str, blocker_id: str, *, blockers: str = BLOCKERS, plan: str = PLAN) -> str:
    return json.dumps(
        {
            "slots": [
                {"source_id": task_id, "slot_kind": "plan", "body": plan},
                {"source_id": blocker_id, "slot_kind": "blockers", "body": blockers},
                {"source_id": "invented-task", "slot_kind": "plan", "body": "Fake board card"},
                {"source_id": "/projects/ops/invented.md", "slot_kind": "verdict_path", "body": "Invented path"},
            ]
        }
    )


def test_slot_kinds_are_the_work_spine() -> None:
    assert SLOT_KINDS == ("plan", "next_owner", "verdict_path", "blockers")


def test_pressure_gate_is_task_side_and_uses_task_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    _knob("compaction_chat_budget_headroom_percent", "90")
    agent, channel = _agent_channel()
    task, _blocker = _task(agent)
    jobs = _capture_scheduler()
    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: _fill_json(task.id, "unused"),
    )
    _enable_system_ai()

    for index in range(4):
        db.create_task_event(
            task_id=task.id,
            author_type="human",
            author_name="Human Operator",
            event_type="comment",
            content=f"note {index}",
        )
    assert _task_history(agent, task.id)
    assert jobs == []

    for index in range(4):
        db.create_task_event(
            task_id=task.id,
            author_type="human",
            author_name="Human Operator",
            event_type="comment",
            content=f"note {index + 4}",
        )
    _knob("compaction_mode", "off")
    assert _task_history(agent, task.id)
    assert jobs == []

    _knob("compaction_mode", "pressure_only")
    _knob("compaction_task_budget_headroom_percent", "lots")
    assert _task_history(agent, task.id)
    assert jobs == []

    _knob("compaction_task_budget_headroom_percent", "0")
    assert _task_history(agent, task.id)
    assert jobs == []
    assert get_chat_fade_gate()["last_run_at"] is None

    for index in range(8):
        _say(channel.id, f"chat {index}")
    assert _channel_history(agent, channel.id)
    assert jobs == []

    _knob("compaction_chat_budget_headroom_percent", "0")
    _knob("compaction_task_budget_headroom_percent", "50")
    assert _channel_history(agent, channel.id)
    assert jobs == []
    assert _task_history(agent, task.id)
    assert len(jobs) == 1
    assert get_chat_fade_gate()["last_run_at"] is None
    assert get_sticky_slot_gate()["last_run_at"] is not None

    assert _task_history(agent, task.id)
    assert len(jobs) == 1

    _set_gate(turns=100, minutes_ago=0)
    assert _task_history(agent, task.id)
    assert len(jobs) == 1

    _set_gate(turns=1, minutes_ago=11)
    assert _task_history(agent, task.id)
    assert len(jobs) == 1

    _set_gate(turns=8, minutes_ago=11)
    assert _task_history(agent, task.id)
    assert len(jobs) == 2


def test_zeroed_knobs_still_do_not_run_every_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    _knob("compaction_min_turns_between_runs", "0")
    _knob("compaction_cooldown_minutes", "0")
    agent, _channel = _agent_channel()
    task, _blocker = _task(agent)
    _events(task.id, 8)
    jobs = _capture_scheduler()
    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: _fill_json(task.id, "unused"),
    )
    _enable_system_ai()

    assert _task_history(agent, task.id)
    assert len(jobs) == 1
    assert _task_history(agent, task.id)
    assert len(jobs) == 1
    note_sticky_slot_turn()
    assert _task_history(agent, task.id)
    assert len(jobs) == 2
    assert _task_history(agent, task.id)
    assert len(jobs) == 2


def test_fail_closed_keeps_existing_slots_and_open_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    agent, _channel = _agent_channel()
    task, blocker = _task(agent)
    _events(task.id, 8)
    jobs = _capture_scheduler()
    before_tasks = len(db.list_tasks())
    upsert_sticky_slots(
        [
            {"source_id": task.id, "slot_kind": "plan", "body": "Hold the old plan."},
            {"source_id": blocker.id, "slot_kind": "blockers", "body": BLOCKERS},
        ]
    )

    _knob("compaction_mode", "off")
    assert _task_history(agent, task.id)
    _knob("compaction_mode", "pressure_only")
    assert get_sticky_slot(task.id, "plan")["body"] == "Hold the old plan."

    assert _task_history(agent, task.id)
    assert jobs == []
    assert get_sticky_slot(blocker.id, "blockers")["body"] == BLOCKERS

    _enable_system_ai()
    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", lambda *args, **kwargs: None)
    assert _task_history(agent, task.id)
    jobs[0]()
    assert get_sticky_slot(task.id, "plan")["body"] == "Hold the old plan."
    assert get_sticky_slot(blocker.id, "blockers")["body"] == BLOCKERS

    def _boom(*args, **kwargs):
        raise RuntimeError("system AI down")

    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", _boom)
    _set_gate(turns=8, minutes_ago=11)
    assert _task_history(agent, task.id)
    jobs[-1]()
    assert get_sticky_slot(blocker.id, "blockers")["body"] == BLOCKERS

    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: "The plan is prose, not slots.",
    )
    _set_gate(turns=8, minutes_ago=11)
    assert _task_history(agent, task.id)
    jobs[-1]()
    assert get_sticky_slot(task.id, "plan")["body"] == "Hold the old plan."
    assert db.get_task("invented-task") is None

    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: json.dumps({"slots": [{"source_id": task.id, "slot_kind": "plan", "body": PLAN}], "notes": "x"}),
    )
    _set_gate(turns=8, minutes_ago=11)
    assert _task_history(agent, task.id)
    jobs[-1]()
    assert get_sticky_slot(task.id, "plan")["body"] == "Hold the old plan."

    def _write_fails(*args, **kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr("core.agent_loop.sticky_slots.upsert_sticky_slots", _write_fails)
    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: _fill_json(task.id, blocker.id, blockers="All clear now."),
    )
    _set_gate(turns=8, minutes_ago=11)
    assert _task_history(agent, task.id)
    jobs[-1]()
    assert get_sticky_slot(task.id, "plan")["body"] == "Hold the old plan."
    assert get_sticky_slot(blocker.id, "blockers")["body"] == BLOCKERS
    assert len(db.list_tasks()) == before_tasks
    assert db.get_task(task.id).status == "blocked"


def test_fill_keeps_open_blockers_and_rejects_invented_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    agent, _channel = _agent_channel()
    stranger = db.create_agent("Bea", role="QA", desk_x=2, desk_y=1)
    task, blocker = _task(agent)
    _events(task.id, 8)
    upsert_sticky_slots(
        [{"source_id": blocker.id, "slot_kind": "blockers", "body": BLOCKERS}]
    )
    jobs = _capture_scheduler()
    prompts: list[str] = []
    before_tasks = len(db.list_tasks())
    caps: list[int | None] = []

    def _complete(messages, max_tokens=None):
        prompts.append(messages[1]["content"])
        caps.append(max_tokens)
        payload = {
            "slots": [
                {"source_id": task.id, "slot_kind": "plan", "body": PLAN},
                {"source_id": agent.id, "slot_kind": "next_owner", "body": "Ada owns the launch."},
                {"source_id": VERDICT, "slot_kind": "verdict_path", "body": "Launch file is the verdict."},
                {"source_id": blocker.id, "slot_kind": "blockers", "body": "All clear now."},
                {"source_id": "invented-task", "slot_kind": "plan", "body": "Fake board card"},
                {"source_id": stranger.id, "slot_kind": "next_owner", "body": "Someone new"},
                {"source_id": "/projects/ops/invented.md", "slot_kind": "verdict_path", "body": "Invented path"},
            ]
        }
        return json.dumps(payload)

    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", _complete)
    _enable_system_ai()
    _task_history(agent, task.id)
    assert len(jobs) == 1
    jobs[0]()

    assert get_sticky_slot(task.id, "plan")["body"] == PLAN
    assert get_sticky_slot(agent.id, "next_owner")["body"] == "Ada owns the launch."
    assert get_sticky_slot(VERDICT, "verdict_path")["body"] == "Launch file is the verdict."
    assert get_sticky_slot(blocker.id, "blockers")["body"] == BLOCKERS
    assert get_sticky_slot("invented-task", "plan") is None
    assert get_sticky_slot(stranger.id, "next_owner") is None
    assert get_sticky_slot("/projects/ops/invented.md", "verdict_path") is None
    assert db.get_task("invented-task") is None
    assert len(db.list_tasks()) == before_tasks
    assert db.get_task(task.id).status == "blocked"
    # No per-call cap: the fill uses the system_ai_max_tokens setting.
    assert caps == [None]
    assert task.id in prompts[0]
    assert blocker.id in prompts[0]
    assert "/me/notes" not in prompts[0]
    assert "standing_prefs" not in prompts[0]
    assert "invented-task" not in prompts[0]


def test_clearing_fill_cannot_hide_an_open_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    agent, _channel = _agent_channel()
    task, blocker = _task(agent)
    _events(task.id, 8)
    jobs = _capture_scheduler()
    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: _fill_json(task.id, blocker.id, blockers="All clear now."),
    )
    _enable_system_ai()
    _task_history(agent, task.id)
    assert len(jobs) == 1
    jobs[0]()
    assert get_sticky_slot(task.id, "plan")["body"] == PLAN
    assert get_sticky_slot(blocker.id, "blockers") is None
    assert db.get_task(task.id).status == "blocked"

    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=250)
    narrow = _task_history(agent, task.id)
    assert f"blockers ({blocker.id}): {BLOCKERS}" in narrow[0]["content"]
    assert "All clear now." not in narrow[0]["content"]


def test_open_status_note_stays_when_history_drops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "0")
    agent, _channel = _agent_channel()
    task = db.create_task(title="Launch", assigned_to=agent.id, owner_id=agent.id)
    db.update_task(task.id, status="blocked", status_note=BLOCKERS)
    _events(task.id, 8)
    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=250)
    narrow = _task_history(agent, task.id)
    assert f"blockers ({task.id}): {BLOCKERS}" in narrow[0]["content"]
    assert db.get_task(task.id).status == "blocked"


def test_injects_when_task_history_drops_an_open_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "0")
    agent, _channel = _agent_channel()
    task, blocker = _task(agent)
    _events(task.id, 8)
    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=10_000)
    wide = _task_history(agent, task.id)
    assert all(not str(item["id"]).startswith(SLOT_ID_PREFIX) for item in wide)
    assert any(item["id"] == blocker.id for item in wide)

    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=250)
    narrow = _task_history(agent, task.id)
    assert str(narrow[0]["id"]).startswith(SLOT_ID_PREFIX)
    assert f"blockers ({blocker.id}): {BLOCKERS}" in narrow[0]["content"]
    rest = " ".join(item["content"] for item in narrow[1:])
    assert BLOCKERS not in rest
    assert all(f"TAIL-KEEP-{index}" in rest for index in (6, 7))

    upsert_sticky_slots(
        [{"source_id": blocker.id, "slot_kind": "blockers", "body": "Stored open condition."}]
    )
    stored = _task_history(agent, task.id)
    assert f"blockers ({blocker.id}): Stored open condition." in stored[0]["content"]
    assert f"blockers ({blocker.id}): {BLOCKERS}" not in stored[0]["content"]


def test_channel_fade_injects_task_slots_without_wiping_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "0")
    agent, channel = _agent_channel()
    task, blocker = _task(agent)
    ids = []
    for index in range(8):
        if index < 3:
            ids.append(_say(channel.id, f"OLD-TURN-{index} launch note", task_id=task.id))
        else:
            ids.append(_say(channel.id, f"TAIL-KEEP-{index} current note"))
    before = [(item.id, item.content) for item in db.list_channel_messages(channel.id)]
    upsert_channel_chat_fade(
        channel_id=channel.id,
        through_message_id=ids[2],
        summary=SUMMARY,
    )
    db.update_agent_state(agent.id, status="blocked")

    faded = _channel_history(agent, channel.id)
    contents = [item["content"] for item in faded]
    assert f"blockers ({blocker.id}): {BLOCKERS}" in contents[0]
    assert contents[1] == SUMMARY
    assert all("OLD-TURN" not in content for content in contents)
    assert all(f"TAIL-KEEP-{index}" in " ".join(contents) for index in range(3, 8))
    assert [(item.id, item.content) for item in db.list_channel_messages(channel.id)] == before
    assert db.get_task(task.id).status == "blocked"
    assert db.get_agent_state(agent.id).status == "blocked"

    plain = []
    for index in range(8):
        plain.append(_say(channel.id, f"PLAIN-{index} no task"))
    upsert_channel_chat_fade(
        channel_id=channel.id,
        through_message_id=plain[2],
        summary=SUMMARY,
    )
    # The earlier task-linked turns are gone from this fetch only if the fade
    # anchor moved forward. Rebuild against a channel that never had a task.
    other = db.create_channel(name="Side", member_agent_ids=[agent.id], created_by=agent.id)
    side_ids = [_say(other.id, f"OLD-TURN-{index} side") for index in range(8)]
    upsert_channel_chat_fade(
        channel_id=other.id,
        through_message_id=side_ids[2],
        summary=SUMMARY,
    )
    side = _channel_history(agent, other.id)
    assert side[0]["content"] == SUMMARY
    assert all(not str(item["id"]).startswith(SLOT_ID_PREFIX) for item in side)


def test_fill_does_not_block_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    agent, _channel = _agent_channel()
    task, blocker = _task(agent)
    _events(task.id, 8)
    _enable_system_ai()
    set_sticky_slot_scheduler(None)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    caller = threading.get_ident()
    seen: dict[str, int] = {}

    def _complete(messages, max_tokens=None):
        seen["thread"] = threading.get_ident()
        entered.set()
        release.wait(timeout=5)
        finished.set()
        return _fill_json(task.id, blocker.id)

    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", _complete)
    started = time.monotonic()
    try:
        immediate = _task_history(agent, task.id)
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert any(item["id"] == blocker.id for item in immediate)
        assert get_sticky_slot(task.id, "plan") is None
        assert entered.wait(2)
        assert not finished.is_set()
        assert seen["thread"] != caller
    finally:
        release.set()
    assert finished.wait(2)
    deadline = time.monotonic() + 2
    while get_sticky_slot(task.id, "plan") is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert get_sticky_slot(task.id, "plan")["body"] == PLAN
    assert get_sticky_slot("invented-task", "plan") is None


def test_slots_stay_off_prefs_notes_human_chat_and_soft_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "0")
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
    # The prefs store is system-owned, outside the agents root; keep it per-test too.
    monkeypatch.setattr(filesystem, "_SYSTEM_ROOT", tmp_path / "system")
    agent, _channel = _agent_channel()
    task, blocker = _task(agent)
    _events(task.id, 8)
    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=250)
    db.update_agent_state(agent.id, status="blocked")
    path = standing_prefs_file(agent.storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    pref_text = "keep launch lines short"
    document = json.dumps(
        {
            "schema_version": 1,
            "prefs": [
                {
                    "id": "tone",
                    "kind": "preference",
                    "text": pref_text,
                    "sources": ["operator"],
                }
            ],
        }
    )
    path.write_text(document, encoding="utf-8")
    notes = tmp_path / "agents" / agent.storage_key / "notes"

    history = _task_history(agent, task.id)
    state = db.get_agent_state(agent.id)
    assert state is not None
    context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger={"type": "task_assigned", "task_id": task.id},
            conversation_history=history,
            prompt_notifications=[],
            reference_materials=[],
            contract_kind="execution",
        )
    )
    contents = [str(message.get("content") or "") for message in context]
    spine = [content for content in contents if f"blockers ({blocker.id})" in content]
    prefs = [content for content in contents if pref_text in content]
    assert len(spine) == 1
    assert len(prefs) == 1
    assert spine[0] != prefs[0]
    assert spine[0].startswith("[Work spine]:")
    assert "standing_prefs" not in spine[0]
    assert "/me/notes" not in spine[0]
    assert "# Standing prefs" in prefs[0]
    assert path.read_text(encoding="utf-8") == document
    assert not notes.exists()
    assert db.get_agent_state(agent.id).status == "blocked"
    assert db.get_task(task.id).status == "blocked"

    for index in range(4):
        db.create_message(
            HUMAN_SENDER_ID,
            agent.id,
            f"direct note {index}",
            message_type="human",
        )
    human = build_prompt_history_view(agent, {"type": "human_chat"}).conversation_history
    assert all(not str(item["id"]).startswith(SLOT_ID_PREFIX) for item in human)
    assert all(blocker.id not in item["content"] for item in human)

    source = (ROOT / "core" / "agent_loop" / "sticky_slots.py").read_text(encoding="utf-8")
    assert "read_standing_prefs" not in source
    assert "standing_prefs.json" not in source
    assert "soft_blocks" not in source
    assert "/me/notes" not in source
    prompt_history = (ROOT / "core" / "agent_loop" / "prompt_history.py").read_text(encoding="utf-8")
    assert "compose_task_sticky_slots" in prompt_history
    assert "compose_channel_sticky_slots" in prompt_history
    assert "apply_channel_chat_fade" in prompt_history
    assert "consider_channel_chat_fade" in prompt_history
    fade = (ROOT / "core" / "agent_loop" / "chat_fade.py").read_text(encoding="utf-8")
    assert "sticky_slot" not in fade


@pytest.mark.asyncio
async def test_skipped_turn_counts_toward_the_gap_and_returns() -> None:
    agent = db.create_agent("Ada", role="QA", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    outcome = await run_turn(agent, state, {"type": "human_chat"})
    assert outcome.trigger_status == "skipped"
    assert get_sticky_slot_gate()["turns_since_run"] == 1


def test_fill_prompt_states_the_body_length_target() -> None:
    assert "under 180 characters" in _FILL_SYSTEM


def test_body_overrun_is_kept_until_the_backstop(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="core.agent_loop.sticky_slots")
    within = "a" * 300
    assert _clean_fact(within) == within
    assert not [r for r in caplog.records if "clipped" in r.getMessage()]

    clipped = _clean_fact("b" * 400)
    assert clipped == "b" * 360
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert [r.getMessage() for r in warnings] == ["sticky slot body clipped: 400 > 360 chars"]
