"""The Metrics dashboard counts a half-open period, not "UTC today" or forever.

``db.get_dashboard_metrics(start, end)`` filters every event-timestamped figure
on ``[start, end)``. Timestamps are stored as UTC text in two shapes — the
datetime adapter's 32-character ``…HH:MM:SS.ffffff+00:00`` and SQL
``current_timestamp``'s 19-character ``…HH:MM:SS`` — so every boundary case
below is seeded in both: a row on ``start`` is in, a row on ``end`` is out, in
either shape. ``GET /api/metrics/dashboard`` takes the pair as required query
parameters.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config

# Midnight at UTC-5 is 05:00 UTC: the bounds arrive in the operator's offset
# and must be compared in UTC.
OPERATOR_TZ = timezone(timedelta(hours=-5))
START = datetime(2001, 3, 10, tzinfo=OPERATOR_TZ)
END = datetime(2001, 3, 11, tzinfo=OPERATOR_TZ)

INSIDE = (
    "2001-03-10 05:00:00",  # exactly start, 19-char
    "2001-03-10 05:00:00.000000+00:00",  # exactly start, 32-char
    "2001-03-10 12:00:00",
    "2001-03-11 04:59:59.999999+00:00",  # the last microsecond in
)
OUTSIDE = (
    "2001-03-10 04:59:59",
    "2001-03-10 04:59:59.999999+00:00",
    "2001-03-11 05:00:00",  # exactly end, 19-char
    "2001-03-11 05:00:00.000000+00:00",  # exactly end, 32-char
)


@pytest.fixture(autouse=True)
def _fresh_db() -> None:
    """A fresh schema in conftest's temp DB, never the live file."""
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    yield
    db.close_connection()


def _diagnostic(agent_id: str, created_at: str, tokens: tuple[int, int], status: str = "success",
                action_name: str | None = None) -> None:
    db.execute(
        """
        INSERT INTO diagnostics (agent_id, agent_name, trigger_type, trigger_data, status,
                                 action_name, prompt_tokens, completion_tokens, created_at)
        VALUES (?, ?, 'test', '{}', ?, ?, ?, ?, ?)
        """,
        [agent_id, agent_id.upper(), status, action_name, tokens[0], tokens[1], created_at],
    )


def _seed() -> None:
    """Rows on both sides of the bounds, in both stored shapes, in every table."""
    cli_agent = db.create_agent("Range Tester", role="Eng")
    for index, stamp in enumerate(INSIDE):
        # Two inside errors: one a rejected decision (has an action), one not.
        status = "error" if index < 2 else "success"
        _diagnostic("a1" if index < 3 else "a2", stamp, (100, 1), status,
                    "speak" if index == 0 else None)
    for stamp in OUTSIDE:
        _diagnostic("a3", stamp, (1000, 10), "error", "speak")

    # Completion is closed_at. Each inside closure is paired with an outside
    # last_activity, and each outside closure with an inside one, so a query
    # on the wrong column counts the wrong four.
    for inside, outside in zip(INSIDE, OUTSIDE):
        for closed, touched in ((inside, outside), (outside, inside)):
            db.execute(
                "INSERT INTO tasks (title, status, closed_at, last_activity) VALUES ('t', 'complete', ?, ?)",
                [closed, touched],
            )

    for stamp in INSIDE:
        db.execute("INSERT INTO messages (from_agent, to_agent, content, created_at) VALUES ('x', 'y', 'hi', ?)",
                   [stamp])
        db.execute("INSERT INTO meeting_sessions (room_id, title, status, created_at) VALUES ('r', 'm', 'ended', ?)",
                   [stamp])
    for stamp in OUTSIDE:
        db.execute("INSERT INTO messages (from_agent, to_agent, content, created_at) VALUES ('x', 'z', 'hi', ?)",
                   [stamp])
        db.execute("INSERT INTO meeting_sessions (room_id, title, status, created_at) VALUES ('r', 'm', 'ended', ?)",
                   [stamp])
    for stamp in INSIDE + OUTSIDE:
        db.execute(
            """
            INSERT INTO bm_cli_events (agent_id, command, executor, policy_tier, decision, created_at)
            VALUES (?, 'ls', 'test', 'read', 'allowed', ?)
            """,
            [cli_agent.id, stamp],
        )


def test_every_period_figure_is_half_open_in_both_stored_shapes() -> None:
    before = db.get_dashboard_metrics(START, END)
    _seed()
    metrics = db.get_dashboard_metrics(START, END)

    tasks = metrics["tasks"]
    assert tasks["completed"] == before["tasks"]["completed"] + 4
    assert tasks["completed_all_time"] == before["tasks"]["completed_all_time"] + 8
    assert "completed_today" not in tasks

    tokens = metrics["tokens"]
    assert tokens["total"] == before["tokens"]["total"] + 4 * 101
    assert tokens["all_time"] == before["tokens"]["all_time"] + 4 * 101 + 4 * 1010
    assert "today" not in tokens
    by_agent = {row["agent_id"]: row for row in tokens["by_agent"]}
    assert by_agent["a1"]["total_tokens"] == 303 and by_agent["a1"]["api_calls"] == 3
    assert by_agent["a2"]["total_tokens"] == 101 and by_agent["a2"]["api_calls"] == 1
    assert "a3" not in by_agent

    errors = metrics["errors"]
    assert errors == {"total": 2, "rate": 0.5, "invalid_decisions": 1}

    comms = metrics["communication"]
    assert comms["messages_sent"] == 4
    assert comms["agent_conversations"] == 1
    assert comms["meetings_held"] == 4
    assert metrics["tool_calls"] == {"total": 4}
    assert "range" not in metrics


def test_back_to_back_periods_count_a_boundary_row_once() -> None:
    _seed()
    previous = db.get_dashboard_metrics(START - timedelta(days=1), START)
    current = db.get_dashboard_metrics(START, END)
    following = db.get_dashboard_metrics(END, END + timedelta(days=1))
    # Two OUTSIDE rows sit just before START, two exactly on END.
    assert previous["tool_calls"]["total"] == 2
    assert current["tool_calls"]["total"] == 4
    assert following["tool_calls"]["total"] == 2


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (START.replace(tzinfo=None), END),
        (START, END.replace(tzinfo=None)),
        (END, START),
        (START, START),
    ],
)
def test_naive_or_empty_periods_are_refused(start: datetime, end: datetime) -> None:
    with pytest.raises(ValueError):
        db.get_dashboard_metrics(start, end)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _get(params: dict[str, str]):
    return _client().get(
        "/api/metrics/dashboard",
        params=params,
        headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()},
    )


def test_route_requires_a_valid_period() -> None:
    _seed()
    assert _get({}).status_code == 422
    assert _get({"start": START.isoformat()}).status_code == 422
    assert _get({"start": "yesterday", "end": END.isoformat()}).status_code == 422

    backwards = _get({"start": END.isoformat(), "end": START.isoformat()})
    assert backwards.status_code == 400
    assert backwards.json()["detail"] == "start must be before end"
    naive = _get({"start": "2001-03-10T00:00:00", "end": END.isoformat()})
    assert naive.status_code == 400
    assert naive.json()["detail"] == "start must be a timezone-aware datetime"

    # The shape the UI sends: UTC instants from Date.toISOString().
    ok = _get({"start": "2001-03-10T05:00:00.000Z", "end": "2001-03-11T05:00:00.000Z"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["tool_calls"] == {"total": 4}
    assert body["tasks"]["completed"] == 4
