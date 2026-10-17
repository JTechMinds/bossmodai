"""Where background processes surface: the turn prompt, the desk API, Needs, and the settings.

Rows stand for this test process (its own pid and creation time) wherever a
live process is only needed to be *seen*; the one Stop test launches a real
`sleep` through the executor and stops it.
"""

from __future__ import annotations

import logging
import os
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
from core.bm_cli.shell_executor import execute_shell_script
from core.bm_cli.shell_script import parse_shell_script
from core.llm import context_builder
from core.llm.live_processes_block import render_live_processes
from core.models.message import HUMAN_SENDER_ID
from db.crud import query_one
from db.settings import reconcile_factory_shell_timeout

_HEADER = "LIVE BACKGROUND PROCESSES YOU STARTED"


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
    """End every row a test left running.

    Rows that stand for this test process are marked ended without a reap
    (their tag names nothing); a real launch is stopped, which reaps it.
    """
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


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: ensure_local_api_token()}


def _self_row(agent, *, command: str = "npx vite --port 5199", log: str | None = "VITE ready\n"):
    """A running row whose root process is this test process."""
    now = datetime.now(timezone.utc)
    process_id = f"{os.urandom(4).hex()}-feed-0000-0000-000000000000"
    log_path = background_processes.process_log_path(agent.storage_key, process_id)
    if log is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log, encoding="utf-8")
    return db.create_agent_process(
        process_id=process_id, agent_id=agent.id, command=command, cwd="/projects/site",
        run_tag=os.urandom(8).hex(), pid=os.getpid(), pid_create_time=psutil.Process().create_time(),
        log_path=str(log_path), started_at=now - timedelta(minutes=5), checkin_due_at=now + timedelta(hours=2),
    )


def _ask_checkin(row) -> None:
    db.execute(
        "UPDATE agent_processes SET checkin_due_at = $1 WHERE id = $2",
        [datetime.now(timezone.utc) - timedelta(seconds=1), row.id],
    )
    assert [item.id for item in background_processes.sweep().checkins] == [row.id]


# ── the turn prompt ────────────────────────────────────────────────────


def _messages(agent, contract_kind: str) -> list[dict]:
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


@pytest.mark.parametrize("contract_kind", ["decision", "execution"])
def test_the_block_reaches_both_turn_kinds_only_while_something_runs(contract_kind: str) -> None:
    agent = db.create_agent("Charles", role="Eng")
    assert not any(_HEADER in str(m["content"]) for m in _messages(agent, contract_kind))

    row = _self_row(agent)
    messages = _messages(agent, contract_kind)

    blocks = [i for i, m in enumerate(messages) if m["role"] == "system" and _HEADER in str(m["content"])]
    assert len(blocks) == 1
    block = messages[blocks[0]]["content"]
    assert f"{row.short_id} · npx vite --port 5199 · /projects/site · running for 5m" in block
    assert block.rstrip().endswith("Stop any you no longer need with `processes stop <id>`.")
    # Right before the conversation history.
    assert messages[blocks[0] + 1]["content"].endswith("Earlier ask")


def test_the_block_says_when_the_operator_was_asked() -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _self_row(agent)
    _ask_checkin(row)

    block = render_live_processes(agent.id, None)

    assert block is not None and "running for 5m · operator asked whether to keep it" in block


# ── the desk API ───────────────────────────────────────────────────────


def test_the_desk_lists_running_then_recent_ended_with_log_tails(client: TestClient) -> None:
    agent = db.create_agent("Charles", role="Eng")
    running = _self_row(agent)
    ended = _self_row(agent, command="node serve.mjs", log=None)
    db.mark_agent_process_ended(
        ended.id, status="stopped", ended_at=datetime.now(timezone.utc), stopped_by="operator",
        stop_reason="Stopped by you",
    )

    res = client.get(f"/api/agents/{agent.id}/processes", headers=_auth())

    assert res.status_code == 200, res.text
    rows = res.json()
    assert [row["id"] for row in rows] == [running.id, ended.id]
    assert rows[0]["status"] == "running" and rows[0]["log_tail"] == "VITE ready\n" and rows[0]["log_error"] is None
    assert rows[1]["stop_reason"] == "Stopped by you"
    assert rows[1]["log_tail"] is None and rows[1]["log_error"] == "The log file is gone."
    assert client.get("/api/agents/nobody/processes", headers=_auth()).status_code == 404


def test_stop_reaps_a_real_process_and_refuses_twice(client: TestClient, tmp_path: Path) -> None:
    agent = db.create_agent("Charles", role="Eng")
    script = parse_shell_script("sleep 30 &")
    result = execute_shell_script(
        script, cwd=tmp_path, timeout_seconds=10, max_output_bytes=4096, allowed_roots=[tmp_path],
        extra_env=[{}], background=BackgroundOwner(agent.id, agent.storage_key, "/me", ("sleep 30",)),
    )
    row = db.get_agent_process(result.started_process_ids[0])
    assert row is not None

    res = client.post(f"/api/agent-processes/{row.id}/stop", headers=_auth())

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "stopped" and res.json()["stop_reason"] == "Stopped by you"
    deadline = time.monotonic() + 5
    while psutil.pid_exists(row.pid) and psutil.Process(row.pid).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline, "the stopped process is still running"
        time.sleep(0.05)
    assert client.post(f"/api/agent-processes/{row.id}/stop", headers=_auth()).status_code == 404
    assert client.post("/api/agent-processes/nope/stop", headers=_auth()).status_code == 404
    changed = db.query("SELECT event FROM activity_log WHERE event = 'agent_process_changed'")
    assert len(changed) == 1


def test_keep_rearms_a_running_process_only(client: TestClient) -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _self_row(agent)
    _ask_checkin(row)

    res = client.post(f"/api/agent-processes/{row.id}/keep", headers=_auth())

    assert res.status_code == 200, res.text
    assert res.json()["checkin_requested_at"] is None
    assert db.list_checkin_requested_agent_processes() == []
    db.mark_agent_process_ended(
        row.id, status="exited", ended_at=datetime.now(timezone.utc), stopped_by=None, stop_reason="gone",
    )
    assert client.post(f"/api/agent-processes/{row.id}/keep", headers=_auth()).status_code == 404


# ── the watchdog tick ──────────────────────────────────────────────────


class _Sink:
    def __init__(self) -> None:
        self.activities: list[tuple[str, dict | None]] = []

    async def broadcast_activity(self, event, detail, agent_name=None, extra=None) -> None:
        self.activities.append((event, extra))


async def test_the_watchdog_announces_a_checkin_and_the_change(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agent_loop import watchdog as watchdog_module

    sink = _Sink()
    monkeypatch.setattr(watchdog_module, "manager", sink)
    agent = db.create_agent("Charles", role="Eng")
    row = _self_row(agent)
    db.execute(
        "UPDATE agent_processes SET checkin_due_at = $1 WHERE id = $2",
        [datetime.now(timezone.utc) - timedelta(seconds=1), row.id],
    )

    await watchdog_module.TaskWatchdog()._sweep_background_processes()
    await watchdog_module.TaskWatchdog()._sweep_background_processes()

    assert sink.activities == [
        ("agent_process_checkin", {"agent_id": agent.id, "process_id": row.id}),
        ("agent_process_changed", {"agent_id": agent.id}),
    ]


# ── Needs ──────────────────────────────────────────────────────────────


def test_a_waiting_checkin_is_a_need_with_keep_and_stop(client: TestClient) -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _self_row(agent)
    assert client.get("/api/needs", headers=_auth()).json() == []
    _ask_checkin(row)

    needs = client.get("/api/needs", headers=_auth()).json()

    (need,) = needs
    asked = db.get_agent_process(row.id)
    assert asked is not None and asked.checkin_requested_at is not None
    assert need == {
        "id": row.id,
        "kind": "process_checkin",
        "agent_id": agent.id,
        "agent_name": "Charles",
        "title": "Charles's background process is still running",
        "sub": "npx vite --port 5199",
        "created_at": asked.checkin_requested_at.isoformat(),
        "conversation_id": agent.id,
        "actions": [
            {"label": "Keep running", "method": "POST", "tone": "primary",
             "href": f"/api/agent-processes/{row.id}/keep"},
            {"label": "Stop", "method": "POST", "tone": "quiet",
             "href": f"/api/agent-processes/{row.id}/stop"},
        ],
    }
    assert client.post(need["actions"][0]["href"], headers=_auth()).status_code == 200
    assert client.get("/api/needs", headers=_auth()).json() == []


def test_an_ended_process_leaves_needs() -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _self_row(agent)
    _ask_checkin(row)
    db.mark_agent_process_ended(
        row.id, status="exited", ended_at=datetime.now(timezone.utc), stopped_by=None, stop_reason="gone",
    )

    assert db.list_checkin_requested_agent_processes() == []


# ── settings ───────────────────────────────────────────────────────────


def _stored(key: str) -> str | None:
    row = query_one("SELECT value FROM settings WHERE key = $1", [key])
    return None if row is None else str(row["value"])


def _forget_reconcile(value: str) -> None:
    db.set_setting("cli_shell_timeout_seconds", value, "cli_policy")
    db.execute("DELETE FROM settings WHERE key = $1", ["cli_shell_timeout_factory_reconciled"])


def test_the_seeds_are_600_seconds_and_a_two_hour_checkin() -> None:
    assert _stored("cli_shell_timeout_seconds") == "600"
    assert _stored("background_process_checkin_minutes") == "120"


def test_the_reconcile_moves_only_the_old_30_once(caplog: pytest.LogCaptureFixture) -> None:
    _forget_reconcile("30")
    with caplog.at_level(logging.INFO, logger="db.settings"):
        reconcile_factory_shell_timeout()
    assert _stored("cli_shell_timeout_seconds") == "600"
    assert any("30s to 600s" in record.getMessage() for record in caplog.records)

    # After the pass, a 30 the operator sets is theirs.
    db.set_setting("cli_shell_timeout_seconds", "30", "cli_policy")
    reconcile_factory_shell_timeout()
    assert _stored("cli_shell_timeout_seconds") == "30"


@pytest.mark.parametrize("value", ["45", "300", "3600"])
def test_the_reconcile_leaves_any_other_value(value: str) -> None:
    _forget_reconcile(value)

    reconcile_factory_shell_timeout()

    assert _stored("cli_shell_timeout_seconds") == value


def test_a_checkin_of_zero_minutes_is_refused_on_save(client: TestClient) -> None:
    res = client.put(
        "/api/settings/background_process_checkin_minutes",
        params={"value": "0", "category": "cli_policy"}, headers=_auth(),
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Background process check-in (minutes) must be a whole number of at least 1."
    assert _stored("background_process_checkin_minutes") == "120"
    ok = client.put(
        "/api/settings/background_process_checkin_minutes",
        params={"value": "30", "category": "cli_policy"}, headers=_auth(),
    )
    assert ok.status_code == 200, ok.text


# ── the execution contract's script line ───────────────────────────────

_CONTRACT = "runtime_contract_execution"
_LINE_MARKER = "background_script_prompt_line_reconciled"


def _line_edit() -> tuple[str, str]:
    from db import settings as settings_db

    ((old, new),) = settings_db._BACKGROUND_SCRIPT_PROMPT_LINE_EDITS[_CONTRACT]
    return old, new


def _rerun_line_reconcile() -> None:
    from db.settings import reconcile_background_script_prompt_line

    db.execute("DELETE FROM settings WHERE key = $1", [_LINE_MARKER])
    reconcile_background_script_prompt_line()


def _shipped_line() -> str:
    """The scripts line the shipped contract holds now (a later revision rewrote it again)."""
    from db import settings as settings_db

    ((_previous, shipped),) = settings_db._PROGRAM_PATHS_PROMPT_LINE_EDITS[_CONTRACT]
    return shipped


def test_the_shipped_contract_teaches_ampersand() -> None:
    from core.default_prompts import prompt_file_path

    old, new = _line_edit()
    shipped = _shipped_line()
    lines = prompt_file_path(_CONTRACT).read_text(encoding="utf-8").splitlines()
    assert lines.count(shipped) == 1 and old not in lines
    assert "background &" in old
    for line in (new, shipped):
        assert "background &" not in line
        assert "end a pipeline with & to keep it running (dev servers, watchers); manage with processes" in line
        assert "followed by &&, ; or &" in line


def test_a_stored_contract_with_the_old_line_gets_the_new_one_once() -> None:
    from core.default_prompts import load_default_prompt

    old, new = _line_edit()
    shipped = load_default_prompt(_CONTRACT)
    edited = "Operator note.\n" + shipped.replace(_shipped_line(), old)
    db.set_setting(_CONTRACT, edited, "advanced")

    _rerun_line_reconcile()

    assert _stored(_CONTRACT) == "Operator note.\n" + shipped.replace(_shipped_line(), new)
    db.set_setting(_CONTRACT, edited, "advanced")
    from db.settings import reconcile_background_script_prompt_line

    reconcile_background_script_prompt_line()
    assert _stored(_CONTRACT) == edited, "the marker makes the pass run once"


def test_a_stored_contract_without_the_old_line_is_left_alone_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db.set_setting(_CONTRACT, "An operator's own contract.", "advanced")

    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun_line_reconcile()

    assert _stored(_CONTRACT) == "An operator's own contract."
    assert any(_CONTRACT in record.getMessage() for record in caplog.records if record.levelno == logging.WARNING)
