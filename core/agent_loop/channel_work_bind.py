"""Sticky work owners for one channel snapshot.

The engine records who is bound to live work. It does not compare talk
text. A bind stays until that work leaves pending, accepted, or active,
or a new human snapshot clears the channel. System AI may still guess
whether an unbound mouth would only echo.
"""

from __future__ import annotations

import db
from db import channel_response_rounds as channel_round_db

# Live board work. Anything else (blocked, complete, waiting, stalled) is settled.
LIVE_WORK_BIND_STATUSES = frozenset({"pending", "accepted", "active"})


def bind_is_live(task_id: str) -> bool:
    """Return whether this task still holds a Talk bind.

    An unknown task id stays bound until a human snapshot clears it.
    A missing task row has already left the board.
    """
    token = (task_id or "").strip()
    if not token:
        return True
    task = db.get_task(token)
    if task is None:
        return False
    return str(task.status or "") in LIVE_WORK_BIND_STATUSES


def live_work_binds(channel_id: str) -> list[dict[str, str]]:
    """Return live binds for the channel and drop settled pairs from each round."""
    token = (channel_id or "").strip()
    if not token:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in db.list_channel_response_rounds(token):
        meta = channel_round_db.get_channel_round_meta(row.id)
        pairs = list(meta.get("work_binds") or [])
        kept = [pair for pair in pairs if bind_is_live(str(pair.get("task_id") or ""))]
        if [(pair["agent_id"], pair["task_id"]) for pair in kept] != [
            (pair["agent_id"], pair["task_id"]) for pair in pairs
        ]:
            channel_round_db.set_channel_round_meta(row.id, work_binds=kept)
        for pair in kept:
            agent_id = str(pair.get("agent_id") or "").strip()
            if not agent_id or agent_id in seen:
                continue
            seen.add(agent_id)
            found.append({"agent_id": agent_id, "task_id": str(pair.get("task_id") or "")})
    return found


def live_work_bind_ids(channel_id: str) -> list[str]:
    """Return agent ids whose channel work is still pending, accepted, or active."""
    return [pair["agent_id"] for pair in live_work_binds(channel_id)]


def clear_channel_work_binds(channel_id: str) -> None:
    """Drop every bind on this channel. A new human snapshot calls this."""
    token = (channel_id or "").strip()
    if not token:
        return
    for row in db.list_channel_response_rounds(token):
        meta = channel_round_db.get_channel_round_meta(row.id)
        if meta.get("work_binds"):
            channel_round_db.set_channel_round_meta(row.id, work_binds=[])


def record_round_work_bind(round_id: str, *, agent_id: str, task_id: str) -> None:
    """Remember one owner on this round and on later rounds of the same snapshot."""
    token = (round_id or "").strip()
    agent = (agent_id or "").strip()
    task = (task_id or "").strip()
    if not token or not agent or not task:
        return
    row = db.get_channel_response_round(token)
    if row is None:
        return
    source = str(row.source_message_id or "")
    pair = {"agent_id": agent, "task_id": task}
    for other in db.list_channel_response_rounds(row.channel_id):
        if other.id != token and str(other.source_message_id or "") != source:
            continue
        meta = channel_round_db.get_channel_round_meta(other.id)
        pairs = [item for item in list(meta.get("work_binds") or []) if item.get("agent_id") != agent]
        pairs.append(pair)
        channel_round_db.set_channel_round_meta(other.id, work_binds=pairs)


def pairs_for_agents(channel_id: str, agent_ids: list[str]) -> list[dict[str, str]]:
    """Pair each Done-round owner with their pending channel card, when one exists."""
    from core.tasking.board import pending_channel_card_for_owner

    pairs: list[dict[str, str]] = []
    seen: set[str] = set()
    for agent_id in agent_ids:
        token = (agent_id or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        card = pending_channel_card_for_owner(channel_id, token)
        task_id = str(getattr(card, "id", "") or "") if card is not None else ""
        pairs.append({"agent_id": token, "task_id": task_id})
    return pairs


def merge_work_binds(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    """Union binds. A later pair with a task id replaces an empty one for that agent."""
    found: list[dict[str, str]] = []
    index: dict[str, int] = {}
    for group in groups:
        for pair in group:
            agent_id = str(pair.get("agent_id") or "").strip()
            task_id = str(pair.get("task_id") or "").strip()
            if not agent_id:
                continue
            item = {"agent_id": agent_id, "task_id": task_id}
            if agent_id in index:
                if task_id:
                    found[index[agent_id]] = item
                continue
            index[agent_id] = len(found)
            found.append(item)
    return found


def snapshot_spoke_ids(channel_id: str, source_message_id: str) -> list[str]:
    """Return who already responded on this human snapshot. No text compare."""
    channel = (channel_id or "").strip()
    source = (source_message_id or "").strip()
    if not channel or not source:
        return []
    found: list[str] = []
    for row in db.list_channel_response_rounds(channel):
        if str(row.source_message_id or "") != source:
            continue
        for candidate in db.list_channel_response_candidates(row.id):
            if str(candidate.status or "") != "responded":
                continue
            if candidate.agent_id not in found:
                found.append(candidate.agent_id)
    return found
