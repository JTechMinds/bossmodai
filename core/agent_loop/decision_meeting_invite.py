"""Record an agent's answer to a meeting invite on the meeting session.

Split out of decision_runtime along the seam between deciding and recording:
``apply_decision`` decides which commitment a decision makes, and this module
owns the meeting-session bookkeeping a ``meeting_invite`` trigger needs once
the answer is known — the participant's state, the system note in the
meeting transcript, and, on a decline, waking the host so the meeting does
not wait on someone who is not coming.

Both helpers are no-ops for any trigger that is not a meeting invite with a
session id, so the caller does not repeat that check.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from core.agent_loop.decision_contract import ConversationDecision
from core.models import Agent


def _invite_session_id(trigger: dict[str, Any]) -> str | None:
    """The meeting session a ``meeting_invite`` trigger is for, or None."""
    if trigger.get("type") != "meeting_invite":
        return None
    session_id = str(trigger.get("session_id") or "").strip()
    return session_id or None


def _record_meeting_invite_decline(
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
    result: dict[str, Any],
) -> None:
    """Mark the agent declined on the invite's session and wake its host.

    Args:
        agent: The invitee answering.
        trigger: The turn's trigger; only a ``meeting_invite`` with a
            ``session_id`` is acted on.
        decision: The decline; its reply (or detail) is the reason recorded.
        result: The turn result. A host other than the invitee gets an
            ``activity_resumed`` request appended to ``trigger_requests``.
    """
    session_id = _invite_session_id(trigger)
    if session_id is None:
        return
    now = datetime.now(timezone.utc)
    reason = (decision.reply or decision.detail or "Declined.").strip()
    participant = db.get_meeting_session_participant(session_id, agent.id)
    if participant is None:
        db.upsert_meeting_session_participant(
            session_id=session_id,
            agent_id=agent.id,
            state="declined",
            required=True,
            reason=reason,
        )
    db.update_meeting_session_participant_state(
        session_id=session_id,
        agent_id=agent.id,
        state="declined",
        reason=reason,
        responded_at=now,
    )
    db.create_meeting_session_message(
        session_id=session_id,
        author_type="system",
        author_name="BossMod",
        content=f"{agent.name} declined the meeting invite: {reason}",
        source_channel="meeting",
    )
    meta = db.get_meeting_session_meta(session_id)
    host_id = str((meta or {}).get("host_agent_id") or "").strip()
    if host_id and host_id != agent.id:
        result["trigger_requests"].append(
            {
                "agent_id": host_id,
                "trigger_type": "activity_resumed",
                "source_channel": "meeting",
                "payload": {
                    "content": f'{agent.name} declined the meeting invite: {reason}',
                    "activity_kind": "meeting",
                    "activity_title": str(trigger.get("meeting_title") or "Meeting"),
                    "session_id": session_id,
                },
            }
        )


def _record_meeting_invite_accept(agent: Agent, trigger: dict[str, Any]) -> None:
    """Mark the agent accepted on the invite's session and note it there.

    Args:
        agent: The invitee answering.
        trigger: The turn's trigger; only a ``meeting_invite`` with a
            ``session_id`` is acted on.
    """
    session_id = _invite_session_id(trigger)
    if session_id is None:
        return
    now = datetime.now(timezone.utc)
    participant = db.get_meeting_session_participant(session_id, agent.id)
    if participant is None:
        db.upsert_meeting_session_participant(
            session_id=session_id,
            agent_id=agent.id,
            state="accepted",
            required=True,
        )
    db.update_meeting_session_participant_state(
        session_id=session_id,
        agent_id=agent.id,
        state="accepted",
        responded_at=now,
    )
    db.create_meeting_session_message(
        session_id=session_id,
        author_type="system",
        author_name="BossMod",
        content=f"{agent.name} accepted the meeting invite.",
        source_channel="meeting",
    )
