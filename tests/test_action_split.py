"""HA-STRUCT-P1-02 — actions.py split keeps parse/dispatch public API."""

from __future__ import annotations

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


def test_parse_action_compact_idle_and_done() -> None:
    idle = parse_action('{"act":"idle","th":"nothing to do"}')
    assert idle["action"] == "idle"
    assert idle["thought"] == "nothing to do"

    done = parse_action(
        '{"act":"done","data":{"sum":"Draft saved.","msg":"Finished the draft."},"th":"complete"}'
    )
    assert done["action"] == "complete"
    assert done["summary"] == "Draft saved."
    assert done["followUpMessage"] == "Finished the draft."


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
        model_work = None
        model_social = None
        model_reasoning = None
        model_extraction = None
        model_self_queue = None

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
