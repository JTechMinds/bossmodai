"""Background processes (`cmd &`): launch, liveness, stop, sweep, check-ins, the `processes` command.

Real processes only, on a fresh temp database. Every process a test starts
is either stopped through its row (reaping its tag) or killed by the
teardown, so nothing outlives the test and nothing else is signalled.
"""

from __future__ import annotations

import os
import shutil
import signal
import sys
import textwrap
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import pytest

import db
from core import config
from core.agent_repository import agent_repository
from core.bm_cli import background_processes
from core.bm_cli.background_processes import BackgroundOwner
from core.bm_cli.floor_roots import project_dir
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.session import set_cli_cwd
from core.bm_cli.shell_executor import execute_shell_script
from core.bm_cli.shell_script import parse_shell_script
from core.floor_moves import move_project
from core.floors import send_home
from db.floors import LOBBY_ID, create_floor

# Starts a detached `sleep 60` in its own session, prints its pid, then
# sleeps. Single quotes only: the script passes it inside double quotes.
_LAUNCHER = textwrap.dedent(
    """\
    import subprocess, time
    child = subprocess.Popen(['sleep', '60'], start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(child.pid, flush=True)
    time.sleep(60)
    """
)


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


@pytest.fixture(autouse=True)
def _stop_everything() -> Iterator[list[int]]:
    """Pids a test saw; every running row is stopped and every pid killed at teardown."""
    pids: list[int] = []
    yield pids
    for row in db.list_running_agent_processes():
        background_processes.stop(row.id, by="system", reason="test teardown")
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            continue
    db.close_connection()


def _gone(pid: int, *, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def _agent(name: str = "Runner"):
    return db.create_agent(name, role="Eng")


def _run(tmp_path: Path, agent, text: str, *, timeout: int = 10, cwd: Path | None = None):
    script = parse_shell_script(text)
    workdir = cwd or tmp_path
    owner = BackgroundOwner(
        agent_id=agent.id,
        storage_key=agent.storage_key,
        cwd_virtual="/me",
        item_commands=tuple(f"item {index}" for index in range(len(script.items))),
    )
    return execute_shell_script(
        script, cwd=workdir, timeout_seconds=timeout, max_output_bytes=65536,
        allowed_roots=[workdir], extra_env=[{}] * len(script.commands()), background=owner,
    )


def _launched(result, index: int = 0):
    row = db.get_agent_process(result.started_process_ids[index])
    assert row is not None
    return row


def _wait_for_log(row, *, within: float = 5.0) -> str:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        text = background_processes.read_log_tail(row, 4096).strip()
        if text:
            return text
        time.sleep(0.05)
    raise AssertionError("the background process wrote nothing to its log")


# ── executor ───────────────────────────────────────────────────────────


def test_a_background_pipeline_returns_at_once_and_keeps_running(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()
    started = time.monotonic()

    result = _run(tmp_path, agent, "sleep 30 &")

    assert time.monotonic() - started < 5
    assert result.exit_code == 0 and result.timed_out is False
    row = _launched(result)
    _stop_everything.append(row.pid)
    assert result.stdout == (
        f"Started in background: {row.short_id} (pid {row.pid}). "
        f"Logs: processes logs {row.short_id}. Stop: processes stop {row.short_id}.\n"
    )
    # The script's own reap ran when it returned; this one carries its own tag.
    assert background_processes.is_alive(row)
    assert row.status == "running" and row.agent_id == agent.id and row.cwd == "/me"
    assert row.command == "item 0"
    assert Path(row.log_path).parent.name == agent.storage_key


def test_the_next_pipeline_runs_after_a_background_one(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()

    result = _run(tmp_path, agent, "sleep 30 & printf after")

    _stop_everything.append(_launched(result).pid)
    assert result.exit_code == 0
    assert result.stdout.endswith("after")


def test_background_output_goes_to_its_log(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()

    result = _run(tmp_path, agent, f"{sys.executable} -c \"print('hello from bg', flush=True)\" &")

    row = _launched(result)
    assert _wait_for_log(row) == "hello from bg"
    assert "hello from bg" not in result.stdout


def test_a_background_command_that_cannot_start_reports_127_and_records_nothing(tmp_path: Path) -> None:
    agent = _agent()

    result = _run(tmp_path, agent, "no-such-program-xyz &")

    assert result.exit_code == 127
    assert "Command not found: no-such-program-xyz" in result.stderr
    assert "did not start" in result.stderr
    assert result.started_process_ids == ()
    assert db.list_agent_processes(agent.id, ["running", "exited", "stopped"]) == []


def test_a_script_with_ampersand_needs_its_owner(tmp_path: Path) -> None:
    script = parse_shell_script("sleep 1 &")
    with pytest.raises(ValueError, match="background processes belong to"):
        execute_shell_script(
            script, cwd=tmp_path, timeout_seconds=5, max_output_bytes=100,
            allowed_roots=[tmp_path], extra_env=[{}],
        )


def test_stop_reaps_the_process_and_its_detached_grandchild(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()
    result = _run(tmp_path, agent, f'{sys.executable} -c "{_LAUNCHER}" &')
    row = _launched(result)
    grandchild = int(_wait_for_log(row))
    _stop_everything.extend([row.pid, grandchild])
    assert os.getsid(grandchild) != os.getsid(row.pid), "the grandchild detached into its own session"

    stopped = background_processes.stop(row.id, by="operator", reason="Stopped by you")

    assert stopped is not None and stopped.status == "stopped"
    assert stopped.stopped_by == "operator" and stopped.stop_reason == "Stopped by you"
    assert _gone(row.pid) and _gone(grandchild)
    assert background_processes.stop(row.id, by="operator", reason="again") is None


def test_a_launch_inside_the_worker_child_records_its_row_there(tmp_path: Path, _stop_everything) -> None:
    """The default 600 s timeout takes the spawned-child path; the child writes the row."""
    agent = _agent()

    result = _run(tmp_path, agent, "sleep 30 &", timeout=config.require_int("cli_shell_timeout_seconds"))

    assert result.exit_code == 0, result.stderr
    row = _launched(result)
    _stop_everything.append(row.pid)
    assert background_processes.is_alive(row)
    # Started by the spawned shell child, which has exited: not this process's child.
    assert psutil.Process(row.pid).ppid() != os.getpid()
    assert background_processes.stop(row.id, by="agent", reason="Stopped by agent") is not None
    assert _gone(row.pid)


# ── lifecycle ──────────────────────────────────────────────────────────


def test_sweep_marks_a_killed_process_exited(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()
    row = _launched(_run(tmp_path, agent, "sleep 30 &"))
    os.kill(row.pid, signal.SIGKILL)
    assert _gone(row.pid)

    result = background_processes.sweep()

    ended = db.get_agent_process(row.id)
    assert ended is not None and ended.status == "exited"
    assert ended.stop_reason == background_processes.EXITED_REASON and ended.stopped_by is None
    assert result.changed_agent_ids == (agent.id,)
    assert background_processes.live_for_agent(agent.id) == []


def test_a_checkin_fires_once_until_kept_and_never_stops_the_process(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()
    row = _launched(_run(tmp_path, agent, "sleep 30 &"))
    _stop_everything.append(row.pid)
    assert row.checkin_due_at - row.started_at == timedelta(minutes=120)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.execute("UPDATE agent_processes SET checkin_due_at = $1 WHERE id = $2", [past, row.id])

    first = background_processes.sweep()
    second = background_processes.sweep()

    assert [item.id for item in first.checkins] == [row.id]
    assert second.checkins == () and second.changed_agent_ids == ()
    asked = db.get_agent_process(row.id)
    assert asked is not None and asked.checkin_requested_at is not None
    # Unanswered: still running, still asked.
    assert background_processes.is_alive(asked) and asked.status == "running"
    assert [item.id for item in db.list_checkin_requested_agent_processes()] == [row.id]

    kept = background_processes.keep_running(row.id)

    assert kept is not None and kept.checkin_requested_at is None
    assert kept.checkin_due_at > datetime.now(timezone.utc) + timedelta(minutes=119)
    assert db.list_checkin_requested_agent_processes() == []
    db.execute("UPDATE agent_processes SET checkin_due_at = $1 WHERE id = $2", [past, row.id])
    assert [item.id for item in background_processes.sweep().checkins] == [row.id]


def test_keep_and_stop_refuse_an_ended_process(tmp_path: Path) -> None:
    agent = _agent()
    row = _launched(_run(tmp_path, agent, "sleep 30 &"))
    assert background_processes.stop(row.id, by="operator", reason="Stopped by you") is not None

    assert background_processes.keep_running(row.id) is None
    assert background_processes.stop(row.id, by="operator", reason="x") is None


def test_deleting_the_agent_stops_its_processes_and_removes_its_rows(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()
    other = _agent("Other")
    mine = _launched(_run(tmp_path, agent, "sleep 30 &"))
    theirs = _launched(_run(tmp_path, other, "sleep 30 &"))
    _stop_everything.extend([mine.pid, theirs.pid])

    log_dir = background_processes.process_log_dir(agent.storage_key)
    assert log_dir.is_dir()

    agent_repository.delete(agent.id)

    assert _gone(mine.pid)
    assert db.get_agent_process(mine.id) is None
    assert not log_dir.exists(), "the deleted agent's process logs go with it"
    assert background_processes.process_log_dir(other.storage_key).is_dir()
    assert background_processes.is_alive(theirs)


def test_sending_an_agent_home_stops_its_processes(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()
    row = _launched(_run(tmp_path, agent, "sleep 30 &"))
    _stop_everything.append(row.pid)

    send_home(agent.id)

    stopped = db.get_agent_process(row.id)
    assert stopped is not None and stopped.status == "stopped"
    assert stopped.stopped_by == "system" and stopped.stop_reason == "Moved floors"
    assert _gone(row.pid)


def test_moving_a_project_stops_only_processes_working_inside_it(tmp_path: Path, _stop_everything) -> None:
    agent = _agent()
    finance = create_floor("Finance")
    project = project_dir(LOBBY_ID, "books")
    shutil.rmtree(project, ignore_errors=True)
    project.mkdir(parents=True)
    inside = _launched(_run(tmp_path, agent, "sleep 30 &", cwd=project))
    outside = _launched(_run(tmp_path, agent, "sleep 30 &"))
    _stop_everything.extend([inside.pid, outside.pid])

    move_project("books", LOBBY_ID, finance.id)

    moved = db.get_agent_process(inside.id)
    assert moved is not None and moved.status == "stopped" and moved.stop_reason == "Project moved"
    assert _gone(inside.pid)
    assert background_processes.is_alive(outside)


# ── the agent's command and the runtime path ───────────────────────────


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _agent_in_project():
    agent = _agent("Script Runner")
    state = db.get_agent_state(agent.id)
    assert state is not None
    root = project_dir(LOBBY_ID, "demo")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    set_cli_cwd(agent.id, "/projects/demo")
    return agent, state, root


def test_a_backgrounded_segment_that_needs_approval_still_cards() -> None:
    _enable_shell()
    agent, state, _root = _agent_in_project()
    script = "printf a > out.txt; sed s/a/b/ out.txt &"

    result = execute_bm_cli(agent, state, script)

    assert result.approval_required is True
    pending = db.list_cli_approval_requests(status="pending", agent_id=agent.id)
    assert [request.command for request in pending] == [script]
    assert "2) sed s/a/b/ out.txt [" in (pending[0].review_note or "")
    assert db.list_agent_processes(agent.id, ["running", "exited", "stopped"]) == []


def test_a_script_that_starts_one_declares_the_desk_repaint(_stop_everything) -> None:
    _enable_shell()
    agent, state, _root = _agent_in_project()

    result = execute_bm_cli(agent, state, "sleep 30 &")

    assert result.ok is True, result.prompt_content
    assert result.data is not None
    (process_id,) = result.data["process_ids"]
    row = db.get_agent_process(process_id)
    assert row is not None
    _stop_everything.append(row.pid)
    assert row.command == "sleep 30" and row.cwd == "/projects/demo"
    assert result.data["activity"] == {
        "event": "agent_process_changed",
        "detail": f"{agent.name} started 1 background process",
        "extra": {"agent_id": agent.id},
    }
    assert "Started in background" in result.prompt_content
    # A retried turn would launch a duplicate.
    assert result.blocks_retry is True
    assert execute_bm_cli(agent, state, "printf a; printf b").blocks_retry is False

    listed = execute_bm_cli(agent, state, "processes")
    assert row.short_id in listed.prompt_content and "running for" in listed.prompt_content

    stopped = execute_bm_cli(agent, state, f"processes stop {row.short_id}")
    assert stopped.ok is True
    assert stopped.data is not None and stopped.data["activity"]["event"] == "agent_process_changed"
    assert _gone(row.pid)
    ended = db.get_agent_process(row.id)
    assert ended is not None and ended.stopped_by == "agent" and ended.stop_reason == "Stopped by agent"

    again = execute_bm_cli(agent, state, f"processes stop {row.short_id}")
    assert again.ok is False and "already ended" in str(again.data)


def test_processes_logs_and_id_rules() -> None:
    agent, state, _root = _agent_in_project()
    row = db.create_agent_process(
        process_id="abcdef12-0000-0000-0000-000000000000", agent_id=agent.id, command="vite", cwd="/me",
        run_tag="t", pid=os.getpid(), pid_create_time=psutil.Process().create_time(),
        log_path=str(background_processes.process_log_path(agent.storage_key, "abcdef12-log")),
        started_at=datetime.now(timezone.utc), checkin_due_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    log = Path(row.log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("ready on 5199\n", encoding="utf-8")

    logs = execute_bm_cli(agent, state, "processes logs abcdef12")
    short = execute_bm_cli(agent, state, "processes logs abc")
    missing = execute_bm_cli(agent, state, "processes logs 99999999")
    other = _agent("Someone")
    not_mine = execute_bm_cli(other, db.get_agent_state(other.id), "processes logs abcdef12")
    log.unlink()
    gone = execute_bm_cli(agent, state, "processes logs abcdef12")
    usage = execute_bm_cli(agent, state, "processes restart abcdef12")

    assert logs.ok is True and "ready on 5199" in logs.prompt_content
    assert short.ok is False and "at least 8 characters" in str(short.data)
    assert missing.ok is False and "No background process of yours matches" in str(missing.data)
    assert not_mine.ok is False
    assert gone.ok is False and "is gone" in str(gone.data)
    assert usage.ok is False and "processes logs <id>" in str(usage.data)
    # The row is this test process; end it without reaping anything.
    db.mark_agent_process_ended(
        row.id, status="exited", ended_at=datetime.now(timezone.utc), stopped_by=None, stop_reason="test",
    )


def test_processes_says_when_there_are_none() -> None:
    agent, state, _root = _agent_in_project()

    listed = execute_bm_cli(agent, state, "processes list")

    assert listed.ok is True
    assert "You have no background processes" in listed.prompt_content


_TOKEN_ENV = {
    "GH_TOKEN": "tok-secret", "GITHUB_TOKEN": "tok-secret",
    "GIT_ASKPASS": "/askpass", "SSH_ASKPASS": "/askpass",
}


def test_a_background_pipeline_never_gets_the_nest_git_credentials(
    monkeypatch: pytest.MonkeyPatch, _stop_everything,
) -> None:
    from core.bm_cli import nest_git
    from core.bm_cli.runtime import VIRTUAL_COMMANDS
    from core.bm_cli.script_prepare import prepare_script
    from core.bm_cli.script_runner import _background_owner, _executable

    monkeypatch.setattr(nest_git, "nest_git_shell_env", lambda *_args, **_kwargs: dict(_TOKEN_ENV))
    agent, _state, root = _agent_in_project()
    # Stand-ins named gh and git that stay alive, so their environment can be read.
    (root / "bin").mkdir()
    for name in ("gh", "git"):
        tool = root / "bin" / name
        tool.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
        tool.chmod(0o755)
    mixed = prepare_script(
        agent, "./bin/gh auth status & ./bin/git push & ./bin/git status", "/projects/demo",
        virtual_commands=VIRTUAL_COMMANDS,
    )
    _script, mixed_envs = _executable(agent, mixed, [segment.parsed for segment in mixed.segments])

    assert all(key not in mixed_envs[0] and key not in mixed_envs[1] for key in _TOKEN_ENV)
    assert mixed_envs[0]["GIT_AUTHOR_NAME"] == agent.name
    assert mixed_envs[2]["GH_TOKEN"] == "tok-secret", "a foreground git still gets its credentials"

    launched = prepare_script(
        agent, "./bin/gh auth status & ./bin/git push &", "/projects/demo", virtual_commands=VIRTUAL_COMMANDS,
    )
    script, envs = _executable(agent, launched, [segment.parsed for segment in launched.segments])
    result = execute_shell_script(
        script, cwd=launched.cwd_real, timeout_seconds=10, max_output_bytes=4096,
        allowed_roots=launched.roots, extra_env=envs, background=_background_owner(agent, launched),
    )

    assert len(result.started_process_ids) == 2, result.stderr
    for process_id in result.started_process_ids:
        row = db.get_agent_process(process_id)
        assert row is not None
        _stop_everything.append(row.pid)
        environ = psutil.Process(row.pid).environ()
        assert not set(_TOKEN_ENV) & set(environ), sorted(environ)


# ── idle agents carry no process text ──────────────────────────────────


def _context(agent, contract_kind: str) -> list[dict]:
    from core.llm import context_builder
    from core.models.message import HUMAN_SENDER_ID

    turn = context_builder.TurnContext(
        agent=agent,
        state=db.get_agent_state(agent.id),
        trigger={"type": "human_chat", "content": "Status?", "from_name": "Human Operator"},
        conversation_history=[{"from_agent": HUMAN_SENDER_ID, "from_name": "Human Operator", "content": "Earlier ask"}],
        prompt_notifications=[],
        reference_materials=[],
        contract_kind=contract_kind,
    )
    return context_builder.build_context(turn)


def _process_blocks(messages: list[dict]) -> list[dict]:
    return [message for message in messages if "BACKGROUND PROCESSES" in str(message["content"])]


@pytest.mark.parametrize("contract_kind", ["decision", "execution"])
def test_an_idle_agent_carries_no_process_text_and_a_busy_one_exactly_one_block(
    contract_kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stop_everything,
) -> None:
    from core.llm import context_builder

    agent = _agent()
    idle = _context(agent, contract_kind)
    with monkeypatch.context() as patched:
        patched.setattr(context_builder, "live_processes_messages", lambda *_args: [])
        without_feature = _context(agent, contract_kind)

    assert _process_blocks(idle) == []
    assert len(idle) == len(without_feature)

    _stop_everything.append(_launched(_run(tmp_path, agent, "sleep 30 &")).pid)
    busy = _context(agent, contract_kind)

    assert len(_process_blocks(busy)) == 1
    assert len(busy) == len(idle) + 1
