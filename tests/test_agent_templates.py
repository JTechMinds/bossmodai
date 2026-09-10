"""Local agent template library — install, upsert, uninstall, and trust.

Installing a template is the pack import pipeline plus one row. These tests
pin both halves of that sentence: the inherited pin / trust / no-live-hire
rules still hold, and installing never creates an agent.
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
