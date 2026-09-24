"""Keep the serve loop free while agent work runs.

Chat, Needs, channel reads, and WebSocket paint share one event loop.
Agent shell, System AI, and decision-repair retries must not occupy that
loop. Long shell waits run on a worker thread. A wait at least as long as
the model stall window or the wall-clock backstop runs in a child process
from that worker.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, TypeVar

T = TypeVar("T")

SHELL_WORKER_ENV = "BOSSMOD_SHELL_WORKER"


def on_request_loop() -> bool:
    """Return True when this thread is inside a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _positive_setting(key: str, *, kind: str) -> float | None:
    try:
        from core import config

        value = config.get_float(key) if kind == "float" else config.get_int(key)
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return float(value)


def shell_starve_floor_seconds() -> float:
    """Shortest wait that starves Needs and channel paint.

    The floor is the smaller of the stall window, the wall backstop, and
    the CLI shell timeout (default 30s). A ``uv run pytest`` hang of that
    length froze paint while HTTP still returned 200.
    """
    limits = [
        value
        for value in (
            _positive_setting("llm_stall_timeout_seconds", kind="float"),
            _positive_setting("llm_request_timeout_seconds", kind="float"),
            _positive_setting("cli_shell_timeout_seconds", kind="int"),
            30.0,
        )
        if value is not None and value > 0
    ]
    return min(limits) if limits else 30.0


def shell_is_long(timeout_seconds: float) -> bool:
    """Return True when this shell wait must leave the request loop."""
    return float(timeout_seconds) >= shell_starve_floor_seconds()


def shell_uses_worker_process(timeout_seconds: float) -> bool:
    """Return True when the wait is at least the stall window or the wall backstop."""
    stall = _positive_setting("llm_stall_timeout_seconds", kind="float")
    backstop = _positive_setting("llm_request_timeout_seconds", kind="float")
    timeout = float(timeout_seconds)
    if stall is not None and timeout >= stall:
        return True
    if backstop is not None and timeout >= backstop:
        return True
    return False


def in_shell_worker_process() -> bool:
    """Return True inside the child that is already running a long shell."""
    return os.environ.get(SHELL_WORKER_ENV) == "1"


async def off_request_loop(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run *func* on a worker thread so the serve loop can paint."""
    return await asyncio.to_thread(func, *args, **kwargs)


async def run_shell_off_request_loop(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a shell-capable call off the serve loop.

    The worker thread waits. When the shell timeout is at least the stall
    window or the wall backstop, the executor itself uses a child process.
    """
    return await off_request_loop(func, *args, **kwargs)


async def breathe() -> None:
    """Yield one loop turn so Needs and channel paint can run."""
    await asyncio.sleep(0)
