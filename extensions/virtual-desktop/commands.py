"""Virtual Desktop — the ``vm`` command: dispatch and the screen subcommands.

Each command runs for one agent while holding that agent's desktop lock,
so a settings restart can never land between an action and its screenshot.
Every subcommand except ``status`` and ``stop`` first starts a stopped
desktop (``reset`` starts its fresh one itself). Coordinates are image
pixels of the agent's latest screenshot; the pointer reported after each
action is the real one. Each screenshot value is formatted once and reused
for the result text and the screenshot's sidecar, so the operator's live
view cannot drift from what the agent was told. File copies and shell
commands are in ``commands_io``.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from core.bm_cli.host_roots import PathOutsideRootsError
from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.extensions.contract import SetupStatus
from core.extensions.manifest import AgentConfigSpec
from core.llm.routing import agent_model
from core.models import Agent
from db.model_capabilities import supports_images

from . import commands_io
from .commands_io import Call, DesktopCommandError, boot_notes, no_args, positive_int, positive_seconds
from .desktop_types import DesktopActionError, DesktopHostLike, DockerUnavailable
from .idle import IdleTracker, format_minutes
from .screen import Rendered, change_key, fit, image_text, image_to_screen, parse_point, pointer_text
from .screenshots import ShotMeta, ShotStore
from .settings import DesktopDefaults, settings_from_config

_USAGE = (
    "USAGE: vm view|point|point1k|click|drag|type|key|scroll|wait|launch|run|upload|download|status|stop|restart|reset"
    ' — run "learn vm" for details'
)
NOT_ENABLED = (
    "NOT_ENABLED: your desktop is off; the boss can turn it on in "
    "Add → Extensions → Virtual Desktop → Configure agents"
)
OFF_LINE = "off; any vm command except status and stop starts it"
# status and stop never start a stopped desktop; reset starts a fresh one itself.
_NO_START = frozenset({"status", "stop", "reset"})
_BUTTONS = {"left": ["click", "1"], "middle": ["click", "2"], "right": ["click", "3"], "double": ["click", "--repeat", "2", "1"]}
_WHEEL = {"up": "4", "down": "5", "left": "6", "right": "7"}


def routed_vision_model(agent: Agent) -> tuple[str | None, bool]:
    """Return the agent's routed model and whether it is flagged image-capable (``None`` when unlinked)."""
    model = agent_model(agent)
    return model, model is not None and supports_images(model)


class VirtualDesktopCommands:
    """Runs ``vm`` for any number of agents.

    Args:
        defaults: Validated manifest defaults.
        host: The desktops.
        shots: Where screenshots are written.
        idle: Idle stretches, stamped at the end of every command.
        setup_status: Reads the setup state (must be ``ready``).
        read_agent_config: The agent's stored settings, ``None`` when off.
        config_spec: The manifest's ``agent_config`` block.
        vision_model: Returns ``(model, image_capable)`` for an agent.
        clock: Monotonic seconds (``vm wait``).
        sleep: Blocking sleep (settle pauses, ``vm wait``).
    """

    def __init__(
        self,
        *,
        defaults: DesktopDefaults,
        host: DesktopHostLike,
        shots: ShotStore,
        idle: IdleTracker,
        setup_status: Callable[[], SetupStatus],
        read_agent_config: Callable[[str], Mapping[str, str] | None],
        config_spec: AgentConfigSpec,
        vision_model: Callable[[Agent], tuple[str | None, bool]] = routed_vision_model,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._defaults = defaults
        self._host = host
        self._shots = shots
        self._idle = idle
        self._setup_status = setup_status
        self._read_agent_config = read_agent_config
        self._spec = config_spec
        self._vision_model = vision_model
        self._clock = clock
        self._sleep = sleep
        # The latest screenshot per agent: what vm point coordinates refer to.
        self._latest: dict[str, Rendered] = {}
        self._lock = threading.Lock()
        self._runners: dict[str, Callable[[Call], BossModCliResult]] = {
            "view": self._view, "point": lambda call: self._move(call, thousandths=False),
            "point1k": lambda call: self._move(call, thousandths=True), "click": self._click,
            "drag": self._drag, "type": self._type, "key": self._key, "scroll": self._scroll,
            "wait": self._wait, "launch": self._launch,
            "run": lambda call: commands_io.run(self._host, self._defaults, call),
            "upload": lambda call: commands_io.upload(self._host, call),
            "download": lambda call: commands_io.download(self._host, call),
            "status": self._status, "stop": self._stop, "restart": self._restart, "reset": self._reset,
        }

    def forget(self, agent_id: str) -> None:
        """Drop the agent's latest screenshot geometry (its desktop stopped or restarted)."""
        with self._lock:
            self._latest.pop(agent_id, None)

    def state_line(self, agent_id: str) -> str:
        """The agent's desktop state for the start of its turn, from memory only (no Docker call)."""
        applied = self._host.applied(agent_id)
        if agent_id not in self._host.running_here() or applied is None:
            return f"Your desktop right now: {OFF_LINE}."
        idle = self._idle.idle_minutes(agent_id)
        used = "" if idle is None else f", last used {_ago(idle)}"
        return f"Your desktop right now: running ({applied.width}x{applied.height}){used}."

    def handle(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        """Run one ``vm`` command for ``ctx.agent``.

        Preconditions, each an explicit error: setup ``ready``
        (``SETUP_REQUIRED``), an image-capable model
        (``MODEL_CANNOT_SEE_IMAGES``), the agent's settings stored
        (``NOT_ENABLED``). Failures come back as error results:
        ``DOCKER_UNAVAILABLE``, ``DESKTOP_ERROR``, ``INVALID_ARGUMENT``,
        ``USAGE`` and the file-copy codes; nothing is clamped or retried
        silently. Every command that gets past the preconditions ends by
        starting a new idle stretch.

        Raises:
            ValueError: The agent's stored settings are corrupt (the CLI
                runtime reports it as the command's error).
            core.bm_cli.host_roots.PathOutsideRootsError: ``vm upload`` /
                ``vm download`` named a path outside the agent's roots; it
                is raised before anything is done, so core's host-path
                consent card can ask the boss.
        """
        status = self._setup_status()
        if status.state != "ready":
            return _error(ctx, parsed, f"SETUP_REQUIRED: the desktop is not set up ({status.state}); the boss can set it up in Add → Extensions")
        model, can_see = self._vision_model(ctx.agent)
        if not can_see:
            return _error(ctx, parsed, f"MODEL_CANNOT_SEE_IMAGES: {model or 'no model configured'}")
        stored = self._read_agent_config(ctx.agent.id)
        if stored is None:
            return _error(ctx, parsed, NOT_ENABLED)
        settings = settings_from_config(self._spec, stored)
        sub = parsed.args[0].lower() if parsed.args else ""
        runner = self._runners.get(sub)
        if runner is None:
            return _error(ctx, parsed, _USAGE)
        try:
            path = commands_io.path_arg(sub, ctx, parsed.args[1:])
        except PathOutsideRootsError:
            # Core's host-path consent card; nothing has been done yet.
            raise
        except ValueError as exc:
            return _error(ctx, parsed, f"INVALID_ARGUMENT: {exc}")
        agent_id = ctx.agent.id
        with self._host.agent_lock(agent_id):
            try:
                boot = None if sub in _NO_START else self._host.ensure_running(agent_id, settings)
                if boot is not None and boot.started:
                    # A fresh boot: no earlier screenshot shows this screen.
                    self.forget(agent_id)
                return runner(Call(ctx, parsed, parsed.args[1:], body, settings, boot, path))
            except DesktopCommandError as exc:
                return _error(ctx, parsed, str(exc))
            except DockerUnavailable as exc:
                return _error(ctx, parsed, f"DOCKER_UNAVAILABLE: {exc}")
            except DesktopActionError as exc:
                return _error(ctx, parsed, f"DESKTOP_ERROR: {exc}")
            except ValueError as exc:
                return _error(ctx, parsed, f"INVALID_ARGUMENT: {exc}")
            finally:
                self._idle.touch(agent_id)

    # ── screen subcommands ───────────────────────────────────────────────

    def _view(self, call: Call) -> BossModCliResult:
        no_args(call, "vm view")
        return self._screenshot(call, [])

    def _move(self, call: Call, *, thousandths: bool) -> BossModCliResult:
        if len(call.args) != 2:
            raise DesktopCommandError(f"USAGE: vm {'point1k' if thousandths else 'point'} <x> <y>")
        x, y = self._target(call.agent_id, call.args[0], call.args[1], thousandths=thousandths)
        self._host.xdotool(call.agent_id, ["mousemove", str(x), str(y)])
        return self._after_action(call, [])

    def _click(self, call: Call) -> BossModCliResult:
        args = call.args
        if len(args) > 3:
            raise DesktopCommandError("USAGE: vm click [left|right|middle|double] | vm click <x> <y> [left|right|middle|double]")
        move: list[str] = []
        if len(args) >= 2:
            x, y = self._target(call.agent_id, args[0], args[1], thousandths=False)
            move = ["mousemove", str(x), str(y)]
        button = (args[-1] if len(args) in (1, 3) else "left").lower()
        if button not in _BUTTONS:
            raise ValueError(f"the button must be left, right, middle or double; got {args[-1]!r}")
        self._host.xdotool(call.agent_id, [*move, *_BUTTONS[button]])
        return self._after_action(call, [f"clicked {button}"])

    def _drag(self, call: Call) -> BossModCliResult:
        if len(call.args) != 2:
            raise DesktopCommandError("USAGE: vm drag <x> <y> (from where the mouse is)")
        x, y = self._target(call.agent_id, call.args[0], call.args[1], thousandths=False)
        self._host.xdotool(call.agent_id, ["mousedown", "1", "mousemove", str(x), str(y), "mouseup", "1"])
        return self._after_action(call, [f"dragged to ({call.args[0]}, {call.args[1]})"])

    def _type(self, call: Call) -> BossModCliResult:
        enter = "--enter" in call.args
        inline = " ".join(arg for arg in call.args if arg != "--enter")
        text = call.body or ""
        if text and inline and " ".join(text.split()) != " ".join(inline.split()):
            raise DesktopCommandError("USAGE: vm type [--enter] <text> — give the text either in the body or after the command, not both")
        text = text or inline
        if not text:
            raise DesktopCommandError("USAGE: vm type needs the text to type, in the body or after the command")
        self._host.xdotool(call.agent_id, ["type", "--clearmodifiers", "--delay", str(self._defaults.type_delay_ms), "--", text])
        if enter:
            self._host.xdotool(call.agent_id, ["key", "--clearmodifiers", "Return"])
        return self._after_action(call, [f"typed {len(text)} characters" + (" and pressed Return" if enter else "")])

    def _key(self, call: Call) -> BossModCliResult:
        if len(call.args) != 1:
            raise DesktopCommandError("USAGE: vm key <combo>, e.g. Return, Tab, ctrl+l, alt+F4")
        self._host.xdotool(call.agent_id, ["key", "--clearmodifiers", call.args[0]])
        return self._after_action(call, [f"pressed {call.args[0]}"])

    def _scroll(self, call: Call) -> BossModCliResult:
        args = call.args
        if len(args) not in (1, 2) or args[0].lower() not in _WHEEL:
            raise DesktopCommandError("USAGE: vm scroll up|down|left|right [n]")
        clicks = positive_int(args[1], "vm scroll's n") if len(args) == 2 else self._defaults.scroll_clicks
        self._host.xdotool(call.agent_id, ["click", "--repeat", str(clicks), _WHEEL[args[0].lower()]])
        return self._after_action(call, [f"scrolled {args[0].lower()} {clicks}"])

    def _wait(self, call: Call) -> BossModCliResult:
        """``vm wait [seconds]``: watch the screen until it changes and holds still, then screenshot."""
        if len(call.args) > 1:
            raise DesktopCommandError("USAGE: vm wait [<seconds>]")
        limit = positive_seconds(call.args[0], "vm wait's limit") if call.args else self._defaults.wait_default_s
        poll_s, settle_s = self._defaults.wait_poll_ms / 1000, self._defaults.wait_settle_ms / 1000
        start = self._clock()
        last = change_key(self._host.capture(call.agent_id).png)
        changed, stable_since, settled = False, start, False
        while (now := self._clock()) - start < limit:
            self._sleep(min(poll_s, limit - (now - start)))
            key = change_key(self._host.capture(call.agent_id).png)
            now = self._clock()
            if key != last:
                last, stable_since, changed = key, now, True
            elif changed and now - stable_since >= settle_s:
                settled = True
                break
        elapsed = self._clock() - start
        if not changed:
            line = f"wait: no change after {elapsed:.1f}s"
        else:
            line = f"wait: screen changed after {elapsed:.1f}s ({'settled' if settled else 'still changing'})"
        return self._screenshot(call, [line])

    def _launch(self, call: Call) -> BossModCliResult:
        notes, status_lines = commands_io.launch(self._host, call)
        self._sleep(self._defaults.launch_settle_ms / 1000)
        return self._screenshot(call, notes, status_lines=status_lines)

    # ── power ────────────────────────────────────────────────────────────

    def _status(self, call: Call) -> BossModCliResult:
        no_args(call, "vm status")
        applied = self._host.applied(call.agent_id)
        running = applied is not None and call.agent_id in self._host.running_here()
        shown = applied if applied is not None and running else call.settings
        lines = [
            f"desktop: {'running' if running else OFF_LINE}",
            f"screen: {shown.width}x{shown.height}" + ("" if running else " (from your settings; applies when it starts)"),
            f"memory limit: {shown.memory_mb} MB",
            f"CPU limit: {shown.cpus}",
            f"image version: {self._defaults.image_version}",
        ]
        idle = self._idle.idle_minutes(call.agent_id) if running else None
        if idle is not None:
            lines.append(f"last used: {_ago(idle)}")
        return self._text(call, lines, data={"running": running})

    def _stop(self, call: Call) -> BossModCliResult:
        no_args(call, "vm stop")
        was_running = self._host.stop(call.agent_id)
        self._stopped(call.agent_id)
        if not was_running:
            return self._text(call, ["desktop already off"], data={"stopped": False})
        line = "desktop powered down: your files and programs are kept; the next vm command (except status and stop) starts it again"
        return self._text(call, [line], status_lines=["Powered down the desktop"], data={"stopped": True})

    def _restart(self, call: Call) -> BossModCliResult:
        no_args(call, "vm restart")
        if call.boot is not None and call.boot.started:
            # It was off and has just started with these settings: that is the reboot.
            return self._screenshot(call, [])
        report = self._host.restart(call.agent_id, call.settings)
        self.forget(call.agent_id)
        return self._screenshot(call, [f"desktop restarted ({report.seconds:.1f}s): files and programs kept"])

    def _reset(self, call: Call) -> BossModCliResult:
        no_args(call, "vm reset")
        report = self._host.reset(call.agent_id, call.settings)
        self._stopped(call.agent_id)
        note = f"desktop reset to a fresh install ({report.seconds:.1f}s): everything that was on it is gone"
        return self._screenshot(call, [note], status_lines=["Reset the desktop"])

    # ── helpers ──────────────────────────────────────────────────────────

    def _target(self, agent_id: str, raw_x: str, raw_y: str, *, thousandths: bool) -> tuple[int, int]:
        """Screen pixels for coordinates on the agent's latest screenshot.

        Raises:
            ValueError: No screenshot since the desktop started, or the
                coordinates are outside it.
        """
        with self._lock:
            latest = self._latest.get(agent_id)
        if latest is None:
            raise ValueError('there is no screenshot of the desktop as it is now; run "vm view" first')
        x, y = parse_point(raw_x, raw_y, latest, thousandths=thousandths)
        return image_to_screen(x, y, latest.scale)

    def _stopped(self, agent_id: str) -> None:
        """Its desktop stopped or was wiped: its screenshots, idle stretch and geometry go."""
        self._shots.delete(agent_id)
        self._idle.forget(agent_id)
        self.forget(agent_id)

    def _after_action(self, call: Call, notes: list[str]) -> BossModCliResult:
        self._sleep(self._defaults.settle_ms / 1000)
        return self._screenshot(call, notes)

    def _screenshot(self, call: Call, notes: list[str], *, status_lines: list[str] | None = None) -> BossModCliResult:
        """Capture, shrink, store and describe the screen; the result carries the image."""
        capture = self._host.capture(call.agent_id)
        rendered = fit(capture.png, self._defaults.image_max_px)
        with self._lock:
            self._latest[call.agent_id] = rendered
        screen = f"{capture.screen[0]}x{capture.screen[1]}"
        image = image_text(rendered)
        pointer = pointer_text(capture, rendered)
        window = capture.active_window
        path = self._shots.store(call.agent_id, rendered.png, ShotMeta(
            command=call.parsed.raw, window=window, screen=screen, image=image, pointer=pointer,
            taken_at=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
        ))
        lines = [f"screen: {screen}", f"image {image}"]
        if window:
            lines.append(f"window: {window}")
        lines += [f"pointer: {pointer}", *notes, *boot_notes(call.boot)]
        result = success_result(
            command=call.parsed.raw,
            detail=f"Virtual Desktop: {call.parsed.args[0].lower()}",
            kind="desktop",
            data={"screenshot": str(path), "window": window, "status_lines": list(status_lines or [])},
            sections=[("DESKTOP", lines)],
            cwd=call.ctx.cwd,
        )
        summary = f"{call.parsed.raw} → {window}" if window else call.parsed.raw
        return dataclasses.replace(result, image_paths=(str(path),), summary=summary)

    def _text(
        self, call: Call, lines: list[str], *, status_lines: list[str] | None = None, data: dict[str, Any],
    ) -> BossModCliResult:
        """A result without a screenshot."""
        return success_result(
            command=call.parsed.raw,
            detail=f"Virtual Desktop: {call.parsed.args[0].lower()}",
            kind="desktop",
            data={**data, "status_lines": list(status_lines or [])},
            sections=[("DESKTOP", [*lines, *boot_notes(call.boot)])],
            cwd=call.ctx.cwd,
        )


def _error(ctx: CliExecutionContext, parsed: ParsedCliCommand, message: str) -> BossModCliResult:
    return error_result(parsed.raw, message, cwd=ctx.cwd)


def _ago(minutes: int) -> str:
    return "less than a minute ago" if minutes == 0 else f"{format_minutes(minutes)} ago"
