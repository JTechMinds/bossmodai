"""Company → floor file system.

The company root holds one folder per floor. An agent's ``/projects`` is its
own floor's folder: the virtual filesystem, the shell jail, and the git fence
all refuse another floor's projects, and an agent on vacation has none. The
Files place lists floors by name, refuses to create at the top level, and a
deleted floor's folder is archived, never removed.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from api.routes._company_floors import FLOOR_FOLDER_WHY, TOP_LEVEL_DESTINATION_WHY
from core import config
from core.bm_cli.floor_roots import (
    ARCHIVED_FLOORS_DIRNAME,
    FLOOR_MARKER_NAME,
    FloorRootUnavailable,
    archived_floors_root,
    company_root,
    floor_root,
    projects_root_for_storage_key,
)
from core.bm_cli.host_roots import (
    HostRootOverlapsCompany,
    PathOutsideRootsError,
    configured_host_roots,
    named_path_roots,
    normalize_host_root_setting,
)
from core.bm_cli.install_layout import RetiredProjectsRootSetting
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.project_git_fence import PROJECT_GIT_ESCAPE_WHY, project_git_fence_reason
from core.bm_cli.project_repo import project_directory_for
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.shell_executor import allowed_shell_roots
from core.bm_cli.virtual_fs import resolve_cli_path, virtual_root_entries
from core.floors import delete_floor, send_home
from db.floors import LOBBY_ID, create_floor


@pytest.fixture(autouse=True)
def _fresh_company(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An empty database and an empty company root per test.

    The company root is set before init_db, which creates every floor's folder.
    """
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(tmp_path / "company"))
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    yield
    db.close_connection()


class _Services:
    """Records runtime resets; a floor delete must not wake anyone."""

    def __init__(self) -> None:
        self.resets: list[str] = []

    async def reset_agent_runtime(self, agent_id: str) -> None:
        self.resets.append(agent_id)

    async def enqueue_trigger(self, **_kwargs: object) -> None:
        raise AssertionError("a floor delete must not wake anyone")


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _two_floors():
    """Ada on Finance, Bob in Lobby. Bob's Lobby project holds a secret."""
    finance = create_floor("Finance")
    floor_root(finance.id)
    ada = db.create_agent("Ada", role="Eng", floor_id=finance.id)
    bob = db.create_agent("Bob", role="Eng")
    secret = floor_root(LOBBY_ID) / "ledger" / "secret.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("lobby only\n", encoding="utf-8")
    return finance, ada, bob, secret


def test_retired_projects_root_variable_stops_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOSSMOD_PROJECTS_ROOT", "/somewhere/projects")
    with pytest.raises(RetiredProjectsRootSetting, match="BOSSMOD_COMPANY_ROOT"):
        company_root()


def test_floor_folders_are_named_by_id_and_unknown_floors_have_none() -> None:
    finance = create_floor("Finance")
    assert floor_root(finance.id) == company_root() / finance.id
    assert floor_root(LOBBY_ID).is_dir()
    assert archived_floors_root() == company_root() / ARCHIVED_FLOORS_DIRNAME
    with pytest.raises(FloorRootUnavailable):
        floor_root("no-such-floor")


def test_projects_is_the_agents_own_floor_and_another_floor_is_denied() -> None:
    finance, ada, bob, secret = _two_floors()

    assert resolve_cli_path(ada.storage_key, "/", "/projects").real_path == floor_root(finance.id)
    assert resolve_cli_path(bob.storage_key, "/", "/projects").real_path == floor_root(LOBBY_ID)
    # The same virtual path is a different, empty place on Ada's floor.
    mine = resolve_cli_path(ada.storage_key, "/", "/projects/ledger/secret.md")
    assert mine.exists is False
    assert mine.real_path == floor_root(finance.id) / "ledger" / "secret.md"
    # Naming the other floor's real path is outside every root.
    with pytest.raises(PathOutsideRootsError):
        resolve_cli_path(ada.storage_key, "/me", str(secret))
    with pytest.raises(PathOutsideRootsError):
        resolve_cli_path(ada.storage_key, "/me", str(company_root()))

    state = db.get_agent_state(ada.id)
    assert state is not None
    written = execute_bm_cli(ada, state, "write /projects/books/plan.md", content="plan\n")
    assert written.ok is True, written.detail
    assert (floor_root(finance.id) / "books" / "plan.md").read_text(encoding="utf-8") == "plan\n"
    assert not (floor_root(LOBBY_ID) / "books").exists()
    listed = execute_bm_cli(ada, state, "ls /projects")
    assert listed.ok is True, listed.detail
    assert "books" in (listed.prompt_content or "")
    assert "ledger" not in (listed.prompt_content or "")


def test_shell_jail_holds_only_the_agents_floor() -> None:
    finance, ada, _bob, _secret = _two_floors()
    roots = allowed_shell_roots(ada.storage_key)
    assert floor_root(finance.id).resolve() in roots
    assert floor_root(LOBBY_ID).resolve() not in roots
    assert company_root().resolve() not in roots


def test_git_fence_refuses_another_floors_project() -> None:
    _finance, ada, bob, _secret = _two_floors()
    ada_state = db.get_agent_state(ada.id)
    bob_state = db.get_agent_state(bob.id)
    assert ada_state is not None and bob_state is not None
    assert execute_bm_cli(ada, ada_state, "mkdir /projects/own").ok is True
    assert execute_bm_cli(bob, bob_state, "mkdir /projects/theirs").ok is True
    theirs = floor_root(LOBBY_ID) / "theirs"
    assert (theirs / ".git").is_dir()

    own = parse_cli_command("git -C /projects/own status")
    assert project_git_fence_reason(ada, own, "/me") is None
    foreign = parse_cli_command(f"git -C {theirs} status")
    assert project_git_fence_reason(ada, foreign, "/me") == PROJECT_GIT_ESCAPE_WHY
    assert project_directory_for(ada.storage_key, theirs) is None
    assert project_directory_for(bob.storage_key, theirs) == theirs.resolve()


def test_an_agent_on_vacation_has_no_projects() -> None:
    _finance, ada, _bob, _secret = _two_floors()
    send_home(ada.id)

    with pytest.raises(PathOutsideRootsError, match="no floor"):
        resolve_cli_path(ada.storage_key, "/", "/projects")
    with pytest.raises(FloorRootUnavailable):
        projects_root_for_storage_key(ada.storage_key)
    assert "projects/" not in virtual_root_entries(ada.storage_key)
    assert "me/" in virtual_root_entries(ada.storage_key)
    company = company_root().resolve()
    assert not any(root == company or company in root.parents for root in named_path_roots(ada.storage_key))
    assert project_directory_for(ada.storage_key, floor_root(LOBBY_ID) / "ledger") is None


def test_files_top_level_lists_floors_by_name_and_refuses_creating_there() -> None:
    finance = create_floor("Finance")
    floor_root(finance.id)
    (company_root() / ".cache").mkdir()
    client, headers = _client()

    top = client.get("/api/company/files", params={"path": "/"}, headers=headers)
    assert top.status_code == 200
    body = top.json()
    assert body["name"] == "Company"
    by_name = {entry["name"]: entry for entry in body["entries"]}
    assert ".cache" not in by_name
    assert by_name[finance.id]["floor_name"] == "Finance"
    assert by_name[finance.id]["mount"] == "floor"
    assert by_name[LOBBY_ID]["floor_name"] == "Lobby"
    assert by_name[ARCHIVED_FLOORS_DIRNAME]["floor_name"] == "Archived floors"
    assert by_name[ARCHIVED_FLOORS_DIRNAME]["mount"] == "archive"
    assert [entry["name"] for entry in body["entries"]][-1] == ARCHIVED_FLOORS_DIRNAME

    inside = client.get("/api/company/files", params={"path": f"/{finance.id}"}, headers=headers)
    assert inside.status_code == 200
    assert [crumb["label"] for crumb in inside.json()["breadcrumbs"]] == ["Company", "Finance"]
    assert inside.json()["name"] == "Finance"

    refused = client.post(
        "/api/company/files/create",
        headers=headers,
        json={"path": "/", "name": "loose", "kind": "folder"},
    )
    assert refused.status_code == 400
    assert refused.json()["detail"] == TOP_LEVEL_DESTINATION_WHY
    assert not (company_root() / "loose").exists()

    created = client.post(
        "/api/company/files/create",
        headers=headers,
        json={"path": f"/{finance.id}", "name": "books", "kind": "folder"},
    )
    assert created.status_code == 201
    assert (floor_root(finance.id) / "books").is_dir()


def test_floor_list_says_which_floors_have_a_folder() -> None:
    on_disk = create_floor("Finance")
    floor_root(on_disk.id)
    bare = create_floor("Bare")
    client, headers = _client()
    listed = client.get("/api/floors", headers=headers)
    assert listed.status_code == 200
    has_folder = {floor["id"]: floor["has_folder"] for floor in listed.json()}
    assert has_folder == {LOBBY_ID: True, on_disk.id: True, bare.id: False}

    created = client.post("/api/floors", headers=headers, json={"name": "Ops"})
    assert created.status_code == 201
    assert floor_root(created.json()["id"]).is_dir()


async def test_deleting_a_floor_archives_its_folder_and_repoints_artifacts() -> None:
    finance = create_floor("Finance")
    ada = db.create_agent("Ada", role="Eng", floor_id=finance.id)
    folder = floor_root(finance.id)
    plan = folder / "books" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("plan\n", encoding="utf-8")
    db.upsert_artifact(
        agent_id=ada.id,
        task_id=None,
        virtual_path="/projects/books/plan.md",
        absolute_path=str(plan.resolve()),
        title="plan.md",
        kind="file",
        category="project",
        size_bytes=5,
        source_command=None,
    )
    # A sibling path that only shares leading characters must not be rewritten.
    lookalike = str(folder.resolve()) + "-other/plan.md"
    db.upsert_artifact(
        agent_id=ada.id,
        task_id=None,
        virtual_path="/me/plan.md",
        absolute_path=lookalike,
        title="plan.md",
        kind="file",
        category="output",
        size_bytes=5,
        source_command=None,
    )

    result = await delete_floor(finance.id, occupants="send_home", services=_Services())

    assert result.folder_archived is True
    archived = archived_floors_root() / finance.id
    assert not folder.exists()
    assert (archived / "books" / "plan.md").read_text(encoding="utf-8") == "plan\n"
    marker = json.loads((archived / FLOOR_MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["id"] == finance.id
    assert marker["name"] == "Finance"
    assert marker["archived_at"]
    moved = str((archived / "books" / "plan.md").resolve())
    assert db.get_artifact_by_absolute_path(moved) is not None
    assert db.get_artifact_by_absolute_path(str(plan.resolve())) is None
    assert db.get_artifact_by_absolute_path(lookalike) is not None

    client, headers = _client()
    listing = client.get(
        "/api/company/files", params={"path": f"/{ARCHIVED_FLOORS_DIRNAME}"}, headers=headers,
    )
    assert listing.status_code == 200
    entries = {entry["name"]: entry for entry in listing.json()["entries"]}
    assert entries[finance.id]["floor_name"] == "Finance"
    crumbs = client.get(
        "/api/company/files",
        params={"path": f"/{ARCHIVED_FLOORS_DIRNAME}/{finance.id}"},
        headers=headers,
    ).json()["breadcrumbs"]
    assert [crumb["label"] for crumb in crumbs] == ["Company", "Archived floors", "Finance"]


async def test_deleting_a_floor_without_a_folder_archives_nothing() -> None:
    bare = create_floor("Bare")
    result = await delete_floor(bare.id, occupants=None, services=_Services())
    assert result.folder_archived is False
    assert not (archived_floors_root() / bare.id).exists()


def _floor_level_fixture():
    """Finance with a project, a stray top-level folder, and one archived floor."""
    finance = create_floor("Finance")
    books = floor_root(finance.id) / "books"
    books.mkdir()
    (books / "plan.md").write_text("plan\n", encoding="utf-8")
    (company_root() / "stray").mkdir()
    archived = archived_floors_root() / "old-floor"
    archived.mkdir()
    (archived / FLOOR_MARKER_NAME).write_text(json.dumps({"id": "old-floor", "name": "Old"}), encoding="utf-8")
    return finance, books


def test_files_refuses_to_touch_floor_folders() -> None:
    finance, _books = _floor_level_fixture()
    client, headers = _client()
    protected = [f"/{finance.id}", f"/{LOBBY_ID}", f"/{ARCHIVED_FLOORS_DIRNAME}", f"/{ARCHIVED_FLOORS_DIRNAME}/old-floor"]
    for path in protected:
        renamed = client.patch("/api/company/files/rename", headers=headers, json={"path": path, "new_name": "x"})
        deleted = client.request("DELETE", "/api/company/files", headers=headers, json={"path": path})
        moved = client.post(
            "/api/company/files/move", headers=headers,
            json={"source": path, "destination": f"/{LOBBY_ID}"},
        )
        copied = client.post(
            "/api/company/files/copy", headers=headers,
            json={"source": path, "destination": f"/{LOBBY_ID}"},
        )
        for response in (renamed, deleted, moved, copied):
            assert response.status_code == 409, (path, response.status_code, response.text)
            assert response.json()["detail"] == FLOOR_FOLDER_WHY
    assert floor_root(finance.id).is_dir()
    assert (archived_floors_root() / "old-floor").is_dir()
    assert not (floor_root(LOBBY_ID) / finance.id).exists()


def test_files_refuses_to_put_anything_at_floor_level() -> None:
    finance, books = _floor_level_fixture()
    client, headers = _client()
    for parent in ("/", f"/{ARCHIVED_FLOORS_DIRNAME}"):
        created = client.post(
            "/api/company/files/create", headers=headers,
            json={"path": parent, "name": "loose", "kind": "folder"},
        )
        moved = client.post(
            "/api/company/files/move", headers=headers,
            json={"source": f"/{finance.id}/books", "destination": parent},
        )
        copied = client.post(
            "/api/company/files/copy", headers=headers,
            json={"source": f"/{finance.id}/books", "destination": parent},
        )
        for response in (created, moved, copied):
            assert response.status_code == 400, (parent, response.status_code, response.text)
            assert response.json()["detail"] == TOP_LEVEL_DESTINATION_WHY
    # A stray top-level folder can be moved into a floor, but not renamed in place.
    renamed = client.patch("/api/company/files/rename", headers=headers, json={"path": "/stray", "new_name": "x"})
    assert renamed.status_code == 400
    assert renamed.json()["detail"] == TOP_LEVEL_DESTINATION_WHY
    moved_in = client.post(
        "/api/company/files/move", headers=headers,
        json={"source": "/stray", "destination": f"/{finance.id}"},
    )
    assert moved_in.status_code == 200
    assert (floor_root(finance.id) / "stray").is_dir()
    assert books.is_dir()
    assert not (company_root() / "loose").exists()
    assert not (company_root() / "books").exists()

    # Inside a floor everything still works.
    renamed_inside = client.patch(
        "/api/company/files/rename", headers=headers,
        json={"path": f"/{finance.id}/books", "new_name": "ledger"},
    )
    assert renamed_inside.status_code == 200
    assert (floor_root(finance.id) / "ledger" / "plan.md").is_file()


def test_search_hits_show_floor_names_not_ids() -> None:
    finance, _books = _floor_level_fixture()
    client, headers = _client()
    hits = client.get("/api/company/files/search", params={"q": "plan"}, headers=headers)
    assert hits.status_code == 200
    by_path = {hit["path"]: hit for hit in hits.json()}
    hit = by_path[f"/{finance.id}/books/plan.md"]
    assert hit["display_path"] == "/Finance/books/plan.md"
    assert hit["floor_name"] is None

    floor_hits = client.get("/api/company/files/search", params={"q": finance.id[:8]}, headers=headers).json()
    floor_hit = next(item for item in floor_hits if item["path"] == f"/{finance.id}")
    assert floor_hit["display_path"] == "/Finance"
    assert floor_hit["floor_name"] == "Finance"
    assert floor_hit["mount"] == "floor"

    archived = client.get("/api/company/files/search", params={"q": "old-floor"}, headers=headers).json()
    archived_hit = next(item for item in archived if item["path"] == f"/{ARCHIVED_FLOORS_DIRNAME}/old-floor")
    assert archived_hit["display_path"] == "/Archived floors/Old"
    assert archived_hit["mount"] == "floor"


def test_host_roots_overlapping_the_company_are_refused_on_write(tmp_path: Path) -> None:
    company = company_root()
    inside = floor_root(LOBBY_ID)
    for candidate in (company, company.parent, inside):
        with pytest.raises(HostRootOverlapsCompany, match="overlaps the company folder"):
            normalize_host_root_setting(str(candidate))

    client, headers = _client()
    refused = client.put(
        "/api/settings/workspace_host_roots",
        params={"value": str(inside), "category": "cli_policy"},
        headers=headers,
    )
    assert refused.status_code == 400
    assert "overlaps the company folder" in refused.json()["detail"]

    elsewhere = tmp_path / "host"
    elsewhere.mkdir()
    assert normalize_host_root_setting(str(elsewhere)) == str(elsewhere.resolve())


def test_a_stored_host_root_overlapping_the_company_is_ignored_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    elsewhere = tmp_path / "host"
    elsewhere.mkdir()
    # Written straight to the table, as a pre-existing setting would be.
    db.set_setting("workspace_host_roots", f"{company_root()}\n{elsewhere}", "cli_policy")
    config.reload()
    agent = db.create_agent("Ada", role="Eng")

    with caplog.at_level(logging.ERROR, logger="core.bm_cli.host_roots"):
        roots = configured_host_roots()
        jail = allowed_shell_roots(agent.storage_key)
    assert roots == (elsewhere.resolve(),)
    assert company_root() not in jail
    assert elsewhere.resolve() in jail
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) >= 2
    assert all("overlaps the company folder" in record.getMessage() for record in errors)
    # The Lobby agent still cannot reach another floor through the stored root.
    finance = create_floor("Finance")
    with pytest.raises(PathOutsideRootsError):
        resolve_cli_path(agent.storage_key, "/me", str(floor_root(finance.id)))
