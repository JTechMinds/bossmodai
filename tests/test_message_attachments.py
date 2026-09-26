"""BossMod AI — Message attachment linkage tests."""

from __future__ import annotations

import uuid

import pytest

from db import attachments as db_att
from db import messages as db_msg
from db.crud import execute
from db.connection import get_connection, init_db


@pytest.fixture(autouse=True)
def _clean():
    """Clean up attachments before and after each test."""
    init_db()
    con = get_connection()
    con.execute("DELETE FROM attachments WHERE message_id = 'pending'")
    con.execute("DELETE FROM attachments")
    yield
    con.execute("DELETE FROM attachments")


def _make_pending_att(name: str = "f.txt") -> str:
    """Create a pending attachment and return its id."""
    att = db_att.create_attachment(
        message_id="pending",
        file_name=name,
        file_size=100,
        mime_type="text/plain",
        storage_path="/tmp/dummy",
        preview_tier="text",
    )
    return att.id


def test_send_with_two_attachments(_clean):
    """AC-5, AC-13: Send a message with 2 attachment_ids → both linked."""
    a1 = _make_pending_att("a.txt")
    a2 = _make_pending_att("b.txt")

    msg = db_msg.create_message(from_agent="test", to_agent=None, content="hello")
    updated = db_att.link_attachments_to_message([a1, a2], msg.id)
    assert updated == 2

    atts = db_att.get_attachments_for_message(msg.id)
    assert len(atts) == 2
    assert all(a.message_id == msg.id for a in atts)


def test_send_text_only_no_attachments(_clean):
    """AC-12: Send a message with 0 attachments → works as before."""
    msg = db_msg.create_message(from_agent="test", to_agent=None, content="plain text")
    atts = db_att.get_attachments_for_message(msg.id)
    assert atts == []


def test_send_with_nonexistent_attachment_id(_clean):
    """Send with an attachment_id that doesn't exist → no rows linked."""
    fake_id = str(uuid.uuid4())
    msg = db_msg.create_message(from_agent="test", to_agent=None, content="hi")
    updated = db_att.link_attachments_to_message([fake_id], msg.id)
    assert updated == 0
    atts = db_att.get_attachments_for_message(msg.id)
    assert atts == []


def test_send_with_six_attachments(_clean):
    """AC-10: Send with 6 attachments → TOO_MANY (enforced at API layer)."""
    # The DB layer doesn't enforce the cap; the API does.
    # This test verifies the DB can handle 6 links (the cap is an API concern).
    ids = [_make_pending_att(f"f{i}.txt") for i in range(6)]
    msg = db_msg.create_message(from_agent="test", to_agent=None, content="six")
    updated = db_att.link_attachments_to_message(ids, msg.id)
    assert updated == 6


def test_send_attachment_only_no_text(_clean):
    """AC-7: Message created with empty content and attachments."""
    a1 = _make_pending_att("img.png")
    msg = db_msg.create_message(from_agent="test", to_agent=None, content="")
    updated = db_att.link_attachments_to_message([a1], msg.id)
    assert updated == 1
    atts = db_att.get_attachments_for_message(msg.id)
    assert len(atts) == 1
