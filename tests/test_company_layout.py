"""The startup move of the flat projects tree into Lobby's folder, and the DB backup.

Temp directories only: the company root and its ``projects`` sibling come
from ``tmp_path``, and the database is conftest's temp file. The fixture sets
the company root before ``init_db`` so the boot-time migration also runs
against the temp tree.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.floor_roots import company_root, floor_root
from db.company_layout import (
    CONFLICT_SUFFIX,
    MARKER_KEY,
    legacy_flat_projects_root,
    migrate_to_floor_layout,
    migration_backup_dir,
)
from db.connection import backup_database, database_path
from db.crud import query_one
from db.floors import LOBBY_ID


@pytest.fixture(autouse=True)
def _fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(tmp_path / "company"))
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


def _marker() -> str | None:
    row = query_one("SELECT value FROM settings WHERE key = $1", [MARKER_KEY])
    return None if row is None else str(row["value"])


def _clear_marker() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [MARKER_KEY])


def _legacy() -> Path:
    return legacy_flat_projects_root(company_root())


def test_the_old_root_is_the_company_roots_projects_sibling(tmp_path: Path) -> None:
    assert _legacy() == (tmp_path / "projects").resolve()


def test_a_boot_with_no_old_tree_sets_the_marker_and_takes_no_backup() -> None:
    assert _marker() == "1"
    before = set(migration_backup_dir().glob("*.bak"))
    _clear_marker()
    assert migrate_to_floor_layout() is True
    assert set(migration_backup_dir().glob("*.bak")) == before
    assert _marker() == "1"


def test_backup_is_a_consistent_copy(tmp_path: Path) -> None:
    db.set_setting("backup_probe", "kept", "general")
    target = backup_database(tmp_path / "backups")
    assert target.parent == tmp_path / "backups"
    assert target.name.startswith(f"{database_path().name}.") and target.name.endswith(".bak")
    assert not list((tmp_path / "backups").glob("*.partial"))
    copy = sqlite3.connect(target)
    try:
        row = copy.execute("SELECT value FROM settings WHERE key = 'backup_probe'").fetchone()
    finally:
        copy.close()
    assert row == ("kept",)


def test_backup_raises_when_the_database_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("db.connection._DB_PATH", str(tmp_path / "absent.sqlite3"))
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "backups")
    assert not (tmp_path / "absent.sqlite3").exists()


def test_backup_raises_instead_of_overwriting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Frozen:
        @staticmethod
        def now(_tz=None):
            from datetime import datetime, timezone

            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr("db.connection.datetime", _Frozen)
    first = backup_database(tmp_path / "backups")
    with pytest.raises(FileExistsError):
        backup_database(tmp_path / "backups")
    assert first.exists()


def test_migration_moves_entries_rewrites_paths_and_runs_once(caplog: pytest.LogCaptureFixture) -> None:
    legacy = _legacy()
    (legacy / "billing").mkdir(parents=True)
    (legacy / "billing" / "plan.md").write_text("plan\n", encoding="utf-8")
    (legacy / "notes.txt").write_text("loose\n", encoding="utf-8")
    old_plan = str((legacy / "billing" / "plan.md").resolve())
    agent = db.create_agent("Ada", role="Eng")
    db.upsert_artifact(
        agent_id=agent.id, task_id=None, virtual_path="/projects/billing/plan.md",
        absolute_path=old_plan, title="plan.md", kind="file", category="project",
        size_bytes=5, source_command=None,
    )
    consent = db.create_consent_request(
        agent_id=agent.id, path=old_plan, grant_root=str((legacy / "billing").resolve()),
        reason="read the plan",
    )
    db.create_once_grant(agent_id=agent.id, root=str((legacy / "billing").resolve()), consent_id=consent.id)
    _clear_marker()
    # The backup dir sits beside conftest's shared temp database.
    backups_before = set(migration_backup_dir().glob("*.bak"))

    assert migrate_to_floor_layout() is True

    lobby = floor_root(LOBBY_ID)
    new_plan = str((lobby / "billing" / "plan.md").resolve())
    assert (lobby / "billing" / "plan.md").read_text(encoding="utf-8") == "plan\n"
    assert (lobby / "notes.txt").read_text(encoding="utf-8") == "loose\n"
    assert legacy.is_dir() and list(legacy.iterdir()) == []
    assert db.get_artifact_by_absolute_path(new_plan) is not None
    assert db.get_artifact_by_absolute_path(old_plan) is None
    moved = db.get_consent_request(consent.id)
    assert moved.path == new_plan
    assert moved.grant_root == str((lobby / "billing").resolve())
    assert db.list_once_grant_roots(agent.id) == [str((lobby / "billing").resolve())]
    assert _marker() == "1"
    assert len(set(migration_backup_dir().glob("*.bak")) - backups_before) == 1

    # A second run is a no-op, even with something new in the old root.
    (legacy / "late").mkdir()
    assert migrate_to_floor_layout() is True
    assert (legacy / "late").is_dir()
    assert not (lobby / "late").exists()


def test_a_name_taken_in_lobby_keeps_both(caplog: pytest.LogCaptureFixture) -> None:
    legacy = _legacy()
    (legacy / "billing").mkdir(parents=True)
    (legacy / "billing" / "old.md").write_text("from the flat root\n", encoding="utf-8")
    lobby = floor_root(LOBBY_ID)
    (lobby / "billing").mkdir()
    (lobby / "billing" / "new.md").write_text("already in lobby\n", encoding="utf-8")
    _clear_marker()

    with caplog.at_level(logging.WARNING, logger="db.company_layout"):
        assert migrate_to_floor_layout() is True

    assert (lobby / "billing" / "new.md").read_text(encoding="utf-8") == "already in lobby\n"
    kept = lobby / f"billing{CONFLICT_SUFFIX}"
    assert (kept / "old.md").read_text(encoding="utf-8") == "from the flat root\n"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any(str(lobby / "billing") in record.getMessage() and str(kept) in record.getMessage() for record in warnings)


def test_a_failed_backup_moves_nothing_and_leaves_the_marker_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    legacy = _legacy()
    company = company_root()
    (legacy / "billing").mkdir(parents=True)
    _clear_marker()

    def _fail(_dest: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr("db.company_layout.backup_database", _fail)
    with caplog.at_level(logging.ERROR, logger="db.company_layout"):
        assert migrate_to_floor_layout() is False

    assert (legacy / "billing").is_dir()
    assert not (floor_root(LOBBY_ID) / "billing").exists()
    assert _marker() is None
    assert any(record.levelno == logging.ERROR for record in caplog.records)

    # The next boot retries and succeeds.
    monkeypatch.undo()
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(company))
    assert migrate_to_floor_layout() is True
    assert (floor_root(LOBBY_ID) / "billing").is_dir()
    assert _marker() == "1"
