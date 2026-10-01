"""HA-SEC-P1-06 — CLI simulator defaults to dry-run."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.filesystem import agent_artifact_dir


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


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return app


def _client() -> TestClient:
    return TestClient(_app())


def _auth_headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _probe_path(agent) -> Path:
    return agent_artifact_dir(agent.storage_key) / "sim-dry-run-probe.md"


def test_simulator_default_post_does_not_create_files() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    probe = _probe_path(agent)
    if probe.exists():
        probe.unlink()

    client = _client()
    res = client.post(
        "/api/cli-policy/simulator/execute",
        headers=_auth_headers(),
        json={
            "command": "write /me/sim-dry-run-probe.md",
            "agent_id": agent.id,
            "content": "should not land",
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dry_run"] is True
    assert body["ok"] is True
    assert body["kind"] == "dry_run"
    assert not probe.exists()


def test_simulator_explicit_execute_writes_file() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    probe = _probe_path(agent)
    if probe.exists():
        probe.unlink()

    client = _client()
    res = client.post(
        "/api/cli-policy/simulator/execute",
        headers=_auth_headers(),
        json={
            "command": "write /me/sim-dry-run-probe.md",
            "agent_id": agent.id,
            "content": "wrote for real",
            "execute": True,
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dry_run"] is False
    assert body["ok"] is True
    assert body["kind"] == "write"
    assert probe.exists()
    assert "wrote for real" in probe.read_text(encoding="utf-8")
    probe.unlink(missing_ok=True)


def test_simulator_dry_run_false_is_explicit_execute() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    probe = _probe_path(agent)
    if probe.exists():
        probe.unlink()

    client = _client()
    res = client.post(
        "/api/cli-policy/simulator/execute",
        headers=_auth_headers(),
        json={
            "command": "write /me/sim-dry-run-probe.md",
            "agent_id": agent.id,
            "content": "via dry_run false",
            "dry_run": False,
        },
    )

    assert res.status_code == 200
    assert res.json()["dry_run"] is False
    assert probe.exists()
    probe.unlink(missing_ok=True)


# ─── A real run shows its declared side effects live (Revision 7, R53) ───


_RESPONSE_KEYS = {
    "command", "ok", "exit_code", "executor", "kind", "output", "detail", "cwd",
    "approval_required", "approval_request_id", "matched_rule_id", "dry_run",
}


class _RecordingManager:
    def __init__(self) -> None:
        self.activities: list[dict] = []
        self.chat: list[dict] = []
        self.channel: list[dict] = []

    async def broadcast_activity(self, event, detail, agent_name=None, extra=None) -> None:
        self.activities.append({"event": event, "detail": detail, "agent_name": agent_name, "extra": extra})

    async def broadcast_chat_message(self, **kwargs) -> None:
        self.chat.append(kwargs)

    async def broadcast_channel_message(self, **kwargs) -> None:
        self.channel.append(kwargs)


def _execute(agent_id: str, command: str, content: str | None = None) -> dict:
    res = _client().post(
        "/api/cli-policy/simulator/execute",
        headers=_auth_headers(),
        json={"command": command, "agent_id": agent_id, "content": content, "execute": True},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_a_simulated_schedules_add_shows_the_note_and_repaints_the_desk(monkeypatch) -> None:
    import json

    recording = _RecordingManager()
    monkeypatch.setattr("api.routes.cli_policy.manager", recording)
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    body = json.dumps({
        "title": "Ping the operator", "instructions": "Send a short ping.",
        "recurrence": {"frequency": "daily", "interval": 1, "start_date": "2026-10-01",
                       "every_minutes": 5, "window_start": "00:00", "window_end": "23:59"},
    })

    result = _execute(agent.id, "schedules add", body)

    assert set(result) == _RESPONSE_KEYS
    assert result["ok"] is True and result["dry_run"] is False
    schedule = db.list_schedules_for_agent(agent.id)[0]
    note = 'Ada scheduled "Ping the operator": Every day, every 5 minutes'
    assert recording.activities == [{
        "event": "schedule_changed", "detail": note, "agent_name": "Ada",
        "extra": {"agent_id": agent.id, "schedule_id": schedule.id},
    }]
    assert [(item["agent_id"], item["content"]) for item in recording.chat] == [(agent.id, note)]


def test_a_simulated_gate_deny_posts_no_blocked_line(monkeypatch) -> None:
    from core.bm_cli.types import BossModCliResult

    recording = _RecordingManager()
    monkeypatch.setattr("api.routes.cli_policy.manager", recording)
    monkeypatch.setattr(
        "core.bm_cli.runtime.execute_bm_cli",
        lambda agent, state, command, content=None, **kwargs: BossModCliResult(
            command=command, ok=False, detail="Shell Executor is off", prompt_content="Shell Executor is off",
            kind="shell_executor_deny", executor="shell", exit_code=126,
        ),
    )
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)

    result = _execute(agent.id, "pytest -q")

    assert set(result) == _RESPONSE_KEYS
    assert (result["ok"], result["kind"]) == (False, "shell_executor_deny")
    assert db.list_notifications(agent_id=agent.id, limit=10) == []
    assert recording.activities == [] and recording.chat == [] and recording.channel == []
