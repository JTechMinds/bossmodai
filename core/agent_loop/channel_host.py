"""Host-owned Talk, Work, and Paused rules for one shared thread.

System AI may choose who speaks. These rules decide when the snapshot
stops. They do not cancel off-thread work wakes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import db
from db import channel_host as host_db

# Two consecutive clear acks end the snapshot. A substantive line resets it.
DUP_ACK_LIMIT = 2
# Two consecutive passes or router stay-outs demote that member.
PASS_DEMOTE_AFTER = 2

PAUSE_KIND = "thread_paused"
RESUME_KIND = "thread_resumed"
PAUSE_LINE = "Thread paused. Resume by restarting the conversation."
RESUME_LINE = "Thread resumed."

# Origin lines that put the working agent back in the thread without a
# Talk ping or a made-up human snapshot.
SPEAK_WORTHY_ORIGIN_KINDS = frozenset(
    {
        "completion",
        "waiting",
        "stalled",
        "blocked_claim",
        "blocked_peer_handoff",
        "blocked_no_task",
        "blocked_no_progress",
        "blocked_host_deny",
        "blocked_shell_executor",
        "blocked_nest_git",
    }
)

_PAUSE_PHRASE = re.compile(
    r"(?is)^\s*(?:please\s+)?"
    r"(?:"
    r"let'?s\s+pause(?:\s+here)?|"
    r"let\s+us\s+pause(?:\s+here)?|"
    r"pause(?:\s+here)?|"
    r"let'?s\s+stop(?:\s+here)?|"
    r"stop(?:\s+here)?|"
    r"let'?s\s+hold(?:\s+here)?|"
    r"hold(?:\s+(?:here|on|up))?|"
    r"pause\s+thread"
    r")"
    r"\s*[.!]?\s*$"
)
_ACK_PHRASE = re.compile(
    r"(?is)^\s*(?:"
    r"copy that|"
    r"got it|"
    r"understood|"
    r"acknowledged|"
    r"acknowledge|"
    r"ack|"
    r"roger(?:\s+that)?|"
    r"noted|"
    r"will do|"
    r"on it|"
    r"sounds good"
    r")"
    r"\s*[.!]?\s*$"
)


@dataclass(frozen=True, slots=True)
class PreparedHumanTurn:
    """Whether a human line may open Talk, plus an optional system marker."""

    allow_round: bool
    marker: dict[str, Any] | None = None


def is_pause_phrase(text: str | None) -> bool:
    """Return True for a clear pause, stop, or hold line and nothing broader."""
    return bool(_PAUSE_PHRASE.match(text or ""))


def is_ack_phrase(text: str | None) -> bool:
    """Return True only when the whole line is a clear acknowledgement."""
    return bool(_ACK_PHRASE.match(text or ""))


def is_thread_paused(channel_id: str) -> bool:
    """Return whether the thread is in host Paused."""
    return bool(host_db.get_channel_host_state(channel_id)["paused"])


def work_holds_talk(channel_id: str) -> bool:
    """Return whether a board commitment has closed peer Talk."""
    state = host_db.get_channel_host_state(channel_id)
    return bool(state["work_agent_id"] or state["work_task_id"])


def blocks_peer_round(channel_id: str, *, author_type: str) -> bool:
    """Return whether this author must not open a Talk round.

    Paused blocks every round open. Human restart clears Pause in
    ``prepare_human_channel_message`` before this check. Peers also stay
    silent while a board commitment holds Talk.
    """
    if is_thread_paused(channel_id):
        return True
    if (author_type or "") != "human" and work_holds_talk(channel_id):
        return True
    return False


def prepare_human_channel_message(channel_id: str, content: str) -> PreparedHumanTurn:
    """Apply Pause or Resume before a human line opens Talk.

    A pause phrase pauses the thread and does not open a round. Any other
    human line while paused restarts the conversation.
    """
    if is_pause_phrase(content):
        return PreparedHumanTurn(allow_round=False, marker=pause_thread(channel_id))
    marker = resume_thread(channel_id) if is_thread_paused(channel_id) else None
    return PreparedHumanTurn(allow_round=True, marker=marker)


def pause_thread(channel_id: str) -> dict[str, Any] | None:
    """Pause one thread, post the resume note, and kill Talk rounds.

    Work wakes are not cancelled. A second pause does not post again.
    """
    token = (channel_id or "").strip()
    if not token or db.get_channel(token) is None or db.is_channel_archived(token):
        return None
    state = host_db.get_channel_host_state(token)
    if state["paused"]:
        return None
    state["paused"] = True
    host_db.save_channel_host_state(state)
    stop_active_talk_rounds(token)
    return _post_marker(token, PAUSE_LINE, PAUSE_KIND)


def resume_thread(channel_id: str) -> dict[str, Any] | None:
    """Clear host Paused and post the resume line. Does not open a round."""
    token = (channel_id or "").strip()
    if not token or db.get_channel(token) is None or db.is_channel_archived(token):
        return None
    state = host_db.get_channel_host_state(token)
    if not state["paused"]:
        return None
    state["paused"] = False
    host_db.save_channel_host_state(state)
    return _post_marker(token, RESUME_LINE, RESUME_KIND)


def note_human_snapshot(channel_id: str, mention_ids: list[str]) -> None:
    """Reset Talk counters for a new human line and remember human @ ids.

    Human @ ids are protected from demotion for this snapshot. Work silence
    ends because a new human ask reopens Talk. Pause is not cleared here.
    """
    token = (channel_id or "").strip()
    if not token:
        return
    state = host_db.get_channel_host_state(token)
    protected: list[str] = []
    for agent_id in mention_ids:
        item = (agent_id or "").strip()
        if item and item not in protected:
            protected.append(item)
    state["pass_streaks"] = {}
    state["demoted_ids"] = []
    state["protected_ids"] = protected
    state["ack_streak"] = 0
    state["work_agent_id"] = ""
    state["work_task_id"] = ""
    host_db.save_channel_host_state(state)


def note_channel_work(channel_id: str, *, agent_id: str, task_id: str) -> None:
    """Close peer Talk because one agent bound a real board task.

    Does not delete that agent's work wakes. Callers stop the Talk queue
    separately via :func:`talk_closed`.
    """
    token = (channel_id or "").strip()
    agent = (agent_id or "").strip()
    task = (task_id or "").strip()
    if not token or not agent or not task:
        return
    state = host_db.get_channel_host_state(token)
    state["work_agent_id"] = agent
    state["work_task_id"] = task
    host_db.save_channel_host_state(state)


def note_speak_worthy_outcome(channel_id: str, *, agent_id: str, task_id: str) -> None:
    """Clear work silence when that commitment is done, blocked, or Needs.

    The origin line already in the thread is the re-entry. This does not
    open a Talk round and does not invent a human snapshot.
    """
    token = (channel_id or "").strip()
    if not token:
        return
    state = host_db.get_channel_host_state(token)
    if not state["work_agent_id"] and not state["work_task_id"]:
        return
    holder = (agent_id or "").strip()
    task = (task_id or "").strip()
    if state["work_agent_id"] and holder and state["work_agent_id"] != holder:
        return
    if state["work_task_id"] and task and state["work_task_id"] != task:
        return
    state["work_agent_id"] = ""
    state["work_task_id"] = ""
    host_db.save_channel_host_state(state)


def talk_closed(channel_id: str) -> bool:
    """Return whether peer Talk must not continue on this thread."""
    return is_thread_paused(channel_id) or work_holds_talk(channel_id)


def record_channel_turn(
    channel_id: str,
    *,
    spoke: bool,
    speaker_id: str,
    spoken_text: str,
) -> bool:
    """Update pass and ack counters. True means dup-ack ends the snapshot."""
    speaker = (speaker_id or "").strip()
    if not speaker:
        return False
    if spoke:
        note_speak(channel_id)
        return note_ack(channel_id, spoken_text)
    note_pass(channel_id, speaker)
    return False


def note_pass(channel_id: str, agent_id: str) -> None:
    """Count one pass or router stay-out. Two in a row demotes that member."""
    agent = (agent_id or "").strip()
    token = (channel_id or "").strip()
    if not token or not agent:
        return
    state = host_db.get_channel_host_state(token)
    streaks = dict(state["pass_streaks"])
    streaks[agent] = int(streaks.get(agent) or 0) + 1
    state["pass_streaks"] = streaks
    if streaks[agent] >= PASS_DEMOTE_AFTER:
        demoted = list(state["demoted_ids"])
        if agent not in demoted:
            demoted.append(agent)
        state["demoted_ids"] = demoted
    host_db.save_channel_host_state(state)


def note_speak(channel_id: str) -> None:
    """A real speak resets every pass streak and lifts demotion."""
    token = (channel_id or "").strip()
    if not token:
        return
    state = host_db.get_channel_host_state(token)
    state["pass_streaks"] = {}
    state["demoted_ids"] = []
    host_db.save_channel_host_state(state)


def note_ack(channel_id: str, spoken_text: str) -> bool:
    """Count consecutive clear acks. A non-ack speak resets the streak."""
    token = (channel_id or "").strip()
    if not token:
        return False
    state = host_db.get_channel_host_state(token)
    if is_ack_phrase(spoken_text):
        state["ack_streak"] = int(state["ack_streak"]) + 1
    else:
        state["ack_streak"] = 0
    host_db.save_channel_host_state(state)
    return int(state["ack_streak"]) >= DUP_ACK_LIMIT


def shape_follow_up_speak(
    channel_id: str,
    *,
    mode: str,
    speak: list[str],
    ordered: list[str],
    required_ids: list[str],
    mention_ids: list[str],
) -> tuple[list[str], list[str]]:
    """Choose the next wake list. A new round is not "wake everyone".

    Fallback keeps only required ids and @ mentions. System speak is kept,
    then demotion drops members with two passes unless a human @ protects
    them or this round @s them. An empty list is a hard stop.
    """
    allowed = set(ordered)
    mentions = [agent_id for agent_id in mention_ids if agent_id in allowed]
    # An empty system speak list is the snapshot stop. Do not re-insert a
    # prior @ or a required id; that reopens rounds until the cap.
    if mode == "system" and not any(agent_id in allowed for agent_id in speak):
        return [], []
    _lift_mentions(channel_id, mentions)
    if mode != "system":
        chosen = [agent_id for agent_id in required_ids if agent_id in allowed]
        for agent_id in mentions:
            if agent_id not in chosen:
                chosen.insert(0, agent_id)
        chosen = _drop_demoted(channel_id, chosen, mentions)
        return chosen, []
    chosen = _drop_demoted(channel_id, [agent_id for agent_id in speak if agent_id in allowed], mentions)
    protected = [
        agent_id
        for agent_id in host_db.get_channel_host_state(channel_id)["protected_ids"]
        if agent_id in set(chosen)
    ]
    for agent_id in reversed(protected):
        chosen.remove(agent_id)
        chosen.insert(0, agent_id)
    for agent_id in reversed(mentions):
        if agent_id in chosen:
            chosen.remove(agent_id)
        chosen.insert(0, agent_id)
    chosen = _drop_demoted(channel_id, chosen, mentions)
    stay = [agent_id for agent_id in ordered if agent_id not in set(chosen)]
    return chosen, stay


def demoted_ids(channel_id: str) -> list[str]:
    """Return members currently demoted from the next wake list."""
    return list(host_db.get_channel_host_state(channel_id)["demoted_ids"])


def stop_active_talk_rounds(channel_id: str) -> None:
    """Complete active Talk rounds and drop their queued wakes.

    Triggers that are not bound to a round id stay queued. That includes
    work resumes, which must survive Pause and an empty speak.
    """
    token = (channel_id or "").strip()
    if not token:
        return
    for row in db.list_channel_response_rounds(token, status="active"):
        _close_round(row.id)


def _close_round(round_id: str) -> None:
    for candidate in db.list_channel_response_candidates(round_id):
        status = str(candidate.status or "")
        if status in {"pending", "queued"}:
            db.mark_channel_candidate_observed(round_id=round_id, agent_id=candidate.agent_id)
    db.delete_queued_triggers_for_round(round_id)
    db.maybe_complete_channel_response_round(round_id)


def _lift_mentions(channel_id: str, mention_ids: list[str]) -> None:
    """@ clears that member's pass streak and demotion."""
    if not mention_ids:
        return
    state = host_db.get_channel_host_state(channel_id)
    streaks = dict(state["pass_streaks"])
    demoted = list(state["demoted_ids"])
    changed = False
    for agent_id in mention_ids:
        if agent_id in streaks:
            streaks.pop(agent_id, None)
            changed = True
        if agent_id in demoted:
            demoted = [item for item in demoted if item != agent_id]
            changed = True
    if not changed:
        return
    state["pass_streaks"] = streaks
    state["demoted_ids"] = demoted
    host_db.save_channel_host_state(state)


def _drop_demoted(channel_id: str, speak: list[str], mention_ids: list[str]) -> list[str]:
    """Drop demoted members. Human @ and this round's @ stay."""
    state = host_db.get_channel_host_state(channel_id)
    protected = set(state["protected_ids"]) | set(mention_ids)
    demoted = set(state["demoted_ids"])
    kept: list[str] = []
    for agent_id in speak:
        if agent_id in demoted and agent_id not in protected:
            continue
        if agent_id not in kept:
            kept.append(agent_id)
    return kept


def _post_marker(channel_id: str, content: str, kind: str) -> dict[str, Any] | None:
    from core.models.channel import ChannelArchivedError

    if db.is_channel_archived(channel_id):
        return None
    try:
        message = db.create_channel_message(
            channel_id=channel_id,
            author_type="system",
            author_name="BossMod",
            content=content,
            source_channel="channel",
            notification_kind=kind,
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
