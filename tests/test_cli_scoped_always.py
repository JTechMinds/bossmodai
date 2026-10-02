"""Scoped CLI Always allow: scope selection, floor isolation, containment, pattern.

An Always rule covers one locked clone (every nest clone, today's
behaviour), one floor project (floor-bound) or the agent's own ``/me``
(agent-bound). A scoped rule whose command reaches outside its scope goes
back to the approval path. A subcommand tool is saved with its subcommand.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from tests._connections import model_connection
from api.auth import LOCAL_API_TOKEN_HEADER, ensure_local_api_token, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.approvals import resume_cli_approval
from core.bm_cli.cli_always import (
    NEST_CWD_PREFIX,
    AlwaysScope,
    always_allow_pattern,
    always_scope_for,
    contain_scoped_always,
    write_scoped_always_rule,
)
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.floor_roots import project_dir
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli, preview_bm_cli
from core.bm_cli.session import set_cli_cwd
from db.floors import LOBBY_ID, create_floor
from tests.test_consent_origin import _ResumeServices
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


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _agent(name: str = "Scope Clerk"):
    agent = db.create_agent(name, role="Eng", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _demo_project() -> Path:
    # New agents live in Lobby, so their /projects is Lobby's folder.
    root = project_dir(LOBBY_ID, "demo")
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("keep", encoding="utf-8")
    return root


def _approval(agent_id: str, command: str, cwd: str, matched_rule_id: str | None = None):
    return db.create_cli_approval_request(
        agent_id=agent_id, command=command, cwd=cwd, matched_rule_id=matched_rule_id,
    )


def _project_always(pattern: str, floor_id: str = LOBBY_ID):
    rule = db.create_cli_policy_rule(
        tier="always_allowed",
        pattern=pattern,
        match_mode="prefix",
        cwd_prefix="/projects/demo",
        floor_id=floor_id,
    )
    policy_engine.reload()
    return rule


# ---------------------------------------------------------------------------
# Scope selection
# ---------------------------------------------------------------------------


def test_scope_is_locked_clones_for_a_command_inside_one_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    approval = _approval(agent.id, "sed -i s/True/False/ tests/test_ok.py", dest)

    assert always_scope_for(agent, approval) == AlwaysScope(
        NEST_CWD_PREFIX, "locked clones", None, None,
    )


def test_scope_is_the_floor_project_for_a_command_inside_it() -> None:
    agent, _state = _agent()
    _demo_project()
    approval = _approval(agent.id, "cp notes.txt copy.txt", "/projects/demo")

    assert always_scope_for(agent, approval) == AlwaysScope(
        "/projects/demo", "demo", LOBBY_ID, None,
    )


def test_scope_is_the_agents_own_me_for_a_command_inside_it() -> None:
    agent, _state = _agent()
    (agent_artifact_dir(agent.storage_key) / "draft.txt").write_text("d", encoding="utf-8")
    approval = _approval(agent.id, "cp draft.txt draft2.txt", "/me")

    assert always_scope_for(agent, approval) == AlwaysScope(
        "/me", "your workspace", None, agent.id,
    )


@pytest.mark.parametrize(
    ("command", "cwd"),
    [
        ("cp notes.txt /me/notes.txt", "/projects/demo"),
        ("cp draft.txt /projects/demo/draft.txt", "/me"),
        ("git", "/projects/demo"),
        ("npm --prefix /projects/demo install", "/projects/demo"),
        ("timeout 5 cp notes.txt /me/notes.txt", "/projects/demo"),
    ],
)
def test_no_scope_when_paths_span_scopes_or_no_subcommand(command: str, cwd: str) -> None:
    agent, _state = _agent()
    _demo_project()
    approval = _approval(agent.id, command, cwd)

    assert always_scope_for(agent, approval) is None
    with pytest.raises(ValueError):
        write_scoped_always_rule(agent, approval)


# ---------------------------------------------------------------------------
# Pattern: tool + subcommand, never a bare git
# ---------------------------------------------------------------------------


def test_pattern_names_the_command_a_wrapper_runs() -> None:
    assert always_allow_pattern("timeout 5 cp a b") == "cp"
    assert always_allow_pattern("nohup git push") == "git push"
    assert always_allow_pattern("env FOO=1 nohup git -C /projects/demo push origin") == "git push"
    assert always_allow_pattern("nohup git") is None
    # find runs more than one command: one rule cannot cover them.
    assert always_allow_pattern("find . -exec cp {} x \\;") is None
    assert always_allow_pattern("find . -delete") is None


def test_wrapped_command_in_a_project_offers_its_own_always() -> None:
    agent, _state = _agent()
    _demo_project()
    approval = _approval(agent.id, "timeout 5 cp notes.txt copy.txt", "/projects/demo")

    assert always_scope_for(agent, approval) == AlwaysScope("/projects/demo", "demo", LOBBY_ID, None)
    rule = write_scoped_always_rule(agent, approval)
    assert rule.pattern == "cp"
    assert rule.cwd_prefix == "/projects/demo"


def test_no_always_offered_for_a_find_that_runs_another_command() -> None:
    agent, _state = _agent()
    _demo_project()
    approval = _approval(agent.id, "find . -exec cp {} x \\;", "/projects/demo")

    assert always_scope_for(agent, approval) is None
    assert approval.as_card()["always_allow"] is False


def test_pattern_names_the_subcommand_for_subcommand_tools() -> None:
    assert always_allow_pattern("git push origin main") == "git push"
    assert always_allow_pattern("git -C /projects/demo --no-pager push origin") == "git push"
    assert always_allow_pattern("gh pr create --fill") == "gh pr"
    assert always_allow_pattern("uv run pytest") == "uv run"
    assert always_allow_pattern("sed -i s/a/b/ x") == "sed"
    assert always_allow_pattern("git") is None


def test_pattern_reuses_a_matched_rule_but_never_a_bare_tool() -> None:
    push = db.create_cli_policy_rule(tier="approval_required", pattern="git push")
    bare = db.create_cli_policy_rule(tier="approval_required", pattern="git")

    assert always_allow_pattern("git push origin main", push.id) == "git push"
    assert always_allow_pattern("git push origin main", bare.id) == "git push"


# ---------------------------------------------------------------------------
# Floor isolation
# ---------------------------------------------------------------------------


def test_project_rule_matches_only_agents_on_its_floor() -> None:
    _enable_shell()
    home, _ = _agent("Home Clerk")
    away, _ = _agent("Away Clerk")
    other = create_floor("Elsewhere")
    assert db.update_agent(away.id, floor_id=other.id) is not None
    _project_always("cp")

    def _tier(agent_id: str | None) -> str:
        return policy_engine.evaluate(
            "cp a b", frozenset(), agent_id=agent_id, cwd="/projects/demo",
        ).tier

    assert _tier(home.id) == "always_allowed"
    assert _tier(away.id) != "always_allowed"
    assert _tier(None) != "always_allowed"
    rows = db.get_cli_policy_rules_by_tier("always_allowed", agent_id=away.id, floor_id=other.id)
    assert all(row.floor_id is None for row in rows)


def test_me_rule_matches_only_its_agent() -> None:
    _enable_shell()
    owner, _ = _agent("Owner")
    stranger, _ = _agent("Stranger")
    db.create_cli_policy_rule(
        tier="always_allowed", pattern="cp", cwd_prefix="/me", agent_id=owner.id,
    )
    policy_engine.reload()

    assert policy_engine.evaluate("cp a b", frozenset(), owner.id, cwd="/me").tier == "always_allowed"
    assert policy_engine.evaluate("cp a b", frozenset(), stranger.id, cwd="/me").tier != "always_allowed"


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------


def test_project_always_runs_inside_and_asks_when_a_path_leaves_the_project() -> None:
    _enable_shell()
    agent, state = _agent()
    root = _demo_project()
    rule = _project_always("cp")
    set_cli_cwd(agent.id, "/projects/demo")

    inside = execute_bm_cli(agent, state, "cp notes.txt copy.txt")
    assert inside.ok is True, inside.prompt_content
    assert (root / "copy.txt").exists()

    leak = agent_artifact_dir(agent.storage_key) / "leak.txt"
    outside = execute_bm_cli(agent, state, "cp notes.txt /me/leak.txt")
    assert outside.approval_required is True
    assert "Always rule cp covers demo only; this command reaches outside it" in (outside.detail or "")
    assert not leak.exists()
    pending = db.get_cli_approval_request(outside.approval_request_id)
    assert pending is not None and pending.matched_rule_id == rule.id

    # A wrapper does not hide the reach: the wrapped cp matches the rule
    # and its paths are checked against the project.
    wrapped = execute_bm_cli(agent, state, "timeout 5 cp notes.txt /me/leak.txt")
    assert wrapped.approval_required is True
    assert "covers demo only" in (wrapped.detail or "")
    assert not leak.exists()


def test_containment_leaves_nest_and_unscoped_rules_alone() -> None:
    _enable_shell()
    agent, _state = _agent()
    _demo_project()
    nest = db.create_cli_policy_rule(
        tier="always_allowed", pattern="git", cwd_prefix=NEST_CWD_PREFIX, category="nest",
    )
    policy_engine.reload()
    decision = policy_engine.evaluate(
        "git frobnicate", frozenset(), agent.id, cwd="/me/host-work/sample",
    )
    assert decision.tier == "always_allowed" and decision.matched_rule_id == nest.id

    parsed = parse_cli_command("git frobnicate /projects/demo")
    assert contain_scoped_always(agent, parsed, "/me/host-work/sample", decision) is decision


# ---------------------------------------------------------------------------
# Always allow end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_always_allow_writes_a_floor_project_rule_and_scoped_note() -> None:
    _enable_shell()
    agent, _state = _agent()
    _demo_project()
    approval = _approval(agent.id, "cp notes.txt copy.txt", "/projects/demo")
    card = approval.as_card()
    assert card["always_allow"] is True
    assert card["always_scope_label"] == "demo"

    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    needs = TestClient(app).get("/api/needs", headers={LOCAL_API_TOKEN_HEADER: ensure_local_api_token()})
    assert needs.status_code == 200, needs.text
    item = next(row for row in needs.json() if row["kind"] == "approval")
    assert "Always allow in demo" in {action["label"] for action in item["actions"]}
    assert item["always_scope_label"] == "demo"

    services = _ResumeServices()
    resolved = await resume_cli_approval(
        approval.id, approved=True, always_allow=True, services=services,
    )
    assert resolved is not None
    assert resolved.decision_note == "Always allowed in demo"
    assert services.triggers[0]["payload"]["decision_note"] == "Always allowed in demo"
    assert "always_scope_label" not in resolved.as_card()
    rules = [
        rule for rule in db.list_cli_policy_rules(tier="always_allowed")
        if rule.cwd_prefix == "/projects/demo"
    ]
    assert [(rule.pattern, rule.floor_id, rule.agent_id) for rule in rules] == [
        ("cp", LOBBY_ID, None),
    ]
    precedents = db.list_human_cli_decisions(
        [agent.id], limit=10, excluded_note_prefix="Always allowed",
    )
    assert precedents == []


# ---------------------------------------------------------------------------
# Host-process commands (audit B1) and dry-run parity (audit B2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["kill 123", "nohup docker ps", "env kill 123"])
def test_no_always_offered_for_a_host_process_command(command: str) -> None:
    agent, _state = _agent()
    approval = _approval(agent.id, command, "/me")

    assert always_scope_for(agent, approval) is None
    assert approval.as_card()["always_allow"] is False
    with pytest.raises(ValueError):
        write_scoped_always_rule(agent, approval)


def test_existing_me_kill_rule_goes_back_to_approval() -> None:
    _enable_shell()
    agent, state = _agent()
    rule = db.create_cli_policy_rule(
        tier="always_allowed", pattern="kill", cwd_prefix="/me", agent_id=agent.id,
    )
    policy_engine.reload()
    set_cli_cwd(agent.id, "/me")
    decision = policy_engine.evaluate("kill 123", frozenset(), agent.id, cwd="/me")
    assert decision.tier == "always_allowed" and decision.matched_rule_id == rule.id

    contained = contain_scoped_always(agent, parse_cli_command("kill 123"), "/me", decision)
    assert contained.approval_required is True
    assert "affects host processes or containers" in (contained.message or "")

    result = execute_bm_cli(agent, state, "kill 123")
    assert result.approval_required is True


def test_dry_run_matches_real_run_under_a_scoped_rule() -> None:
    _enable_shell()
    agent, state = _agent()
    root = _demo_project()
    _project_always("cp")
    set_cli_cwd(agent.id, "/projects/demo")

    # The company root outlives one test's database, so start clean.
    (root / "dry-copy.txt").unlink(missing_ok=True)
    dry_inside = preview_bm_cli(agent, state, "cp notes.txt dry-copy.txt")
    assert dry_inside.ok is True and dry_inside.kind == "dry_run"
    assert (dry_inside.data or {}).get("policy_tier") == "always_allowed"
    assert not (root / "dry-copy.txt").exists()
    real_inside = execute_bm_cli(agent, state, "cp notes.txt dry-copy.txt")
    assert real_inside.ok is True, real_inside.prompt_content
    assert (root / "dry-copy.txt").exists()

    dry_outside = preview_bm_cli(agent, state, "cp notes.txt /me/leak.txt")
    real_outside = execute_bm_cli(agent, state, "cp notes.txt /me/leak.txt")
    assert dry_outside.approval_required is True
    assert real_outside.approval_required is True
    assert "covers demo only" in (dry_outside.detail or "")
    assert "covers demo only" in (real_outside.detail or "")
