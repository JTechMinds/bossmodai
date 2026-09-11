"""Discard stale thread replies at deliver when a newer human tip arrived.

Does not lock the composer. Queued older-round candidates are cancelled on
the new human message; in-flight turns skip at persist and wake once on the
latest tip. Several agents skipping the same tip share one system line.
"""

from __future__ import annotations

from typing import Any

import db
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.models import Agent
from core.models.channel import (
    ChannelArchivedError,
    THREAD_STALE_SKIP_KIND,
    THREAD_STALE_SKIP_LINE,
)

_CHANNEL_TURN_TYPES = frozenset({"channel_message", "channel_response"})
_QUEUED_CANDIDATE_STATUSES = frozenset({"pending", "queued"})


def cancel_queued_older_thread_rounds(
    *,
    channel_id: str,
    keep_source_message_id: str,
) -> int:
    """Cancel not-yet-started candidates and triggers for older rounds.

    In-flight ``responding`` / claimed turns stay so deliver can skip them.
    """
    channel_token = (channel_id or "").strip()
    keep_tip = (keep_source_message_id or "").strip()
    if not channel_token or not keep_tip:
        return 0
    cancelled = 0
    for round_record in db.list_channel_response_rounds(channel_token, status="active"):
        if str(round_record.source_message_id or "").strip() == keep_tip:
            continue
        cancelled += _cancel_queued_round(round_record.id)
    return cancelled


def supersede_stale_thread_turn(
    agent: Agent,
    trigger: dict[str, Any],
    *,
    will_post: bool = True,
) -> dict[str, Any] | None:
    """If this wake's tip is no longer current, discard the reply.

    Returns a turn result when the post must not land; ``None`` when deliver
    may continue. ``will_post`` is false for observe so a silent read still
    closes the old candidate without a skip line.
    """
    if str(trigger.get("type") or "") not in _CHANNEL_TURN_TYPES:
        return None
    channel_id = str(trigger.get("channel_id") or "").strip()
    wake_tip = str(trigger.get("source_message_id") or "").strip()
    if not channel_id or not wake_tip:
        return None
    latest = db.get_later_human_channel_message(channel_id, wake_tip)
    if latest is None:
        return None

    round_id = str(trigger.get("round_id") or "").strip()
    if round_id:
        _finish_superseded_candidate(round_id=round_id, agent_id=agent.id)
        _cancel_queued_round(round_id)

    result: dict[str, Any] = {
        "event": "decision_applied",
        "detail": f"{agent.name} skipped — newer message in thread",
        "agent_name": agent.name,
        "trigger_requests": _ensure_wake_on_latest_tip(
            channel_id=channel_id,
            latest=latest,
            channel_name=str(trigger.get("channel_name") or "").strip() or None,
        ),
    }
    if will_post:
        skip_line = _post_skip_line_once(channel_id=channel_id, tip=latest)
        if skip_line:
            result["channel_message"] = skip_line
    return result


def _cancel_queued_round(round_id: str) -> int:
    """Mark not-yet-started candidates observed and drop their queued triggers.

    A claimed trigger means the turn already started; leave that candidate
    for the deliver-time skip.
    """
    cancelled = 0
    in_flight = db.list_claimed_agent_ids_for_round(round_id)
    for candidate in db.list_channel_response_candidates(round_id):
        status = str(candidate.status or "")
        if status not in _QUEUED_CANDIDATE_STATUSES:
            continue
        if candidate.agent_id in in_flight:
            continue
        db.mark_channel_candidate_observed(round_id=round_id, agent_id=candidate.agent_id)
        cancelled += 1
    db.delete_queued_triggers_for_round(round_id)
    db.maybe_complete_channel_response_round(round_id)
    return cancelled


def _finish_superseded_candidate(*, round_id: str, agent_id: str) -> None:
    """Close this agent's old-round row so the skip is not a posted reply."""
    candidate = db.get_channel_response_candidate(round_id=round_id, agent_id=agent_id)
    if candidate is None:
        return
    status = str(candidate.status or "")
    if status in {"responded", "observed"}:
        db.maybe_complete_channel_response_round(round_id)
        return
    db.mark_channel_candidate_observed(round_id=round_id, agent_id=agent_id)
    db.maybe_complete_channel_response_round(round_id)


def _post_skip_line_once(*, channel_id: str, tip: Any) -> dict[str, Any] | None:
    """Post the skip line once per newer tip. Later skippers reuse it."""
    existing = db.find_thread_skip_line_after(
        channel_id=channel_id,
        after_message_id=tip.id,
    )
    if existing is not None:
        return None
    if db.is_channel_archived(channel_id):
        return None
    try:
        message = db.create_channel_message(
            channel_id=channel_id,
            author_type="system",
            author_name="BossMod",
            content=THREAD_STALE_SKIP_LINE,
            source_channel="channel",
            notification_kind=THREAD_STALE_SKIP_KIND,
        )
    except ChannelArchivedError:
        return None
    return {
        "channel_id": channel_id,
        "content": message.content,
        "author_type": message.author_type,
        "author_name": message.author_name or "BossMod",
        "message_id": message.id,
        "created_at": message.created_at,
        "notification_kind": message.notification_kind,
    }


def _ensure_wake_on_latest_tip(
    *,
    channel_id: str,
    latest: Any,
    channel_name: str | None,
) -> list[dict[str, Any]]:
    """Open one fresh round on the latest human tip when none exists yet."""
    existing = db.get_channel_response_round_for_source(
        channel_id=channel_id,
        source_message_id=latest.id,
    )
    if existing is not None:
        return []
    return start_channel_peer_round(
        channel_id=channel_id,
        message_id=latest.id,
        content=latest.content,
        from_name=latest.author_name,
        author_type=latest.author_type,
        channel_name=channel_name,
    )
