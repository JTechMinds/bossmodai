"""Browser Vision host: the browser starts once however many agents race to it."""

from __future__ import annotations

import asyncio
import importlib
import threading
from pathlib import Path

import pytest

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_PACKAGE = import_package(get_discovery().get("browser-vision"))
_host_module = importlib.import_module(f"{_PACKAGE.__name__}.browser_host")
_viewports = importlib.import_module(f"{_PACKAGE.__name__}.viewports")


class _Counts:
    def __init__(self) -> None:
        self.starts = 0
        self.launches = 0
        self.contexts = 0


class _Page:
    def on(self, event, handler) -> None:
        return None


class _Context:
    def __init__(self, counts: _Counts) -> None:
        counts.contexts += 1

    def set_default_timeout(self, ms) -> None:
        return None

    def set_default_navigation_timeout(self, ms) -> None:
        return None

    def on(self, event, handler) -> None:
        return None

    async def new_page(self) -> _Page:
        return _Page()

    async def close(self) -> None:
        return None


class _Browser:
    def __init__(self, counts: _Counts) -> None:
        self._counts = counts

    def is_connected(self) -> bool:
        return True

    async def new_context(self, **options) -> _Context:
        return _Context(self._counts)

    async def close(self) -> None:
        return None


class _Chromium:
    def __init__(self, counts: _Counts) -> None:
        self._counts = counts

    async def launch(self, **options) -> _Browser:
        self._counts.launches += 1
        # Yield so a second agent's coroutine can interleave here.
        await asyncio.sleep(0.05)
        return _Browser(self._counts)


class _Playwright:
    def __init__(self, counts: _Counts) -> None:
        self.chromium = _Chromium(counts)
        self.devices: dict = {}

    async def stop(self) -> None:
        return None


def _counting_async_playwright(counts: _Counts):
    class _Starter:
        async def start(self) -> _Playwright:
            counts.starts += 1
            await asyncio.sleep(0.05)
            return _Playwright(counts)

    return lambda: _Starter()


def test_two_agents_opening_at_once_start_one_playwright_and_one_browser(monkeypatch, tmp_path: Path) -> None:
    counts = _Counts()
    monkeypatch.setattr(_host_module, "async_playwright", _counting_async_playwright(counts))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    host = _host_module.BrowserHost(
        browsers_path=tmp_path / "ms-playwright",
        nav_timeout_ms=5000,
        action_timeout_ms=5000,
        download_timeout_ms=5000,
        settle_ms=0,
        wait_poll_ms=500,
        wait_settle_ms=1000,
    )
    viewport = _viewports.ViewportSpec(name="desktop", device=None, width=1280, height=800)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def open_for(agent_id: str) -> None:
        barrier.wait()
        try:
            host.open_session(agent_id, viewport, tmp_path / agent_id)
        except BaseException as exc:  # surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=open_for, args=(name,)) for name in ("agent-a", "agent-b")]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert errors == []
        assert (counts.starts, counts.launches, counts.contexts) == (1, 1, 2)
        assert host.has_session("agent-a") and host.has_session("agent-b")

        # After shutdown a new loop is used; the lock must not be the old loop's.
        host.shutdown()
        host.open_session("agent-a", viewport, tmp_path / "agent-a")
        assert (counts.starts, counts.launches) == (2, 2)
    finally:
        host.shutdown()
