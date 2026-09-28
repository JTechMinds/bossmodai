"""Browser Vision screenshot store (sidecars, pruning, latest, per-session folders) and session markers."""

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
sessions = importlib.import_module(f"{_PACKAGE.__name__}.sessions")


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
    path = store.store("agent-1", "s1", b"png-bytes", _meta(view=zoomed))
    assert path.parent == tmp_path / "agent-1" / "s1"
    assert path.read_bytes() == b"png-bytes"
    sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar == asdict(_meta(view=zoomed))
    assert set(sidecar) == {"command", "url", "title", "window", "view", "image", "marks", "taken_at"}


def test_prune_removes_png_and_json_together(tmp_path: Path) -> None:
    store = shots.ScreenshotStore(tmp_path, keep=2)
    paths = [store.store("agent-1", "s1", b"x", _meta(f"bv view {i}")) for i in range(4)]
    folder = tmp_path / "agent-1" / "s1"
    assert sorted(p.name for p in folder.glob("*.png")) == sorted(p.name for p in paths[2:])
    assert sorted(p.name for p in folder.glob("*.json")) == sorted(p.with_suffix(".json").name for p in paths[2:])


def test_latest_skips_a_png_without_a_readable_sidecar_and_warns(tmp_path: Path, caplog) -> None:
    store = shots.ScreenshotStore(tmp_path, keep=5)
    older = store.store("agent-1", "s1", b"old", _meta("bv view older"))
    newest = store.store("agent-1", "s1", b"new", _meta("bv view newest"))
    newest.with_suffix(".json").unlink()

    with caplog.at_level(logging.WARNING):
        found = store.latest("agent-1", "s1")

    assert found is not None
    assert found[0] == older and found[1].command == "bv view older"
    assert "sidecar is unreadable" in caplog.text

    older.with_suffix(".json").write_text("{not json", encoding="utf-8")
    assert store.latest("agent-1", "s1") is None
    assert store.latest("nobody", "s1") is None


def test_each_session_has_its_own_folder_and_latest(tmp_path: Path) -> None:
    store = shots.ScreenshotStore(tmp_path, keep=5)
    old = store.store("agent-1", "s1", b"old", _meta("bv view old"))
    new = store.store("agent-1", "s2", b"new", _meta("bv view new"))
    assert old.parent == tmp_path / "agent-1" / "s1" and new.parent == tmp_path / "agent-1" / "s2"
    assert store.latest("agent-1", "s1")[0] == old
    assert store.latest("agent-1", "s2")[0] == new


def test_delete_session_removes_its_folder_and_an_emptied_agent_folder(tmp_path: Path) -> None:
    store = shots.ScreenshotStore(tmp_path, keep=5)
    store.store("agent-1", "s1", b"x", _meta())
    store.store("agent-1", "s2", b"x", _meta())
    store.delete_session("agent-1", "s1")
    assert not (tmp_path / "agent-1" / "s1").exists() and (tmp_path / "agent-1" / "s2").is_dir()
    store.delete_session("agent-1", "s2")
    assert not (tmp_path / "agent-1").exists()
    store.delete_session("agent-1", "s2")  # already gone: nothing to do


def test_clear_removes_every_agents_sessions(tmp_path: Path) -> None:
    store = shots.ScreenshotStore(tmp_path / "shots", keep=5)
    store.clear()  # no folder yet
    store.store("a-agent", "s1", b"x", _meta())
    store.store("b-agent", "s2", b"x", _meta())
    store.clear()
    assert list((tmp_path / "shots").iterdir()) == []


def _marker(pid: int = 4242, url: str = "https://example.com") -> "sessions.SessionMarker":
    return sessions.SessionMarker(session_id="s1", pid=pid, opened_at="2026-09-28T12:00:00+00:00", url=url)


def test_markers_round_trip_list_and_remove(tmp_path: Path) -> None:
    markers = sessions.SessionMarkers(tmp_path / "sessions")
    assert markers.agents() == [] and markers.read("agent-1") is None
    markers.write("agent-1", _marker())
    markers.write("agent-1", _marker(url="https://example.com/next"))
    assert markers.read("agent-1") == _marker(url="https://example.com/next")
    assert json.loads(markers.path("agent-1").read_text(encoding="utf-8")) == {
        "session_id": "s1", "pid": 4242, "opened_at": "2026-09-28T12:00:00+00:00", "url": "https://example.com/next",
    }
    assert markers.agents() == ["agent-1"]
    assert [p.name for p in (tmp_path / "sessions").iterdir()] == ["agent-1.json"]  # no partial file left
    markers.remove("agent-1")
    assert markers.agents() == [] and markers.read("agent-1") is None
    markers.remove("agent-1")


def test_a_malformed_marker_raises_marker_error(tmp_path: Path) -> None:
    import pytest

    markers = sessions.SessionMarkers(tmp_path)
    markers.path("agent-1").write_text("{not json", encoding="utf-8")
    with pytest.raises(sessions.SessionMarkerError):
        markers.read("agent-1")
    markers.path("agent-1").write_text(json.dumps({**_marker().model_dump(), "pid": 0}), encoding="utf-8")
    with pytest.raises(sessions.SessionMarkerError):
        markers.read("agent-1")


def test_pid_alive_tells_a_running_process_from_a_finished_one() -> None:
    import os
    import subprocess
    import sys

    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert sessions.pid_alive(os.getpid()) is True
    assert sessions.pid_alive(finished.pid) is False
