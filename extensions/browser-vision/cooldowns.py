"""Browser Vision — sites left alone after they showed a bot check.

A site that answered with a bot check is not visited again until its
cooldown ends, so agents do not keep hitting it from the operator's IP. The
file is ``cooldowns.json`` in the extension data dir and deliberately
outlives the process: the block on the site does too. Only the process that
owns the browser (the runtime worker, the one running ``bv``) writes it.

Format: ``{site_key: {"blocked_at": iso, "until": iso}}`` (UTC). The time the
site blocked is kept, not derived from ``until``, so changing the manifest's
cooldown length never misstates it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter, ValidationError

from .sites import matching_site


class CooldownStoreError(ValueError):
    """``cooldowns.json`` exists but cannot be read as cooldown entries."""


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Aware only: a naive time could not be compared with the UTC clock.
    blocked_at: AwareDatetime
    until: AwareDatetime


_ENTRIES = TypeAdapter(dict[str, _Entry])


@dataclass(frozen=True)
class Cooldown:
    """One site under cooldown.

    Attributes:
        site: The site key that showed the bot check.
        blocked_at: When it did (UTC).
        until: When it may be tried again (UTC).
    """

    site: str
    blocked_at: datetime
    until: datetime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CooldownStore:
    """Reads and writes the cooldown file.

    Args:
        path: ``cooldowns.json`` inside the extension data dir.
        clock: Returns the current UTC time (injectable for expiry tests).
    """

    def __init__(self, path: Path, clock: Callable[[], datetime] = _utc_now) -> None:
        self._path = path
        self._clock = clock

    def active(self, key: str) -> Cooldown | None:
        """Return the cooldown covering site ``key`` (itself or a parent site), if any.

        Raises:
            CooldownStoreError: The file is unreadable or malformed.
        """
        entries = self._read()
        site = matching_site(key, entries)
        return entries[site] if site is not None else None

    def block(self, site: str, minutes: float) -> Cooldown:
        """Put ``site`` on cooldown for ``minutes`` from now and save it atomically.

        Expired entries are dropped from the file in the same write.

        Raises:
            CooldownStoreError: The existing file is unreadable or malformed.
            OSError: The file cannot be written.
        """
        entries = self._read()
        now = self._clock()
        cooldown = Cooldown(site=site, blocked_at=now, until=now + timedelta(minutes=minutes))
        entries[site] = cooldown
        payload = {key: _Entry(blocked_at=entry.blocked_at, until=entry.until) for key, entry in entries.items()}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Written aside and renamed in, so a crash never leaves half a file.
        partial = self._path.with_name(f".{self._path.name}.partial")
        partial.write_bytes(_ENTRIES.dump_json(payload))
        os.replace(partial, self._path)
        return cooldown

    def _read(self) -> dict[str, Cooldown]:
        """Return the unexpired entries (expired ones are pruned here)."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise CooldownStoreError(f"{self._path}: {exc}") from exc
        try:
            parsed = _ENTRIES.validate_json(raw)
        except ValidationError as exc:
            raise CooldownStoreError(f"{self._path}: {exc}") from exc
        now = self._clock()
        return {
            site: Cooldown(site=site, blocked_at=entry.blocked_at, until=entry.until)
            for site, entry in parsed.items()
            if entry.until > now
        }
