"""HA-STRUCT-P1-05 — managed_writer package peel keeps public APIs."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.bm_cli import managed_writer
from core.bm_cli.managed_writer import (
    ManagedWriteOutcome,
    ManagedWriteProgress,
    is_managed_batch_write_request,
    is_managed_section_rewrite_request,
    is_managed_write_request,
    run_managed_batch_write,
    run_managed_section_rewrite,
    run_managed_write,
)
from core.bm_cli.managed_writer.batch import _parse_batch_write_manifest
from core.bm_cli.managed_writer.generate import _parse_section_plan


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


def test_public_exports_still_import_from_managed_writer() -> None:
    assert callable(run_managed_write)
    assert callable(run_managed_batch_write)
    assert callable(run_managed_section_rewrite)
    assert run_managed_write is managed_writer.run_managed_write
    assert is_managed_write_request is managed_writer.is_managed_write_request
    assert ManagedWriteOutcome is managed_writer.ManagedWriteOutcome
    assert ManagedWriteProgress is managed_writer.ManagedWriteProgress


def test_entrypoints_live_in_focused_modules() -> None:
    assert run_managed_write.__module__ == "core.bm_cli.managed_writer.write"
    assert run_managed_batch_write.__module__ == "core.bm_cli.managed_writer.batch"
    assert run_managed_section_rewrite.__module__ == "core.bm_cli.managed_writer.section"
    assert is_managed_write_request.__module__ == "core.bm_cli.managed_writer.detect"

    barrel = open(managed_writer.__file__, encoding="utf-8").read()
    assert "async def run_managed_write" not in barrel
    assert "async def run_managed_batch_write" not in barrel
    assert "async def run_managed_section_rewrite" not in barrel
    assert "def is_managed_write_request" not in barrel


def test_detect_managed_requests_without_llm() -> None:
    assert is_managed_write_request("write /me/notes.md", None) is True
    assert is_managed_write_request("write /me/notes.md", "") is True
    assert is_managed_write_request("write /me/notes.md", "body") is False
    assert is_managed_write_request("write /me/a.md /me/b.md", None) is False
    assert is_managed_batch_write_request("bwrite", "a.md :: goal") is True
    assert is_managed_batch_write_request("bwrite", None) is False
    assert is_managed_batch_write_request("bwrite /me/a.md", "a.md :: goal") is False
    assert is_managed_section_rewrite_request('rewsect /me/doc.md "Intro"', "tighten") is True
    assert is_managed_section_rewrite_request('rewsect /me/doc.md "Intro"', None) is False


def test_batch_manifest_and_section_plan_parsers() -> None:
    files = _parse_batch_write_manifest("/me/a.md :: write notes\n/me/b.md :: write outline")
    assert [item.path for item in files] == ["/me/a.md", "/me/b.md"]
    assert files[0].goal == "write notes"

    json_files = _parse_batch_write_manifest(
        '{"files":[{"path":"/me/c.md","goal":"summarize"}]}'
    )
    assert json_files[0].path == "/me/c.md"

    sections = _parse_section_plan('{"sections":[{"heading":"Intro","goal":"say hi"}]}')
    assert sections[0].heading == "Intro"
    assert sections[0].goal == "say hi"


def test_package_modules_stay_focused() -> None:
    from core.bm_cli.managed_writer import batch, generate, helpers, section, write

    limits = {
        write.__file__: 150,
        batch.__file__: 500,
        section.__file__: 450,
        generate.__file__: 550,
        helpers.__file__: 280,
        managed_writer.__file__: 50,
    }
    for path, limit in limits.items():
        lines = open(path, encoding="utf-8").read().count("\n")
        assert lines < limit, f"{path} has {lines} lines (limit {limit})"
