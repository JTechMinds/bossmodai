"""Fix D — value tags render the full block; aliases apply only to conditions."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.turn_context import _get_current_activity, _get_current_task
from core.llm import context_builder
from core.llm.template_engine import render_template
from core.models.message import HUMAN_SENDER_ID
from core.tasking import create_or_bind_task

_ALLOWED = {"task", "task.status", "activity", "activity.kind"}
_CONTEXT = {
    "task": {"value": "FULL TASK BLOCK", "status": "active"},
    "activity": {"value": "FULL ACTIVITY BLOCK", "kind": "work"},
}


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


def test_value_tags_render_the_full_block() -> None:
    rendered = render_template("{{task}} | {{activity}}", _CONTEXT, allowed_paths=_ALLOWED)
    assert rendered == "FULL TASK BLOCK | FULL ACTIVITY BLOCK"


def test_condition_shorthand_still_uses_the_alias() -> None:
    template = "{{if task = 'active'}}T{{end}}{{if activity = 'work'}}A{{end}}{{if task = 'done'}}X{{end}}"
    assert render_template(template, _CONTEXT, allowed_paths=_ALLOWED) == "TA"


def test_allowed_paths_include_the_unaliased_blocks() -> None:
    names = {item["name"] for item in context_builder.template_variable_metadata()}
    assert {"task", "activity", "task.status", "activity.kind"} <= names


def test_rendered_system_prompt_contains_the_task_description() -> None:
    agent = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1)
    task = create_or_bind_task(
        title="Execute the 9 fixes",
        description="Refactor fetch in message.js and split conversation.css.",
        project=None,
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task
    activity_runtime.activate_work_activity(agent.id, task, task_status="active", detail="Halfway through fix 4.")
    state = db.get_agent_state(agent.id)
    assert state is not None
    turn = context_builder.TurnContext(
        agent=agent,
        state=state,
        trigger={"type": "activity_resumed", "content": "Resume.", "source_channel": "work"},
        conversation_history=[],
        prompt_notifications=[],
        reference_materials=[],
        current_activity=_get_current_activity(agent.id),
        current_task=_get_current_task(agent.id),
        contract_kind="execution",
    )
    system_prompt = context_builder.build_context(turn)[0]["content"]
    current_task = system_prompt.split("## Current Task", 1)[1].split("## Task Board", 1)[0]
    assert "description: Refactor fetch in message.js and split conversation.css." in current_task
    assert current_task.strip() != "active"
    current_activity = system_prompt.split("## Current Activity", 1)[1].split("## Current Task", 1)[0]
    assert "kind: work" in current_activity
    assert current_activity.strip() != "work"
