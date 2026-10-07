"""Browser Vision — the ``bv`` subcommands.

Parses ``bv <sub> …``, keeps each agent's sticky view settings, drives the
browser host, draws the keypad and marks, and returns a CLI result that
carries the screenshot path for the model.

Three ways to aim, all resolved against the MOST RECENT screenshot:

- element marks: ``@n`` names a control from the result legend (primary);
- direct pointing: ``point <x> <y>`` (image pixels) or ``point1k <x> <y>``
  (0–1000 of the image) moves the mouse there and returns a screenshot with
  the cursor drawn; ``click`` with no argument clicks where the mouse is;
- the 3×3 keypad: ``zoom <d>`` narrows the view to region ``d`` (1–9, laid out
  like a phone keypad), ``click <d>`` clicks a region's centre (fallback for
  anything the page does not expose as a control).

Any action resets the zoom and returns the full page; ``view`` keeps it.

Actions that can reach the network (open, back, click, type --enter, key,
select) are paced per site (``BrowserHost.pace``), refused while the site
is on a bot-check cooldown, and checked for a bot check afterwards
(``botwall.detect``).
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path
from core.extensions.contract import SetupStatus
from core.llm.routing import agent_model
from core.models import Agent
from db.model_capabilities import supports_images

from .botwall import BotWallRules, detect
from .browser_host import ActionOutcome, BrowserActionError, Capture, Paced, SessionInfo, WaitOutcome
from .cooldowns import CooldownStore, CooldownStoreError
from .grid import (
    GridColor,
    GridStyle,
    LabelStyle,
    Rect,
    ViewGeometry,
    ZoomLimit,
    image_to_page,
    parse_color,
    parse_keypad_digit,
    parse_opacity,
    region_center,
    render_view,
    zoom_into,
)
from .marks import Mark, feedback_line, hover_line, legend_line
from .screenshots import ScreenshotStore, ShotMeta
from .sessions import SessionMarker, SessionMarkers
from .sites import site_key
from .viewports import ViewportSpec, WindowPreset, resolve_viewport

DOWNLOADS_VIRTUAL_DIR = "/me/downloads"
WINDOW_SWAP_NOTE = (
    "window changed: cookies and site storage were kept; "
    "form contents and scroll position were reset"
)
# An RFC 3986 scheme followed by ':'.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
# host:port, optionally with a path — looks like "scheme:" but is not one.
_HOST_PORT_RE = re.compile(r"^[A-Za-z0-9.-]+:\d+(?:/.*)?$")
# @n: a mark from the latest screenshot's legend.
_MARK_RE = re.compile(r"^@(\d+)$")
# Operator status lines keep URLs and reasons to this many characters.
_STATUS_MAX_CHARS = 80
_VIEW_FLAGS = {"--marks", "--grid", "--grid-color", "--grid-opacity"}
_USAGE = (
    "USAGE: bv open|view|zoom|point|point1k|click|type|select|key|scroll|back|wait|window|status|close"
    ' — run "learn bv" for details'
)
# bv point1k: the 0–1000 scale's top end (1000,1000 is the bottom-right corner).
_POINT1K_MAX = 1000
_TYPE_USAGE = "USAGE: bv type [@<n>] [--enter] [<text …>]"
# A pacing wait shorter than this is not worth a line in the result.
_PACED_NOTE_MIN_S = 0.05


class BrowserVisionDefaults(BaseModel):
    """The manifest's ``defaults`` block, validated when the extension starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    window_default: str
    window_presets: dict[str, WindowPreset]
    label_font_ratio: float = Field(gt=0)
    label_font_min_px: int = Field(gt=0)
    label_font_max_px: int = Field(gt=0)
    label_opacity: float = Field(ge=0, le=1)
    keypad_font_px: int = Field(gt=0)
    marks_default: bool
    image_max_px: int = Field(gt=0)
    grid_default: bool
    grid_color_default: str
    grid_opacity_default: float = Field(ge=0, le=1)
    nav_timeout_ms: int = Field(gt=0)
    action_timeout_ms: int = Field(gt=0)
    download_timeout_ms: int = Field(gt=0)
    settle_ms: int = Field(ge=0)
    wait_default_s: float = Field(gt=0)
    wait_poll_ms: int = Field(gt=0)
    wait_settle_ms: int = Field(ge=0)
    screenshots_keep: int = Field(ge=1)
    scroll_fraction: float = Field(gt=0)
    pace_min_ms: int = Field(ge=0)
    pace_jitter_ms: int = Field(ge=0)
    botwall: BotWallRules

    @field_validator("grid_color_default")
    @classmethod
    def _color_parses(cls, value: str) -> str:
        parse_color(value)
        return value

    @model_validator(mode="after")
    def _label_font_bounds_ordered(self) -> "BrowserVisionDefaults":
        if self.label_font_min_px > self.label_font_max_px:
            raise ValueError("label_font_min_px must not exceed label_font_max_px")
        return self

    @model_validator(mode="after")
    def _default_window_exists(self) -> "BrowserVisionDefaults":
        if self.window_default not in self.window_presets:
            raise ValueError(f"window_default {self.window_default!r} is not a window preset")
        return self


class BrowserHostLike(Protocol):
    """What the commands need from the browser (``BrowserHost`` or a test fake)."""

    def has_session(self, agent_id: str) -> bool: ...
    def sessions(self) -> dict[str, SessionInfo]: ...
    def pace(self, key: str) -> Paced: ...
    def open_session(self, agent_id: str, viewport: ViewportSpec, downloads_dir: Path) -> None: ...
    def goto(self, agent_id: str, url: str) -> ActionOutcome: ...
    def click(self, agent_id: str, x: float, y: float) -> ActionOutcome: ...
    def hover(self, agent_id: str, x: float, y: float) -> ActionOutcome: ...
    def type_text(self, agent_id: str, text: str, *, enter: bool) -> ActionOutcome: ...
    def select(self, agent_id: str, mark_n: int, x: float, y: float, label: str) -> ActionOutcome: ...
    def describe(self, agent_id: str, x: float, y: float) -> dict[str, Any] | None: ...
    def press(self, agent_id: str, key: str) -> ActionOutcome: ...
    def scroll(self, agent_id: str, dy: float) -> ActionOutcome: ...
    def back(self, agent_id: str) -> ActionOutcome: ...
    def wait_for_change(self, agent_id: str, timeout_s: float) -> WaitOutcome: ...
    def set_viewport(self, agent_id: str, viewport: ViewportSpec) -> None: ...
    def capture(self, agent_id: str) -> Capture: ...
    def close(self, agent_id: str) -> bool: ...
    def shutdown(self) -> None: ...


# When a screenshot result says "Browsing <url>" to the operator.
Announce = Literal["always", "navigation", "never"]


@dataclass
class _AgentView:
    """One agent's sticky settings and latest screenshot facts."""

    window: ViewportSpec
    grid: GridStyle
    show_marks: bool
    # Zoom stack: each entry a region of the one before; empty = full page.
    zoom: list[Rect] = field(default_factory=list)
    zoom_path: list[int] = field(default_factory=list)
    # From the most recent screenshot: its viewport size and marks.
    viewport: tuple[int, int] | None = None
    marks: tuple[Mark, ...] = ()
    downloads: list[str] = field(default_factory=list)
    # The page URL of the latest screenshot, to tell when an action navigated.
    last_url: str | None = None
    # Where the latest screenshot's image sits on the page (for bv point).
    image: ViewGeometry | None = None
    # The mouse position bv point / click set, in page CSS px; None = unset.
    # Cleared when the page moves under it (see the bv point docs in handle).
    pointer: tuple[float, float] | None = None

    def current_rect(self) -> Rect:
        """The rect the keypad splits: the top of the zoom stack, or the viewport."""
        if self.zoom:
            return self.zoom[-1]
        if self.viewport is None:
            raise CommandError('NO_VIEW: run "bv view" first')
        return 0.0, 0.0, float(self.viewport[0]), float(self.viewport[1])

    def reset_zoom(self) -> None:
        self.zoom.clear()
        self.zoom_path.clear()


class CommandError(ValueError):
    """A usage or precondition error; the message goes to the agent as is."""


def routed_vision_model(agent: Agent) -> tuple[str | None, bool]:
    """Return the agent's routed model and whether it is flagged image-capable.

    An agent has one AI connection for every activation, so its model is the
    one CLI commands run on; None when the agent has no usable connection.
    """
    model = agent_model(agent)
    return model, model is not None and supports_images(model)


def agent_downloads_dir(agent: Agent) -> Path:
    """Return the real folder behind the agent's ``/me/downloads``.

    Raises:
        ValueError: ``/me`` did not resolve to a real folder.
    """
    resolved = resolve_cli_path(agent.storage_key, "/", DOWNLOADS_VIRTUAL_DIR)
    if resolved.real_path is None:
        raise ValueError(f"{DOWNLOADS_VIRTUAL_DIR} has no folder on disk")
    return resolved.real_path


class BrowserVisionCommands:
    """Runs ``bv`` for any number of agents.

    Args:
        defaults: Validated manifest defaults.
        host: The browser.
        shots: Where screenshots are written.
        markers: Where each open session's marker is kept (for the live view).
        cooldowns: Sites under a bot-check cooldown.
        setup_status: Reads the setup state (must be ``ready`` to run).
        vision_model: Returns ``(model, image_capable)`` for an agent.
        downloads_dir: Returns the real folder behind an agent's ``/me/downloads``.
    """

    def __init__(
        self,
        *,
        defaults: BrowserVisionDefaults,
        host: BrowserHostLike,
        shots: ScreenshotStore,
        markers: SessionMarkers,
        cooldowns: CooldownStore,
        setup_status: Callable[[], SetupStatus],
        vision_model: Callable[[Agent], tuple[str | None, bool]] = routed_vision_model,
        downloads_dir: Callable[[Agent], Path] = agent_downloads_dir,
    ) -> None:
        self._defaults = defaults
        self._host = host
        self._shots = shots
        self._markers = markers
        self._cooldowns = cooldowns
        self._setup_status = setup_status
        self._vision_model = vision_model
        self._downloads_dir = downloads_dir
        self._views: dict[str, _AgentView] = {}
        self._lock = threading.Lock()

    def shutdown(self) -> None:
        """Close every browser session, delete their screenshots, forget view settings.

        Only the sessions this process's host has open are ended: the app
        process and the runtime worker share the data dir, so files another
        process owns are not this one's to delete. The files are deleted even
        when closing the browser fails, since its sessions end either way.

        Raises:
            BrowserActionError: The browser did not close cleanly.
            OSError: A screenshot folder cannot be removed.
        """
        ended = self._host.sessions()
        with self._lock:
            self._views.clear()
        try:
            self._host.shutdown()
        finally:
            for agent_id, info in ended.items():
                self._end_session(agent_id, info.session_id)

    def state_line(self, agent_id: str) -> str:
        """The agent's real browser state, for the start of its turn.

        Read from the host's open sessions, which never starts the browser:
        before the browser thread exists nothing is open.

        Raises:
            BrowserActionError: The browser thread exists but did not answer.
        """
        session = self._host.sessions().get(agent_id)
        if session is None:
            return "Your browser right now: no page open."
        return f"Your browser right now: open at {short_url(session.url)} (window {session.window})."

    def handle(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        """Run one ``bv`` command for ``ctx.agent``.

        Preconditions, each an explicit error: setup is ``ready``
        (``SETUP_REQUIRED``) and the agent's routed model is flagged
        image-capable (``MODEL_CANNOT_SEE_IMAGES``). Usage errors, unknown
        marks, zoom limits, points outside the image and browser failures
        come back as error results the agent can recover from; nothing is
        clamped or retried silently.

        The pointer (``point``/``point1k``, and ``click`` on a mark or region)
        is drawn on every screenshot while set. ``open``, ``back``,
        ``scroll``, ``window`` and ``zoom out|reset`` clear it, and so does
        any screenshot whose page URL differs from the previous one.
        """
        status = self._setup_status()
        if status.state != "ready":
            return self._error(ctx, parsed, f"SETUP_REQUIRED: the browser is not set up ({status.state}); the boss can set it up in Add → Extensions")
        model, can_see = self._vision_model(ctx.agent)
        if not can_see:
            return self._error(ctx, parsed, f"MODEL_CANNOT_SEE_IMAGES: {model or 'no model configured'}")
        if not parsed.args:
            return self._error(ctx, parsed, _USAGE)
        sub, args = parsed.args[0].lower(), parsed.args[1:]
        runner = {
            "open": self._open,
            "view": self._view,
            "zoom": self._zoom,
            "point": self._point,
            "point1k": self._point1k,
            "window": self._window,
            "click": self._click,
            "type": self._type,
            "select": self._select,
            "key": self._key,
            "scroll": self._scroll,
            "back": self._back,
            "wait": self._wait,
            "status": self._status,
            "close": self._close,
        }.get(sub)
        if runner is None:
            return self._error(ctx, parsed, _USAGE)
        try:
            return runner(ctx, parsed, args, body)
        except BrowserActionError as exc:
            return self._error(ctx, parsed, f"BROWSER_ERROR: {exc}")
        except (CommandError, ZoomLimit) as exc:
            return self._error(ctx, parsed, str(exc))
        except CooldownStoreError as exc:
            # Before ValueError (its base): this is a broken file, not an argument.
            return self._error(ctx, parsed, f"COOLDOWN_FILE_UNREADABLE: {exc}")
        except ValueError as exc:
            # The keypad/colour/viewport parsers raise ValueError naming the accepted form.
            return self._error(ctx, parsed, f"INVALID_ARGUMENT: {exc}")

    # ── subcommands ────────────────────────────────────────────────────────

    def _open(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1:
            raise CommandError("USAGE: bv open <url>")
        url = normalize_url(args[0])
        # The key of the URL asked for: a wall may be served from another
        # host (ratelimited.redfin.com), but it is this site that blocked.
        site = site_key(url)
        paced = self._gate(site)
        view = self._view_for(ctx.agent.id)
        view.pointer = None
        self._host.open_session(ctx.agent.id, view.window, self._downloads_dir(ctx.agent))
        session = self._host.sessions().get(ctx.agent.id)
        if session is None:
            raise BrowserActionError("the browser session did not open")
        self._record_session(ctx.agent.id, session)
        try:
            outcome = self._host.goto(ctx.agent.id, url)
        except BrowserActionError as exc:
            reason = _truncate(str(exc).strip().splitlines()[0] if str(exc).strip() else "navigation failed")
            failed = self._error(ctx, parsed, f"BROWSER_ERROR: {exc}")
            return _with_status_lines(failed, [f"Couldn't open {short_url(url)} — {reason}"])
        return self._after_action(ctx, parsed, view, outcome, notes=paced, announce="always", wall_site=site)

    def _view(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        flags = _parse_flags(args, _VIEW_FLAGS, "bv view [--marks on|off] [--grid on|off] [--grid-color auto|#rrggbb] [--grid-opacity 0-1]")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        grid = view.grid
        if "--grid" in flags:
            grid = dataclasses.replace(grid, enabled=_parse_on_off(flags["--grid"], "--grid"))
        if "--grid-color" in flags:
            grid = dataclasses.replace(grid, color=parse_color(flags["--grid-color"]))
        if "--grid-opacity" in flags:
            grid = dataclasses.replace(grid, opacity=parse_opacity(flags["--grid-opacity"]))
        if "--marks" in flags:
            view.show_marks = _parse_on_off(flags["--marks"], "--marks")
        view.grid = grid
        # A view keeps the zoom: it re-captures the same rect.
        return self._screenshot(ctx, parsed, view, ActionOutcome(), notes=[])

    def _zoom(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        usage = "USAGE: bv zoom <1-9> [<1-9> …] | bv zoom out | bv zoom reset"
        if not args:
            raise CommandError(usage)
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        if len(args) == 1 and args[0].lower() == "reset":
            view.reset_zoom()
            view.pointer = None
        elif len(args) == 1 and args[0].lower() == "out":
            if not view.zoom:
                raise CommandError("ZOOM_OUT: already showing the full page")
            view.zoom.pop()
            view.zoom_path.pop()
            view.pointer = None
        else:
            digits = [parse_keypad_digit(arg) for arg in args]
            # Validate the whole chain before changing the view.
            rect = view.current_rect()
            chain: list[Rect] = []
            for digit in digits:
                rect = zoom_into(rect, digit)
                chain.append(rect)
            view.zoom.extend(chain)
            view.zoom_path.extend(digits)
        return self._screenshot(ctx, parsed, view, ActionOutcome(), notes=[])

    def _window(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1:
            raise CommandError(f"USAGE: bv window {'|'.join(self._defaults.window_presets)}|<W>x<H>")
        window = resolve_viewport(args[0], self._defaults.window_presets)
        view = self._view_for(ctx.agent.id)
        view.window = window
        view.pointer = None
        if not self._host.has_session(ctx.agent.id):
            return success_result(
                command=parsed.raw,
                detail=f"Browser Vision: window {window.name}",
                kind="browser",
                data={"window": window.name, "status_lines": []},
                sections=[("BROWSER", [f"window set to {window.name}; it applies when you run bv open"])],
                cwd=ctx.cwd,
            )
        self._host.set_viewport(ctx.agent.id, window)
        return self._after_action(ctx, parsed, view, ActionOutcome(), notes=[WINDOW_SWAP_NOTE])

    def _point(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        return self._point_at(ctx, parsed, args, thousandths=False)

    def _point1k(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        return self._point_at(ctx, parsed, args, thousandths=True)

    def _point_at(
        self,
        ctx: CliExecutionContext,
        parsed: ParsedCliCommand,
        args: tuple[str, ...],
        *,
        thousandths: bool,
    ) -> BossModCliResult:
        """``point``/``point1k``: move the mouse to a spot on the latest image.

        The two differ only in units: image pixels, or 0–1000 of the image's
        width and height. The view (zoom) is kept.
        """
        name = "point1k" if thousandths else "point"
        if len(args) != 2:
            raise CommandError(f"USAGE: bv {name} <x> <y>")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        if view.image is None:
            raise CommandError('NO_VIEW: run "bv view" first')
        image = view.image
        px, py = parse_point(args[0], args[1], image, thousandths=thousandths)
        x, y = image_to_page(image, px, py)
        outcome = self._host.hover(ctx.agent.id, x, y)
        view.pointer = (x, y)
        hovering = hover_line(self._host.describe(ctx.agent.id, x, y))
        kx, ky = px * _POINT1K_MAX / image.width, py * _POINT1K_MAX / image.height
        line = f"pointer: ({px:.0f}, {py:.0f}) px = ({kx:.0f}, {ky:.0f})‰ — {hovering}"
        return self._screenshot(ctx, parsed, view, outcome, notes=[line])

    def _click(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) > 1:
            raise CommandError(
                "USAGE: bv click @<n> (a mark) | bv click <1-9> (a keypad region of the current view)"
                " | bv click (where the mouse is)"
            )
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        region_note: str | None = None
        if not args:
            if view.pointer is None:
                raise CommandError('NO_POINTER: run "bv point x y" first')
            x, y = view.pointer
        else:
            target = args[0]
            if _MARK_RE.match(target):
                x, y = self._mark(view, target).point
            else:
                digit = parse_keypad_digit(target)
                x, y = region_center(view.current_rect(), digit)
                if not view.zoom and not view.grid.enabled:
                    # The regions exist whether or not the grid is drawn.
                    region_note = f"keypad region {digit} of the full page (the 3×3 grid is hidden; its regions still apply)"
            # The next screenshot shows where this click went.
            view.pointer = (x, y)
        feedback = feedback_line(self._host.describe(ctx.agent.id, x, y))
        site = self._current_site(ctx.agent.id)
        paced = self._gate(site)
        outcome = self._host.click(ctx.agent.id, x, y)
        notes = [f"{feedback} at ({x:.0f}, {y:.0f})"]
        if region_note is not None:
            notes.append(region_note)
        return self._after_action(
            ctx, parsed, view, outcome, notes=notes + paced, announce="navigation", clicked=feedback, wall_site=site,
        )

    def _type(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        target, text = parse_type_args(args, body)
        enter = "--enter" in args
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        notes: list[str] = []
        clicked: str | None = None
        mark = self._mark(view, target) if target is not None else None
        # Only a submit (--enter) can reach the network; plain typing is local.
        site = self._current_site(ctx.agent.id) if enter else None
        paced = self._gate(site)
        if mark is not None:
            x, y = mark.point
            clicked = feedback_line(self._host.describe(ctx.agent.id, x, y))
            notes.append(f"{clicked} at ({x:.0f}, {y:.0f})")
            self._host.click(ctx.agent.id, x, y)
        outcome = self._host.type_text(ctx.agent.id, text, enter=enter)
        notes.append(f"typed {len(text)} characters")
        return self._after_action(
            ctx, parsed, view, outcome, notes=notes + paced, announce="navigation", clicked=clicked, wall_site=site,
        )

    def _select(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) < 2 or not _MARK_RE.match(args[0]):
            raise CommandError("USAGE: bv select @<n> <option text>")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        mark = self._mark(view, args[0])
        if mark.kind != "select":
            raise CommandError(f"NOT_A_SELECT: @{mark.n} is a {mark.kind}; use bv click or bv type for it")
        option = " ".join(args[1:])
        site = self._current_site(ctx.agent.id)
        paced = self._gate(site)
        outcome = self._host.select(ctx.agent.id, mark.n, mark.point[0], mark.point[1], option)
        return self._after_action(
            ctx, parsed, view, outcome, notes=[f'selected "{option}" in @{mark.n}', *paced], announce="navigation",
            wall_site=site,
        )

    def _key(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1:
            raise CommandError("USAGE: bv key <Key or combo>, e.g. Enter, Tab, Control+A")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        site = self._current_site(ctx.agent.id)
        paced = self._gate(site)
        outcome = self._host.press(ctx.agent.id, args[0])
        return self._after_action(
            ctx, parsed, view, outcome, notes=[f"pressed {args[0]}", *paced], announce="navigation", wall_site=site,
        )

    def _scroll(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1 or args[0].lower() not in {"up", "down"}:
            raise CommandError("USAGE: bv scroll up|down")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        if view.viewport is None:
            raise CommandError('NO_VIEW: run "bv view" first')
        distance = self._defaults.scroll_fraction * view.viewport[1]
        dy = distance if args[0].lower() == "down" else -distance
        view.pointer = None
        outcome = self._host.scroll(ctx.agent.id, dy)
        return self._after_action(ctx, parsed, view, outcome, notes=[f"scrolled {args[0].lower()} {abs(dy):g}px"])

    def _back(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if args:
            raise CommandError("USAGE: bv back")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        site = self._current_site(ctx.agent.id)
        paced = self._gate(site)
        view.pointer = None
        outcome = self._host.back(ctx.agent.id)
        return self._after_action(
            ctx, parsed, view, outcome, notes=["went back", *paced], announce="navigation", wall_site=site,
        )

    def _wait(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        """``wait [<seconds>]``: watch the page until it changes and settles, then screenshot.

        For single-page apps that show a spinner instead of loading a new
        page. The default comes from the manifest (``wait_default_s``);
        there is no maximum (D12).
        """
        if len(args) > 1:
            raise CommandError("USAGE: bv wait [<seconds>]")
        seconds = parse_wait_seconds(args[0]) if args else self._defaults.wait_default_s
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        waited = self._host.wait_for_change(ctx.agent.id, seconds)
        return self._after_action(
            ctx, parsed, view, ActionOutcome(downloads=waited.downloads), notes=[wait_line(waited)], announce="navigation",
        )

    def _status(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        view = self._view_for(ctx.agent.id)
        lines: list[str] = []
        if self._host.has_session(ctx.agent.id):
            capture = self._host.capture(ctx.agent.id)
            lines += [f"url: {capture.url}", f"title: {capture.title}", f"viewport: {capture.width}x{capture.height}"]
        else:
            lines.append('no page open; run "bv open <url>"')
        lines += [
            f"window: {view.window.name}",
            _view_line(view),
            f"marks: {'on' if view.show_marks else 'off'}",
            f"grid: {'on' if view.grid.enabled else 'off'}, colour {_color_text(view.grid.color)}, opacity {view.grid.opacity:g}",
        ]
        lines += [f"downloaded: {item}" for item in view.downloads] or ["downloads this session: none"]
        return success_result(
            command=parsed.raw,
            detail="Browser Vision: status",
            kind="browser",
            data={"window": view.window.name, "marks": view.show_marks, "grid": view.grid.enabled, "status_lines": []},
            sections=[("BROWSER", lines)],
            cwd=ctx.cwd,
        )

    def _close(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        session = self._host.sessions().get(ctx.agent.id)
        closed = self._host.close(ctx.agent.id)
        with self._lock:
            self._views.pop(ctx.agent.id, None)
        if session is not None:
            self._end_session(ctx.agent.id, session.session_id)
        return success_result(
            command=parsed.raw,
            detail="Browser Vision: close",
            kind="browser",
            data={"closed": closed, "status_lines": ["Closed the browser"] if closed else []},
            sections=[("BROWSER", ["browser session closed" if closed else "no browser session was open"])],
            cwd=ctx.cwd,
        )

    # ── helpers ────────────────────────────────────────────────────────────

    def _record_session(self, agent_id: str, session: SessionInfo) -> None:
        """Write the session's marker: this process holds it open, at its current URL."""
        self._markers.write(agent_id, SessionMarker(
            session_id=session.session_id,
            pid=os.getpid(),
            opened_at=session.opened_at,
            url=session.url,
        ))

    def _end_session(self, agent_id: str, session_id: str) -> None:
        """A session ended: its screenshots and marker go, so nothing shows its page as current."""
        self._markers.remove(agent_id)
        self._shots.delete_session(agent_id, session_id)

    def _current_site(self, agent_id: str) -> str | None:
        """The site key of the agent's open page (``None`` for about:blank and the like).

        Raises:
            CommandError: ``NO_PAGE`` when the agent has no session.
        """
        session = self._host.sessions().get(agent_id)
        if session is None:
            raise CommandError('NO_PAGE: run "bv open <url>" first')
        return site_key(session.url)

    def _gate(self, site: str | None) -> list[str]:
        """Let an action that can reach ``site`` go ahead: cooldown first, then pacing.

        A page with no site (``None``: about:blank, file:, data:) reaches no
        server, so it is neither refused nor paced.

        Returns:
            The result note for a noticeable pacing wait, e.g.
            ``paced 2.8s for redfin.com (polite browsing)``, else nothing.

        Raises:
            CommandError: ``SITE_COOLDOWN`` while the site (or a parent
                site) is on a bot-check cooldown; nothing was loaded.
            CooldownStoreError: The cooldown file is unreadable.
        """
        if site is None:
            return []
        cooldown = self._cooldowns.active(site)
        if cooldown is not None:
            raise CommandError(
                f"SITE_COOLDOWN: {cooldown.site} showed a bot check at {_local_hhmm(cooldown.blocked_at)}; "
                f"it can be tried again after {_local_hhmm(cooldown.until)} (local time)"
            )
        paced = self._host.pace(site)
        if paced.waited_s <= _PACED_NOTE_MIN_S:
            return []
        return [f"paced {paced.waited_s:.1f}s for {paced.site} (polite browsing)"]

    def _view_for(self, agent_id: str) -> _AgentView:
        with self._lock:
            view = self._views.get(agent_id)
            if view is None:
                defaults = self._defaults
                view = _AgentView(
                    window=resolve_viewport(defaults.window_default, defaults.window_presets),
                    grid=GridStyle(
                        enabled=defaults.grid_default,
                        color=parse_color(defaults.grid_color_default),
                        opacity=defaults.grid_opacity_default,
                    ),
                    show_marks=defaults.marks_default,
                )
                self._views[agent_id] = view
            return view

    def _require_page(self, agent_id: str) -> None:
        if not self._host.has_session(agent_id):
            raise CommandError('NO_PAGE: run "bv open <url>" first')

    def _mark(self, view: _AgentView, token: str) -> Mark:
        """Resolve ``@n`` against the most recent screenshot's marks.

        Raises:
            CommandError: ``NO_VIEW`` before any screenshot, ``MARK_NOT_FOUND``
                (with the valid range) for an unknown number.
        """
        if view.viewport is None:
            raise CommandError('NO_VIEW: run "bv view" first')
        match = _MARK_RE.match(token)
        number = int(match.group(1)) if match else -1
        found = next((mark for mark in view.marks if mark.n == number), None)
        if found is None:
            valid = f"@1–@{len(view.marks)}" if view.marks else "none (no marks on the latest screenshot)"
            raise CommandError(f"MARK_NOT_FOUND: {token} is not on the latest screenshot; valid marks: {valid}")
        return found

    def _after_action(
        self,
        ctx: CliExecutionContext,
        parsed: ParsedCliCommand,
        view: _AgentView,
        outcome: ActionOutcome,
        *,
        notes: list[str],
        announce: Announce = "never",
        clicked: str | None = None,
        wall_site: str | None = None,
    ) -> BossModCliResult:
        """An action changed the page: back to the full view, then screenshot."""
        view.reset_zoom()
        return self._screenshot(
            ctx, parsed, view, outcome, notes=notes, announce=announce, clicked=clicked, wall_site=wall_site,
        )

    def _screenshot(
        self,
        ctx: CliExecutionContext,
        parsed: ParsedCliCommand,
        view: _AgentView,
        outcome: ActionOutcome,
        *,
        notes: list[str],
        announce: Announce = "never",
        clicked: str | None = None,
        wall_site: str | None = None,
    ) -> BossModCliResult:
        """Capture, render, store and describe the current view.

        ``wall_site`` is set after an action that can navigate: the site the
        action was aimed at. If this capture is a bot check (``botwall``),
        the result leads with a ``BLOCKED`` line, the operator's status line
        says so instead of ``Browsing``, and ``wall_site`` goes on cooldown.

        ``announce`` decides the operator status lines (``data["status_lines"]``,
        posted by core like task lines): ``"always"`` says ``Browsing <url>``
        (open), ``"navigation"`` says it only when the page URL differs from
        the previous screenshot's, ``"never"`` says nothing (view, zoom,
        scroll, window). Each saved download adds a ``Downloaded`` line.

        The result's ``summary`` is one line standing in for the full text
        once a newer screenshot supersedes this one: the command, the short
        URL, and ``clicked`` (the click feedback) and downloads when present.
        """
        capture = self._host.capture(ctx.agent.id)
        previous_url = view.last_url
        if capture.url != previous_url:
            # The page moved under the pointer; it no longer points at anything.
            view.pointer = None
        view.viewport = (capture.width, capture.height)
        view.marks = capture.marks
        # Zooming exists only to use the keypad, so a zoomed view always draws
        # it; the full page follows the sticky --grid setting.
        style = dataclasses.replace(view.grid, enabled=True) if view.zoom else view.grid
        rendered = render_view(
            capture.png,
            view.viewport,
            view.zoom[-1] if view.zoom else None,
            style,
            capture.marks,
            show_marks=view.show_marks,
            image_max_px=self._defaults.image_max_px,
            label=LabelStyle(
                font_ratio=self._defaults.label_font_ratio,
                font_min_px=self._defaults.label_font_min_px,
                font_max_px=self._defaults.label_font_max_px,
                opacity=self._defaults.label_opacity,
            ),
            keypad_font_px=self._defaults.keypad_font_px,
            pointer=view.pointer,
        )
        view.image = rendered.geometry
        view_line = _view_line(view)
        path = self._shots.store(
            ctx.agent.id,
            capture.session.session_id,
            rendered.png,
            ShotMeta(
                command=parsed.raw,
                url=capture.url,
                title=capture.title,
                window=f"{view.window.name} {capture.width}x{capture.height}",
                view=view_line,
                image=f"{rendered.width}x{rendered.height}",
                marks=len(capture.marks),
                taken_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._record_session(ctx.agent.id, capture.session)

        blocked = self._check_wall(capture, wall_site)
        lines = [] if blocked is None else [blocked]
        lines += [
            f"url: {capture.url}",
            f"title: {capture.title}",
            f"viewport: {capture.width}x{capture.height}",
            f"window: {view.window.name} {capture.width}x{capture.height}",
            view_line,
        ]
        # Always stated: bv point coordinates are pixels of this image.
        image_line = f"image {rendered.width}x{rendered.height}"
        lines.append(image_line if rendered.scale == 1 else f"{image_line} (scale ×{rendered.scale:.4g})")
        lines.append(_overlays_line(view))
        lines.append(f"marks: {len(capture.marks)}" + ("" if view.show_marks else " (hidden on the image)"))
        lines += [legend_line(mark) for mark in capture.marks]
        lines += notes
        status_lines: list[str] = []
        if blocked is not None:
            status_lines.append(f"Blocked by {wall_site}'s bot check")
        elif announce == "always" or (announce == "navigation" and capture.url != previous_url):
            status_lines.append(f"Browsing {short_url(capture.url)}")
        view.last_url = capture.url
        summary = f"{parsed.raw} → {short_url(capture.url)}"
        if clicked is not None:
            summary += f"; {clicked}"
        for download in outcome.downloads:
            if download.error is None:
                entry = f"{DOWNLOADS_VIRTUAL_DIR}/{download.file_name} ({download.size} bytes)"
                view.downloads.append(entry)
                lines.append(f"downloaded: {entry}")
                status_lines.append(f"Downloaded {download.file_name} to {DOWNLOADS_VIRTUAL_DIR}")
                summary += f"; downloaded {download.file_name}"
            else:
                lines.append(f"download failed: {download.error}")

        result = success_result(
            command=parsed.raw,
            detail=f"Browser Vision: {capture.url}",
            kind="browser",
            data={
                "url": capture.url,
                "title": capture.title,
                "view": view_line,
                "marks": len(capture.marks),
                "screenshot": str(path),
                "status_lines": status_lines,
            },
            sections=[("BROWSER", lines)],
            cwd=ctx.cwd,
        )
        return dataclasses.replace(result, image_paths=(str(path),), summary=summary)

    def _check_wall(self, capture: Capture, wall_site: str | None) -> str | None:
        """Return the ``BLOCKED`` line when ``capture`` is a bot check, putting the site on cooldown.

        Raises:
            CooldownStoreError: The cooldown file is unreadable.
            OSError: The cooldown file cannot be written.
        """
        if wall_site is None:
            return None
        rules = self._defaults.botwall
        reason = detect(capture.nav_status, list(capture.frame_urls), capture.title, capture.text, rules)
        if reason is None:
            return None
        self._cooldowns.block(wall_site, rules.cooldown_minutes)
        return (
            f"BLOCKED: {wall_site} is showing a bot check ({reason}). Stop browsing this site and tell "
            "the boss; do not retry or switch tools to get around it."
        )

    def _error(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, message: str) -> BossModCliResult:
        return error_result(parsed.raw, message, cwd=ctx.cwd)


def _view_line(view: _AgentView) -> str:
    """``view: full page`` or ``view: zoom 5 › 3 — region W×H px at (x, y)``."""
    if not view.zoom:
        return "view: full page"
    x0, y0, x1, y1 = view.zoom[-1]
    path = " › ".join(str(digit) for digit in view.zoom_path)
    return f"view: zoom {path} — region {x1 - x0:.0f}×{y1 - y0:.0f} px at ({x0:.0f}, {y0:.0f})"


def parse_wait_seconds(raw: str) -> float:
    """Parse ``bv wait``'s seconds: any positive, finite number.

    Raises:
        ValueError: Not a positive number; ``handle`` reports it as
            ``INVALID_ARGUMENT`` naming the form.
    """
    message = f"bv wait [<seconds>]: seconds must be a positive number, got {raw!r}"
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(message) from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(message)
    return value


def wait_line(outcome: WaitOutcome) -> str:
    """The result line for a ``bv wait``: what changed and whether it settled."""
    if not outcome.changed:
        return f"wait: no change after {outcome.elapsed_s:.1f}s"
    state = "settled" if outcome.settled else "still changing"
    return f"wait: page changed after {outcome.elapsed_s:.1f}s ({state})"


def parse_type_args(args: tuple[str, ...], body: str | None) -> tuple[str | None, str]:
    """Split ``bv type [@n] [--enter] [<text …>]`` into its mark and its text.

    ``--enter`` may appear anywhere and is not part of the text. A leading
    ``@n`` (after dropping ``--enter``) is the mark to click first. The text
    is the command body when there is one, otherwise the remaining args
    joined with single spaces. The same text in both places (equal once
    runs of whitespace are collapsed) is typed once, as the body gives it.

    Args:
        args: The tokens after ``type``.
        body: The command body, if any.

    Returns:
        ``(target, text)``: the ``@n`` token or ``None``, and the text to type.

    Raises:
        CommandError: Different text given in the body and after the
            command, or text given nowhere.
    """
    rest = [arg for arg in args if arg != "--enter"]
    target = rest.pop(0) if rest and _MARK_RE.match(rest[0]) else None
    inline = " ".join(rest)
    # None and "" both mean no body.
    in_body = body or ""
    if in_body and inline and " ".join(in_body.split()) != " ".join(inline.split()):
        raise CommandError(f"{_TYPE_USAGE} — give the text either in the body or after the command, not both")
    if not in_body and not inline:
        raise CommandError("USAGE: bv type needs the text to type, in the body or after the command")
    return target, in_body or inline


def _overlays_line(view: _AgentView) -> str:
    """What is drawn on this screenshot and how to change it (one line)."""
    marks = "marks on" if view.show_marks else "marks off (bv view --marks on to show them)"
    if view.zoom:
        grid = "3×3 grid shown while zoomed (bv zoom reset for the full page)"
    elif view.grid.enabled:
        grid = "3×3 grid on (bv view --grid off to hide it)"
    else:
        grid = "3×3 grid off (bv view --grid on to add it)"
    return f"overlays: {marks} · {grid}"


def parse_point(raw_x: str, raw_y: str, image: ViewGeometry, *, thousandths: bool) -> tuple[float, float]:
    """Parse ``bv point``/``point1k`` coordinates into image px of ``image``.

    ``point`` takes image pixels, 0–(W-1) across and 0–(H-1) down from the
    top-left. ``point1k`` takes 0–1000 of the width and height: ``k * W /
    1000``, so 0,0 is the top-left corner and 1000,1000 the bottom-right one.

    Raises:
        CommandError: ``POINT_OUT_OF_VIEW`` naming the valid range for the
            command's units, for a non-number or an out-of-range value.
    """
    if thousandths:
        valid = f"POINT_OUT_OF_VIEW: x must be 0–{_POINT1K_MAX}, y 0–{_POINT1K_MAX} (bv point1k scale)"
        limits = (float(_POINT1K_MAX), float(_POINT1K_MAX))
    else:
        valid = (
            f"POINT_OUT_OF_VIEW: x must be 0–{image.width - 1}, y 0–{image.height - 1} "
            f"(pixels of the latest screenshot, image {image.width}x{image.height})"
        )
        limits = (float(image.width - 1), float(image.height - 1))
    values: list[float] = []
    for raw, limit in zip((raw_x, raw_y), limits):
        try:
            value = float(raw)
        except ValueError:
            raise CommandError(f"{valid}; got {raw!r}") from None
        if not math.isfinite(value) or not 0 <= value <= limit:
            raise CommandError(f"{valid}; got {raw!r}")
        values.append(value)
    x, y = values
    if thousandths:
        return x * image.width / _POINT1K_MAX, y * image.height / _POINT1K_MAX
    return x, y


def short_url(url: str) -> str:
    """Return ``url`` for an operator line: no ``scheme://``, no trailing ``/``, ≤ 80 chars.

    A URL without ``://`` (``about:blank``, ``data:…``) keeps its scheme, which
    is the only part that says what it is.
    """
    text = url.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    return _truncate(text.rstrip("/") or text)


def _local_hhmm(moment: datetime) -> str:
    """``HH:MM`` of an aware time in this machine's local zone (what the operator reads)."""
    return moment.astimezone().strftime("%H:%M")


def _truncate(text: str) -> str:
    return text if len(text) <= _STATUS_MAX_CHARS else text[: _STATUS_MAX_CHARS - 1] + "…"


def _with_status_lines(result: BossModCliResult, lines: list[str]) -> BossModCliResult:
    return dataclasses.replace(result, data={**(result.data or {}), "status_lines": lines})


def normalize_url(raw: str) -> str:
    """Return the URL to navigate to for ``bv open``.

    Only the form is checked (D8): the browser's own navigation error is the
    authority on whether a URL loads.

    - A value with an explicit scheme (``file:``, ``about:``, ``data:``,
      ``http:`` …) is passed through unchanged.
    - ``<name>:<digits>`` (optionally followed by ``/…``) is a host with a
      port, not a scheme: ``localhost:3000`` is scheme-less.
    - A scheme-less value gets ``https://`` and must then have a host.

    Raises:
        CommandError: Empty input, or scheme-less input with no host.
    """
    text = raw.strip()
    if not text:
        raise CommandError("INVALID_URL: empty URL (e.g. https://example.com)")
    if _SCHEME_RE.match(text) and not _HOST_PORT_RE.match(text):
        return text
    url = f"https://{text}"
    if not urlsplit(url).hostname:
        raise CommandError(f"INVALID_URL: {raw!r} is not a URL (e.g. https://example.com)")
    return url


def _parse_flags(args: tuple[str, ...], allowed: set[str], usage: str) -> dict[str, str]:
    """Parse ``--flag value`` pairs; every flag takes exactly one value."""
    flags: dict[str, str] = {}
    index = 0
    while index < len(args):
        name = args[index]
        if name not in allowed:
            raise CommandError(f"USAGE: {usage} (unknown argument {name!r})")
        if index + 1 >= len(args):
            raise CommandError(f"USAGE: {usage} ({name} needs a value)")
        flags[name] = args[index + 1]
        index += 2
    return flags


def _parse_on_off(raw: str, flag: str) -> bool:
    value = raw.strip().lower()
    if value not in {"on", "off"}:
        raise CommandError(f"USAGE: {flag} on|off, got {raw!r}")
    return value == "on"


def _color_text(color: GridColor) -> str:
    if color == "auto":
        return "auto"
    return "#{:02x}{:02x}{:02x}".format(*color)
