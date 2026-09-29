"""Microsoft 365 Mailbox — short, stable message ids (plan D6).

Graph ids are ~150 characters. An agent sees ``m`` plus the first 8 hex
characters of ``sha256(graph_id)``, so the same message has the same short id
in every turn, after a worker restart and in the operator's viewer. The map
back is persisted per agent, capped at the most recent ``keep`` entries, and
written atomically (a temp file, then ``os.replace``).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Iterable


class UnknownMessageId(Exception):
    """A short id is not in the agent's map (never listed, or aged out)."""


class IdMapError(Exception):
    """The map file is unreadable, or two Graph ids share a short id."""


def short_id(graph_id: str) -> str:
    """Return the short id of a Graph message id (pure)."""
    return "m" + hashlib.sha256(graph_id.encode("utf-8")).hexdigest()[:8]


class IdMap:
    """One agent's short id → Graph id map, most recently listed last.

    Args:
        path: The JSON file (its folder is created on first write).
        keep: How many entries to keep (the oldest go first).

    The file is re-read on every call: the app process (operator viewer) and
    the runtime worker (agent) share it.
    """

    _locks: dict[Path, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: Path, keep: int) -> None:
        if keep < 1:
            raise ValueError("keep must be at least 1")
        self._path = path
        self._keep = keep
        with IdMap._locks_guard:
            self._lock = IdMap._locks.setdefault(path, threading.Lock())

    def remember(self, graph_ids: Iterable[str]) -> list[str]:
        """Record Graph ids as the most recent and return their short ids, in order.

        Raises:
            IdMapError: The file is unreadable, or a short id already names a
                different Graph id (a hash collision; nothing is written).
            OSError: The file cannot be written.
        """
        ids = list(graph_ids)
        with self._lock:
            entries = self._read()
            shorts: list[str] = []
            for graph_id in ids:
                short = short_id(graph_id)
                existing = entries.pop(short, None)
                if existing is not None and existing != graph_id:
                    raise IdMapError(f"ID_COLLISION: {short} already names another message")
                entries[short] = graph_id
                shorts.append(short)
            while len(entries) > self._keep:
                entries.pop(next(iter(entries)))
            self._write(entries)
            return shorts

    def resolve(self, short: str) -> str:
        """Return the Graph id for a short id.

        Raises:
            UnknownMessageId: The id is not in the map.
            IdMapError: The file is unreadable.
        """
        with self._lock:
            graph_id = self._read().get(short.strip().lower())
        if graph_id is None:
            raise UnknownMessageId(short)
        return graph_id

    def _read(self) -> dict[str, str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise IdMapError(f"cannot read {self._path.name}: {exc.strerror or exc}") from exc
        try:
            pairs = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IdMapError(f"{self._path.name} is not JSON") from exc
        if not isinstance(pairs, list) or not all(
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(part, str) for part in pair)
            for pair in pairs
        ):
            raise IdMapError(f"{self._path.name} is not a list of [short, id] pairs")
        # dicts keep insertion order: oldest first, as written.
        return dict(pairs)

    def _write(self, entries: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps([[short, graph_id] for short, graph_id in entries.items()]), encoding="utf-8")
        os.replace(tmp, self._path)
