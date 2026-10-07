"""Office chatter: one floor's agent-to-agent messages, newest first.

Agents message each other outside any thread; those rows are persisted in
``messages`` with the floor the conversation happened on. This route pages
them for the Office chatter panel in Chat's context column. New rows arrive
live as the ``peer_message`` WebSocket event, in the same row shape
(``core.agent_loop.message_delivery.peer_message_event``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from core import config
from core.agent_loop.message_delivery import peer_message_event
from core.models.message import HUMAN_SENDER_ID, Message
from db.floors import get_floor
import db

logger = logging.getLogger(__name__)

router = APIRouter()

_PAGE_SIZE_SETTING = "office_chatter_page_size"


def _is_floor_peer_row(message: Message, floor_id: str) -> bool:
    """True when ``message`` is an agent→agent row filed on ``floor_id``."""
    return (
        message.floor_id == floor_id
        and message.to_agent is not None
        and message.from_agent != HUMAN_SENDER_ID
        and message.to_agent != HUMAN_SENDER_ID
    )


@router.get("/office/chatter")
async def get_office_chatter(floor_id: str, before: str | None = None) -> dict[str, Any]:
    """Return one page of a floor's agent-to-agent messages, newest first.

    The page size is the server-owned ``office_chatter_page_size`` setting;
    the client only sends a cursor, so a caller cannot inflate the page.

    Args:
        floor_id: The floor whose conversations are listed. Required.
        before: The ``message_id`` of the oldest row already shown. Only
            strictly older rows (by send time, then id) are returned. Omit it
            for the newest page.

    Returns:
        ``{"messages": [row, ...], "has_more": bool}``. Each row is
        ``message_id``, ``from_agent_id``, ``to_agent_id``, ``content``,
        ``message_type``, ``created_at`` (ISO 8601) and ``floor_id``.
        ``has_more`` says whether older rows exist past this page.

    Raises:
        HTTPException: 404 ``"Floor not found"`` for an unknown floor. 400
            ``"Unknown cursor"`` when ``before`` does not resolve, or resolves
            to a row that is not agent→agent on this floor. 500 when the
            page-size setting is missing or below 1; the bad setting is
            logged at ERROR and not clamped, so it gets fixed.
    """
    if get_floor(floor_id) is None:
        raise HTTPException(404, "Floor not found")

    cursor = None
    if before is not None:
        anchor = db.get_message(before)
        if anchor is None or not _is_floor_peer_row(anchor, floor_id):
            raise HTTPException(400, "Unknown cursor")
        cursor = (anchor.created_at, anchor.id)

    page_size = config.get_int(_PAGE_SIZE_SETTING)
    if page_size is None or page_size < 1:
        logger.error(
            "Setting %s=%r is invalid; it must be an integer of at least 1",
            _PAGE_SIZE_SETTING,
            config.get(_PAGE_SIZE_SETTING),
        )
        # The detail is shown verbatim in the panel, so it names the setting by
        # its Settings label; the log line above keeps the key for diagnosis.
        raise HTTPException(
            500,
            "The Office Chatter Page Size setting must be a whole number of at least 1. "
            "Change it in Settings.",
        )

    # One extra row says whether an older page exists, without a count query.
    rows = db.list_floor_peer_messages(floor_id, page_size + 1, before=cursor)
    return {
        "messages": [peer_message_event(message) for message in rows[:page_size]],
        "has_more": len(rows) > page_size,
    }
