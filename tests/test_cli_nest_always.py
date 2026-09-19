"""Nest CLI Always allow, command+cwd coalesce, and stale chrome."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, ensure_local_api_token, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.approvals import resume_cli_approval
from core.bm_cli.cli_always import (
    NEST_CWD_PREFIX,
    always_allow_pattern,
    offers_always_allow_cli,
    write_nest_always_rule,
)
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.models.cli_policy import CLI_APPROVAL_KIND
from tests.test_consent_origin import _ResumeServices, _channel_for
from tests.test_project_env import _lock_and_cd_clone


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


def _auth() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: ensure_local_api_token()}


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def test_offers_always_allow_cli_nest_yes_desktop_no() -> None:
    assert offers_always_allow_cli("/me/host-work/llm_helper") is True
    assert offers_always_allow_cli("/me/host-work") is True
    assert offers_always_allow_cli("/home/operator/Desktop") is False
    assert offers_always_allow_cli("/home/operator/Desktop/secret.txt") is False
    assert offers_always_allow_cli("/me") is False
    assert offers_always_allow_cli("") is False
    assert offers_always_allow_cli(None) is False


def test_always_allow_pattern_uses_argv0_or_matched_rule() -> None:
    assert always_allow_pattern("sed -i s/True/False/ tests/test_ok.py") == "sed"
    rule = db.create_cli_policy_rule(
        tier="approval_required",
        pattern="git push",
        match_mode="prefix",
    )
    assert always_allow_pattern("git push origin main", rule.id) == "git push"


def test_nest_sed_card_offers_always_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    channel = _channel_for(agent.id)
    paused = execute_bm_cli(
        agent, state, "sed -i s/True/False/ tests/test_ok.py", channel_id=channel.id,
    )
    assert paused.approval_required is True
    card = (paused.data or {}).get("cli_approval") or {}
    assert card.get("always_allow") is True
    assert card.get("cwd", "").startswith("/me/host-work/")
    stored = db.get_cli_approval_request(paused.approval_request_id)
    assert stored is not None
    assert stored.as_card()["always_allow"] is True

    client = _api_client()
    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    approvals = [item for item in res.json() if item["kind"] == "approval"]
    assert len(approvals) == 1
    assert {action["label"] for action in approvals[0]["actions"]} == {
        "Approve", "Always allow", "Reject",
    }
    assert dest.startswith("/me/host-work/")


def test_desk_pip_card_does_not_offer_always_allow() -> None:
    from tests.test_consent_origin import _agent_and_state

    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()
    agent, state = _agent_and_state()
    paused = execute_bm_cli(agent, state, "pip install pytest")
    assert paused.approval_required is True
    card = (paused.data or {}).get("cli_approval") or {}
    assert card.get("always_allow") is False
    client = _api_client()
    res = client.get("/api/needs", headers=_auth())
    labels = {action["label"] for item in res.json() if item["kind"] == "approval"
              for action in item["actions"]}
    assert labels == {"Approve", "Reject"}


def test_always_allow_writes_real_nest_rule_and_skips_next_sed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    channel = _channel_for(agent.id)
    target = real / "tests" / "test_ok.py"
    before = target.read_text(encoding="utf-8")
    paused = execute_bm_cli(
        agent, state, "sed -i s/True/False/ tests/test_ok.py", channel_id=channel.id,
    )
    assert paused.approval_request_id
    client = _api_client()
    res = client.post(
        f"/api/cli-policy/approvals/{paused.approval_request_id}/always-allow",
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["decision_note"] == "Always allowed"

    rules = [
        rule for rule in db.list_cli_policy_rules(tier="always_allowed")
        if rule.pattern == "sed" and rule.cwd_prefix == NEST_CWD_PREFIX
    ]
    assert len(rules) == 1
    assert rules[0].match_mode == "prefix"
    assert rules[0].category == "nest"

    policy_engine.reload()
    again = execute_bm_cli(
        agent, state, "sed -i s/True/False/ tests/test_ok.py", channel_id=channel.id,
    )
    assert again.approval_required is False, again.detail
    assert again.ok is True, again.prompt_content
    assert target.read_text(encoding="utf-8") != before
    assert dest.startswith("/me/host-work/")


def test_nest_always_does_not_allow_desktop_sed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    write_nest_always_rule("sed -i s/a/b/ x", "/me/host-work/sample")
    policy_engine.reload()
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    nest = policy_engine.evaluate(
        "sed -i s/a/b/ tests/x.py", frozenset(), cwd="/me/host-work/sample",
    )
    desk = policy_engine.evaluate(
        "sed -i s/a/b/ secret.txt", frozenset(), cwd=str(desktop),
    )
    home = policy_engine.evaluate(
        "sed -i s/a/b/ secret.txt", frozenset(), cwd="/me",
    )
    assert nest.tier == "always_allowed"
    assert nest.allowed is True
    assert desk.tier != "always_allowed"
    assert home.tier != "always_allowed"


def test_always_allow_on_desktop_cwd_is_rejected() -> None:
    from tests.test_consent_origin import _agent_and_state

    agent, _state = _agent_and_state()
    stored = db.create_cli_approval_request(
        agent_id=agent.id,
        command="sed -i s/a/b/ secret.txt",
        cwd="/home/operator/Desktop",
    )
    client = _api_client()
    res = client.post(
        f"/api/cli-policy/approvals/{stored.id}/always-allow",
        headers=_auth(),
    )
    assert res.status_code == 400, res.text
    assert stored.id == db.get_cli_approval_request(stored.id).id
    assert db.get_cli_approval_request(stored.id).status == "pending"
    assert not any(
        rule.pattern == "sed" and rule.cwd_prefix == NEST_CWD_PREFIX
        for rule in db.list_cli_policy_rules(tier="always_allowed")
    )


def test_duplicate_nest_sed_coalesces_one_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    channel = _channel_for(agent.id)
    first = execute_bm_cli(
        agent, state, "sed -i s/a/b/ tests/test_ok.py", channel_id=channel.id,
    )
    second = execute_bm_cli(
        agent, state, "sed -i s/a/b/ tests/test_ok.py", channel_id=channel.id,
    )
    assert first.approval_request_id == second.approval_request_id
    pending = db.list_cli_approval_requests(status="pending", agent_id=agent.id)
    assert len(pending) == 1
    cards = [
        item for item in db.list_channel_messages(channel.id)
        if item.approval_id == first.approval_request_id
    ]
    assert len(cards) == 1
    client = _api_client()
    res = client.get("/api/needs", headers=_auth())
    approvals = [item for item in res.json() if item["kind"] == "approval"]
    assert len(approvals) == 1
    assert dest.startswith("/me/host-work/")


def test_same_command_different_cwd_does_not_coalesce() -> None:
    from tests.test_consent_origin import _agent_and_state

    agent, _state = _agent_and_state()
    first = db.create_cli_approval_request(
        agent_id=agent.id, command="sed -i s/a/b/ x", cwd="/me/host-work/one",
    )
    second = db.create_cli_approval_request(
        agent_id=agent.id, command="sed -i s/a/b/ x", cwd="/me/host-work/two",
    )
    assert first.id != second.id
    pending = db.list_cli_approval_requests(status="pending", agent_id=agent.id)
    assert len(pending) == 2


@pytest.mark.asyncio
async def test_always_allow_collapses_cwd_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_consent_origin import _agent_and_state

    agent, _state = _agent_and_state()
    first = db.create_cli_approval_request(
        agent_id=agent.id, command="awk '{print}' x", cwd="/me/host-work/one",
    )
    monkeypatch.setattr(
        "db.cli_approval_requests.get_pending_for_command",
        lambda *args, **kwargs: None,
    )
    second = db.create_cli_approval_request(
        agent_id=agent.id, command="awk '{print}' x", cwd="/me/host-work/one",
    )
    other = db.create_cli_approval_request(
        agent_id=agent.id, command="awk '{print}' x", cwd="/me/host-work/two",
    )
    assert first.id != second.id
    services = _ResumeServices()
    updated = await resume_cli_approval(
        first.id, approved=True, always_allow=True, services=services,
    )
    assert updated is not None
    assert db.get_cli_approval_request(first.id).status == "approved"
    assert db.get_cli_approval_request(second.id).status == "approved"
    assert db.get_cli_approval_request(other.id).status == "pending"
    assert len(services.triggers) == 1


@pytest.mark.asyncio
async def test_stale_always_allow_does_not_wake_agent() -> None:
    from tests.test_consent_origin import _agent_and_state

    agent, _state = _agent_and_state()
    stored = db.create_cli_approval_request(
        agent_id=agent.id, command="sed -i s/a/b/ x", cwd="/me/host-work/one",
    )
    db.approve_cli_approval_request(stored.id, decision_by="human")
    services = _ResumeServices()
    missing = await resume_cli_approval(
        stored.id, approved=True, always_allow=True, services=services,
    )
    assert missing is None
    assert services.triggers == []

    client = _api_client()
    res = client.post(
        f"/api/cli-policy/approvals/{stored.id}/approve",
        headers=_auth(),
    )
    assert res.status_code == 404
    assert "not found or already resolved" in res.text
    unknown = client.post(
        "/api/cli-policy/approvals/does-not-exist/always-allow",
        headers=_auth(),
    )
    assert unknown.status_code == 404


def test_approve_once_does_not_write_always_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    paused = execute_bm_cli(agent, state, "sed -i s/a/b/ tests/test_ok.py")
    client = _api_client()
    res = client.post(
        f"/api/cli-policy/approvals/{paused.approval_request_id}/approve",
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    assert not any(
        rule.pattern == "sed" and rule.cwd_prefix == NEST_CWD_PREFIX
        for rule in db.list_cli_policy_rules(tier="always_allowed")
    )
    again = execute_bm_cli(agent, state, "sed -i s/a/b/ tests/test_ok.py")
    assert again.approval_required is True


def test_reject_stays_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    target = real / "tests" / "test_ok.py"
    before = target.read_text(encoding="utf-8")
    paused = execute_bm_cli(agent, state, "sed -i s/a/b/ tests/test_ok.py")
    client = _api_client()
    res = client.post(
        f"/api/cli-policy/approvals/{paused.approval_request_id}/reject",
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    assert db.get_cli_approval_request(paused.approval_request_id).status == "rejected"
    assert target.read_text(encoding="utf-8") == before
    again = execute_bm_cli(agent, state, "sed -i s/a/b/ tests/test_ok.py")
    assert again.approval_required is True
