"""BossMod AI — Telegram bot integration.

Public API
----------
start(services, broadcast_manager)
    Launch the bot alongside FastAPI.  Returns a ``TelegramEventBridge``
    for the runtime event dispatcher, or ``None`` if Telegram is disabled.

stop()
    Gracefully shut down the bot.

is_enabled()
    Check whether the integration is configured and enabled.

get_bridge()
    Return the active event bridge, or ``None``.
"""

from __future__ import annotations

import logging
from typing import Any

from core import config
from integrations.telegram.auth import telegram_start_block_reason

logger = logging.getLogger(__name__)

_state: dict[str, Any] = {}


def is_enabled() -> bool:
    """Return True if Telegram integration is configured and enabled."""
    return config.get("telegram_enabled") == "true" and bool(config.get("telegram_bot_token"))


async def start(
    *,
    services: Any,
    broadcast_manager: Any,
) -> Any | None:
    """Launch the Telegram bot.  Returns the event bridge or ``None``."""
    if not is_enabled():
        return None

    blocked = telegram_start_block_reason()
    if blocked:
        logger.error(blocked)
        return None

    from integrations.telegram.bot import create_application
    from integrations.telegram.bridge import TelegramEventBridge

    token = config.require("telegram_bot_token")
    app = create_application(token, services=services, broadcast_manager=broadcast_manager)
    bridge = TelegramEventBridge(app.bot)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    _state["app"] = app
    _state["bridge"] = bridge
    logger.info("Telegram bot started")
    return bridge


async def stop() -> None:
    """Gracefully shut down the Telegram bot."""
    app = _state.get("app")
    if app is not None:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Telegram bot stopped")
    _state.clear()


def get_bridge() -> Any | None:
    """Return the active event bridge, or ``None``."""
    return _state.get("bridge")
