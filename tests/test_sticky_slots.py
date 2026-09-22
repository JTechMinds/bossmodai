"""Pressure-gated sticky slots. Fails closed to the warm window and chat fade."""

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
    SLOT_ID_PREFIX,
    StickySlotFill,
    note_sticky_slot_turn,
    set_sticky_slot_scheduler,
)
from core.bm_cli import filesystem
from core.llm import context_builder
from core.models.message import HUMAN_SENDER_ID
from db.chat_fade import get_chat_fade_gate, upsert_channel_chat_fade
from db.crud import execute
from db.sticky_slots import FACT_KEYS, get_sticky_slot, get_sticky_slot_gate, merge_sticky_slot


ROOT = Path(__file__).resolve().parents[1]
PLAN = "Ship the patch."
NEXT_OWNER = "Ada"
VERDICT = "Ops thread"
BLOCKERS = "Budget still open."
OLD_PLAN = "Hold the old plan."
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
    # Keep chat fade from claiming its own gate while these tests measure slots.
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


def _say(channel_id: str, content: str) -> str:
    message = db.create_channel_message(
        channel_id=channel_id,
        author_type="human",
        author_name="Human Operator",
        content=content,
        source_channel="channel",
    )
    return message.id


def _fill(channel_id: str, count: int = 8) -> list[str]:
    contents = []
    for index in range(count):
        if index < 3:
            contents.append(f"OLD-TURN-{index} launch note")
        else:
            contents.append(f"TAIL-KEEP-{index} current note")
    return [_say(channel_id, content) for content in contents]


def _history(agent, channel_id: str) -> list[dict]:
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


def _pocket(channel_id: str, source_ids: list[str], **facts: str) -> None:
    merge_sticky_slot(
        scope_kind="channel",
        scope_id=channel_id,
        facts=facts,
        source_message_ids=source_ids,
    )


def _fill_json(**overrides: object) -> str:
    payload = {
        "plan": PLAN,
        "next_owner": NEXT_OWNER,
        "verdict_path": VERDICT,
        "blockers": BLOCKERS,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_slot_model_is_the_four_work_spine_facts() -> None:
    assert tuple(StickySlotFill.model_fields) == FACT_KEYS


def test_pressure_gate_uses_task_headroom_not_chat_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    _knob("compaction_chat_budget_headroom_percent", "0")
    agent, channel = _agent_channel()
    jobs = _capture_scheduler()
    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", lambda *args, **kwargs: _fill_json())
    _enable_system_ai()

    for index in range(4):
        _say(channel.id, f"note {index}")
    assert _history(agent, channel.id)
    assert jobs == []

    for index in range(4):
        _say(channel.id, f"note {index + 4}")
    _knob("compaction_mode", "off")
    assert _history(agent, channel.id)
    assert jobs == []

    _knob("compaction_mode", "pressure_only")
    _knob("compaction_task_budget_headroom_percent", "lots")
    assert _history(agent, channel.id)
    assert jobs == []

    _knob("compaction_chat_budget_headroom_percent", "90")
    _knob("compaction_task_budget_headroom_percent", "0")
    assert _history(agent, channel.id)
    assert jobs == []
    assert get_chat_fade_gate()["last_run_at"] is None

    _knob("compaction_chat_budget_headroom_percent", "0")
    _knob("compaction_task_budget_headroom_percent", "50")
    assert _history(agent, channel.id)
    assert len(jobs) == 1
    assert get_chat_fade_gate()["last_run_at"] is None
    assert get_sticky_slot_gate()["last_run_at"] is not None

    assert _history(agent, channel.id)
    assert len(jobs) == 1

    _set_gate(turns=100, minutes_ago=0)
    assert _history(agent, channel.id)
    assert len(jobs) == 1

    _set_gate(turns=1, minutes_ago=11)
    assert _history(agent, channel.id)
    assert len(jobs) == 1

    _set_gate(turns=8, minutes_ago=11)
    assert _history(agent, channel.id)
    assert len(jobs) == 2


def test_zeroed_knobs_still_do_not_run_every_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    _knob("compaction_min_turns_between_runs", "0")
    _knob("compaction_cooldown_minutes", "0")
    agent, channel = _agent_channel()
    jobs = _capture_scheduler()
    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", lambda *args, **kwargs: _fill_json())
    _enable_system_ai()
    _fill(channel.id, 8)

    assert _history(agent, channel.id)
    assert len(jobs) == 1
    assert _history(agent, channel.id)
    assert len(jobs) == 1
    note_sticky_slot_turn()
    assert _history(agent, channel.id)
    assert len(jobs) == 2
    assert _history(agent, channel.id)
    assert len(jobs) == 2


def test_fail_closed_keeps_the_warm_window_and_chat_fade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    agent, channel = _agent_channel()
    ids = _fill(channel.id, 8)
    jobs = _capture_scheduler()
    before = [(item.id, item.content) for item in db.list_channel_messages(channel.id)]

    _knob("compaction_mode", "off")
    hard = [item["content"] for item in _history(agent, channel.id)]
    _knob("compaction_mode", "pressure_only")

    unavailable = [item["content"] for item in _history(agent, channel.id)]
    assert unavailable == hard
    assert jobs == []
    assert get_sticky_slot("channel", channel.id) is None

    _enable_system_ai()
    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", lambda *args, **kwargs: None)
    assert _history(agent, channel.id)
    assert len(jobs) == 1
    jobs[0]()
    assert get_sticky_slot("channel", channel.id) is None
    assert [item["content"] for item in _history(agent, channel.id)] == hard

    def _boom(*args, **kwargs):
        raise RuntimeError("system AI down")

    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", _boom)
    _set_gate(turns=8, minutes_ago=11)
    assert _history(agent, channel.id)
    jobs[-1]()
    assert get_sticky_slot("channel", channel.id) is None
    assert [item["content"] for item in _history(agent, channel.id)] == hard

    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: "The plan is to keep going in prose.",
    )
    _set_gate(turns=8, minutes_ago=11)
    assert _history(agent, channel.id)
    jobs[-1]()
    assert get_sticky_slot("channel", channel.id) is None

    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: json.dumps({"plan": PLAN, "notes": "dump the notebook"}),
    )
    _set_gate(turns=8, minutes_ago=11)
    assert _history(agent, channel.id)
    jobs[-1]()
    assert get_sticky_slot("channel", channel.id) is None

    _pocket(channel.id, [ids[0]], plan=OLD_PLAN, blockers=BLOCKERS)
    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: "not-json",
    )
    _set_gate(turns=8, minutes_ago=11)
    assert _history(agent, channel.id)
    jobs[-1]()
    kept = get_sticky_slot("channel", channel.id)
    assert kept is not None
    assert kept["plan"] == OLD_PLAN
    assert kept["blockers"] == BLOCKERS
    assert kept["next_owner"] is None

    def _write_fails(**kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr("core.agent_loop.sticky_slots.merge_sticky_slot", _write_fails)
    monkeypatch.setattr(
        "core.agent_loop.sticky_slots.complete_text",
        lambda *args, **kwargs: _fill_json(),
    )
    _set_gate(turns=8, minutes_ago=11)
    assert _history(agent, channel.id)
    jobs[-1]()
    kept = get_sticky_slot("channel", channel.id)
    assert kept is not None
    assert kept["plan"] == OLD_PLAN
    assert kept["blockers"] == BLOCKERS

    def _read_fails(*args, **kwargs):
        raise RuntimeError("read failed")

    monkeypatch.setattr("core.agent_loop.sticky_slots.get_sticky_slot", _read_fails)
    upsert_channel_chat_fade(
        channel_id=channel.id,
        through_message_id=ids[2],
        summary=SUMMARY,
    )
    shown = [item["content"] for item in _history(agent, channel.id)]
    assert shown[0] == SUMMARY
    assert PLAN not in " ".join(shown)
    assert OLD_PLAN not in " ".join(shown)
    assert [(item.id, item.content) for item in db.list_channel_messages(channel.id)] == before

    monkeypatch.setattr("core.agent_loop.sticky_slots.get_sticky_slot", get_sticky_slot)
    _knob("compaction_mode", "off")
    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=250)
    quiet = [item["content"] for item in _history(agent, channel.id)]
    assert all(not str(item).startswith("plan:") for item in quiet)
    assert OLD_PLAN not in " ".join(quiet)
    assert get_sticky_slot("channel", channel.id)["plan"] == OLD_PLAN


def test_injects_when_the_warm_window_drops_covered_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "0")
    agent, channel = _agent_channel()
    ids = _fill(channel.id, 8)
    _pocket(
        channel.id,
        ids[:3],
        plan=PLAN,
        next_owner=NEXT_OWNER,
        verdict_path=VERDICT,
        blockers=BLOCKERS,
    )
    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=10_000)
    wide = _history(agent, channel.id)
    assert all(not str(item["id"]).startswith(SLOT_ID_PREFIX) for item in wide)
    assert any("OLD-TURN-0" in item["content"] for item in wide)

    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=250)
    narrow = _history(agent, channel.id)
    assert str(narrow[0]["id"]).startswith(SLOT_ID_PREFIX)
    assert narrow[0]["from_name"] == "Work spine"
    spine = narrow[0]["content"]
    assert f"plan: {PLAN}" in spine
    assert f"next owner: {NEXT_OWNER}" in spine
    assert f"verdict path: {VERDICT}" in spine
    assert f"blockers: {BLOCKERS}" in spine
    rest = " ".join(item["content"] for item in narrow[1:])
    assert "OLD-TURN" not in rest
    assert all(f"TAIL-KEEP-{index}" in rest for index in (6, 7))

    other = db.create_channel(name="Side", member_agent_ids=[agent.id], created_by=agent.id)
    _fill(other.id, 8)
    merge_sticky_slot(
        scope_kind="channel",
        scope_id=other.id,
        facts={"plan": "Unanchored plan text."},
        source_message_ids=["missing-turn"],
    )
    side = _history(agent, other.id)
    assert all("Unanchored plan text." not in item["content"] for item in side)
    assert any("TAIL-KEEP-6" in item["content"] for item in side)
    assert any("TAIL-KEEP-7" in item["content"] for item in side)


def test_injects_spine_beside_chat_fade_without_wiping_the_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "0")
    agent, channel = _agent_channel()
    ids = _fill(channel.id, 8)
    before = [(item.id, item.content) for item in db.list_channel_messages(channel.id)]
    upsert_channel_chat_fade(
        channel_id=channel.id,
        through_message_id=ids[2],
        summary=SUMMARY,
    )
    _pocket(
        channel.id,
        ids[:3],
        plan=PLAN,
        next_owner=NEXT_OWNER,
        verdict_path=VERDICT,
        blockers=BLOCKERS,
    )

    faded = _history(agent, channel.id)
    contents = [item["content"] for item in faded]
    assert contents[0].startswith("plan:")
    assert contents[1] == SUMMARY
    assert all("OLD-TURN" not in content for content in contents)
    assert all(f"TAIL-KEEP-{index}" in " ".join(contents) for index in range(3, 8))
    assert [(item.id, item.content) for item in db.list_channel_messages(channel.id)] == before


def test_fill_does_not_block_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    agent, channel = _agent_channel()
    _fill(channel.id, 8)
    _enable_system_ai()
    set_sticky_slot_scheduler(None)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    caller = threading.get_ident()
    seen: dict[str, int] = {}

    def _complete(messages, max_tokens=220):
        seen["thread"] = threading.get_ident()
        entered.set()
        release.wait(timeout=5)
        finished.set()
        return _fill_json()

    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", _complete)
    started = time.monotonic()
    try:
        immediate = _history(agent, channel.id)
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert any("OLD-TURN-0" in item["content"] for item in immediate)
        assert all(not str(item["id"]).startswith(SLOT_ID_PREFIX) for item in immediate)
        assert get_sticky_slot("channel", channel.id) is None
        assert entered.wait(2)
        assert not finished.is_set()
        assert seen["thread"] != caller
    finally:
        release.set()
    assert finished.wait(2)
    deadline = time.monotonic() + 2
    while get_sticky_slot("channel", channel.id) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    stored = get_sticky_slot("channel", channel.id)
    assert stored is not None
    assert stored["plan"] == PLAN
    still_visible = _history(agent, channel.id)
    assert any("OLD-TURN-0" in item["content"] for item in still_visible)
    assert all(not str(item["id"]).startswith(SLOT_ID_PREFIX) for item in still_visible)


def test_fill_replaces_only_returned_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "50")
    agent, channel = _agent_channel()
    ids = _fill(channel.id, 8)
    before = [(item.id, item.content) for item in db.list_channel_messages(channel.id)]
    db.update_agent_state(agent.id, status="blocked")
    _pocket(channel.id, [ids[0]], plan=OLD_PLAN, blockers=BLOCKERS)
    jobs = _capture_scheduler()
    prompts: list[str] = []

    def _complete(messages, max_tokens=220):
        prompts.append(messages[1]["content"])
        return _fill_json(verdict_path=None, blockers=None)

    monkeypatch.setattr("core.agent_loop.sticky_slots.complete_text", _complete)
    _enable_system_ai()
    _history(agent, channel.id)
    assert len(jobs) == 1
    jobs[0]()

    stored = get_sticky_slot("channel", channel.id)
    assert stored is not None
    assert stored["plan"] == PLAN
    assert stored["next_owner"] == NEXT_OWNER
    assert stored["verdict_path"] is None
    assert stored["blockers"] == BLOCKERS
    assert "OLD-TURN-0" in prompts[0]
    assert "TAIL-KEEP-7" not in prompts[0]
    assert "/me/notes" not in prompts[0]
    assert "standing_prefs" not in prompts[0]
    assert [(item.id, item.content) for item in db.list_channel_messages(channel.id)] == before
    state = db.get_agent_state(agent.id)
    assert state is not None
    assert state.status == "blocked"


def test_slots_stay_distinct_from_standing_prefs_notes_and_soft_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_tokens(monkeypatch)
    _knob("compaction_task_budget_headroom_percent", "0")
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
    agent, channel = _agent_channel()
    ids = _fill(channel.id, 8)
    db.update_agent_prompt_history_policy(agent.id, max_allowed_history_tokens=250)
    db.update_agent_state(agent.id, status="blocked")
    _pocket(
        channel.id,
        ids[:3],
        plan=PLAN,
        next_owner=NEXT_OWNER,
        verdict_path=VERDICT,
        blockers=BLOCKERS,
    )
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
    notes = path.parent / "notes"
    assert not notes.exists()

    history = _history(agent, channel.id)
    state = db.get_agent_state(agent.id)
    assert state is not None
    context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger={"type": "channel_message", "channel_id": channel.id},
            conversation_history=history,
            prompt_notifications=[],
            reference_materials=[],
            contract_kind="decision",
        )
    )
    contents = [str(message.get("content") or "") for message in context]
    spine = [content for content in contents if PLAN in content]
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
    assert list(path.parent.iterdir()) == [path]
    assert db.get_agent_state(agent.id).status == "blocked"

    for index in range(8):
        db.create_message(
            HUMAN_SENDER_ID,
            agent.id,
            f"OLD-TURN-{index} direct note" if index < 3 else f"TAIL-KEEP-{index} direct note",
            message_type="human",
        )
    human_ids = [item.id for item in db.get_human_chat_thread(agent.id)]
    merge_sticky_slot(
        scope_kind="human",
        scope_id=agent.id,
        facts={"plan": "Direct plan stays on the human thread."},
        source_message_ids=human_ids[:3],
    )
    human = build_prompt_history_view(agent, {"type": "human_chat"}).conversation_history
    assert human[0]["content"].startswith("plan: Direct plan stays")
    channel_text = " ".join(item["content"] for item in _history(agent, channel.id))
    assert "Direct plan stays" not in channel_text
    assert PLAN in channel_text

    source = (ROOT / "core" / "agent_loop" / "sticky_slots.py").read_text(encoding="utf-8")
    assert "read_standing_prefs" not in source
    assert "standing_prefs.json" not in source
    assert "soft_blocks" not in source
    assert "/me/notes" not in source
    prompt_history = (ROOT / "core" / "agent_loop" / "prompt_history.py").read_text(encoding="utf-8")
    assert "compose_sticky_slots" in prompt_history
    assert "apply_channel_chat_fade" in prompt_history
    assert "consider_channel_chat_fade" in prompt_history


@pytest.mark.asyncio
async def test_skipped_turn_counts_toward_the_gap_and_returns() -> None:
    agent = db.create_agent("Ada", role="QA", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    outcome = await run_turn(agent, state, {"type": "human_chat"})
    assert outcome.trigger_status == "skipped"
    assert get_sticky_slot_gate()["turns_since_run"] == 1
