import atexit
import os
import shutil
import tempfile

import pytest


def _ensure_test_db_path() -> None:
    # Avoid touching the repo-root dev DB (`bossmod.sqlite3`) during pytest runs.
    # db/connection.py reads BOSSMOD_DB_PATH at import time, and pytest loads conftest
    # before importing test modules, so this is an effective safety rail.
    if not os.environ.get("BOSSMOD_DB_PATH"):
        root = tempfile.mkdtemp(prefix="bossmodai-test-db-")
        os.environ["BOSSMOD_DB_PATH"] = os.path.join(root, "bossmod-test.sqlite3")
        atexit.register(shutil.rmtree, root, ignore_errors=True)
    # Project workspaces must stay outside the application checkout. Point tests
    # at a temp data root before filesystem.py reads BOSSMOD_PROJECTS_ROOT.
    if not os.environ.get("BOSSMOD_PROJECTS_ROOT"):
        projects = tempfile.mkdtemp(prefix="bossmodai-test-projects-")
        os.environ["BOSSMOD_PROJECTS_ROOT"] = projects
        atexit.register(shutil.rmtree, projects, ignore_errors=True)


_ensure_test_db_path()


@pytest.fixture(autouse=True)
def _reset_model_call_budget():
    """Keep one test's model-call lanes from filling the next test's knob."""
    from core.llm.call_budget import budget

    budget.reset()
    yield
    budget.reset()

