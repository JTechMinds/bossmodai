"""Workspace preference consent: clone / branch / edit-host / cancel."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.runtime import execute_bm_cli
from core.agent_loop.notifications import persist_chat_notification, project_chat_notifications
from core.agent_loop.runtime_core import (
    LOCKED_WORKSPACE_COPY_STEER,
    format_runtime_core_block,
    workspace_preference_context,
)
from core.bm_cli.filesystem import agent_artifact_dir
from core.llm import context_builder
from core.models.host_path_consent import (
    WORKSPACE_PREFERENCE_BODY,
    WORKSPACE_PREFERENCE_KIND,
    WORKSPACE_PREFERENCE_TITLE,
)
from core.runtime import runtime_services


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


def _agent_and_state():
    agent = db.create_agent("Path Clerk", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _allow_host(host: Path) -> None:
    db.set_setting("workspace_host_roots", str(host.resolve()), "cli_policy")
    config.reload()


def choose_edit_host(
    client: TestClient,
    request_id: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Operator chooses Edit host directly on a pending workspace preference card."""
    response = client.post(
        f"/api/workspace-preference/{request_id}/edit-host",
        headers=headers or _headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "edit_host"
    return body


def test_card_fires_on_named_host_path_write(tmp_path: Path) -> None:
    host = tmp_path / "named-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("original\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="changed\n")
    assert paused.ok is False
    assert paused.consent_required is True
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card["kind"] == WORKSPACE_PREFERENCE_KIND
    assert card["title"] == WORKSPACE_PREFERENCE_TITLE
    assert card.get("body") == WORKSPACE_PREFERENCE_BODY
    assert card.get("git") is False
    assert fixture.read_text(encoding="utf-8") == "original\n"

    read = execute_bm_cli(agent, state, f"cat {fixture}")
    assert read.ok is True
    assert "original" in read.prompt_content


def test_workspace_preference_strips_glued_ls_flags(tmp_path: Path) -> None:
    host = tmp_path / "llm_helper"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("original\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()

    paused = execute_bm_cli(agent, state, f"write {host}/-la", content="junk\n")
    assert paused.consent_required is True
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card["kind"] == WORKSPACE_PREFERENCE_KIND
    assert card["path"] == str(host.resolve())
    assert card["grant_root"] == str(host.resolve())
    assert "/-la" not in card["path"]
    assert "/-la" not in card["grant_root"]
    assert not (host / "-la").exists()
    assert fixture.read_text(encoding="utf-8") == "original\n"


def test_cancel_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "cancel-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("keep\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="nope\n")
    request_id = paused.consent_request_id
    assert request_id
    cancelled = client.post(f"/api/workspace-preference/{request_id}/cancel", headers=_headers())
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "denied"
    assert fixture.read_text(encoding="utf-8") == "keep\n"

    again = execute_bm_cli(agent, state, f"write {fixture}", content="still nope\n")
    assert again.ok is False
    assert again.consent_required is False
    assert "blocked" in (again.detail or "").lower() or "cancelled" in (again.detail or "").lower()
    assert fixture.read_text(encoding="utf-8") == "keep\n"


def test_edit_host_requires_explicit_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "edit-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("before\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="after\n")
    assert paused.consent_required is True
    assert fixture.read_text(encoding="utf-8") == "before\n"

    request_id = paused.consent_request_id
    assert request_id
    choose_edit_host(client, request_id)

    allowed = execute_bm_cli(agent, state, f"write {fixture}", content="after\n")
    assert allowed.ok is True
    assert fixture.read_text(encoding="utf-8") == "after\n"


def test_non_git_hides_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "plain-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("x\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="y\n")
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card.get("git") is False
    request_id = paused.consent_request_id
    branched = client.post(f"/api/workspace-preference/{request_id}/branch", headers=_headers())
    assert branched.status_code == 409
    assert fixture.read_text(encoding="utf-8") == "x\n"
    pending = db.get_consent_request(request_id or "")
    assert pending is not None
    assert pending.status == "pending"


def test_git_repo_offers_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "git-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("git\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=host, check=True, capture_output=True)
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="changed\n")
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card.get("git") is True
    request_id = paused.consent_request_id
    branched = client.post(f"/api/workspace-preference/{request_id}/branch", headers=_headers())
    assert branched.status_code == 200, branched.text
    body = branched.json()
    assert body["status"] == "branched"
    assert body.get("clone_dest", "").startswith("/me/")
    assert fixture.read_text(encoding="utf-8") == "git\n"

    blocked = execute_bm_cli(agent, state, f"write {fixture}", content="host write\n")
    assert blocked.ok is False
    assert fixture.read_text(encoding="utf-8") == "git\n"


def test_clone_into_workspace_does_not_write_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "clone-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("keep host\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="changed\n")
    request_id = paused.consent_request_id
    cloned = client.post(f"/api/workspace-preference/{request_id}/clone", headers=_headers())
    assert cloned.status_code == 200, cloned.text
    body = cloned.json()
    assert body["status"] == "cloned"
    dest = body.get("clone_dest") or ""
    assert dest.startswith("/me/host-work/")
    assert fixture.read_text(encoding="utf-8") == "keep host\n"

    copy = agent_artifact_dir(agent.storage_key) / "host-work" / Path(dest).name
    assert copy.exists()
    copied = copy.read_text(encoding="utf-8") if copy.is_file() else (copy / "note.txt").read_text(encoding="utf-8")
    assert copied == "keep host\n"

    blocked = execute_bm_cli(agent, state, f"write {fixture}", content="host write\n")
    assert blocked.ok is False
    assert fixture.read_text(encoding="utf-8") == "keep host\n"


def test_desk_me_write_does_not_open_workspace_card() -> None:
    agent, state = _agent_and_state()
    result = execute_bm_cli(agent, state, "write /me/note.txt", content="desk\n")
    assert result.consent_required is False
    assert result.ok is True


def test_nested_paths_under_same_root_share_one_card(tmp_path: Path) -> None:
    host = tmp_path / "llm_helper"
    docs = host / "docs"
    docs.mkdir(parents=True)
    nested = docs / "requirements-llm-helper-bugfix.md"
    _allow_host(host)
    agent, state = _agent_and_state()

    first = execute_bm_cli(agent, state, f"write {docs / 'outline.md'}", content="outline\n")
    second = execute_bm_cli(agent, state, f"write {nested}", content="criteria\n")
    assert first.consent_required is True
    assert second.consent_required is True
    assert first.consent_request_id == second.consent_request_id
    assert (second.data or {}).get("consent_reused") is True
    pending = [
        row
        for row in db.list_consent_requests(agent_id=agent.id, status="pending")
        if (row.card_kind or "") == WORKSPACE_PREFERENCE_KIND
    ]
    assert len(pending) == 1


def test_nested_preference_does_not_post_a_second_card(tmp_path: Path) -> None:
    host = tmp_path / "llm_helper"
    docs = host / "docs"
    docs.mkdir(parents=True)
    _allow_host(host)
    agent, state = _agent_and_state()
    first = execute_bm_cli(agent, state, f"write {docs / 'outline.md'}", content="outline\n")
    second = execute_bm_cli(
        agent, state, f"write {docs / 'requirements.md'}", content="criteria\n"
    )
    trigger = {"type": "human_chat", "source_channel": "chat"}
    first_notes = project_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=None,
        action={"action": "bm_cli"},
        result={
            "event": "workspace_preference_required",
            "consent_required": True,
            "consent_request_id": first.consent_request_id,
            "consent_reused": False,
            "host_path_consent": (first.data or {}).get("host_path_consent"),
        },
    )
    assert len(first_notes) == 1
    persist_chat_notification(agent, first_notes[0])
    second_notes = project_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=None,
        action={"action": "bm_cli"},
        result={
            "event": "workspace_preference_required",
            "consent_required": True,
            "consent_request_id": second.consent_request_id,
            "consent_reused": bool((second.data or {}).get("consent_reused")),
            "host_path_consent": (second.data or {}).get("host_path_consent"),
        },
    )
    assert second_notes == []


def _lock_workspace_copy(
    agent_id: str,
    *,
    path: str,
    dest: str,
    status: str = "branched",
    task_id: str | None = None,
):
    pending = db.create_consent_request(
        agent_id=agent_id,
        path=path,
        grant_root=path,
        reason=WORKSPACE_PREFERENCE_BODY,
        card_kind=WORKSPACE_PREFERENCE_KIND,
        is_git=status == "branched",
        task_id=task_id,
    )
    updated = db.resolve_consent_request(pending.id, status=status, clone_dest=dest)
    assert updated is not None
    return updated


def test_runtime_core_steers_off_host_direct_when_workspace_copy_locked() -> None:
    agent, state = _agent_and_state()
    host_path = "/home/operator/Projects/sample_repo"
    dest = "/me/host-work/sample_repo"
    task = db.create_task("Harness", assigned_to=agent.id)
    task_id = task.id
    _lock_workspace_copy(agent.id, path=host_path, dest=dest, task_id=task_id)

    block = format_runtime_core_block(agent, task_id=task_id)
    assert LOCKED_WORKSPACE_COPY_STEER in block
    assert "Stay on the clone" in block
    assert "Do not recommend editing the live host tree" in block
    assert "Do not park @Operator to reopen" in block
    assert "Do not park @Operator as the test runner or git pusher" in block
    assert "Do not invent a desk deny" in block
    assert "Do not pip install into the host Python" in block
    assert f"Locked workspace copy for {host_path}: work at {dest}." in block
    assert "Host writes stay blocked." in block
    assert "work directly in the host tree" not in block.lower()

    preference = workspace_preference_context(agent_id=agent.id, task_id=task_id)
    assert preference["preference"] == "branched"
    assert preference["clone_dest"] == dest

    context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger={
                "type": "channel_message",
                "source_channel": "channel",
                "content": "Where should the working tree live?",
                "from_name": "Human Operator",
            },
            conversation_history=[],
            prompt_notifications=[],
            reference_materials=[],
            current_task={
                "id": task_id,
                "title": "Harness",
                "status": "open",
                "description": "Validate the test harness.",
            },
            contract_kind="decision",
        )
    )
    core_msgs = [
        str(message.get("content") or "")
        for message in context
        if str(message.get("content") or "").startswith("# Runtime core")
    ]
    assert core_msgs
    assert LOCKED_WORKSPACE_COPY_STEER in core_msgs[0]
    assert dest in core_msgs[0]
    assert "Do not park @Operator to reopen" in core_msgs[0]
    assert "Do not park @Operator as the test runner or git pusher" in core_msgs[0]


def test_runtime_core_steers_teammate_off_host_direct_when_task_branch_locked() -> None:
    writer, _ = _agent_and_state()
    lead = db.create_agent("Lead Clerk", role="Coordinator")
    host_path = "/home/operator/Projects/sample_repo"
    dest = "/me/host-work/sample_repo"
    task = db.create_task("Harness", assigned_to=writer.id)
    task_id = task.id
    _lock_workspace_copy(writer.id, path=host_path, dest=dest, task_id=task_id)

    block = format_runtime_core_block(lead, task_id=task_id)
    assert LOCKED_WORKSPACE_COPY_STEER in block
    assert dest in block
    assert "Do not recommend editing the live host tree" in block
    assert "Do not park @Operator to reopen" in block
    assert "Do not park @Operator as the test runner or git pusher" in block


def test_runtime_core_does_not_inject_clone_dest_for_edit_host() -> None:
    agent, _ = _agent_and_state()
    host_path = "/home/operator/Projects/sample_repo"
    _lock_workspace_copy(
        agent.id,
        path=host_path,
        dest="/me/host-work/unused",
        status="edit_host",
    )
    block = format_runtime_core_block(agent)
    assert LOCKED_WORKSPACE_COPY_STEER in block
    assert "/me/host-work/unused" not in block
    assert f"Locked workspace copy for {host_path}" not in block
    preference = workspace_preference_context(agent_id=agent.id)
    assert preference["preference"] == ""
    assert preference["clone_dest"] == ""


def test_runtime_core_steers_off_host_direct_when_clone_locked() -> None:
    agent, _ = _agent_and_state()
    host_path = "/home/operator/Projects/sample_repo"
    dest = "/me/host-work/sample_repo"
    _lock_workspace_copy(agent.id, path=host_path, dest=dest, status="cloned")
    block = format_runtime_core_block(agent)
    assert LOCKED_WORKSPACE_COPY_STEER in block
    assert dest in block
    assert "Do not recommend editing the live host tree" in block
    preference = workspace_preference_context(agent_id=agent.id)
    assert preference["preference"] == "cloned"
    assert preference["clone_dest"] == dest


def _enable_shell() -> None:
    from core.bm_cli.policy_engine import policy_engine

    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _init_tiny_pytest_repo(host: Path) -> None:
    """Standalone git repo whose pytest.ini stops upward config discovery."""
    tests = host / "tests"
    tests.mkdir()
    (host / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (tests / "test_ok.py").write_text("def test_ok() -> None:\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=host, check=True, capture_output=True)


def test_validate_on_clone_is_agent_owned_not_a_desk_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Desk /me is not the limiter. After Branch lock, cli owns validate-on-clone.

    Case evidence (invented desk-can't vs real CLI gaps):
    - /me and /me/host-work are a real workspace (write, echo, cd, pytest, local git).
    - python -m pytest and bash stay never_allowed (policy, not desk).
        - git push pauses for nest git auth first, then approval
          (not Operator-as-runner; always-allow does not skip auth).
    """
    from core.bm_cli.policy_engine import policy_engine
    from core.bm_cli.workspace_preference import cwd_is_nested_clone_repo

    host = tmp_path / "llm_helper"
    host.mkdir()
    _init_tiny_pytest_repo(host)
    _allow_host(host)
    _enable_shell()
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    desk_write = execute_bm_cli(agent, state, "write /me/scratch.txt", content="desk-ok\n")
    assert desk_write.ok is True

    paused = execute_bm_cli(agent, state, f"write {host / 'note.txt'}", content="changed\n")
    request_id = paused.consent_request_id
    assert request_id
    branched = client.post(f"/api/workspace-preference/{request_id}/branch", headers=_headers())
    assert branched.status_code == 200, branched.text
    dest = branched.json().get("clone_dest") or ""
    assert dest.startswith("/me/host-work/")

    blocked_host = execute_bm_cli(agent, state, f"write {host / 'note.txt'}", content="host write\n")
    assert blocked_host.ok is False
    assert not (host / "note.txt").exists() or (host / "note.txt").read_text(encoding="utf-8") != "host write\n"

    cd = execute_bm_cli(agent, state, f"cd {dest}")
    assert cd.ok is True
    assert cwd_is_nested_clone_repo(agent, dest) is True

    echo = execute_bm_cli(agent, state, "echo clone-shell-ok")
    assert echo.ok is True
    assert echo.executor == "shell"
    assert "clone-shell-ok" in (echo.prompt_content or "")

    python_pytest = execute_bm_cli(agent, state, "python -m pytest -q")
    assert python_pytest.ok is False
    assert python_pytest.approval_required is False
    python_decision = policy_engine.evaluate("python -m pytest -q", frozenset())
    assert python_decision.tier == "never_allowed"

    bash_script = execute_bm_cli(agent, state, "bash scripts/run-tests.sh")
    assert bash_script.ok is False
    assert policy_engine.evaluate("bash scripts/run-tests.sh", frozenset()).tier == "never_allowed"

    collected = execute_bm_cli(agent, state, "pytest -q tests/test_ok.py")
    assert collected.ok is True, collected.prompt_content
    assert collected.executor == "shell"
    assert collected.exit_code == 0
    payload = collected.prompt_content or ""
    assert "1 passed" in payload or "passed" in payload.lower()

    extra = execute_bm_cli(
        agent,
        state,
        f"write {dest}/tests/test_extra.py",
        content="def test_extra() -> None:\n    assert 1 + 1 == 2\n",
    )
    assert extra.ok is True

    added = execute_bm_cli(agent, state, "git add tests/test_extra.py")
    assert added.ok is True, added.prompt_content
    committed = execute_bm_cli(agent, state, "git commit -m validate-on-clone")
    assert committed.ok is True, committed.prompt_content
    assert committed.executor == "shell"
    assert committed.exit_code == 0

    push = execute_bm_cli(agent, state, "git push origin HEAD")
    assert push.ok is False
    assert push.consent_required is True
    assert push.kind == "nest_git_consent_required"
    from core.bm_cli.nest_git import write_nest_git_secret
    from core.models.nest_git import NEST_GIT_PAT_KEY

    write_nest_git_secret(NEST_GIT_PAT_KEY, "ghp_validate-on-clone-pat")
    approved = execute_bm_cli(agent, state, "git push origin HEAD")
    assert approved.ok is False
    assert approved.approval_required is True
    assert approved.kind == "approval_required"
