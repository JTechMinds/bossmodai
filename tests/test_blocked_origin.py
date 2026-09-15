"""Blocked origin: why + @NextOwner wake, quieter auto GH, in-thread host deny.

Debra's locked order:
1. Origin line is ``Blocked — {why}. @NextOwner`` (not bare no progress).
2. ``@NextOwner`` on that line wakes them. Tag is not hope.
3. Auto GH only when there is no next owner and no origin line.
4. Host-deny after Branch still posts the why; suppress_*_broadcast does not bury it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import db
from core import config
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.auto_github import (
    AUTO_GH_EVENT_PREFIX,
    list_opened_auto_github_issues,
    reset_opened_auto_github_issues,
)
from core.agent_loop.blocked_origin import (
    HOST_DENY_WHY,
    format_blocked_line,
    is_host_deny_result,
    should_open_auto_github_issue,
)
from core.agent_loop.soft_blocks import NO_PROGRESS_LINE, apply_no_progress_block
from core.bm_cli.results import error_result
from core.bm_cli.runtime import execute_bm_cli
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    reset_opened_auto_github_issues()


def teardown_function() -> None:
    db.close_connection()


def _named(agent, line: str) -> str:
    return f"{agent.name} {line}"


def _thread_task(*, assignee_id: str, channel_id: str, title: str = "Spec"):
    return create_or_bind_task(
        title=title,
        description="Author the spec.",
        project=None,
        assigned_to=assignee_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel_id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


def _jim_debra_channel():
    jim = db.create_agent("Jim", role="Implementation Spec Author", desk_x=1, desk_y=1)
    debra = db.create_agent("Debra", role="Requirements Analyst", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Jim, Debra",
        member_agent_ids=[jim.id, debra.id],
        created_by=HUMAN_SENDER_ID,
    )
    return jim, debra, channel


def _allow_host(host: Path) -> None:
    db.set_setting("workspace_host_roots", str(host.resolve()), "cli_policy")
    config.reload()


def _init_git_host(host: Path) -> Path:
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("git\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=host, check=True, capture_output=True)
    return fixture


def _debra_wakes(debra_id: str) -> list:
    return [
        row
        for row in db.list_agent_triggers(debra_id, status="queued")
        if row.get("trigger_type") in {"channel_message", "task_follow_up", "peer_message"}
    ]


def test_blocked_line_is_why_plus_next_owner() -> None:
    """1. Origin copy is Blocked — {why}. @NextOwner, not a bare no-progress dump."""
    assert format_blocked_line("no progress", "@Debra") == "Blocked — no progress. @Debra"
    assert format_blocked_line(HOST_DENY_WHY, "@Debra") == "Blocked — host deny. @Debra"
    assert format_blocked_line(HOST_DENY_WHY, "@Debra") != NO_PROGRESS_LINE
    assert format_blocked_line(HOST_DENY_WHY) == "Blocked — host deny"


def test_no_progress_origin_wakes_tagged_next_owner() -> None:
    """2. @Debra on the no-progress line is a wake, not a hope tag."""
    jim, debra, channel = _jim_debra_channel()
    creation = _thread_task(assignee_id=jim.id, channel_id=channel.id)
    activate_work_activity(jim.id, creation.task)
    result = apply_no_progress_block(
        jim,
        {"type": "channel_response", "channel_id": channel.id},
    )
    persist_result_triggers(result)
    expected = _named(jim, "Blocked — no progress. @Debra")
    assert result["detail"] == "Blocked — no progress. @Debra"
    assert any(
        item.author_type == "system" and (item.content or "") == expected
        for item in db.list_channel_messages(channel.id)
    )
    assert result["auto_github_issue"] is False
    assert _debra_wakes(debra.id), "tagged next owner must receive a wake"


def test_auto_github_does_not_open_when_next_owner_is_on_origin_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3. Persist reads the flag and does not open Auto GH when @NextOwner is named."""
    opener_calls: list[dict] = []

    def _capture_open(**kwargs):
        opener_calls.append(kwargs)
        raise AssertionError("open_auto_github_issue must not run when @Debra is named")

    monkeypatch.setattr(
        "core.agent_loop.auto_github.open_auto_github_issue",
        _capture_open,
    )
    jim, _debra, channel = _jim_debra_channel()
    creation = _thread_task(assignee_id=jim.id, channel_id=channel.id)
    activate_work_activity(jim.id, creation.task)
    result = apply_no_progress_block(
        jim,
        {"type": "channel_response", "channel_id": channel.id},
    )
    expected = _named(jim, "Blocked — no progress. @Debra")
    assert any(
        item.author_type == "system" and (item.content or "") == expected
        for item in db.list_channel_messages(channel.id)
    )
    assert result["auto_github_issue"] is False
    persist_result_triggers(result)
    assert opener_calls == []
    assert list_opened_auto_github_issues() == []
    events = db.list_task_events(creation.task.id)
    assert not any(
        (event.content or "").startswith(AUTO_GH_EVENT_PREFIX) for event in events
    )


def test_auto_github_opens_only_when_no_owner_and_no_origin_line() -> None:
    """3. Persist reads auto_github_issue; opener runs only when the flag is True."""
    assert should_open_auto_github_issue(origin_line=None, next_owner=None) is True
    persist_result_triggers(
        {
            "auto_github_issue": False,
            "auto_github": {
                "title": "Blocked — no progress. @Debra",
                "body": "Blocked — no progress. @Debra",
                "origin_line": "Jim Blocked — no progress. @Debra",
                "next_owner": "@Debra",
            },
        }
    )
    assert list_opened_auto_github_issues() == []

    opened_result = {
        "auto_github_issue": True,
        "auto_github": {
            "title": "Blocked — no progress",
            "body": "Blocked — no progress",
            "origin_line": None,
            "next_owner": None,
        },
    }
    persist_result_triggers(opened_result)
    assert opened_result["auto_github"]["opened"] is True
    assert opened_result["auto_github"]["issue"]["number"] == 1
    assert opened_result["auto_github_issue"] is True
    assert list_opened_auto_github_issues() == [opened_result["auto_github"]["issue"]]


@pytest.mark.asyncio
async def test_host_deny_after_branch_surfaces_in_thread(tmp_path: Path) -> None:
    """4. Host-deny after Branch posts Blocked — host deny. @NextOwner in-thread."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
    from api.routes import router
    from core.runtime import runtime_services

    async def _persist_trigger(**kwargs):
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )

    runtime_services.enqueue_trigger = _persist_trigger
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    client = TestClient(app)
    headers = {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}

    jim, debra, channel = _jim_debra_channel()
    creation = _thread_task(assignee_id=jim.id, channel_id=channel.id)
    activate_work_activity(jim.id, creation.task)
    state = db.get_agent_state(jim.id)
    assert state is not None

    host = tmp_path / "git-root"
    fixture = _init_git_host(host)
    _allow_host(host)

    paused = execute_bm_cli(jim, state, f"write {fixture}", content="changed\n")
    assert paused.consent_required is True
    request_id = paused.consent_request_id
    assert request_id
    branched = client.post(f"/api/workspace-preference/{request_id}/branch", headers=headers)
    assert branched.status_code == 200, branched.text
    assert branched.json()["status"] == "branched"

    denied = execute_bm_cli(jim, state, f"write {fixture}", content="host write\n")
    assert denied.ok is False
    assert is_host_deny_result(denied)
    assert fixture.read_text(encoding="utf-8") == "git\n"

    result = await execute_action(
        {"action": "bm_cli", "command": f"write {fixture}", "content": "host write\n"},
        jim,
        state,
        trigger={"type": "channel_response", "channel_id": channel.id},
    )
    persist_result_triggers(result)

    assert result["suppress_world_broadcast"] is True
    assert result["suppress_activity_broadcast"] is True
    expected = _named(jim, "Blocked — host deny. @Debra")
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert expected in contents
    assert not any(
        (item or "").startswith(_named(jim, "Blocked — no progress"))
        for item in contents
    )
    assert result["auto_github_issue"] is False
    assert _debra_wakes(debra.id), "host-deny origin tag must wake Debra"


def test_host_deny_kind_is_detected_on_cli_error() -> None:
    denied = error_result(
        "write /tmp/x",
        "Host writes stay blocked. Work in the agent workspace copy at /me.",
        kind="host_deny",
    )
    assert is_host_deny_result(denied)
    ok = SimpleNamespace(ok=True, kind="generic", detail="read notes", data={})
    assert is_host_deny_result(ok) is False
