"""Named Nest git credentials: match by remote, default fallback, migration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.cli_always import write_nest_always_rule
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.nest_git import (
    nest_git_auth_ready,
    nest_git_pat,
    nest_git_shell_env,
    nest_git_status,
    write_nest_git_secret,
)
from core.bm_cli.nest_git_store import (
    add_credential,
    load_credentials,
    match_score,
    migrate_legacy_store,
    normalize_remote_url,
    select_nest_git_credential,
    suggested_match_for_remote,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.session import set_cli_cwd
from core.bm_cli.shell_executor import ShellExecutionResult
from core.models.nest_git import (
    NEST_GIT_ADD_LABEL,
    NEST_GIT_DEFAULT_CREDENTIAL_ID,
    NEST_GIT_DEFAULT_CREDENTIAL_LABEL,
    NEST_GIT_ENABLE_LABEL,
    NEST_GIT_KIND,
    NEST_GIT_NO_MATCH_WHY,
    NEST_GIT_PAT_KEY,
    NEST_GIT_PICK_PREFIX,
    NEST_GIT_TOKEN_NO_REPO_OWNER,
)
from core.runtime import runtime_services
from db.secret_store import SECRET_PREFIX, is_encrypted, is_secret_setting_key
from tests.test_workspace_preference import _agent_and_state


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


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _persist_trigger(**kwargs: Any) -> None:
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )

    monkeypatch.setattr(runtime_services, "enqueue_trigger", _persist_trigger)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _write_origin(repo: Path, url: str) -> None:
    git = repo / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "config").write_text(
        f'[remote "origin"]\n\turl = {url}\n',
        encoding="utf-8",
    )


def _nest_repo(agent, url: str) -> str:
    dest = "/me/host-work/sample_repo"
    real = agent_artifact_dir(agent.storage_key) / "host-work" / "sample_repo"
    real.mkdir(parents=True, exist_ok=True)
    _write_origin(real, url)
    set_cli_cwd(agent.id, dest)
    return dest


def test_normalize_and_match_score() -> None:
    assert normalize_remote_url("https://github.com/Acme/app.git") == "github.com/Acme/app"
    assert normalize_remote_url("git@github.com:Acme/app.git") == "github.com/Acme/app"
    assert normalize_remote_url("ssh://git@gitlab.example.com/g/proj.git") == "gitlab.example.com/g/proj"
    assert match_score("github.com/Acme/app", "github.com/Acme/app") == 3
    assert match_score("github.com/Acme/*", "github.com/Acme/app") == 2
    assert match_score("github.com", "github.com/Acme/app") == 1
    assert match_score("github.com/Acme/*", "github.com/Widgets/app") == 0
    assert match_score("gitlab.example.com", "gitlab.example.com/g/proj") == 1
    assert suggested_match_for_remote("https://github.com/Acme/app.git") == "github.com/Acme/*"


def test_legacy_single_store_migrates_to_default() -> None:
    token = "ghp_migrate-default-store-AAAA"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    migrated = migrate_legacy_store()
    creds = load_credentials()
    assert creds
    assert creds[0].id == NEST_GIT_DEFAULT_CREDENTIAL_ID
    assert creds[0].label == NEST_GIT_DEFAULT_CREDENTIAL_LABEL
    assert creds[0].is_default is True
    assert creds[0].match == ""
    assert nest_git_pat() == token
    assert select_nest_git_credential("github.com/Acme/app") is not None
    assert select_nest_git_credential("github.com/Acme/app").id == NEST_GIT_DEFAULT_CREDENTIAL_ID
    status = nest_git_status()
    assert status["default_id"] == NEST_GIT_DEFAULT_CREDENTIAL_ID
    assert status["has_pat"] is True
    assert token not in json.dumps(status)
    assert migrated is None or migrated.id == NEST_GIT_DEFAULT_CREDENTIAL_ID


def test_select_prefers_exact_then_owner_then_default() -> None:
    add_credential(
        label="Catch-all",
        match="",
        pat="ghp_default-fallback-BBBB",
        is_default=True,
        credential_id="default",
    )
    add_credential(
        label="Acme",
        match="github.com/Acme/*",
        pat="ghp_acme-owner-star-CCCC",
        is_default=False,
    )
    add_credential(
        label="Widgets app",
        match="github.com/Widgets/app",
        pat="ghp_widgets-exact-DDDD",
        is_default=False,
    )
    assert select_nest_git_credential("github.com/Widgets/app").label == "Widgets app"
    assert select_nest_git_credential("github.com/Acme/tools").label == "Acme"
    assert select_nest_git_credential("github.com/Other/repo").label == "Catch-all"
    assert select_nest_git_credential("gitlab.example.com/g/proj").label == "Catch-all"


def test_unmatched_without_default_is_fail_closed() -> None:
    add_credential(
        label="Acme",
        match="github.com/Acme/*",
        pat="ghp_acme-only-EEEE",
        is_default=False,
    )
    assert select_nest_git_credential("github.com/Widgets/app") is None
    agent, state = _agent_and_state()
    cwd = _nest_repo(agent, "https://github.com/Widgets/app.git")
    parsed = parse_cli_command("git push origin main")
    assert nest_git_auth_ready(agent=agent, parsed=parsed, cwd=cwd) is False
    paused = execute_bm_cli(agent, state, "git push origin main")
    assert paused.ok is False
    assert paused.consent_required is True
    assert paused.kind == "nest_git_consent_required"
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card["kind"] == NEST_GIT_KIND
    assert NEST_GIT_NO_MATCH_WHY in str(card.get("error") or "")
    assert "Resource owner = the org that owns the repo" in str(card.get("error") or "")
    assert any(item.get("label") == "Acme" for item in card.get("credentials") or [])
    dumped = json.dumps(card)
    assert "ghp_acme-only-EEEE" not in dumped


def test_inject_uses_matching_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    add_credential(
        label="Acme",
        match="github.com/Acme/*",
        pat="ghp_inject-acme-FFFF",
        is_default=False,
    )
    add_credential(
        label="Widgets",
        match="github.com/Widgets/app",
        pat="ghp_inject-widgets-GGGG",
        is_default=False,
    )
    add_credential(
        label="Default",
        match="",
        pat="ghp_inject-default-HHHH",
        is_default=True,
        credential_id="default",
    )
    captured: dict[str, Any] = {}

    def _run(command: str, **kwargs: Any) -> ShellExecutionResult:
        captured["command"] = command
        captured["extra_env"] = dict(kwargs.get("extra_env") or {})
        return ShellExecutionResult(exit_code=0, stdout="ok", stderr="", timed_out=False, duration_ms=1)

    monkeypatch.setattr("core.bm_cli.runtime.execute_shell_command", _run)
    _enable_shell()
    agent, state = _agent_and_state()
    cwd = _nest_repo(agent, "https://github.com/Acme/tools.git")
    write_nest_always_rule("git push origin main", cwd)
    policy_engine.reload()
    result = execute_bm_cli(agent, state, "git push origin main")
    extra = captured.get("extra_env") or {}
    assert extra.get("BOSSMOD_NEST_GIT_PASSWORD") == "ghp_inject-acme-FFFF"
    assert extra.get("GIT_ASKPASS")
    assert "ghp_inject-acme-FFFF" not in (captured.get("command") or "")
    assert "ghp_inject-acme-FFFF" not in (result.prompt_content or "")

    captured.clear()
    _write_origin(agent_artifact_dir(agent.storage_key) / "host-work" / "sample_repo", "https://github.com/Widgets/app.git")
    execute_bm_cli(agent, state, "git push origin main")
    assert (captured.get("extra_env") or {}).get("BOSSMOD_NEST_GIT_PASSWORD") == "ghp_inject-widgets-GGGG"

    extra = nest_git_shell_env(agent, parse_cli_command("git fetch"), cwd)
    _write_origin(agent_artifact_dir(agent.storage_key) / "host-work" / "sample_repo", "https://github.com/Other/repo.git")
    extra = nest_git_shell_env(agent, parse_cli_command("git fetch"), cwd)
    assert extra.get("BOSSMOD_NEST_GIT_PASSWORD") == "ghp_inject-default-HHHH"


def test_named_secrets_use_bm1_and_settings_crud(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "ghp_named-bm1-wrap-IIII"
    client = _api_client(monkeypatch)
    created = client.post(
        "/api/nest-git/items",
        headers=_headers(),
        json={"label": "Acme", "match": "github.com/Acme/*", "pat": token},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert token not in json.dumps(body)
    creds = body["credentials"]
    assert len(creds) == 1
    assert creds[0]["label"] == "Acme"
    assert creds[0]["match"] == "github.com/Acme/*"
    assert creds[0]["has_pat"] is True
    cred_id = creds[0]["id"]
    assert is_secret_setting_key(f"nest_git_pat_{cred_id}")
    raw = db.query_one("SELECT value FROM settings WHERE key = $1", [f"nest_git_pat_{cred_id}"])
    stored = "" if raw is None else str(raw.get("value") or "")
    assert token not in stored
    assert stored.startswith(SECRET_PREFIX)
    assert is_encrypted(stored)

    edited = client.put(
        f"/api/nest-git/items/{cred_id}",
        headers=_headers(),
        json={"label": "Acme org", "match": "github.com/Acme/*", "is_default": True},
    )
    assert edited.status_code == 200
    assert edited.json()["credentials"][0]["label"] == "Acme org"
    assert edited.json()["default_id"] == cred_id

    removed = client.delete(f"/api/nest-git/items/{cred_id}", headers=_headers())
    assert removed.status_code == 200
    assert removed.json()["credentials"] == []


def test_pick_saved_credential_appends_remote_match(monkeypatch: pytest.MonkeyPatch) -> None:
    add_credential(
        label="Acme",
        match="github.com/Acme/*",
        pat="ghp_pick-acme-JJJJ",
        is_default=False,
    )
    agent, state = _agent_and_state()
    _nest_repo(agent, "https://github.com/Widgets/app.git")
    paused = execute_bm_cli(agent, state, "git push origin main")
    request_id = paused.consent_request_id
    assert request_id
    cred_id = load_credentials()[0].id
    client = _api_client(monkeypatch)
    used = client.post(
        f"/api/nest-git/{request_id}/use",
        headers=_headers(),
        json={"credential_id": cred_id},
    )
    assert used.status_code == 200, used.text
    updated = load_credentials()[0]
    assert "github.com/Widgets/app" in updated.match
    assert nest_git_auth_ready(
        agent=agent,
        parsed=parse_cli_command("git push origin main"),
        cwd="/me/host-work/sample_repo",
    )
    assert "ghp_pick-acme-JJJJ" not in used.text


def test_needs_nest_git_actions_post_multi_cred_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Needs describes Enable / Add / Use with the JSON body those routes require."""
    add_credential(
        label="Acme",
        match="github.com/Acme/*",
        pat="ghp_needs-body-acme-LLLL",
        is_default=False,
    )
    agent, state = _agent_and_state()
    _nest_repo(agent, "https://github.com/Widgets/app.git")
    paused = execute_bm_cli(agent, state, "git push origin main")
    request_id = paused.consent_request_id
    assert request_id
    client = _api_client(monkeypatch)
    needs = client.get("/api/needs", headers=_headers())
    assert needs.status_code == 200
    items = [item for item in needs.json() if item.get("card_kind") == NEST_GIT_KIND]
    assert len(items) == 1
    actions = {action["label"]: action for action in items[0]["actions"]}
    assert actions[NEST_GIT_ENABLE_LABEL]["body"] == {}
    assert actions[NEST_GIT_ADD_LABEL]["body"] == {}
    assert actions[f"{NEST_GIT_PICK_PREFIX} Acme"]["body"] == {"credential_id": load_credentials()[0].id}
    dumped = json.dumps(items)
    assert "ghp_needs-body-acme-LLLL" not in dumped

    empty = client.post(
        f"/api/nest-git/{request_id}/credentials",
        headers=_headers(),
    )
    assert empty.status_code == 422
    assert "Field required" in empty.text

    described = client.post(
        f"/api/nest-git/{request_id}/credentials",
        headers=_headers(),
        json=actions[NEST_GIT_ADD_LABEL]["body"],
    )
    assert described.status_code == 400, described.text
    assert described.status_code != 422
    assert "Field required" not in described.text

    used = client.post(
        f"/api/nest-git/{request_id}/use",
        headers=_headers(),
        json=actions[f"{NEST_GIT_PICK_PREFIX} Acme"]["body"],
    )
    assert used.status_code == 200, used.text
    assert "ghp_needs-body-acme-LLLL" not in used.text


def test_always_allow_unmatched_still_hits_nest_git_card() -> None:
    add_credential(
        label="Acme",
        match="github.com/Acme/*",
        pat="ghp_always-allow-unmatched-KKKK",
        is_default=False,
    )
    agent, state = _agent_and_state()
    cwd = _nest_repo(agent, "https://github.com/Widgets/app.git")
    write_nest_always_rule("git push origin main", cwd)
    policy_engine.reload()
    paused = execute_bm_cli(agent, state, "git push origin main")
    assert paused.consent_required is True
    assert paused.kind == "nest_git_consent_required"
    error = str(((paused.data or {}).get("host_path_consent") or {}).get("error") or "")
    assert NEST_GIT_NO_MATCH_WHY in error
    assert "Resource owner = the org that owns the repo" in error
    assert NEST_GIT_TOKEN_NO_REPO_OWNER[:20] in error
