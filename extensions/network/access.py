"""Local Network Access — the access code, its sessions and the login throttle (pure logic).

The access code is the only credential a device on the network needs. A
session is not stored: its cookie value is derived from the code, so
rotating the code invalidates every session at once without a session table.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import os
import secrets
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# No 0/O, 1/I/L: a code is read off one screen and typed on another.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_SESSION_LABEL = b"bossmod-network-session-v1"
_CODE_FILE_MODE = 0o600


def generate_code(length: int) -> str:
    """Return a random access code of ``length`` characters from :data:`CODE_ALPHABET`.

    Args:
        length: How many characters; at least 1.

    Raises:
        ValueError: ``length`` is less than 1.
    """
    if length < 1:
        raise ValueError("an access code needs at least one character")
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def is_well_formed(code: str) -> bool:
    """Return whether ``code`` is non-empty and uses only :data:`CODE_ALPHABET`."""
    return bool(code) and all(char in CODE_ALPHABET for char in code)


def normalize_code(typed: str) -> str:
    """Return what a person typed as a comparable code: no spaces or dashes, upper case."""
    return "".join(typed.split()).replace("-", "").upper()


def codes_match(typed: str, code: str) -> bool:
    """Return whether the typed text is the access code, compared in constant time."""
    return hmac.compare_digest(normalize_code(typed).encode("utf-8"), code.encode("utf-8"))


def session_value(code: str) -> str:
    """Return the cookie value that proves a login with ``code``.

    HMAC-SHA256 of a fixed label keyed by the code, hex-encoded. It changes
    whenever the code does.
    """
    return hmac.new(code.encode("utf-8"), _SESSION_LABEL, hashlib.sha256).hexdigest()


def session_matches(cookie_value: str | None, code: str) -> bool:
    """Return whether a cookie value is the session for ``code``, compared in constant time."""
    if not cookie_value:
        return False
    return hmac.compare_digest(cookie_value.encode("utf-8"), session_value(code).encode("utf-8"))


class LoginThrottle:
    """Per-client lockout after repeated wrong codes (in memory, per process).

    A client that fails ``max_failures`` times within ``lockout_seconds`` of
    its first failure is refused for ``lockout_seconds``. A restart clears
    it, which is acceptable: the code itself changes only on request.

    Args:
        max_failures: Wrong codes allowed before the lockout; at least 1.
        lockout_seconds: How long failures count and a lockout lasts; at least 1.
        clock: Monotonic seconds; injected so tests need not wait.

    Raises:
        ValueError: A limit is less than 1.
    """

    def __init__(self, max_failures: int, lockout_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        if max_failures < 1 or lockout_seconds < 1:
            raise ValueError("max_failures and lockout_seconds must be at least 1")
        self._max = max_failures
        self._window = lockout_seconds
        self._clock = clock
        # client → (failures, window start, locked until or 0.0)
        self._state: dict[str, tuple[int, float, float]] = {}

    def retry_after(self, client: str) -> int:
        """Return whole seconds until ``client`` may try again, or 0 when it may now."""
        self._forget_expired()
        state = self._state.get(client)
        if state is None:
            return 0
        remaining = state[2] - self._clock()
        return max(0, math.ceil(remaining))

    def record_failure(self, client: str) -> None:
        """Count one wrong code from ``client``; the max-th one starts its lockout."""
        self._forget_expired()
        now = self._clock()
        failures, started, _locked = self._state.get(client, (0, now, 0.0))
        failures += 1
        locked_until = now + self._window if failures >= self._max else 0.0
        self._state[client] = (failures, started, locked_until)

    def record_success(self, client: str) -> None:
        """Forget ``client``'s failures after a correct code."""
        self._state.pop(client, None)

    def _forget_expired(self) -> None:
        now = self._clock()
        expired = [
            client for client, (_failures, started, locked_until) in self._state.items()
            if (locked_until and now >= locked_until) or (not locked_until and now - started >= self._window)
        ]
        for client in expired:
            del self._state[client]


class AccessCodeStore:
    """The access code, kept sealed in a file in the extension's data dir (mode 0600).

    Args:
        path: The file holding the sealed code.
        seal: The host's at-rest wrap (``ExtensionContext.seal``).
        unseal: Reverses ``seal`` (``ExtensionContext.unseal``).
        length: Characters in a newly generated code.
    """

    def __init__(self, path: Path, seal: Callable[[str], str], unseal: Callable[[str], str], length: int) -> None:
        self._path = path
        self._seal = seal
        self._unseal = unseal
        self._length = length

    def load_or_create(self) -> str:
        """Return the stored code, generating and storing one when there is none.

        A file that cannot be read back (damaged, or sealed with a data key
        that no longer exists) is replaced by a new code and the replacement
        is logged at warning level: the code is a credential anyone can
        regenerate, and a dead end here would leave the feature unusable.

        Raises:
            OSError: The file cannot be read or written for a reason other
                than not existing.
        """
        try:
            sealed = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self.rotate()
        try:
            code = self._unseal(sealed.strip())
        except (RuntimeError, ValueError):
            logger.warning("The network access code in %s cannot be read; generating a new one", self._path, exc_info=True)
            return self.rotate()
        if not is_well_formed(code):
            logger.warning("The network access code in %s is malformed; generating a new one", self._path)
            return self.rotate()
        return code

    def rotate(self) -> str:
        """Generate a new code, store it (replacing any earlier one) and return it.

        Raises:
            OSError: The file cannot be written.
        """
        code = generate_code(self._length)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target with the final mode, then renamed over
        # it, so the code is never on disk readable by others or half written.
        temp = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _CODE_FILE_MODE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                # A leftover temp file from a crash keeps its old mode otherwise.
                os.fchmod(handle.fileno(), _CODE_FILE_MODE)
                handle.write(self._seal(code))
            os.replace(temp, self._path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return code
