"""BossMod AI — Telegram allowlist helpers (fail-closed).

Empty ``telegram_allowed_user_ids`` is deny-all. A configured allowlist
must contain at least one valid numeric Telegram user ID.
"""

from __future__ import annotations

from core import config

# Shown when Telegram is enabled without a usable allowlist.
EMPTY_ALLOWLIST_START_ERROR = (
    "Telegram is enabled but telegram_allowed_user_ids is empty. "
    "Refusing to start the bot (fail-closed). Add at least one numeric "
    "Telegram user ID in Settings > Telegram."
)


def parse_allowed_user_ids(raw: str | None) -> set[int]:
    """Parse a comma-separated allowlist. Invalid tokens are skipped."""
    if not raw or not str(raw).strip():
        return set()
    allowed: set[int] = set()
    for part in str(raw).split(","):
        token = part.strip()
        if not token:
            continue
        try:
            allowed.add(int(token))
        except ValueError:
            continue
    return allowed


def is_telegram_user_allowed(user_id: int | None, allowed_raw: str | None = None) -> bool:
    """Return True only if ``user_id`` is present on a non-empty allowlist."""
    raw = allowed_raw if allowed_raw is not None else config.get("telegram_allowed_user_ids")
    allowed = parse_allowed_user_ids(raw)
    if not allowed:
        return False
    return user_id is not None and user_id in allowed


def telegram_start_block_reason() -> str | None:
    """Return a start-refusal reason, or None if the bot may launch.

    Disabled / unconfigured Telegram is not an error (returns None so
    ``start()`` can no-op). An enabled bot with an empty allowlist is blocked.
    """
    if config.get("telegram_enabled") != "true":
        return None
    if not config.get("telegram_bot_token"):
        return None
    if not parse_allowed_user_ids(config.get("telegram_allowed_user_ids")):
        return EMPTY_ALLOWLIST_START_ERROR
    return None
