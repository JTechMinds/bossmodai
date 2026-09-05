"""BossMod AI — Meeting orchestration utilities (kickoff + readiness checks)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db


def maybe_start_meeting_kickoff_round(*, session_id: str) -> list[dict[str, Any]]:
    """Start the first structured meeting round when the session is ready.

    Returns trigger requests to enqueue (typically one initial `session_response`).
    """
    meta = db.get_meeting_session_meta(session_id)
    if meta is None:
        return []
    if str(meta.get("phase") or "") != "assembling":
        return []
    if str(meta.get("kickoff_round_id") or "").strip():
        return []
    if not db.meeting_all_required_accounted_for(session_id):
        return []

    roster = db.list_meeting_participant_details(session_id)
    arrived = [item for item in roster if str(item.get("state") or "") == "arrived"]
    if len(arrived) < 2:
        return []

    host_id = str(meta.get("host_agent_id") or "").strip()
    if not host_id or not any(item.get("agent_id") == host_id for item in arrived):
        return []

    packet_summary = ""
    packet_id = str(meta.get("context_packet_id") or "").strip()
    if packet_id:
        packet = db.get_meeting_context_packet(packet_id)
        if packet is not None:
            packet_summary = str(packet.get("summary") or "").strip()

    session = db.get_meeting_session(session_id)
    title = session.title if session else "Meeting"
    kickoff_text = (
        "MEETING START\n"
        f"topic: {packet_summary or title}\n"
        "Each participant: reply with (1) your status, (2) blockers, (3) what you need from others.\n"
        "Host: after everyone replies, summarize decisions + action items and assign tasks.\n"
    )

    kickoff_message = db.create_meeting_session_message(
        session_id=session_id,
        author_type="system",
        author_name="BossMod",
        content=kickoff_text,
        source_channel="meeting",
    )
    round_row = db.create_meeting_response_round(session_id=session_id, source_message_id=kickoff_message.id)

    ordered_ids: list[str] = [host_id]
    ordered_ids.extend(
        [
            str(item.get("agent_id"))
            for item in sorted(arrived, key=lambda item: str(item.get("name") or ""))
            if str(item.get("agent_id") or "") and str(item.get("agent_id")) != host_id
        ]
    )
    for index, agent_id in enumerate(ordered_ids, start=1):
        db.create_meeting_response_candidate(round_id=round_row.id, agent_id=agent_id)
        db.update_meeting_response_candidate(
            round_id=round_row.id,
            agent_id=agent_id,
            status="queued",
            queue_position=index,
        )

    next_candidate = db.activate_next_response_candidate(round_row.id)
    db.update_meeting_session_meta(session_id, phase="active", kickoff_round_id=round_row.id)
    if next_candidate is None:
        return []
    return [
        {
            "agent_id": next_candidate.agent_id,
            "trigger_type": "session_response",
            "source_channel": "chat",
            "payload": {
                "content": kickoff_text,
                "session_id": session_id,
                "round_id": round_row.id,
                "from_name": "BossMod",
                "author_type": "system",
                "source_message_id": kickoff_message.id,
                "meeting_title": title,
            },
        }
    ]

