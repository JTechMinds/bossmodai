"""BossMod AI — Attachment CRUD."""

from __future__ import annotations

from core.models import Attachment
from db.crud import execute, fetch_all, insert_returning, query_one

_ATTACHMENT_COLUMNS = (
    "id, message_id, file_name, file_size, mime_type, "
    "storage_path, preview_tier, created_at"
)


def create_attachment(
    message_id: str,
    file_name: str,
    file_size: int,
    mime_type: str,
    storage_path: str,
    preview_tier: str,
) -> Attachment:
    """Insert a new attachment row and return it."""
    return insert_returning(
        f"""
        INSERT INTO attachments (message_id, file_name, file_size, mime_type,
                                 storage_path, preview_tier)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING {_ATTACHMENT_COLUMNS}
        """,
        [message_id, file_name, file_size, mime_type, storage_path, preview_tier],
        Attachment,
    )


def get_attachments_for_message(message_id: str) -> list[Attachment]:
    """Return all attachments for a message in insertion order."""
    return fetch_all(
        f"""
        SELECT {_ATTACHMENT_COLUMNS} FROM attachments
        WHERE message_id = $1
        ORDER BY created_at ASC
        """,
        [message_id],
        Attachment,
    )


def get_attachment_by_id(attachment_id: str) -> Attachment | None:
    """Return a single attachment by its id, or None if not found."""
    rows = fetch_all(
        f"SELECT {_ATTACHMENT_COLUMNS} FROM attachments WHERE id = $1",
        [attachment_id],
        Attachment,
    )
    return rows[0] if rows else None


def link_attachments_to_message(attachment_ids: list[str], message_id: str) -> int:
    """Update message_id for a batch of attachment ids. Returns rows updated."""
    if not attachment_ids:
        return 0
    placeholders = ", ".join(f"${i + 1}" for i in range(len(attachment_ids)))
    sql = f"""
    UPDATE attachments SET message_id = ${len(attachment_ids) + 1}
    WHERE id IN ({placeholders})
    """
    params = attachment_ids + [message_id]
    con = __import__("db.connection", fromlist=["get_connection"]).get_connection()
    result = con.execute(sql, params)
    return result.rowcount


def delete_attachments_for_message(message_id: str) -> int:
    """Remove all attachment rows for a message. Returns rows deleted."""
    con = __import__("db.connection", fromlist=["get_connection"]).get_connection()
    result = con.execute(
        "DELETE FROM attachments WHERE message_id = $1",
        [message_id],
    )
    return result.rowcount
