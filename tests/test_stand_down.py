"""The one clean stop for agents leaving service (core/stand_down.py), and the non-sticky system deny."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import pytest

import db
from core import config
from core.bm_cli import background_processes
from core.bm_cli.shell_executor_consent import _denied_for_scope
from core.bm_cli.workspace_preference import _resolved_preference_for_write
from core.channel_archive import archive_thread_as_operator
from core.models.host_path_consent import SHELL_EXECUTOR_GRANT_ROOT, SHELL_EXECUTOR_KIND, WORKSPACE_PREFERENCE_KIND
from core.models.message import HUMAN_SENDER_ID
from core.stand_down import StandDownWhy, open_task_ids_on_floor, stand_down
from core.tasking.service import create_or_bind_task
from db.floors import create_floor, set_floor_suspended
from tests._stand_down import StandDownServices

WHY = StandDownWhy(label="Test stop", phrase="the test stopped them")


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


def _agent(name: str, x: int, *, floor_id: str | None = None):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1, floor_id=floor_id)


def _suspended_floor_with(*names: str):
    """A floor with these residents, then suspended: every resident's gate is closed."""
    floor = create_floor("Finance")
    agents = [_agent(name, index + 1, floor_id=floor.id) for index, name in enumerate(names)]
    set_floor_suspended(floor.id, datetime.now(timezone.utc))
    return floor, agents


def _on_vacation(agent_id: str) -> None:
    """Close one agent's gate the way send_home does, without its stand-down."""
    db.update_agent(agent_id, floor_id=None, vacation_since=datetime.now(timezone.utc))


def _task(title: str, *, assigned_to: str | None, owner_id: str | None = None, requester_id: str = HUMAN_SENDER_ID):
    """A task the operator asked for in the agent's DM, so cancelling it posts an origin line there."""
    return create_or_bind_task(
        title=title,
        description="Work",
        project=None,
        assigned_to=assigned_to,
        requester_id=requester_id,
        owner_id=owner_id,
        created_by=requester_id,
        parent_task_id=None,
        work_contract=None,
        source_channel="chat",
        notification_policy="completion_blocked",
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task


def _running_process(agent):
    """A running process row whose run tag matches nothing, so stopping it kills nothing."""
    process_id = f"{os.urandom(4).hex()}-feed-0000-0000-000000000000"
    now = datetime.now(timezone.utc)
    return db.create_agent_process(
        process_id=process_id, agent_id=agent.id, command="npx vite", cwd="/me",
        run_tag=os.urandom(8).hex(), pid=os.getpid(), pid_create_time=psutil.Process().create_time(),
        log_path=str(background_processes.process_log_path(agent.storage_key, process_id)),
        started_at=now - timedelta(minutes=5), checkin_due_at=now + timedelta(hours=2),
    )


def _hosted_meeting(host_id: str) -> str:
    """One assembling meeting ``host_id`` hosts; returns its session id."""
    [session] = db.query(
        "INSERT INTO meeting_sessions (room_id, title, status) VALUES ($1, $2, 'active') RETURNING id",
        ["meeting_room", "Sync"],
    )
    db.execute(
        "INSERT INTO meeting_session_meta (session_id, host_agent_id, meeting_mode, phase) "
        "VALUES ($1, $2, 'room', 'assembling')",
        [session["id"], host_id],
    )
    return str(session["id"])


# ─── The precondition ───


async def test_stand_down_refuses_an_agent_whose_gate_is_open() -> None:
    ada = _agent("Ada", 1)
    _floor, [bob] = _suspended_floor_with("Bob")
    services = StandDownServices()

    with pytest.raises(ValueError, match=f"Ada \\({ada.id}\\)"):
        await stand_down([bob.id, ada.id], why=WHY, extra_task_ids=[], services=services)

    assert services.resets == [] and services.released == [], "nothing was stopped"


async def test_stand_down_names_an_unknown_agent() -> None:
    with pytest.raises(LookupError, match="nobody"):
        await stand_down(["nobody"], why=WHY, extra_task_ids=[], services=StandDownServices())


# ─── Each step ───


async def test_the_live_turn_is_cancelled_its_claim_discarded_and_queued_triggers_gone() -> None:
    _floor, [ada] = _suspended_floor_with("Ada")
    live = db.create_agent_trigger(
        agent_id=ada.id, trigger_type="human_chat", source_channel="chat", payload={"content": "hi"},
    )
    assert db.claim_trigger(live.id) is not None
    db.create_agent_trigger(
        agent_id=ada.id, trigger_type="task_follow_up", source_channel="chat", payload={"content": "later"},
    )
    services = StandDownServices()

    result = await stand_down([ada.id], why=WHY, extra_task_ids=[], services=services)

    assert result.agent_ids == [ada.id]
    assert services.resets == [ada.id]
    assert db.get_agent_trigger(live.id) is None
    assert db.list_agent_triggers(ada.id, status="claimed") == []
    assert db.list_agent_triggers(ada.id, status="queued") == []


async def test_assigned_open_tasks_and_extras_are_cancelled_and_owned_work_elsewhere_is_left() -> None:
    ada, bob = _agent("Ada", 1), _agent("Bob", 2)
    mine = _task("Ada's report", assigned_to=ada.id)
    colleagues = _task("Bob's part", assigned_to=bob.id, owner_id=ada.id)
    backlog = _task("Filed by Bob", assigned_to=None, requester_id=bob.id)
    closed = _task("Already closed", assigned_to=ada.id)
    db.update_task(closed.id, status="abandoned")
    _on_vacation(ada.id)

    result = await stand_down([ada.id], why=WHY, extra_task_ids=[backlog.id, closed.id], services=StandDownServices())

    assert result.tasks_cancelled == [mine.id, backlog.id], "oldest first; a closed extra is already settled"
    for task_id in (mine.id, backlog.id):
        task = db.get_task(task_id)
        assert task.status == "cancelled" and task.status_note == "Cancelled: the test stopped them"
    assert db.get_task(colleagues.id).status == "pending", "the assignee can still finish it"
    assert db.get_task(closed.id).status == "abandoned"
    [line] = [posted["chat_message"] for posted in result.posted_lines if "chat_message" in posted]
    assert line["agent_id"] == ada.id and "Cancelled: the test stopped them" in line["content"]


async def test_hosted_meetings_end_with_the_phrase() -> None:
    _floor, [ada] = _suspended_floor_with("Ada")
    session_id = _hosted_meeting(ada.id)

    result = await stand_down([ada.id], why=WHY, extra_task_ids=[], services=StandDownServices())

    assert result.meetings_ended == 1
    assert db.get_meeting_session_meta(session_id)["phase"] == "canceled"
    lines = [item.content for item in db.list_meeting_session_messages(session_id, limit=10)]
    assert lines[-1] == "Meeting ended: the test stopped them."
    [painted] = [posted for posted in result.posted_lines if "meeting_message" in posted]
    assert painted["meeting_message"]["session_id"] == session_id and painted["agent_ids"] == []


async def test_pending_approvals_are_rejected_and_consents_denied_by_system() -> None:
    _floor, [ada] = _suspended_floor_with("Ada")
    approval = db.create_cli_approval_request(agent_id=ada.id, command="rm -rf build")
    host_path = db.create_consent_request(agent_id=ada.id, path="/srv/data", grant_root="/srv/data", reason="Read")
    shell = db.create_consent_request(
        agent_id=ada.id, path=SHELL_EXECUTOR_GRANT_ROOT, grant_root=SHELL_EXECUTOR_GRANT_ROOT,
        reason="Validate", card_kind=SHELL_EXECUTOR_KIND,
    )

    result = await stand_down([ada.id], why=WHY, extra_task_ids=[], services=StandDownServices())

    assert (result.approvals_closed, result.consents_closed) == (1, 2)
    closed = db.get_cli_approval_request(approval.id)
    assert (closed.status, closed.decision_by, closed.decision_note) == (
        "rejected", "system", "Closed: the test stopped them",
    )
    for request in (host_path, shell):
        denied = db.get_consent_request(request.id)
        assert (denied.status, denied.decision_by, denied.decision_note) == (
            "denied", "system", "Closed: the test stopped them",
        )
    assert db.agent_has_pending_operator_gate(ada.id) is False


async def test_background_processes_stop_with_the_label() -> None:
    _floor, [ada, bob] = _suspended_floor_with("Ada", "Bob")
    row = _running_process(ada)

    result = await stand_down([ada.id, bob.id], why=WHY, extra_task_ids=[], services=StandDownServices())

    assert result.process_stopped_agent_ids == [ada.id], "only the agent that had one"
    stopped = db.get_agent_process(row.id)
    assert (stopped.status, stopped.stopped_by, stopped.stop_reason) == ("stopped", "system", "Test stop")


async def test_activities_and_snapshots_go_and_only_an_office_agent_is_seated() -> None:
    _floor, [ada] = _suspended_floor_with("Ada")
    away = _agent("Away", 5)
    walk = db.create_runtime_activity(ada.id, "movement", title="Walk", destination="Kitchen")
    db.update_agent_state(ada.id, x=20, y=9)
    work = db.create_runtime_activity(away.id, "work", title="Report")
    db.save_work_snapshot(
        work.id, agent_id=away.id, task_id=None, transcript=[], fingerprints=[], no_progress_checkpoints=0,
    )
    db.update_agent_state(away.id, x=21, y=9)
    _on_vacation(away.id)

    await stand_down([ada.id, away.id], why=WHY, extra_task_ids=[], services=StandDownServices())

    for agent_id in (ada.id, away.id):
        open_items = [item for item in db.list_activities(agent_id=agent_id, limit=50) if item.status in {"active", "paused"}]
        assert open_items == []
    assert {item.id: item.detail for item in db.list_activities(agent_id=ada.id, limit=50)}[walk.id] == (
        "Closed: the test stopped them"
    )
    assert db.get_work_snapshot(work.id) is None
    seated = db.get_agent_state(ada.id)
    assert (seated.x, seated.y) == (1, 1), "an office agent is seated at its desk"
    left = db.get_agent_state(away.id)
    assert (left.x, left.y) == (21, 9), "a vacationer has no body in the office to seat"


async def test_extensions_release_is_filed_for_the_agents() -> None:
    _floor, [ada, bob] = _suspended_floor_with("Ada", "Bob")
    services = StandDownServices()

    await stand_down([ada.id, bob.id, ada.id], why=WHY, extra_task_ids=[], services=services)

    assert services.released == [[ada.id, bob.id]]


async def test_nothing_is_released_for_no_agents() -> None:
    services = StandDownServices()
    result = await stand_down([], why=WHY, extra_task_ids=[], services=services)
    assert services.released == [] and result.agent_ids == []


async def test_a_second_run_is_a_no_op() -> None:
    ada = _agent("Ada", 1)
    task = _task("Report", assigned_to=ada.id)
    _hosted_meeting(ada.id)
    db.create_cli_approval_request(agent_id=ada.id, command="make deploy")
    _running_process(ada)
    _on_vacation(ada.id)
    first = await stand_down([ada.id], why=WHY, extra_task_ids=[task.id], services=StandDownServices())
    assert first.tasks_cancelled and first.meetings_ended and first.approvals_closed and first.process_stopped_agent_ids

    again = await stand_down([ada.id], why=WHY, extra_task_ids=[task.id], services=StandDownServices())

    assert again.agent_ids == [ada.id]
    assert (again.tasks_cancelled, again.meetings_ended, again.approvals_closed, again.consents_closed) == ([], 0, 0, 0)
    assert again.process_stopped_agent_ids == [] and again.posted_lines == []


def test_open_task_ids_on_floor_reads_threads_assignees_and_reporters() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    other = _agent("Other", 2)
    thread = db.create_channel(name="Desk", member_agent_ids=[ada.id], created_by=ada.id)
    bound = create_or_bind_task(
        title="Bound", description="d", project=None, assigned_to=ada.id, requester_id=HUMAN_SENDER_ID,
        owner_id=None, created_by=HUMAN_SENDER_ID, parent_task_id=None, work_contract=None,
        source_channel="channel", notification_policy="completion_blocked", notification_channel_id=thread.id,
        audit_author_name="Human Operator", audit_author_type="human",
    ).task
    assigned = _task("Assigned", assigned_to=ada.id)
    filed = _task("Filed", assigned_to=None, requester_id=ada.id)
    elsewhere = _task("Elsewhere", assigned_to=other.id)
    closed = _task("Closed", assigned_to=ada.id)
    db.update_task(closed.id, status="cancelled")

    assert open_task_ids_on_floor(finance.id) == [bound.id, assigned.id, filed.id]
    assert elsewhere.id not in open_task_ids_on_floor(finance.id)


# ─── System-made denials never stick ───


def _resolved(request_id: str, *, by: str) -> None:
    assert db.resolve_consent_request(request_id, status="denied", decision_by=by) is not None


def test_a_system_host_path_deny_is_not_sticky_but_an_operator_deny_is() -> None:
    ada = _agent("Ada", 1)
    closed = db.create_consent_request(agent_id=ada.id, path="/srv/data", grant_root="/srv/data", reason="Read")
    _resolved(closed.id, by="system")
    assert db.find_denied_for_scope(ada.id, "/srv/data", task_id=None) is None

    refused = db.create_consent_request(agent_id=ada.id, path="/srv/data", grant_root="/srv/data", reason="Read")
    _resolved(refused.id, by="human")
    assert db.find_denied_for_scope(ada.id, "/srv/data", task_id=None).id == refused.id


def test_a_system_workspace_preference_deny_is_not_sticky_but_an_operator_deny_is() -> None:
    ada = _agent("Ada", 1)
    closed = db.create_consent_request(
        agent_id=ada.id, path="/srv/app", grant_root="/srv/app", reason="Write", card_kind=WORKSPACE_PREFERENCE_KIND,
    )
    _resolved(closed.id, by="system")
    assert db.find_workspace_preference_for_scope(ada.id, "/srv/app", task_id=None) is None
    # The fallback scope match (a write inside the folder) skips it too.
    assert _resolved_preference_for_write(agent_id=ada.id, path="/srv/app/src/main.py", task_id=None) is None

    refused = db.create_consent_request(
        agent_id=ada.id, path="/srv/app", grant_root="/srv/app", reason="Write", card_kind=WORKSPACE_PREFERENCE_KIND,
    )
    _resolved(refused.id, by="human")
    assert db.find_workspace_preference_for_scope(ada.id, "/srv/app", task_id=None).id == refused.id
    assert _resolved_preference_for_write(agent_id=ada.id, path="/srv/app/src/main.py", task_id=None).id == refused.id


def test_a_system_shell_executor_deny_is_not_sticky_but_an_operator_deny_is() -> None:
    ada = _agent("Ada", 1)

    def card():
        return db.create_consent_request(
            agent_id=ada.id, path=SHELL_EXECUTOR_GRANT_ROOT, grant_root=SHELL_EXECUTOR_GRANT_ROOT,
            reason="Validate", card_kind=SHELL_EXECUTOR_KIND,
        )

    _resolved(card().id, by="system")
    assert _denied_for_scope(ada.id, task_id=None) is None
    refused = card()
    _resolved(refused.id, by="human")
    assert _denied_for_scope(ada.id, task_id=None).id == refused.id


async def test_a_thread_archive_deny_is_no_longer_sticky() -> None:
    ada = _agent("Ada", 1)
    thread = db.create_channel(name="Desk", member_agent_ids=[ada.id], created_by=ada.id)
    card = db.create_consent_request(
        agent_id=ada.id, path="/srv/data", grant_root="/srv/data", reason="Read", channel_id=thread.id,
    )

    await archive_thread_as_operator(thread.id, cancel_open_tasks=True, services=StandDownServices())

    denied = db.get_consent_request(card.id)
    assert (denied.status, denied.decision_by) == ("denied", "system")
    assert db.find_denied_for_scope(ada.id, "/srv/data", task_id=None) is None
