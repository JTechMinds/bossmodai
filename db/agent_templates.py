"""BossMod AI — Installed agent template storage.

One row per installed template. Install and re-install are the same call:
``upsert_agent_template`` looks the row up by its natural key and updates it
or inserts. Nothing here fetches, parses, or trusts a pack — that stays in
``core.agent_pack`` — and nothing here creates an agent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.models.agent_template import AgentTemplate
from db.crud import build_update_returning, execute, fetch_all, fetch_one, insert_returning

_TEMPLATE_COLUMNS = (
    "id, source, pack_id, source_url, category, title, specialty, description, "
    "what_done_looks_like, personality_hint, tools_hint, author_name, author_url, "
    "commit_sha, content_hash, installed_at, updated_at"
)
# The natural key (source, pack_id, source_url) and installed_at identify the
# row and when it entered the library; a re-install refreshes everything else.
_MUTABLE_COLUMNS = {
    "category",
    "title",
    "specialty",
    "description",
    "what_done_looks_like",
    "personality_hint",
    "tools_hint",
    "author_name",
    "author_url",
    "commit_sha",
    "content_hash",
    "updated_at",
}


def list_agent_templates() -> list[AgentTemplate]:
    """Return every installed template, grouped the way the picker renders.

    Ordered by category then title so the picker and the marketplace rail can
    group without re-sorting. Returns an empty list when nothing is installed.
    """
    return fetch_all(
        f"SELECT {_TEMPLATE_COLUMNS} FROM agent_templates ORDER BY category, title",
        [],
        AgentTemplate,
    )


def get_agent_template(template_id: str) -> AgentTemplate | None:
    """Return one installed template by primary key, or ``None`` if absent."""
    return fetch_one(
        f"SELECT {_TEMPLATE_COLUMNS} FROM agent_templates WHERE id = $1",
        [template_id],
        AgentTemplate,
    )


def find_agent_template(
    *,
    pack_id: str | None,
    source_url: str | None,
) -> AgentTemplate | None:
    """Return the installed template for one natural key, or ``None``.

    ``pack_id`` is the key for catalog installs and ``source_url`` for URL
    installs; ``pack_id`` wins when both are given, matching the partial
    indexes (the URL index only covers rows with a NULL ``pack_id``).

    Raises ``ValueError`` when neither key is given — an unkeyed lookup has no
    answer, and returning ``None`` would make every install insert a duplicate.
    """
    if pack_id:
        return fetch_one(
            f"SELECT {_TEMPLATE_COLUMNS} FROM agent_templates WHERE pack_id = $1",
            [pack_id],
            AgentTemplate,
        )
    if source_url:
        return fetch_one(
            f"SELECT {_TEMPLATE_COLUMNS} FROM agent_templates "
            "WHERE source_url = $1 AND pack_id IS NULL",
            [source_url],
            AgentTemplate,
        )
    raise ValueError("find_agent_template needs either a pack_id or a source_url.")


def upsert_agent_template(
    *,
    source: str,
    pack_id: str | None,
    source_url: str | None,
    category: str,
    title: str,
    specialty: str,
    description: str,
    what_done_looks_like: str,
    personality_hint: str | None,
    tools_hint: list[str],
    author_name: str | None,
    author_url: str | None,
    commit_sha: str,
    content_hash: str,
) -> AgentTemplate:
    """Install or re-install one template and return the stored row.

    Select-then-insert-or-update rather than SQL UPSERT: uniqueness is two
    partial indexes, and an ``ON CONFLICT`` target against a partial index has
    to repeat its predicate. Install is a rare operator click, so two plain
    statements are worth more than one clever one.

    ``source`` is ``'catalog'`` or ``'url'``; ``pack_id`` keys the first and
    ``source_url`` the second. ``tools_hint`` is stored as a JSON array in the
    ``TEXT`` column. On an existing row every mutable column plus ``updated_at``
    is refreshed, so a changed pack updates in place instead of duplicating.

    Raises ``ValueError`` when neither natural key is given, and ``RuntimeError``
    if an existing row could not be re-read after its update. A ``source`` value
    outside the two allowed strings is rejected by the column's CHECK
    constraint as ``sqlite3.IntegrityError``.
    """
    if not pack_id and not source_url:
        raise ValueError(
            "An agent template needs a pack_id (catalog install) or a "
            "source_url (URL install) as its natural key."
        )
    encoded_tools = json.dumps(list(tools_hint))
    # One clock read for both columns. The app writes timestamps rather than
    # leaning on the column defaults because SQLite's current_timestamp is
    # second-resolution, which would make installed_at and updated_at
    # incomparable against each other and unorderable between two installs in
    # the same second.
    now = datetime.now(timezone.utc)

    existing = find_agent_template(pack_id=pack_id, source_url=source_url)
    if existing is not None:
        updated = build_update_returning(
            "agent_templates",
            "id",
            existing.id,
            {
                "category": category,
                "title": title,
                "specialty": specialty,
                "description": description,
                "what_done_looks_like": what_done_looks_like,
                "personality_hint": personality_hint,
                "tools_hint": encoded_tools,
                "author_name": author_name,
                "author_url": author_url,
                "commit_sha": commit_sha,
                "content_hash": content_hash,
                "updated_at": now,
            },
            _MUTABLE_COLUMNS,
            _TEMPLATE_COLUMNS,
            AgentTemplate,
        )
        if updated is None:
            raise RuntimeError(
                f"Failed to reload agent template {existing.id} after update"
            )
        return updated

    return insert_returning(
        f"""
        INSERT INTO agent_templates (
            source, pack_id, source_url, category, title, specialty,
            description, what_done_looks_like, personality_hint, tools_hint,
            author_name, author_url, commit_sha, content_hash,
            installed_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        RETURNING {_TEMPLATE_COLUMNS}
        """,
        [
            source,
            pack_id,
            source_url,
            category,
            title,
            specialty,
            description,
            what_done_looks_like,
            personality_hint,
            encoded_tools,
            author_name,
            author_url,
            commit_sha,
            content_hash,
            now,
            now,
        ],
        AgentTemplate,
    )


def delete_agent_template(template_id: str) -> bool:
    """Uninstall one template.

    Returns ``True`` when a row was removed and ``False`` when the id was not
    installed, so the route can answer 404 honestly instead of reporting a
    delete that did nothing.
    """
    if get_agent_template(template_id) is None:
        return False
    execute("DELETE FROM agent_templates WHERE id = $1", [template_id])
    return True
