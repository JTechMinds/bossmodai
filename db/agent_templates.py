"""BossMod AI — Agent template library storage.

One row per template. Two writers, one per kind of row. A pack install and
re-install are the same call: ``upsert_agent_template`` looks the row up by its
natural key (catalog ``pack_id`` or ``source_url``) and updates it or inserts.
The operator's own templates, saved from an agent form, are
``save_local_template``'s: ``source = 'local'``, keyed by title, with no pack,
URL, pin or hash. Nothing here fetches, parses, or trusts a pack — that stays
in ``core.agent_pack`` — and nothing here creates an agent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.agent_loop.communication_contract import dump_communication_json
from core.models.agent_template import AgentTemplate
from db.connection import transaction
from db.crud import build_update_returning, execute, fetch_all, fetch_one, insert_returning

_TEMPLATE_COLUMNS = (
    "id, source, pack_id, source_url, category, title, specialty, description, "
    "what_done_looks_like, personality_hint, tools_hint, communication, "
    "author_name, author_url, "
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
    "communication",
    "author_name",
    "author_url",
    "commit_sha",
    "content_hash",
    "updated_at",
}
# What `upsert_agent_template` installs. `'local'` is the third source and is
# never a pack: save_local_template writes it.
_PACK_SOURCES = ("catalog", "url")
# What saving over a local template may change. Its title is its key, and
# installed_at is when it first entered the library.
_LOCAL_MUTABLE_COLUMNS = {
    "category",
    "specialty",
    "description",
    "what_done_looks_like",
    "personality_hint",
    "communication",
    "updated_at",
}


class LocalTemplateTitleTaken(Exception):
    """A local template with this title exists and the save did not ask to replace it.

    The route answers it as 409 ``local_title_taken``, which the client turns
    into a Replace / Cancel question rather than overwriting silently.
    """

    def __init__(self, title: str) -> None:
        super().__init__(f'A local template named "{title}" already exists.')
        self.title = title


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
    A local template has neither key and is never found here: it is keyed by
    title among local rows, which ``save_local_template`` looks up itself.
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
    communication: dict[str, str] | None = None,
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

    Raises ``ValueError`` when ``source`` is not one of those two — ``'local'``
    included: a local template has no pin and no hash to store, and is
    ``save_local_template``'s — or when neither natural key is given, and
    ``RuntimeError`` if an existing row could not be re-read after its update.
    """
    if source not in _PACK_SOURCES:
        raise ValueError(
            f"upsert_agent_template installs a pack ('catalog' or 'url'), not {source!r}; "
            "a local template is saved with save_local_template."
        )
    if not pack_id and not source_url:
        raise ValueError(
            "An agent template needs a pack_id (catalog install) or a "
            "source_url (URL install) as its natural key."
        )
    encoded_tools = json.dumps(list(tools_hint))
    encoded_communication = dump_communication_json(communication, specialty=specialty)
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
                "communication": encoded_communication,
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
            communication, author_name, author_url, commit_sha, content_hash,
            installed_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
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
            encoded_communication,
            author_name,
            author_url,
            commit_sha,
            content_hash,
            now,
            now,
        ],
        AgentTemplate,
    )


def save_local_template(
    *,
    title: str,
    category: str,
    specialty: str,
    description: str,
    what_done_looks_like: str,
    personality_hint: str | None,
    communication: dict[str, str] | None,
    replace: bool,
) -> AgentTemplate:
    """Save the operator's own template — a role contract from an agent form.

    A local row is keyed by its title among local rows (the
    ``idx_agent_templates_local`` index). With no local template by that title
    it is inserted with ``source = 'local'``, ``tools_hint`` ``[]``, and no
    ``pack_id``, ``source_url``, author, ``commit_sha`` or ``content_hash``:
    it was never fetched. With one, ``replace`` decides: False refuses, True
    updates it in place — same ``id`` and ``installed_at``, refreshed
    ``updated_at`` — so agents recreated from it later read the new contract.
    ``communication`` is stored with ``dump_communication_json`` like the pack
    writer's. The lookup and the write share one transaction, so two saves of
    one title cannot both see it free.

    Args:
        title: The library name, already validated by the caller.
        category: A category slug; the picker and the marketplace group by it.
        specialty: The role. Required: a template fills it.
        description: Required for the same reason.
        what_done_looks_like: May be empty.
        personality_hint: The visible name of a personality, or ``None``.
        communication: The four closed enums, or ``None``.
        replace: Whether an existing local template of this title is replaced.

    Returns:
        The stored row.

    Raises:
        LocalTemplateTitleTaken: When the title is taken and ``replace`` is
            False.
        RuntimeError: When a replaced row could not be re-read.
    """
    encoded_communication = dump_communication_json(communication, specialty=specialty)
    now = datetime.now(timezone.utc)
    with transaction():
        existing = fetch_one(
            f"SELECT {_TEMPLATE_COLUMNS} FROM agent_templates "
            "WHERE source = 'local' AND title = $1",
            [title],
            AgentTemplate,
        )
        if existing is not None:
            if not replace:
                raise LocalTemplateTitleTaken(title)
            updated = build_update_returning(
                "agent_templates",
                "id",
                existing.id,
                {
                    "category": category,
                    "specialty": specialty,
                    "description": description,
                    "what_done_looks_like": what_done_looks_like,
                    "personality_hint": personality_hint,
                    "communication": encoded_communication,
                    "updated_at": now,
                },
                _LOCAL_MUTABLE_COLUMNS,
                _TEMPLATE_COLUMNS,
                AgentTemplate,
            )
            if updated is None:
                raise RuntimeError(
                    f"Failed to reload local template {existing.id} after replacing it"
                )
            return updated
        return insert_returning(
            f"""
            INSERT INTO agent_templates (
                source, pack_id, source_url, category, title, specialty,
                description, what_done_looks_like, personality_hint, tools_hint,
                communication, author_name, author_url, commit_sha, content_hash,
                installed_at, updated_at
            ) VALUES ('local', NULL, NULL, $1, $2, $3, $4, $5, $6, '[]', $7,
                      NULL, NULL, NULL, NULL, $8, $9)
            RETURNING {_TEMPLATE_COLUMNS}
            """,
            [
                category,
                title,
                specialty,
                description,
                what_done_looks_like,
                personality_hint,
                encoded_communication,
                now,
                now,
            ],
            AgentTemplate,
        )


def delete_agent_template(template_id: str) -> bool:
    """Remove one template from the library: an uninstall, or a local delete.

    Returns ``True`` when a row was removed and ``False`` when the id was not
    in the library, so the route can answer 404 honestly instead of reporting
    a delete that did nothing.
    """
    if get_agent_template(template_id) is None:
        return False
    execute("DELETE FROM agent_templates WHERE id = $1", [template_id])
    return True
