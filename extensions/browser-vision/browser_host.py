"""Browser Vision — the headless browser, owned by one thread per process.

Playwright objects are bound to the event loop that created them, but CLI
commands arrive on whatever pool thread the runtime used. So one daemon
thread runs its own asyncio loop with the Playwright async API, and every
public method here is synchronous: it submits a coroutine to that loop and
waits for the result.

One ``BrowserContext`` (cookies, storage, tabs) per agent. The browser starts
on the first command and a context lives until ``close``, ``shutdown`` (the
extension was disabled or the worker is stopping) — there is no idle close.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from playwright.async_api import (
    Browser,
    BrowserContext,
    Download,
    Error as PlaywrightError,
    Page,
    Playwright,
    async_playwright,
)

from core.attachments import sanitize_file_name

from .viewports import ViewportSpec

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BrowserActionError(Exception):
    """A browser action failed or timed out; the message is Playwright's own."""


@dataclass(frozen=True)
class Capture:
    """One viewport screenshot at CSS scale, with where it was taken."""

    png: bytes
    url: str
    title: str
    width: int
    height: int


@dataclass(frozen=True)
class DownloadResult:
    """One file an action downloaded, or why it failed.

    Attributes:
        file_name: The saved name inside the downloads folder (``None`` when
            the download failed before a name was chosen).
        size: Bytes on disk; ``None`` on failure.
        error: Why it failed; ``None`` on success.
    """

    file_name: str | None
    size: int | None
    error: str | None


@dataclass(frozen=True)
class ActionOutcome:
    """What an action caused besides the page change: downloads."""

    downloads: tuple[DownloadResult, ...] = ()


@dataclass
class _Session:
    context: BrowserContext
    page: Page
    viewport: ViewportSpec
    touch: bool
    downloads_dir: Path
    pending: list[Download] = field(default_factory=list)


class BrowserHost:
    """Headless Chromium with one context per agent, driven from one thread.

    Args:
        browsers_path: Where setup put the browser (``PLAYWRIGHT_BROWSERS_PATH``).
        nav_timeout_ms: Launch and navigation timeout.
        action_timeout_ms: Click/type/screenshot timeout.
        download_timeout_ms: How long an action waits for a download it started.
        settle_ms: Pause after an action so the page can react (and a
            download can start) before the next screenshot.
    """

    def __init__(
        self,
        *,
        browsers_path: Path,
        nav_timeout_ms: int,
        action_timeout_ms: int,
        download_timeout_ms: int,
        settle_ms: int,
    ) -> None:
        self._browsers_path = browsers_path
        self._nav_timeout_ms = nav_timeout_ms
        self._action_timeout_ms = action_timeout_ms
        self._download_timeout_ms = download_timeout_ms
        self._settle_s = settle_ms / 1000
        # The longest one call can legitimately take: a browser launch (nav
        # timeout), the action, a download it started, and the settle pause.
        self._call_timeout_s = (nav_timeout_ms + action_timeout_ms + download_timeout_ms + settle_ms) / 1000
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # Touched only on the browser thread.
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[str, _Session] = {}
        # Serialises browser start and session creation. Coroutines from two
        # agents interleave on the loop, so an unguarded check-then-start
        # would launch Playwright twice and leak one. Created lazily ON the
        # browser thread (an asyncio.Lock belongs to the loop it is first
        # used on) and dropped at teardown, since shutdown ends that loop.
        self._start_lock: asyncio.Lock | None = None

    # ── public, synchronous ────────────────────────────────────────────────

    def has_session(self, agent_id: str) -> bool:
        """Return whether this agent has an open browser context."""
        if self._loop is None:
            return False
        return self._run(lambda: self._has_session(agent_id))

    def open_session(self, agent_id: str, viewport: ViewportSpec, downloads_dir: Path) -> None:
        """Create the agent's context (starting the browser) when it has none.

        Raises:
            BrowserActionError: The browser failed to launch or the device
                preset is unknown.
        """
        self._run(lambda: self._open_session(agent_id, viewport, downloads_dir))

    def goto(self, agent_id: str, url: str) -> ActionOutcome:
        """Navigate to ``url`` (waits for DOMContentLoaded).

        Raises:
            BrowserActionError: Navigation failed and started no download.
        """
        return self._run(lambda: self._goto(agent_id, url))

    def click(self, agent_id: str, x: float, y: float) -> ActionOutcome:
        """Click (or tap, on a touch window) at CSS ``(x, y)``."""
        return self._run(lambda: self._act(agent_id, lambda s: self._click(s, x, y)))

    def type_text(self, agent_id: str, text: str, *, enter: bool) -> ActionOutcome:
        """Type ``text`` into the focused element, then press Enter if asked."""
        return self._run(lambda: self._act(agent_id, lambda s: self._type(s, text, enter)))

    def press(self, agent_id: str, key: str) -> ActionOutcome:
        """Press a key or combo exactly as given (``Enter``, ``Control+A``)."""
        return self._run(lambda: self._act(agent_id, lambda s: s.page.keyboard.press(key)))

    def scroll(self, agent_id: str, dy: float) -> ActionOutcome:
        """Scroll the page under the viewport centre by ``dy`` CSS px."""
        return self._run(lambda: self._act(agent_id, lambda s: self._scroll(s, dy)))

    def back(self, agent_id: str) -> ActionOutcome:
        """Go back one entry in the page history."""
        return self._run(lambda: self._act(agent_id, lambda s: self._back(s)))

    def set_viewport(self, agent_id: str, viewport: ViewportSpec) -> None:
        """Reopen the agent's context with a new window, keeping cookies and storage.

        Touch, scale factor and user agent are context-level, so the context
        is replaced: its storage state and URL are carried over; in-page form
        contents and scroll position are not.
        """
        self._run(lambda: self._set_viewport(agent_id, viewport))

    def capture(self, agent_id: str) -> Capture:
        """Screenshot the agent's viewport at CSS scale."""
        return self._run(lambda: self._capture(agent_id))

    def close(self, agent_id: str) -> bool:
        """Close the agent's context. Returns whether one was open."""
        if self._loop is None:
            return False
        return self._run(lambda: self._close(agent_id))

    def shutdown(self) -> None:
        """Close every context and the browser, and stop the thread.

        The host can be used again afterwards; it restarts lazily.
        """
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop, self._thread = None, None
        if loop is None or thread is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._guard(self._teardown), loop).result(self._call_timeout_s)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(self._call_timeout_s)

    # ── thread plumbing ────────────────────────────────────────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, name="browser-vision", daemon=True)
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    def _run(self, make: Callable[[], Awaitable[T]]) -> T:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._guard(make), loop)
        try:
            return future.result(self._call_timeout_s)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise BrowserActionError(f"browser did not answer within {self._call_timeout_s:.0f}s") from None

    async def _guard(self, make: Callable[[], Awaitable[T]]) -> T:
        try:
            return await make()
        except PlaywrightError as exc:
            raise BrowserActionError(exc.message) from exc

    # ── on the browser thread ──────────────────────────────────────────────

    async def _has_session(self, agent_id: str) -> bool:
        return agent_id in self._sessions

    def _lock_for_loop(self) -> asyncio.Lock:
        """Return the start lock, creating it on the running (browser) loop."""
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        return self._start_lock

    async def _ensure_browser(self) -> Browser:
        """Start Playwright and Chromium once; callers hold the start lock."""
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        if self._playwright is None:
            # Process-wide, but only this extension drives Playwright here.
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(self._browsers_path)
            self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True, timeout=self._nav_timeout_ms)
        return self._browser

    async def _open_session(self, agent_id: str, viewport: ViewportSpec, downloads_dir: Path) -> None:
        async with self._lock_for_loop():
            if agent_id in self._sessions:
                return
            self._sessions[agent_id] = await self._new_session(viewport, downloads_dir, storage_state=None)

    async def _new_session(
        self,
        viewport: ViewportSpec,
        downloads_dir: Path,
        *,
        storage_state: dict[str, Any] | None,
    ) -> _Session:
        """Open a context and page; the caller holds the start lock."""
        browser = await self._ensure_browser()
        options: dict[str, Any] = {"accept_downloads": True}
        if viewport.device is not None:
            descriptor = self._playwright.devices.get(viewport.device) if self._playwright else None
            if descriptor is None:
                raise BrowserActionError(f"unknown device preset {viewport.device!r}")
            options.update({key: value for key, value in descriptor.items() if key != "default_browser_type"})
        else:
            options["viewport"] = {"width": viewport.width, "height": viewport.height}
        if storage_state is not None:
            options["storage_state"] = storage_state
        context = await browser.new_context(**options)
        context.set_default_timeout(self._action_timeout_ms)
        context.set_default_navigation_timeout(self._nav_timeout_ms)
        page = await context.new_page()
        session = _Session(
            context=context,
            page=page,
            viewport=viewport,
            touch=bool(options.get("has_touch")),
            downloads_dir=downloads_dir,
        )
        self._watch(session, page)
        # A link that opens a new tab moves the session to that tab, so the
        # next screenshot shows what the click opened.
        context.on("page", lambda new_page: self._adopt(session, new_page))
        return session

    def _watch(self, session: _Session, page: Page) -> None:
        # A lambda, not ``list.append``: Playwright tags the handler object
        # with an attribute, which a builtin method cannot take.
        page.on("download", lambda download: session.pending.append(download))

    def _adopt(self, session: _Session, page: Page) -> None:
        self._watch(session, page)
        session.page = page

    def _session(self, agent_id: str) -> _Session:
        session = self._sessions.get(agent_id)
        if session is None:
            raise BrowserActionError('no browser page is open; run "bv open <url>" first')
        return session

    async def _goto(self, agent_id: str, url: str) -> ActionOutcome:
        session = self._session(agent_id)
        try:
            await session.page.goto(url, wait_until="domcontentloaded", timeout=self._nav_timeout_ms)
        except PlaywrightError as exc:
            # A URL that serves a file aborts the navigation and starts a
            # download instead; that is a success with a download line.
            await asyncio.sleep(self._settle_s)
            if not session.pending:
                raise BrowserActionError(exc.message) from exc
        return await self._after_action(session)

    async def _act(self, agent_id: str, action: Callable[[_Session], Awaitable[Any]]) -> ActionOutcome:
        session = self._session(agent_id)
        await action(session)
        return await self._after_action(session)

    async def _click(self, session: _Session, x: float, y: float) -> None:
        if session.touch:
            await session.page.touchscreen.tap(x, y)
        else:
            await session.page.mouse.click(x, y)

    async def _type(self, session: _Session, text: str, enter: bool) -> None:
        await session.page.keyboard.type(text)
        if enter:
            await session.page.keyboard.press("Enter")

    async def _scroll(self, session: _Session, dy: float) -> None:
        size = session.page.viewport_size
        if size is None:
            raise BrowserActionError("the page has no viewport size")
        # The wheel scrolls whatever is under the pointer; aim at the middle.
        await session.page.mouse.move(size["width"] / 2, size["height"] / 2)
        await session.page.mouse.wheel(0, dy)

    async def _back(self, session: _Session) -> None:
        await session.page.go_back(wait_until="domcontentloaded", timeout=self._nav_timeout_ms)

    async def _after_action(self, session: _Session) -> ActionOutcome:
        """Let the page react, wait for it to be parsed, then save any downloads."""
        await asyncio.sleep(self._settle_s)
        await session.page.wait_for_load_state("domcontentloaded", timeout=self._nav_timeout_ms)
        pending = list(session.pending)
        # Cleared in place: the page listeners append to this same list.
        session.pending.clear()
        results = [await self._save(session, download) for download in pending]
        return ActionOutcome(downloads=tuple(results))

    async def _save(self, session: _Session, download: Download) -> DownloadResult:
        session.downloads_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(session.downloads_dir, sanitize_file_name(download.suggested_filename))
        try:
            await asyncio.wait_for(download.save_as(target), timeout=self._download_timeout_ms / 1000)
        except asyncio.TimeoutError:
            await download.cancel()
            return DownloadResult(
                file_name=None,
                size=None,
                error=f"{download.suggested_filename}: did not finish within {self._download_timeout_ms / 1000:.0f}s",
            )
        except PlaywrightError as exc:
            return DownloadResult(file_name=None, size=None, error=f"{download.suggested_filename}: {exc.message}")
        return DownloadResult(file_name=target.name, size=target.stat().st_size, error=None)

    async def _set_viewport(self, agent_id: str, viewport: ViewportSpec) -> None:
        session = self._session(agent_id)
        state = await session.context.storage_state()
        url = session.page.url
        # Replacing the context may (re)start the browser: same lock as open.
        async with self._lock_for_loop():
            await session.context.close()
            del self._sessions[agent_id]
            replacement = await self._new_session(viewport, session.downloads_dir, storage_state=state)
            self._sessions[agent_id] = replacement
        if url and url != "about:blank":
            await replacement.page.goto(url, wait_until="domcontentloaded", timeout=self._nav_timeout_ms)
            await asyncio.sleep(self._settle_s)

    async def _capture(self, agent_id: str) -> Capture:
        session = self._session(agent_id)
        size = session.page.viewport_size
        if size is None:
            raise BrowserActionError("the page has no viewport size")
        png = await session.page.screenshot(type="png", scale="css", timeout=self._action_timeout_ms)
        return Capture(
            png=png,
            url=session.page.url,
            title=await session.page.title(),
            width=size["width"],
            height=size["height"],
        )

    async def _close(self, agent_id: str) -> bool:
        session = self._sessions.pop(agent_id, None)
        if session is None:
            return False
        await session.context.close()
        return True

    async def _teardown(self) -> None:
        for agent_id in list(self._sessions):
            session = self._sessions.pop(agent_id)
            try:
                await session.context.close()
            except PlaywrightError:
                # The browser may already be gone; shutdown must continue.
                logger.warning("Browser Vision: context for %s was already closed", agent_id)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        # Bound to this loop, which shutdown is about to stop.
        self._start_lock = None


def _unique_path(folder: Path, name: str) -> Path:
    """Return ``folder/name``, or ``name (n).ext`` when that file exists."""
    candidate = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate
