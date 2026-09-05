"""HA-STRUCT-P1-05 — Settings preview peeled out of live context_builder."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.llm import context_builder, context_preview


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


def test_preview_helpers_live_outside_live_builder() -> None:
    builder_source = open(context_builder.__file__, encoding="utf-8").read()
    preview_source = open(context_preview.__file__, encoding="utf-8").read()
    assert "def preview_runtime_contract" not in builder_source
    assert "def preview_prompt_bundle" not in builder_source
    assert "def _preview_trigger" not in builder_source
    assert "def preview_runtime_contract" in preview_source
    assert "def preview_prompt_bundle" in preview_source
    assert context_preview.preview_runtime_contract.__module__ == "core.llm.context_preview"
    assert context_builder.preview_runtime_contract is context_preview.preview_runtime_contract
    assert context_builder.preview_prompt_bundle is context_preview.preview_prompt_bundle


def test_preview_runtime_contract_renders_without_llm() -> None:
    rendered = context_builder.preview_runtime_contract("decision", "human_chat")
    assert isinstance(rendered, str)
    assert rendered.strip()
    assert "Taylor" in rendered or "human" in rendered.lower() or "decision" in rendered.lower()


def test_preview_prompt_bundle_includes_roles() -> None:
    preview = context_preview.preview_prompt_bundle("execution", "activity_resumed")
    assert "messages" in preview
    assert "rendered" in preview
    roles = {str(message.get("role")) for message in preview["messages"]}
    assert "system" in roles
    assert "user" in roles
    assert "[SYSTEM" in preview["rendered"]


def test_live_builder_stays_larger_than_preview_but_preview_is_focused() -> None:
    builder_lines = open(context_builder.__file__, encoding="utf-8").read().count("\n")
    preview_lines = open(context_preview.__file__, encoding="utf-8").read().count("\n")
    assert preview_lines < 350
    assert builder_lines < 1100
    assert "Settings contract/prompt preview lives in context_preview" in open(
        context_builder.__file__, encoding="utf-8"
    ).read()
