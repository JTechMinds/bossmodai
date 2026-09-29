"""BossMod AI — wake agents on extension events (manifest ``wake``).

A runtime-worker singleton shaped like ``ChannelIdleWatch``: started and
stopped with the other services, so Pause stops it. Every tick it asks each
enabled ``wake`` extension, per configured agent whose own interval (the
manifest's ``wake.interval_field``) has come due, for new events, and wakes
the agent with one ``extension_event`` trigger:

- two-phase: ``poll_wake`` → the trigger is persisted → ``commit_wake``, so a
  failed or skipped enqueue never loses events;
- coalesced: new lines merge into the agent's still-queued event from the
  same extension instead of stacking a second wake; a claimed one is never
  touched;
- vacation: a vacationer's due tick calls ``skip_wake`` instead, so what
  arrives meanwhile never wakes it;
- health: every real check is recorded (``db.record_wake_check``) for the
  desk; a failure is logged at warning only when its sentence changes, and
  at info when it clears.

The extension knows only its data source; scheduling, vacation and the
trigger stay here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Any, Callable

import db
from core import config
from core.agent_loop.dispatcher import dispatcher
from core.extensions.contract import SupportsWake, WakeBatch
from core.extensions.loader import ExtensionLoadError, load_extension
from core.extensions.manifest import agent_config_value
from core.extensions.registry import ExtensionEntry, enabled_ids, get_discovery
from core.floors import is_on_vacation

logger = logging.getLogger(__name__)

TRIGGER_TYPE = "extension_event"
# Keep equal to _SEED_SETTINGS in db/settings.py.
WAKE_TICK_SECONDS_FALLBACK = 5.0


def wake_tick_seconds() -> float:
    """Return how often the service looks for due pairs. Missing or non-positive uses the seed (5)."""
    value = config.get_float("extension_wake_tick_seconds")
    if value is None or value <= 0:
        return WAKE_TICK_SECONDS_FALLBACK
    return value


def event_payload(entry: ExtensionEntry, title: str, lines: list[str]) -> dict[str, Any]:
    """Build an ``extension_event`` trigger payload (pure).

    ``content`` is rebuilt from ``title`` and ``lines`` on every merge, so the
    prompt needs only ``{{trigger.content}}``; ``from_name`` names the
    extension in ``{{trigger.from_name}}``.

    Args:
        entry: The (valid) extension.
        title: One line naming what arrived.
        lines: One line per item.

    Returns:
        The payload dict.
    """
    name = entry.manifest.name
    return {
        "extension_id": entry.id,
        "extension_name": name,
        "from_name": name,
        "title": title,
        "lines": list(lines),
        "content": "\n".join([title, *(f"- {line}" for line in lines)]),
    }


def _queued_lines(raw: str, trigger_id: str) -> list[str]:
    """The ``lines`` of a queued event's stored payload.

    Raises:
        ValueError: The payload is not JSON or its ``lines`` is not a list of
            strings (only this module writes it, so that is corruption).
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"queued extension_event {trigger_id} has a payload that is not JSON") from exc
    lines = payload.get("lines") if isinstance(payload, dict) else None
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError(f"queued extension_event {trigger_id} has no list of lines")
    return lines


class ExtensionWakeWatch:
    """Polls enabled ``wake`` extensions for configured agents and wakes them.

    Args:
        clock: Monotonic seconds; injectable for tests.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # (extension id, agent id) -> clock() of the last poll or skip. In
        # memory: after a restart every pair is due at once, which is harmless.
        self._last_polled: dict[tuple[str, str], float] = {}
        # Host-side problems (a load failure, an unreadable interval, a
        # delivery bug) keyed by (extension id, agent id or ""), logged only
        # when the detail changes. Poll failures compare against the stored
        # status row instead.
        self._host_errors: dict[tuple[str, str], str] = {}

    def start(self) -> None:
        """Start the tick loop. A second call while running does nothing."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Extension wake watch started")

    async def stop(self) -> None:
        """Stop the tick loop and wait for it to exit."""
        self._running = False
        loop_task = self._task
        self._task = None
        if loop_task:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task
        logger.info("Extension wake watch stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                config.refresh_if_changed()
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Extension wake watch loop error")
            await asyncio.sleep(wake_tick_seconds())

    async def run_once(self) -> None:
        """Check every due (extension, agent) pair once.

        Raises:
            ExtensionSettingError: ``extensions_enabled`` is unreadable.
        """
        enabled = enabled_ids()
        for entry in get_discovery().valid_entries():
            if entry.manifest.wake is None or entry.id not in enabled:
                continue
            try:
                instance = await asyncio.to_thread(load_extension, entry)
            except ExtensionLoadError as exc:
                self._note_host_error((entry.id, ""), f"cannot load: {exc}")
                continue
            self._clear_host_error((entry.id, ""))
            if not isinstance(instance, SupportsWake):
                # load_extension refuses this; kept so the type is narrowed honestly.
                self._note_host_error((entry.id, ""), "the extension has no poll_wake() method")
                continue
            for agent_id in sorted(db.configured_agent_ids(entry.id)):
                key = (entry.id, agent_id)
                try:
                    await self._check(entry, instance, agent_id)
                except Exception as exc:
                    # One agent's bad config or a delivery bug must not stop
                    # the others; the detail is logged when it changes.
                    self._note_host_error(key, f"{type(exc).__name__}: {exc}")
                else:
                    self._clear_host_error(key)

    async def _check(self, entry: ExtensionEntry, instance: SupportsWake, agent_id: str) -> None:
        """Poll (or skip) one pair when its interval is due, deliver, then commit.

        Raises:
            ValueError: The stored config or its interval is unreadable, or
                the batch names another agent.
            KeyError: The interval field is missing with no default.
        """
        agent = db.get_agent(agent_id)
        if agent is None:
            return
        spec = entry.manifest.agent_config
        stored = db.get_extension_agent_config(entry.id, agent_id)
        if spec is None or stored is None:
            return
        interval = int(agent_config_value(spec, stored, entry.manifest.wake.interval_field))
        key = (entry.id, agent_id)
        now = self._clock()
        last = self._last_polled.get(key)
        if last is not None and now - last < interval:
            return
        self._last_polled[key] = now

        if is_on_vacation(agent):
            # Nothing is recorded: the desk keeps showing the last real check.
            await asyncio.to_thread(instance.skip_wake, agent_id)
            return

        try:
            batch = await asyncio.to_thread(instance.poll_wake, agent_id)
        except Exception as exc:
            # The contract lets poll_wake raise the extension's own types; the
            # host records the plain sentence and moves on (plan W11).
            self._record_failure(entry.id, agent_id, instance.describe_wake_error(exc), exc)
            return
        if batch is None:
            self._record_ok(entry.id, agent_id, 0)
            return
        if batch.agent_id != agent_id:
            raise ValueError(f"poll_wake for {agent_id} returned a batch for {batch.agent_id}")
        if not self._deliver(entry, batch):
            # The agent went on vacation (or the payload was refused) between
            # the check and the enqueue: not told, so not committed.
            return
        await asyncio.to_thread(instance.commit_wake, batch)
        self._record_ok(entry.id, agent_id, len(batch.lines))

    def _deliver(self, entry: ExtensionEntry, batch: WakeBatch) -> bool:
        """Merge into the agent's queued event from this extension, else enqueue a new one.

        Returns:
            Whether the agent is guaranteed to be told (a row was updated or
            written).

        Raises:
            ValueError: The queued event's payload is corrupt.
        """
        existing = db.find_queued_extension_event(batch.agent_id, entry.id)
        if existing is not None:
            merged = _unique(_queued_lines(existing.payload, existing.id) + batch.lines)
            if db.update_queued_trigger_payload(existing.id, event_payload(entry, batch.title, merged)):
                return True
            # Claimed in between: its turn has started, so this is new mail.
        return dispatcher.enqueue_trigger(
            agent_id=batch.agent_id,
            trigger_type=TRIGGER_TYPE,
            source_channel="system",
            payload=event_payload(entry, batch.title, _unique(batch.lines)),
        )

    def _record_ok(self, ext_id: str, agent_id: str, new_count: int) -> None:
        previous = db.get_wake_status(ext_id, agent_id)
        if previous is not None and not previous["ok"]:
            logger.info("Extension %s: checking for agent %s works again", ext_id, agent_id)
        db.record_wake_check(ext_id, agent_id, ok=True, error=None, new_count=new_count)

    def _record_failure(self, ext_id: str, agent_id: str, sentence: str, exc: Exception) -> None:
        previous = db.get_wake_status(ext_id, agent_id)
        if previous is None or previous["ok"] or previous["error"] != sentence:
            logger.warning(
                "Extension %s: checking for agent %s failed: %s (%s: %s)",
                ext_id, agent_id, sentence, type(exc).__name__, exc,
            )
        db.record_wake_check(ext_id, agent_id, ok=False, error=sentence, new_count=0)

    def _note_host_error(self, key: tuple[str, str], detail: str) -> None:
        if self._host_errors.get(key) != detail:
            logger.warning("Extension wake %s%s: %s", key[0], f" for agent {key[1]}" if key[1] else "", detail)
            self._host_errors[key] = detail

    def _clear_host_error(self, key: tuple[str, str]) -> None:
        if self._host_errors.pop(key, None) is not None:
            logger.info("Extension wake %s%s recovered", key[0], f" for agent {key[1]}" if key[1] else "")


def _unique(lines: list[str]) -> list[str]:
    """Lines in order with exact duplicates dropped (pure)."""
    return list(dict.fromkeys(lines))


extension_wake_watch = ExtensionWakeWatch()
