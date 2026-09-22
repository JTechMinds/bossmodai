"""Local agent template library — install, upsert, uninstall, trust, and local.

Installing a template is the pack import pipeline plus one row. These tests
pin both halves of that sentence: the inherited pin / trust / no-live-hire
rules still hold, and installing never creates an agent. The operator's own
templates (``source = 'local'``) are pinned at the bottom, with the migration
that taught an existing library to hold them.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_pack import (
    DEFAULT_CATALOG_PIN,
    DEFAULT_CATALOG_REPO,
    describe_pack,
    pack_content_hash,
    parse_pack_yaml,
)
from core.models.agent_template import AgentTemplate

# The pack fetch double and the catalog fixtures are shared with the browse
# door's tests. One fake source, one set of fixture packs — a second copy would
# drift from the pipeline both suites exercise.
from tests.test_agent_packs import (
    AUDITOR_PACK,
    AUDITOR_PATH,
    PINNED_SHA,
    VALID_PACK,
    FakePackSource,
    _catalog_source,
)

# Same pack file, one clause added to Mission: still schema- and quality-valid,
# so it exercises "the pack changed" rather than "the pack broke".
CHANGED_AUDITOR_PACK = AUDITOR_PACK.replace(
    "Reviews claims and named artifacts",
    "Reviews claims, diffs, and named artifacts",
)
URL_PACK_SHA = "cccccccccccccccccccccccccccccccccccccccc"
UNTRUSTED_URL = f"https://github.com/acme/untrusted/blob/{PINNED_SHA}/packs/writer.yaml"
UNTRUSTED_URL_NEW_PIN = (
    f"https://github.com/acme/untrusted/blob/{URL_PACK_SHA}/packs/writer.yaml"
)
UNTRUSTED_IDENTITY = "github.com/acme/untrusted/packs/writer.yaml"
SCHEMA = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text(
    encoding="utf-8"
)


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _client(monkeypatch: pytest.MonkeyPatch, source: FakePackSource) -> TestClient:
    import api.routes.agent_templates as template_routes

    monkeypatch.setattr(template_routes, "_SOURCE", source)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _url_source(*refs: str) -> FakePackSource:
    """A source serving one pack from a repo that is not the catalog repo."""
    source = FakePackSource()
    for ref in refs:
        source.add(
            owner="acme",
            repo="untrusted",
            path="packs/writer.yaml",
            ref=ref,
            sha=ref,
            yaml_text=VALID_PACK,
        )
    return source


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def test_install_creates_one_row_and_never_an_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, _catalog_source())
    response = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor", "ref": PINNED_SHA},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "catalog"
    assert body["pack_id"] == "code-auditor"
    assert body["source_url"] is None
    assert body["category"] == "engineering"
    assert body["title"] == "Code Auditor"
    assert body["specialty"] == "Code Auditor"
    assert "Reviews claims" in body["description"]
    assert "Fail examples:" in body["what_done_looks_like"]
    assert body["tools_hint"] == ["work"]
    assert body["author_name"] == "JTech Minds"
    assert body["author_url"] == "https://github.com/JTechMinds"
    assert body["commit_sha"] == PINNED_SHA
    assert body["content_hash"] == pack_content_hash(parse_pack_yaml(AUDITOR_PACK))

    installed = db.list_agent_templates()
    assert len(installed) == 1
    assert installed[0].id == body["id"]
    # Installing is a library write, never a hire.
    assert db.list_agents() == []


def test_install_without_ref_uses_the_configured_catalog_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _catalog_source()
    client = _client(monkeypatch, source)
    response = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor"},
    )
    assert response.status_code == 200, response.text
    assert source.resolve_calls[0][2] == DEFAULT_CATALOG_PIN
    assert response.json()["commit_sha"] == PINNED_SHA


def test_install_without_id_or_url_is_rejected_before_any_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _catalog_source()
    client = _client(monkeypatch, source)
    response = client.post("/api/agent-templates", headers=_headers(), json={})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_source"
    assert not source.fetch_calls
    assert db.list_agent_templates() == []


def test_installing_the_same_pack_twice_updates_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, _catalog_source())
    first = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor", "ref": PINNED_SHA},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor", "ref": PINNED_SHA},
    )
    assert second.status_code == 200, second.text

    rows = db.list_agent_templates()
    assert len(rows) == 1
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["content_hash"] == first.json()["content_hash"]
    assert db.list_agents() == []


def test_changed_pack_updates_content_hash_and_updated_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, _catalog_source())
    first = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor", "ref": PINNED_SHA},
    )
    assert first.status_code == 200, first.text
    before = db.list_agent_templates()[0]

    changed_client = _client(monkeypatch, _catalog_source(CHANGED_AUDITOR_PACK))
    second = changed_client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor", "ref": PINNED_SHA},
    )
    assert second.status_code == 200, second.text

    rows = db.list_agent_templates()
    assert len(rows) == 1
    after = rows[0]
    assert after.id == before.id
    assert after.content_hash != before.content_hash
    assert after.content_hash == pack_content_hash(parse_pack_yaml(CHANGED_AUDITOR_PACK))
    assert after.updated_at > before.updated_at
    assert after.installed_at == before.installed_at
    assert "diffs" in after.description


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

def test_uninstall_removes_the_row_then_404s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, _catalog_source())
    installed = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor", "ref": PINNED_SHA},
    )
    assert installed.status_code == 200, installed.text
    template_id = installed.json()["id"]

    removed = client.delete(f"/api/agent-templates/{template_id}", headers=_headers())
    assert removed.status_code == 204, removed.text
    assert db.list_agent_templates() == []

    again = client.delete(f"/api/agent-templates/{template_id}", headers=_headers())
    assert again.status_code == 404


# ---------------------------------------------------------------------------
# URL installs — trust gate and the ref-free natural key
# ---------------------------------------------------------------------------

def test_url_install_needs_trust_then_succeeds_with_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _url_source(PINNED_SHA)
    client = _client(monkeypatch, source)
    denied = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"url": UNTRUSTED_URL},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "trust_required"
    assert not source.fetch_calls
    assert db.list_agent_templates() == []

    confirmed = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"url": UNTRUSTED_URL, "confirm": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["source"] == "url"
    assert body["pack_id"] is None
    assert body["source_url"] == UNTRUSTED_IDENTITY
    assert body["category"] == "imported"
    # A URL install has no catalog row, so the pack's specialty is its title.
    assert body["title"] == "Software Engineer"
    assert body["commit_sha"] == PINNED_SHA
    assert len(db.list_agent_templates()) == 1
    assert db.list_agents() == []


def test_url_reinstall_at_a_new_pin_updates_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _url_source(PINNED_SHA, URL_PACK_SHA)
    client = _client(monkeypatch, source)
    first = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"url": UNTRUSTED_URL, "confirm": True},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"url": UNTRUSTED_URL_NEW_PIN, "confirm": True},
    )
    assert second.status_code == 200, second.text

    rows = db.list_agent_templates()
    assert len(rows) == 1
    assert rows[0].id == first.json()["id"]
    assert rows[0].commit_sha == URL_PACK_SHA


# ---------------------------------------------------------------------------
# Inherited refusals
# ---------------------------------------------------------------------------

def test_agent_id_in_the_body_is_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent(
        "Existing Hire",
        role="Writer",
        description="Writes drafts.",
        done_fail_bar="A named draft exists.",
    )
    source = _catalog_source()
    client = _client(monkeypatch, source)
    blocked = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor", "ref": PINNED_SHA, "agent_id": agent.id},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "live_hire_overwrite"
    assert not source.fetch_calls
    assert db.list_agent_templates() == []
    persisted = db.get_agent(agent.id)
    assert persisted is not None
    assert persisted.role == "Writer"


def test_floating_ref_is_refused_without_installing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _catalog_source()
    client = _client(monkeypatch, source)
    response = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={
            "url": (
                "https://github.com/JTechMinds/BossMod_AgentMP/blob/main/"
                f"{AUDITOR_PATH}"
            )
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "floating_ref"
    assert not source.fetch_calls
    assert db.list_agent_templates() == []


# ---------------------------------------------------------------------------
# List endpoint and catalog cards
# ---------------------------------------------------------------------------

def test_list_returns_installed_templates_grouped_by_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, _catalog_source())
    assert client.get("/api/agent-templates", headers=_headers()).json() == []
    for pack_id in ("feature-planner", "code-auditor"):
        installed = client.post(
            "/api/agent-templates",
            headers=_headers(),
            json={"id": pack_id, "ref": PINNED_SHA},
        )
        assert installed.status_code == 200, installed.text

    listed = client.get("/api/agent-templates", headers=_headers())
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [row["category"] for row in rows] == ["engineering", "product"]
    assert [row["pack_id"] for row in rows] == ["code-auditor", "feature-planner"]
    assert db.list_agents() == []


def test_catalog_cards_carry_description_and_content_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.routes.agent_packs as pack_routes

    source = _catalog_source()
    monkeypatch.setattr(pack_routes, "_SOURCE", source)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    client = TestClient(app)

    listed = client.get("/api/agent-packs", headers=_headers())
    assert listed.status_code == 200, listed.text
    card = listed.json()["categories"][0]["packs"][0]
    assert card["id"] == "code-auditor"
    assert "Reviews claims" in card["description"]
    assert card["content_hash"] == pack_content_hash(parse_pack_yaml(AUDITOR_PACK))


def test_installed_rows_carry_parsed_sections_without_storing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The library row is read structured, and stored flat.

    ``description`` and ``what_done_looks_like`` are what fills the create
    form, so they stay the row's source of truth; the split is derived on read
    from the same parser the quality gate uses.
    """
    client = _client(monkeypatch, _catalog_source())
    installed = client.post(
        "/api/agent-templates",
        headers=_headers(),
        json={"id": "code-auditor", "ref": PINNED_SHA},
    )
    assert installed.status_code == 200, installed.text

    listed = client.get("/api/agent-templates", headers=_headers())
    assert listed.status_code == 200, listed.text
    row = listed.json()[0]
    assert set(row["sections"]) == {"description", "done"}
    assert set(row["sections"]["description"]) == {
        "preamble",
        "mission",
        "in_scope",
        "out_of_scope",
        "handoff",
    }
    assert set(row["sections"]["done"]) == {"preamble", "fail_examples"}
    assert row["sections"] == describe_pack(
        row["description"], row["what_done_looks_like"]
    )
    assert row["sections"]["description"]["mission"].startswith("Reviews claims")
    assert row["sections"]["done"]["fail_examples"].startswith('"Looks good"')
    # Install returns the same model, so it carries the same derived split.
    assert installed.json()["sections"] == row["sections"]
    # The flat hire strings the create form reads are untouched.
    assert "Mission:" in row["description"]
    assert "Fail examples:" in row["what_done_looks_like"]

    # Derived on read means exactly that: no column holds the parsed form.
    table = SCHEMA.split("CREATE TABLE IF NOT EXISTS agent_templates (", 1)[1]
    assert "sections" not in table.split(");", 1)[0]


# ---------------------------------------------------------------------------
# Storage and model invariants
# ---------------------------------------------------------------------------

def _upsert_kwargs(**overrides) -> dict:
    base = dict(
        source="catalog",
        pack_id="code-auditor",
        source_url=None,
        category="engineering",
        title="Code Auditor",
        specialty="Code Auditor",
        description="Mission: reviews claims.",
        what_done_looks_like="A checkable allow/deny exists.",
        personality_hint=None,
        tools_hint=["work", "cli"],
        author_name="JTech Minds",
        author_url="https://github.com/JTechMinds",
        commit_sha=PINNED_SHA,
        content_hash="0" * 64,
    )
    base.update(overrides)
    return base


def test_upsert_round_trips_tools_hint_and_finds_by_natural_key() -> None:
    stored = db.upsert_agent_template(**_upsert_kwargs())
    assert stored.tools_hint == ["work", "cli"]
    found = db.find_agent_template(pack_id="code-auditor", source_url=None)
    assert found is not None
    assert found.id == stored.id
    assert db.get_agent_template(stored.id) is not None
    assert db.delete_agent_template(stored.id) is True
    assert db.delete_agent_template(stored.id) is False


def test_upsert_and_find_refuse_a_row_with_no_natural_key() -> None:
    with pytest.raises(ValueError):
        db.upsert_agent_template(**_upsert_kwargs(pack_id=None, source_url=None))
    with pytest.raises(ValueError):
        db.find_agent_template(pack_id=None, source_url=None)


def test_malformed_tools_hint_is_surfaced_not_defaulted() -> None:
    stored = db.upsert_agent_template(**_upsert_kwargs())
    db.execute(
        "UPDATE agent_templates SET tools_hint = $1 WHERE id = $2",
        ["{not json", stored.id],
    )
    with pytest.raises(Exception) as corrupt:
        db.list_agent_templates()
    assert "tools_hint" in str(corrupt.value)

    with pytest.raises(Exception) as wrong_shape:
        AgentTemplate.model_validate({**stored.model_dump(), "tools_hint": "[1, 2]"})
    assert "tools_hint" in str(wrong_shape.value)


def test_pack_content_hash_is_content_addressed() -> None:
    original = pack_content_hash(parse_pack_yaml(AUDITOR_PACK))
    assert original == pack_content_hash(parse_pack_yaml(AUDITOR_PACK))
    assert original != pack_content_hash(parse_pack_yaml(CHANGED_AUDITOR_PACK))
    assert len(original) == 64


def test_catalog_repo_default_is_the_one_installs_read() -> None:
    assert config.get("agent_pack_catalog_repo") == DEFAULT_CATALOG_REPO


# ---------------------------------------------------------------------------
# Migration: the library learns to hold local templates
# ---------------------------------------------------------------------------

# The agent_templates DDL as it shipped before local templates, verbatim, plus
# the two indexes that stood on it. `communication` is present or absent: a
# library older than that column is the case that proves the rebuild runs
# AFTER the column is added, since the copy names every column.
_OLD_TEMPLATE_COLUMNS = """
    id                   VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    source               VARCHAR NOT NULL CHECK (source IN ('catalog', 'url')),
    pack_id              VARCHAR,
    source_url           TEXT,
    category             VARCHAR NOT NULL,
    title                VARCHAR NOT NULL,
    specialty            TEXT NOT NULL,
    description          TEXT NOT NULL,
    what_done_looks_like TEXT NOT NULL,
    personality_hint     VARCHAR,
    tools_hint           TEXT NOT NULL DEFAULT '[]',
    communication        TEXT,
    author_name          VARCHAR,
    author_url           TEXT,
    commit_sha           VARCHAR NOT NULL,
    content_hash         VARCHAR NOT NULL,
    installed_at         TIMESTAMP DEFAULT current_timestamp,
    updated_at           TIMESTAMP DEFAULT current_timestamp
"""
_OLD_TEMPLATE_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_templates_pack
    ON agent_templates(pack_id) WHERE pack_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_templates_url
    ON agent_templates(source_url) WHERE pack_id IS NULL;
"""
_OLD_CATALOG_ROW = {
    "id": "old-catalog-row",
    "source": "catalog",
    "pack_id": "code-auditor",
    "source_url": None,
    "category": "engineering",
    "title": "Code Auditor",
    "specialty": "Code Auditor",
    "description": "Mission: reviews claims.",
    "what_done_looks_like": "A checkable allow/deny exists.",
    "personality_hint": "Software Engineer",
    "tools_hint": '["work"]',
    "communication": None,
    "author_name": "JTech Minds",
    "author_url": "https://github.com/JTechMinds",
    "commit_sha": PINNED_SHA,
    "content_hash": "0" * 64,
    "installed_at": "2026-09-01 00:00:00+00:00",
    "updated_at": "2026-09-02 00:00:00+00:00",
}
_REBUILT = "Migration: rebuilt agent_templates to allow local templates"


def _build_old_library(*, with_communication: bool) -> None:
    """Replace the test database with one holding only the OLD library table.

    Written with a raw connection, as the app of that era would have left it,
    at the temp path conftest points BOSSMOD_DB_PATH at — never the repo's.
    """
    import sqlite3
    import uuid

    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    columns = _OLD_TEMPLATE_COLUMNS
    row = dict(_OLD_CATALOG_ROW)
    if not with_communication:
        columns = columns.replace("    communication        TEXT,\n", "")
        del row["communication"]
    raw = sqlite3.connect(db_path)
    try:
        raw.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))
        raw.executescript(
            f"CREATE TABLE agent_templates ({columns});\n{_OLD_TEMPLATE_INDEXES}"
        )
        names = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        raw.execute(f"INSERT INTO agent_templates ({names}) VALUES ({marks})", list(row.values()))
        raw.commit()
    finally:
        raw.close()


def _library_state() -> tuple[list, list[dict]]:
    """The library table's DDL and index DDL, and every row as stored."""
    con = db.get_connection()
    ddl = con.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE tbl_name = 'agent_templates' ORDER BY type, name"
    ).fetchall()
    rows = db.query(
        "SELECT id, source, pack_id, source_url, category, title, specialty, description, "
        "what_done_looks_like, personality_hint, tools_hint, communication, author_name, "
        "author_url, commit_sha, content_hash, CAST(installed_at AS TEXT) AS installed_at, "
        "CAST(updated_at AS TEXT) AS updated_at FROM agent_templates ORDER BY id"
    )
    return [tuple(entry) for entry in ddl], rows


@pytest.mark.parametrize("with_communication", [True, False])
def test_the_library_rebuild_keeps_every_row_and_every_index(
    with_communication: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    """Spec §3.5: an old library gains `local`, loses nothing, keeps its indexes.

    init_db applies schema.sql before the migrations, so the indexes it made
    were on the old table and went with it; the rebuild must put all three
    back. It must also be a no-op the second time: the app runs it at every
    start.
    """
    _build_old_library(with_communication=with_communication)
    with caplog.at_level("INFO", logger="db.connection"):
        db.init_db()
    assert _REBUILT in caplog.text

    _ddl, rows = _library_state()
    expected = dict(_OLD_CATALOG_ROW)
    if not with_communication:
        # Added empty by the column migration that runs first.
        expected["communication"] = None
    assert rows == [expected]

    table_sql = db.query(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agent_templates'"
    )[0]["sql"]
    assert "'local'" in table_sql
    assert "commit_sha           VARCHAR NOT NULL" not in table_sql
    indexes = {
        row["name"] for row in db.query(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'agent_templates'"
        )
    }
    assert {
        "idx_agent_templates_pack", "idx_agent_templates_url", "idx_agent_templates_local",
    } <= indexes
    assert not db.query(
        "SELECT name FROM sqlite_master WHERE name = 'agent_templates__new'"
    )

    # A local row inserts: no pack, no URL, no pin, no hash.
    db.execute(
        "INSERT INTO agent_templates (source, category, title, specialty, description, "
        "what_done_looks_like) VALUES ($1, $2, $3, $4, $5, $6)",
        ["local", "custom", "Mine", "Drafts", "Writes drafts.", ""],
    )
    stored = db.list_agent_templates()
    local = [row for row in stored if row.source == "local"]
    assert len(local) == 1
    assert local[0].commit_sha is None and local[0].content_hash is None
    # ...and the recreated local index holds: one local template per title.
    with pytest.raises(Exception) as duplicate:
        db.execute(
            "INSERT INTO agent_templates (source, category, title, specialty, description, "
            "what_done_looks_like) VALUES ($1, $2, $3, $4, $5, $6)",
            ["local", "custom", "Mine", "Again", "Twice.", ""],
        )
    assert "UNIQUE" in str(duplicate.value)
    # The recreated pack and URL indexes hold too.
    keyed = (
        "INSERT INTO agent_templates (source, pack_id, source_url, category, title, "
        "specialty, description, what_done_looks_like, commit_sha, content_hash) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)"
    )
    db.execute(keyed, ["url", None, "github.com/a/b/c.yaml", "imported", "U", "S", "D", "W",
                       PINNED_SHA, "h"])
    for params in (
        ["catalog", "code-auditor", None, "engineering", "Dup", "S", "D", "W", PINNED_SHA, "h"],
        ["url", None, "github.com/a/b/c.yaml", "imported", "U2", "S", "D", "W", PINNED_SHA, "h"],
    ):
        with pytest.raises(Exception) as twice:
            db.execute(keyed, params)
        assert "UNIQUE" in str(twice.value), params

    # A second start is a no-op: same DDL, same rows, and no rebuild.
    before = _library_state()
    caplog.clear()
    with caplog.at_level("INFO", logger="db.connection"):
        db.init_db()
    assert _REBUILT not in caplog.text
    assert _library_state() == before


def test_the_library_checks_hold_both_halves_of_the_local_rule() -> None:
    """A local row has no pin and no hash; a pack row must have both."""
    insert = (
        "INSERT INTO agent_templates (source, pack_id, source_url, category, title, "
        "specialty, description, what_done_looks_like, commit_sha, content_hash) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)"
    )
    refused = [
        # local with a pin, with a hash, with a pack key, with a URL key
        ["local", None, None, "custom", "A", "S", "D", "", PINNED_SHA, None],
        ["local", None, None, "custom", "B", "S", "D", "", None, "h"],
        ["local", "code-auditor", None, "custom", "C", "S", "D", "", None, None],
        ["local", None, "github.com/a/b/c.yaml", "custom", "D", "S", "D", "", None, None],
        # a catalog row missing its pin, and missing its hash
        ["catalog", "p1", None, "engineering", "E", "S", "D", "", None, "h"],
        ["catalog", "p2", None, "engineering", "F", "S", "D", "", PINNED_SHA, None],
    ]
    for params in refused:
        with pytest.raises(Exception) as rejected:
            db.execute(insert, params)
        assert "CHECK" in str(rejected.value), params
    assert db.list_agent_templates() == []


# ---------------------------------------------------------------------------
# Local templates: the operator's own role contract, saved to the library
# ---------------------------------------------------------------------------

def _local_body(**overrides) -> dict:
    body = {
        "title": "Release Notes Writer",
        "category": "custom",
        "specialty": "Writes release notes",
        "description": "Turns a merged PR list into notes an operator can read.",
        "what_done_looks_like": "A dated notes file exists.",
        "personality_hint": "Software Engineer",
        "communication": {"tone": "direct", "density": "compact", "jargon": "light",
                          "audience": "operator"},
    }
    body.update(overrides)
    return body


def test_a_local_template_is_saved_without_a_pack_behind_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, FakePackSource())
    response = client.post("/api/agent-templates/local", headers=_headers(), json=_local_body(
        title="  Release Notes Writer  ",
    ))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"] == "local"
    assert body["title"] == "Release Notes Writer"
    assert body["category"] == "custom"
    assert body["specialty"] == "Writes release notes"
    assert body["what_done_looks_like"] == "A dated notes file exists."
    assert body["personality_hint"] == "Software Engineer"
    assert body["communication"] == _local_body()["communication"]
    assert body["tools_hint"] == []
    for absent in ("pack_id", "source_url", "author_name", "author_url",
                   "commit_sha", "content_hash"):
        assert body[absent] is None, absent
    # Read back by the same list the picker and the marketplace read.
    listed = client.get("/api/agent-templates", headers=_headers()).json()
    assert [row["id"] for row in listed] == [body["id"]]
    assert listed[0]["sections"]["description"]["preamble"].startswith("Turns a merged")
    # A library write, never a hire.
    assert db.list_agents() == []


def test_a_taken_local_title_is_a_409_the_client_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, FakePackSource())
    first = client.post("/api/agent-templates/local", headers=_headers(), json=_local_body())
    assert first.status_code == 201, first.text
    again = client.post("/api/agent-templates/local", headers=_headers(), json=_local_body(
        specialty="Something else",
    ))
    assert again.status_code == 409, again.text
    detail = again.json()["detail"]
    assert detail["code"] == "local_title_taken"
    assert "Release Notes Writer" in detail["message"]
    # Refused means unchanged.
    rows = db.list_agent_templates()
    assert len(rows) == 1 and rows[0].specialty == "Writes release notes"


def test_replace_updates_the_local_template_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, FakePackSource())
    first = client.post("/api/agent-templates/local", headers=_headers(),
                        json=_local_body()).json()
    replaced = client.post("/api/agent-templates/local", headers=_headers(), json=_local_body(
        category="writing", specialty="Edits release notes", description="Tightens them.",
        what_done_looks_like="", personality_hint="", replace=True,
    ))
    assert replaced.status_code == 201, replaced.text
    body = replaced.json()
    assert body["id"] == first["id"]
    assert body["installed_at"] == first["installed_at"]
    assert body["updated_at"] > first["updated_at"]
    assert (body["category"], body["specialty"], body["description"]) == (
        "writing", "Edits release notes", "Tightens them.",
    )
    assert body["what_done_looks_like"] == ""
    assert body["personality_hint"] is None
    assert len(db.list_agent_templates()) == 1
    # replace on a title nobody holds is an ordinary insert.
    fresh = client.post("/api/agent-templates/local", headers=_headers(), json=_local_body(
        title="Another", replace=True,
    ))
    assert fresh.status_code == 201, fresh.text
    assert len(db.list_agent_templates()) == 2


def test_a_local_title_does_not_collide_with_an_installed_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Titles are a key among LOCAL templates only; a pack keeps its own key."""
    client = _client(monkeypatch, _catalog_source())
    installed = client.post("/api/agent-templates", headers=_headers(),
                            json={"id": "code-auditor", "ref": PINNED_SHA})
    assert installed.status_code == 200, installed.text
    local = client.post("/api/agent-templates/local", headers=_headers(),
                        json=_local_body(title="Code Auditor"))
    assert local.status_code == 201, local.text
    assert sorted(row.source for row in db.list_agent_templates()) == ["catalog", "local"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"category": "Not A Slug"}, "category"),
        ({"category": "trailing-"}, "category"),
        ({"specialty": "   "}, "specialty"),
        ({"description": ""}, "description"),
        ({"title": "  "}, "title"),
        ({"title": "x" * 121}, "title"),
        ({"communication": {"tone": "an essay about tone"}}, "communication"),
    ],
)
def test_a_local_template_body_is_validated(
    monkeypatch: pytest.MonkeyPatch, overrides: dict, field: str,
) -> None:
    client = _client(monkeypatch, FakePackSource())
    response = client.post("/api/agent-templates/local", headers=_headers(),
                           json=_local_body(**overrides))
    assert response.status_code == 422, response.text
    assert any(field in error["loc"] for error in response.json()["detail"]), response.text
    assert db.list_agent_templates() == []


def test_a_local_template_is_deleted_by_the_same_route_as_an_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, FakePackSource())
    saved = client.post("/api/agent-templates/local", headers=_headers(),
                        json=_local_body()).json()
    first = client.delete(f"/api/agent-templates/{saved['id']}", headers=_headers())
    assert first.status_code == 204, first.text
    assert db.list_agent_templates() == []
    second = client.delete(f"/api/agent-templates/{saved['id']}", headers=_headers())
    assert second.status_code == 404, second.text


def test_the_pack_writer_refuses_a_local_row() -> None:
    """A local template has no pin and no hash; the pack upsert must say so."""
    with pytest.raises(ValueError) as refused:
        db.upsert_agent_template(**_upsert_kwargs(source="local"))
    assert "save_local_template" in str(refused.value)
    assert db.list_agent_templates() == []
