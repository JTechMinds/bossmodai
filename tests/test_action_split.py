"""HA-STRUCT-P1-02 — actions.py split keeps parse/dispatch public API."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop import actions
from core.agent_loop.actions import (
    TERMINAL_ACTIONS,
    _ACTION_HANDLERS,
    _SUPPORTED_ACTIONS,
    execute_action,
    parse_action,
)


def test_public_exports_still_import_from_actions() -> None:
    assert callable(parse_action)
    assert callable(execute_action)
    assert TERMINAL_ACTIONS == {"idle", "waiting", "complete", "blocked", "delegated", "abandoned"}


def test_dispatch_table_covers_supported_actions() -> None:
    assert set(_ACTION_HANDLERS) == _SUPPORTED_ACTIONS
    for name, handler in _ACTION_HANDLERS.items():
        assert callable(handler), name


def test_handler_modules_importable() -> None:
    from core.agent_loop import (
        actions_cli,
        actions_lifecycle,
        actions_meetings,
        actions_shared,
        actions_tasks,
        actions_work,
        task_followups,
    )

    assert actions_cli._handle_bm_cli is _ACTION_HANDLERS["bm_cli"]
    assert actions_cli._handle_request_host_access is _ACTION_HANDLERS["request_host_access"]
    assert actions_work._handle_work is _ACTION_HANDLERS["work"]
    assert actions_work._handle_message is _ACTION_HANDLERS["message"]
    assert actions_work._handle_walk_to is _ACTION_HANDLERS["walkTo"]
    assert actions_work._handle_idle is _ACTION_HANDLERS["idle"]
    assert actions_tasks._handle_task_message is _ACTION_HANDLERS["taskMessage"]
    assert actions_tasks._handle_delegate_task is _ACTION_HANDLERS["delegateTask"]
    assert actions_lifecycle._handle_waiting is _ACTION_HANDLERS["waiting"]
    assert actions_lifecycle._handle_complete is _ACTION_HANDLERS["complete"]
    assert actions_lifecycle._handle_blocked is _ACTION_HANDLERS["blocked"]
    assert actions_lifecycle._handle_delegated is _ACTION_HANDLERS["delegated"]
    assert actions_lifecycle._handle_abandoned is _ACTION_HANDLERS["abandoned"]
    assert actions_meetings._handle_attend_meeting is _ACTION_HANDLERS["attendMeeting"]
    assert actions_meetings._handle_remote_meeting is _ACTION_HANDLERS["remoteMeeting"]
    assert callable(actions_shared._count_action_tokens)
    assert callable(task_followups._append_task_follow_up_message)


def test_task_follow_up_trigger_bound_and_turn_path_imports() -> None:
    from core.agent_loop import actions_tasks, execution_turn
    from core.agent_loop.activity_scheduler import build_task_follow_up_trigger

    assert actions_tasks.build_task_follow_up_trigger is build_task_follow_up_trigger
    assert callable(execution_turn._run_execution_turn)
    assert _ACTION_HANDLERS["taskMessage"] is actions_tasks._handle_task_message


def test_parse_action_compact_idle_and_done() -> None:
    idle = parse_action('{"act":"idle","th":"nothing to do"}')
    assert idle["action"] == "idle"
    assert idle["thought"] == "nothing to do"

    done = parse_action(
        '{"act":"done","data":{"sum":"Draft saved.","msg":"Finished the draft.","claim":{"type":"proof","ev":"allow: /me/draft.md"}},"th":"complete"}'
    )
    assert done["action"] == "complete"
    assert done["summary"] == "Draft saved."
    assert done["followUpMessage"] == "Finished the draft."
    assert done["doneClaim"]["type"] == "proof"


def test_parse_action_garbage_returns_parse_failed() -> None:
    parsed = parse_action("not json at all")
    assert parsed["action"] == "_parse_failed"
    assert "_raw_snippet" in parsed


def test_parse_action_code_fence_and_walk() -> None:
    raw = """```json
{"act":"walk","data":{"dst":"desk"},"th":"go sit"}
```"""
    parsed = parse_action(raw)
    assert parsed["action"] == "walkTo"
    assert parsed["destination"] == "desk"


async def test_execute_action_unknown_action_is_status_changed() -> None:
    class _Agent:
        name = "Ada"
        connection_id = None

    class _State:
        x = 0
        y = 0

    result = await execute_action({"action": "notARealAction"}, _Agent(), _State())
    assert result["event"] == "status_changed"
    assert "Unknown action" in result["detail"]
    assert result["agent_name"] == "Ada"


def test_actions_module_stays_parse_and_dispatch() -> None:
    source = actions.__file__
    assert source.endswith("actions.py")
    # Handlers must not live in the dispatch module.
    text = open(source, encoding="utf-8").read()
    assert "async def _handle_work" not in text
    assert "async def _handle_complete" not in text
    assert "async def execute_action" in text
    assert "def parse_action" in text


# ─── walkTo: no shared tile, no fallback desk ───


@pytest.fixture
def _fresh_db():
    """A clean temp database (conftest points BOSSMOD_DB_PATH at a temp file)."""
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    yield
    db.close_connection()


async def test_a_deskless_walk_to_desk_is_told_so_and_does_not_move(_fresh_db) -> None:
    from core.agent_loop import activity_runtime, actions_work

    agent = db.create_agent("Deskless")
    assert agent.desk_x is None
    state = db.get_agent_state(agent.id)

    result = await actions_work._handle_walk_to(agent, state, {"destination": "desk"})

    assert result["event"] == "world_feedback"
    assert result["detail"] == "You have no desk assigned; this floor's desks are all taken."
    assert "path" not in result
    assert activity_runtime.get_active_activity(agent.id) is None


async def test_two_agents_walking_to_one_room_get_different_tiles(_fresh_db) -> None:
    from core.agent_loop import actions_work
    from core.world.tilemap import get_room_at, is_chair

    first = db.create_agent("First")
    second = db.create_agent("Second")

    walked = []
    for agent in (first, second):
        result = await actions_work._handle_walk_to(
            agent, db.get_agent_state(agent.id), {"destination": "meetingRoom"},
        )
        assert result["event"] == "agent_moved", result
        walked.append(result["path"][-1])

    # The first walk is still in progress, so its destination is already
    # taken when the second agent picks a tile.
    assert walked[0] != walked[1]
    for tile in walked:
        assert get_room_at(*tile)["id"] == "meeting_room"
        assert not is_chair(tile)
