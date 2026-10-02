"""CLI approval gate: facts, invariants, review context, verdict parse.

Auto-approve is off by default. An active thread's flag, an agent's DM
flag, or Global auto-approve (Settings → Advanced) turns the gate on, and
Global wins over both. Hard blocks still win.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, ensure_local_api_token, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.cli_turn_result import map_cli_result
from core.agent_loop.work_binding import bind_turn
from api.routes.settings import _operator_surfaces_for_setting
from core.bm_cli.approval_gate import AUDIT_PREFIX, auto_approve_effective
from core.config import ConfigError
from core.bm_cli.approval_gate.context import build_review_context, command_shape
from core.bm_cli.approval_gate.effects import classify_effect
from core.bm_cli.approval_gate.facts import command_facts, paths_within
from core.bm_cli.approval_gate.gate import (
    NOT_ASKED_HOST_PROCESS,
    NOT_ASKED_ROOT,
    REVIEW_ERROR_PREFIX,
    UNSURE_PREFIX,
)
from core.bm_cli.approval_gate.review import parse_verdict
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.floor_roots import floor_root, project_dir
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.session import set_cli_cwd
from db.floors import LOBBY_ID, create_floor

ROOT = Path(__file__).resolve().parent.parent
_COMPLETE = "core.bm_cli.approval_gate.review.complete_text"


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


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: ensure_local_api_token()}


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _agent_and_state(name: str = "Ops Clerk"):
    agent = db.create_agent(name, role="Eng")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _thread(agent_id: str, *, enabled: bool):
    channel = db.create_channel(name="Ops", member_agent_ids=[agent_id])
    if enabled:
        channel = db.update_channel(channel.id, cli_auto_approve=True)
    assert channel is not None
    return channel


def _project_file(name: str = "demo") -> Path:
    # New agents live in Lobby, so their /projects is Lobby's folder.
    root = project_dir(LOBBY_ID, name)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "notes.txt"
    path.write_text("keep", encoding="utf-8")
    return path


def _outside_repo(tmp_path: Path) -> Path:
    """A real git repository outside every path-jail root."""
    root = tmp_path / "outside-root"
    root.mkdir()
    init = subprocess.run(
        ["git", "-C", str(root), "init"], capture_output=True, text=True, check=False
    )
    assert init.returncode == 0, init.stderr
    return root


def _verdict(decision: str, basis: str, why: str):
    def _complete(_messages):
        return json.dumps({"decision": decision, "basis": basis, "why": why})

    return _complete


def _boom_if_asked(reason: str):
    def _boom(_messages):
        raise AssertionError(reason)

    return _boom


def _user_payload(messages: list) -> dict:
    user = next(item for item in messages if item.get("role") == "user")
    return json.loads(user["content"])


def _card_note(result) -> str:
    return str(((result.data or {}).get("cli_approval") or {}).get("review_note") or "")


# ── facts ──────────────────────────────────────────────────────────────


def test_facts_label_virtual_and_real_paths() -> None:
    agent, _state = _agent_and_state()
    notes = _project_file()
    scratch = agent_artifact_dir(agent.storage_key) / "scratch.txt"
    scratch.write_text("x", encoding="utf-8")
    parsed = parse_cli_command("rm notes.txt /me/scratch.txt")

    facts = command_facts(agent, parsed, "/projects/demo")

    assert facts.effect == "delete"
    assert facts.cwd_real == notes.parent.resolve()
    by_virtual = {fact.virtual: fact for fact in facts.write_targets}
    project = by_virtual["/projects/demo/notes.txt"]
    assert project.real == notes.resolve()
    assert project.workspace == "project"
    assert project.label == "/projects/demo/notes.txt (floor project demo)"
    assert project.is_root is False
    me = by_virtual["/me/scratch.txt"]
    assert me.real == scratch.resolve()
    assert me.workspace == "me"
    assert "/me" in me.label


def test_facts_detect_whole_roots() -> None:
    agent, _state = _agent_and_state()
    _project_file()
    (project_dir(LOBBY_ID, "demo") / ".git").mkdir()
    clone = agent_artifact_dir(agent.storage_key) / "host-work" / "agentmp"
    (clone / "src").mkdir(parents=True)
    command = (
        "rm -rf /projects /projects/demo /projects/demo/notes.txt /projects/demo/.git "
        "/me /me/host-work/agentmp /me/host-work/agentmp/src"
    )
    facts = command_facts(agent, parse_cli_command(command), "/me")

    roots = {fact.virtual: fact.is_root for fact in facts.write_targets}
    assert roots == {
        "/projects": True,
        "/projects/demo": True,
        "/projects/demo/notes.txt": False,
        "/projects/demo/.git": True,
        "/me": True,
        "/me/host-work/agentmp": True,
        "/me/host-work/agentmp/src": False,
    }
    clone_fact = next(f for f in facts.write_targets if f.virtual == "/me/host-work/agentmp")
    assert clone_fact.workspace == "clone"
    assert "locked clone" in clone_fact.label
    assert paths_within(
        command_facts(agent, parse_cli_command("rm notes.txt"), "/projects/demo"),
        project_dir(LOBBY_ID, "demo").resolve(),
    )
    assert not paths_within(facts, floor_root(LOBBY_ID).resolve())


@pytest.mark.parametrize(
    ("command", "effect"),
    [
        ("rm notes.txt", "delete"),
        ("git rm notes.txt", "delete"),
        ("git clean -fd", "delete"),
        ("mv a.txt b.txt", "local_write"),
        ("git -C /projects/demo commit -m wip", "local_write"),
        ("git push origin main", "network_write"),
        ("gh pr create --fill", "network_write"),
        ("gh repo delete o/r", "network_write"),
        ("gh release create v1", "network_write"),
        ("gh api repos/o/r/issues -X POST", "network_write"),
        ("gh api repos/o/r/issues --method=PATCH", "network_write"),
        ("gh api repos/o/r/issues -f title=x", "network_write"),
        ("npm publish", "network_write"),
        ("curl -d a=b https://example.com", "network_write"),
        ("curl -XPOST https://example.com", "network_write"),
        ("git clone https://example.com/r.git", "network_read"),
        ("git fetch origin", "network_read"),
        ("gh api repos/o/r", "network_read"),
        ("gh pr view 3", "network_read"),
        ("curl https://example.com", "network_read"),
        ("wget https://example.com", "network_read"),
        ("pip install requests", "install"),
        ("npm install --prefix /projects/demo", "install"),
        ("uv add httpx", "install"),
        ("uv pip install httpx", "install"),
        ("kill 1234", "host_process"),
        ("pkill -f node", "host_process"),
        ("killall node", "host_process"),
        ("docker ps", "host_process"),
        ("cat notes.txt", "read_only"),
        ("git status", "read_only"),
        ("git -C /projects/demo ls-files", "read_only"),
        ("git remote -v", "read_only"),
        ("git remote add origin x", "unknown"),
        ("tar -tf a.tar", "unknown"),
        ("tar -tzf a.tar.gz", "unknown"),
        ("tar -xf a.tar", "unknown"),
        ("sleep 1", "read_only"),
        ("find . -name '*.md'", "read_only"),
        ("find . -delete", "unknown"),
        ("find . -exec rm {} ;", "unknown"),
        ("find . -execdir ls ;", "unknown"),
        ("find . -fprint out.txt", "unknown"),
        ("find . -fls out.txt", "unknown"),
        ("awk '{print $1}' notes.txt", "unknown"),
    ],
)
def test_effect_table(command: str, effect: str) -> None:
    assert classify_effect(parse_cli_command(command)) == effect


@pytest.mark.parametrize(
    ("command", "effect", "writes"),
    [
        ("timeout 5 rm notes.txt", "delete", ["/projects/demo/notes.txt"]),
        ("env FOO=1 cp notes.txt /me/copy.txt", "local_write", ["/projects/demo/notes.txt", "/me/copy.txt"]),
        ("nohup kill 1", "host_process", []),
        ("find . -name '*.txt' -delete", "delete", ["/projects/demo"]),
        ("find /projects/demo/notes.txt -exec mv {} /me \\;", "local_write", ["/projects/demo/notes.txt", "/me"]),
        ("find . -fls /me/list.txt", "local_write", ["/me/list.txt"]),
        ("time -o /me/t.txt ls", "local_write", ["/me/t.txt"]),
        ("timeout 5 git status", "read_only", []),
    ],
)
def test_facts_see_through_wrappers_and_find_actions(
    command: str, effect: str, writes: list[str],
) -> None:
    agent, _state = _agent_and_state()
    _project_file()

    facts = command_facts(agent, parse_cli_command(command), "/projects/demo")

    assert facts.effect == effect
    assert [fact.virtual for fact in facts.write_targets] == writes


def test_find_start_path_is_a_root_only_without_test_predicates() -> None:
    agent, _state = _agent_and_state()
    _project_file()

    def _roots(command: str) -> list[bool]:
        facts = command_facts(agent, parse_cli_command(command), "/projects/demo")
        assert facts.effect == "delete"
        return [fact.is_root for fact in facts.write_targets]

    assert _roots("find . -delete") == [True]
    assert _roots("find . -maxdepth 1 -xdev ( -print -o -ls ) -delete") == [True]
    assert _roots("find . -exec rm {} ;") == [True]
    assert _roots("find . -name '*.pyc' -delete") == [False]
    assert _roots("find . -type f -exec rm {} +") == [False]
    # A literal root operand, not a find start path, stays a root.
    assert _roots("find . -name x -exec rm -rf /projects/demo ;") == [True]


# ── invariants ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /projects/demo",
        "rm -rf .",
        "mv /projects/demo /me/demo-old",
        "timeout 5 rm -rf /projects/demo",
        "nohup mv /projects/demo /me/demo-old",
        "find /projects/demo -exec rm -rf {} +",
        "find /projects/demo -delete",
        "find /projects/demo -depth -print -delete",
    ],
)
def test_root_delete_or_move_is_a_card(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    monkeypatch.setattr(_COMPLETE, _boom_if_asked("a whole-root delete is never reviewed"))

    result = execute_bm_cli(agent, state, command, channel_id=channel.id)

    assert result.approval_required is True
    assert notes.exists()
    assert _card_note(result) == NOT_ASKED_ROOT


@pytest.mark.parametrize(
    "command",
    [
        "find . -name '*.pyc' -delete",
        "find /projects/demo -type f -mtime +7 -exec rm {} +",
        "find . ! -path './keep/*' -delete",
    ],
)
def test_filtered_find_delete_from_a_project_root_is_reviewed(
    monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    seen: list = []

    def _ask(messages):
        seen.append(_user_payload(messages))
        return json.dumps({"decision": "ask", "basis": "unsure", "why": "check"})

    monkeypatch.setattr(_COMPLETE, _ask)
    result = execute_bm_cli(agent, state, command, channel_id=channel.id)

    assert seen, "a filtered find deletes matches, not the project root"
    assert result.approval_required is True
    assert _card_note(result) == f"{UNSURE_PREFIX}check"


def test_moving_a_file_into_a_project_root_is_reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    _project_file()
    (agent_artifact_dir(agent.storage_key) / "draft.txt").write_text("d", encoding="utf-8")
    set_cli_cwd(agent.id, "/me")
    seen: list = []

    def _ask(messages):
        seen.append(messages)
        return json.dumps({"decision": "ask", "basis": "unsure", "why": "check"})

    monkeypatch.setattr(_COMPLETE, _ask)
    result = execute_bm_cli(agent, state, "mv draft.txt /projects/demo", channel_id=channel.id)

    assert seen, "the destination being a project root is not a root move"
    assert result.approval_required is True
    assert _card_note(result) == f"{UNSURE_PREFIX}check"


@pytest.mark.parametrize("command", ["docker ps", "kill 1234", "env kill 1234", "nohup docker ps"])
def test_host_process_commands_are_a_card(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    monkeypatch.setattr(_COMPLETE, _boom_if_asked("host-process commands are not reviewed"))

    result = execute_bm_cli(agent, state, command, channel_id=channel.id)

    assert result.approval_required is True
    assert _card_note(result) == NOT_ASKED_HOST_PROCESS


def test_never_allow_fence_and_jail_stay_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    calls: list[object] = []

    def _allow_everything(messages):
        calls.append(messages)
        return '{"decision": "approve", "basis": "harmless", "why": "looks fine"}'

    monkeypatch.setattr(_COMPLETE, _allow_everything)

    blocked = execute_bm_cli(agent, state, "bash -c 'echo hi'", channel_id=channel.id)
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert "Blocked" in blocked.detail
    assert calls == []

    outside = _outside_repo(tmp_path)
    fenced = execute_bm_cli(agent, state, f"git -C {outside} status", channel_id=channel.id)
    assert fenced.ok is False
    assert fenced.approval_required is False
    assert "Path jail" in fenced.detail
    assert calls == []

    set_cli_cwd(agent.id, "/projects/demo")
    jailed = execute_bm_cli(agent, state, "rm /etc/passwd", channel_id=channel.id)
    assert jailed.ok is False
    assert jailed.approval_required is False
    assert "Path jail" in jailed.detail
    assert calls == []
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []


def test_never_allow_and_fence_ignore_remembered_approves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remembered Approve does not open a never-allow, a fence, or a jail."""
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    remembered = db.create_cli_approval_request(
        agent_id=agent.id,
        command="rm notes.txt",
        cwd="/projects/demo",
        channel_id=channel.id,
    )
    assert db.approve_cli_approval_request(remembered.id, decision_by="human") is not None
    monkeypatch.setattr(_COMPLETE, _boom_if_asked("hard blocks must not reach System AI"))

    blocked = execute_bm_cli(agent, state, "bash -c 'echo hi'", channel_id=channel.id)
    assert blocked.ok is False
    assert "Blocked" in blocked.detail

    outside = _outside_repo(tmp_path)
    fenced = execute_bm_cli(agent, state, f"git -C {outside} status", channel_id=channel.id)
    assert fenced.ok is False
    assert "Path jail" in fenced.detail

    set_cli_cwd(agent.id, "/projects/demo")
    jailed = execute_bm_cli(agent, state, "rm /etc/passwd", channel_id=channel.id)
    assert jailed.ok is False
    assert jailed.approval_required is False
    assert "Path jail" in jailed.detail
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []


# ── context ────────────────────────────────────────────────────────────


def _facts(agent, command: str = "rm notes.txt"):
    _project_file()
    return command_facts(agent, parse_cli_command(command), "/projects/demo")


def test_context_carries_the_bound_task_and_detached_turns_have_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _state = _agent_and_state()
    task = db.create_task(
        title="Trim notes",
        description="Remove stale notes",
        project="demo",
        assigned_to=agent.id,
        work_contract={"deliverables": [{"type": "file", "path": "/projects/demo/summary.md"}]},
    )
    monkeypatch.setattr(
        "core.agent_loop.activity_runtime.get_active_task_id", lambda _agent_id: task.id,
    )
    facts = _facts(agent)

    context = build_review_context(agent, facts, channel_id=None)
    assert context.task == {
        "title": "Trim notes",
        "description": "Remove stale notes",
        "status": task.status,
        "project": "demo",
        "deliverables": ["/projects/demo/summary.md"],
    }
    assert context.agent == {"name": agent.name, "specialty": "Eng", "description": None}
    assert "/projects/demo" in context.floor_projects

    with bind_turn(agent.id, {"type": "human_chat", "detached_origin": True}):
        detached = build_review_context(agent, facts, channel_id=None)
    assert detached.task is None


def test_context_reads_the_thread_or_the_dm() -> None:
    agent, _state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="please clean up the notes",
        source_channel="channel",
    )
    db.create_message("__human__", agent.id, "dm: delete the scratch file", message_type="human")
    db.create_message(agent.id, "__human__", "on it", message_type="human")
    facts = _facts(agent)

    thread = build_review_context(agent, facts, channel_id=channel.id).conversation
    assert {"speaker": "operator", "text": "please clean up the notes"} in thread
    assert all("dm:" not in line["text"] for line in thread)

    dm = build_review_context(agent, facts, channel_id=None).conversation
    # Both rows can share one created_at second, so order is not asserted.
    assert len(dm) == 2
    assert {"speaker": "operator", "text": "dm: delete the scratch file"} in dm
    assert {"speaker": agent.name, "text": "on it"} in dm


def test_conversation_is_bounded_by_its_setting() -> None:
    agent, _state = _agent_and_state()
    for index in range(5):
        db.create_message("__human__", agent.id, f"line {index}", message_type="human")
    db.set_setting("cli_auto_approve_context_messages", "2", "cli_policy")
    config.reload()

    dm = build_review_context(agent, _facts(agent), channel_id=None).conversation
    assert len(dm) == 2


def test_precedents_are_floor_wide_and_include_rejections() -> None:
    agent, _state = _agent_and_state()
    teammate, _ = _agent_and_state("Teammate")
    stranger, _ = _agent_and_state("Stranger")
    other_floor = create_floor("Elsewhere")
    assert db.update_agent(stranger.id, floor_id=other_floor.id) is not None

    def _decide(agent_id: str, command: str, *, approve: bool, by: str, note: str | None = None):
        row = db.create_cli_approval_request(agent_id=agent_id, command=command, cwd="/projects/demo")
        decide = db.approve_cli_approval_request if approve else db.reject_cli_approval_request
        assert decide(row.id, decision_by=by, decision_note=note) is not None

    _decide(teammate.id, "ls -la", approve=True, by="human")
    _decide(teammate.id, "rm old.txt", approve=False, by="human")
    _decide(agent.id, "rm older.txt", approve=True, by="human")
    _decide(stranger.id, "rm theirs.txt", approve=True, by="human")
    _decide(agent.id, "rm system.txt", approve=True, by="system", note=f"{AUDIT_PREFIX} x")
    _decide(agent.id, "rm always.txt", approve=True, by="human", note="Always allowed")
    _decide(agent.id, "rm scoped.txt", approve=True, by="human", note="Always allowed in demo")

    precedents = build_review_context(agent, _facts(agent), channel_id=None).precedents

    commands = [item["command"] for item in precedents]
    assert set(commands) == {"ls -la", "rm old.txt", "rm older.txt"}
    assert [item["same_shape"] for item in precedents] == [True, True, False]
    rejected = next(item for item in precedents if item["command"] == "rm old.txt")
    assert rejected["decision"] == "rejected"
    assert rejected["by_agent"] == "Teammate"


def _decide_as(agent_id: str, command: str, *, approve: bool = True) -> str:
    row = db.create_cli_approval_request(agent_id=agent_id, command=command, cwd="/projects/demo")
    decide = db.approve_cli_approval_request if approve else db.reject_cli_approval_request
    assert decide(row.id, decision_by="human") is not None
    return row.id


def _use_precedent_limit(limit: int) -> None:
    db.set_setting("cli_auto_approve_precedent_limit", str(limit), "cli_policy")
    config.reload()


def test_an_older_same_shape_decision_is_not_crowded_out() -> None:
    agent, _state = _agent_and_state()
    _decide_as(agent.id, "rm stale.txt", approve=False)
    for name in ("a", "b", "c"):
        _decide_as(agent.id, f"ls {name}")
    _use_precedent_limit(2)

    precedents = build_review_context(agent, _facts(agent), channel_id=None).precedents

    assert len(precedents) == 2
    assert precedents[0]["command"] == "rm stale.txt"
    assert precedents[0]["decision"] == "rejected"
    assert precedents[0]["same_shape"] is True
    assert precedents[1]["command"] == "ls c"


def test_precedents_order_same_shape_then_same_program_then_rest() -> None:
    agent, _state = _agent_and_state()
    _decide_as(agent.id, "rm old.txt")
    _decide_as(agent.id, "rm -rf build")
    _decide_as(agent.id, "rm new.txt")
    _decide_as(agent.id, "ls -la")
    _decide_as(agent.id, "rmdir empty")

    precedents = build_review_context(agent, _facts(agent), channel_id=None).precedents

    assert [item["command"] for item in precedents] == [
        "rm new.txt", "rm old.txt", "rm -rf build", "rmdir empty", "ls -la",
    ]


def test_a_row_in_both_reads_appears_once() -> None:
    agent, _state = _agent_and_state()
    only = _decide_as(agent.id, "rm old.txt")
    assert only

    precedents = build_review_context(agent, _facts(agent), channel_id=None).precedents

    assert [item["command"] for item in precedents] == ["rm old.txt"]


def test_command_shape_collapses_paths() -> None:
    assert command_shape("rm notes.txt") == command_shape("rm other.txt")
    assert command_shape("rm notes.txt") != command_shape("rm -f notes.txt")
    assert command_shape("rm notes.txt") != command_shape("mv notes.txt other.txt")
    assert command_shape("not a \"command") is None


# ── verdict parse ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        '{"decision": "approve", "basis": "task_work", "why": "in project"}',
        '<think>is it {fine}? yes</think>\n{"decision": "approve", "basis": "task_work", "why": "in project"}',
        '```json\n{"decision": "approve", "basis": "task_work", "why": "in project"}\n```',
        '{"decision": "approve", "basis": "task_work", "why": "in project"}\nHope that helps.',
    ],
)
def test_verdict_envelope_is_tolerated(raw: str) -> None:
    verdict = parse_verdict(raw)
    assert verdict is not None
    assert (verdict.decision, verdict.basis, verdict.why) == ("approve", "task_work", "in project")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not json",
        "sure, go ahead",
        '{"decision": "approve", "basis": "harmless"}',
        '{"decision": "approve", "basis": "harmless", "why": "ok", "extra": 1}',
        '{"decision": true, "basis": "harmless", "why": "ok"}',
        '{"decision": "approve", "basis": "harmless", "why": 3}',
        '{"decision": "approve", "basis": "harmless", "why": "   "}',
        '{"decision": "approve", "basis": "out_of_scope", "why": "contradiction"}',
        '<think>never closed {"decision": "approve", "basis": "harmless", "why": "ok"}',
    ],
)
def test_bad_verdicts_are_rejected(raw: str | None) -> None:
    assert parse_verdict(raw) is None


def test_bad_verdict_stays_on_the_approval_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    monkeypatch.setattr(
        _COMPLETE,
        lambda _m: '{"decision": "approve", "basis": "harmless", "why": "ok", "extra": 1}',
    )

    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.approval_required is True
    assert notes.read_text(encoding="utf-8") == "keep"
    assert _card_note(result).startswith(REVIEW_ERROR_PREFIX)
    assert "not a review" in _card_note(result)
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []


# ── resolver ───────────────────────────────────────────────────────────


def _set_global(value: str) -> None:
    db.set_setting("cli_auto_approve_global", value, "advanced")
    config.reload()


def test_resolver_uses_an_active_thread_flag() -> None:
    agent, _state = _agent_and_state()
    on = _thread(agent.id, enabled=True)
    off = _thread(agent.id, enabled=False)

    assert auto_approve_effective(agent, on.id) is True
    assert auto_approve_effective(agent, off.id) is False
    assert auto_approve_effective(agent, "missing-channel") is False
    db.archive_channel(on.id)
    assert auto_approve_effective(agent, on.id) is False


def test_resolver_uses_the_dm_flag_when_there_is_no_thread() -> None:
    """``channel_id=None`` (a DM, or work with no origin thread) reads the agent's flag."""
    agent, _state = _agent_and_state()
    other, _other_state = _agent_and_state("Other Clerk")
    off_thread = _thread(agent.id, enabled=False)
    assert agent.cli_auto_approve_dm is False
    assert auto_approve_effective(agent, None) is False

    updated = db.set_agent_cli_auto_approve_dm(agent.id, True)
    assert updated is not None and updated.cli_auto_approve_dm is True
    # Read live, so the Agent object a turn started with does not go stale.
    assert auto_approve_effective(agent, None) is True
    # The DM flag is the DM's only: a thread still follows its own flag,
    # and another agent's DM keeps its own.
    assert auto_approve_effective(agent, off_thread.id) is False
    assert auto_approve_effective(other, None) is False

    db.set_agent_cli_auto_approve_dm(agent.id, False)
    assert auto_approve_effective(agent, None) is False


def test_resolver_global_overrides_both_flags_and_keeps_them() -> None:
    agent, _state = _agent_and_state()
    off_thread = _thread(agent.id, enabled=False)
    # Seeded off.
    assert config.get_live("cli_auto_approve_global") == "false"

    _set_global("true")
    assert auto_approve_effective(agent, off_thread.id) is True
    assert auto_approve_effective(agent, None) is True
    assert auto_approve_effective(agent, "missing-channel") is True
    # Neither conversation's own flag was written.
    assert db.get_channel(off_thread.id).cli_auto_approve is False
    assert db.get_agent(agent.id).cli_auto_approve_dm is False

    _set_global("false")
    assert auto_approve_effective(agent, off_thread.id) is False
    assert auto_approve_effective(agent, None) is False


def test_resolver_refuses_to_guess_a_bad_global_value() -> None:
    agent, _state = _agent_and_state()
    _set_global("yes")
    with pytest.raises(ConfigError, match="must be true or false"):
        auto_approve_effective(agent, None)


# ── end to end: audit, route, UI strings ───────────────────────────────


def test_toggle_off_keeps_the_approval_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default off. System AI is not asked, and the file stays."""
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=False)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    monkeypatch.setattr(_COMPLETE, _boom_if_asked("System AI must not run while the flag is off"))

    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.approval_required is True
    assert result.ok is False
    assert notes.read_text(encoding="utf-8") == "keep"
    assert not _card_note(result)
    pending = db.list_cli_approval_requests(status="pending", agent_id=agent.id)
    assert len(pending) == 1
    assert pending[0].decision_by is None
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []


def test_toggle_on_auto_approves_with_the_basis_in_the_audit(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    assert channel.cli_auto_approve is True
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    why = "deletes one project file"
    seen: list = []

    def _approve(messages):
        seen.append(messages)
        return json.dumps({"decision": "approve", "basis": "task_work", "why": why})

    monkeypatch.setattr(_COMPLETE, _approve)

    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.ok is True, result.detail
    assert result.approval_required is False
    assert not notes.exists()
    payload = _user_payload(seen[0])
    assert payload["effect"] == "delete"
    assert payload["write_targets"] == ["/projects/demo/notes.txt (floor project demo)"]
    line = f"{AUDIT_PREFIX} [task_work] {why}"
    assert result.data is not None
    assert result.data.get("audit") == line
    assert result.data.get("approved_by") == "system"
    assert line in (result.prompt_content or "")

    approved = db.list_cli_approval_requests(status="approved", decision_by="system")
    assert len(approved) == 1
    assert approved[0].decision_note == line

    mapped = map_cli_result(agent, result, command="rm notes.txt")
    assert mapped["audit"] == line
    assert mapped["approved_by"] == "system"

    feed = db.get_recent_activity_log_entries(limit=10)
    assert any(
        row.get("event") == "cli_auto_approved" and line in str(row.get("detail") or "")
        for row in feed
    )
    events = db.list_bm_cli_events(agent_id=agent.id, limit=5)
    assert any(line in str(row.get("stdout_preview") or "") for row in events)

    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    audits = [item for item in res.json() if item["kind"] == "audit"]
    assert len(audits) == 1
    assert line in audits[0]["sub"]
    assert "rm notes.txt" in audits[0]["sub"]


def test_cross_project_write_reaches_the_reviewer_with_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    _project_file("demo")
    other = project_dir(LOBBY_ID, "other")
    other.mkdir(parents=True, exist_ok=True)
    secret = other / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    set_cli_cwd(agent.id, "/projects/demo")
    seen: list = []

    def _ask(messages):
        seen.append(messages)
        return json.dumps({"decision": "ask", "basis": "out_of_scope", "why": "not this task"})

    monkeypatch.setattr(_COMPLETE, _ask)
    result = execute_bm_cli(agent, state, "rm /projects/other/secret.txt", channel_id=channel.id)

    assert _user_payload(seen[0])["write_targets"] == [
        "/projects/other/secret.txt (floor project other)",
    ]
    assert result.approval_required is True
    assert secret.read_text(encoding="utf-8") == "nope"
    assert _card_note(result) == f"{UNSURE_PREFIX}not this task"


def test_me_delete_can_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    scratch = agent_artifact_dir(agent.storage_key) / "scratch.txt"
    scratch.write_text("temp", encoding="utf-8")
    set_cli_cwd(agent.id, "/me")
    monkeypatch.setattr(_COMPLETE, _verdict("approve", "task_work", "deletes one file in /me"))

    result = execute_bm_cli(agent, state, "rm scratch.txt", channel_id=channel.id)

    assert result.ok is True, result.detail
    assert not scratch.exists()
    assert result.data is not None
    assert str(result.data.get("audit") or "").startswith(AUDIT_PREFIX)


def test_unsure_review_shows_why_on_the_card(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    """Toggle on, and an ask still paints the reason beside Approve."""
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    reason = "deletes more than the notes file"
    monkeypatch.setattr(_COMPLETE, _verdict("ask", "unsure", reason))

    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.approval_required is True
    assert notes.read_text(encoding="utf-8") == "keep"
    expected = f"{UNSURE_PREFIX}{reason}"
    assert _card_note(result) == expected
    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    approvals = [item for item in res.json() if item["kind"] == "approval"]
    assert len(approvals) == 1
    assert approvals[0]["review_note"] == expected
    assert approvals[0]["sub"] == "rm notes.txt"


def test_review_call_failure_shows_why_on_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")

    def _boom(_messages):
        raise RuntimeError("timeout")

    monkeypatch.setattr(_COMPLETE, _boom)
    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.approval_required is True
    assert notes.read_text(encoding="utf-8") == "keep"
    assert _card_note(result).startswith(REVIEW_ERROR_PREFIX)
    assert "review call failed" in _card_note(result)


def test_deny_pick_and_soft_block_stay(client: TestClient) -> None:
    """The thread flag does not rewrite Default Policy, Deny rows, or Soft-block."""
    _enable_shell()
    db.set_setting("cli_default_policy", "deny", "cli_policy")
    config.reload()
    policy_engine.reload()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=False)
    task = db.create_task(title="Stuck", assigned_to=agent.id, requester_id=agent.id)
    db.update_task(task.id, status="blocked")
    never = {rule.pattern for rule in db.list_cli_policy_rules(tier="never_allowed")}
    assert "bash" in never

    res = client.patch(
        f"/api/channels/{channel.id}/cli-auto-approve", headers=_auth(), json={"enabled": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["cli_auto_approve"] is True
    assert config.get_live("cli_default_policy") == "deny"
    assert db.get_task(task.id).status == "blocked"
    still_never = {rule.pattern for rule in db.list_cli_policy_rules(tier="never_allowed")}
    assert still_never == never

    fresh = db.get_agent_state(agent.id)
    assert fresh is not None
    denied = execute_bm_cli(agent, fresh, "zz-unmatched-cmd --flag", channel_id=channel.id)
    assert denied.approval_required is False
    assert denied.ok is False
    assert "denied by default policy" in denied.detail
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []

    off = client.patch(
        f"/api/channels/{channel.id}/cli-auto-approve", headers=_auth(), json={"enabled": False},
    )
    assert off.status_code == 200, off.text
    assert off.json()["cli_auto_approve"] is False
    assert config.get_live("cli_default_policy") == "deny"
    assert db.get_task(task.id).status == "blocked"


def test_dm_flag_auto_approves_dm_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no origin thread, the agent's DM flag turns the gate on end to end."""
    _enable_shell()
    agent, state = _agent_and_state()
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    monkeypatch.setattr(_COMPLETE, _boom_if_asked("System AI must not run while the DM flag is off"))

    carded = execute_bm_cli(agent, state, "rm notes.txt")
    assert carded.approval_required is True
    assert notes.read_text(encoding="utf-8") == "keep"
    # A still-pending identical card is reused without asking the gate, so
    # the operator decides this one before the flag-on ask.
    assert carded.approval_request_id is not None
    assert db.reject_cli_approval_request(carded.approval_request_id) is not None

    db.set_agent_cli_auto_approve_dm(agent.id, True)
    monkeypatch.setattr(_COMPLETE, _verdict("approve", "task_work", "deletes one project file"))
    fresh = db.get_agent_state(agent.id)
    assert fresh is not None
    result = execute_bm_cli(agent, fresh, "rm notes.txt")

    assert result.ok is True, result.detail
    assert not notes.exists()
    assert result.data is not None
    assert result.data.get("audit") == f"{AUDIT_PREFIX} [task_work] deletes one project file"


def test_global_still_runs_the_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Global on means the gate runs everywhere, not that every command is approved."""
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=False)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    _set_global("true")
    monkeypatch.setattr(_COMPLETE, _verdict("ask", "out_of_scope", "nothing asked for this delete"))

    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.approval_required is True
    assert notes.read_text(encoding="utf-8") == "keep"
    assert _card_note(result).startswith(UNSURE_PREFIX)


def test_dm_route_sets_only_the_dm_flag(client: TestClient) -> None:
    agent, _state = _agent_and_state()
    channel = _thread(agent.id, enabled=False)

    on = client.patch(
        f"/api/agents/{agent.id}/cli-auto-approve", headers=_auth(), json={"enabled": True},
    )
    assert on.status_code == 200, on.text
    assert on.json()["id"] == agent.id
    assert on.json()["cli_auto_approve_dm"] is True
    assert on.json()["cli_auto_approve_global"] is False
    assert "api_key" not in on.json()
    assert db.get_agent(agent.id).cli_auto_approve_dm is True
    assert db.get_channel(channel.id).cli_auto_approve is False

    got = client.get(f"/api/agents/{agent.id}", headers=_auth())
    assert got.status_code == 200, got.text
    assert got.json()["cli_auto_approve_dm"] is True
    assert got.json()["cli_auto_approve_global"] is False

    off = client.patch(
        f"/api/agents/{agent.id}/cli-auto-approve", headers=_auth(), json={"enabled": False},
    )
    assert off.status_code == 200, off.text
    assert off.json()["cli_auto_approve_dm"] is False

    missing = client.patch(
        "/api/agents/no-such-agent/cli-auto-approve", headers=_auth(), json={"enabled": True},
    )
    assert missing.status_code == 404


def test_payloads_report_global_auto_approve(client: TestClient) -> None:
    """Thread and agent payloads carry the global flag, so the UI greys out without a second fetch."""
    agent, _state = _agent_and_state()
    channel = _thread(agent.id, enabled=False)

    saved = client.put(
        "/api/settings/cli_auto_approve_global?value=true&category=advanced", headers=_auth(),
    )
    assert saved.status_code == 200, saved.text

    thread = client.get(f"/api/channels/{channel.id}", headers=_auth())
    assert thread.status_code == 200, thread.text
    assert thread.json()["channel"]["cli_auto_approve_global"] is True
    assert thread.json()["channel"]["cli_auto_approve"] is False
    person = client.get(f"/api/agents/{agent.id}", headers=_auth())
    assert person.json()["cli_auto_approve_global"] is True
    assert person.json()["cli_auto_approve_dm"] is False

    refused = client.put(
        "/api/settings/cli_auto_approve_global?value=yes&category=advanced", headers=_auth(),
    )
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Global auto-approve must be true or false."
    assert config.get_live("cli_auto_approve_global") == "true"


def test_global_setting_refetches_open_conversations() -> None:
    assert _operator_surfaces_for_setting("cli_auto_approve_global", "advanced") == [
        "advanced-system",
        "chat",
    ]
    assert _operator_surfaces_for_setting("diagnostics_enabled", "advanced") == ["advanced-system"]


def test_thread_toggle_is_off_in_the_menu_until_the_flag_is_set() -> None:
    thread = (ROOT / "ui/static/js/conversation/sources/thread-source.js").read_text(encoding="utf-8")
    assert "id: 'channel-cli-auto-approve'" in thread
    assert "enabled: !!(channel && channel.cli_auto_approve)" in thread
    assert "globalEnabled: !!(channel && channel.cli_auto_approve_global)" in thread
    switch = (ROOT / "ui/static/js/conversation/auto-approve-switch.js").read_text(encoding="utf-8")
    assert "const LABEL = 'Auto-approve safe commands';" in switch
    # The request itself moved to thread-requests.js with the other
    # thread-setting requests; the menu switch above stays in the source.
    requests = (ROOT / "ui/static/js/conversation/sources/thread-requests.js").read_text(encoding="utf-8")
    assert "/cli-auto-approve" in requests
    popover = (ROOT / "ui/static/js/needs/needs-popover.js").read_text(encoding="utf-8")
    assert "audit: 'Auto-approved'" in popover
    assert "popover-need-review" in popover
    shape = (ROOT / "ui/static/js/needs/need-shape.js").read_text(encoding="utf-8")
    assert "'cli_auto_approved'" in shape
    assert "reviewNote: raw.review_note" in shape
    assert "review_note: need.reviewNote" in shape
    card_js = (ROOT / "ui/static/js/core/consent-card.js").read_text(encoding="utf-8")
    assert "hpc-review" in card_js
