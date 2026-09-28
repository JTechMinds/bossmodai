"""Browser Vision screenshot store: sidecars, pruning, latest, agents."""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_PACKAGE = import_package(get_discovery().get("browser-vision"))
shots = importlib.import_module(f"{_PACKAGE.__name__}.screenshots")


def _meta(command: str = "bv open example.com", view: str = "view: full page"):
    return shots.ShotMeta(
        command=command,
        url="https://example.com",
        title="Example",
        window="desktop 1280x800",
        view=view,
        image="1280x800",
        marks=5,
        taken_at="2026-09-28T12:00:00+00:00",
    )


def test_store_writes_a_sidecar_with_every_field(tmp_path: Path) -> None:
    store = shots.ScreenshotStore(tmp_path, keep=5)
    zoomed = "view: zoom 5 — region 427×267 px at (427, 267)"
    path = store.store("agent-1", b"png-bytes", _meta(view=zoomed))
    assert path.read_bytes() == b"png-bytes"
    sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar == asdict(_meta(view=zoomed))
    assert set(sidecar) == {"command", "url", "title", "window", "view", "image", "marks", "taken_at"}


def test_prune_removes_png_and_json_together(tmp_path: Path) -> None:
    store = shots.ScreenshotStore(tmp_path, keep=2)
    paths = [store.store("agent-1", b"x", _meta(f"bv view {i}")) for i in range(4)]
    folder = tmp_path / "agent-1"
    assert sorted(p.name for p in folder.glob("*.png")) == sorted(p.name for p in paths[2:])
    assert sorted(p.name for p in folder.glob("*.json")) == sorted(p.with_suffix(".json").name for p in paths[2:])


def test_latest_skips_a_png_without_a_readable_sidecar_and_warns(tmp_path: Path, caplog) -> None:
    store = shots.ScreenshotStore(tmp_path, keep=5)
    older = store.store("agent-1", b"old", _meta("bv view older"))
    newest = store.store("agent-1", b"new", _meta("bv view newest"))
    newest.with_suffix(".json").unlink()

    with caplog.at_level(logging.WARNING):
        found = store.latest("agent-1")

    assert found is not None
    assert found[0] == older and found[1].command == "bv view older"
    assert "sidecar is unreadable" in caplog.text

    older.with_suffix(".json").write_text("{not json", encoding="utf-8")
    assert store.latest("agent-1") is None
    assert store.latest("nobody") is None


def test_agents_lists_agent_folders(tmp_path: Path) -> None:
    store = shots.ScreenshotStore(tmp_path / "shots", keep=5)
    assert store.agents() == []
    store.store("b-agent", b"x", _meta())
    store.store("a-agent", b"x", _meta())
    assert store.agents() == ["a-agent", "b-agent"]
