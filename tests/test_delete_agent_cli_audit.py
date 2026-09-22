"""Deleting an agent must not trip the CLI audit foreign key."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config


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


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _audit_event(agent_id: str, approval_request_id: str) -> None:
    db.create_bm_cli_event(
        agent_id=agent_id,
        command="bash echo hi",
        content_present=False,
        executor="shell",
        cwd_before="/",
        cwd_after="/",
        policy_tier="approval",
        decision="approval_required",
        exit_code=0,
        result_kind="approval",
        stdout_preview=None,
        stderr_preview=None,
        changed_paths=None,
        trigger_type="activity_resumed",
        approval_request_id=approval_request_id,
    )


def test_delete_agent_with_linked_cli_audit_succeeds() -> None:
    keeper = db.create_agent("Keeper", role="Eng", desk_x=1, desk_y=1)
    agent = db.create_agent("Ada", role="Eng", desk_x=3, desk_y=1)
    approval = db.create_cli_approval_request(agent_id=agent.id, command="bash echo hi")
    keeper_approval = db.create_cli_approval_request(
        agent_id=keeper.id, command="bash echo keep",
    )
    _audit_event(agent.id, approval.id)
    _audit_event(keeper.id, keeper_approval.id)

    response = _api_client().delete(f"/api/agents/{agent.id}", headers=_headers())

    assert response.status_code == 204
    assert db.get_agent(agent.id) is None
    assert db.get_cli_approval_request(approval.id) is None
    assert db.list_bm_cli_events(agent_id=agent.id) == []
    assert db.get_agent(keeper.id) is not None
    assert db.get_cli_approval_request(keeper_approval.id) is not None
    kept = db.list_bm_cli_events(agent_id=keeper.id)
    assert len(kept) == 1
    assert kept[0]["approval_request_id"] == keeper_approval.id
