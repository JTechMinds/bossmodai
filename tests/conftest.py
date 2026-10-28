import atexit
import os
import shutil
import tempfile

import pytest


def _ensure_test_db_path() -> None:
    # Avoid touching the repo-root dev DB (`bossmod.sqlite3`) during pytest runs.
    # db/connection.py reads BOSSMOD_DB_PATH at import time, and pytest loads conftest
    # before importing test modules, so this is an effective safety rail. Always a
    # fresh temp file, even when the shell exports one: the data key and the
    # migration backups live beside it.
    root = tempfile.mkdtemp(prefix="bossmodai-test-db-")
    os.environ["BOSSMOD_DB_PATH"] = os.path.join(root, "bossmod-test.sqlite3")
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    # Floor folders live under the company root, outside the application
    # checkout. Always a fresh temp root, even when the shell exports one:
    # a test run must never create, move or archive folders in real company
    # data. floor_roots.py reads BOSSMOD_COMPANY_ROOT on every call.
    # The company root sits inside its own temp data dir because the startup
    # layout migration reads the flat projects tree from the company root's
    # ``projects`` sibling: here that is <temp>/projects, never real data.
    data = tempfile.mkdtemp(prefix="bossmodai-test-data-")
    os.environ["BOSSMOD_COMPANY_ROOT"] = os.path.join(data, "company")
    # The whole artifacts tree (per-agent /me folders, reset backups) too:
    # filesystem.py reads BOSSMOD_ARTIFACTS_ROOT once, at import, and nothing
    # has imported it yet. Without this, a test's agent_0001 is the running
    # installation's agent_0001, and a test that cleans up deletes it.
    os.environ["BOSSMOD_ARTIFACTS_ROOT"] = os.path.join(data, "artifacts")
    atexit.register(shutil.rmtree, data, ignore_errors=True)


def _refuse_real_data() -> None:
    import sys

    # Must hold before the first test: a real root makes the run destructive.
    assert "core.bm_cli.filesystem" not in sys.modules, (
        "core.bm_cli.filesystem was imported before conftest set BOSSMOD_ARTIFACTS_ROOT"
    )
    from tests._real_data_guard import assert_no_real_data

    assert_no_real_data()


_ensure_test_db_path()
_refuse_real_data()


@pytest.fixture(autouse=True)
def _reset_model_call_budget():
    """Keep one test's model-call lanes from filling the next test's knob."""
    from core.llm.call_budget import budget

    budget.reset()
    yield
    budget.reset()


def _clear_connection_health() -> None:
    """Delete every AI connection health row, when this worker's schema has the table.

    A test that never ran ``init_db`` has no table yet; that is checked, not
    caught, so a real database error still fails the test.
    """
    import db

    present = db.query_one(
        "SELECT 1 AS present FROM sqlite_master WHERE type = 'table' AND name = 'ai_connection_health'"
    )
    if present is not None:
        db.execute("DELETE FROM ai_connection_health")


@pytest.fixture(autouse=True)
def _reset_connection_health():
    """Keep one test's paused AI connection from holding the next test's work.

    The test database is shared per worker (``_ensure_test_db_path``), so a
    pause recorded by one test would otherwise deny the next test's calls.
    """
    _clear_connection_health()
    yield
    _clear_connection_health()

