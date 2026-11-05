"""Local Network Access — the access code's matching, its sessions and the login throttle (pure logic).

The access code is the only credential a device on the network needs. The
operator chooses it in the extension's Settings (the host stores it sealed
at rest); nothing here stores or generates one. A session is not stored
either: its cookie value is derived from the code, so changing the code
signs every device out at once without a session table.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from typing import Callable

_SESSION_LABEL = b"bossmod-network-session-v1"


def canonical_code(text: str) -> str:
    """Return a code as it is compared: surrounding spaces dropped, case folded.

    Case is ignored because phones auto-capitalize; inner spaces and dashes
    are kept, because inside a chosen code they make it a different code.
    """
    return text.strip().casefold()


def codes_match(typed: str, code: str) -> bool:
    """Return whether the typed text is the access code (ignoring case and surrounding spaces), in constant time."""
    return hmac.compare_digest(canonical_code(typed).encode("utf-8"), canonical_code(code).encode("utf-8"))


def session_value(code: str) -> str:
    """Return the cookie value that proves a login with ``code``.

    HMAC-SHA256 of a fixed label keyed by the canonical code, hex-encoded.
    It changes whenever the code does, but not when only its case does.
    """
    return hmac.new(canonical_code(code).encode("utf-8"), _SESSION_LABEL, hashlib.sha256).hexdigest()


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

