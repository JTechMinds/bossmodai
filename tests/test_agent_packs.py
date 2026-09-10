"""Agent pack schema, pin, import hydrate, trust gate, and export round-trip."""

from __future__ import annotations

import logging
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
    DEFAULT_CATALOG_PIN,
    DEFAULT_CATALOG_REPO,
    WITHHELD_REFUSED,
    WITHHELD_UNAVAILABLE,
    PackImportRequest,
    confirm_token_for,
    describe_pack,
    export_pack,
    import_pack,
    list_catalog,
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

UNPARSEABLE_PACK = "specialty: [unclosed\n"

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
    for ref, sha in (
        (PINNED_SHA, PINNED_SHA),
        ("v1.0.0", TAG_SHA),
        (DEFAULT_CATALOG_PIN, PINNED_SHA),
    ):
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
    assert auditor.summary == "Hire when work claims done and needs evidence-backed CLEAR."
    planner = resolve_catalog_entry(index, pack_id="feature-planner")
    assert planner.summary == "Hire when you need product cuts / sequencing, not code."
    path, category, pack_id = validate_catalog_pack_path(auditor.path)
    assert path == AUDITOR_PATH
    assert category == auditor.category
    assert pack_id == auditor.id
    pack = parse_pack_yaml(AUDITOR_PACK)
    assert pack.schema == SCHEMA_ID
    assert pack.kind == PACK_KIND_AGENT
    assert pack.pack_author is not None
    assert pack.pack_author.name == "JTech Minds"
    assert pack.description.startswith(
        "Hire when work claims done and needs evidence-backed CLEAR."
    )
    validate_pack_quality(pack)
    planner_pack = parse_pack_yaml(PLANNER_PACK)
    assert planner_pack.specialty == "Feature Planner"
    assert planner_pack.description.startswith(
        "Hire when you need product cuts / sequencing, not code."
    )
    validate_pack_quality(planner_pack)


def test_catalog_allows_missing_summary() -> None:
    index = parse_catalog_yaml(
        """
packs:
  - id: code-auditor
    kind: agent
    path: packs/engineering/code-auditor.agent.yaml
    category: engineering
    title: Code Auditor
"""
    )
    assert index.entries[0].summary is None


def test_catalog_rejects_non_string_or_long_summary() -> None:
    with pytest.raises(AgentPackError) as not_text:
        parse_catalog_yaml(
            """
packs:
  - id: code-auditor
    kind: agent
    path: packs/engineering/code-auditor.agent.yaml
    category: engineering
    title: Code Auditor
    summary: ["not", "a", "line"]
"""
        )
    assert not_text.value.code == "invalid_catalog"
    with pytest.raises(AgentPackError) as too_long:
        parse_catalog_yaml(
            f"""
packs:
  - id: code-auditor
    kind: agent
    path: packs/engineering/code-auditor.agent.yaml
    category: engineering
    title: Code Auditor
    summary: {"x" * 161}
"""
        )
    assert too_long.value.code == "invalid_catalog"


def test_list_catalog_falls_back_to_pack_preamble_when_summary_missing() -> None:
    source = FakePackSource()
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    source.add(
        owner=owner,
        repo=repo,
        path=CATALOG_INDEX_PATH,
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text="""
packs:
  - id: code-auditor
    kind: agent
    path: packs/engineering/code-auditor.agent.yaml
    category: engineering
    title: Code Auditor
""",
    )
    source.add(
        owner=owner,
        repo=repo,
        path=AUDITOR_PATH,
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text=AUDITOR_PACK,
    )
    result = list_catalog(source=source, catalog_repo=DEFAULT_CATALOG_REPO, ref=PINNED_SHA)
    assert result.packs[0].entry.summary is None
    assert result.packs[0].summary == (
        "Hire when work claims done and needs evidence-backed CLEAR."
    )


def test_list_catalog_prefers_pack_preamble_over_index_summary() -> None:
    source = FakePackSource()
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    source.add(
        owner=owner,
        repo=repo,
        path=CATALOG_INDEX_PATH,
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text="""
packs:
  - id: code-auditor
    kind: agent
    path: packs/engineering/code-auditor.agent.yaml
    category: engineering
    title: Code Auditor
    summary: Hire from the catalog index, not the pack preamble.
""",
    )
    source.add(
        owner=owner,
        repo=repo,
        path=AUDITOR_PATH,
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text=AUDITOR_PACK,
    )
    result = list_catalog(source=source, catalog_repo=DEFAULT_CATALOG_REPO, ref=PINNED_SHA)
    assert result.packs[0].summary == (
        "Hire when work claims done and needs evidence-backed CLEAR."
    )


def test_list_catalog_uses_index_summary_when_pack_has_no_preamble() -> None:
    source = FakePackSource()
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    source.add(
        owner=owner,
        repo=repo,
        path=CATALOG_INDEX_PATH,
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text="""
packs:
  - id: code-auditor
    kind: agent
    path: packs/engineering/code-auditor.agent.yaml
    category: engineering
    title: Code Auditor
    summary: Hire from the catalog index when the pack has no preamble.
""",
    )
    source.add(
        owner=owner,
        repo=repo,
        path=AUDITOR_PATH,
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text=VALID_PACK,
    )
    result = list_catalog(source=source, catalog_repo=DEFAULT_CATALOG_REPO, ref=PINNED_SHA)
    assert result.packs[0].summary == (
        "Hire from the catalog index when the pack has no preamble."
    )


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
    assert result.hire_fields["description"].startswith(
        "Hire when work claims done and needs evidence-backed CLEAR."
    )
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
    assert result.catalog_entry.summary == (
        "Hire when work claims done and needs evidence-backed CLEAR."
    )
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
    assert config.get("agent_pack_catalog_pin") == DEFAULT_CATALOG_PIN
    assert config.get("agent_pack_url_allowlist") is None
    assert DEFAULT_CATALOG_PIN == "dcc94ca"


def test_previous_default_catalog_pin_is_bumped() -> None:
    db.set_setting("agent_pack_catalog_pin", "3c1e0a6", "agent_packs")
    from db.settings import seed_defaults

    seed_defaults()
    config.reload()
    assert config.get("agent_pack_catalog_pin") == DEFAULT_CATALOG_PIN


def test_custom_catalog_pin_is_not_bumped() -> None:
    db.set_setting("agent_pack_catalog_pin", "cafebab", "agent_packs")
    from db.settings import seed_defaults

    seed_defaults()
    config.reload()
    assert config.get("agent_pack_catalog_pin") == "cafebab"


def test_list_catalog_groups_categories_and_reads_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _catalog_source()
    result = list_catalog(
        source=source,
        catalog_repo=DEFAULT_CATALOG_REPO,
        ref=DEFAULT_CATALOG_PIN,
    )
    assert result.commit_sha == PINNED_SHA
    assert result.requested_ref == DEFAULT_CATALOG_PIN
    assert [card.entry.id for card in result.packs] == ["code-auditor", "feature-planner"]
    assert result.packs[0].pack_author == {
        "name": "JTech Minds",
        "url": "https://github.com/JTechMinds",
    }
    assert result.packs[0].specialty == "Code Auditor"
    assert result.packs[0].summary == (
        "Hire when work claims done and needs evidence-backed CLEAR."
    )
    assert result.packs[1].summary == (
        "Hire when you need product cuts / sequencing, not code."
    )
    assert source.resolve_calls[0][2] == DEFAULT_CATALOG_PIN
    assert source.fetch_calls[0][2] == CATALOG_INDEX_PATH
    assert db.list_agents() == []


def test_list_catalog_empty_index_is_not_an_error() -> None:
    source = FakePackSource()
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    source.add(
        owner=owner,
        repo=repo,
        path=CATALOG_INDEX_PATH,
        ref=PINNED_SHA,
        sha=PINNED_SHA,
        yaml_text="packs: []\n",
    )
    result = list_catalog(source=source, catalog_repo=DEFAULT_CATALOG_REPO, ref=PINNED_SHA)
    assert result.packs == ()
    assert result.commit_sha == PINNED_SHA


def test_api_list_catalog_default_pin_and_empty_and_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _catalog_source()
    client = _client(monkeypatch, source)
    listed = client.get("/api/agent-packs", headers=_headers())
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["ref"] == DEFAULT_CATALOG_PIN
    assert body["commit_sha"] == PINNED_SHA
    assert body["pin_short"] == PINNED_SHA[:7]
    assert [c["id"] for c in body["categories"]] == ["engineering", "product"]
    engineering = body["categories"][0]["packs"]
    assert engineering[0]["id"] == "code-auditor"
    assert engineering[0]["title"] == "Code Auditor"
    assert engineering[0]["summary"] == (
        "Hire when work claims done and needs evidence-backed CLEAR."
    )
    assert body["categories"][1]["packs"][0]["summary"] == (
        "Hire when you need product cuts / sequencing, not code."
    )
    assert engineering[0]["pack_author"] == {
        "name": "JTech Minds",
        "url": "https://github.com/JTechMinds",
    }
    assert db.list_agents() == []

    empty = FakePackSource()
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    empty.add(
        owner=owner,
        repo=repo,
        path=CATALOG_INDEX_PATH,
        ref=DEFAULT_CATALOG_PIN,
        sha=PINNED_SHA,
        yaml_text="packs: []\n",
    )
    empty_client = _client(monkeypatch, empty)
    empty_body = empty_client.get("/api/agent-packs", headers=_headers())
    assert empty_body.status_code == 200, empty_body.text
    assert empty_body.json()["categories"] == []

    failed = FakePackSource()
    fail_client = _client(monkeypatch, failed)
    denied = fail_client.get("/api/agent-packs", headers=_headers())
    assert denied.status_code == 400
    assert denied.json()["detail"]["code"] == "pin_unresolved"


def test_list_catalog_withholds_a_pack_that_fails_quality(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Browse offers what install accepts, so a thin pack is offered by neither.

    The gate runs at the source: the thin pack is not a card at all, rather
    than a card whose only outcome is an error on Install. It comes back in
    ``withheld`` with the code install would have raised, and the refusal is
    logged — the operator owns the catalog repo, and a pack that merely
    vanished is a pack that stays broken.
    """
    source = _catalog_source(THIN_PACK)  # the thin pack sits at AUDITOR_PATH
    with caplog.at_level(logging.WARNING, logger="core.agent_pack.service"):
        result = list_catalog(
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            ref=PINNED_SHA,
        )

    assert [card.entry.id for card in result.packs] == ["feature-planner"]
    assert [row.id for row in result.withheld] == ["code-auditor"]
    refused = result.withheld[0]
    assert refused.code == "pack_quality"
    assert refused.path == AUDITOR_PATH
    assert refused.title == "Code Auditor"
    assert refused.message == (
        "Pack description is missing required sections: "
        "Mission, In scope, Out of scope, Handoff."
    )

    # The gate is pure over the parse the list already ran: one ref resolve,
    # one index read, one read per listed pack — the same fetches as before.
    assert len(source.resolve_calls) == 1
    assert [call[2] for call in source.fetch_calls] == [
        CATALOG_INDEX_PATH,
        AUDITOR_PATH,
        PLANNER_PATH,
    ]

    warning = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warning) == 1
    assert "code-auditor" in warning[0].getMessage()
    assert "pack_quality" in warning[0].getMessage()


def test_list_catalog_withholds_a_pack_that_does_not_parse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A row whose file is not a pack is refused the same way, with its own code.

    It used to be listed as a bare index row with no fields under it. There is
    no such card any more: the two failures differ only in the ``code`` the
    maintainer is handed.
    """
    source = _catalog_source(UNPARSEABLE_PACK, pack_path=PLANNER_PATH)
    with caplog.at_level(logging.WARNING, logger="core.agent_pack.service"):
        result = list_catalog(
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            ref=PINNED_SHA,
        )

    assert [card.entry.id for card in result.packs] == ["code-auditor"]
    assert [(row.id, row.code) for row in result.withheld] == [
        ("feature-planner", "invalid_yaml")
    ]
    assert result.withheld[0].path == PLANNER_PATH
    assert result.withheld[0].message == "Pack YAML is not valid data."
    assert [call[2] for call in source.fetch_calls] == [
        CATALOG_INDEX_PATH,
        AUDITOR_PATH,
        PLANNER_PATH,
    ]
    assert "feature-planner" in caplog.text


def test_api_withheld_packs_leave_the_grid_and_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused pack is absent from the grid AND from the category totals.

    Categories are grouped from the offered set, so an "All N" built from these
    rows counts installable packs only. The fact is not lost with the card:
    ``withheld`` names it, with the reason.
    """
    source = _catalog_source(THIN_PACK)
    client = _client(monkeypatch, source)
    listed = client.get("/api/agent-packs", headers=_headers())
    assert listed.status_code == 200, listed.text
    body = listed.json()

    # engineering held exactly one row and it did not survive the gate, so the
    # category is gone rather than present and empty.
    assert [category["id"] for category in body["categories"]] == ["product"]
    assert sum(len(category["packs"]) for category in body["categories"]) == 1
    assert body["categories"][0]["packs"][0]["id"] == "feature-planner"
    assert body["withheld"] == [
        {
            "id": "code-auditor",
            "path": "packs/engineering/code-auditor.agent.yaml",
            "category": "engineering",
            "title": "Code Auditor",
            "kind": "refused",
            "code": "pack_quality",
            "message": (
                "Pack description is missing required sections: "
                "Mission, In scope, Out of scope, Handoff."
            ),
        }
    ]
    assert db.list_agents() == []


def _unreadable_planner_source() -> FakePackSource:
    """A catalog whose auditor row is thin and whose planner file is not there.

    One source, two different failures: the auditor is READ and refused by the
    quality gate, the planner cannot be fetched at all. ``FakePackSource``
    raises ``fetch_failed`` for a file it does not hold, which is the code
    ``GitHubPackSource.fetch_file`` raises for a 404 and for a 502 alike.
    """
    source = _catalog_source(THIN_PACK)  # the thin pack sits at AUDITOR_PATH
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    del source.files[(owner.lower(), repo.lower(), PLANNER_PATH, PINNED_SHA.lower())]
    return source


def test_list_catalog_tells_a_refused_pack_apart_from_an_unreadable_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Content rejection and transport failure are not the same fact.

    Both leave the grid — you cannot install what you cannot read — but only
    one of them is an accusation. A pack the gate refused is a file the
    maintainer has to change; a pack that never arrived says nothing at all
    about its content, and the operator's move is to retry. One try block
    around the fetch AND the parse reported a GitHub 502 as "your pack is
    broken", so the two calls are caught separately and the kind is decided by
    WHICH ONE failed rather than by reading the code back out of the error.
    """
    source = _unreadable_planner_source()
    with caplog.at_level(logging.WARNING, logger="core.agent_pack.service"):
        result = list_catalog(
            source=source,
            catalog_repo=DEFAULT_CATALOG_REPO,
            ref=PINNED_SHA,
        )

    assert result.packs == ()
    assert [(row.id, row.kind, row.code) for row in result.withheld] == [
        ("code-auditor", WITHHELD_REFUSED, "pack_quality"),
        ("feature-planner", WITHHELD_UNAVAILABLE, "fetch_failed"),
    ]
    # The file and the category the maintainer has to go to, for both kinds.
    assert [(row.path, row.category) for row in result.withheld] == [
        (AUDITOR_PATH, "engineering"),
        (PLANNER_PATH, "product"),
    ]
    # Verbatim, and never rewritten into the other kind's language.
    refused, unavailable = result.withheld
    assert refused.message == (
        "Pack description is missing required sections: "
        "Mission, In scope, Out of scope, Handoff."
    )
    assert unavailable.message == "missing file"

    # Both are logged, and the log line says which kind it was.
    warnings = [record.getMessage() for record in caplog.records
                if record.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert WITHHELD_REFUSED in warnings[0] and "code-auditor" in warnings[0]
    assert WITHHELD_UNAVAILABLE in warnings[1] and "feature-planner" in warnings[1]


def test_api_withheld_rows_publish_the_file_the_category_and_the_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A maintainer told ``code-auditor: pack_quality`` still has to find the file.

    ``path`` was captured and then dropped on the way out, and ``category``
    was never captured at all — and this payload is FLAT while ``categories``
    is grouped, so without it "which category lost a pack" cannot be derived
    from the response at all. Both are published now, for both kinds.

    ``kind`` is the discriminator, in ONE list rather than two: the headline
    an operator reads is how many rows are missing from the grid in front of
    them, and that is one length here.
    """
    source = _unreadable_planner_source()
    client = _client(monkeypatch, source)
    listed = client.get("/api/agent-packs", headers=_headers())
    assert listed.status_code == 200, listed.text
    body = listed.json()

    assert body["categories"] == []
    assert body["withheld"] == [
        {
            "id": "code-auditor",
            "path": AUDITOR_PATH,
            "category": "engineering",
            "title": "Code Auditor",
            "kind": "refused",
            "code": "pack_quality",
            "message": (
                "Pack description is missing required sections: "
                "Mission, In scope, Out of scope, Handoff."
            ),
        },
        {
            "id": "feature-planner",
            "path": PLANNER_PATH,
            "category": "product",
            "title": "Feature Planner",
            "kind": "unavailable",
            "code": "fetch_failed",
            "message": "missing file",
        },
    ]
    # A per-category count of what went missing, which is the one derivation
    # the grid's own shape invites and the flat list could not answer before.
    assert sorted(row["category"] for row in body["withheld"]) == [
        "engineering",
        "product",
    ]
    assert db.list_agents() == []


def test_api_catalog_of_only_invalid_packs_is_an_empty_grid_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every pack refused is still a served catalog: nothing to install, and why.

    The index itself parsed, so there is nothing to raise about; the browse
    door answers with an empty grid and a full withheld list rather than a 400
    that would tell the operator nothing about which pack broke.
    """
    source = _catalog_source(THIN_PACK)
    owner, repo = DEFAULT_CATALOG_REPO.split("/", 1)
    for ref, sha in (
        (PINNED_SHA, PINNED_SHA),
        ("v1.0.0", TAG_SHA),
        (DEFAULT_CATALOG_PIN, PINNED_SHA),
    ):
        source.add(
            owner=owner,
            repo=repo,
            path=PLANNER_PATH,
            ref=ref,
            sha=sha,
            yaml_text=UNPARSEABLE_PACK,
        )
    client = _client(monkeypatch, source)
    listed = client.get("/api/agent-packs", headers=_headers())
    assert listed.status_code == 200, listed.text
    body = listed.json()

    assert body["categories"] == []
    assert [(row["id"], row["code"]) for row in body["withheld"]] == [
        ("code-auditor", "pack_quality"),
        ("feature-planner", "invalid_yaml"),
    ]
    assert body["commit_sha"] == PINNED_SHA
    assert db.list_agents() == []


def test_describe_pack_splits_every_required_section() -> None:
    """A quality-valid pack is structure, and the server is what reads it."""
    pack = parse_pack_yaml(AUDITOR_PACK)
    validate_pack_quality(pack)
    parsed = describe_pack(pack.description, pack.what_done_looks_like)
    assert set(parsed) == {"description", "done"}
    assert set(parsed["description"]) == {
        "preamble",
        "mission",
        "in_scope",
        "out_of_scope",
        "handoff",
    }
    assert set(parsed["done"]) == {"preamble", "fail_examples"}
    # A pack carries a When-to-hire lead-in AND its sections; the two are not
    # alternatives, and a reader that printed only the mission would drop the
    # line the pack author opened with.
    assert parsed["description"]["preamble"] == (
        "Hire when work claims done and needs evidence-backed CLEAR."
    )
    assert parsed["description"]["mission"].startswith("Reviews claims")
    assert "pull requests" in parsed["description"]["in_scope"]
    assert "credential hunting" in parsed["description"]["out_of_scope"]
    assert parsed["description"]["handoff"].startswith("The operator")
    # Labels are consumed, not echoed back inside the body.
    for body in parsed["description"].values():
        assert not str(body).startswith("Mission:")
    assert parsed["done"]["preamble"].startswith("A checkable allow/deny exists")
    assert "vibe check" in parsed["done"]["fail_examples"]
    assert "Fail examples" not in parsed["done"]["preamble"]


def test_describe_pack_keeps_a_preamble_and_nulls_what_is_absent() -> None:
    """Partial structure is reported as partial — never patched up."""
    partial = describe_pack(
        "Reads the room first.\nMission: Ships the smallest correct change.\n"
        "Handoff: The operator gets the diff.",
        "A named diff exists.",
    )
    assert partial["description"]["preamble"] == "Reads the room first."
    assert partial["description"]["mission"] == "Ships the smallest correct change."
    assert partial["description"]["handoff"] == "The operator gets the diff."
    assert partial["description"]["in_scope"] is None
    assert partial["description"]["out_of_scope"] is None
    assert partial["done"]["preamble"] == "A named diff exists."
    assert partial["done"]["fail_examples"] is None

    # A thin pack carries no heading at all. Every section is None and the text
    # survives whole as the preamble; nothing is invented to fill the shape.
    thin = parse_pack_yaml(THIN_PACK)
    unlabeled = describe_pack(thin.description, thin.what_done_looks_like)
    assert unlabeled["description"]["preamble"] == thin.description.strip()
    assert all(
        unlabeled["description"][key] is None
        for key in ("mission", "in_scope", "out_of_scope", "handoff")
    )
    assert unlabeled["done"]["fail_examples"] is None

    empty = describe_pack("", "")
    assert empty["description"]["preamble"] == ""
    assert empty["done"]["preamble"] == ""
    assert empty["description"]["mission"] is None


def test_api_cards_carry_sections_done_and_tools_at_no_extra_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The card gains parsed structure out of the parse it already ran."""
    source = _catalog_source()
    client = _client(monkeypatch, source)
    listed = client.get("/api/agent-packs", headers=_headers())
    assert listed.status_code == 200, listed.text
    card = listed.json()["categories"][0]["packs"][0]
    assert card["id"] == "code-auditor"
    assert card["what_done_looks_like"].startswith("A checkable allow/deny exists")
    assert card["tools_hint"] == ["work"]
    assert card["sections"] == describe_pack(
        card["description"], card["what_done_looks_like"]
    )
    assert card["sections"]["description"]["mission"].startswith("Reviews claims")
    assert card["sections"]["done"]["fail_examples"].endswith("evidence attached.")

    # The parsed form is a view of a fetch that already happened: one ref
    # resolve, one index read, one read per listed pack, and nothing more.
    assert len(source.resolve_calls) == 1
    assert [call[2] for call in source.fetch_calls] == [
        CATALOG_INDEX_PATH,
        AUDITOR_PATH,
        PLANNER_PATH,
    ]


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
