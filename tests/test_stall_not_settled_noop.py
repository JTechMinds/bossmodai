"""Stall is not a settled no-op.

Landed project writes reset the no-progress streak. A real reply to
``Blocked — … @NextOwner`` hard-wakes the blocked agent. A settled essay
still ends as empty speak and stay-out.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.channel_rounds import advance_channel_round, start_channel_peer_round
from core.agent_loop.guardian import check_no_progress
from core.agent_loop.liveness import (
    command_mutates_project,
    next_actions_since_progress,
    outcome_resets_no_progress,
    record_action_liveness,
)
from core.agent_loop.soft_blocks import apply_no_progress_block
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from core.time import ensure_utc
from db import channel_response_rounds as channel_round_db


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


def _thread_task(*, assignee_id: str, channel_id: str, title: str = "Scaffold"):
    return create_or_bind_task(
        title=title,
        description="Write the project.",
        project=None,
        assigned_to=assignee_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel_id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


def _pair():
    charles = db.create_agent("Charles", role="Builder", desk_x=1, desk_y=1)
    brad = db.create_agent("Brad", role="Reviewer", desk_x=2, desk_y=1)
    ada = db.create_agent("Ada", role="QA", desk_x=3, desk_y=1)
    channel = db.create_channel(
        name="Charles, Brad",
        member_agent_ids=[charles.id, brad.id, ada.id],
        created_by=HUMAN_SENDER_ID,
    )
    return charles, brad, ada, channel


def _enable_system_ai() -> None:
    connection = db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )
    db.set_setting("system_ai_connection", connection.id, "llm")
    config.reload()


def _payload(speak: list[str], stay_out: list[str]) -> str:
    return json.dumps({"speak": speak, "stay_out": stay_out})


def _script(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> dict[str, Any]:
    calls: dict[str, Any] = {"n": 0, "prompts": []}

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        blob = "\n".join(item.get("content") or "" for item in messages)
        calls["prompts"].append(blob)
        if calls["n"] > len(replies):
            raise AssertionError("router was called more times than scripted")
        return replies[calls["n"] - 1]

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    return calls


def _statuses(round_id: str) -> dict[str, str]:
    return {
        candidate.agent_id: str(candidate.status or "")
        for candidate in db.list_channel_response_candidates(round_id)
    }


def _write(path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {"action": "bm_cli", "command": f"write {path}"},
        {"event": "bm_cli_result"},
    )


def test_project_writes_reset_the_no_progress_streak() -> None:
    """A scaffold of landed writes does not accumulate into Blocked — no progress."""
    agent = db.create_agent("Charles", role="Builder", desk_x=1, desk_y=1)
    agent.guardian_no_progress_threshold = 3
    streak = 0
    for index in range(5):
        action, result = _write(f"/projects/diablo-poc/file{index}.py")
        assert outcome_resets_no_progress(action, result) is True
        streak = next_actions_since_progress(streak, progressed=True)
        assert streak == 0
        assert check_no_progress(agent, streak) is None

    for _ in range(2):
        streak = next_actions_since_progress(
            streak,
            progressed=outcome_resets_no_progress(
                {"action": "bm_cli", "command": "ls /projects/diablo-poc"},
                {"event": "bm_cli_result"},
            ),
        )
    assert streak == 2
    assert check_no_progress(agent, streak) is None
    streak = next_actions_since_progress(streak, progressed=False)
    assert check_no_progress(agent, streak) is not None


def test_successful_write_moves_last_progress_and_a_read_does_not() -> None:
    charles, _brad, _ada, channel = _pair()
    creation = _thread_task(assignee_id=charles.id, channel_id=channel.id)
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    db.update_task(creation.task.id, last_progress_at=past, last_heartbeat_at=past)
    written_at = datetime.now(timezone.utc)
    action, result = _write("/projects/diablo-poc/README.md")
    record_action_liveness(creation.task.id, action, result, at=written_at)
    task = db.get_task(creation.task.id)
    assert task is not None
    assert ensure_utc(task.last_progress_at) == ensure_utc(written_at)

    later = written_at + timedelta(minutes=5)
    record_action_liveness(
        creation.task.id,
        {"action": "bm_cli", "command": "cat /projects/diablo-poc/README.md"},
        {"event": "bm_cli_result"},
        at=later,
    )
    task = db.get_task(creation.task.id)
    assert task is not None
    assert ensure_utc(task.last_progress_at) == ensure_utc(written_at)
    assert ensure_utc(task.last_heartbeat_at) == ensure_utc(later)

    record_action_liveness(
        creation.task.id,
        {"action": "bm_cli", "command": "write /projects/diablo-poc/miss.py"},
        {"event": "bm_cli_error"},
        at=later,
    )
    task = db.get_task(creation.task.id)
    assert task is not None
    assert ensure_utc(task.last_progress_at) == ensure_utc(written_at)


def test_mutating_cli_counts_and_reads_do_not() -> None:
    assert command_mutates_project("write /projects/diablo-poc/main.py") is True
    assert command_mutates_project("mkdir /projects/diablo-poc") is True
    assert command_mutates_project("bwrite", kind="batch-write") is True
    assert command_mutates_project("ls /projects/diablo-poc") is False
    assert command_mutates_project("cat /projects/diablo-poc/main.py") is False
    assert command_mutates_project("git status") is False
    assert command_mutates_project("python scaffold.py") is True
    assert command_mutates_project("git status", kind="shell", executor="shell") is False
    assert command_mutates_project("python scaffold.py", kind="shell", executor="shell") is True
    assert outcome_resets_no_progress(
        {"action": "bm_cli", "command": "write /projects/diablo-poc/main.py"},
        {"event": "bm_cli_result", "consent_required": True},
    ) is False


def _tagged_owner(detail: str, agents: list) -> Any:
    name = detail.split("@", 1)[1].strip()
    return next(agent for agent in agents if agent.name == name)


def test_blocked_next_owner_reply_hard_wakes_and_does_not_stay_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What's the block? reopens Charles. Empty speak must not stay him out."""
    charles, brad, ada, channel = _pair()
    creation = _thread_task(assignee_id=charles.id, channel_id=channel.id)
    activate_work_activity(charles.id, creation.task)
    result = apply_no_progress_block(
        charles,
        {"type": "channel_response", "channel_id": channel.id},
    )
    assert db.get_task(creation.task.id).status == "blocked"
    owner = _tagged_owner(result["detail"], [brad, ada])
    wakes = [
        item
        for item in result.get("trigger_requests") or []
        if item.get("agent_id") == owner.id
    ]
    assert wakes, "the blocked line still wakes the tagged next owner"
    payload = wakes[0]["payload"]
    round_id = payload["round_id"]
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=owner.id)

    _enable_system_ai()
    calls = _script(
        monkeypatch,
        [_payload([], [charles.id, owner.id])],
    )
    progress = advance_channel_round(
        payload,
        spoke=True,
        speaker_id=owner.id,
        spoken_text="What's the block?",
    )
    assert calls["n"] == 1
    assert "not a settled no-op" in calls["prompts"][0]
    assert progress["trigger_requests"]
    assert progress["trigger_requests"][0]["agent_id"] == charles.id
    follow_id = progress["trigger_requests"][0]["payload"]["round_id"]
    statuses = _statuses(follow_id)
    assert statuses[charles.id] == "queued"
    assert statuses.get(charles.id) != "observed"
    assert charles.id in set(channel_round_db.get_channel_round_meta(follow_id)["pinned_ids"])
    reopened = db.get_task(creation.task.id)
    assert reopened is not None
    assert reopened.status == "active"
    assert reopened.title == creation.task.title


def test_operator_at_stays_ahead_of_the_blocked_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charles, brad, ada, channel = _pair()
    creation = _thread_task(assignee_id=charles.id, channel_id=channel.id)
    activate_work_activity(charles.id, creation.task)
    blocked = apply_no_progress_block(
        charles,
        {"type": "channel_response", "channel_id": channel.id},
    )
    assert db.get_task(creation.task.id).status == "blocked"
    owner = _tagged_owner(blocked["detail"], [brad, ada])
    other = brad if owner.id == ada.id else ada
    _enable_system_ai()
    calls = _script(
        monkeypatch,
        [
            _payload([], [charles.id, brad.id, ada.id]),
            _payload([], [charles.id, brad.id, ada.id]),
        ],
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content=f"@{other.name} what's the block?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
    )
    assert "not a settled no-op" not in "\n".join(calls["prompts"])
    assert triggers
    assert triggers[0]["agent_id"] == other.id
    assert charles.id not in {item["agent_id"] for item in triggers}
    assert db.get_task(creation.task.id).status == "blocked"

    tagged = db.create_channel_message(
        channel_id=channel.id,
        author_type="system",
        author_name="BossMod",
        content="Charles Blocked — no progress. @Human Operator",
        source_channel="channel",
    )
    ask = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content=f"@{other.name} what's the block?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=ask.id,
        content=ask.content,
        from_name="Human Operator",
        author_type="human",
    )
    assert calls["n"] == 2
    assert triggers[0]["agent_id"] == other.id
    round_id = triggers[0]["payload"]["round_id"]
    pinned = channel_round_db.get_channel_round_meta(round_id)["pinned_ids"]
    assert pinned[0] == other.id
    assert charles.id in pinned
    assert _statuses(round_id)[charles.id] == "pending"
    assert _statuses(round_id)[charles.id] != "observed"
    assert db.get_task(creation.task.id).status == "active"
    assert tagged.id


def test_settled_essay_stays_out_and_does_not_reopen_the_blocked_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settled no-op is still empty speak and stay-out, not a hard wake."""
    charles, brad, ada, channel = _pair()
    creation = _thread_task(assignee_id=charles.id, channel_id=channel.id)
    activate_work_activity(charles.id, creation.task)
    blocked = apply_no_progress_block(
        charles,
        {"type": "channel_response", "channel_id": channel.id},
    )
    owner = _tagged_owner(blocked["detail"], [brad, ada])
    author = brad if owner.id == ada.id else ada
    _enable_system_ai()
    essay = (
        f"@{owner.name} the record is unchanged. My lane is the same as the last pass. "
        "No further action from me."
    )
    others = [agent.id for agent in (charles, brad, ada) if agent.id != author.id]
    calls = _script(
        monkeypatch,
        [
            _payload([], others),
            _payload([], others),
        ],
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=author.id,
        author_name=author.name,
        content=essay,
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=essay,
        from_name=author.name,
        author_type="agent",
        exclude_agent_ids={author.id},
        from_agent=author.id,
    )
    assert calls["n"] == 2
    assert "Who speaks next?" in calls["prompts"][-1]
    assert "Settled status, an echo of a line the thread already shows, or a no-op" in calls["prompts"][0]
    assert triggers == []
    rounds = db.list_channel_response_rounds(channel.id)
    essay_round = next(row for row in rounds if row.source_message_id == message.id)
    assert essay_round.status == "completed"
    assert _statuses(essay_round.id)[charles.id] == "observed"
    assert _statuses(essay_round.id)[owner.id] == "observed"
    assert db.get_task(creation.task.id).status == "blocked"


def test_ack_to_the_blocked_line_does_not_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charles, brad, _ada, channel = _pair()
    creation = _thread_task(assignee_id=charles.id, channel_id=channel.id)
    activate_work_activity(charles.id, creation.task)
    result = apply_no_progress_block(
        charles,
        {"type": "channel_response", "channel_id": channel.id},
    )
    owner = _tagged_owner(result["detail"], [brad, _ada])
    payload = next(item["payload"] for item in result["trigger_requests"] if item["agent_id"] == owner.id)
    db.mark_channel_candidate_responded(round_id=payload["round_id"], agent_id=owner.id)
    _enable_system_ai()
    calls = _script(
        monkeypatch,
        [
            _payload([], [owner.id]),
            _payload([], [owner.id]),
        ],
    )
    progress = advance_channel_round(
        payload,
        spoke=True,
        speaker_id=owner.id,
        spoken_text="got it",
    )
    assert progress["trigger_requests"] == []
    assert "not a settled no-op" not in "\n".join(calls["prompts"])
    assert db.get_task(creation.task.id).status == "blocked"
