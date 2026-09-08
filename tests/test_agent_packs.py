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
    CATALOG_INDEX_PATH,
    DEFAULT_CATALOG_PATH,
    DEFAULT_CATALOG_REPO,
    PackImportRequest,
    confirm_token_for,
    export_pack,
    import_pack,
    parse_pack_yaml,
    validate_pack_quality,
)
from core.agent_pack.catalog import parse_catalog_yaml, resolve_catalog_entry, validate_catalog_pack_path
from core.agent_pack.github import (
    FLOATING_REFS,
    catalog_pack_location,
    parse_github_pack_url,
    validate_pin_ref,
)
from core.agent_pack.schema import PACK_KIND_AGENT, SCHEMA_ID, AgentPackError
from core.agent_loop.role_contracts import suggest_finish_line

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "agent_mp"
AUDITOR_PATH = "packs/engineering/code-auditor.agent.yaml"
PLANNER_PATH = "packs/product/feature-planner.agent.yaml"
CATALOG_YAML = (FIXTURES / "catalog.yaml").read_text(encoding="utf-8")
AUDITOR_PACK = (FIXTURES / AUDITOR_PATH).read_text(encoding="utf-8")
PLANNER_PACK = (FIXTURES / PLANNER_PATH).read_text(encoding="utf-8")
PINNED_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TAG_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

THIN_PACK = """
schema: bossmod.agent_pack/v1
kind: agent
specialty: Software Engineer
description: Implements features and fixes in the office workspace.
what_done_looks_like: Tests evidence or a named artifact exists. Empty done does not count.
personality_hint: Software Engineer
tools_hint:
  - cli
  - work
extra_credit: ignored on purpose
"""

VALID_PACK = """
schema: bossmod.agent_pack/v1
kind: agent
pack_author:
  name: Northwind
  url: https://northwind.example
specialty: Software Engineer
description: |
  Mission: Implements features and fixes against a checkable bar in the office workspace.
  In scope: Named files, tests, and desk artifacts the operator can open.
  Out of scope: Production deploys, credential hunting, and host-wide scans.
  Handoff: The operator or a reviewer receives the named artifact plus test evidence.
what_done_looks_like: |
  Tests evidence or a named artifact exists. Empty done does not count.
  Fail examples: "Looks good" with no path; a vibe check; done with zero evidence.
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


def _catalog_source(pack_yaml: str | None = None, *, pack_path: str = AUDITOR_PATH) -> FakePackSource:
    source = FakePackSource()
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    pack_text = AUDITOR_PACK if pack_yaml is None else pack_yaml
    for ref, sha in ((PINNED_SHA, PINNED_SHA), ("v1.0.0", TAG_SHA)):
        source.add(owner=owner, repo=repo, path=CATALOG_INDEX_PATH, ref=ref, sha=sha, yaml_text=CATALOG_YAML)
        source.add(
            owner=owner,
            repo=repo,
            path=AUDITOR_PATH,
            ref=ref,
            sha=sha,
            yaml_text=pack_text if pack_path == AUDITOR_PATH else AUDITOR_PACK,
        )
        source.add(
            owner=owner,
            repo=repo,
            path=PLANNER_PATH,
            ref=ref,
            sha=sha,
            yaml_text=pack_text if pack_path == PLANNER_PATH else PLANNER_PACK,
        )
    return source


def test_schema_validates_required_hire_fields() -> None:
    pack = parse_pack_yaml(THIN_PACK)
    assert pack.schema == SCHEMA_ID
    assert pack.kind == PACK_KIND_AGENT
    assert pack.specialty == "Software Engineer"
    assert "Implements features" in pack.description
    assert "Tests evidence" in pack.what_done_looks_like
    assert pack.personality_hint == "Software Engineer"
    assert pack.tools_hint == ("cli", "work")
    assert pack.pack_author is None
    assert "extra_credit" in pack.ignored_keys
    hire = pack.hire_fields()
    assert hire["role"] == pack.specialty
    assert hire["description"] == pack.description
    assert hire["done_fail_bar"] == pack.what_done_looks_like
    assert "name" not in hire
    assert "kind" not in hire
    assert "desk_x" not in hire
    assert "pack_author" not in hire


def test_schema_defaults_missing_kind_to_agent() -> None:
    pack = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists. Empty done does not count.
"""
    )
    assert pack.kind == PACK_KIND_AGENT


@pytest.mark.parametrize("kind", ["skill", "workflow"])
def test_schema_rejects_reserved_non_agent_kinds(kind: str) -> None:
    with pytest.raises(AgentPackError) as exc:
        parse_pack_yaml(
            f"""
schema: bossmod.agent_pack/v1
kind: {kind}
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists.
"""
        )
    assert exc.value.code == "unsupported_kind"


def test_schema_rejects_unknown_kind() -> None:
    with pytest.raises(AgentPackError) as exc:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v1
kind: plugin
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists.
"""
        )
    assert exc.value.code == "invalid_schema"


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
    assert pack.pack_author is None


def test_schema_accepts_pack_author_name_and_optional_url() -> None:
    pack = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
pack_author:
  name: Northwind
  url: https://northwind.example
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists. Empty done does not count.
"""
    )
    assert pack.pack_author is not None
    assert pack.pack_author.name == "Northwind"
    assert pack.pack_author.url == "https://northwind.example"
    assert pack.as_dict()["pack_author"] == {
        "name": "Northwind",
        "url": "https://northwind.example",
    }
    assert "pack_author" not in pack.hire_fields()


def test_schema_ignores_unknown_pack_author_keys() -> None:
    pack = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
pack_author:
  name: Northwind
  twitter: not-stored
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists. Empty done does not count.
"""
    )
    assert pack.pack_author is not None
    assert pack.pack_author.name == "Northwind"
    assert "pack_author.twitter" in pack.ignored_keys


def test_schema_accepts_pack_author_name_only() -> None:
    pack = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
pack_author:
  name: Northwind
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists. Empty done does not count.
"""
    )
    assert pack.pack_author is not None
    assert pack.pack_author.name == "Northwind"
    assert pack.pack_author.url is None
    assert pack.as_dict()["pack_author"] == {"name": "Northwind"}


@pytest.mark.parametrize(
    "author_yaml",
    [
        "pack_author: Northwind",
        "pack_author: []",
        "pack_author: 3",
        "pack_author:\n  url: https://northwind.example",
        "pack_author:\n  name: ''",
        "pack_author:\n  name: Northwind\n  url: not-a-url",
        "pack_author:\n  name: Northwind\n  url: javascript:alert(1)",
        "pack_author:\n  name: Northwind\n  url: 12",
    ],
)
def test_schema_rejects_bad_pack_author_shapes(author_yaml: str) -> None:
    raw = f"""
schema: bossmod.agent_pack/v1
{author_yaml}
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists. Empty done does not count.
"""
    with pytest.raises(AgentPackError) as exc:
        parse_pack_yaml(raw)
    assert exc.value.code == "invalid_schema"


def test_schema_rejects_tools_hint_prose() -> None:
    with pytest.raises(AgentPackError) as prose:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v1
specialty: Engineer
description: Builds software.
what_done_looks_like: Tests or an artifact exist.
tools_hint: use the cli and work tools to finish the job
"""
        )
    assert prose.value.code == "invalid_schema"
    with pytest.raises(AgentPackError) as listed:
        parse_pack_yaml(
            """
schema: bossmod.agent_pack/v1
specialty: Engineer
description: Builds software.
what_done_looks_like: Tests or an artifact exist.
tools_hint:
  - use the cli tool for everything
"""
        )
    assert listed.value.code == "invalid_schema"


def test_quality_accepts_labeled_senior_sections() -> None:
    pack = parse_pack_yaml(VALID_PACK)
    validate_pack_quality(pack)
    assert "Mission:" in pack.description
    assert "In scope:" in pack.description
    assert "Out of scope:" in pack.description
    assert "Handoff:" in pack.description
    assert "Fail examples:" in pack.what_done_looks_like
    assert pack.pack_author is not None
    assert pack.pack_author.name == "Northwind"


def test_quality_folds_additive_fields_into_hire_hydrate() -> None:
    pack = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
specialty: Writer
description: Extra office context for drafts.
mission: Writes first drafts that a reviewer can open as a named file in this workspace.
in_scope: Named drafts, outlines, and edit passes the operator can read from the desk.
out_of_scope: Publishing, production deploys, and sending mail on behalf of the operator.
handoff: A reviewer or the operator receives the named draft and the finish line.
what_done_looks_like: A named draft exists and can be opened. Empty done does not count.
fail_examples: '"Looks good" with no file; a vibe paragraph; done with no named draft.'
"""
    )
    validate_pack_quality(pack)
    hire = pack.hire_fields()
    assert "Mission:" in hire["description"]
    assert "In scope:" in hire["description"]
    assert "Handoff:" in hire["description"]
    assert "Fail examples:" in hire["done_fail_bar"]
    assert "pack_author" not in hire


def test_quality_rejects_one_liner_and_missing_fail_examples() -> None:
    thin = parse_pack_yaml(THIN_PACK)
    with pytest.raises(AgentPackError) as missing:
        validate_pack_quality(thin)
    assert missing.value.code == "pack_quality"
    no_fail = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
specialty: Writer
description: |
  Mission: Writes first drafts that a reviewer can open as a named file in this workspace.
  In scope: Named drafts, outlines, and edit passes the operator can read from the desk.
  Out of scope: Publishing, production deploys, and sending mail on behalf of the operator.
  Handoff: A reviewer or the operator receives the named draft and the finish line.
what_done_looks_like: A named draft exists. Empty done does not count.
"""
    )
    with pytest.raises(AgentPackError) as fail_exc:
        validate_pack_quality(no_fail)
    assert fail_exc.value.code == "pack_quality"
    assert "Fail examples" in str(fail_exc.value)


def test_quality_rejects_short_mission() -> None:
    pack = parse_pack_yaml(
        """
schema: bossmod.agent_pack/v1
specialty: Writer
description: |
  Mission: Writes drafts.
  In scope: Named drafts, outlines, and edit passes the operator can read from the desk.
  Out of scope: Publishing, production deploys, and sending mail on behalf of the operator.
  Handoff: A reviewer or the operator receives the named draft and the finish line.
what_done_looks_like: |
  A named draft exists and can be opened. Empty done does not count.
  Fail examples: "Looks good" with no file; a vibe paragraph; done with no named draft.
"""
    )
    with pytest.raises(AgentPackError) as exc:
        validate_pack_quality(pack)
    assert exc.value.code == "pack_quality"
    assert "Mission" in str(exc.value)


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


def test_sample_catalog_fixture_validates() -> None:
    index = parse_catalog_yaml(CATALOG_YAML)
    assert [entry.id for entry in index.entries] == ["code-auditor", "feature-planner"]
    auditor = resolve_catalog_entry(index, pack_id="code-auditor")
    assert auditor.path == AUDITOR_PATH
    assert auditor.category == "engineering"
    assert auditor.kind == PACK_KIND_AGENT
    path, category, pack_id = validate_catalog_pack_path(auditor.path)
    assert path == AUDITOR_PATH
    assert category == auditor.category
    assert pack_id == auditor.id
    pack = parse_pack_yaml(AUDITOR_PACK)
    assert pack.schema == SCHEMA_ID
    assert pack.kind == PACK_KIND_AGENT
    assert pack.pack_author is not None
    assert pack.pack_author.name == "JTech Minds"
    validate_pack_quality(pack)
    planner = parse_pack_yaml(PLANNER_PACK)
    assert planner.specialty == "Feature Planner"
    validate_pack_quality(planner)


def test_catalog_rejects_category_folder_mismatch() -> None:
    with pytest.raises(AgentPackError) as exc:
        parse_catalog_yaml(
            """
packs:
  - id: code-auditor
    kind: agent
    path: packs/engineering/code-auditor.agent.yaml
    category: product
    title: Code Auditor
"""
        )
    assert exc.value.code == "catalog_category_mismatch"


def test_catalog_rejects_profiles_wrapper_and_display_name_folder() -> None:
    with pytest.raises(AgentPackError) as profiles:
        parse_catalog_yaml(
            """
packs:
  - id: code-auditor
    kind: agent
    path: packs/Profiles/engineering/code-auditor.agent.yaml
    category: engineering
    title: Code Auditor
"""
        )
    assert profiles.value.code == "invalid_catalog"
    with pytest.raises(AgentPackError) as display:
        parse_catalog_yaml(
            """
packs:
  - id: code-auditor
    kind: agent
    path: packs/Engineering/code-auditor.agent.yaml
    category: Engineering
    title: Code Auditor
"""
        )
    assert display.value.code == "invalid_catalog"


@pytest.mark.parametrize("ref", sorted(FLOATING_REFS))
def test_pin_rejects_floating_refs(ref: str) -> None:
    with pytest.raises(AgentPackError) as exc:
        validate_pin_ref(ref)
    assert exc.value.code == "floating_ref"
    with pytest.raises(AgentPackError) as url_exc:
        parse_github_pack_url(
            f"https://github.com/acme/packs/blob/{ref}/packs/engineering/code-auditor.agent.yaml"
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
    catalog = catalog_pack_location(
        catalog_repo=DEFAULT_CATALOG_REPO,
        path=AUDITOR_PATH,
        ref=PINNED_SHA,
    )
    assert catalog.path == AUDITOR_PATH
    assert catalog.from_catalog is True


def test_import_rejects_skill_kind_without_installing() -> None:
    source = _catalog_source(
        """
schema: bossmod.agent_pack/v1
kind: skill
specialty: Writer
description: Writes first drafts.
what_done_looks_like: A named draft exists.
"""
    )
    before = db.list_agents()
    with pytest.raises(AgentPackError) as exc:
        import_pack(
            PackImportRequest(pack_id="code-auditor", ref=PINNED_SHA),
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            catalog_path=DEFAULT_CATALOG_PATH,
            extra_allowlist="",
            confirm_secret="test-secret",
        )
    assert exc.value.code == "unsupported_kind"
    assert db.list_agents() == before


def test_import_hydrates_hire_fields_without_creating_an_agent() -> None:
    source = _catalog_source()
    before = db.list_agents()
    result = import_pack(
        PackImportRequest(pack_id="code-auditor", ref=PINNED_SHA),
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        extra_allowlist="",
        confirm_secret="test-secret",
    )
    assert result.hire_fields["role"] == "Code Auditor"
    assert "Reviews claims" in result.hire_fields["description"]
    assert "Fail examples:" in result.hire_fields["done_fail_bar"]
    assert "name" not in result.hire_fields
    assert "pack_author" not in result.hire_fields
    assert result.pack.pack_author is not None
    assert result.pack.pack_author.name == "JTech Minds"
    assert result.location.commit_sha == PINNED_SHA
    assert result.location.path == AUDITOR_PATH
    assert result.catalog_entry is not None
    assert result.catalog_entry.id == "code-auditor"
    assert result.catalog_entry.category == "engineering"
    assert db.list_agents() == before
    assert source.fetch_calls[0][2] == CATALOG_INDEX_PATH
    assert source.fetch_calls[1][2] == AUDITOR_PATH
    assert source.fetch_calls[0][3] == PINNED_SHA


def test_import_rejects_thin_pack_without_creating_an_agent() -> None:
    source = _catalog_source(THIN_PACK)
    before = db.list_agents()
    with pytest.raises(AgentPackError) as exc:
        import_pack(
            PackImportRequest(pack_id="code-auditor", ref=PINNED_SHA),
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            catalog_path=DEFAULT_CATALOG_PATH,
            extra_allowlist="",
            confirm_secret="test-secret",
        )
    assert exc.value.code == "pack_quality"
    assert db.list_agents() == before


def test_import_pins_tag_to_resolved_commit() -> None:
    source = _catalog_source()
    result = import_pack(
        PackImportRequest(pack_id="code-auditor", ref="v1.0.0"),
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        extra_allowlist="",
        confirm_secret="test-secret",
    )
    assert result.location.requested_ref == "v1.0.0"
    assert result.location.commit_sha == TAG_SHA
    assert source.fetch_calls[0][2] == CATALOG_INDEX_PATH
    assert source.fetch_calls[0][3] == TAG_SHA
    assert source.fetch_calls[1][3] == TAG_SHA


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
                pack_id="code-auditor",
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
    assert pack.kind == PACK_KIND_AGENT
    parsed = parse_pack_yaml(pack.to_yaml())
    assert parsed.kind == PACK_KIND_AGENT
    assert parsed.specialty == agent.role
    assert parsed.description == agent.description
    assert parsed.what_done_looks_like == agent.done_fail_bar
    hire = parsed.hire_fields()
    assert hire["role"] == agent.role
    assert hire["description"] == agent.description
    assert hire["done_fail_bar"] == agent.done_fail_bar
    assert "name" not in hire
    assert "pack_author" not in hire
    assert hire["role"] != agent.name
    assert parsed.pack_author is None


def test_export_fills_pack_author_from_company_and_round_trips() -> None:
    agent = db.create_agent(
        "Operator Named",
        role="QA Engineer",
        description="Checks claims and files.",
        done_fail_bar="A checkable allow/deny exists. Empty done does not count.",
    )
    pack = export_pack(
        agent,
        company_name="Northwind",
        company_url="https://northwind.example",
    )
    assert pack.pack_author is not None
    assert pack.pack_author.name == "Northwind"
    assert pack.pack_author.url == "https://northwind.example"
    parsed = parse_pack_yaml(pack.to_yaml())
    assert parsed.pack_author is not None
    assert parsed.pack_author.name == "Northwind"
    assert parsed.pack_author.url == "https://northwind.example"
    assert "pack_author" not in parsed.hire_fields()


def test_export_omits_pack_author_when_company_unknown() -> None:
    agent = db.create_agent(
        "Operator Named",
        role="QA Engineer",
        description="Checks claims and files.",
        done_fail_bar="A checkable allow/deny exists. Empty done does not count.",
    )
    pack = export_pack(agent, company_name="", company_url="https://northwind.example")
    assert pack.pack_author is None
    assert "pack_author" not in pack.as_dict()


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
        json={"id": "code-auditor", "ref": PINNED_SHA},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["pin"]["commit_sha"] == PINNED_SHA
    assert body["catalog"]["id"] == "code-auditor"
    assert body["catalog"]["category"] == "engineering"
    assert body["hire_fields"]["role"] == "Code Auditor"
    assert "name" not in body["hire_fields"]
    assert "pack_author" not in body["hire_fields"]
    assert body["pack"]["pack_author"]["name"] == "JTech Minds"
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
    assert pack["kind"] == PACK_KIND_AGENT
    assert pack["specialty"] == created["role"]
    assert pack["description"] == created["description"]
    parsed = parse_pack_yaml(exported.json()["yaml"])
    validate_pack_quality(parsed)
    assert "pack_author" not in pack


def test_api_export_fills_pack_author_from_company_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _catalog_source()
    client = _client(monkeypatch, source)
    agent = db.create_agent(
        "Desk Neighbor",
        role="Code Auditor",
        description="Reviews claims.",
        done_fail_bar="A checkable allow/deny exists.",
    )
    db.set_setting("company_name", "Northwind", "general")
    db.set_setting("company_url", "https://northwind.example", "general")
    config.reload()
    exported = client.get(f"/api/agents/{agent.id}/pack", headers=_headers())
    assert exported.status_code == 200, exported.text
    body = exported.json()["pack"]
    assert body["pack_author"] == {
        "name": "Northwind",
        "url": "https://northwind.example",
    }
    parsed = parse_pack_yaml(exported.json()["yaml"])
    assert parsed.pack_author is not None
    assert parsed.pack_author.name == "Northwind"


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
        json={"id": "code-auditor", "ref": PINNED_SHA, "agent_id": agent.id},
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
            "url": "https://github.com/JTechMinds/BossMod_AgentMP/blob/main/packs/engineering/code-auditor.agent.yaml"
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "floating_ref"
    assert not source.fetch_calls


def test_seeded_catalog_settings_exist() -> None:
    assert config.get("agent_pack_catalog_repo") == DEFAULT_CATALOG_REPO
    assert config.get("agent_pack_catalog_path") == DEFAULT_CATALOG_PATH
    assert config.get("agent_pack_url_allowlist") is None


def test_catalog_import_by_listed_path_and_unknown_id() -> None:
    source = _catalog_source()
    listed = import_pack(
        PackImportRequest(path=AUDITOR_PATH, ref=PINNED_SHA),
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        catalog_path=DEFAULT_CATALOG_PATH,
        extra_allowlist="",
        confirm_secret="test-secret",
    )
    assert listed.catalog_entry is not None
    assert listed.catalog_entry.id == "code-auditor"
    with pytest.raises(AgentPackError) as missing:
        import_pack(
            PackImportRequest(pack_id="not-a-pack", ref=PINNED_SHA),
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            catalog_path=DEFAULT_CATALOG_PATH,
            extra_allowlist="",
            confirm_secret="test-secret",
        )
    assert missing.value.code == "catalog_miss"
    with pytest.raises(AgentPackError) as unlisted:
        import_pack(
            PackImportRequest(path="packs/engineering/missing.agent.yaml", ref=PINNED_SHA),
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            catalog_path=DEFAULT_CATALOG_PATH,
            extra_allowlist="",
            confirm_secret="test-secret",
        )
    assert unlisted.value.code == "catalog_miss"
