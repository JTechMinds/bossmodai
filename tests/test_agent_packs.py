"""Agent pack schema, pin, import hydrate, trust gate, and export round-trip."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_pack import (
    DEFAULT_CATALOG_PATH,
    DEFAULT_CATALOG_REPO,
    PackImportRequest,
    confirm_token_for,
    export_pack,
    import_pack,
    parse_pack_yaml,
)
from core.agent_pack.github import (
    FLOATING_REFS,
    catalog_location,
    parse_github_pack_url,
    validate_pin_ref,
)
from core.agent_pack.schema import SCHEMA_ID, AgentPackError
from core.agent_loop.role_contracts import suggest_finish_line

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PACK = ROOT / "packs" / "software-engineer.yaml"
PINNED_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TAG_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

VALID_PACK = """
schema: bossmod.agent_pack/v1
specialty: Software Engineer
description: Implements features and fixes in the office workspace.
what_done_looks_like: Tests evidence or a named artifact exists. Empty done does not count.
personality_hint: Software Engineer
tools_hint:
  - cli
  - work
extra_credit: ignored on purpose
"""


@dataclass
class FakePackSource:
    files: dict[tuple[str, str, str, str], str]
    refs: dict[tuple[str, str, str], str]
    resolve_calls: list[tuple[str, str, str]]
    fetch_calls: list[tuple[str, str, str, str]]

    def __init__(self) -> None:
        self.files = {}
        self.refs = {}
        self.resolve_calls = []
        self.fetch_calls = []

    def add(self, *, owner: str, repo: str, path: str, ref: str, sha: str, yaml_text: str) -> None:
        self.refs[(owner.lower(), repo.lower(), ref)] = sha.lower()
        self.files[(owner.lower(), repo.lower(), path, sha.lower())] = yaml_text

    def resolve_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        self.resolve_calls.append((owner, repo, ref))
        key = (owner.lower(), repo.lower(), ref)
        if len(ref) == 40 and all(ch in "0123456789abcdef" for ch in ref.lower()):
            return ref.lower()
        if key not in self.refs:
            raise AgentPackError("ref not found", code="pin_unresolved")
        return self.refs[key]

    def fetch_file(self, owner: str, repo: str, path: str, sha: str) -> str:
        self.fetch_calls.append((owner, repo, path, sha))
        key = (owner.lower(), repo.lower(), path, sha.lower())
        if key not in self.files:
            raise AgentPackError("missing file", code="fetch_failed")
        return self.files[key]


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
    import api.routes.agent_packs as pack_routes

    monkeypatch.setattr(pack_routes, "_SOURCE", source)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _catalog_source(yaml_text: str = VALID_PACK, *, path: str = "packs/software-engineer.yaml") -> FakePackSource:
    source = FakePackSource()
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    source.add(owner=owner, repo=repo, path=path, ref=PINNED_SHA, sha=PINNED_SHA, yaml_text=yaml_text)
    source.add(owner=owner, repo=repo, path=path, ref="v1.0.0", sha=TAG_SHA, yaml_text=yaml_text)
    return source


def test_schema_validates_required_hire_fields() -> None:
    pack = parse_pack_yaml(VALID_PACK)
    assert pack.schema == SCHEMA_ID
    assert pack.specialty == "Software Engineer"
    assert "Implements features" in pack.description
    assert "Tests evidence" in pack.what_done_looks_like
    assert pack.personality_hint == "Software Engineer"
    assert pack.tools_hint == ("cli", "work")
    assert "extra_credit" in pack.ignored_keys
    hire = pack.hire_fields()
    assert hire["role"] == pack.specialty
    assert hire["description"] == pack.description
    assert hire["done_fail_bar"] == pack.what_done_looks_like
    assert "name" not in hire
    assert "desk_x" not in hire


def test_schema_accepts_hire_field_aliases() -> None:
    pack = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
role: Writer
description: Writes first drafts.
done_fail_bar: A named draft exists. Empty done does not count.
"""
    )
    assert pack.specialty == "Writer"
    assert pack.what_done_looks_like.startswith("A named draft")


def test_schema_rejects_missing_and_wrong_schema() -> None:
    with pytest.raises(AgentPackError) as missing:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v1
specialty: Writer
description: Writes drafts.
"""
        )
    assert missing.value.code == "missing_field"
    with pytest.raises(AgentPackError) as schema:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v0
specialty: Writer
description: Writes drafts.
what_done_looks_like: A named draft exists.
"""
        )
    assert schema.value.code == "invalid_schema"


def test_schema_ignores_name_and_unknown_keys() -> None:
    pack = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
name: Should Not Hire This
specialty: Auditor
description: Reviews claims.
what_done_looks_like: A checkable allow/deny exists.
author: catalog
tags:
  - review
"""
    )
    assert pack.specialty == "Auditor"
    assert "name" in pack.ignored_keys
    assert "author" in pack.ignored_keys
    assert "tags" in pack.ignored_keys
    assert "name" not in pack.hire_fields()


@pytest.mark.parametrize(
    "key",
    ["shell", "exec", "script", "api_key", "credentials", "hooks", "on_import"],
)
def test_schema_rejects_dangerous_keys(key: str) -> None:
    raw = f"""
schema: bossmod.agent_pack/v1
specialty: Engineer
description: Builds software.
what_done_looks_like: Tests or an artifact exist.
{key}: rm -rf /
"""
    with pytest.raises(AgentPackError) as exc:
        parse_pack_yaml(raw)
    assert exc.value.code == "dangerous_key"


def test_schema_rejects_nested_dangerous_keys() -> None:
    with pytest.raises(AgentPackError) as exc:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v1
specialty: Engineer
description: Builds software.
what_done_looks_like: Tests or an artifact exist.
metadata:
  shell: echo pwned
"""
        )
    assert exc.value.code == "dangerous_key"


def test_schema_rejects_python_yaml_tags() -> None:
    with pytest.raises(AgentPackError) as exc:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v1
specialty: !!python/object/apply:os.system ["echo pwned"]
description: Builds software.
what_done_looks_like: Tests or an artifact exist.
"""
        )
    assert exc.value.code in {"invalid_yaml", "invalid_schema"}


def test_sample_catalog_pack_validates() -> None:
    pack = parse_pack_yaml(SAMPLE_PACK.read_text(encoding="utf-8"))
    assert pack.schema == SCHEMA_ID
    assert pack.specialty
    assert pack.description
    assert pack.what_done_looks_like


@pytest.mark.parametrize("ref", sorted(FLOATING_REFS))
def test_pin_rejects_floating_refs(ref: str) -> None:
    with pytest.raises(AgentPackError) as exc:
        validate_pin_ref(ref)
    assert exc.value.code == "floating_ref"
    with pytest.raises(AgentPackError) as url_exc:
        parse_github_pack_url(
            f"https://github.com/acme/packs/blob/{ref}/packs/software-engineer.yaml"
        )
    assert url_exc.value.code == "floating_ref"


def test_pin_accepts_commit_sha_and_tag() -> None:
    sha_loc = parse_github_pack_url(
        f"https://github.com/acme/extra/blob/{PINNED_SHA}/roles/writer.yaml"
    )
    assert sha_loc.requested_ref == PINNED_SHA
    assert sha_loc.path == "roles/writer.yaml"
    tag_loc = parse_github_pack_url(
        "https://raw.githubusercontent.com/acme/extra/v1.0.0/roles/writer.yaml"
    )
    assert tag_loc.requested_ref == "v1.0.0"
    catalog = catalog_location(
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        path="software-engineer.yaml",
        ref=PINNED_SHA,
    )
    assert catalog.path == "packs/software-engineer.yaml"
    assert catalog.from_catalog is True


def test_import_hydrates_hire_fields_without_creating_an_agent() -> None:
    source = _catalog_source()
    before = db.list_agents()
    result = import_pack(
        PackImportRequest(path="software-engineer.yaml", ref=PINNED_SHA),
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        extra_allowlist="",
        confirm_secret="test-secret",
    )
    assert result.hire_fields["role"] == "Software Engineer"
    assert "Implements features" in result.hire_fields["description"]
    assert "name" not in result.hire_fields
    assert result.location.commit_sha == PINNED_SHA
    assert db.list_agents() == before
    assert source.fetch_calls
    assert source.fetch_calls[0][3] == PINNED_SHA


def test_import_pins_tag_to_resolved_commit() -> None:
    source = _catalog_source()
    result = import_pack(
        PackImportRequest(path="software-engineer.yaml", ref="v1.0.0"),
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        extra_allowlist="",
        confirm_secret="test-secret",
    )
    assert result.location.requested_ref == "v1.0.0"
    assert result.location.commit_sha == TAG_SHA
    assert source.fetch_calls[0][3] == TAG_SHA


def test_import_rejects_agent_id_so_live_hire_is_not_overwritten() -> None:
    agent = db.create_agent(
        "Existing Hire",
        role="Writer",
        description="Writes drafts.",
        done_fail_bar="A named draft exists.",
    )
    source = _catalog_source()
    with pytest.raises(AgentPackError) as exc:
        import_pack(
            PackImportRequest(
                path="software-engineer.yaml",
                ref=PINNED_SHA,
                agent_id=agent.id,
            ),
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            catalog_path=DEFAULT_CATALOG_PATH,
            extra_allowlist="",
            confirm_secret="test-secret",
        )
    assert exc.value.code == "live_hire_overwrite"
    assert not source.fetch_calls
    persisted = db.get_agent(agent.id)
    assert persisted is not None
    assert persisted.role == "Writer"
    assert persisted.name == "Existing Hire"


def test_trust_gate_fail_closed_without_confirm() -> None:
    source = FakePackSource()
    source.add(
        owner="acme",
        repo="untrusted",
        path="packs/writer.yaml",
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text=VALID_PACK,
    )
    url = f"https://github.com/acme/untrusted/blob/{PINNED_SHA}/packs/writer.yaml"
    with pytest.raises(AgentPackError) as exc:
        import_pack(
            PackImportRequest(url=url),
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            catalog_path=DEFAULT_CATALOG_PATH,
            extra_allowlist="",
            confirm_secret="test-secret",
        )
    assert exc.value.code == "trust_required"
    assert exc.value.status == 403
    assert not source.fetch_calls


def test_trust_gate_allows_confirm_flag_and_confirm_token() -> None:
    source = FakePackSource()
    source.add(
        owner="acme",
        repo="untrusted",
        path="packs/writer.yaml",
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text=VALID_PACK,
    )
    url = f"https://github.com/acme/untrusted/blob/{PINNED_SHA}/packs/writer.yaml"
    flagged = import_pack(
        PackImportRequest(url=url, confirm=True),
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        extra_allowlist="",
        confirm_secret="test-secret",
    )
    assert flagged.pack.specialty == "Software Engineer"
    location = parse_github_pack_url(url)
    token = confirm_token_for(location, "test-secret")
    sourced = import_pack(
        PackImportRequest(url=url, confirm_token=token),
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        extra_allowlist="",
        confirm_secret="test-secret",
    )
    assert sourced.location.commit_sha == PINNED_SHA


def test_trust_gate_allows_allowlisted_repo_without_confirm() -> None:
    source = FakePackSource()
    source.add(
        owner="acme",
        repo="extra",
        path="packs/writer.yaml",
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text=VALID_PACK,
    )
    result = import_pack(
        PackImportRequest(
            url=f"https://github.com/acme/extra/blob/{PINNED_SHA}/packs/writer.yaml"
        ),
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        extra_allowlist="acme/extra",
        confirm_secret="test-secret",
    )
    assert result.pack.specialty == "Software Engineer"


def test_non_github_host_rejected_even_with_confirm() -> None:
    with pytest.raises(AgentPackError) as exc:
        parse_github_pack_url(f"https://evil.example/blob/{PINNED_SHA}/pack.yaml")
    assert exc.value.code == "invalid_source"


def test_export_round_trip_matches_hire_profile_fields() -> None:
    agent = db.create_agent(
        "Operator Named",
        role="QA Engineer",
        description="Checks claims and files.",
        done_fail_bar="A checkable allow/deny exists. Empty done does not count.",
    )
    pack = export_pack(agent)
    assert pack.schema == SCHEMA_ID
    parsed = parse_pack_yaml(pack.to_yaml())
    assert parsed.specialty == agent.role
    assert parsed.description == agent.description
    assert parsed.what_done_looks_like == agent.done_fail_bar
    hire = parsed.hire_fields()
    assert hire["role"] == agent.role
    assert hire["description"] == agent.description
    assert hire["done_fail_bar"] == agent.done_fail_bar
    assert "name" not in hire
    assert hire["role"] != agent.name


def test_export_fills_done_bar_from_specialty_when_blank() -> None:
    agent = db.create_agent(
        "Blank Bar",
        role="Software Engineer",
        description="Implements features.",
    )
    pack = export_pack(agent)
    assert pack.what_done_looks_like == suggest_finish_line(agent.role, agent.description)


def test_api_import_hydrates_then_operator_still_names_hire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _catalog_source()
    client = _client(monkeypatch, source)
    imported = client.post(
        "/api/agent-packs/import",
        headers=_headers(),
        json={"path": "software-engineer.yaml", "ref": PINNED_SHA},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["pin"]["commit_sha"] == PINNED_SHA
    assert body["hire_fields"]["role"] == "Software Engineer"
    assert "name" not in body["hire_fields"]
    hired = client.post(
        "/api/agents",
        headers=_headers(),
        json={
            "name": "Desk Neighbor",
            **{k: v for k, v in body["hire_fields"].items() if k in {"role", "description", "done_fail_bar"}},
        },
    )
    assert hired.status_code == 201, hired.text
    created = hired.json()
    assert created["name"] == "Desk Neighbor"
    assert created["role"] == body["hire_fields"]["role"]
    exported = client.get(f"/api/agents/{created['id']}/pack", headers=_headers())
    assert exported.status_code == 200, exported.text
    pack = exported.json()["pack"]
    assert pack["schema"] == SCHEMA_ID
    assert pack["specialty"] == created["role"]
    assert pack["description"] == created["description"]
    parse_pack_yaml(exported.json()["yaml"])


def test_api_trust_gate_and_live_hire_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakePackSource()
    source.add(
        owner="acme",
        repo="untrusted",
        path="packs/writer.yaml",
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text=VALID_PACK,
    )
    client = _client(monkeypatch, source)
    url = f"https://github.com/acme/untrusted/blob/{PINNED_SHA}/packs/writer.yaml"
    denied = client.post(
        "/api/agent-packs/import",
        headers=_headers(),
        json={"url": url},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "trust_required"
    confirmed = client.post(
        "/api/agent-packs/import",
        headers=_headers(),
        json={"url": url, "confirm": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    agent = db.create_agent("Live", role="Writer", description="Writes.", done_fail_bar="A draft exists.")
    blocked = client.post(
        "/api/agent-packs/import",
        headers=_headers(),
        json={"path": "software-engineer.yaml", "ref": PINNED_SHA, "agent_id": agent.id},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "live_hire_overwrite"


def test_api_rejects_floating_main_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _catalog_source()
    client = _client(monkeypatch, source)
    response = client.post(
        "/api/agent-packs/import",
        headers=_headers(),
        json={
            "url": "https://github.com/JTechMinds/bossmodai/blob/main/packs/software-engineer.yaml"
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "floating_ref"
    assert not source.fetch_calls


def test_seeded_catalog_settings_exist() -> None:
    assert config.get("agent_pack_catalog_repo") == DEFAULT_CATALOG_REPO
    assert config.get("agent_pack_catalog_path") == DEFAULT_CATALOG_PATH
    assert config.get("agent_pack_url_allowlist") is None
