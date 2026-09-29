"""Browser Vision host: the browser starts once however many agents race to it."""

from __future__ import annotations

import asyncio
import importlib
import random
import threading
import time
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
        self.launch_options: list[dict] = []


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
        self._counts.launch_options.append(options)
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
        pace_min_ms=0,
        pace_jitter_ms=0,
        rng=random.Random(0),
        page_text_chars=3000,
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
        # R35: full Chromium in new headless mode, not the headless shell.
        assert counts.launch_options == [{"headless": True, "channel": "chromium", "timeout": 5000}]
        assert host.has_session("agent-a") and host.has_session("agent-b")

        # After shutdown a new loop is used; the lock must not be the old loop's.
        host.shutdown()
        host.open_session("agent-a", viewport, tmp_path / "agent-a")
        assert (counts.starts, counts.launches) == (2, 2)
    finally:
        host.shutdown()


# ── R31: a child frame that detaches mid-capture is skipped, not fatal ──────


class _StubElement:
    def __init__(self, box: dict) -> None:
        self._box = box

    async def bounding_box(self) -> dict:
        return self._box

    async def evaluate(self, script) -> list[float]:
        return [2.0, 3.0, 2.0, 3.0]


class _StubFrame:
    def __init__(self, url: str, element: _StubElement | None) -> None:
        self.url = url
        self._element = element

    async def frame_element(self) -> _StubElement:
        if self._element is None:
            raise _host_module.PlaywrightError("Frame has been detached.")
        return self._element


class _StubPage:
    def __init__(self, main_frame, frames) -> None:
        self.main_frame = main_frame
        self.frames = [main_frame, *frames]
        self.viewport_size = {"width": 1280, "height": 800}


class _StubSession:
    def __init__(self, page) -> None:
        self.page = page


def test_a_child_frame_that_detached_is_skipped_with_a_warning(tmp_path: Path, caplog) -> None:
    host = _host_module.BrowserHost(
        browsers_path=tmp_path / "ms-playwright",
        nav_timeout_ms=5000,
        action_timeout_ms=5000,
        download_timeout_ms=5000,
        settle_ms=0,
        wait_poll_ms=500,
        wait_settle_ms=1000,
        pace_min_ms=0,
        pace_jitter_ms=0,
        rng=random.Random(0),
        page_text_chars=3000,
    )
    main = _StubFrame("https://example.com/", None)
    kept = _StubFrame("https://example.com/kept", _StubElement({"x": 100.0, "y": 50.0, "width": 304.0, "height": 156.0}))
    gone = _StubFrame("https://ads.example.net/slot", None)
    session = _StubSession(_StubPage(main, [gone, kept]))

    with caplog.at_level("WARNING", logger=_host_module.__name__):
        offsets = asyncio.run(host._frame_offsets(session))

    assert offsets == [
        (main, 0.0, 0.0, 1280.0, 800.0),
        (kept, 102.0, 53.0, 300.0, 150.0),
    ]
    assert [record.getMessage() for record in caplog.records] == [
        "Browser Vision: skipped marks in frame https://ads.example.net/slot (Frame has been detached.)",
    ]


# ── R33: polite pacing per site, across agents ──────────────────────────────


class _Draws(random.Random):
    """A Random whose jitter draws are fixed values, recording each request."""

    def __init__(self, values: list[float]) -> None:
        super().__init__(0)
        self.values = list(values)
        self.asked: list[tuple[float, float]] = []

    def uniform(self, a: float, b: float) -> float:
        self.asked.append((a, b))
        return self.values.pop(0)


def _pacing_host(tmp_path: Path, rng: random.Random, *, min_ms: int = 200, jitter_ms: int = 300):
    return _host_module.BrowserHost(
        browsers_path=tmp_path / "ms-playwright",
        nav_timeout_ms=5000,
        action_timeout_ms=5000,
        download_timeout_ms=5000,
        settle_ms=0,
        wait_poll_ms=500,
        wait_settle_ms=1000,
        pace_min_ms=min_ms,
        pace_jitter_ms=jitter_ms,
        rng=rng,
        page_text_chars=3000,
    )


def test_a_second_action_on_a_site_waits_the_minimum_plus_the_drawn_jitter(tmp_path: Path) -> None:
    draws = _Draws([0.25, 0.1])
    host = _pacing_host(tmp_path, draws)
    first = host.pace("redfin.com")
    started = time.monotonic()
    second = host.pace("redfin.com")
    elapsed = time.monotonic() - started
    assert first == _host_module.Paced(site="redfin.com", waited_s=0.0)
    assert second.site == "redfin.com"
    # min 0.2 s + the second draw 0.1 s, less the moment between the calls.
    assert 0.29 <= second.waited_s <= 0.3
    assert elapsed >= 0.29
    assert draws.asked == [(0, 0.3), (0, 0.3)]
    # Nothing touched the browser.
    assert host._loop is None


def test_different_sites_do_not_wait_for_each_other(tmp_path: Path) -> None:
    host = _pacing_host(tmp_path, random.Random(1))
    assert host.pace("redfin.com").waited_s == 0.0
    assert host.pace("zillow.com").waited_s == 0.0


def test_www_and_subdomains_share_a_site(tmp_path: Path) -> None:
    sites = importlib.import_module(f"{_PACKAGE.__name__}.sites")
    assert sites.site_key("https://WWW.Redfin.com/FL/Miami") == "redfin.com"
    assert sites.site_key("about:blank") is None
    host = _pacing_host(tmp_path, random.Random(1))
    host.pace(sites.site_key("https://www.redfin.com/"))
    paced = host.pace(sites.site_key("https://ratelimited.redfin.com/"))
    assert paced.site == "redfin.com" and paced.waited_s >= 0.19


def test_two_agents_on_one_site_are_paced_together(tmp_path: Path) -> None:
    host = _pacing_host(tmp_path, random.Random(1), jitter_ms=0)
    barrier = threading.Barrier(2)
    waits: list[float] = []

    def act() -> None:
        barrier.wait()
        waits.append(host.pace("redfin.com").waited_s)

    threads = [threading.Thread(target=act) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(waits) == 2
    first, second = sorted(waits)
    assert first == 0.0 and 0.19 <= second <= 0.2
