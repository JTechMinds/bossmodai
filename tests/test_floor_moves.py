"""Moving people, threads and projects between floors (core/floor_moves.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.floor_roots import floor_root
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.virtual_fs import resolve_cli_path
from core.channel_members import ThreadSeatError, seat_agent_in_thread
from core.floor_moves import (
    MovePlanChanged,
    MoveRefused,
    ProjectExists,
    apply_move,
    list_projects,
    move_project,
    plan_move,
)
from core.floors import CROSS_FLOOR_DENY, AgentOnVacation, send_home, task_floor_id
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from db.floors import LOBBY_ID, create_floor


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


class _Services:
    """Records runtime resets. A move must cancel each mover's live turn."""

    def __init__(self) -> None:
        self.resets: list[str] = []

    async def reset_agent_runtime(self, agent_id: str) -> None:
        self.resets.append(agent_id)


def _agent(name: str, x: int, *, floor_id: str | None = None):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1, floor_id=floor_id)


def _thread(name: str, *agents) -> object:
    return db.create_channel(name=name, member_agent_ids=[a.id for a in agents], created_by=agents[0].id)


def _bound_task(title: str, assignee, channel) -> object:
    return create_or_bind_task(
        title=title,
        description="Board work",
        project=None,
        assigned_to=assignee.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=assignee.id,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel.id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task


def _members(channel) -> set[str]:
    return {row.agent_id for row in db.list_channel_members(channel.id)}


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _crew():
    """Ada and Bob share Books and Ledger; Ada also sits in Ops with Cy."""
    ada, bob, cy = _agent("Ada", 1), _agent("Bob", 2), _agent("Cy", 3)
    books = _thread("Books", ada, bob)
    ledger = _thread("Ledger", bob, ada)
    ops = _thread("Ops", ada, cy)
    return ada, bob, cy, books, ledger, ops


# ─── The plan ───


def test_a_thread_brings_its_members_and_its_identical_twin() -> None:
    ada, bob, cy, books, ledger, ops = _crew()
    finance = create_floor("Finance")

    plan = plan_move(finance.id, agent_ids=[], channel_ids=[books.id])

    assert {(ref.id, ref.reason) for ref in plan.agents} == {
        (ada.id, "thread member"), (bob.id, "thread member"),
    }
    assert all(ref.from_floor_id == LOBBY_ID for ref in plan.agents)
    assert [ref.id for ref in plan.threads] == [books.id]
    assert [ref.id for ref in plan.companions] == [ledger.id]
    assert sorted(plan.companions[0].member_names) == ["Ada", "Bob"]
    # Ops keeps Cy and loses Ada: it stays, and says who goes and who stays.
    assert [(ref.id, ref.leaving_names, ref.staying_names) for ref in plan.split_threads] == [
        (ops.id, ["Ada"], ["Cy"]),
    ]
    assert len(plan.fingerprint) == 64
    # Nothing was written.
    assert db.get_agent(ada.id).floor_id == LOBBY_ID
    assert db.get_channel(books.id).floor_id == LOBBY_ID


def test_a_picked_agent_is_marked_picked_even_inside_a_moving_thread() -> None:
    ada, bob, _cy, books, _ledger, _ops = _crew()
    finance = create_floor("Finance")
    plan = plan_move(finance.id, agent_ids=[bob.id], channel_ids=[books.id])
    assert [(ref.id, ref.reason) for ref in plan.agents] == [(bob.id, "picked"), (ada.id, "thread member")]


def test_an_excluded_companion_stays_behind_as_a_split_thread() -> None:
    ada, bob, _cy, books, ledger, ops = _crew()
    finance = create_floor("Finance")
    whole = plan_move(finance.id, agent_ids=[], channel_ids=[books.id])

    plan = plan_move(finance.id, agent_ids=[], channel_ids=[books.id], exclude_companion_ids=[ledger.id])

    assert plan.companions == []
    split = {ref.id: ref for ref in plan.split_threads}
    assert set(split) == {ledger.id, ops.id}
    assert sorted(split[ledger.id].leaving_names) == ["Ada", "Bob"]
    assert split[ledger.id].staying_names == []
    assert plan.fingerprint != whole.fingerprint
    # Only a would-be companion can be excluded; anything else is a stale click.
    with pytest.raises(MoveRefused):
        plan_move(finance.id, agent_ids=[], channel_ids=[books.id], exclude_companion_ids=[ops.id])


def test_open_work_bound_to_a_thread_that_stays_is_listed_as_stranded() -> None:
    ada, _bob, _cy, books, _ledger, ops = _crew()
    finance = create_floor("Finance")
    stranded = _bound_task("Ops report", ada, ops)
    _bound_task("Books close", ada, books)

    plan = plan_move(finance.id, agent_ids=[], channel_ids=[books.id])

    assert [(ref.id, ref.title, ref.thread_name) for ref in plan.stranded_tasks] == [
        (stranded.id, "Ops report", "Ops"),
    ]


def test_a_plan_refuses_nothing_chosen_already_there_archived_and_missing() -> None:
    ada, _bob, _cy, books, _ledger, _ops = _crew()
    finance = create_floor("Finance")
    with pytest.raises(MoveRefused):
        plan_move(finance.id, agent_ids=[], channel_ids=[])
    with pytest.raises(MoveRefused, match="Already on this floor: Ada, Books"):
        plan_move(LOBBY_ID, agent_ids=[ada.id], channel_ids=[books.id])
    db.archive_channel(books.id)
    with pytest.raises(MoveRefused, match="archived"):
        plan_move(finance.id, agent_ids=[], channel_ids=[books.id])
    with pytest.raises(LookupError):
        plan_move("nope", agent_ids=[ada.id], channel_ids=[])
    with pytest.raises(LookupError):
        plan_move(finance.id, agent_ids=["nobody"], channel_ids=[])


def test_a_vacationer_cannot_be_moved() -> None:
    ada = _agent("Ada", 1)
    finance = create_floor("Finance")
    send_home(ada.id)
    with pytest.raises(AgentOnVacation):
        plan_move(finance.id, agent_ids=[ada.id], channel_ids=[])
    client, headers = _client()
    response = client.post(f"/api/floors/{finance.id}/move-plan", headers=headers, json={"agent_ids": [ada.id]})
    assert response.status_code == 409


# ─── Applying it ───


async def test_apply_moves_threads_and_people_and_leaves_split_threads_clean() -> None:
    ada, bob, cy, books, ledger, ops = _crew()
    finance = create_floor("Finance")
    for channel in (books, ops):
        db.create_agent_trigger(
            agent_id=ada.id,
            trigger_type="channel_message",
            source_channel="channel",
            payload={"channel_id": channel.id, "content": "hi"},
        )
    db.create_agent_trigger(
        agent_id=ada.id, trigger_type="human_chat", source_channel="chat", payload={"content": "dm"},
    )
    plan = plan_move(finance.id, agent_ids=[], channel_ids=[books.id])
    services = _Services()

    result = await apply_move(
        finance.id, agent_ids=[], channel_ids=[books.id], fingerprint=plan.fingerprint, services=services,
    )

    assert sorted(result.moved_agents) == sorted([ada.id, bob.id])
    assert sorted(result.moved_threads) == sorted([books.id, ledger.id])
    assert result.left_threads == [ops.id]
    assert sorted(services.resets) == sorted([ada.id, bob.id])
    assert db.get_agent(ada.id).floor_id == db.get_agent(bob.id).floor_id == finance.id
    assert db.get_agent(cy.id).floor_id == LOBBY_ID
    assert db.get_channel(books.id).floor_id == db.get_channel(ledger.id).floor_id == finance.id
    assert db.get_channel(ops.id).floor_id == LOBBY_ID
    assert _members(books) == {ada.id, bob.id}
    assert _members(ops) == {cy.id}
    queued = [
        json.loads(trigger.payload or "{}").get("channel_id") or trigger.trigger_type
        for trigger in db.list_queued_triggers()
        if trigger.agent_id == ada.id
    ]
    # The trigger for the thread Ada left is gone; the moved thread's and the
    # unbound DM's are kept.
    assert sorted(queued) == sorted([books.id, "human_chat"])


async def test_apply_also_releases_archived_seats_off_the_target() -> None:
    ada, bob = _agent("Ada", 1), _agent("Bob", 2)
    old = _thread("Old", ada, bob)
    db.archive_channel(old.id)
    finance = create_floor("Finance")
    plan = plan_move(finance.id, agent_ids=[ada.id], channel_ids=[])
    await apply_move(finance.id, agent_ids=[ada.id], channel_ids=[], fingerprint=plan.fingerprint, services=_Services())
    assert _members(old) == {bob.id}


async def test_apply_refuses_a_plan_that_changed_since_it_was_shown() -> None:
    ada, bob, cy, books, _ledger, ops = _crew()
    finance = create_floor("Finance")
    plan = plan_move(finance.id, agent_ids=[], channel_ids=[books.id])
    dee = _agent("Dee", 4)
    db.add_channel_members(ops.id, [dee.id])
    services = _Services()

    with pytest.raises(MovePlanChanged):
        await apply_move(
            finance.id, agent_ids=[], channel_ids=[books.id], fingerprint=plan.fingerprint, services=services,
        )

    assert services.resets == []
    assert db.get_agent(ada.id).floor_id == LOBBY_ID
    assert db.get_channel(books.id).floor_id == LOBBY_ID
    assert _members(ops) == {ada.id, cy.id, dee.id}


async def test_moved_threads_and_people_work_on_the_target_floor() -> None:
    ada, bob, cy, books, _ledger, ops = _crew()
    card = _bound_task("Books close", ada, books)
    finance = create_floor("Finance")
    plan = plan_move(finance.id, agent_ids=[], channel_ids=[books.id])
    await apply_move(finance.id, agent_ids=[], channel_ids=[books.id], fingerprint=plan.fingerprint, services=_Services())

    assert task_floor_id(db.get_task(card.id)) == finance.id
    assert db.find_active_channel_for_members([ada.id, bob.id]).floor_id == finance.id
    eve = _agent("Eve", 5, floor_id=finance.id)
    seat_agent_in_thread(books.id, eve.id)
    assert eve.id in _members(books)
    fresh = db.create_channel(name="New", member_agent_ids=[ada.id, eve.id], created_by=ada.id)
    assert fresh.floor_id == finance.id
    # Ada cannot be seated back in the Lobby thread she left.
    with pytest.raises(ThreadSeatError, match=CROSS_FLOOR_DENY):
        seat_agent_in_thread(ops.id, ada.id)


async def test_movers_leave_their_old_floors_projects_behind_as_cwd() -> None:
    ada, bob, cy = _agent("Ada", 1), _agent("Bob", 2), _agent("Cy", 3)
    finance = create_floor("Finance")
    # The same project name on both floors: a stale "/projects/x" would quietly
    # resolve to Finance's x after the move.
    lobby_x = floor_root(LOBBY_ID) / "x"
    lobby_x.mkdir()
    (floor_root(finance.id) / "x").mkdir()
    db.update_agent_cli_state(ada.id, cwd="/projects/x")
    # Stored as an absolute path, the form a cd through a host root leaves.
    db.update_agent_cli_state(bob.id, cwd=str(lobby_x.resolve()))
    db.update_agent_cli_state(cy.id, cwd="/projects/x")
    plan = plan_move(finance.id, agent_ids=[ada.id, bob.id], channel_ids=[])

    result = await apply_move(
        finance.id, agent_ids=[ada.id, bob.id], channel_ids=[], fingerprint=plan.fingerprint, services=_Services(),
    )

    assert sorted(result.cwd_reset) == sorted([ada.id, bob.id])
    assert get_cli_cwd(ada.id) == get_cli_cwd(bob.id) == "/me"
    # Cy did not move; Lobby's x is still Cy's /projects/x.
    assert get_cli_cwd(cy.id) == "/projects/x"
    moved = db.get_agent(ada.id)
    here = resolve_cli_path(moved.storage_key, get_cli_cwd(ada.id), ".")
    assert here.mount == "me"
    assert here.real_path.resolve() != (floor_root(finance.id) / "x").resolve()


async def test_a_mover_outside_projects_keeps_its_cwd() -> None:
    ada = _agent("Ada", 1)
    finance = create_floor("Finance")
    db.update_agent_cli_state(ada.id, cwd="/me/notes")
    plan = plan_move(finance.id, agent_ids=[ada.id], channel_ids=[])
    result = await apply_move(
        finance.id, agent_ids=[ada.id], channel_ids=[], fingerprint=plan.fingerprint, services=_Services(),
    )
    assert result.cwd_reset == []
    assert get_cli_cwd(ada.id) == "/me/notes"


def test_move_api_plans_applies_and_broadcasts(monkeypatch: pytest.MonkeyPatch) -> None:
    ada, bob, _cy, books, ledger, ops = _crew()
    finance = create_floor("Finance")
    monkeypatch.setattr("api.routes.floors.runtime_services", _Services())
    painted: list[str] = []
    floors_sent: list[object] = []

    async def _channel(summary: dict) -> None:
        painted.append(str(summary["id"]))

    async def _floors(rows: list) -> None:
        floors_sent.append(rows)

    async def _world() -> None:
        painted.append("world")

    monkeypatch.setattr("api.routes.floors.manager.broadcast_channel_updated", _channel)
    monkeypatch.setattr("api.routes.floors.manager.broadcast_floors_updated", _floors)
    monkeypatch.setattr("api.routes.floors.manager.broadcast_world_state", _world)
    client, headers = _client()

    body = {"agent_ids": [], "channel_ids": [books.id], "exclude_companion_ids": []}
    planned = client.post(f"/api/floors/{finance.id}/move-plan", headers=headers, json=body)
    assert planned.status_code == 200
    plan = planned.json()
    assert {"agents", "threads", "companions", "split_threads", "stranded_tasks", "fingerprint"} <= set(plan)
    assert [row["id"] for row in plan["companions"]] == [ledger.id]

    moved = client.post(
        f"/api/floors/{finance.id}/move", headers=headers, json={**body, "fingerprint": plan["fingerprint"]},
    )
    assert moved.status_code == 200
    assert moved.json()["left_threads"] == [ops.id]
    assert painted[0] == "world"
    assert set(painted[1:]) == {books.id, ledger.id, ops.id}
    assert len(floors_sent) == 1
    assert db.get_agent(ada.id).floor_id == finance.id

    again = client.post(f"/api/floors/{finance.id}/move-plan", headers=headers, json=body)
    assert again.status_code == 400
    missing = client.post("/api/floors/nope/move-plan", headers=headers, json=body)
    assert missing.status_code == 404


# ─── Projects ───


def _project(floor_id: str, name: str) -> Path:
    path = floor_root(floor_id) / name
    (path / "src").mkdir(parents=True)
    (path / "src" / "plan.md").write_text("plan", encoding="utf-8")
    return path


def test_list_projects_is_the_floor_folders_top_level_directories() -> None:
    finance = create_floor("Finance")
    _project(finance.id, "books")
    _project(finance.id, "Audit")
    (floor_root(finance.id) / ".hidden").mkdir()
    (floor_root(finance.id) / "loose.txt").write_text("x", encoding="utf-8")
    rows = list_projects(finance.id)
    assert [row["name"] for row in rows] == ["Audit", "books"]
    assert all(row["modified_at"] for row in rows)
    with pytest.raises(LookupError):
        list_projects("nope")


def test_project_move_moves_the_folder_rewrites_paths_and_resets_cwd() -> None:
    finance = create_floor("Finance")
    legal = create_floor("Legal")
    source = _project(finance.id, "books")
    stays = _agent("Stays", 1, floor_id=finance.id)
    elsewhere = _agent("Elsewhere", 2, floor_id=finance.id)
    db.update_agent_cli_state(stays.id, cwd="/projects/books/src")
    db.update_agent_cli_state(elsewhere.id, cwd="/projects/books-2")
    absolute = _agent("Absolute", 3, floor_id=finance.id)
    db.update_agent_cli_state(absolute.id, cwd=str((source / "src").resolve()))
    artifact = db.upsert_artifact(
        agent_id=stays.id,
        task_id=None,
        virtual_path="/projects/books/src/plan.md",
        absolute_path=str((source / "src" / "plan.md").resolve()),
        title="plan.md",
        kind="file",
        category="project",
        size_bytes=4,
        source_command=None,
    )

    result = move_project("books", finance.id, legal.id)

    target = floor_root(legal.id) / "books"
    assert not source.exists()
    assert (target / "src" / "plan.md").read_text(encoding="utf-8") == "plan"
    assert result.artifacts_rewritten == 1
    assert result.cwd_reset_agent_ids == [stays.id, absolute.id]
    assert db.get_agent_cli_state(absolute.id).cwd == "/me"
    assert db.get_agent_cli_state(stays.id).cwd == "/me"
    assert db.get_agent_cli_state(elsewhere.id).cwd == "/projects/books-2"
    rewritten = db.get_artifact_by_absolute_path(str((target / "src" / "plan.md").resolve()))
    assert rewritten is not None and rewritten.id == artifact.id
    assert rewritten.virtual_path == "/projects/books/src/plan.md"


def test_project_move_refuses_a_taken_name_same_floor_and_bad_names() -> None:
    finance = create_floor("Finance")
    legal = create_floor("Legal")
    _project(finance.id, "books")
    _project(legal.id, "books")
    with pytest.raises(ProjectExists):
        move_project("books", finance.id, legal.id)
    assert (floor_root(finance.id) / "books").is_dir()
    with pytest.raises(ValueError):
        move_project("books", finance.id, finance.id)
    for bad in ("../books", ".git", "", "a/b"):
        with pytest.raises(ValueError):
            move_project(bad, finance.id, legal.id)
    with pytest.raises(LookupError):
        move_project("ghost", finance.id, legal.id)

    client, headers = _client()
    listed = client.get(f"/api/floors/{legal.id}/projects", headers=headers)
    assert listed.status_code == 200
    assert [row["name"] for row in listed.json()] == ["books"]
    conflict = client.post(
        f"/api/floors/{legal.id}/projects/move", headers=headers,
        json={"project": "books", "from_floor_id": finance.id},
    )
    assert conflict.status_code == 409
    missing = client.post(
        f"/api/floors/{legal.id}/projects/move", headers=headers,
        json={"project": "ghost", "from_floor_id": finance.id},
    )
    assert missing.status_code == 404
