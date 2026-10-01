"""Microsoft 365 Mailbox — new unread mail wakes the agent (host ``SupportsWake``).

Each agent's watermark is a ``WakeState`` persisted at
``<data_dir>/wake/<agent_id>.json`` and written atomically (a temp file, then
``os.replace``, like ``ids.py``):

- ``since``: the newest ``receivedDateTime`` already handled;
- ``seen_at_since``: the Graph ids received at exactly ``since`` that were
  handled (Graph's ``ge`` precision is undocumented, so equal timestamps are
  de-duplicated here);
- ``mailbox``: the address it belongs to; a different configured address
  starts afresh.

What wakes: unread inbox messages received at or after ``since``, minus those
already seen, minus any sent from the mailbox itself (a loop guard). No file,
a changed mailbox, or a vacation skip records "start watching now", which
delivers nothing and so cannot lose mail. ``poll`` never advances the
watermark past a message it delivers; the host commits the returned cursor
only after the agent's trigger is persisted.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from core.extensions.contract import WakeBatch

from .graph import InboxPage, MessageSummary
from .ids import IdMap


class WakeStateError(Exception):
    """A state file or cursor is unreadable (not JSON, or the wrong shape)."""


class WakeNotConfigured(Exception):
    """The agent has no stored mailbox (its config was removed mid-check)."""


class WakeMailbox(Protocol):
    """What waking needs from ``graph.GraphMailbox`` (a fake in tests)."""

    mailbox: str

    def list_new_unread(self, since: datetime, top: int) -> InboxPage: ...


@dataclass(frozen=True)
class WakeState:
    """One agent's watermark (see the module docstring)."""

    mailbox: str
    since: datetime
    seen_at_since: tuple[str, ...]

    def to_json(self) -> str:
        """Serialise (the file content and the batch cursor are the same text)."""
        return json.dumps({
            "mailbox": self.mailbox,
            "since": self.since.astimezone(timezone.utc).isoformat(),
            "seen_at_since": list(self.seen_at_since),
        })

    @staticmethod
    def from_json(raw: str) -> "WakeState":
        """Parse ``to_json`` output.

        Raises:
            WakeStateError: Not JSON, the wrong shape, or a naive ``since``.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WakeStateError("the wake state is not JSON") from exc
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("mailbox"), str)
            or not isinstance(data.get("since"), str)
            or not isinstance(data.get("seen_at_since"), list)
            or not all(isinstance(item, str) for item in data["seen_at_since"])
        ):
            raise WakeStateError("the wake state is not {mailbox, since, seen_at_since}")
        try:
            since = datetime.fromisoformat(data["since"])
        except ValueError as exc:
            raise WakeStateError(f"the wake state has an unreadable since {data['since']!r}") from exc
        if since.tzinfo is None:
            raise WakeStateError("the wake state's since has no timezone")
        return WakeState(mailbox=data["mailbox"], since=since, seen_at_since=tuple(data["seen_at_since"]))


class WakeStore:
    """One agent's state file.

    Args:
        path: The JSON file (its folder is created on first write).
    """

    _locks: dict[Path, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: Path) -> None:
        self._path = path
        with WakeStore._locks_guard:
            self._lock = WakeStore._locks.setdefault(path, threading.Lock())

    def read(self) -> WakeState | None:
        """Return the stored state, or ``None`` when there is no file yet.

        Raises:
            WakeStateError: The file is unreadable or malformed.
        """
        with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise WakeStateError(f"cannot read {self._path.name}: {exc.strerror or exc}") from exc
        return WakeState.from_json(raw)

    def write(self, state: WakeState) -> None:
        """Replace the stored state atomically.

        Raises:
            OSError: The file cannot be written.
        """
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(state.to_json(), encoding="utf-8")
            os.replace(tmp, self._path)


class MailWake:
    """Poll, commit and skip for every agent of one extension instance.

    Args:
        mailbox_for: The agent's mailbox, or ``None`` when it has no stored config.
        id_map_for: The agent's short-id map (lines carry ids ``mail read`` accepts).
        store_for: The agent's state file.
        batch_max: Most messages one poll delivers; the rest follow next poll.
        preview_chars: Longest preview in a line.
        clock: "Now" (UTC); injectable for tests.
    """

    def __init__(
        self,
        *,
        mailbox_for: Callable[[str], WakeMailbox | None],
        id_map_for: Callable[[str], IdMap],
        store_for: Callable[[str], WakeStore],
        batch_max: int,
        preview_chars: int,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._mailbox_for = mailbox_for
        self._id_map_for = id_map_for
        self._store_for = store_for
        self._batch_max = batch_max
        self._preview_chars = preview_chars
        self._clock = clock

    def poll(self, agent_id: str) -> WakeBatch | None:
        """Return new mail since the committed watermark, or ``None``.

        The first poll (no state, or another mailbox) records "now" and
        returns ``None``. A page holding only seen or self-sent messages moves
        the watermark past them directly: nothing deliverable is skipped, and
        otherwise such messages would fill every page. Anything delivered is
        committed only through ``commit``.

        Raises:
            WakeNotConfigured: The agent has no stored mailbox.
            WakeStateError: The state file is unreadable.
            IdMapError: The id map is unreadable.
            GraphAuthError, GraphUnreachable, GraphHttpError: The inbox read failed.
        """
        mailbox = self._mailbox(agent_id)
        store = self._store_for(agent_id)
        state = store.read()
        if state is None or state.mailbox.lower() != mailbox.mailbox.lower():
            store.write(self._fresh(mailbox.mailbox))
            return None
        page = mailbox.list_new_unread(state.since, top=self._batch_max)
        if not page.messages:
            return None
        next_state = _advance(state, page.messages)
        seen = set(state.seen_at_since)
        own = mailbox.mailbox.lower()
        deliver = [
            message for message in page.messages
            if message.id not in seen and not (message.sender and message.sender.address.lower() == own)
        ]
        if not deliver:
            if next_state != state:
                store.write(next_state)
            return None
        shorts = self._id_map_for(agent_id).remember("inbox", (message.id for message in deliver))
        return WakeBatch(
            agent_id=agent_id,
            title=f"New email in {mailbox.mailbox}",
            lines=[self._line(short, message) for short, message in zip(shorts, deliver)],
            cursor=next_state.to_json(),
        )

    def commit(self, batch: WakeBatch) -> None:
        """Persist the batch's cursor as the agent's watermark.

        Raises:
            WakeStateError: The cursor is not one ``poll`` produced.
            OSError: The file cannot be written.
        """
        self._store_for(batch.agent_id).write(WakeState.from_json(batch.cursor))

    def skip(self, agent_id: str) -> None:
        """Record "start watching now" for the agent's mailbox (no network call).

        Raises:
            WakeNotConfigured: The agent has no stored mailbox.
            OSError: The file cannot be written.
        """
        self._store_for(agent_id).write(self._fresh(self._mailbox(agent_id).mailbox))

    def _mailbox(self, agent_id: str) -> WakeMailbox:
        mailbox = self._mailbox_for(agent_id)
        if mailbox is None:
            raise WakeNotConfigured(f"agent {agent_id} has no mailbox configured")
        return mailbox

    def _fresh(self, mailbox: str) -> WakeState:
        return WakeState(mailbox=mailbox, since=self._clock(), seen_at_since=())

    def _line(self, short: str, message: MessageSummary) -> str:
        sender = message.sender.display() if message.sender else "(no sender)"
        subject = message.subject or "(no subject)"
        preview = _clip(" ".join(message.preview.split()), self._preview_chars)
        return f'[{short}] {sender} — {subject} — "{preview}"'


def _advance(state: WakeState, messages: list[MessageSummary]) -> WakeState:
    """The watermark after handling ``messages`` (pure).

    ``since`` becomes the newest ``received``; the seen set is the ids at
    exactly that moment, kept from before when the moment did not move.
    """
    newest = max(message.received for message in messages)
    at_newest = {message.id for message in messages if message.received == newest}
    if newest == state.since:
        at_newest |= set(state.seen_at_since)
    return WakeState(mailbox=state.mailbox, since=newest, seen_at_since=tuple(sorted(at_newest)))


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
