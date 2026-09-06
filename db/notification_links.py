"""BossMod AI — Structured notification action targets."""

from __future__ import annotations

from core.models import NotificationLink
from db.crud import execute, fetch_all, query_one

_NOTIFICATION_LINK_COLUMNS = "notification_id, target_kind, target_path, label, created_at"


def create_notification_link(
    *,
    notification_id: str,
    target_kind: str,
    target_path: str,
    label: str = "Open in Desk",
) -> NotificationLink:
    """Insert or replace one structured notification link."""
    execute("DELETE FROM notification_links WHERE notification_id = $1", [notification_id])
    links = fetch_all(
        f"""
        INSERT INTO notification_links (notification_id, target_kind, target_path, label)
        VALUES ($1, $2, $3, $4)
        RETURNING {_NOTIFICATION_LINK_COLUMNS}
        """,
        [notification_id, target_kind, target_path, label],
        NotificationLink,
    )
    return links[0]


def list_notification_links(notification_ids: list[str]) -> dict[str, NotificationLink]:
    """Return notification links keyed by notification id."""
    if not notification_ids:
        return {}
    placeholders = ", ".join(f"${index + 1}" for index in range(len(notification_ids)))
    rows = fetch_all(
        f"""
        SELECT {_NOTIFICATION_LINK_COLUMNS}
        FROM notification_links
        WHERE notification_id IN ({placeholders})
        """,
        notification_ids,
        NotificationLink,
    )
    return {item.notification_id: item for item in rows}


def has_consent_notification(consent_id: str) -> bool:
    """Return True when a chat card already exists for this consent request."""
    row = query_one(
        """
        SELECT notification_id FROM notification_links
        WHERE target_kind = 'host_path_consent' AND target_path = $1
        """,
        [consent_id],
    )
    return row is not None
