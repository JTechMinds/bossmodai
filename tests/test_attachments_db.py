"""DB tests for db/attachments.py — CRUD against a real (temp) SQLite DB."""

from __future__ import annotations

import uuid

import pytest

from datetime import datetime, timedelta, timezone

from core.models import Attachment
from db import attachments as att_db
from db import messages as msg_db
from db.attachments import AttachmentLinkError

_CTX = {"context_type": "direct", "context_id": "agent-1"}


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
        context_type="direct",
        context_id="agent-1",
    )
    assert isinstance(row, Attachment)
    assert row.file_name == "hello.png"
    assert row.file_size == 1234
    assert row.message_id == "pending"
    assert row.context_type == "direct"
    assert row.context_id == "agent-1"
    assert row.id  # uuid assigned


def test_get_attachments_for_message(db):
    mid = f"m-{uuid.uuid4().hex[:8]}"
    a1 = att_db.create_attachment(mid, "a.png", 1, "image/png", "/a", "image", **_CTX)
    a2 = att_db.create_attachment(mid, "b.txt", 2, "text/plain", "/b", "text", **_CTX)
    rows = att_db.get_attachments_for_message(mid)
    assert len(rows) == 2
    assert rows[0].file_name == "a.png"
    assert rows[1].file_name == "b.txt"


def test_get_attachment_by_id_missing(db):
    assert att_db.get_attachment_by_id("nonexistent-uuid") is None


def test_get_attachment_by_id_found(db):
    created = att_db.create_attachment("m1", "x.png", 5, "image/png", "/x", "image", **_CTX)
    fetched = att_db.get_attachment_by_id(created.id)
    assert fetched is not None
    assert fetched.id == created.id


def test_delete_attachments_for_messages(db):
    mid = f"m-{uuid.uuid4().hex[:8]}"
    mid2 = f"m-{uuid.uuid4().hex[:8]}"
    a = att_db.create_attachment(mid, "a.png", 1, "image/png", "/a", "image", **_CTX)
    b = att_db.create_attachment(mid, "b.png", 1, "image/png", "/b", "image", **_CTX)
    d = att_db.create_attachment(mid2, "d.png", 1, "image/png", "/d", "image", **_CTX)
    other = att_db.create_attachment("other", "c.png", 1, "image/png", "/c", "image", **_CTX)
    removed = att_db.delete_attachments_for_messages([mid, mid2, mid])
    assert sorted(row.id for row in removed) == sorted([a.id, b.id, d.id])
    assert all(isinstance(row, Attachment) for row in removed)
    assert {row.storage_path for row in removed} == {"/a", "/b", "/d"}
    assert att_db.get_attachments_for_message(mid) == []
    assert att_db.get_attachments_for_message(mid2) == []
    assert att_db.get_attachment_by_id(other.id) is not None


def test_delete_attachments_for_no_messages_is_a_no_op(db):
    kept = att_db.create_attachment("keep", "k.png", 1, "image/png", "/k", "image", **_CTX)
    assert att_db.delete_attachments_for_messages([]) == []
    assert att_db.get_attachment_by_id(kept.id) is not None


def _pending(name: str = "a.png", **ctx: str) -> Attachment:
    scope = {**_CTX, **ctx}
    return att_db.create_attachment("pending", name, 1, "image/png", f"/{name}", "image", **scope)


def test_link_pending_attachments_links_all_in_given_order(db):
    a1 = _pending("a.png")
    a2 = _pending("b.png")
    linked = att_db.link_pending_attachments([a2.id, a1.id], "real-msg-id", **_CTX)
    assert [row.id for row in linked] == [a2.id, a1.id]
    for a in (a1, a2):
        assert att_db.get_attachment_by_id(a.id).message_id == "real-msg-id"


def test_link_pending_attachments_unknown_id_raises_and_links_nothing(db):
    a1 = _pending("a.png")
    with pytest.raises(AttachmentLinkError) as excinfo:
        att_db.link_pending_attachments([a1.id, "nope"], "m1", **_CTX)
    assert excinfo.value.missing_ids == ["nope"]
    assert att_db.get_attachment_by_id(a1.id).message_id == "pending"


def test_link_pending_attachments_already_linked_raises(db):
    a1 = _pending("a.png")
    att_db.link_pending_attachments([a1.id], "first", **_CTX)
    with pytest.raises(AttachmentLinkError) as excinfo:
        att_db.link_pending_attachments([a1.id], "second", **_CTX)
    assert excinfo.value.missing_ids == [a1.id]
    assert att_db.get_attachment_by_id(a1.id).message_id == "first"


def test_link_pending_attachments_wrong_context_raises(db):
    other_agent = _pending("a.png", context_id="agent-2")
    other_kind = _pending("b.png", context_type="thread")
    with pytest.raises(AttachmentLinkError) as excinfo:
        att_db.link_pending_attachments([other_agent.id, other_kind.id], "m1", **_CTX)
    assert excinfo.value.missing_ids == [other_agent.id, other_kind.id]
    assert att_db.get_attachment_by_id(other_agent.id).message_id == "pending"
    assert att_db.get_attachment_by_id(other_kind.id).message_id == "pending"


def test_get_attachments_for_messages_maps_every_requested_id(db):
    a1 = att_db.create_attachment("m1", "a.png", 1, "image/png", "/a", "image", **_CTX)
    result = att_db.get_attachments_for_messages(["m1", "m2", "m1"])
    assert list(result) == ["m1", "m2"]
    assert [row.id for row in result["m1"]] == [a1.id]
    assert result["m2"] == []


def test_delete_pending_attachment_only_while_pending(db):
    pending = _pending("a.png")
    linked = _pending("b.png")
    att_db.link_pending_attachments([linked.id], "m1", **_CTX)
    assert att_db.delete_pending_attachment(pending.id).id == pending.id
    assert att_db.get_attachment_by_id(pending.id) is None
    assert att_db.delete_pending_attachment(linked.id) is None
    assert att_db.get_attachment_by_id(linked.id) is not None


def test_list_stale_pending_returns_only_old_pending_rows(db):
    old = _pending("old.png")
    fresh = _pending("fresh.png")
    sent = _pending("sent.png")
    att_db.link_pending_attachments([sent.id], "m1", **_CTX)
    from db.connection import get_connection
    get_connection().execute(
        "UPDATE attachments SET created_at = $1 WHERE id IN ($2, $3)",
        [datetime.now(timezone.utc) - timedelta(hours=48), old.id, sent.id],
    )
    stale = att_db.list_stale_pending(datetime.now(timezone.utc) - timedelta(hours=24))
    assert [row.id for row in stale] == [old.id]
    assert fresh.id not in {row.id for row in stale}


def test_fk_cascade_on_message_delete(db):
    """Deleting a message should remove its attachments (app-level cascade)."""
    msg = msg_db.create_message("agent1", None, "hello")
    att_db.create_attachment(msg.id, "a.png", 1, "image/png", "/a", "image", **_CTX)
    att_db.create_attachment(msg.id, "b.png", 1, "image/png", "/b", "image", **_CTX)
    assert len(att_db.get_attachments_for_message(msg.id)) == 2
    # App-level cascade: delete attachments when the message is deleted
    removed = att_db.delete_attachments_for_messages([msg.id])
    assert {row.file_name for row in removed} == {"a.png", "b.png"}
    assert len(att_db.get_attachments_for_message(msg.id)) == 0
