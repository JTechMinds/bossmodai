"""Phase 3 (refresh efficiency): per-route statement budgets, and same output.

Every SQLite statement a request issues is counted at
``SQLiteCompatConnection.execute``, the one path every statement takes, so
nothing a helper sends on the side is missed. A budget is a fixed number,
the same at one row and at fifty: that is what "no N+1" means.

Batching must not change what the routes return. The Tasks place and the
desk read the board, and the rail reads the channel list, so each is
compared against an oracle that reads the old way, one row at a time.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth, install_settings_refresh
from api.routes import router
from core import config
from core.agent_loop.channel_host import is_thread_paused, pause_thread
from core.agent_loop.role_contracts import is_auditor_specialty, operator_done_claim_guidance
from core.bm_cli.approval_gate import global_auto_approve_enabled
from core.models.message import HUMAN_SENDER_ID
from core.tasking import build_task_board, serialize_task_board
from core.tasking.resolution import OPEN_TASK_STATUSES
from db.connection import SQLiteCompatConnection

# One request through the app's own middleware stack, statement by statement.
#
# Board, any scope (9):
#   1 settings revision (SettingsRefreshMiddleware; the token is cached)
#   1 the route's agent lookup
#   1 the agent's active work activity, 1 the current task it is bound to
#   1 open tasks, every open status at once
#   1 self: recent completed tasks;  owned: recent completed delegated tasks
#   1 self: those tasks by id;       owned/delegated: the rollup's agents
#   1 every agent the serialized tasks name
#   1 every serialized task's newest event
_BOARD_BUDGET = 9
# Channel list (7):
#   1 settings revision, 1 channels (newest message joined for the order),
#   1 members, 1 their active activities, 1 newest message per channel,
#   1 paused threads, 1 global auto-approve (a live read, once per request)
_CHANNELS_BUDGET = 7


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


def _client() -> TestClient:
    """The app's real request stack: the token gate and the settings refresh."""
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    install_settings_refresh(app)
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


@contextmanager
def _counting() -> Iterator[list[str]]:
    calls: list[str] = []
    original = SQLiteCompatConnection.execute

    def counting(self, sql, params=None):
        calls.append(sql)
        return original(self, sql, params)

    SQLiteCompatConnection.execute = counting
    try:
        yield calls
    finally:
        SQLiteCompatConnection.execute = original


# ─── Seeds ───


def _task(title: str, *, status: str, **fields: Any):
    task = db.create_task(title, **fields)
    if status == "complete":
        db.update_task(task.id, status="active")
    if status != "pending":
        db.update_task(task.id, status=status)
    return task


def _seed_board(task_count: int) -> dict[str, str]:
    """A board with every shape the serializer handles.

    ``task_count`` open tasks the worker holds for the boss, cycling through
    the open statuses and four kinds of requester (the operator, the boss,
    the auditor, an id with no agent). Plus: a current task, delegated
    children with a parent, finished tasks, tasks with several events and
    tasks with none.
    """
    boss = db.create_agent("Boss", role="Manager", desk_x=1, desk_y=1)
    worker = db.create_agent("Worker", role="Engineer", done_fail_bar="tests pass", desk_x=2, desk_y=1)
    auditor = db.create_agent("Auditor", role="QA Auditor", desk_x=3, desk_y=1)
    requesters = (HUMAN_SENDER_ID, boss.id, auditor.id, "no-such-agent")
    first = None
    for index in range(task_count):
        task = _task(
            f"Task {index}",
            status=OPEN_TASK_STATUSES[index % len(OPEN_TASK_STATUSES)],
            project="Apollo" if index % 2 else "Gemini",
            assigned_to=worker.id,
            owner_id=boss.id,
            requester_id=requesters[index % len(requesters)],
        )
        first = first or task
        for event in range(index % 3):
            db.create_task_event(
                task_id=task.id,
                author_type="agent",
                author_name="Boss",
                event_type="comment",
                content=f"note {index}.{event}",
            )
    parent = _task("Parent", status="active", project="Apollo", assigned_to=boss.id, owner_id=boss.id)
    for index in range(3):
        _task(
            f"Child {index}",
            status=("pending", "blocked", "waiting")[index],
            project="Apollo",
            assigned_to=auditor.id,
            owner_id=boss.id,
            requester_id=boss.id,
            parent_task_id=parent.id,
        )
    for index in range(3):
        _task(f"Done {index}", status="complete", assigned_to=worker.id, owner_id=boss.id)
    if first is not None:
        db.create_runtime_activity(worker.id, "work", task_id=first.id)
    return {"boss": boss.id, "worker": worker.id, "auditor": auditor.id}


def _seed_channels(channel_count: int) -> None:
    agents = [
        db.create_agent(f"Member {index}", role="Eng", desk_x=1 + index, desk_y=2)
        for index in range(3)
    ]
    db.create_runtime_activity(agents[0].id, "work")
    for index in range(channel_count):
        members = [agents[index % 3].id, agents[(index + 1) % 3].id]
        channel = db.create_channel(name=f"Room {index}", member_agent_ids=members)
        if index % 4 != 3:
            db.create_channel_message(
                channel_id=channel.id,
                author_type="human",
                author_name="You",
                content=f"hello {index}",
                source_channel="channel",
            )
        if index % 5 == 1:
            pause_thread(channel.id)


# ─── Oracles: the pre-batch reads, one row at a time ───


def _legacy_task_row(task) -> dict[str, Any]:
    """``_serialize_task`` as it read before batching: get_agent per name, events per task."""
    assigned = db.get_agent(task.assigned_to) if task.assigned_to else None
    owner = db.get_agent(task.owner_id) if task.owner_id else None
    requester_name = None
    if task.requester_id and task.requester_id != HUMAN_SENDER_ID:
        requester = db.get_agent(task.requester_id)
        requester_name = requester.name if requester is not None else None
    elif task.requester_id == HUMAN_SENDER_ID:
        requester_name = "Human Operator"
    events = db.list_task_events(task.id, limit=5)
    latest = events[-1] if events else None
    return {
        **task.model_dump(mode="json"),
        "assigned_to_name": assigned.name if assigned is not None else None,
        "assigned_to_role": assigned.role if assigned is not None else None,
        "assigned_to_done_fail_bar": assigned.done_fail_bar if assigned is not None else None,
        "done_claim_guidance": operator_done_claim_guidance(
            auditor=is_auditor_specialty(assigned.role if assigned is not None else None),
            done_fail_bar=assigned.done_fail_bar if assigned is not None else None,
            has_file_deliverables=bool(task.work_contract and task.work_contract.deliverables),
        ),
        "owner_name": owner.name if owner is not None else None,
        "requester_name": requester_name,
        "latest_event": (
            {
                "event_type": latest.event_type,
                "author_name": latest.author_name,
                "content": latest.content,
                "created_at": latest.created_at,
            }
            if latest is not None
            else None
        ),
    }


def _legacy_open_ids(**filters: str) -> list[str]:
    """``_open_tasks`` as it read before batching: one ``list_tasks`` per open status."""
    tasks = []
    seen: set[str] = set()
    for status in OPEN_TASK_STATUSES:
        for task in db.list_tasks(status=status, **filters):
            if task.id not in seen:
                seen.add(task.id)
                tasks.append(task)
    tasks.sort(key=lambda item: (item.last_activity, item.created_at), reverse=True)
    return [task.id for task in tasks]


def _legacy_rollup(tasks) -> list[dict[str, Any]]:
    """``_assignee_rollup`` as it read before batching: get_agent per task."""
    counts: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if not task.assigned_to:
            continue
        agent = db.get_agent(task.assigned_to)
        row = counts.setdefault(task.assigned_to, {
            "agent_id": task.assigned_to,
            "agent_name": agent.name if agent is not None else task.assigned_to,
            "counts": {},
        })
        row["counts"][task.status] = int(row["counts"].get(task.status, 0)) + 1
    rows = list(counts.values())
    rows.sort(key=lambda item: str(item["agent_name"]).lower())
    return rows


def _as_received(payload: Any) -> Any:
    """The route's JSON encoding, so datetimes compare as the UI receives them."""
    return json.loads(json.dumps(jsonable_encoder(payload)))


def _legacy_board_json(board: dict[str, Any]) -> Any:
    def row(task):
        return _legacy_task_row(task) if task is not None else None

    return _as_received({
        "scope": board["scope"],
        "current_task": row(board.get("current_task")),
        "sections": {key: [row(item) for item in value] for key, value in board["sections"].items()},
        "assignee_rollup": board.get("assignee_rollup") or [],
        "child_tasks_by_parent": {
            key: [row(item) for item in value] for key, value in board["child_tasks_by_parent"].items()
        },
    })


def _legacy_channels_json(status: str) -> Any:
    """``GET /api/channels`` as it read before batching, one channel at a time."""
    from api.routes.agents import _serialize_channel_summary

    items = []
    for channel in db.list_channels(status=status):
        items.append(_serialize_channel_summary(
            channel,
            members=db.list_channel_member_details(channel.id),
            latest_message=db.get_latest_channel_message(channel.id),
            conversation_paused=is_thread_paused(channel.id),
            auto_approve_global=global_auto_approve_enabled(),
        ))
    return _as_received(items)


# ─── Budgets ───


@pytest.mark.parametrize("task_count", [1, 50])
@pytest.mark.parametrize("scope", ["self", "owned", "delegated"])
def test_the_board_reads_a_fixed_number_of_statements(task_count: int, scope: str) -> None:
    ids = _seed_board(task_count)
    agent_id = ids["worker"] if scope == "self" else ids["boss"]
    client = _client()
    headers = _headers()
    with _counting() as calls:
        res = client.get("/api/tasks/board", params={"agent_id": agent_id, "scope": scope}, headers=headers)
    assert res.status_code == 200, res.text
    assert len(calls) <= _BOARD_BUDGET, calls


@pytest.mark.parametrize("channel_count", [1, 20])
def test_the_channel_list_reads_a_fixed_number_of_statements(channel_count: int) -> None:
    _seed_channels(channel_count)
    client = _client()
    headers = _headers()
    with _counting() as calls:
        res = client.get("/api/channels", headers=headers)
    assert res.status_code == 200, res.text
    assert len(res.json()) == channel_count
    assert len(calls) <= _CHANNELS_BUDGET, calls


# ─── Same output ───


@pytest.mark.parametrize("scope", ["self", "owned", "delegated"])
def test_the_board_json_is_what_the_row_by_row_reads_produced(scope: str) -> None:
    ids = _seed_board(14)
    agent_id = ids["worker"] if scope == "self" else ids["boss"]
    expected = _legacy_board_json(build_task_board(agent_id, scope=scope))

    res = _client().get("/api/tasks/board", params={"agent_id": agent_id, "scope": scope}, headers=_headers())

    assert res.status_code == 200, res.text
    assert res.json() == expected
    # Not vacuous: the seed reaches the parts each scope fills.
    assert any(rows for rows in expected["sections"].values())
    if scope == "self":
        assert expected["current_task"] is not None
        assert expected["sections"]["recent_completed_tasks"]
        assert any(row["latest_event"] for row in expected["sections"]["my_open_tasks"])
        assert {row["requester_name"] for row in expected["sections"]["my_open_tasks"]} >= {
            "Human Operator", "Boss", "Auditor", None,
        }
    else:
        assert expected["assignee_rollup"]
        assert expected["child_tasks_by_parent"]


def test_board_sections_hold_the_tasks_one_read_per_status_found() -> None:
    ids = _seed_board(14)
    self_board = build_task_board(ids["worker"], scope="self")
    owned_board = build_task_board(ids["boss"], scope="owned")

    assert [task.id for task in self_board["sections"]["my_open_tasks"]] == _legacy_open_ids(
        assigned_to=ids["worker"]
    )
    assert [task.id for task in owned_board["sections"]["tasks_i_own"]] == _legacy_open_ids(owner_id=ids["boss"])
    assert owned_board["assignee_rollup"] == _legacy_rollup(owned_board["sections"]["tasks_i_delegated"])


def test_a_task_in_two_sections_is_serialized_once() -> None:
    ids = _seed_board(14)
    board = serialize_task_board(build_task_board(ids["boss"], scope="owned"))
    by_id: dict[str, list[dict[str, Any]]] = {}
    for rows in board["sections"].values():
        for row in rows:
            by_id.setdefault(row["id"], []).append(row)
    repeated = [rows for rows in by_id.values() if len(rows) > 1]
    assert repeated, "the seed must put a task in more than one section"
    for rows in repeated:
        assert all(row is rows[0] for row in rows)


@pytest.mark.parametrize("status", ["active", "archived"])
def test_the_channel_list_json_is_what_the_row_by_row_reads_produced(status: str) -> None:
    _seed_channels(12)
    if status == "archived":
        for channel in db.list_channels(status="active")[:5]:
            db.archive_channel(channel.id)
    expected = _legacy_channels_json(status)

    res = _client().get("/api/channels", params={"status": status}, headers=_headers())

    assert res.status_code == 200, res.text
    assert res.json() == expected
    assert expected, "the seed must list threads"
    if status == "active":
        assert any(item["conversation_paused"] for item in expected)
        assert any(item["latest_message"] is None for item in expected)
        assert any(
            member["currentActivityKind"] == "work" for item in expected for member in item["members"]
        )


def test_the_channel_order_follows_the_newest_message() -> None:
    _seed_channels(4)
    oldest = db.list_channels()[-1]
    db.create_channel_message(
        channel_id=oldest.id,
        author_type="human",
        author_name="You",
        content="newest",
        source_channel="channel",
    )
    assert db.list_channels()[0].id == oldest.id


# ─── Query plans ───


def test_the_last_message_times_query_is_index_backed() -> None:
    """``db/world._latest_human_chat_times`` reads both message indexes, no table scan."""
    from db.crud import query

    plan = query(
        """
        EXPLAIN QUERY PLAN
        SELECT
            CASE WHEN from_agent = $1 THEN to_agent ELSE from_agent END AS agent_id,
            MAX(created_at) AS last_message_at
        FROM messages
        WHERE (from_agent = $1 AND to_agent IS NOT NULL)
           OR to_agent = $1
        GROUP BY agent_id
        """,
        [HUMAN_SENDER_ID],
    )
    details = [str(row["detail"]) for row in plan]
    assert any("idx_messages_from_agent_created" in detail for detail in details), details
    assert any("idx_messages_to_agent_created" in detail for detail in details), details
    assert not any(detail.startswith("SCAN messages") for detail in details), details


def test_the_explained_query_is_the_one_world_state_sends() -> None:
    """Keeps the EXPLAIN above honest: it must be the SQL ``_latest_human_chat_times`` runs."""
    import inspect

    import db.world as world

    source = " ".join(inspect.getsource(world._latest_human_chat_times).split())
    assert "WHERE (from_agent = $1 AND to_agent IS NOT NULL) OR to_agent = $1 GROUP BY agent_id" in source
