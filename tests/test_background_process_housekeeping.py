"""Background processes, revision 2: retention, lifecycle-stop announcements, and real folders for programs.

Rows that only need to be *seen* or stopped by bookkeeping stand for this
test process (its pid and creation time, a run tag nothing carries, so a
stop reaps nothing). A row that must really work inside a folder is a real
``sleep`` launched through the executor.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, ensure_local_api_token, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli import background_processes
from core.bm_cli.background_processes import BackgroundOwner
from core.bm_cli.floor_roots import floor_root, project_dir
from core.bm_cli.session import set_cli_cwd
from core.bm_cli.shell_executor import execute_shell_script
from core.bm_cli.shell_script import parse_shell_script
from core.bm_cli.virtual_fs import resolve_cli_path
from core.floor_moves import plan_move
from core.floors import send_home
from db.crud import query_one
from db.floors import LOBBY_ID, create_floor
from db.settings import seed_defaults


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


@pytest.fixture(autouse=True)
def _end_rows() -> Iterator[None]:
    """End every row a test left running; a real launch is stopped, which reaps it."""
    yield
    for row in db.list_running_agent_processes():
        if row.pid == os.getpid():
            db.mark_agent_process_ended(
                row.id, status="exited", ended_at=datetime.now(timezone.utc), stopped_by=None,
                stop_reason="test teardown",
            )
        else:
            background_processes.stop(row.id, by="system", reason="test teardown")
    db.close_connection()


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: ensure_local_api_token()}


def _agent(name: str, *, floor_id: str | None = None, x: int = 1):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1, floor_id=floor_id)


def _self_row(agent, *, log: str | None = "ready\n"):
    """A running row whose root process is this test process, with an optional log."""
    now = datetime.now(timezone.utc)
    process_id = f"{os.urandom(4).hex()}-feed-0000-0000-000000000000"
    log_path = background_processes.process_log_path(agent.storage_key, process_id)
    if log is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log, encoding="utf-8")
    return db.create_agent_process(
        process_id=process_id, agent_id=agent.id, command="npx vite", cwd="/me",
        run_tag=os.urandom(8).hex(), pid=os.getpid(), pid_create_time=psutil.Process().create_time(),
        log_path=str(log_path), started_at=now - timedelta(days=30), checkin_due_at=now + timedelta(hours=2),
    )


def _ended(row, *, days_ago: float, status: str = "stopped"):
    ended = db.mark_agent_process_ended(
        row.id, status=status, ended_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        stopped_by="operator" if status == "stopped" else None, stop_reason="test",
    )
    assert ended is not None
    return ended


# ── step 22: retention ────────────────────────────────────────────────


def test_old_ended_rows_and_their_logs_go_and_everything_else_stays() -> None:
    agent = _agent("Charles")
    old_stopped = _ended(_self_row(agent), days_ago=20)
    old_exited_no_log = _ended(_self_row(agent, log=None), days_ago=15, status="exited")
    recent = _ended(_self_row(agent), days_ago=1, status="exited")
    running = _self_row(agent)

    removed = background_processes.prune_ended(datetime.now(timezone.utc) - timedelta(days=14))

    assert removed == 2
    assert db.get_agent_process(old_stopped.id) is None and not Path(old_stopped.log_path).exists()
    assert db.get_agent_process(old_exited_no_log.id) is None, "a log already gone is not an error"
    assert db.get_agent_process(recent.id) is not None and Path(recent.log_path).exists()
    kept = db.get_agent_process(running.id)
    assert kept is not None and kept.status == "running" and Path(running.log_path).exists()


def test_a_log_that_cannot_be_deleted_raises_with_its_path() -> None:
    agent = _agent("Charles")
    row = _self_row(agent, log=None)
    # A directory where the log should be: unlink fails with something other than "gone".
    Path(row.log_path).mkdir(parents=True)
    _ended(row, days_ago=30)

    with pytest.raises(OSError, match=row.log_path):
        background_processes.prune_ended(datetime.now(timezone.utc) - timedelta(days=14))


def test_prune_refuses_a_naive_cutoff() -> None:
    with pytest.raises(ValueError):
        background_processes.prune_ended(datetime.now())


def test_the_watchdog_prunes_processes_by_the_retention_setting(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    from core.agent_loop import watchdog as watchdog_module

    cutoffs: list[datetime] = []
    monkeypatch.setattr(background_processes, "prune_ended", lambda before: cutoffs.append(before) or 3)
    before = datetime.now(timezone.utc)

    with caplog.at_level(logging.INFO, logger=watchdog_module.__name__):
        watchdog_module.TaskWatchdog()._prune_history_if_due()

    assert len(cutoffs) == 1 and abs(cutoffs[0] - (before - timedelta(days=14))) < timedelta(seconds=5)
    assert any("3 ended background process(es) (> 14 days)" in r.getMessage() for r in caplog.records)


def test_the_retention_setting_is_seeded_and_must_be_positive() -> None:
    assert query_one("SELECT value, category FROM settings WHERE key = 'agent_process_retention_days'") == {
        "value": "14", "category": "advanced",
    }
    client, headers = _client()

    refused = client.put(
        "/api/settings/agent_process_retention_days", params={"value": "0", "category": "advanced"}, headers=headers,
    )

    assert refused.status_code == 400
    assert refused.json()["detail"] == "Background process history (days) must be a whole number of at least 1."
    ok = client.put(
        "/api/settings/agent_process_retention_days", params={"value": "30", "category": "advanced"}, headers=headers,
    )
    assert ok.status_code == 200, ok.text


# ── step 23: lifecycle stops are announced ────────────────────────────


class _Services:
    async def reset_agent_runtime(self, agent_id: str) -> None:
        return None


def _record_announcements(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def _record(sink, **kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("api.routes.floors.announce_processes_changed", _record)
    monkeypatch.setattr("api.routes.floors.runtime_services", _Services())
    return calls


def test_stop_helpers_return_the_rows_they_stopped() -> None:
    agent = _agent("Charles")
    first, second = _self_row(agent), _self_row(agent)

    stopped = background_processes.stop_for_agent(agent.id, reason="Moved floors")

    assert sorted(row.id for row in stopped) == sorted([first.id, second.id])
    assert all(row.status == "stopped" and row.stop_reason == "Moved floors" for row in stopped)
    assert background_processes.stop_for_agent(agent.id, reason="Moved floors") == []


def test_a_floor_delete_announces_only_agents_whose_processes_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_announcements(monkeypatch)
    finance = create_floor("Finance")
    busy = _agent("Ada", floor_id=finance.id, x=1)
    _idle = _agent("Bob", floor_id=finance.id, x=2)
    row = _self_row(busy)
    client, headers = _client()

    response = client.delete(f"/api/floors/{finance.id}?occupants=send_home", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["process_stopped_agent_ids"] == [busy.id]
    assert calls == [{
        "agent_id": busy.id, "agent_name": "Ada", "detail": "Background processes stopped: Moved floors",
    }]
    assert db.get_agent_process(row.id).stop_reason == "Moved floors"


def test_a_move_announces_only_movers_whose_processes_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_announcements(monkeypatch)
    finance = create_floor("Finance")
    busy, idle = _agent("Ada", x=1), _agent("Bob", x=2)
    _self_row(busy)
    plan = plan_move(finance.id, agent_ids=[busy.id, idle.id], channel_ids=[])
    client, headers = _client()

    response = client.post(
        f"/api/floors/{finance.id}/move", headers=headers,
        json={"agent_ids": [busy.id, idle.id], "channel_ids": [], "fingerprint": plan.fingerprint},
    )

    assert response.status_code == 200, response.text
    assert response.json()["process_stopped_agent_ids"] == [busy.id]
    assert [call["agent_id"] for call in calls] == [busy.id]
    assert calls[0]["detail"] == "Background processes stopped: Moved floors"


def test_a_project_move_announces_only_agents_working_inside_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_announcements(monkeypatch)
    legal = create_floor("Legal")
    inside, outside = _agent("Ada", x=1), _agent("Bob", x=2)
    site = project_dir(LOBBY_ID, "site")
    shutil.rmtree(site, ignore_errors=True)
    site.mkdir(parents=True)
    in_row = _launch_sleep(inside, cwd=site)
    out_row = _self_row(outside)
    client, headers = _client()

    response = client.post(
        f"/api/floors/{legal.id}/projects/move", headers=headers, json={"project": "site", "from_floor_id": LOBBY_ID},
    )

    assert response.status_code == 200, response.text
    assert response.json()["process_stopped_agent_ids"] == [inside.id]
    assert calls == [{
        "agent_id": inside.id, "agent_name": "Ada", "detail": "Background processes stopped: Project moved",
    }]
    assert db.get_agent_process(in_row.id).status == "stopped"
    assert db.get_agent_process(out_row.id).status == "running"


def _launch_sleep(agent, *, cwd: Path):
    result = execute_shell_script(
        parse_shell_script("sleep 30 &"), cwd=cwd, timeout_seconds=10, max_output_bytes=4096,
        allowed_roots=[cwd], extra_env=[{}],
        background=BackgroundOwner(agent.id, agent.storage_key, "/projects/site", ("sleep 30",)),
    )
    (process_id,) = result.started_process_ids
    row = db.get_agent_process(process_id)
    assert row is not None
    return row


# ── step 25: programs see the real folders ────────────────────────────

_SHOW = "import os; print(os.environ['BOSSMOD_ME'], os.environ.get('BOSSMOD_PROJECTS', '-'))"


def _agent_in_project():
    agent = _agent("Runner")
    root = project_dir(LOBBY_ID, "demo")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    set_cli_cwd(agent.id, "/projects/demo")
    return agent, root


def _real(agent, mount: str) -> str:
    real = resolve_cli_path(agent.storage_key, "/", mount).real_path
    assert real is not None
    return str(real)


def test_foreground_and_background_programs_see_the_real_folders() -> None:
    from core.bm_cli.runtime import VIRTUAL_COMMANDS
    from core.bm_cli.script_prepare import prepare_script
    from core.bm_cli.script_runner import _background_owner, _executable

    agent, _root = _agent_in_project()
    # A bare name: an absolute interpreter path would be outside the path jail.
    text = f'python3 -c "{_SHOW}" ; python3 -c "{_SHOW}" &'
    prepared = prepare_script(agent, text, "/projects/demo", virtual_commands=VIRTUAL_COMMANDS)
    script, envs = _executable(agent, prepared, [segment.parsed for segment in prepared.segments])

    result = execute_shell_script(
        script, cwd=prepared.cwd_real, timeout_seconds=10, max_output_bytes=4096,
        allowed_roots=prepared.roots, extra_env=envs, background=_background_owner(agent, prepared),
    )

    expected = f"{_real(agent, '/me')} {_real(agent, '/projects')}"
    assert result.stdout.splitlines()[0] == expected, result.stderr
    row = db.get_agent_process(result.started_process_ids[0])
    assert row is not None
    deadline = datetime.now(timezone.utc) + timedelta(seconds=5)
    while not background_processes.read_log_tail(row, 4096).strip():
        assert datetime.now(timezone.utc) < deadline, "the background program wrote nothing"
        time.sleep(0.05)
    assert background_processes.read_log_tail(row, 4096).strip() == expected
    assert _real(agent, "/projects") == str(floor_root(LOBBY_ID))


def test_a_vacationing_agent_gets_no_projects_folder() -> None:
    from core.bm_cli import runtime

    agent = _agent("Runner")
    away = send_home(agent.id)

    env = runtime._agent_path_env(away)

    assert env == {"BOSSMOD_ME": _real(away, "/me")}


# ── step 25: the execution contract's line, original -> v2 -> v3 ──────

_CONTRACT = "runtime_contract_execution"
_MARKERS = ("background_script_prompt_line_reconciled", "program_paths_prompt_line_reconciled")


def _chain() -> tuple[str, str, str]:
    from db import settings as settings_db

    ((original, v2),) = settings_db._BACKGROUND_SCRIPT_PROMPT_LINE_EDITS[_CONTRACT]
    ((v2_again, v3),) = settings_db._PROGRAM_PATHS_PROMPT_LINE_EDITS[_CONTRACT]
    assert v2_again == v2
    return original, v2, v3


def _stored() -> str:
    row = query_one("SELECT value FROM settings WHERE key = $1", [_CONTRACT])
    assert row is not None
    return str(row["value"])


def _reseed_with(contract: str) -> None:
    db.set_setting(_CONTRACT, contract, "advanced")
    for marker in _MARKERS:
        db.execute("DELETE FROM settings WHERE key = $1", [marker])
    seed_defaults()


def test_the_shipped_contract_names_the_env_vars_on_one_line() -> None:
    from core.default_prompts import load_default_prompt

    original, v2, v3 = _chain()
    lines = load_default_prompt(_CONTRACT).splitlines()
    assert lines.count(v3) == 1 and original not in lines and v2 not in lines
    assert "BOSSMOD_ME / BOSSMOD_PROJECTS" in v3 and "\n" not in v3


@pytest.mark.parametrize("start", ["original", "v2"])
def test_a_stored_contract_reaches_v3_from_either_older_line(start: str) -> None:
    from core.default_prompts import load_default_prompt

    original, v2, v3 = _chain()
    shipped = load_default_prompt(_CONTRACT)
    older = original if start == "original" else v2

    _reseed_with("Operator note.\n" + shipped.replace(v3, older))

    assert _stored() == "Operator note.\n" + shipped
