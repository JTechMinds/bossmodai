"""BossMod AI — Settings preview of runtime contracts and prompt bundles.

Live turn assembly stays in context_builder. These helpers build representative
preview turns for Settings → Runtime Contracts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from core.agent_loop.communication import communication_profile_for_trigger
from core.llm.context_builder import (
    AUTHORED_PROMPT_ALLOWED_PATHS,
    TurnContext,
    _build_prompt_render_context,
    _default_role_prompt,
    _render_turn_contract,
    build_context,
)
from core.llm.template_engine import render_template
from core.models import Agent, AgentState


def _preview_trigger(trigger_type: str) -> dict[str, Any]:
    base = {
        "type": trigger_type,
        "from_name": "Human Operator",
        "content": "Can you give me a quick status update?",
        "source_channel": "chat",
        "task_title": "Write API summary",
        "task_description": "Summarize the current API behavior and save the draft.",
    }
    if trigger_type == "peer_message":
        base.update({"from_name": "Morgan", "from_agent": "agent-morgan", "content": "Can you take a look at the deployment notes?"})
    if trigger_type == "task_follow_up":
        base.update(
            {
                "from_name": "Morgan",
                "from_agent": "agent-morgan",
                "content": "I finished the draft and need your review.",
                "task_status": "accepted",
                "task_party": "stakeholder",
                "attention_kind": "review_request",
            }
        )
    if trigger_type in {"session_message", "session_response"}:
        base.update({"from_name": "Meeting Room", "content": "Please share your progress."})
    if trigger_type in {"channel_message", "channel_response"}:
        base.update({"from_name": "Planning Channel", "content": "Who can summarize next steps?"})
    if trigger_type == "task_assigned":
        base.update({"content": "", "from_name": "Human Operator"})
    if trigger_type == "activity_resumed":
        base.update({"content": "Continue the current work activity.", "activity_kind": "work"})
    if trigger_type == "watchdog_status_ping":
        base.update({"content": "Provide a status update on the current task.", "task_title": "Write API summary"})
    if trigger_type == "social":
        base.update({"content": "", "from_name": "Nearby Team", "nearby_names": ["Morgan", "Riley"]})
    return base


def _preview_activity(contract_kind: str, trigger_type: str) -> dict[str, Any] | None:
    if contract_kind != "execution":
        return None
    kind = "meeting" if trigger_type in {"session_message", "session_response", "channel_message", "channel_response"} else "work"
    title = "Draft API summary" if kind == "work" else "Planning sync"
    detail = "Continue the current activity with the team context already in progress."
    return {
        "id": "preview-activity",
        "kind": kind,
        "status": "active",
        "title": title,
        "detail": detail,
        "destination": "meetingRoom" if kind == "meeting" else "",
        "metadata": {},
    }


def _preview_task(contract_kind: str, trigger_type: str) -> dict[str, Any] | None:
    if contract_kind != "execution" and trigger_type not in {"task_assigned", "task_follow_up"}:
        return None
    return {
        "id": "preview-task",
        "title": "Write API summary",
        "description": "Summarize the current API behavior and save the result to /me/api_summary.md",
        "status": "active" if contract_kind == "execution" else ("accepted" if trigger_type == "task_follow_up" else "pending"),
        "project": "BossMod AI",
        "completion_summary": "",
        "status_note": "",
        "work_contract": {
            "deliverables": [{"type": "file", "path": "/me/api_summary.md", "description": None}],
        },
    }


def _preview_session(trigger_type: str) -> dict[str, Any] | None:
    if trigger_type not in {"session_message", "session_response"}:
        return None
    return {
        "title": "Planning Sync",
        "room_name": "Meeting Room",
        "participants": [{"name": "Taylor"}, {"name": "Morgan"}, {"name": "Riley"}],
    }


def _preview_channel(trigger_type: str) -> dict[str, Any] | None:
    if trigger_type not in {"channel_message", "channel_response"}:
        return None
    return {
        "kind": "channel",
        "name": "Planning",
        "participants": [{"name": "Taylor"}, {"name": "Morgan"}, {"name": "Riley"}],
    }
def preview_runtime_contract(
    contract_kind: str,
    trigger_type: str,
    template_overrides: dict[str, str] | None = None,
    trigger_overrides: dict[str, Any] | None = None,
) -> str:
    """Render one runtime contract against a representative preview context."""
    turn = _build_preview_turn_context(contract_kind, trigger_type)
    if trigger_overrides:
        turn.trigger = {**turn.trigger, **trigger_overrides}
    agent = turn.agent
    render_context = _build_prompt_render_context(turn)
    render_context["personality"] = render_template(
        agent.prompt_template or _default_role_prompt(agent),
        render_context,
        allowed_paths=AUTHORED_PROMPT_ALLOWED_PATHS,
    )
    return _render_turn_contract(contract_kind, render_context, template_overrides)


def preview_prompt_bundle(
    contract_kind: str,
    trigger_type: str,
    template_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Render the representative full prompt bundle for one runtime turn."""
    turn = _build_preview_turn_context(contract_kind, trigger_type)
    messages = build_context(turn, template_overrides=template_overrides)
    return {
        "messages": messages,
        "rendered": _render_preview_messages(messages),
    }


def _build_preview_turn_context(contract_kind: str, trigger_type: str) -> TurnContext:
    """Build a representative preview turn context for prompt authoring."""
    now = datetime.now(timezone.utc)
    agent = Agent(
        id="preview-agent",
        storage_key="preview-agent",
        name="Taylor",
        role="Operations Analyst",
        description="Keeps operational answers short and checkable.",
        done_fail_bar="Good: a named artifact or tests evidence. Fail: empty done.",
        prompt_template="You are {{agent_name}}, keep answers concise and operational.",
        created_at=now,
    )
    state = AgentState(
        agent_id=agent.id,
        x=14,
        y=9,
        status="work_active" if contract_kind == "execution" else "idle",
        last_active_at=now,
        idle_since=now,
    )
    turn = TurnContext(
        agent=agent,
        state=state,
        trigger=_preview_trigger(trigger_type),
        conversation_history=[],
        prompt_notifications=[],
        reference_materials=["RECENT COMPLETED TASKS:\n- API summary completed"],
        current_activity=_preview_activity(contract_kind, trigger_type),
        current_task=_preview_task(contract_kind, trigger_type),
        current_session=_preview_session(trigger_type),
        current_channel=_preview_channel(trigger_type),
        nearby_agents=[{"name": "Morgan"}, {"name": "Riley"}],
        pending_trigger_count=1,
        contract_kind=contract_kind,
        communication_snapshot_json=_preview_communication_snapshot_json(trigger_type),
    )
    return turn


def _preview_communication_snapshot_json(trigger_type: str) -> str | None:
    """Return a representative snapshot JSON for previewable communication turns."""
    profile = communication_profile_for_trigger(trigger_type)
    if profile is None:
        return None
    snapshot = {
        "communication": {
            "profile": profile.name,
            "trigger_type": trigger_type,
            "speaker": "Human Operator" if trigger_type == "human_chat" else "Morgan",
            "author_type": "human" if trigger_type == "human_chat" else "agent",
        },
        "runtime": {
            "status": "idle",
            "location": "Main Workspace",
            "cwd": "/projects/orchard",
            "current_activity": "none",
            "current_task": "none",
            "self_open_task_count": 1,
            "owned_delegated_task_count": 0,
            "waiting_on_me_count": 0,
        },
        "current_task": {
            "id": "preview-task",
            "title": "Write API summary",
            "status": "active",
            "description": "Summarize the current API behavior and save the result.",
        },
        "task_board": {
            "self": {
                "scope": "self",
                "current_task": {
                    "id": "preview-task",
                    "title": "Write API summary",
                    "status": "active",
                    "description": "Summarize the current API behavior and save the result.",
                },
                "sections": {
                    "my_open_tasks": [
                        {
                            "id": "preview-waiting-task",
                            "title": "Review release notes",
                            "status": "accepted",
                            "description": "Review the draft release notes and flag gaps.",
                        }
                    ]
                },
            },
            "owned": {
                "scope": "owned",
                "current_task": {
                    "id": "preview-task",
                    "title": "Write API summary",
                    "status": "active",
                    "description": "Summarize the current API behavior and save the result.",
                },
                "sections": {},
            },
            "project_summary": [
                {
                    "project": "Orchard",
                    "path": "/projects/orchard",
                    "counts": {"accepted": 1},
                    "latest_tasks": [
                        {
                            "title": "Review release notes",
                            "status": "accepted",
                            "assigned_to": "preview-agent",
                            "assignee_name": "Taylor",
                        }
                    ],
                }
            ],
        },
        "recent_completed_tasks": [
            {
                "task_id": "done-1",
                "title": "Review deployment notes",
                "status": "complete",
                "summary": "Reviewed the notes and flagged rollout risks.",
            }
        ],
    }
    return json.dumps(snapshot, indent=2)


def _render_preview_messages(messages: list[dict[str, str]]) -> str:
    """Format one preview message bundle into a readable text block."""
    counters: dict[str, int] = {}
    blocks: list[str] = []
    for message in messages:
        role = str(message.get("role") or "unknown")
        counters[role] = counters.get(role, 0) + 1
        heading = f"[{role.upper()} {counters[role]}]"
        blocks.append(f"{heading}\n{message.get('content', '')}")
    return "\n\n".join(blocks)
