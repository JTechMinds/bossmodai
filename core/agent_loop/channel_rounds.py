"""BossMod AI — Shared channel response-round coordination.

Discuss and status messages wake one member at a time. When System AI is
set, one short route per round chooses an ordered speak list and a
stay-out list. Human @ mentions stay first and are never dropped. Stay-out
members are an engine pass: the queue advances, nothing is posted, and the
identity model is not called. A failed or unset route keeps the #124 drain
(mentioned first, then a stable lead / round-robin) and each of those wakes
is a normal soft-judge turn. Fan-out remains only for the narrow allowlist
in ``channel_round_plan``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import db
from core.agent_loop.channel_round_plan import (
    DISPATCH_FANOUT,
    DISPATCH_ROUNDS,
    channel_response_round_cap,
    classify_channel_dispatch,
    mention_ids_in_order,
    order_round_members,
)
from core.agent_loop.channel_host import (
    blocks_peer_round,
    demoted_ids,
    is_pause_phrase,
    note_human_snapshot,
    note_pass,
    pause_thread,
    record_channel_turn,
    shape_follow_up_speak,
    stop_active_talk_rounds,
    talk_closed,
)
from core.agent_loop.channel_router import RoundPlan, plan_channel_route
from core.agent_loop.response_rounds import (
    SharedRoundBinding,
    begin_shared_response,
    finalize_shared_response,
    observe_shared_message,
)
from core.models import Agent
from core.models.channel import ChannelArchivedError
from db import channel_response_rounds as channel_round_db

logger = logging.getLogger(__name__)

ROUND_MARKER_KIND = "channel_round_marker"

_CHANNEL_ROUNDS = SharedRoundBinding(
    parent_key="channel_id",
    label="channel",
    queue_fail_label="shared channel reply queue",
    trigger_type="channel_response",
    source_channel="channel",
    extra_payload_key="channel_name",
    mark_observed=db.mark_channel_candidate_observed,
    mark_responded=db.mark_channel_candidate_responded,
    reserve=db.reserve_channel_response_slot,
    activate_next=db.activate_next_channel_response_candidate,
    maybe_complete=db.maybe_complete_channel_response_round,
)


def start_channel_peer_round(
    *,
    channel_id: str,
    message_id: str,
    content: str,
    from_name: str,
    author_type: str,
    exclude_agent_ids: set[str] | frozenset[str] | None = None,
    from_agent: str | None = None,
    channel_name: str | None = None,
    handoff: bool = False,
    required_ids: list[str] | None = None,
    board_owner_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Open a new channel response round so peers can react to one message.

    Discuss and status wake the first member only; later members wake after
    that turn drains. Fan-out still wakes every included member at once.
    Agent deliverable posts exclude the author. In-round replies must not
    call this — finishing the turn already advances the queue.
    """
    excluded = {
        item.strip()
        for item in (exclude_agent_ids or set())
        if isinstance(item, str) and item.strip()
    }
    channel = db.get_channel(channel_id)
    if channel is None or channel.status != "active":
        return []

    resolved_name = (channel_name or "").strip() or channel.name

    if author_type == "human" and is_pause_phrase(content):
        pause_thread(channel_id)
        return []
    if blocks_peer_round(channel_id, author_type=author_type):
        return []

    members = _ordered_members(channel_id, excluded)
    if author_type == "human":
        note_human_snapshot(channel_id, mention_ids_in_order(content, members))
    if not members:
        return []
    mode = classify_channel_dispatch(content, members)
    # Operator @ is an override pin. Agent @ in prose is not. Structured
    # next_owners and Board next-card owners are hard pins of the same kind.
    operator_pins = mention_ids_in_order(content, members) if author_type == "human" else []
    member_ids = {member["id"] for member in members}
    board_ids = [agent_id for agent_id in (board_owner_ids or []) if agent_id in member_ids]
    pins = _merge_ids(operator_pins, list(required_ids or []), board_ids, allowed=member_ids)
    lead_id = _lead_id(channel, [member["id"] for member in members])
    ordered_ids = order_round_members(
        [member["id"] for member in members],
        lead_id=lead_id,
        mentioned_ids=pins,
        round_index=1,
    )
    if not ordered_ids:
        return []
    plan = _plan_for_members(
        mode=mode,
        members=members,
        fallback_order=ordered_ids,
        latest_message=content,
        required_ids=pins,
        handoff=handoff,
        sticky_note=_board_sticky(members, board_ids),
    )

    round_record = db.create_channel_response_round(
        channel_id=channel_id,
        source_message_id=message_id,
    )
    channel_round_db.set_channel_round_meta(
        round_record.id,
        round_index=1,
        dispatch_mode=mode,
        stepped_out=list(plan.stay_out),
        next_mentions=[],
        router_mode=plan.mode if mode == DISPATCH_ROUNDS else "fallback",
        pinned_ids=list(plan.pinned) if mode == DISPATCH_ROUNDS else [],
    )
    wake_ids = _install_round_queue(
        round_id=round_record.id,
        channel_id=channel_id,
        speak_ids=plan.speak,
        stay_out_ids=plan.stay_out if mode == DISPATCH_ROUNDS else [],
        wake_all=mode == DISPATCH_FANOUT,
    )

    payload: dict[str, Any] = {
        "content": content,
        "channel_id": channel_id,
        "round_id": round_record.id,
        "from_name": from_name,
        "author_type": author_type,
        "source_message_id": message_id,
        "channel_name": resolved_name,
        "dispatch_mode": mode,
        "round_index": 1,
    }
    if isinstance(from_agent, str) and from_agent.strip():
        payload["from_agent"] = from_agent

    if mode == DISPATCH_ROUNDS and not wake_ids:
        db.maybe_complete_channel_response_round(round_record.id)
    triggers: list[dict[str, Any]] = []
    for agent_id in wake_ids:
        triggers.append(
            {
                "agent_id": agent_id,
                "trigger_type": "channel_message",
                "source_channel": "channel",
                "payload": dict(payload),
            }
        )
    return triggers


def post_agent_channel_share(
    *,
    channel_id: str,
    agent: Agent,
    content: str,
    source_channel: str = "channel",
    handoff: bool = False,
    required_ids: list[str] | None = None,
    board_owner_ids: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Persist an agent-authored channel share and open a peer response round.

    System completion cards must not call this — they stay transcript-only.
    In-round ``channel_response`` replies must not call this either.
    """
    if db.is_channel_archived(channel_id):
        return {}, []
    try:
        message = db.create_channel_message(
            channel_id=channel_id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            content=content,
            source_channel=source_channel,
        )
    except ChannelArchivedError:
        return {}, []
    channel_message = {
        "channel_id": channel_id,
        "content": message.content,
        "author_type": message.author_type,
        "author_name": agent.name,
        "author_agent_id": agent.id,
        "message_id": message.id,
        "created_at": message.created_at,
    }
    return channel_message, start_channel_peer_round(
        channel_id=channel_id,
        message_id=message.id,
        content=message.content,
        from_name=agent.name,
        author_type="agent",
        exclude_agent_ids={agent.id},
        from_agent=agent.id,
        handoff=handoff,
        required_ids=required_ids,
        board_owner_ids=board_owner_ids,
    )


def observe_channel_message(
    agent: Agent,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Mark one shared-channel message as observed without replying."""
    result = observe_shared_message(agent, trigger, _CHANNEL_ROUNDS)
    if _dispatch_mode(trigger) == DISPATCH_ROUNDS:
        progress = advance_channel_round(trigger, spoke=False, speaker_id=agent.id)
        result["trigger_requests"] = progress["trigger_requests"]
        if progress.get("round_marker"):
            result["round_marker"] = progress["round_marker"]
    return result


def begin_channel_response(
    agent: Agent,
    trigger: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Queue one shared-channel responder and report whether they can speak now."""
    return begin_shared_response(agent, trigger, _CHANNEL_ROUNDS)


def finalize_channel_response(
    *,
    agent_id: str,
    trigger: dict[str, Any],
    responded: bool,
) -> list[dict[str, Any]]:
    """Advance the shared channel reply queue after one responder finishes."""
    queued = finalize_shared_response(
        agent_id=agent_id,
        trigger=trigger,
        responded=responded,
        binding=_CHANNEL_ROUNDS,
    )
    if _dispatch_mode(trigger) != DISPATCH_ROUNDS:
        return queued
    progress = advance_channel_round(
        trigger,
        spoke=responded,
        speaker_id=agent_id,
        spoken_text=str(trigger.get("spoken_text") or ""),
    )
    if progress.get("round_marker"):
        trigger["round_marker"] = progress["round_marker"]
    return progress["trigger_requests"]


def advance_channel_round(
    trigger: dict[str, Any],
    *,
    spoke: bool,
    speaker_id: str,
    spoken_text: str = "",
) -> dict[str, Any]:
    """Move a rounds-mode snapshot to the next wake, or open the next round.

    A pass steps the speaker out for this human snapshot. A speak can
    re-enter them. Mid-round @mentions are stored for the next round and
    do not reorder the current queue. A newer human tip, a full step-out,
    or the soft cap stops the snapshot. Two live snapshots for one channel
    are not stacked.
    """
    empty: dict[str, Any] = {"trigger_requests": []}
    round_id = str(trigger.get("round_id") or "").strip()
    channel_id = str(trigger.get("channel_id") or "").strip()
    if not round_id or not channel_id:
        return empty
    if _dispatch_mode(trigger) != DISPATCH_ROUNDS:
        return empty

    meta = channel_round_db.get_channel_round_meta(round_id)
    stepped = list(meta["stepped_out"])
    mentions = list(meta["next_mentions"])
    speaker = (speaker_id or "").strip()
    if spoke and speaker:
        stepped = [agent_id for agent_id in stepped if agent_id != speaker]
        mentions = _append_mentions(mentions, channel_id, spoken_text or str(trigger.get("spoken_text") or ""))
    elif speaker and speaker not in stepped:
        stepped.append(speaker)
    channel_round_db.set_channel_round_meta(
        round_id,
        stepped_out=stepped,
        next_mentions=mentions,
    )

    source_id = str(trigger.get("source_message_id") or "").strip()
    if not _snapshot_is_current(channel_id, source_id):
        db.maybe_complete_channel_response_round(round_id)
        return empty
    if _other_snapshot_is_active(channel_id, round_id, source_id):
        db.maybe_complete_channel_response_round(round_id)
        return empty

    spoken = spoken_text or str(trigger.get("spoken_text") or "")
    dup_ack = record_channel_turn(
        channel_id,
        spoke=spoke,
        speaker_id=speaker,
        spoken_text=spoken,
    )
    if dup_ack or talk_closed(channel_id):
        stop_active_talk_rounds(channel_id)
        return empty

    candidates = db.list_channel_response_candidates(round_id)
    if any(str(candidate.status or "") in {"queued", "responding"} for candidate in candidates):
        return empty
    pending = _pending_candidates(candidates)
    if pending and spoke and str(meta.get("router_mode") or "") == "system":
        pending = _redecide_remaining(
            round_id=round_id,
            channel_id=channel_id,
            pending=pending,
            pinned_ids=list(meta.get("pinned_ids") or []),
            opening_message=str(trigger.get("content") or ""),
            latest_message=spoken,
        )
    if pending:
        nxt = pending[0]
        db.update_channel_response_candidate(
            round_id=round_id,
            agent_id=nxt.agent_id,
            status="queued",
            queue_position=nxt.queue_position,
        )
        return {"trigger_requests": [_wake_trigger(trigger, nxt.agent_id, round_id, meta)]}

    db.maybe_complete_channel_response_round(round_id)
    return _open_follow_up_round(
        trigger,
        round_id=round_id,
        channel_id=channel_id,
        source_id=source_id,
        meta=channel_round_db.get_channel_round_meta(round_id),
        participants=[candidate.agent_id for candidate in candidates],
    )


def _plan_for_members(
    *,
    mode: str,
    members: list[dict[str, str]],
    fallback_order: list[str],
    latest_message: str,
    required_ids: list[str],
    opening_message: str = "",
    handoff: bool = False,
    sticky_note: str = "",
) -> RoundPlan:
    """Route a rounds queue. Fan-out and an unset router keep the drain order."""
    if mode != DISPATCH_ROUNDS:
        return RoundPlan(
            speak=list(fallback_order),
            stay_out=[],
            mode="fallback",
            pinned=[],
        )
    return plan_channel_route(
        members=members,
        fallback_order=fallback_order,
        latest_message=latest_message,
        pending_mention_ids=list(required_ids),
        forced_ids=list(required_ids),
        opening_message=opening_message,
        handoff=handoff,
        sticky_note=sticky_note,
    )


def _install_round_queue(
    *,
    round_id: str,
    channel_id: str,
    speak_ids: list[str],
    stay_out_ids: list[str],
    wake_all: bool,
) -> list[str]:
    """Create the queue. Stay-out members are an engine pass and are not woken."""
    wakes: list[str] = []
    position = 1
    for agent_id in speak_ids:
        db.create_channel_response_candidate(round_id=round_id, agent_id=agent_id)
        status = "pending"
        if not wake_all and not wakes:
            status = "queued"
        db.update_channel_response_candidate(
            round_id=round_id,
            agent_id=agent_id,
            status=status,
            queue_position=position,
        )
        if wake_all or status == "queued":
            wakes.append(agent_id)
        position += 1
    for agent_id in stay_out_ids:
        db.create_channel_response_candidate(round_id=round_id, agent_id=agent_id)
        db.update_channel_response_candidate(
            round_id=round_id,
            agent_id=agent_id,
            status="pending",
            queue_position=position,
        )
        position += 1
        _engine_pass(agent_id, round_id=round_id, channel_id=channel_id)
    return wakes


def _engine_pass(agent_id: str, *, round_id: str, channel_id: str) -> None:
    """Advance one unselected member without an identity-model turn.

    Same record as a #124 pass: no channel line, no warm context, no model.
    """
    db.mark_channel_candidate_observed(round_id=round_id, agent_id=agent_id)
    note_pass(channel_id, agent_id)
    agent = db.get_agent(agent_id)
    if agent is None:
        return
    log_channel_pass(
        agent,
        {
            "type": "channel_message",
            "round_id": round_id,
            "channel_id": channel_id,
        },
        "",
    )


def _redecide_remaining(
    *,
    round_id: str,
    channel_id: str,
    pending: list[Any],
    pinned_ids: list[str],
    opening_message: str,
    latest_message: str,
) -> list[Any]:
    """Optionally re-route members still waiting after a speak.

    One wake is issued by the caller. A failed re-route leaves the remaining
    drain in place and stops further routes for this round. Required @ ids
    that are still waiting stay in speak. @ ids stored from the speak wait
    for the next round and are not pulled ahead here.
    """
    remaining_ids = [candidate.agent_id for candidate in pending]
    remaining_set = set(remaining_ids)
    forced = [agent_id for agent_id in pinned_ids if agent_id in remaining_set]
    voluntary = [agent_id for agent_id in remaining_ids if agent_id not in set(forced)]
    if not voluntary:
        return pending
    latest = (latest_message or "").strip() or opening_message
    plan = plan_channel_route(
        members=_members_in_order(channel_id, remaining_ids),
        fallback_order=remaining_ids,
        latest_message=latest,
        pending_mention_ids=forced,
        forced_ids=forced,
        opening_message=opening_message,
    )
    if plan.mode != "system":
        channel_round_db.set_channel_round_meta(round_id, router_mode="fallback")
        return pending
    meta = channel_round_db.get_channel_round_meta(round_id)
    stepped = list(meta.get("stepped_out") or [])
    for agent_id in plan.stay_out:
        if agent_id not in remaining_set:
            continue
        _engine_pass(agent_id, round_id=round_id, channel_id=channel_id)
        if agent_id not in stepped:
            stepped.append(agent_id)
    start_at = min(candidate.queue_position or 1 for candidate in pending)
    for offset, agent_id in enumerate(plan.speak):
        if agent_id not in remaining_set:
            continue
        db.update_channel_response_candidate(
            round_id=round_id,
            agent_id=agent_id,
            status="pending",
            queue_position=start_at + offset,
        )
    channel_round_db.set_channel_round_meta(
        round_id,
        stepped_out=stepped,
        router_mode="system",
        pinned_ids=[agent_id for agent_id in plan.pinned if agent_id in remaining_set],
    )
    return _pending_candidates(db.list_channel_response_candidates(round_id))


def _pending_candidates(candidates: list[Any]) -> list[Any]:
    pending = [
        candidate
        for candidate in candidates
        if str(candidate.status or "") == "pending"
    ]
    pending.sort(key=lambda candidate: (candidate.queue_position or 9999, candidate.created_at))
    return pending


def _members_in_order(channel_id: str, agent_ids: list[str]) -> list[dict[str, str]]:
    by_id = {member["id"]: member for member in _ordered_members(channel_id, set())}
    return [by_id[agent_id] for agent_id in agent_ids if agent_id in by_id]


def _latest_channel_text(channel_id: str, fallback: str) -> str:
    """Newest transcript line, skipping round-boundary markers."""
    for row in reversed(db.list_channel_messages(channel_id, limit=12)):
        if row.author_type == "system" and (row.notification_kind or "") == ROUND_MARKER_KIND:
            continue
        text = (row.content or "").strip()
        if text:
            return text
    return fallback


def _ordered_members(channel_id: str, excluded: set[str]) -> list[dict[str, str]]:
    """Membership order with names, skipping excluded agents."""
    details = {
        str(member.get("id") or ""): member
        for member in db.list_channel_member_details(channel_id)
    }
    ordered: list[dict[str, str]] = []
    for membership in db.list_channel_members(channel_id):
        agent_id = membership.agent_id
        if not agent_id or agent_id in excluded:
            continue
        detail = details.get(agent_id) or {}
        ordered.append(
            {
                "id": agent_id,
                "name": str(detail.get("name") or ""),
                "role": str(detail.get("role") or ""),
            }
        )
    return ordered


def _merge_ids(*groups: list[str], allowed: set[str]) -> list[str]:
    """Keep the first occurrence of each id that is in ``allowed``."""
    found: list[str] = []
    for group in groups:
        for agent_id in group:
            token = (agent_id or "").strip()
            if token in allowed and token not in found:
                found.append(token)
    return found


def _board_sticky(members: list[dict[str, str]], owner_ids: list[str]) -> str:
    """One sticky line per Board next owner so the router can read the pin."""
    by_id = {member["id"]: member for member in members}
    lines: list[str] = []
    for agent_id in owner_ids:
        member = by_id.get(agent_id) or {}
        name = str(member.get("name") or "").strip() or agent_id
        lines.append(f"Board next: {agent_id} | {name}")
    return "\n".join(lines)


def _lead_id(channel: Any, member_ids: list[str]) -> str | None:
    """Channel creator when they are in the round, otherwise the first member."""
    created_by = str(getattr(channel, "created_by", "") or "").strip()
    if created_by and created_by in member_ids:
        return created_by
    return member_ids[0] if member_ids else None


def _dispatch_mode(trigger: dict[str, Any]) -> str:
    """Prefer the trigger stamp, then the round row. Unset rows stay fan-out."""
    stamped = str(trigger.get("dispatch_mode") or "").strip()
    if stamped in {DISPATCH_ROUNDS, DISPATCH_FANOUT}:
        return stamped
    round_id = str(trigger.get("round_id") or "").strip()
    if not round_id:
        return DISPATCH_FANOUT
    return str(channel_round_db.get_channel_round_meta(round_id)["dispatch_mode"])


def _snapshot_is_current(channel_id: str, source_message_id: str) -> bool:
    """False once a newer human tip has replaced this snapshot."""
    if not source_message_id:
        return True
    return db.get_later_human_channel_message(channel_id, source_message_id) is None


def _other_snapshot_is_active(channel_id: str, round_id: str, source_message_id: str) -> bool:
    """True when another human snapshot already has a live round in this channel."""
    for row in db.list_channel_response_rounds(channel_id, status="active"):
        if row.id == round_id:
            continue
        if source_message_id and row.source_message_id == source_message_id:
            continue
        return True
    return False


def _append_mentions(existing: list[str], channel_id: str, text: str) -> list[str]:
    """Reserve @mentions for the next round without touching the current queue."""
    if not text.strip():
        return list(existing)
    members = _ordered_members(channel_id, set())
    found = mention_ids_in_order(text, members)
    merged = list(existing)
    for agent_id in found:
        if agent_id not in merged:
            merged.append(agent_id)
    return merged


def _wake_trigger(
    trigger: dict[str, Any],
    agent_id: str,
    round_id: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Build the next ordered channel wake. It is a fresh judgment, not a queue pop."""
    payload = {
        "content": trigger.get("content", ""),
        "channel_id": trigger.get("channel_id"),
        "round_id": round_id,
        "from_name": trigger.get("from_name", "Human Operator"),
        "author_type": trigger.get("author_type", "human"),
        "from_agent": trigger.get("from_agent"),
        "source_message_id": trigger.get("source_message_id"),
        "channel_name": trigger.get("channel_name"),
        "dispatch_mode": DISPATCH_ROUNDS,
        "round_index": meta.get("round_index") or 1,
    }
    return {
        "agent_id": agent_id,
        "trigger_type": "channel_message",
        "source_channel": "channel",
        "payload": payload,
    }


def _open_follow_up_round(
    trigger: dict[str, Any],
    *,
    round_id: str,
    channel_id: str,
    source_id: str,
    meta: dict[str, Any],
    participants: list[str],
) -> dict[str, Any]:
    """Open the next round only for who should speak. Empty speak is a hard stop.

    System AI may name a short speak list, including someone who passed once.
    An empty system speak list ends the snapshot. A passed human @ is not
    pinned back in, and shape must not re-insert one. A failed or unset
    route does not wake the room. It wakes required @ ids only. Two
    consecutive passes demote a member from that list.
    """
    empty: dict[str, Any] = {"trigger_requests": []}
    index = int(meta.get("round_index") or 1)
    if index >= channel_response_round_cap():
        return empty
    if not _snapshot_is_current(channel_id, source_id):
        return empty
    if _other_snapshot_is_active(channel_id, round_id, source_id):
        return empty
    next_index = index + 1
    if _round_index_exists(channel_id, source_id, next_index):
        return empty

    mention_ids = [agent_id for agent_id in meta.get("next_mentions") or [] if agent_id]
    stepped = set(meta.get("stepped_out") or [])
    member_ids = [member["id"] for member in _ordered_members(channel_id, set())]
    member_set = set(member_ids)
    # The previous round's roster, plus anyone this round @mentioned.
    # System AI may speak a member who passed once. Someone never in the
    # snapshot stays out until they are mentioned.
    pool: list[str] = []
    for agent_id in list(participants) + list(mention_ids):
        if agent_id in member_set and agent_id not in pool:
            pool.append(agent_id)
    if not pool:
        return empty

    channel = db.get_channel(channel_id)
    if channel is None or channel.status != "active":
        return empty
    ordered = order_round_members(
        [agent_id for agent_id in member_ids if agent_id in set(pool)],
        lead_id=_lead_id(channel, pool),
        mentioned_ids=mention_ids,
        round_index=next_index,
    )
    if not ordered:
        return empty

    responded = {
        candidate.agent_id
        for candidate in db.list_channel_response_candidates(round_id)
        if str(candidate.status or "") == "responded"
    }
    required: list[str] = []
    for agent_id in list(meta.get("pinned_ids") or []) + list(mention_ids):
        if agent_id in set(ordered) and agent_id not in responded and agent_id not in required:
            required.append(agent_id)
    roster = _members_in_order(channel_id, ordered)
    latest = _latest_channel_text(channel_id, str(trigger.get("content") or ""))
    plan = _plan_for_members(
        mode=DISPATCH_ROUNDS,
        members=roster,
        fallback_order=ordered,
        latest_message=latest,
        required_ids=required,
        opening_message=str(trigger.get("content") or ""),
    )
    if plan.mode == "system" and not plan.named_speak:
        return empty
    speak_ids, stay_ids = shape_follow_up_speak(
        channel_id,
        mode=plan.mode,
        speak=list(plan.speak),
        ordered=ordered,
        required_ids=required,
        mention_ids=mention_ids,
    )
    if not speak_ids:
        return empty
    blocked = set(demoted_ids(channel_id))
    stay_ids = [agent_id for agent_id in stay_ids if agent_id not in blocked]
    stepped_now = list(stepped)
    for agent_id in stay_ids:
        if agent_id not in stepped_now:
            stepped_now.append(agent_id)

    round_record = db.create_channel_response_round(
        channel_id=channel_id,
        source_message_id=source_id or str(trigger.get("source_message_id") or ""),
    )
    channel_round_db.set_channel_round_meta(
        round_record.id,
        round_index=next_index,
        dispatch_mode=DISPATCH_ROUNDS,
        stepped_out=stepped_now,
        next_mentions=[],
        router_mode=plan.mode,
        pinned_ids=[agent_id for agent_id in plan.pinned if agent_id in set(speak_ids)],
    )
    follow_wake = _install_round_queue(
        round_id=round_record.id,
        channel_id=channel_id,
        speak_ids=speak_ids,
        stay_out_ids=stay_ids,
        wake_all=False,
    )
    if not follow_wake:
        db.maybe_complete_channel_response_round(round_record.id)
        return empty
    follow_trigger = dict(trigger)
    follow_trigger["round_id"] = round_record.id
    follow_trigger["round_index"] = next_index
    follow_trigger["dispatch_mode"] = DISPATCH_ROUNDS
    progress: dict[str, Any] = {
        "trigger_requests": [
            _wake_trigger(follow_trigger, follow_wake[0], round_record.id, {"round_index": next_index})
        ]
    }
    marker = _post_round_marker(channel_id, source_id, next_index)
    if marker:
        progress["round_marker"] = marker
    return progress


def _round_index_exists(channel_id: str, source_id: str, round_index: int) -> bool:
    if not source_id:
        return False
    for row in db.list_channel_response_rounds(channel_id):
        if row.source_message_id != source_id:
            continue
        meta = channel_round_db.get_channel_round_meta(row.id)
        if int(meta.get("round_index") or 1) == round_index:
            return True
    return False


def _post_round_marker(
    channel_id: str,
    source_id: str,
    round_index: int,
) -> dict[str, Any] | None:
    """One system line so later wakes can see the round boundary. Not a pass.

    Posted for agent prompt history and diagnostics. The operator transcript
    filters `channel_round_marker` and does not paint "Round N".
    """
    if round_index <= 1 or not source_id:
        return None
    content = f"Round {round_index}"
    existing = db.find_channel_round_marker(
        channel_id=channel_id,
        after_message_id=source_id,
        content=content,
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
            content=content,
            source_channel="channel",
            notification_kind=ROUND_MARKER_KIND,
        )
    except ChannelArchivedError:
        return None
    logger.info(
        "channel round marker channel=%s source=%s round=%s",
        channel_id,
        source_id,
        round_index,
    )
    return {
        "channel_id": channel_id,
        "content": message.content,
        "author_type": message.author_type,
        "author_name": message.author_name or "BossMod",
        "message_id": message.id,
        "created_at": message.created_at,
        "notification_kind": message.notification_kind,
    }


def log_channel_pass(agent: Agent, trigger: dict[str, Any], reply: str) -> None:
    """Record a pass in diagnostics. The text is not a channel message."""
    logger.info(
        "channel pass kept off channel agent=%s round=%s",
        agent.id,
        str(trigger.get("round_id") or ""),
    )
    db.create_diagnostic(
        agent_id=agent.id,
        agent_name=agent.name,
        trigger_type=str(trigger.get("type") or "channel_message"),
        trigger_data=json.dumps(
            {
                "round_id": trigger.get("round_id"),
                "channel_id": trigger.get("channel_id"),
                "pass": True,
            }
        ),
        status="skipped",
        mode="decision",
        model=None,
        model_source="runtime",
        context=None,
        raw_response=None,
        action_name="channel_pass",
        parsed_action=None,
        result=json.dumps({"posted": False, "reply_suppressed": bool((reply or "").strip())}),
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        error=None,
        duration_ms=0,
    )
