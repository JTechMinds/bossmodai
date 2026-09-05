"""Meeting execution handlers (room and remote).

Mechanical extract from actions.py (HA-STRUCT-P1-02).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.agent_loop import activity_runtime
from core.agent_loop.actions_shared import (
    _ACTION_PROMPT_ALLOWED_PATHS,
    _build_trigger_request,
    _count_action_tokens,
    _resolve_agent_by_id,
)
from core.agent_loop.meeting_orchestrator import maybe_start_meeting_kickoff_round
from core.default_prompts import render_default_prompt
from core.models import Agent, AgentState
from core.world.tilemap import get_room_at
import db


async def _handle_remote_meeting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a remote meeting from the agent's current location."""
    target = None
    agent_ids = []
    if isinstance(action.get("agentIds"), list):
        agent_ids = [item.strip() for item in action.get("agentIds") if isinstance(item, str) and item.strip()]
    if len(agent_ids) == 1:
        target = _resolve_agent_by_id(agent_ids[0])
    topic = action.get("topic", "")

    if target is None:
        return {"event": "status_changed", "detail": "No valid meeting participant specified", "agent_name": agent.name}

    room = get_room_at(state.x, state.y)
    if not room or room["room_type"] != "workspace":
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": render_default_prompt(
                "internal_action_requires_workspace",
                {"room_name": room_name},
                allowed_paths=_ACTION_PROMPT_ALLOWED_PATHS,
            ),
            "agent_name": agent.name,
            "feedback_code": "walk_to_desk_first",
        }

    # Send a meeting request message
    meeting_content = f"Remote meeting requested: {topic}" if topic else "Remote meeting requested"
    msg = db.create_message(
        from_agent=agent.id,
        to_agent=target.id,
        content=meeting_content,
        message_type="meeting",
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, meeting_content),
    )

    active = activity_runtime.get_active_activity(agent.id)
    if active and active.kind == "meeting":
        current_detail = str(active.detail or "").strip()
        next_detail = current_detail or meeting_content
        db.update_activity(
            active.id,
            title=topic or active.title,
            detail=next_detail,
            metadata={**active.metadata, "topic": topic, "meeting_mode": "remote"}
            if topic
            else {**active.metadata, "meeting_mode": "remote"},
        )
    else:
        parent = activity_runtime.get_active_activity(agent.id)
        if parent and parent.kind in {"assignment", "break", "conversation", "social", "work"}:
            db.update_activity(parent.id, status="paused")
        activity_runtime.start_meeting_activity(
            agent.id,
            title=topic or "Remote meeting",
            detail=meeting_content,
            parent_activity_id=parent.id if parent else None,
            metadata={"topic": topic, "meeting_mode": "remote"} if topic else {"meeting_mode": "remote"},
        )

    return {
        "event": "meeting_started",
        "detail": f"{agent.name} started remote meeting with {target.name}" + (f": {topic}" if topic else ""),
        "agent_name": agent.name,
        "trigger_requests": [_build_trigger_request(
            agent_id=target.id,
            trigger_type="peer_message",
            source_channel="work",
            payload={
                "content": meeting_content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": "meeting",
                "source_message_id": msg.id,
            },
        )],
    }


async def _handle_attend_meeting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attend an in-person meeting from the meeting room."""
    topic = (action.get("topic") or "").strip()
    agent_ids: list[str] = []
    room = get_room_at(state.x, state.y)

    if not room or room["room_type"] != "meeting":
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": f"You're in the {room_name}. Walk to the meetingRoom first.",
            "agent_name": agent.name,
        }

    if isinstance(action.get("agentIds"), list):
        agent_ids = [item.strip() for item in action.get("agentIds") if isinstance(item, str) and item.strip()]

    if not agent_ids:
        active = activity_runtime.get_active_activity(agent.id)
        existing_session_id = None
        if active and active.kind == "meeting":
            existing_session_id = str((active.metadata or {}).get("session_id") or "").strip() or None

        if existing_session_id:
            # Joining an already-orchestrated meeting (invited participants may arrive before others).
            pass
        else:
            existing_session = db.get_active_meeting_session_by_room(room["id"])
            if existing_session:
                meta = db.get_meeting_session_meta(existing_session.id)
                if meta is not None:
                    # Joining an orchestrated meeting session even if you're early/alone.
                    pass
                else:
                    other_participants = [
                        participant
                        for participant in db.list_active_meeting_participants(room["id"])
                        if str(participant.get("id") or "").strip() and str(participant.get("id")) != agent.id
                    ]
                    if not other_participants:
                        return {
                            "event": "world_feedback",
                            "detail": (
                                "No one else is currently in the Meeting Room. "
                                'If you were asked to meet with someone, invite them by re-running `mtg` with `data.mode="room"` '
                                "and the teammate's `data.aids` (list), or send them a `socialmsg` asking them to join the Meeting Room. "
                                "If you don't actually need a meeting right now, end the meeting commitment with `idle`."
                            ),
                            "agent_name": agent.name,
                            "feedback_code": "meeting_requires_participant",
                        }
            else:
                other_participants = [
                    participant
                    for participant in db.list_active_meeting_participants(room["id"])
                    if str(participant.get("id") or "").strip() and str(participant.get("id")) != agent.id
                ]
                if not other_participants:
                    return {
                        "event": "world_feedback",
                        "detail": (
                            "No one else is currently in the Meeting Room. "
                            'If you were asked to meet with someone, invite them by re-running `mtg` with `data.mode="room"` '
                            "and the teammate's `data.aids` (list), or send them a `socialmsg` asking them to join the Meeting Room. "
                            "If you don't actually need a meeting right now, end the meeting commitment with `idle`."
                        ),
                        "agent_name": agent.name,
                        "feedback_code": "meeting_requires_participant",
                    }

    meeting_content = f"In-person meeting in Meeting Room: {topic}" if topic else "In-person meeting in Meeting Room"
    msg = db.create_message(
        from_agent=agent.id,
        to_agent=None,
        content=meeting_content,
        message_type="meeting",
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, meeting_content),
    )

    detail = f"{agent.name} joined an in-person meeting"
    if topic:
        detail += f": {topic}"

    result = {
        "event": "meeting_started",
        "detail": detail,
        "agent_name": agent.name,
    }
    session_title = topic or "In-person meeting"
    active = activity_runtime.get_active_activity(agent.id)
    session = None
    if active and active.kind == "meeting":
        session_id_hint = str((active.metadata or {}).get("session_id") or "").strip()
        if session_id_hint:
            session = db.get_meeting_session(session_id_hint)
    if session is None:
        session = db.ensure_room_meeting_session(
            room["id"],
            title=session_title,
            created_by_agent_id=agent.id,
        )

    if active and active.kind == "meeting":
        metadata = {**active.metadata, "session_id": session.id}
        current_detail = str(active.detail or "").strip()
        next_detail = current_detail or meeting_content
        db.update_activity(
            active.id,
            title=topic or active.title,
            detail=next_detail,
            metadata={**metadata, "topic": topic} if topic else metadata,
        )
        current_meeting = db.get_activity(active.id) or active
    else:
        parent = activity_runtime.get_active_activity(agent.id)
        if parent and parent.kind in {"assignment", "break", "conversation", "social", "work"}:
            db.update_activity(parent.id, status="paused")
        current_meeting = activity_runtime.start_meeting_activity(
            agent.id,
            title=session_title,
            detail=meeting_content,
            parent_activity_id=parent.id if parent else None,
            metadata={"session_id": session.id, "topic": topic} if topic else {"session_id": session.id},
        )

    current_metadata = current_meeting.metadata or {}
    if not current_metadata.get("session_join_announced"):
        session_message = db.create_meeting_session_message(
            session_id=session.id,
            author_type="system",
            author_name="BossMod",
            content=f"{agent.name} joined the meeting.",
            source_channel="meeting",
        )
        db.update_activity(
            current_meeting.id,
            metadata={**current_metadata, "session_id": session.id, "session_join_announced": True},
        )
        result["meeting_message"] = {
            "agent_id": agent.id,
            "session_id": session.id,
            "content": session_message.content,
            "author_type": session_message.author_type,
            "author_name": session_message.author_name,
            "message_id": session_message.id,
            "created_at": session_message.created_at,
        }
    if agent_ids:
        # Orchestrated meeting: create a durable context packet + invite triggers.
        now = datetime.now(timezone.utc)
        meta = db.get_meeting_session_meta(session.id)
        if meta is None:
            context_summary = topic or (active.title if active else "") or "Meeting"
            context_payload = {
                "topic": topic,
                "purpose": (active.detail if active else "") or meeting_content,
                "host_agent_id": agent.id,
                "host_name": agent.name,
                "room_id": room["id"],
                "meeting_mode": "room",
                "created_at": now.isoformat(),
            }
            packet = db.create_meeting_context_packet(
                session_id=session.id,
                summary=context_summary,
                payload=context_payload,
            )
            db.upsert_meeting_session_meta(
                session_id=session.id,
                host_agent_id=agent.id,
                meeting_mode="room",
                phase="assembling",
                context_packet_id=str(packet.get("id")),
            )
            db.upsert_meeting_session_participant(session_id=session.id, agent_id=agent.id, state="arrived", required=True)
            for invited_id in sorted({*agent_ids}):
                if invited_id == agent.id:
                    continue
                db.upsert_meeting_session_participant(
                    session_id=session.id,
                    agent_id=invited_id,
                    state="invited",
                    required=True,
                )
            roster = db.list_meeting_participant_details(session.id)
            roster_names = ", ".join([str(item.get("name") or "") for item in roster if item.get("name")]) or "unknown"
            db.create_meeting_session_message(
                session_id=session.id,
                author_type="system",
                author_name="BossMod",
                content=(
                    f"MEETING PRE-READ\n"
                    f"topic: {context_summary}\n"
                    f"host: {agent.name}\n"
                    f"required participants: {roster_names}\n"
                    f"notes: arrive in the Meeting Room; accept/decline invite with a reason.\n"
                ),
                source_channel="meeting",
            )
            invite_content = (
                f'Meeting invite from {agent.name}: "{context_summary}". '
                f"Please accept or decline with a reason. If you accept, walk to the Meeting Room and join."
            )
            result["trigger_requests"] = [
                _build_trigger_request(
                    agent_id=invited_id,
                    trigger_type="meeting_invite",
                    source_channel="meeting",
                    payload={
                        "content": invite_content,
                        "from_agent": agent.id,
                        "from_name": agent.name,
                        "session_id": session.id,
                        "meeting_title": session_title,
                        "meeting_mode": "room",
                        "room_id": room["id"],
                        "context_summary": context_summary,
                        "context_packet_id": str(packet.get("id")),
                    },
                )
                for invited_id in sorted({*agent_ids})
                if invited_id != agent.id
            ]
        else:
            result["detail"] = f"{agent.name} is waiting for invited participants to arrive"

    # If this is an orchestrated meeting session, update arrival state and kick off the first structured round
    # once everyone is accounted for.
    meta = db.get_meeting_session_meta(session.id)
    if meta is not None:
        now = datetime.now(timezone.utc)
        participant = db.get_meeting_session_participant(session.id, agent.id)
        if participant is not None and participant.get("state") != "arrived":
            db.update_meeting_session_participant_state(
                session_id=session.id,
                agent_id=agent.id,
                state="arrived",
                arrived_at=now,
            )
            db.create_meeting_session_message(
                session_id=session.id,
                author_type="system",
                author_name="BossMod",
                content=f"{agent.name} arrived in the Meeting Room.",
                source_channel="meeting",
            )

        kickoff_requests = maybe_start_meeting_kickoff_round(session_id=session.id)
        for req in kickoff_requests:
            result.setdefault("trigger_requests", []).append(req)
    return result
