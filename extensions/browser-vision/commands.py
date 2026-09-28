"""Browser Vision — the ``bv`` subcommands.

Parses ``bv <sub> …``, keeps each agent's sticky view settings, drives the
browser host, draws the grid and returns a CLI result that carries the
screenshot path for the model.

Active grid rule: click numbers always refer to the most recent screenshot.
Every screenshot registers its grid (density, cols, rows) as the agent's
active grid, and ``bv click <n>`` resolves against it.
"""

from __future__ import annotations

import dataclasses
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path
from core.extensions.contract import SetupStatus
from core.llm.routing import select_model_with_source
from core.models import Agent
from db.model_capabilities import supports_images

from .browser_host import ActionOutcome, BrowserActionError, Capture
from .grid import (
    CellOutOfRange,
    FocusSpec,
    GridColor,
    GridSpec,
    GridStyle,
    LabelStyle,
    focus_rect,
    parse_color,
    parse_density,
    parse_focus,
    parse_opacity,
    render_grid,
)
from .screenshots import ScreenshotStore, ShotMeta
from .viewports import ViewportSpec, WindowPreset, resolve_viewport

DOWNLOADS_VIRTUAL_DIR = "/me/downloads"
UNLABELLED_NOTICE = "cells too small to label at this size; add/narrow --focus"
WINDOW_SWAP_NOTE = (
    "window changed: cookies and site storage were kept; "
    "form contents and scroll position were reset"
)
# An RFC 3986 scheme followed by ':'.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
# host:port, optionally with a path — looks like "scheme:" but is not one.
_HOST_PORT_RE = re.compile(r"^[A-Za-z0-9.-]+:\d+(?:/.*)?$")
_VIEW_FLAGS = {"--density", "--grid", "--grid-color", "--grid-opacity", "--focus"}


class BrowserVisionDefaults(BaseModel):
    """The manifest's ``defaults`` block, validated when the extension starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    window_default: str
    window_presets: dict[str, WindowPreset]
    density_default: int = Field(gt=0)
    label_min_px: int = Field(gt=0)
    label_font_ratio: float = Field(gt=0)
    label_font_min_px: int = Field(gt=0)
    label_opacity: float = Field(ge=0, le=1)
    image_max_px: int = Field(gt=0)
    grid_default: bool
    grid_color_default: str
    grid_opacity_default: float = Field(ge=0, le=1)
    nav_timeout_ms: int = Field(gt=0)
    action_timeout_ms: int = Field(gt=0)
    download_timeout_ms: int = Field(gt=0)
    settle_ms: int = Field(ge=0)
    screenshots_keep: int = Field(ge=1)
    scroll_fraction: float = Field(gt=0)

    @field_validator("grid_color_default")
    @classmethod
    def _color_parses(cls, value: str) -> str:
        parse_color(value)
        return value

    @model_validator(mode="after")
    def _default_window_exists(self) -> "BrowserVisionDefaults":
        if self.window_default not in self.window_presets:
            raise ValueError(f"window_default {self.window_default!r} is not a window preset")
        return self


class BrowserHostLike(Protocol):
    """What the commands need from the browser (``BrowserHost`` or a test fake)."""

    def has_session(self, agent_id: str) -> bool: ...
    def open_session(self, agent_id: str, viewport: ViewportSpec, downloads_dir: Path) -> None: ...
    def goto(self, agent_id: str, url: str) -> ActionOutcome: ...
    def click(self, agent_id: str, x: float, y: float) -> ActionOutcome: ...
    def type_text(self, agent_id: str, text: str, *, enter: bool) -> ActionOutcome: ...
    def press(self, agent_id: str, key: str) -> ActionOutcome: ...
    def scroll(self, agent_id: str, dy: float) -> ActionOutcome: ...
    def back(self, agent_id: str) -> ActionOutcome: ...
    def set_viewport(self, agent_id: str, viewport: ViewportSpec) -> None: ...
    def capture(self, agent_id: str) -> Capture: ...
    def close(self, agent_id: str) -> bool: ...
    def shutdown(self) -> None: ...


@dataclass
class _AgentView:
    """One agent's sticky settings for its browser session."""

    window: ViewportSpec
    density: int
    grid: GridStyle
    active: GridSpec | None = None
    downloads: list[str] = field(default_factory=list)


class CommandError(ValueError):
    """A usage or precondition error; the message goes to the agent as is."""


def routed_vision_model(agent: Agent) -> tuple[str | None, bool]:
    """Return the agent's routed model and whether it is flagged image-capable.

    Agents run CLI commands on turns routed in ``work`` mode: routing picks
    ``work`` for every trigger except ``social``.
    """
    model, _source = select_model_with_source(agent, "work")
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
        setup_status: Callable[[], SetupStatus],
        vision_model: Callable[[Agent], tuple[str | None, bool]] = routed_vision_model,
        downloads_dir: Callable[[Agent], Path] = agent_downloads_dir,
    ) -> None:
        self._defaults = defaults
        self._host = host
        self._shots = shots
        self._setup_status = setup_status
        self._vision_model = vision_model
        self._downloads_dir = downloads_dir
        self._views: dict[str, _AgentView] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Forget every agent's sticky settings (the browser was shut down)."""
        with self._lock:
            self._views.clear()

    def handle(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        """Run one ``bv`` command for ``ctx.agent``.

        Preconditions, each an explicit error: setup is ``ready``
        (``SETUP_REQUIRED``) and the agent's routed model is flagged
        image-capable (``MODEL_CANNOT_SEE_IMAGES``). Usage errors, cells off
        the grid and browser failures come back as error results the agent
        can recover from; nothing is clamped or retried silently.
        """
        status = self._setup_status()
        if status.state != "ready":
            return self._error(ctx, parsed, f"SETUP_REQUIRED: the browser is not set up ({status.state}); the operator can set it up in Add → Extensions")
        model, can_see = self._vision_model(ctx.agent)
        if not can_see:
            return self._error(ctx, parsed, f"MODEL_CANNOT_SEE_IMAGES: {model or 'no model configured'}")
        if not parsed.args:
            return self._usage(ctx, parsed)
        sub, args = parsed.args[0].lower(), parsed.args[1:]
        runner = {
            "open": self._open,
            "view": self._view,
            "window": self._window,
            "click": self._click,
            "type": self._type,
            "key": self._key,
            "scroll": self._scroll,
            "back": self._back,
            "status": self._status,
            "close": self._close,
        }.get(sub)
        if runner is None:
            return self._usage(ctx, parsed)
        try:
            return runner(ctx, parsed, args, body)
        except BrowserActionError as exc:
            return self._error(ctx, parsed, f"BROWSER_ERROR: {exc}")
        except (CommandError, CellOutOfRange) as exc:
            return self._error(ctx, parsed, str(exc))
        except ValueError as exc:
            # The grid/viewport parsers raise ValueError naming the accepted form.
            return self._error(ctx, parsed, f"INVALID_ARGUMENT: {exc}")

    # ── subcommands ────────────────────────────────────────────────────────

    def _open(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1:
            raise CommandError("USAGE: bv open <url>")
        url = normalize_url(args[0])
        view = self._view_for(ctx.agent.id)
        self._host.open_session(ctx.agent.id, view.window, self._downloads_dir(ctx.agent))
        outcome = self._host.goto(ctx.agent.id, url)
        return self._full_view(ctx, parsed, view, outcome, notes=[])

    def _view(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        flags = _parse_flags(args, _VIEW_FLAGS, "bv view [--density N] [--grid on|off] [--grid-color auto|#rrggbb] [--grid-opacity 0-1] [--focus <a>-<b>]")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        density = parse_density(flags["--density"]) if "--density" in flags else None
        focus = parse_focus(flags["--focus"]) if "--focus" in flags else None
        grid = view.grid
        if "--grid" in flags:
            grid = dataclasses.replace(grid, enabled=_parse_on_off(flags["--grid"]))
        if "--grid-color" in flags:
            grid = dataclasses.replace(grid, color=parse_color(flags["--grid-color"]))
        if "--grid-opacity" in flags:
            grid = dataclasses.replace(grid, opacity=parse_opacity(flags["--grid-opacity"]))
        # Validate the focus against the grid the agent just saw BEFORE
        # changing anything sticky, so a bad focus leaves the view untouched.
        focus_density = density if density is not None else view.density
        rect = None
        if focus is not None:
            if view.active is None:
                raise CommandError('NO_VIEW: run "bv view" first')
            rect = focus_rect(view.active, focus[0], focus[1], focus_density)
        view.grid = grid
        if focus is None:
            if density is not None:
                view.density = density
            return self._full_view(ctx, parsed, view, ActionOutcome(), notes=[])
        capture = self._host.capture(ctx.agent.id)
        spec = GridSpec(capture.width, capture.height, focus_density)
        focus_spec = FocusSpec(a=focus[0], b=focus[1], rect=rect)
        return self._screenshot_result(ctx, parsed, view, capture, spec, focus_spec, ActionOutcome(), notes=[])

    def _window(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1:
            raise CommandError(f"USAGE: bv window {'|'.join(self._defaults.window_presets)}|<W>x<H>")
        window = resolve_viewport(args[0], self._defaults.window_presets)
        view = self._view_for(ctx.agent.id)
        view.window = window
        if not self._host.has_session(ctx.agent.id):
            return success_result(
                command=parsed.raw,
                detail=f"Browser Vision: window {window.name}",
                kind="browser",
                data={"window": window.name},
                sections=[("BROWSER", [f"window set to {window.name}; it applies when you run bv open"])],
                cwd=ctx.cwd,
            )
        self._host.set_viewport(ctx.agent.id, window)
        return self._full_view(ctx, parsed, view, ActionOutcome(), notes=[WINDOW_SWAP_NOTE])

    def _click(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1 or not (args[0].isascii() and args[0].isdigit()):
            raise CommandError("USAGE: bv click <n> (n is a cell number from the most recent screenshot)")
        view = self._view_for(ctx.agent.id)
        # Click numbers come from a screenshot; without one there is nothing to resolve.
        if view.active is None:
            raise CommandError('NO_VIEW: run "bv view" first')
        self._require_page(ctx.agent.id)
        number = int(args[0])
        x, y = view.active.cell_center(number)
        outcome = self._host.click(ctx.agent.id, x, y)
        return self._full_view(ctx, parsed, view, outcome, notes=[f"clicked cell {number} at ({x:g}, {y:g})"])

    def _type(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if any(arg != "--enter" for arg in args):
            raise CommandError("USAGE: bv type [--enter], with the text to type in the body")
        if body is None or body == "":
            raise CommandError("USAGE: bv type needs the text to type in the body")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        outcome = self._host.type_text(ctx.agent.id, body, enter="--enter" in args)
        return self._full_view(ctx, parsed, view, outcome, notes=[f"typed {len(body)} characters"])

    def _key(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1:
            raise CommandError("USAGE: bv key <Key or combo>, e.g. Enter, Tab, Control+A")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        outcome = self._host.press(ctx.agent.id, args[0])
        return self._full_view(ctx, parsed, view, outcome, notes=[f"pressed {args[0]}"])

    def _scroll(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if len(args) != 1 or args[0].lower() not in {"up", "down"}:
            raise CommandError("USAGE: bv scroll up|down")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        if view.active is None:
            raise CommandError('NO_VIEW: run "bv view" first')
        distance = self._defaults.scroll_fraction * view.active.viewport_h
        dy = distance if args[0].lower() == "down" else -distance
        outcome = self._host.scroll(ctx.agent.id, dy)
        return self._full_view(ctx, parsed, view, outcome, notes=[f"scrolled {args[0].lower()} {abs(dy):g}px"])

    def _back(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        if args:
            raise CommandError("USAGE: bv back")
        view = self._view_for(ctx.agent.id)
        self._require_page(ctx.agent.id)
        outcome = self._host.back(ctx.agent.id)
        return self._full_view(ctx, parsed, view, outcome, notes=["went back"])

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
            f"density: {view.density}",
            f"grid: {'on' if view.grid.enabled else 'off'}, colour {_color_text(view.grid.color)}, opacity {view.grid.opacity:g}",
        ]
        lines += [f"downloaded: {item}" for item in view.downloads] or ["downloads this session: none"]
        return success_result(
            command=parsed.raw,
            detail="Browser Vision: status",
            kind="browser",
            data={"window": view.window.name, "density": view.density, "grid": view.grid.enabled},
            sections=[("BROWSER", lines)],
            cwd=ctx.cwd,
        )

    def _close(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, args: tuple[str, ...], body: str | None) -> BossModCliResult:
        closed = self._host.close(ctx.agent.id)
        with self._lock:
            self._views.pop(ctx.agent.id, None)
        return success_result(
            command=parsed.raw,
            detail="Browser Vision: close",
            kind="browser",
            data={"closed": closed},
            sections=[("BROWSER", ["browser session closed" if closed else "no browser session was open"])],
            cwd=ctx.cwd,
        )

    # ── helpers ────────────────────────────────────────────────────────────

    def _view_for(self, agent_id: str) -> _AgentView:
        with self._lock:
            view = self._views.get(agent_id)
            if view is None:
                defaults = self._defaults
                view = _AgentView(
                    window=resolve_viewport(defaults.window_default, defaults.window_presets),
                    density=defaults.density_default,
                    grid=GridStyle(
                        enabled=defaults.grid_default,
                        color=parse_color(defaults.grid_color_default),
                        opacity=defaults.grid_opacity_default,
                    ),
                )
                self._views[agent_id] = view
            return view

    def _require_page(self, agent_id: str) -> None:
        if not self._host.has_session(agent_id):
            raise CommandError('NO_PAGE: run "bv open <url>" first')

    def _full_view(
        self,
        ctx: CliExecutionContext,
        parsed: ParsedCliCommand,
        view: _AgentView,
        outcome: ActionOutcome,
        *,
        notes: list[str],
    ) -> BossModCliResult:
        capture = self._host.capture(ctx.agent.id)
        spec = GridSpec(capture.width, capture.height, view.density)
        return self._screenshot_result(ctx, parsed, view, capture, spec, None, outcome, notes=notes)

    def _screenshot_result(
        self,
        ctx: CliExecutionContext,
        parsed: ParsedCliCommand,
        view: _AgentView,
        capture: Capture,
        spec: GridSpec,
        focus: FocusSpec | None,
        outcome: ActionOutcome,
        *,
        notes: list[str],
    ) -> BossModCliResult:
        rendered = render_grid(
            capture.png,
            spec,
            view.grid,
            focus,
            label_min_px=self._defaults.label_min_px,
            image_max_px=self._defaults.image_max_px,
            label=LabelStyle(
                font_ratio=self._defaults.label_font_ratio,
                font_min_px=self._defaults.label_font_min_px,
                opacity=self._defaults.label_opacity,
            ),
        )
        grid_line = _grid_line(spec, view.grid)
        focus_line = f"focus: cells {focus.a}–{focus.b}" if focus is not None else None
        path = self._shots.store(
            ctx.agent.id,
            rendered.png,
            ShotMeta(
                command=parsed.raw,
                url=capture.url,
                title=capture.title,
                window=f"{view.window.name} {capture.width}x{capture.height}",
                grid=grid_line,
                image=f"{rendered.width}x{rendered.height}",
                focus=focus_line,
                taken_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        view.active = spec

        lines = [
            f"url: {capture.url}",
            f"title: {capture.title}",
            f"viewport: {capture.width}x{capture.height}",
            f"window: {view.window.name} {capture.width}x{capture.height}",
            grid_line,
        ]
        if rendered.scale != 1:
            lines.append(f"image {rendered.width}x{rendered.height} (scale ×{rendered.scale:.4g})")
        if focus_line is not None:
            lines.append(focus_line)
        if view.grid.enabled and not rendered.labelled:
            lines.append(UNLABELLED_NOTICE)
        lines += notes
        for download in outcome.downloads:
            if download.error is None:
                entry = f"{DOWNLOADS_VIRTUAL_DIR}/{download.file_name} ({download.size} bytes)"
                view.downloads.append(entry)
                lines.append(f"downloaded: {entry}")
            else:
                lines.append(f"download failed: {download.error}")

        result = success_result(
            command=parsed.raw,
            detail=f"Browser Vision: {capture.url}",
            kind="browser",
            data={
                "url": capture.url,
                "title": capture.title,
                "density": spec.density,
                "cols": spec.cols,
                "rows": spec.rows,
                "screenshot": str(path),
            },
            sections=[("BROWSER", lines)],
            cwd=ctx.cwd,
        )
        return dataclasses.replace(result, image_paths=(str(path),))

    def _usage(self, ctx: CliExecutionContext, parsed: ParsedCliCommand) -> BossModCliResult:
        return self._error(ctx, parsed, 'USAGE: bv open|view|window|click|type|key|scroll|back|status|close — run "learn bv" for details')

    def _error(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, message: str) -> BossModCliResult:
        return error_result(parsed.raw, message, cwd=ctx.cwd)


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


def _parse_on_off(raw: str) -> bool:
    value = raw.strip().lower()
    if value not in {"on", "off"}:
        raise CommandError(f"USAGE: --grid on|off, got {raw!r}")
    return value == "on"


def _color_text(color: GridColor) -> str:
    if color == "auto":
        return "auto"
    return "#{:02x}{:02x}{:02x}".format(*color)


def _grid_line(spec: GridSpec, style: GridStyle) -> str:
    cells = f"density {spec.density} → {spec.cols} cols × {spec.rows} rows, cells 0–{spec.cell_count - 1}"
    if style.enabled:
        return f"grid: on, {cells}, colour {_color_text(style.color)}, opacity {style.opacity:g}"
    return f"grid: off ({cells}; click numbers still refer to this grid)"
