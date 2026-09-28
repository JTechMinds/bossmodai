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
import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar
from uuid import uuid4

from playwright.async_api import (
    Browser,
    BrowserContext,
    Download,
    Error as PlaywrightError,
    Frame,
    Page,
    Playwright,
    async_playwright,
)

from core.attachments import sanitize_file_name

from .marks import Mark, RawMark, place, raw_from_js
from .viewports import ViewportSpec

logger = logging.getLogger(__name__)

# The in-page inspection for element marks (one function; see the file).
_MARKS_JS = (Path(__file__).with_name("page_marks.js")).read_text(encoding="utf-8")
# An element's border + padding, left/top/right/bottom: a frame's content box.
_CONTENT_INSET_JS = """(e) => {
    const s = getComputedStyle(e);
    const px = (v) => parseFloat(v) || 0;
    return [
        px(s.borderLeftWidth) + px(s.paddingLeft), px(s.borderTopWidth) + px(s.paddingTop),
        px(s.borderRightWidth) + px(s.paddingRight), px(s.borderBottomWidth) + px(s.paddingBottom),
    ];
}"""

# The page's title and visible text, main frame plus same-origin child
# frames (a cross-origin frame's document is not readable from the page, so
# by definition it is not part of the fingerprint).
_PAGE_TEXT_JS = """() => {
    const texts = [];
    const walk = (doc) => {
        texts.push(doc.body ? doc.body.innerText : '');
        for (const frame of doc.querySelectorAll('iframe, frame')) {
            let child = null;
            try { child = frame.contentDocument; } catch (e) { child = null; }
            if (child) walk(child);
        }
    };
    walk(document);
    return [document.title, texts.join('\\u0000')];
}"""

T = TypeVar("T")


class BrowserActionError(Exception):
    """A browser action failed or timed out; the message is Playwright's own."""


@dataclass(frozen=True)
class SessionInfo:
    """One agent's open browser session, as the host knows it.

    Attributes:
        session_id: Chosen when the session opens (``uuid4().hex``); kept
            when a window change replaces the context, since that is the
            same logical session.
        opened_at: ISO-8601 UTC time the session opened.
        url: The session page's current URL.
        window: The window preset name (or ``WxH``) the context uses.
    """

    session_id: str
    opened_at: str
    url: str
    window: str


@dataclass(frozen=True)
class Capture:
    """One viewport screenshot at CSS scale, with where it was taken."""

    png: bytes
    url: str
    title: str
    width: int
    height: int
    # The session that took it: its screenshot files belong to that session.
    session: SessionInfo
    # The page's visible controls at capture time, numbered (see marks.py).
    marks: tuple[Mark, ...] = ()


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


@dataclass(frozen=True)
class WaitOutcome:
    """What ``wait_for_change`` saw.

    Attributes:
        changed: The page's fingerprint (URL, title, visible text) differed
            from the one at the start at least once.
        elapsed_s: How long the wait took, in seconds.
        settled: After changing, the fingerprint then held still for the
            settle window before the timeout.
        downloads: Files the page started downloading meanwhile.
    """

    changed: bool
    elapsed_s: float
    settled: bool
    downloads: tuple[DownloadResult, ...] = ()


@dataclass
class _Session:
    session_id: str
    opened_at: str
    context: BrowserContext
    page: Page
    viewport: ViewportSpec
    touch: bool
    downloads_dir: Path
    pending: list[Download] = field(default_factory=list)
    # Mark number → the frame it was found in, from the latest capture.
    mark_frames: dict[int, Frame] = field(default_factory=dict)


class BrowserHost:
    """Headless Chromium with one context per agent, driven from one thread.

    Args:
        browsers_path: Where setup put the browser (``PLAYWRIGHT_BROWSERS_PATH``).
        nav_timeout_ms: Launch and navigation timeout.
        action_timeout_ms: Click/type/screenshot timeout.
        download_timeout_ms: How long an action waits for a download it started.
        settle_ms: Pause after an action so the page can react (and a
            download can start) before the next screenshot.
        wait_poll_ms: How often ``wait_for_change`` re-reads the page.
        wait_settle_ms: How long a changed page must hold still before
            ``wait_for_change`` calls it settled.
    """

    def __init__(
        self,
        *,
        browsers_path: Path,
        nav_timeout_ms: int,
        action_timeout_ms: int,
        download_timeout_ms: int,
        settle_ms: int,
        wait_poll_ms: int,
        wait_settle_ms: int,
    ) -> None:
        self._browsers_path = browsers_path
        self._nav_timeout_ms = nav_timeout_ms
        self._action_timeout_ms = action_timeout_ms
        self._download_timeout_ms = download_timeout_ms
        self._settle_s = settle_ms / 1000
        self._wait_poll_s = wait_poll_ms / 1000
        self._wait_settle_s = wait_settle_ms / 1000
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

    def sessions(self) -> dict[str, SessionInfo]:
        """Return every open session by agent id, without starting the browser.

        Before the browser thread exists there is nothing open, so this
        answers ``{}`` without creating it (like ``has_session``).
        """
        if self._loop is None:
            return {}
        return self._run(self._session_infos)

    def open_session(self, agent_id: str, viewport: ViewportSpec, downloads_dir: Path) -> None:
        """Create the agent's context (starting the browser) when it has none.

        A new session gets a fresh id and open time (see ``SessionInfo``).

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

    def hover(self, agent_id: str, x: float, y: float) -> ActionOutcome:
        """Move the mouse to CSS ``(x, y)`` without clicking, so hover menus open."""
        return self._run(lambda: self._act(agent_id, lambda s: s.page.mouse.move(x, y)))

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
        """Screenshot the agent's viewport at CSS scale, with its element marks."""
        return self._run(lambda: self._capture(agent_id))

    def describe(self, agent_id: str, x: float, y: float) -> dict[str, Any] | None:
        """Say what a click at page CSS ``(x, y)`` would land on, frames included.

        Returns:
            ``{"kind", "name"}`` for a control, ``{"tag"}`` for anything else,
            ``None`` when nothing is there.
        """
        return self._run(lambda: self._describe(agent_id, x, y))

    def select(self, agent_id: str, mark_n: int, x: float, y: float, label: str) -> ActionOutcome:
        """Choose the option labelled ``label`` in the ``<select>`` of mark ``mark_n``.

        Native dropdowns do not render in headless screenshots, so the option
        is chosen through the frame's ``select_option``, not by clicking.

        Raises:
            BrowserActionError: No ``<select>`` at the mark's point, or
                Playwright's own error (e.g. no option with that label).
        """
        return self._run(lambda: self._act(agent_id, lambda s: self._select(s, mark_n, x, y, label)))

    def wait_for_change(self, agent_id: str, timeout_s: float) -> WaitOutcome:
        """Watch the page until it changes and then holds still, or ``timeout_s`` passes.

        The page is fingerprinted (URL, title, a hash of the visible text of
        the main frame and its same-origin child frames) at the start and
        every ``wait_poll_ms``. After the first difference it keeps polling
        until the fingerprint has been stable for ``wait_settle_ms``. Then,
        as after any action, it waits for the page to be parsed and saves
        any downloads the page started.

        The thread timeout is widened for this call only: the wait itself,
        a settle window that may run past it, and the tail every action is
        budgeted for (navigation, action, download save, settle pause).

        Raises:
            BrowserActionError: No session, or the page could not be read at
                the start.
        """
        budget = timeout_s + self._wait_settle_s + self._call_timeout_s
        return self._run(lambda: self._wait_for_change(agent_id, timeout_s), timeout_s=budget)

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

    def _run(self, make: Callable[[], Awaitable[T]], *, timeout_s: float | None = None) -> T:
        """Run a coroutine on the browser thread and wait for it.

        ``timeout_s`` overrides the per-call limit for a call that is meant
        to take long (``wait_for_change``); every other call uses
        ``_call_timeout_s``.
        """
        limit = self._call_timeout_s if timeout_s is None else timeout_s
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._guard(make), loop)
        try:
            return future.result(limit)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise BrowserActionError(f"browser did not answer within {limit:.0f}s") from None

    async def _guard(self, make: Callable[[], Awaitable[T]]) -> T:
        try:
            return await make()
        except PlaywrightError as exc:
            raise BrowserActionError(exc.message) from exc

    # ── on the browser thread ──────────────────────────────────────────────

    async def _has_session(self, agent_id: str) -> bool:
        return agent_id in self._sessions

    async def _session_infos(self) -> dict[str, SessionInfo]:
        return {agent_id: _info(session) for agent_id, session in self._sessions.items()}

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
            self._sessions[agent_id] = await self._new_session(
                viewport,
                downloads_dir,
                storage_state=None,
                session_id=uuid4().hex,
                opened_at=datetime.now(timezone.utc).isoformat(),
            )

    async def _new_session(
        self,
        viewport: ViewportSpec,
        downloads_dir: Path,
        *,
        storage_state: dict[str, Any] | None,
        session_id: str,
        opened_at: str,
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
            session_id=session_id,
            opened_at=opened_at,
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

    async def _fingerprint(self, session: _Session) -> tuple[str, str, str]:
        """``(url, title, sha256 of the visible text)`` of the session's page."""
        title, text = await session.page.evaluate(_PAGE_TEXT_JS)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return session.page.url, title, digest

    async def _poll_fingerprint(self, session: _Session) -> tuple[str, str, str] | None:
        """The page fingerprint, or ``None`` while the page is between documents.

        A navigation destroys the execution context mid-evaluate; that is the
        page changing, so it counts as a change that has not settled rather
        than as an error. A page that stays unreadable surfaces its error at
        the screenshot that follows the wait.
        """
        try:
            return await self._fingerprint(session)
        except PlaywrightError as exc:
            logger.debug("Browser Vision: page unreadable while waiting (%s)", exc.message)
            return None

    async def _wait_for_change(self, agent_id: str, timeout_s: float) -> WaitOutcome:
        session = self._session(agent_id)
        loop = asyncio.get_running_loop()
        start = loop.time()
        deadline = start + timeout_s
        last: tuple[str, str, str] | None = await self._fingerprint(session)
        changed_at: float | None = None
        stable_since = start
        settled = False
        while (now := loop.time()) < deadline:
            await asyncio.sleep(min(self._wait_poll_s, deadline - now))
            current = await self._poll_fingerprint(session)
            now = loop.time()
            if current is None or current != last:
                last, stable_since = current, now
                if changed_at is None:
                    changed_at = now
            elif changed_at is not None and now - stable_since >= self._wait_settle_s:
                settled = True
                break
        elapsed = loop.time() - start
        outcome = await self._after_action(session)
        return WaitOutcome(changed=changed_at is not None, elapsed_s=elapsed, settled=settled, downloads=outcome.downloads)

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
            # Same logical session: its id (and screenshot folder) carry over.
            replacement = await self._new_session(
                viewport,
                session.downloads_dir,
                storage_state=state,
                session_id=session.session_id,
                opened_at=session.opened_at,
            )
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
        marks = await self._collect_marks(session, size["width"], size["height"])
        return Capture(
            png=png,
            url=session.page.url,
            title=await session.page.title(),
            width=size["width"],
            height=size["height"],
            session=_info(session),
            marks=marks,
        )

    async def _frame_offsets(self, session: _Session) -> list[tuple[Frame, float, float, float, float]]:
        """Every frame with its content box in page CSS px: ``(frame, x, y, w, h)``.

        A child frame's box comes from its element's ``bounding_box()`` (page
        coordinates, for nested frames too) plus the element's border and
        padding, since the frame's own coordinates start at its content box.
        A frame whose element has no box (hidden, detached) is skipped.
        """
        size = session.page.viewport_size or {"width": 0, "height": 0}
        frames: list[tuple[Frame, float, float, float, float]] = [
            (session.page.main_frame, 0.0, 0.0, float(size["width"]), float(size["height"]))
        ]
        for frame in session.page.frames:
            if frame is session.page.main_frame:
                continue
            element = await frame.frame_element()
            box = await element.bounding_box()
            if box is None:
                continue
            inset = await element.evaluate(_CONTENT_INSET_JS)
            frames.append((
                frame,
                box["x"] + inset[0],
                box["y"] + inset[1],
                box["width"] - inset[0] - inset[2],
                box["height"] - inset[1] - inset[3],
            ))
        return frames

    async def _collect_marks(self, session: _Session, width: int, height: int) -> tuple[Mark, ...]:
        """Run the page inspection in every frame and number the visible controls.

        A frame that navigates or detaches mid-inspection is skipped with a
        warning (its marks come back on the next screenshot); the page's own
        frame failing is an error.
        """
        raw: list[tuple[RawMark, float, float, Frame]] = []
        for frame, dx, dy, _w, _h in await self._frame_offsets(session):
            try:
                items = await frame.evaluate(_MARKS_JS, {"mode": "collect"})
            except PlaywrightError as exc:
                if frame is session.page.main_frame:
                    raise
                logger.warning("Browser Vision: skipped marks in frame %s (%s)", frame.url, exc.message)
                continue
            raw.extend((raw_from_js(item), dx, dy, frame) for item in items)
        placed = place(raw, width, height)
        session.mark_frames = {mark.n: frame for mark, frame in placed}
        return tuple(mark for mark, _frame in placed)

    async def _frame_at(self, session: _Session, x: float, y: float) -> tuple[Frame, float, float]:
        """The innermost frame whose content box holds page point ``(x, y)``."""
        best = (session.page.main_frame, 0.0, 0.0, float("inf"))
        for frame, fx, fy, fw, fh in await self._frame_offsets(session):
            if frame is session.page.main_frame:
                continue
            if fx <= x < fx + fw and fy <= y < fy + fh and fw * fh < best[3]:
                best = (frame, fx, fy, fw * fh)
        return best[0], best[1], best[2]

    async def _describe(self, agent_id: str, x: float, y: float) -> dict[str, Any] | None:
        session = self._session(agent_id)
        hit = await session.page.main_frame.evaluate(_MARKS_JS, {"mode": "describe", "x": x, "y": y})
        if hit and hit.get("frame"):
            frame, fx, fy = await self._frame_at(session, x, y)
            hit = await frame.evaluate(_MARKS_JS, {"mode": "describe", "x": x - fx, "y": y - fy})
        return hit

    async def _select(self, session: _Session, mark_n: int, x: float, y: float, label: str) -> None:
        frame = session.mark_frames.get(mark_n)
        if frame is None:
            raise BrowserActionError(f"mark @{mark_n} is not on the latest screenshot")
        offsets = {f: (fx, fy) for f, fx, fy, _w, _h in await self._frame_offsets(session)}
        if frame not in offsets:
            raise BrowserActionError(f"the frame of mark @{mark_n} is gone")
        fx, fy = offsets[frame]
        handle = await frame.evaluate_handle(_MARKS_JS, {"mode": "element", "x": x - fx, "y": y - fy})
        element = handle.as_element()
        if element is None:
            raise BrowserActionError(f"NOT_A_SELECT: there is no <select> at mark @{mark_n}")
        await element.select_option(label=label, timeout=self._action_timeout_ms)

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


def _info(session: _Session) -> SessionInfo:
    return SessionInfo(
        session_id=session.session_id,
        opened_at=session.opened_at,
        url=session.page.url,
        window=session.viewport.name,
    )


def _unique_path(folder: Path, name: str) -> Path:
    """Return ``folder/name``, or ``name (n).ext`` when that file exists."""
    candidate = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate
