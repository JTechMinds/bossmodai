import atexit
import os
import shutil
import tempfile


def _ensure_test_db_path() -> None:
    # Avoid touching the repo-root dev DB (`bossmod.sqlite3`) during pytest runs.
    # db/connection.py reads BOSSMOD_DB_PATH at import time, and pytest loads conftest
    # before importing test modules, so this is an effective safety rail.
    if os.environ.get("BOSSMOD_DB_PATH"):
        return
    root = tempfile.mkdtemp(prefix="bossmodai-test-db-")
    os.environ["BOSSMOD_DB_PATH"] = os.path.join(root, "bossmod-test.sqlite3")
    atexit.register(shutil.rmtree, root, ignore_errors=True)


_ensure_test_db_path()

