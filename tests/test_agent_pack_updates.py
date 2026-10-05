"""Catalog updates for installed packs and the agents hired from them.

Covers the pure planner (per-file staleness, withheld / removed / not
installed packs, the edited flag, URL templates left out), the one-transaction
apply, the hire-time link, the desk's per-agent status and update, and the
read-only preview route — all against the fake catalog source shared with the
browse door's tests, so nothing reaches GitHub.
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
    CATALOG_INDEX_PATH,
    CATALOG_PIN_SETTING,
    DEFAULT_CATALOG_REPO,
    AgentPack,
    AgentPackError,
    apply_updates,
    check_updates,
    list_catalog,
    pack_content_hash,
    parse_pack_yaml,
    plan_updates,
)
from tests.test_agent_packs import (
    AUDITOR_PACK,
    AUDITOR_PATH,
    CATALOG_YAML,
    PLANNER_PACK,
    PLANNER_PATH,
    VALID_PACK,
    FakePackSource,
)

OWNER, REPO = DEFAULT_CATALOG_REPO.split("/", 1)
OLD_SHA = "1" * 40
NEW_SHA = "2" * 40
CHANGED_AUDITOR_PACK = AUDITOR_PACK.replace(
    "Reviews claims and named artifacts",
    "Reviews claims, diffs, and named artifacts",
)
NEW_PACK_PATH = "packs/engineering/sw-engineer.agent.yaml"
CATALOG_WITH_NEW_PACK = CATALOG_YAML + (
    "  - id: sw-engineer\n"
    "    kind: agent\n"
    f"    path: {NEW_PACK_PATH}\n"
    "    category: engineering\n"
    "    title: Software Engineer\n"
)
CATALOG_WITHOUT_PLANNER = CATALOG_YAML.split("  - id: feature-planner", 1)[0]
_COMMUNICATION = {"tone": "direct", "density": "compact", "jargon": "light", "audience": "operator"}


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    db.set_setting(CATALOG_PIN_SETTING, OLD_SHA, "agent_packs")
    config.reload()


def teardown_function() -> None:
    db.close_connection()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source(
    new_files: dict[str, str] | None = None,
    *,
    new_index: str = CATALOG_YAML,
) -> FakePackSource:
    """The catalog at OLD_SHA (the pin) and NEW_SHA (HEAD).

    OLD_SHA serves the shipped fixtures. NEW_SHA serves ``new_index`` and,
    per path, ``new_files`` over the same fixtures; a path mapped to ``None``
    is missing at NEW_SHA, so its fetch fails.
    """
    source = FakePackSource()
    old = {AUDITOR_PATH: AUDITOR_PACK, PLANNER_PATH: PLANNER_PACK}
    for path, text in old.items():
        source.add(owner=OWNER, repo=REPO, path=path, ref=OLD_SHA, sha=OLD_SHA, yaml_text=text)
    source.add(owner=OWNER, repo=REPO, path=CATALOG_INDEX_PATH, ref=OLD_SHA, sha=OLD_SHA,
               yaml_text=CATALOG_YAML)
    new = {**old, NEW_PACK_PATH: VALID_PACK, **(new_files or {})}
    for path, text in new.items():
        if text is None:
            continue
        source.add(owner=OWNER, repo=REPO, path=path, ref=NEW_SHA, sha=NEW_SHA, yaml_text=text)
    source.add(owner=OWNER, repo=REPO, path=CATALOG_INDEX_PATH, ref=NEW_SHA, sha=NEW_SHA,
               yaml_text=new_index)
    source.heads[(OWNER.lower(), REPO.lower())] = NEW_SHA
    return source


def _install(pack_id: str, raw: str, *, title: str, category: str = "engineering"):
    """Install a catalog template at OLD_SHA the way the install route does."""
    pack = parse_pack_yaml(raw)
    return db.upsert_agent_template(
        source="catalog",
        pack_id=pack_id,
        source_url=None,
        category=category,
        title=title,
        specialty=pack.specialty,
        description=pack.description,
        what_done_looks_like=pack.what_done_looks_like,
        tools_hint=list(pack.tools_hint),
        communication=pack.communication.as_dict(),
        author_name=None,
        author_url=None,
        commit_sha=OLD_SHA,
        content_hash=pack_content_hash(raw),
    )


def _install_both():
    return (
        _install("code-auditor", AUDITOR_PACK, title="Code Auditor"),
        _install("feature-planner", PLANNER_PACK, title="Feature Planner", category="product"),
    )


def _client(monkeypatch: pytest.MonkeyPatch, source: FakePackSource) -> TestClient:
    import api.routes.agent_packs as pack_routes

    monkeypatch.setattr(pack_routes, "_SOURCE", source)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def _connection_id() -> str:
    existing = db.list_connections()
    if existing:
        return existing[0].id
    return db.create_connection(name="Qwen", api_base_url="http://127.0.0.1:9/v1", model="qwen").id


def _hire(client: TestClient, template, name: str, **overrides) -> dict:
    """Hire through POST /api/agents the way the form does: fields + template_id."""
    body = {
        "name": name,
        "role": template.specialty,
        "description": template.description,
        "done_fail_bar": template.what_done_looks_like,
        "communication": template.communication,
        "connection_id": _connection_id(),
        "template_id": template.id,
    }
    body.update(overrides)
    response = client.post("/api/agents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _pin() -> str:
    """The stored catalog pin, read straight from the settings table."""
    return db.query_one("SELECT value FROM settings WHERE key = $1", [CATALOG_PIN_SETTING])["value"]


def _catalog(source: FakePackSource, sha: str = NEW_SHA):
    return list_catalog(source=source, catalog_repo=DEFAULT_CATALOG_REPO, ref=sha)


# ---------------------------------------------------------------------------
# Content hash: the published file's bytes
# ---------------------------------------------------------------------------

def test_raw_bytes_hash_stays_put_when_the_canonical_form_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    before = {card.entry.id: card.content_hash for card in _catalog(source, OLD_SHA).packs}
    original = AgentPack.as_dict

    def _grown(self):
        return {**original(self), "a_key_this_app_learned_later": "x"}

    monkeypatch.setattr(AgentPack, "as_dict", _grown)
    after = {card.entry.id: card.content_hash for card in _catalog(source, OLD_SHA).packs}
    assert before == after
    assert after["code-auditor"] == pack_content_hash(AUDITOR_PACK)


# ---------------------------------------------------------------------------
# plan_updates
# ---------------------------------------------------------------------------

def test_plan_lists_only_the_changed_pack_and_its_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    client = _client(monkeypatch, source)
    ada = _hire(client, auditor, "Ada")
    _hire(client, planner, "Pat")

    plan = plan_updates(_catalog(source), db.list_agent_templates(), db.list_agents(), OLD_SHA)

    assert plan.target_sha == NEW_SHA and plan.pinned_sha == OLD_SHA
    assert [(row.pack_id, row.template_id, row.from_sha, row.to_sha) for row in plan.templates] == [
        ("code-auditor", auditor.id, OLD_SHA, NEW_SHA),
    ]
    assert [(row.agent_id, row.pack_id, row.edited) for row in plan.agents] == [
        (ada["id"], "code-auditor", False),
    ]
    assert plan.agents[0].template_title == "Code Auditor"
    assert plan.skipped == ()


def test_unchanged_packs_plan_nothing() -> None:
    _install_both()
    plan = plan_updates(_catalog(_source()), db.list_agent_templates(), db.list_agents(), OLD_SHA)
    assert (plan.templates, plan.agents, plan.skipped) == ((), (), ())


def test_withheld_and_removed_packs_are_skipped_with_their_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auditor, planner = _install_both()
    _install("retired-pack", AUDITOR_PACK, title="Retired Pack")
    # Auditor refused at the target, planner gone from the index entirely.
    source = _source({AUDITOR_PATH: "specialty: [unclosed\n"}, new_index=CATALOG_WITHOUT_PLANNER)
    client = _client(monkeypatch, source)
    _hire(client, auditor, "Ada")
    _hire(client, planner, "Pat")

    plan = plan_updates(_catalog(source), db.list_agent_templates(), db.list_agents(), OLD_SHA)

    assert plan.templates == ()
    assert plan.agents == ()
    kinds = {row.pack_id: (row.kind, row.code) for row in plan.skipped}
    assert kinds == {
        "code-auditor": ("refused", "invalid_yaml"),
        "feature-planner": ("removed", "pack_removed"),
        "retired-pack": ("removed", "pack_removed"),
    }


def test_an_unreadable_pack_is_skipped_as_unavailable() -> None:
    _install_both()
    source = _source({AUDITOR_PATH: None})
    plan = plan_updates(_catalog(source), db.list_agent_templates(), db.list_agents(), OLD_SHA)
    assert [(row.pack_id, row.kind, row.code) for row in plan.skipped] == [
        ("code-auditor", "unavailable", "fetch_failed"),
    ]


def test_agents_behind_an_uninstalled_pack_are_reported_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auditor, _planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    _hire(_client(monkeypatch, source), auditor, "Ada")
    assert db.delete_agent_template(auditor.id)

    plan = plan_updates(_catalog(source), db.list_agent_templates(), db.list_agents(), OLD_SHA)

    assert plan.agents == ()
    assert [(row.pack_id, row.kind) for row in plan.skipped] == [("code-auditor", "not_installed")]


def test_edited_flags_an_agent_whose_contract_was_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    client = _client(monkeypatch, source)
    ada = _hire(client, auditor, "Ada")
    bea = _hire(client, auditor, "Bea")
    # Edited in the form before hiring: the link holds the TEMPLATE's hash.
    cy = _hire(client, auditor, "Cy", description="My own words.")
    patched = client.patch(f"/api/agents/{bea['id']}", json={"done_fail_bar": "Something else."})
    assert patched.status_code == 200, patched.text

    plan = plan_updates(_catalog(source), db.list_agent_templates(), db.list_agents(), OLD_SHA)

    edited = {row.agent_id: row.edited for row in plan.agents}
    assert edited == {ada["id"]: False, bea["id"]: True, cy["id"]: True}


def test_url_templates_are_left_out_of_update_all() -> None:
    pack = parse_pack_yaml(AUDITOR_PACK)
    db.upsert_agent_template(
        source="url", pack_id=None, source_url="github.com/acme/x/packs/a.yaml",
        category="imported", title=pack.specialty, specialty=pack.specialty,
        description=pack.description, what_done_looks_like=pack.what_done_looks_like,
        tools_hint=[], communication=None, author_name=None, author_url=None,
        commit_sha=OLD_SHA, content_hash="stale",
    )
    plan = plan_updates(_catalog(_source()), db.list_agent_templates(), db.list_agents(), OLD_SHA)
    assert (plan.templates, plan.agents, plan.skipped) == ((), (), ())


# ---------------------------------------------------------------------------
# apply_updates
# ---------------------------------------------------------------------------

def test_apply_writes_templates_pin_and_agents_together(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    client = _client(monkeypatch, source)
    ada = _hire(client, auditor, "Ada", color="#1d4ed8")
    pat = _hire(client, planner, "Pat")

    plan = apply_updates(source=source, catalog_repo=DEFAULT_CATALOG_REPO,
                         target_sha=NEW_SHA, include_agents=True)

    assert [row.pack_id for row in plan.templates] == ["code-auditor"]
    assert [row.agent_id for row in plan.agents] == [ada["id"]]
    template = db.find_agent_template(pack_id="code-auditor", source_url=None)
    assert template.id == auditor.id
    assert template.commit_sha == NEW_SHA
    assert template.content_hash == pack_content_hash(CHANGED_AUDITOR_PACK)
    assert "diffs" in template.description
    assert _pin() == NEW_SHA
    updated = db.get_agent(ada["id"])
    assert updated.description == template.description
    assert (updated.pack_commit_sha, updated.pack_content_hash) == (NEW_SHA, template.content_hash)
    # Only the contract moves: name, specialty and colour are the operator's.
    assert (updated.name, updated.role, updated.color) == ("Ada", ada["role"], "#1d4ed8")
    untouched = db.get_agent(pat["id"])
    assert untouched.pack_commit_sha == OLD_SHA
    # A second check against the new pin has nothing left to do.
    again = check_updates(source=source, catalog_repo=DEFAULT_CATALOG_REPO, pinned_ref=NEW_SHA)
    assert (again.templates, again.agents, again.skipped) == ((), (), ())


def test_apply_without_agents_leaves_agents_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    ada = _hire(_client(monkeypatch, source), auditor, "Ada")

    plan = apply_updates(source=source, catalog_repo=DEFAULT_CATALOG_REPO,
                         target_sha=NEW_SHA, include_agents=False)

    assert plan.agents == ()
    assert [row.pack_id for row in plan.templates] == ["code-auditor"]
    agent = db.get_agent(ada["id"])
    assert agent.description == auditor.description
    assert agent.pack_commit_sha == OLD_SHA
    # ...and the agent is now behind the installed template, on its desk.
    status = _client(monkeypatch, source).get(f"/api/agents/{ada['id']}/pack-status").json()
    assert status["update_available"] is True


def test_apply_writes_nothing_when_the_catalog_read_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    ada = _hire(_client(monkeypatch, source), auditor, "Ada")
    del source.files[(OWNER.lower(), REPO.lower(), CATALOG_INDEX_PATH, NEW_SHA)]

    with pytest.raises(AgentPackError):
        apply_updates(source=source, catalog_repo=DEFAULT_CATALOG_REPO,
                      target_sha=NEW_SHA, include_agents=True)

    assert _pin() == OLD_SHA
    assert db.find_agent_template(pack_id="code-auditor", source_url=None).commit_sha == OLD_SHA
    assert db.get_agent(ada["id"]).pack_commit_sha == OLD_SHA


def test_a_failed_write_rolls_the_whole_apply_back(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    ada = _hire(_client(monkeypatch, source), auditor, "Ada")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "set_agent_pack_link", _boom)
    with pytest.raises(RuntimeError, match="disk full"):
        apply_updates(source=source, catalog_repo=DEFAULT_CATALOG_REPO,
                      target_sha=NEW_SHA, include_agents=True)

    template = db.find_agent_template(pack_id="code-auditor", source_url=None)
    assert (template.commit_sha, template.content_hash) == (OLD_SHA, auditor.content_hash)
    assert _pin() == OLD_SHA
    agent = db.get_agent(ada["id"])
    assert agent.description == auditor.description
    assert agent.pack_commit_sha == OLD_SHA


def test_apply_refuses_a_target_that_is_not_a_full_sha() -> None:
    with pytest.raises(AgentPackError) as exc:
        apply_updates(source=_source(), catalog_repo=DEFAULT_CATALOG_REPO,
                      target_sha="2222222", include_agents=False)
    assert exc.value.code == "invalid_source"
    assert _pin() == OLD_SHA


# ---------------------------------------------------------------------------
# Routes: preview and apply
# ---------------------------------------------------------------------------

def test_preview_route_reads_only_and_reports_the_review(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    client = _client(monkeypatch, source)
    ada = _hire(client, auditor, "Ada")

    response = client.get("/api/agent-packs/updates")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pinned_short"] == OLD_SHA[:7] and body["target_short"] == NEW_SHA[:7]
    assert body["pin_moves"] is True and body["needs_review"] is True
    assert body["templates"] == [{
        "template_id": auditor.id, "pack_id": "code-auditor", "title": "Code Auditor",
        "from_short": OLD_SHA[:7], "to_short": NEW_SHA[:7],
    }]
    assert body["agents"] == [{
        "agent_id": ada["id"], "name": "Ada", "pack_id": "code-auditor",
        "template_title": "Code Auditor", "from_short": OLD_SHA[:7], "to_short": NEW_SHA[:7],
        "edited": False,
    }]
    assert body["skipped"] == []
    # Read-only: not even the pin moved.
    assert _pin() == OLD_SHA
    assert db.find_agent_template(pack_id="code-auditor", source_url=None).commit_sha == OLD_SHA


def test_only_the_pin_moving_needs_no_review(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_both()
    # HEAD added a pack nobody installed; every installed file is unchanged.
    source = _source(new_index=CATALOG_WITH_NEW_PACK)
    client = _client(monkeypatch, source)

    body = client.get("/api/agent-packs/updates").json()
    assert body["pin_moves"] is True
    assert body["needs_review"] is False

    applied = client.post("/api/agent-packs/updates/apply",
                          json={"target_sha": body["target_sha"], "include_agents": False})
    assert applied.status_code == 200, applied.text
    assert _pin() == NEW_SHA
    after = client.get("/api/agent-packs/updates").json()
    assert after["pin_moves"] is False and after["needs_review"] is False
    details = [row["detail"] for row in db.get_recent_activity_log_entries()]
    assert f"Catalog pin advanced to {NEW_SHA[:7]}" in details


def test_apply_route_reports_what_it_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    client = _client(monkeypatch, source)
    ada = _hire(client, auditor, "Ada")

    applied = client.post("/api/agent-packs/updates/apply",
                          json={"target_sha": NEW_SHA, "include_agents": True})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert [row["pack_id"] for row in body["templates"]] == ["code-auditor"]
    assert [row["agent_id"] for row in body["agents"]] == [ada["id"]]
    assert body["pinned_sha"] == NEW_SHA

    bad = client.post("/api/agent-packs/updates/apply",
                      json={"target_sha": "nope", "include_agents": True})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "invalid_source"


# ---------------------------------------------------------------------------
# Hire-time link
# ---------------------------------------------------------------------------

def test_a_hire_from_a_pack_template_is_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    agent = _hire(_client(monkeypatch, _source()), auditor, "Ada")
    stored = db.get_agent(agent["id"])
    assert (stored.pack_id, stored.pack_source_url) == ("code-auditor", None)
    assert (stored.pack_commit_sha, stored.pack_content_hash) == (OLD_SHA, auditor.content_hash)
    assert stored.pack_contract_hash


def test_a_local_template_links_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    local = db.save_local_template(
        title="Mine", category="custom", specialty="Drafts", description="Writes drafts.",
        what_done_looks_like="A draft exists.", communication=_COMMUNICATION, replace=False,
    )
    agent = _hire(_client(monkeypatch, _source()), local, "Lo")
    stored = db.get_agent(agent["id"])
    assert (stored.pack_id, stored.pack_source_url, stored.pack_commit_sha) == (None, None, None)


def test_an_unknown_template_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _source())
    response = client.post("/api/agents", json={
        "name": "Ada", "connection_id": _connection_id(), "template_id": "missing",
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Template not found"
    assert db.list_agents() == []


def test_patch_cannot_forge_a_pack_link(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _source())
    agent = client.post("/api/agents", json={"name": "Ada", "connection_id": _connection_id()}).json()
    response = client.patch(f"/api/agents/{agent['id']}", json={
        "name": "Ada Two", "pack_id": "code-auditor", "pack_content_hash": "forged",
        "pack_commit_sha": OLD_SHA, "pack_contract_hash": "forged",
    })
    assert response.status_code == 200, response.text
    stored = db.get_agent(agent["id"])
    assert stored.name == "Ada Two"
    assert (stored.pack_id, stored.pack_content_hash, stored.pack_commit_sha) == (None, None, None)


# ---------------------------------------------------------------------------
# Desk: per-agent status and update
# ---------------------------------------------------------------------------

def test_pack_status_and_update_from_the_desk(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    source = _source({AUDITOR_PATH: CHANGED_AUDITOR_PACK})
    client = _client(monkeypatch, source)
    ada = _hire(client, auditor, "Ada")

    current = client.get(f"/api/agents/{ada['id']}/pack-status").json()
    assert current == {
        "linked": True, "pack_id": "code-auditor", "template_id": auditor.id,
        "template_title": "Code Auditor", "installed": True, "current_short": OLD_SHA[:7],
        "available_short": None, "available_content_hash": None,
        "update_available": False, "edited": False,
    }

    apply_updates(source=source, catalog_repo=DEFAULT_CATALOG_REPO,
                  target_sha=NEW_SHA, include_agents=False)
    behind = client.get(f"/api/agents/{ada['id']}/pack-status").json()
    assert behind["update_available"] is True
    assert behind["available_short"] == NEW_SHA[:7]
    new_hash = pack_content_hash(CHANGED_AUDITOR_PACK)
    assert behind["available_content_hash"] == new_hash

    stale = client.post(f"/api/agents/{ada['id']}/pack-update",
                        json={"expected_content_hash": auditor.content_hash})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_template"
    assert db.get_agent(ada["id"]).pack_commit_sha == OLD_SHA

    updated = client.post(f"/api/agents/{ada['id']}/pack-update",
                          json={"expected_content_hash": new_hash})
    assert updated.status_code == 200, updated.text
    assert "diffs" in updated.json()["description"]
    after = client.get(f"/api/agents/{ada['id']}/pack-status").json()
    assert (after["update_available"], after["current_short"], after["edited"]) == (
        False, NEW_SHA[:7], False,
    )


def test_pack_status_for_an_unlinked_agent_and_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    auditor, _planner = _install_both()
    client = _client(monkeypatch, _source())
    plain = client.post("/api/agents", json={"name": "Plain", "connection_id": _connection_id()}).json()

    status = client.get(f"/api/agents/{plain['id']}/pack-status").json()
    assert status["linked"] is False and status["update_available"] is False
    refused = client.post(f"/api/agents/{plain['id']}/pack-update",
                          json={"expected_content_hash": "x"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "not_linked"

    linked = _hire(client, auditor, "Ada")
    assert db.delete_agent_template(auditor.id)
    gone = client.get(f"/api/agents/{linked['id']}/pack-status").json()
    assert (gone["linked"], gone["installed"], gone["update_available"]) == (True, False, False)
    missing = client.post(f"/api/agents/{linked['id']}/pack-update",
                          json={"expected_content_hash": "x"})
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "template_not_installed"

    assert client.get("/api/agents/nobody/pack-status").status_code == 404
