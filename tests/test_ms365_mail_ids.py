"""Microsoft 365 Mailbox: the short-id map (ids.py) — folder-aware triples, cap, collisions."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_PACKAGE = import_package(get_discovery().get("ms365-mail"))
ids = importlib.import_module(f"{_PACKAGE.__name__}.ids")


def test_entries_round_trip_as_triples_and_resolve_to_a_ref(tmp_path: Path) -> None:
    path = tmp_path / "message_ids" / "a.json"
    first = ids.IdMap(path, keep=10)
    [inbox_short] = first.remember("inbox", ["G-IN"])
    [archive_short] = first.remember("archive", ["G-AR"])
    [sent_short] = first.remember("sentitems", ["G-SENT"])
    assert json.loads(path.read_text(encoding="utf-8")) == [
        [inbox_short, "inbox", "G-IN"], [archive_short, "archive", "G-AR"], [sent_short, "sentitems", "G-SENT"],
    ]
    again = ids.IdMap(path, keep=10)
    assert again.resolve(archive_short.upper()) == ids.MessageRef(folder="archive", graph_id="G-AR")
    assert again.resolve(sent_short) == ids.MessageRef(folder="sentitems", graph_id="G-SENT")


def test_re_listing_records_the_folder_it_was_listed_in_now(tmp_path: Path) -> None:
    id_map = ids.IdMap(tmp_path / "a.json", keep=10)
    [short] = id_map.remember("inbox", ["G1"])
    id_map.remember("archive", ["G1"])
    assert id_map.resolve(short).folder == "archive"


@pytest.mark.parametrize("content", [
    '[["m1", "drafts", "G1"]]',
    '[["m1", "G1"]]',
    '[["m1", "inbox", 3]]',
])
def test_a_bad_entry_in_the_file_is_an_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "a.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ids.IdMapError):
        ids.IdMap(path, keep=10).resolve("m1")


def test_remember_refuses_an_unknown_folder(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown folder"):
        ids.IdMap(tmp_path / "a.json", keep=10).remember("drafts", ["G1"])


def test_a_collision_raises_and_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "a.json"
    id_map = ids.IdMap(path, keep=10)
    monkeypatch.setattr(ids, "short_id", lambda graph_id: "mcollide")
    id_map.remember("inbox", ["G1"])
    id_map.remember("archive", ["G1"])  # the same Graph id again is not a collision
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ids.IdMapError, match="ID_COLLISION"):
        id_map.remember("inbox", ["G2"])
    assert path.read_text(encoding="utf-8") == before
    assert id_map.resolve("mcollide") == ids.MessageRef(folder="archive", graph_id="G1")


def test_the_id_map_is_capped_and_survives_a_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "message_ids" / "a.json"
    first = ids.IdMap(path, keep=3)
    shorts = first.remember("inbox", [f"G{n}" for n in range(5)])
    again = ids.IdMap(path, keep=3)
    assert again.resolve(shorts[4]).graph_id == "G4" and again.resolve(shorts[2]).graph_id == "G2"
    with pytest.raises(ids.UnknownMessageId):
        again.resolve(shorts[0])
    # Listing an old id again makes it the most recent.
    again.remember("inbox", ["G2"])
    again.remember("inbox", ["G5"])
    assert again.resolve(shorts[2]).graph_id == "G2"
    with pytest.raises(ids.UnknownMessageId):
        again.resolve(shorts[3])
    assert not list(path.parent.glob(".*.tmp"))


def test_short_ids_are_stable_hashes() -> None:
    assert ids.short_id("abc") == ids.short_id("abc")
    assert ids.short_id("abc") != ids.short_id("abd")
    assert len(ids.short_id("abc")) == 9 and ids.short_id("abc").startswith("m")
