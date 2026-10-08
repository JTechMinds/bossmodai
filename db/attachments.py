"""BossMod AI — Attachment CRUD."""

from __future__ import annotations

from datetime import datetime

from core.models import Attachment
from db.crud import execute, fetch_all, insert_returning

_ATTACHMENT_COLUMNS = (
    "id, message_id, file_name, file_size, mime_type, "
    "storage_path, preview_tier, context_type, context_id, created_at"
)

# Uploads carry this message id until a send links them to a real message.
PENDING_MESSAGE_ID = "pending"


class AttachmentLinkError(Exception):
    """Attachments could not be linked to a message; nothing was linked.

    ``missing_ids`` lists the requested ids that are unknown, already linked
    to a message, or were uploaded for a different conversation. It is empty
    when the request was refused as a whole (for example, too many files).
    """

    def __init__(self, message: str, *, missing_ids: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing_ids: list[str] = list(missing_ids) if missing_ids is not None else []


def _placeholders(count: int, *, start: int = 1) -> str:
    return ", ".join(f"${start + i}" for i in range(count))


def create_attachment(
    message_id: str,
    file_name: str,
    file_size: int,
    mime_type: str,
    storage_path: str,
    preview_tier: str,
    *,
    context_type: str,
    context_id: str,
) -> Attachment:
    """Insert a new attachment row and return it.

    Args:
        message_id: ``PENDING_MESSAGE_ID`` for a fresh upload, or a message id.
        file_name: The sanitized display name.
        file_size: Size in bytes.
        mime_type: Detected MIME type.
        storage_path: Absolute path of the stored file.
        preview_tier: ``image``, ``text``, ``document`` or ``other``.
        context_type: ``direct`` or ``thread``: the conversation it was uploaded for.
        context_id: The agent id (``direct``) or channel id (``thread``).

    Returns:
        The stored row.

    Raises:
        sqlite3.IntegrityError: A CHECK constraint rejected the tier or context type.
    """
    return insert_returning(
        f"""
        INSERT INTO attachments (message_id, file_name, file_size, mime_type,
                                 storage_path, preview_tier, context_type, context_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING {_ATTACHMENT_COLUMNS}
        """,
        [message_id, file_name, file_size, mime_type, storage_path, preview_tier,
         context_type, context_id],
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


def get_attachments_for_messages(message_ids: list[str]) -> dict[str, list[Attachment]]:
    """Return the attachments of many messages in one query.

    Args:
        message_ids: Message ids to look up. Duplicates are fine.

    Returns:
        A map with one entry per requested id, each in insertion order. A
        message without attachments maps to an empty list, so callers index
        it directly instead of guessing at absence.
    """
    unique = list(dict.fromkeys(message_ids))
    result: dict[str, list[Attachment]] = {message_id: [] for message_id in unique}
    if not unique:
        return result
    rows = fetch_all(
        f"""
        SELECT {_ATTACHMENT_COLUMNS} FROM attachments
        WHERE message_id IN ({_placeholders(len(unique))})
        ORDER BY created_at ASC
        """,
        unique,
        Attachment,
    )
    for row in rows:
        result[row.message_id].append(row)
    return result


def get_attachment_by_id(attachment_id: str) -> Attachment | None:
    """Return a single attachment by its id, or None if not found."""
    rows = fetch_all(
        f"SELECT {_ATTACHMENT_COLUMNS} FROM attachments WHERE id = $1",
        [attachment_id],
        Attachment,
    )
    return rows[0] if rows else None


def link_pending_attachments(
    attachment_ids: list[str],
    message_id: str,
    *,
    context_type: str,
    context_id: str,
) -> list[Attachment]:
    """Link pending uploads of one conversation to a message, all or nothing.

    Only rows that are still pending AND were uploaded for this same
    conversation are linked. The update runs under a savepoint, so it is one
    unit on its own and nests inside a caller's open transaction (the message
    insert), which is how ``core.messaging`` keeps a refused link from leaving
    an orphan message behind.

    Args:
        attachment_ids: Ids from the upload responses. Duplicates collapse.
        message_id: The message the files belong to.
        context_type: ``direct`` or ``thread``.
        context_id: The agent id or channel id the message was posted to.

    Returns:
        The linked rows, in the order the ids were given.

    Raises:
        AttachmentLinkError: Any id is unknown, already linked, or belongs to
            another conversation. No row is changed.
    """
    unique = list(dict.fromkeys(attachment_ids))
    if not unique:
        return []
    execute("SAVEPOINT link_pending_attachments")
    try:
        rows = fetch_all(
            f"""
            UPDATE attachments SET message_id = $1
            WHERE id IN ({_placeholders(len(unique), start=4)})
              AND message_id = $2
              AND context_type = $3
              AND context_id = ${len(unique) + 4}
            RETURNING {_ATTACHMENT_COLUMNS}
            """,
            [message_id, PENDING_MESSAGE_ID, context_type, *unique, context_id],
            Attachment,
        )
        linked = {row.id: row for row in rows}
        missing = [attachment_id for attachment_id in unique if attachment_id not in linked]
        if missing:
            raise AttachmentLinkError(
                "Some attachments are unknown, already sent, or belong to another conversation.",
                missing_ids=missing,
            )
    except BaseException:
        execute("ROLLBACK TO SAVEPOINT link_pending_attachments")
        execute("RELEASE SAVEPOINT link_pending_attachments")
        raise
    execute("RELEASE SAVEPOINT link_pending_attachments")
    return [linked[attachment_id] for attachment_id in unique]


def delete_pending_attachment(attachment_id: str) -> Attachment | None:
    """Delete one attachment row only while it is still pending.

    Args:
        attachment_id: The upload's id.

    Returns:
        The deleted row, so the caller can remove its file, or None when no
        pending row has this id (unknown, or already linked to a message).
    """
    rows = fetch_all(
        f"""
        DELETE FROM attachments
        WHERE id = $1 AND message_id = $2
        RETURNING {_ATTACHMENT_COLUMNS}
        """,
        [attachment_id, PENDING_MESSAGE_ID],
        Attachment,
    )
    return rows[0] if rows else None


def list_stale_pending(older_than: datetime) -> list[Attachment]:
    """Return pending uploads created before ``older_than``, oldest first."""
    return fetch_all(
        f"""
        SELECT {_ATTACHMENT_COLUMNS} FROM attachments
        WHERE message_id = $1 AND created_at < $2
        ORDER BY created_at ASC
        """,
        [PENDING_MESSAGE_ID, older_than],
        Attachment,
    )


def delete_attachments_for_messages(message_ids: list[str]) -> list[Attachment]:
    """Remove every attachment row of the given messages and return the rows.

    The rows come back so the caller can remove their files once its
    transaction has committed; this touches the database only.

    Args:
        message_ids: Message ids whose attachments are removed. Duplicates are
            fine. An empty list deletes nothing and runs no query.

    Returns:
        The deleted rows, in no particular order.
    """
    unique = list(dict.fromkeys(message_ids))
    if not unique:
        return []
    return fetch_all(
        f"""
        DELETE FROM attachments
        WHERE message_id IN ({_placeholders(len(unique))})
        RETURNING {_ATTACHMENT_COLUMNS}
        """,
        unique,
        Attachment,
    )
