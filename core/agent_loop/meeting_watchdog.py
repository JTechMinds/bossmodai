"""BossMod AI — Meeting assembly watchdog (invites + timeouts)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from core import config
from core.agent_loop.dispatcher import dispatcher
from core.agent_loop.meeting_orchestrator import maybe_start_meeting_kickoff_round
from core.time import ensure_utc
import db

logger = logging.getLogger(__name__)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


class MeetingWatchdog:
    """Monitors assembling meetings and marks missing participants as timed out."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Meeting watchdog started")

    async def stop(self) -> None:
        self._running = False
        loop_task = self._task
        self._task = None
        if loop_task:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task
        logger.info("Meeting watchdog stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check_meetings()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Meeting watchdog loop error")
            interval = config.get_float("meeting_watchdog_check_interval_seconds") or 5.0
            await asyncio.sleep(interval)

    async def _check_meetings(self) -> None:
        now = datetime.now(timezone.utc)
        accept_timeout = config.get_int("meeting_invite_accept_timeout_seconds") or 90
        arrival_timeout = config.get_int("meeting_invite_arrival_timeout_seconds") or 180

        sessions = db.query(
            """
            SELECT
                m.session_id,
                m.host_agent_id,
                m.phase,
                s.title,
                s.room_id
            FROM meeting_session_meta m
            JOIN meeting_sessions s ON s.id = m.session_id
            WHERE s.status = 'active' AND m.phase = 'assembling'
            ORDER BY s.created_at ASC
            """
        )
        for row in sessions:
            session_id = str(row.get("session_id") or "").strip()
            if not session_id:
                continue
            host_id = str(row.get("host_agent_id") or "").strip()
            title = str(row.get("title") or "Meeting")

            participants = db.list_meeting_participant_details(session_id)
            if not participants:
                continue

            updates: list[str] = []
            for participant in participants:
                agent_id = str(participant.get("agent_id") or "").strip()
                name = str(participant.get("name") or "Unknown")
                state = str(participant.get("state") or "")
                if not agent_id or state == "arrived":
                    continue
                invited_at = _as_datetime(participant.get("invited_at"))
                responded_at = _as_datetime(participant.get("responded_at"))
                arrived_at = _as_datetime(participant.get("arrived_at"))

                if state == "invited":
                    if invited_at and now - invited_at >= timedelta(seconds=accept_timeout):
                        reason = f"No response within {accept_timeout}s"
                        db.update_meeting_session_participant_state(
                            session_id=session_id,
                            agent_id=agent_id,
                            state="timed_out",
                            reason=reason,
                            responded_at=now,
                        )
                        db.create_meeting_session_message(
                            session_id=session_id,
                            author_type="system",
                            author_name="BossMod",
                            content=f"{name} timed out (no response).",
                            source_channel="meeting",
                        )
                        updates.append(f"{name} timed out (no response).")
                elif state in {"accepted", "in_transit"}:
                    if arrived_at is None and responded_at and now - responded_at >= timedelta(seconds=arrival_timeout):
                        reason = f"Did not arrive within {arrival_timeout}s"
                        db.update_meeting_session_participant_state(
                            session_id=session_id,
                            agent_id=agent_id,
                            state="timed_out",
                            reason=reason,
                            responded_at=responded_at,
                        )
                        db.create_meeting_session_message(
                            session_id=session_id,
                            author_type="system",
                            author_name="BossMod",
                            content=f"{name} timed out (did not arrive).",
                            source_channel="meeting",
                        )
                        updates.append(f"{name} timed out (did not arrive).")

            if updates and host_id:
                dispatcher.enqueue_trigger(
                    agent_id=host_id,
                    trigger_type="activity_resumed",
                    source_channel="meeting",
                    payload={
                        "content": f'Meeting "{title}" assembly update: ' + " ".join(updates),
                        "activity_kind": "meeting",
                        "activity_title": title,
                        "session_id": session_id,
                    },
                )

            kickoff_requests = maybe_start_meeting_kickoff_round(session_id=session_id)
            for req in kickoff_requests:
                dispatcher.enqueue_trigger(
                    agent_id=req["agent_id"],
                    trigger_type=req["trigger_type"],
                    source_channel=req["source_channel"],
                    payload=req["payload"],
                )


meeting_watchdog = MeetingWatchdog()

