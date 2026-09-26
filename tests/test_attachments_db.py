"""DB tests for db/attachments.py — CRUD against a real (temp) SQLite DB."""

from __future__ import annotations

import uuid

import pytest

from core.models import Attachment
from db import attachments as att_db
from db import messages as msg_db


@pytest.fixture(autouse=True)
def db():
    """Ensure the schema is applied and clean attachments between tests."""
    from db.connection import init_db
    init_db()
    yield
    # Cleanup: remove all attachment rows so tests don't leak into each other
    from db.connection import get_connection
    con = get_connection()
    con.execute("DELETE FROM attachments")


def test_create_attachment(db):
    row = att_db.create_attachment(
        message_id="pending",
        file_name="hello.png",
        file_size=1234,
        mime_type="image/png",
        storage_path="/tmp/hello.png",
        preview_tier="image",
    )
    assert isinstance(row, Attachment)
    assert row.file_name == "hello.png"
    assert row.file_size == 1234
    assert row.message_id == "pending"
    assert row.id  # uuid assigned


def test_get_attachments_for_message(db):
    mid = f"m-{uuid.uuid4().hex[:8]}"
    a1 = att_db.create_attachment(mid, "a.png", 1, "image/png", "/a", "image")
    a2 = att_db.create_attachment(mid, "b.txt", 2, "text/plain", "/b", "text")
    rows = att_db.get_attachments_for_message(mid)
    assert len(rows) == 2
    assert rows[0].file_name == "a.png"
    assert rows[1].file_name == "b.txt"


def test_get_attachment_by_id_missing(db):
    assert att_db.get_attachment_by_id("nonexistent-uuid") is None


def test_get_attachment_by_id_found(db):
    created = att_db.create_attachment("m1", "x.png", 5, "image/png", "/x", "image")
    fetched = att_db.get_attachment_by_id(created.id)
    assert fetched is not None
    assert fetched.id == created.id


def test_delete_attachments_for_message(db):
    mid = f"m-{uuid.uuid4().hex[:8]}"
    att_db.create_attachment(mid, "a.png", 1, "image/png", "/a", "image")
    att_db.create_attachment(mid, "b.png", 1, "image/png", "/b", "image")
    att_db.create_attachment("other", "c.png", 1, "image/png", "/c", "image")
    removed = att_db.delete_attachments_for_message(mid)
    assert removed == 2
    assert len(att_db.get_attachments_for_message(mid)) == 0


def test_link_attachments_to_message(db):
    a1 = att_db.create_attachment("pending", "a.png", 1, "image/png", "/a", "image")
    a2 = att_db.create_attachment("pending", "b.png", 1, "image/png", "/b", "image")
    updated = att_db.link_attachments_to_message([a1.id, a2.id], "real-msg-id")
    assert updated == 2
    for a in (a1, a2):
        linked = att_db.get_attachment_by_id(a.id)
        assert linked.message_id == "real-msg-id"


def test_fk_cascade_on_message_delete(db):
    """Deleting a message should remove its attachments (app-level cascade)."""
    msg = msg_db.create_message("agent1", None, "hello")
    att_db.create_attachment(msg.id, "a.png", 1, "image/png", "/a", "image")
    att_db.create_attachment(msg.id, "b.png", 1, "image/png", "/b", "image")
    assert len(att_db.get_attachments_for_message(msg.id)) == 2
    # App-level cascade: delete attachments when the message is deleted
    att_db.delete_attachments_for_message(msg.id)
    assert len(att_db.get_attachments_for_message(msg.id)) == 0
