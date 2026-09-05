"""Named-path + diagnostic CLI capability pass.

Allowlisted extra host roots let a user-named absolute path be opened,
read, and edited. Empty setting stays fail-closed. Path jail still applies
on approved shell commands. This is not a full host mount.
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
from core.bm_cli.filesystem import agent_artifact_dir, projects_artifact_root
from core.bm_cli.host_roots import (
    PathOutsideRootsError,
    denial_message,
    normalize_host_root_setting,
    parse_host_root_setting,
    validate_host_root,
)
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.shell_executor import (
    PATH_JAIL_DENIED_EXIT_CODE,
    allowed_shell_roots,
    execute_shell_command,
)
from core.bm_cli.virtual_fs import resolve_cli_path, virtual_root_entries


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return app


def _client() -> TestClient:
    return TestClient(_app())


def _auth_headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _set_host_roots(*roots: Path) -> None:
    db.set_setting(
        "workspace_host_roots",
        "\n".join(str(root) for root in roots),
        "cli_policy",
    )
    config.reload()


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


# ---------------------------------------------------------------------------
# Setting parse / validate
# ---------------------------------------------------------------------------


def test_parse_host_root_setting_accepts_newlines_and_commas() -> None:
    assert parse_host_root_setting("") == []
    assert parse_host_root_setting(None) == []
    assert parse_host_root_setting("  \n# comment\n") == []
    assert parse_host_root_setting("/tmp/a\n/tmp/b") == ["/tmp/a", "/tmp/b"]
    assert parse_host_root_setting("/tmp/a, /tmp/b") == ["/tmp/a", "/tmp/b"]


def test_validate_host_root_rejects_escape_and_system_dirs(tmp_path: Path) -> None:
    allowed = tmp_path / "pc-projects"
    allowed.mkdir()
    assert validate_host_root(str(allowed)) == allowed.resolve()

    with pytest.raises(ValueError, match="absolute"):
        validate_host_root("relative/path")
    with pytest.raises(ValueError, match="filesystem root"):
        validate_host_root("/")
    with pytest.raises(ValueError, match="denied system"):
        validate_host_root("/etc")
    with pytest.raises(ValueError, match="does not exist"):
        validate_host_root(str(tmp_path / "missing"))
    file_root = tmp_path / "not-a-dir"
    file_root.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        validate_host_root(str(file_root))


def test_normalize_host_root_setting_dedupes(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    canonical = normalize_host_root_setting(f"{root}\n{root}/\n")
    assert canonical == str(root.resolve())


def test_workspace_host_roots_default_empty() -> None:
    assert config.get("workspace_host_roots") is None
    assert config.get("cli_shell_enabled") == "false"


def test_settings_api_rejects_filesystem_root_as_host_root() -> None:
    client = _client()
    denied = client.put(
        "/api/settings/workspace_host_roots",
        params={"value": "/", "category": "cli_policy"},
        headers=_auth_headers(),
    )
    assert denied.status_code == 400
    assert "filesystem root" in denied.json()["detail"]
    assert config.get("workspace_host_roots") is None


# ---------------------------------------------------------------------------
# Virtual CLI named paths
# ---------------------------------------------------------------------------


def test_resolve_cli_path_opens_named_host_file(tmp_path: Path) -> None:
    host = tmp_path / "named-root"
    host.mkdir()
    fixture = host / "review.py"
    fixture.write_text("print('before')\n", encoding="utf-8")
    _set_host_roots(host)

    agent = db.create_agent("Path Reviewer")
    resolved = resolve_cli_path(agent.storage_key, "/me", str(fixture))
    assert resolved.mount == "host"
    assert resolved.real_path == fixture.resolve()
    assert resolved.exists is True
    assert "me/" in virtual_root_entries()
    assert f"{host.resolve()}/" in virtual_root_entries()


def test_resolve_cli_path_denies_outside_host_roots(tmp_path: Path) -> None:
    host = tmp_path / "named-root"
    host.mkdir()
    _set_host_roots(host)
    agent = db.create_agent("Path Reviewer")

    with pytest.raises(PathOutsideRootsError, match="outside the allowed workspace roots"):
        resolve_cli_path(agent.storage_key, "/me", "/etc/passwd")
    with pytest.raises(PathOutsideRootsError):
        resolve_cli_path(agent.storage_key, "/me", str(tmp_path / "other" / "secret.md"))


def test_resolve_cli_path_canonicalizes_real_projects_path() -> None:
    agent = db.create_agent("Path Reviewer")
    projects = projects_artifact_root()
    notes = projects / "alpha" / "notes.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("project notes\n", encoding="utf-8")

    resolved = resolve_cli_path(agent.storage_key, "/me", str(notes.resolve()))
    assert resolved.mount == "projects"
    assert resolved.virtual_path == "/projects/alpha/notes.md"
    assert resolved.real_path == notes.resolve()


def test_empty_host_roots_do_not_grant_tmp_access(tmp_path: Path) -> None:
    outsider = tmp_path / "unmounted.md"
    outsider.write_text("nope\n", encoding="utf-8")
    agent = db.create_agent("Path Reviewer")
    with pytest.raises(PathOutsideRootsError):
        resolve_cli_path(agent.storage_key, "/me", str(outsider))


def test_execute_bm_cli_reads_and_writes_named_host_path(tmp_path: Path) -> None:
    host = tmp_path / "named-root"
    host.mkdir()
    fixture = host / "review.py"
    fixture.write_text("print('before')\n", encoding="utf-8")
    _set_host_roots(host)

    agent = db.create_agent("Path Reviewer")
    state = db.get_agent_state(agent.id)
    assert state is not None

    read = execute_bm_cli(agent, state, f"cat {fixture}")
    assert read.ok is True
    assert "print('before')" in read.prompt_content

    written = execute_bm_cli(
        agent,
        state,
        f"write {fixture}",
        "print('after')\n",
    )
    assert written.ok is True
    assert fixture.read_text(encoding="utf-8") == "print('after')\n"

    denied = execute_bm_cli(agent, state, "cat /etc/passwd")
    assert denied.ok is False
    payload = (denied.detail or "") + denied.prompt_content
    assert "outside the allowed workspace roots" in payload
    assert "not a full host mount" in payload
    assert '"/me"' in payload
    assert str(host.resolve()) in payload
    assert "root:" not in payload


# ---------------------------------------------------------------------------
# Company files API
# ---------------------------------------------------------------------------


def test_company_files_named_path_read_edit_and_deny(tmp_path: Path) -> None:
    host = tmp_path / "pc-projects"
    host.mkdir()
    fixture = host / "app.py"
    fixture.write_text("x = 1\n", encoding="utf-8")
    _set_host_roots(host)

    client = _client()
    headers = _auth_headers()

    opened = client.get(
        "/api/company/files",
        params={"path": str(fixture)},
        headers=headers,
    )
    assert opened.status_code == 200
    body = opened.json()
    assert body["kind"] == "file"
    assert body["content"] == "x = 1\n"
    assert "not a full unrestricted host mount" in body["workspace_note"]
    assert str(host.resolve()) in body["host_roots"]

    saved = client.put(
        "/api/company/files",
        headers=headers,
        json={"path": str(fixture), "content": "x = 2\n"},
    )
    assert saved.status_code == 200
    assert fixture.read_text(encoding="utf-8") == "x = 2\n"

    denied = client.get(
        "/api/company/files",
        params={"path": "/etc/passwd"},
        headers=headers,
    )
    assert denied.status_code == 400
    detail = denied.json()["detail"]
    assert "outside the allowed workspace roots" in detail
    assert "not a full host mount" in detail


def test_company_files_root_still_hides_backups_with_host_roots(tmp_path: Path) -> None:
    host = tmp_path / "pc-projects"
    host.mkdir()
    (host / "ok.md").write_text("ok\n", encoding="utf-8")
    (host / "leak.bak").write_bytes(b"hidden")
    _set_host_roots(host)

    client = _client()
    headers = _auth_headers()
    listed = client.get("/api/company/files", params={"path": "/"}, headers=headers)
    assert listed.status_code == 200
    names = [entry["name"] for entry in listed.json()["entries"]]
    assert host.name in names
    assert "db_backups" not in names
    assert "agents" not in names

    hidden = client.get(
        "/api/company/files",
        params={"path": str(host / "leak.bak")},
        headers=headers,
    )
    assert hidden.status_code in {400, 404}


def test_company_relative_project_file_still_opens() -> None:
    projects = projects_artifact_root()
    notes = projects / "alpha" / "notes.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("hello project\n", encoding="utf-8")

    client = _client()
    opened = client.get(
        "/api/company/files",
        params={"path": "/alpha/notes.md"},
        headers=_auth_headers(),
    )
    assert opened.status_code == 200
    assert opened.json()["content"] == "hello project\n"


# ---------------------------------------------------------------------------
# Diagnostic CLI + path jail
# ---------------------------------------------------------------------------


def test_shell_jail_allows_host_root_and_denies_escape(tmp_path: Path) -> None:
    host = tmp_path / "named-root"
    host.mkdir()
    (host / "ok.txt").write_text("host-ok\n", encoding="utf-8")
    _set_host_roots(host)
    agent = db.create_agent("Diag")
    roots = allowed_shell_roots(agent.storage_key)
    assert host.resolve() in roots

    allowed = execute_shell_command(
        f"ls {host}",
        cwd=agent_artifact_dir(agent.storage_key),
        allowed_roots=roots,
    )
    assert allowed.denied_by_path_jail is False
    assert allowed.exit_code == 0
    assert "ok.txt" in allowed.stdout

    escaped = execute_shell_command(
        "cat /etc/passwd",
        cwd=agent_artifact_dir(agent.storage_key),
        allowed_roots=roots,
    )
    assert escaped.denied_by_path_jail is True
    assert escaped.exit_code == PATH_JAIL_DENIED_EXIT_CODE
    assert "root:" not in escaped.stdout


def test_execute_bm_cli_uname_and_path_escape(tmp_path: Path) -> None:
    host = tmp_path / "named-root"
    host.mkdir()
    _set_host_roots(host)
    _enable_shell()

    agent = db.create_agent("Diag")
    state = db.get_agent_state(agent.id)
    assert state is not None

    listed = execute_bm_cli(agent, state, f"ls {host}")
    assert listed.ok is True

    uname = execute_bm_cli(agent, state, "uname -a")
    assert uname.ok is True
    assert uname.exit_code == 0
    assert "Linux" in uname.prompt_content

    escaped = execute_bm_cli(agent, state, "cat /etc/passwd")
    assert escaped.ok is False
    payload = f"{escaped.detail} {escaped.prompt_content}"
    assert "Path jail" in payload or "outside the allowed workspace roots" in payload
    assert "root:" not in payload


def test_seed_rules_include_uname_always_allowed() -> None:
    always = {rule.pattern for rule in db.list_cli_policy_rules() if rule.tier == "always_allowed"}
    assert "uname" in always
    assert "cat" in always


def test_denial_message_is_honest() -> None:
    text = denial_message("/etc/passwd", extra_roots=())
    assert "not a full host mount" in text
    assert "/me" in text
    assert "/projects" in text
