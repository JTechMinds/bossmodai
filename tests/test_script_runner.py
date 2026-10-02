"""Agent CLI scripts: decide every segment first, then run once or pause once.

Executor tests run real processes (printf, cat, wc, false, true, sleep)
inside temp jail roots. Runtime tests go through ``execute_bm_cli`` /
``execute_approved_command`` on a fresh temp database.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.approval_gate.facts import GateSegment, script_facts
from core.bm_cli.approval_gate.gate import NOT_ASKED_ROOT
from core.bm_cli.cli_always import always_allow_pattern
from core.bm_cli.floor_roots import project_dir
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import VIRTUAL_COMMANDS, execute_approved_command, execute_bm_cli, preview_bm_cli
from core.bm_cli.script_prepare import (
    CD_ALONE_STEER,
    CD_STEER,
    ScriptRefused,
    bossmod_glob_steer,
    bossmod_only_steer,
    prepare_script,
)
from core.bm_cli.session import get_cli_cwd, set_cli_cwd
from core.bm_cli.shell_executor import execute_shell_script
from core.bm_cli.shell_lexer import STEER_VARIABLE
from core.bm_cli.shell_script import Word, parse_shell_script
from db.crud import query
from db.floors import LOBBY_ID

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


# ── executor: real processes in a temp jail ────────────────────────────


def _run(tmp_path: Path, text: str, *, timeout: int = 10, cap: int = 65536, envs=None):
    script = parse_shell_script(text)
    return execute_shell_script(
        script,
        cwd=tmp_path,
        timeout_seconds=timeout,
        max_output_bytes=cap,
        allowed_roots=[tmp_path],
        extra_env=envs if envs is not None else [{}] * len(script.commands()),
    )


@pytest.mark.parametrize(
    ("text", "exit_code", "stdout"),
    [
        ("false && printf no", 1, ""),
        ("true && printf yes", 0, "yes"),
        ("false || printf rescued", 0, "rescued"),
        ("true || printf no", 0, ""),
        ("false; printf after", 0, "after"),
        ("printf a; false", 1, "a"),
        ("false && printf x || printf y", 0, "y"),
        ("true || printf x && printf z", 0, "z"),
        ("printf a | false", 1, ""),
        ("false | printf b", 0, "b"),
        ("printf 'a\\nb\\n' | wc -l", 0, "2"),
    ],
)
def test_connectors_follow_bash_exit_code_semantics(
    tmp_path: Path, text: str, exit_code: int, stdout: str,
) -> None:
    result = _run(tmp_path, text)

    assert result.exit_code == exit_code
    assert result.stdout.strip() == stdout
    assert result.timed_out is False


def test_redirects_write_append_read_and_join_streams_in_order(tmp_path: Path) -> None:
    result = _run(tmp_path, "printf hi > in.txt && printf ' there' >> in.txt && cat < in.txt | wc -c")
    assert result.stdout.strip() == "8"
    assert (tmp_path / "in.txt").read_text() == "hi there"

    joined = _run(tmp_path, "cat missing.txt > both.txt 2>&1")
    assert joined.exit_code != 0
    assert "missing.txt" in (tmp_path / "both.txt").read_text()
    assert joined.stderr == ""

    # 2>&1 before > sends stderr where stdout went then: the capture.
    early = _run(tmp_path, "cat missing.txt 2>&1 > out.txt")
    assert "missing.txt" in early.stdout
    assert (tmp_path / "out.txt").read_text() == ""

    both = _run(tmp_path, "cat in.txt missing.txt &> all.txt")
    text = (tmp_path / "all.txt").read_text()
    assert "hi there" in text and "missing.txt" in text


def test_a_missing_program_is_127_and_the_next_pipeline_still_runs(tmp_path: Path) -> None:
    result = _run(tmp_path, "no-such-program-xyz; printf after")
    assert result.stdout == "after"
    assert "Command not found: no-such-program-xyz" in result.stderr

    alone = _run(tmp_path, "no-such-program-xyz")
    assert alone.exit_code == 127


def test_timeout_kills_the_whole_chain(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _run(tmp_path, "sleep 30 | sleep 30; printf after", timeout=1)
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert result.exit_code == 124
    assert "after" not in result.stdout
    assert elapsed < 5, "the deadline covers the whole script and kills live processes"


def test_the_deadline_is_shared_across_pipelines(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _run(tmp_path, "sleep 0.7; sleep 0.7; printf done", timeout=1)

    assert result.timed_out is True
    assert "done" not in result.stdout
    assert time.monotonic() - started < 3


def test_output_cap_applies_to_captured_output(tmp_path: Path) -> None:
    result = _run(tmp_path, "printf 0123456789abcdef | cat", cap=8)
    assert result.stdout == "01234567\n[truncated — 16 bytes total]"


def test_injected_secrets_are_redacted_per_command(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "printf token-one; printf ' token-two'",
        envs=[{"GH_TOKEN": "token-one"}, {"GH_TOKEN": "token-two"}],
    )
    assert result.stdout == "*** ***"


def test_env_is_per_command_and_the_git_ceiling_cannot_be_overridden(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "printenv ONLY_FIRST; printenv ONLY_FIRST; GIT_CEILING_DIRECTORIES=/ FOO=bar printenv FOO GIT_CEILING_DIRECTORIES",
        envs=[{"ONLY_FIRST": "1"}, {}, {}],
    )
    lines = result.stdout.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "bar"
    assert lines[2] == str(tmp_path.resolve().parent)


def test_the_executor_jails_argv_and_redirects_before_anything_runs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for text in ("printf x > made.txt; cat /etc/hostname", "printf x > made.txt; printf y > ../escape.txt"):
        script = parse_shell_script(text)
        result = execute_shell_script(
            script, cwd=root, timeout_seconds=5, max_output_bytes=1000,
            allowed_roots=[root], extra_env=[{}] * len(script.commands()),
        )
        assert result.denied_by_path_jail is True
        assert not (root / "made.txt").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_the_executor_refuses_unexpanded_globs(tmp_path: Path) -> None:
    script = parse_shell_script("ls *.txt")
    with pytest.raises(ValueError, match="expand globs"):
        execute_shell_script(
            script, cwd=tmp_path, timeout_seconds=5, max_output_bytes=100,
            allowed_roots=[tmp_path], extra_env=[{}],
        )


# ── runtime: decide every segment, then run or pause once ──────────────


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _set_global_auto_approve(value: str) -> None:
    db.set_setting("cli_auto_approve_global", value, "advanced")
    config.reload()


def _agent_in_project():
    agent = db.create_agent("Script Runner", role="Eng")
    state = db.get_agent_state(agent.id)
    assert state is not None
    root = project_dir(LOBBY_ID, "demo")
    # The company root is this run's temp dir (conftest); each test starts clean.
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "notes.txt").write_text("keep\n", encoding="utf-8")
    set_cli_cwd(agent.id, "/projects/demo")
    return agent, state, root


def _audit() -> list[tuple[str, str, str]]:
    return [
        (row["command"], row["policy_tier"], row["decision"])
        for row in query("SELECT * FROM bm_cli_events ORDER BY rowid")
    ]


def _error(result) -> str:
    return str((result.data or {}).get("error") or result.detail)


def test_a_block_in_segment_two_means_segment_one_never_runs() -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    script = "printf one > first.txt; python3 -c 1"

    result = execute_bm_cli(agent, state, script)

    assert result.ok is False
    assert result.command == script
    assert not (root / "first.txt").exists()
    assert _audit() == [(script, "never_allowed", "denied")]


def test_one_card_per_script_lists_each_segment_tier() -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    script = "printf a | sed s/a/b/ > out.txt"

    result = execute_bm_cli(agent, state, script)

    assert result.approval_required is True
    pending = db.list_cli_approval_requests(status="pending", agent_id=agent.id)
    assert [request.command for request in pending] == [script]
    note = pending[0].review_note or ""
    assert note.startswith("Script segments: 1) printf a [always_allowed]; 2) sed s/a/b/ [")
    assert not (root / "out.txt").exists()
    assert _audit() == [(script, "default", "approval_required")]
    # One prefix rule cannot cover a script: no Always is offered.
    assert pending[0].as_card()["always_allow"] is False
    assert always_allow_pattern(script) is None

    approved = execute_approved_command(
        agent, state, script, approval_request_id=pending[0].id, cwd="/projects/demo",
    )
    assert approved.ok is True
    assert (root / "out.txt").read_text() == "b"
    assert _audit()[-1] == (script, "approved", "allowed")


def test_an_approved_script_rechecks_never_allowed_per_segment() -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    script = "printf a > x.txt; python3 -c 1"
    request = db.create_cli_approval_request(agent_id=agent.id, command=script, cwd="/projects/demo")

    result = execute_approved_command(agent, state, script, approval_request_id=request.id, cwd="/projects/demo")

    assert result.ok is False
    assert "python" in _error(result).lower() or "blocked" in _error(result).lower()
    assert not (root / "x.txt").exists()


def test_an_approved_script_rechecks_the_jail() -> None:
    _enable_shell()
    agent, state, _root = _agent_in_project()
    script = "printf a | cat > /etc/bossmod-script-test"
    request = db.create_cli_approval_request(agent_id=agent.id, command=script, cwd="/projects/demo")

    result = execute_approved_command(agent, state, script, approval_request_id=request.id, cwd="/projects/demo")

    assert result.ok is False
    assert result.kind == "host_deny"
    assert "Path jail" in _error(result)
    assert not Path("/etc/bossmod-script-test").exists()


@pytest.mark.parametrize("target", ["/etc/bossmod-script-test", "../../../../../../escape-script-test.txt"])
def test_a_redirect_outside_the_jail_is_a_block(target: str) -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    script = f"printf first > first.txt && printf x > {target}"

    result = execute_bm_cli(agent, state, script)

    assert result.ok is False
    assert result.kind == "host_deny"
    assert "Path jail" in _error(result)
    assert not (root / "first.txt").exists()
    assert _audit() == [(script, "never_allowed", "denied")]


def test_glob_expansion_is_sorted_keeps_unmatched_words_and_stays_jailed(tmp_path: Path) -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    (root / "a.txt").write_text("a\n", encoding="utf-8")

    listed = execute_bm_cli(agent, state, "ls *.txt")
    assert listed.ok is True
    assert "a.txt\nb.txt\nnotes.txt" in listed.prompt_content

    virtual = execute_bm_cli(agent, state, "cat /projects/demo/[ab].txt | wc -l")
    assert virtual.ok is True
    assert "2" in virtual.prompt_content

    unmatched = execute_bm_cli(agent, state, "ls *.nomatch")
    assert unmatched.ok is False
    assert "*.nomatch" in unmatched.prompt_content

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "link").symlink_to(outside)
    escaped = execute_bm_cli(agent, state, "cat link/*.txt")
    assert escaped.ok is False
    assert "Path jail" in _error(escaped)
    assert "secret" not in escaped.prompt_content.replace("secret.txt", "")


@pytest.mark.parametrize("connector", ["&&", ";"])
def test_a_leading_cd_persists_and_the_rest_runs_there(connector: str) -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    set_cli_cwd(agent.id, "/me")

    result = execute_bm_cli(agent, state, f"cd /projects/demo {connector} printf hi > made.txt")

    assert result.ok is True
    assert (root / "made.txt").read_text() == "hi"
    assert get_cli_cwd(agent.id) == "/projects/demo"
    assert result.cwd == "/projects/demo"


def test_a_leading_cd_does_not_persist_when_the_script_is_blocked() -> None:
    _enable_shell()
    agent, state, _root = _agent_in_project()
    set_cli_cwd(agent.id, "/me")

    result = execute_bm_cli(agent, state, "cd /projects/demo && python3 -c 1")

    assert result.ok is False
    assert get_cli_cwd(agent.id) == "/me"


@pytest.mark.parametrize("connector", ["&&", ";"])
def test_a_leading_cd_to_a_missing_directory_refuses_the_whole_line(connector: str) -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    set_cli_cwd(agent.id, "/me")

    result = execute_bm_cli(agent, state, f"cd /projects/missing {connector} printf hi > /projects/demo/made.txt")

    assert result.ok is False
    assert _error(result) == "Directory not found: /projects/missing"
    assert not (root / "made.txt").exists(), "the rest of the line must never run"
    assert get_cli_cwd(agent.id) == "/me"


@pytest.mark.parametrize("script", ["cd /me ;", "cd /me\n", "cd /me ;\n"])
def test_nothing_after_a_leading_cd_never_reaches_segment_preparation(script: str) -> None:
    agent, _state, _root = _agent_in_project()

    with pytest.raises(ScriptRefused) as refused:
        prepare_script(agent, script, "/projects/demo", virtual_commands=VIRTUAL_COMMANDS)

    assert refused.value.message == CD_ALONE_STEER
    assert refused.value.jail is False


@pytest.mark.parametrize(
    ("script", "steer"),
    [
        ("printf a && write notes.md", bossmod_only_steer("write")),
        ("task 12 | wc -l", bossmod_only_steer("task")),
        ("ls; cd /me", CD_STEER),
        ("cd /me || ls", CD_STEER),
        ("cd /me | ls", CD_STEER),
        ("cd /me ;", CD_ALONE_STEER),
        ("ol *.md", bossmod_glob_steer("ol")),
        ("echo $HOME", STEER_VARIABLE),
    ],
)
def test_steers_refuse_the_whole_script(script: str, steer: str) -> None:
    _enable_shell()
    agent, state, _root = _agent_in_project()

    result = execute_bm_cli(agent, state, script)

    assert result.ok is False
    assert _error(result) == steer
    assert _audit() == [(script, "parse", "denied")]


def test_a_program_selecting_assignment_is_never_allowed() -> None:
    _enable_shell()
    agent, state, _root = _agent_in_project()

    result = execute_bm_cli(agent, state, "PATH=. printf x | wc -c")

    assert result.ok is False
    assert "setting PATH changes which program runs" in _error(result)
    assert _audit()[0][1] == "never_allowed"


@pytest.mark.parametrize(
    "name",
    ["GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_GLOBAL", "GIT_EDITOR",
     "GIT_SEQUENCE_EDITOR", "EDITOR", "VISUAL", "GIT_PAGER", "PAGER"],
)
def test_a_git_config_editor_or_pager_prefix_is_never_allowed(name: str) -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    script = f"printf a > ran.txt; {name}=x git log | wc -l"

    result = execute_bm_cli(agent, state, script)

    assert result.ok is False
    assert f"setting {name} changes which program runs" in _error(result)
    assert not (root / "ran.txt").exists()
    assert _audit() == [(script, "never_allowed", "denied")]


def _snapshot() -> dict[str, list]:
    tables = [row["name"] for row in query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )]
    return {name: query(f'SELECT * FROM "{name}"') for name in tables}


@pytest.mark.parametrize(
    ("script", "outcome"),
    [
        ("printf a | wc -c > count.txt", "run"),
        ("printf a | sed s/a/b/ > count.txt", "approval"),
        ("printf a > count.txt; python3 -c 1", "block"),
        ("printf a > count.txt && write notes.md", "steer"),
        ("cd /projects/demo && printf a > count.txt", "run"),
        ("cd /projects/demo ; printf a > count.txt", "run"),
    ],
)
def test_a_dry_run_agrees_with_a_real_run_and_writes_nothing(script: str, outcome: str) -> None:
    _enable_shell()
    agent, state, root = _agent_in_project()
    start = "/me" if script.startswith("cd ") else "/projects/demo"
    set_cli_cwd(agent.id, start)
    before = _snapshot()

    preview = preview_bm_cli(agent, state, script)

    assert _snapshot() == before, "a dry run must not write to the database"
    assert get_cli_cwd(agent.id) == start, "a dry run must not persist cd"
    assert not (root / "count.txt").exists(), "a dry run must not execute"
    assert (preview.data or {}).get("dry_run") is True or outcome == "steer"
    real = execute_bm_cli(agent, state, script)
    assert (preview.ok, preview.approval_required, preview.consent_required) == (
        real.ok, real.approval_required, real.consent_required,
    )
    assert preview.command == real.command == script
    if outcome == "steer":
        assert _error(preview) == _error(real) == bossmod_only_steer("write")
    elif outcome == "block":
        assert "python3" in preview.prompt_content and "[never_allowed]" in preview.prompt_content
    else:
        assert "Script segments: 1) printf a [always_allowed]" in preview.prompt_content


def test_virtual_names_in_a_script_run_natively_under_shell_policy() -> None:
    agent, state, _root = _agent_in_project()

    off = execute_bm_cli(agent, state, "ls | wc -l")
    assert off.ok is False, "a script needs the shell even when every name is virtual"

    _enable_shell()
    on = execute_bm_cli(agent, state, "ls | wc -l")
    assert on.ok is True
    assert on.kind == "shell"
    assert on.prompt_content.endswith("STDOUT:\n1"), "only notes.txt is in the project"


def test_auto_approve_reviews_the_union_and_runs_the_whole_script(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    _set_global_auto_approve("true")
    agent, state, root = _agent_in_project()
    seen: list[dict] = []

    def _approve(messages):
        user = next(item for item in messages if item.get("role") == "user")
        seen.append(json.loads(user["content"]))
        return json.dumps({"decision": "approve", "basis": "task_work", "why": "edits stay in the project"})

    monkeypatch.setattr(_COMPLETE, _approve)
    script = "printf a | sed s/a/b/ > out.txt"

    result = execute_bm_cli(agent, state, script)

    assert result.ok is True
    assert (root / "out.txt").read_text() == "b"
    assert "approved-by=system [task_work]" in result.prompt_content
    (payload,) = seen
    assert payload["command"] == script
    assert [segment["command"] for segment in payload["segments"]] == ["printf a", "sed s/a/b/"]
    assert payload["segments"][1]["write_targets"] == ["/projects/demo/out.txt (floor project demo)"]
    approved = db.list_cli_approval_requests(status="approved", decision_by="system")
    assert [request.command for request in approved] == [script]


def test_gate_invariants_apply_to_every_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    _set_global_auto_approve("true")
    agent, state, root = _agent_in_project()

    def _boom(_messages):
        raise AssertionError("a whole-root delete is never reviewed")

    monkeypatch.setattr(_COMPLETE, _boom)

    result = execute_bm_cli(agent, state, "printf a && rm -rf /projects/demo")

    assert result.approval_required is True
    assert (root / "notes.txt").exists()
    note = str(((result.data or {}).get("cli_approval") or {}).get("review_note") or "")
    assert NOT_ASKED_ROOT in note
    assert "Script segments: 1) printf a" in note


def test_script_facts_count_redirect_targets_as_writes() -> None:
    agent, _state, root = _agent_in_project()
    facts = script_facts(
        agent,
        [
            GateSegment(parse_cli_command("cat notes.txt"), "/projects/demo", (str(root / "b.txt"),)),
            GateSegment(parse_cli_command("wc -l"), "/projects/demo"),
        ],
        command="cat notes.txt > b.txt; wc -l",
    )

    assert facts.command == "cat notes.txt > b.txt; wc -l"
    assert facts.effect == "local_write"
    assert [fact.virtual for fact in facts.write_targets] == ["/projects/demo/b.txt"]
    assert [segment.effect for segment in facts.segments] == ["local_write", "read_only"]


def test_a_script_writes_one_audit_row_with_its_full_text() -> None:
    _enable_shell()
    agent, state, _root = _agent_in_project()
    script = "printf 'a\\nb\\n' | wc -l && printf done"

    result = execute_bm_cli(agent, state, script)

    assert result.ok is True
    assert result.command == script
    assert _audit() == [(script, "always_allowed", "allowed")]


def test_words_keep_their_virtual_form_for_policy() -> None:
    # Expansion keeps /projects in the word, so the segment is decided as typed.
    agent, _state, root = _agent_in_project()
    (root / "a.txt").write_text("a", encoding="utf-8")
    from core.bm_cli.runtime import VIRTUAL_COMMANDS
    from core.bm_cli.script_prepare import prepare_script

    prepared = prepare_script(agent, "cat /projects/demo/a.* | wc -c", "/projects/demo", virtual_commands=VIRTUAL_COMMANDS)

    assert prepared.segments[0].command.argv == (
        Word("cat", False, "cat"), Word("/projects/demo/a.txt", False, "/projects/demo/a.txt"),
    )
    assert prepared.segments[0].parsed.raw == "cat /projects/demo/a.txt"
