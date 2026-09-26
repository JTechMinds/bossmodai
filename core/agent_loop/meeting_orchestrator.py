"""BossMod AI — Meeting orchestration utilities (kickoff, readiness checks, ending)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import ensure_live_work_continuation, persist_result_triggers
from core.runtime.services import request_dispatcher_wake


@dataclass(frozen=True)
class EndedMeeting:
    """One meeting ``_end_meeting`` ended, with what the UI needs to paint it.

    Attributes:
        session_id: The ended meeting session.
        message: The posted ``Meeting ended: …`` transcript line, keyed like
            ``_handle_attend_meeting``'s ``result["meeting_message"]`` minus
            ``agent_id`` (``session_id``, ``content``, ``author_type``,
            ``author_name``, ``message_id``, ``created_at``), so it can be
            passed as ``broadcast_meeting_message(agent_id=…, **message)``.
        released_agent_ids: Every participant of the session except the
            deleted host: everyone who may have the meeting open and must see
            the line, not only those whose meeting activity was just ended.
    """

    session_id: str
    message: dict[str, Any]
    released_agent_ids: tuple[str, ...]


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



def end_meetings_hosted_by(agent_id: str, *, reason: str) -> list[EndedMeeting]:
    """End every unfinished meeting one agent hosts, because the host is going away.

    A meeting without its host never kicks off (``maybe_start_meeting_kickoff_round``
    needs the host present), so deleting a host ends its meetings instead of
    leaving them to wait forever. Called by ``AgentRepository.delete`` while
    the host's row still exists.

    A meeting is unfinished while its session is ``active`` and its meta
    phase is ``assembling`` or ``active``; ended or canceled meetings are left
    untouched. Each one is ended by ``_end_meeting``.

    Args:
        agent_id: The host.
        reason: Why it ended, completing ``Meeting ended: {reason}.`` in the
            transcript, e.g. ``host Ada was deleted``.

    Returns:
        One ``EndedMeeting`` per ended meeting, oldest first, for the caller
        to paint the ``Meeting ended`` line to its participants.
    """
    return [
        _end_meeting(str(row["session_id"]), phase=str(row["phase"]), reason=reason, host_agent_id=agent_id)
        for row in db.list_unfinished_meetings_hosted_by(agent_id)
    ]


def end_meetings_without_host(*, reason: str) -> list[EndedMeeting]:
    """End every unfinished meeting whose host no longer exists. Idempotent.

    The orphan cleanup's counterpart of ``end_meetings_hosted_by``: the host
    is NULL (detached by a delete that predates ending hosted meetings) or an
    id no agent has any more. Each meeting is ended by ``_end_meeting``.

    Args:
        reason: Completes ``Meeting ended: {reason}.`` in the transcript.

    Returns:
        One ``EndedMeeting`` per ended meeting, oldest first. Empty on a
        second run.
    """
    return [
        _end_meeting(str(row["session_id"]), phase=str(row["phase"]), reason=reason, host_agent_id=None)
        for row in db.list_unfinished_meetings_without_host()
    ]


def _end_meeting(session_id: str, *, phase: str, reason: str, host_agent_id: str | None) -> EndedMeeting:
    """End one unfinished meeting and release everyone still in it.

    An ``assembling`` meeting never started, so its phase becomes
    ``canceled``; an ``active`` one becomes ``ended``. The session is ended,
    one system line is posted, every open response round is completed and
    its queued turns dropped, every other queued trigger bound to the
    session (invites, resumes) is dropped, and every other agent whose
    active activity is this meeting leaves it the way ``idle`` does:
    ``complete_activity`` resumes the paused parent. The dispatcher only
    runs its continuation check after a turn, and no turn of theirs ends
    here, so the same check (``ensure_live_work_continuation``) is run and
    persisted here to give resumed live work its next turn, and the worker
    is woken once so that turn does not wait for its poll.

    An agent still walking to the meeting is not released here (its active
    activity is the walk); ``activity_runtime.resolve_arrival`` completes
    the finished meeting when it arrives.

    Returns:
        The ``EndedMeeting``, with the posted line and every participant
        except ``host_agent_id``.

    Raises:
        ValueError: ``phase`` is not ``assembling`` or ``active``.
    """
    if phase not in {"assembling", "active"}:
        raise ValueError(f"Meeting {session_id} is {phase!r}, not an unfinished meeting")
    db.update_meeting_session_meta(session_id, phase="canceled" if phase == "assembling" else "ended")
    db.end_meeting_session(session_id)
    ended_line = db.create_meeting_session_message(
        session_id=session_id,
        author_type="system",
        author_name="BossMod",
        content=f"Meeting ended: {reason}.",
        source_channel="meeting",
    )
    for round_row in db.list_meeting_response_rounds(session_id, status="active"):
        db.complete_meeting_response_round(round_row.id)
        db.delete_queued_triggers_for_round(round_row.id)
    db.delete_queued_triggers_for_session(session_id)

    resumed_any = False
    for agent in db.list_agents():
        if agent.id == host_agent_id:
            continue
        active = activity_runtime.get_active_activity(agent.id)
        if active is None or active.kind != "meeting":
            continue
        if str((active.metadata or {}).get("session_id") or "").strip() != session_id:
            continue
        activity_runtime.complete_activity(active.id, detail=f"Meeting ended: {reason}.")
        resume = ensure_live_work_continuation(agent.id)
        if resume is not None and persist_result_triggers({"trigger_requests": [resume]}):
            resumed_any = True
    if resumed_any:
        request_dispatcher_wake()

    return EndedMeeting(
        session_id=session_id,
        message={
            "session_id": session_id,
            "content": ended_line.content,
            "author_type": ended_line.author_type,
            "author_name": ended_line.author_name,
            "message_id": ended_line.id,
            "created_at": ended_line.created_at,
        },
        released_agent_ids=tuple(
            str(item["agent_id"])
            for item in db.list_meeting_participant_details(session_id)
            if str(item["agent_id"]) != host_agent_id
        ),
    )
