"""BossMod AI — the contract between the extension host and an extension.

Setup state is derived from marker files in the extension's data dir, never
from process memory, so the app and the runtime worker read the same answer
and a restart mid-setup reads as "interrupted":

- ``ready.json`` — written by the extension only after a successful launch probe;
- ``setup.lock`` — the pid of the process running setup (host-owned);
- ``setup.error`` — the last failure's message (host-owned);
- ``setup.log`` — the setup output (host creates it, the extension writes it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from core.extensions.manifest import ExtensionManifest

if TYPE_CHECKING:
    from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
    from core.models import Agent

READY_FILE = "ready.json"
SETUP_LOCK_FILE = "setup.lock"
SETUP_ERROR_FILE = "setup.error"
SETUP_LOG_FILE = "setup.log"

SetupState = Literal["not_required", "missing", "installing", "ready", "failed"]


class SetupStatus(BaseModel):
    """Where an extension's one-click setup stands."""

    state: SetupState
    detail: str | None = None


class SetupError(Exception):
    """Setup failed; the message is shown to the operator verbatim."""


@dataclass(frozen=True)
class ExtensionContext:
    """What the host hands an extension's ``create(ctx)``.

    Attributes:
        manifest: The validated manifest.
        data_dir: The extension's private data dir. Not created by the host;
            the extension creates it on demand.
        runtime_worker: This process is the runtime worker, the one that
            runs agents' commands. Only there may an extension treat state
            left by an earlier process as dead (the app process shares the
            data dir with a live worker).
    """

    manifest: ExtensionManifest
    data_dir: Path
    runtime_worker: bool = False


class Extension(Protocol):
    """What ``create(ctx)`` must return."""

    def handle(
        self,
        ctx: CliExecutionContext,
        parsed: ParsedCliCommand,
        body: str | None,
    ) -> BossModCliResult:
        """Run one command in the extension's namespace for one agent."""
        ...

    def setup_status(self) -> SetupStatus:
        """Return the current setup state (read from the data dir's markers)."""
        ...

    def run_setup(self, log_path: Path) -> None:
        """Perform setup, blocking, writing progress to ``log_path``.

        Raises:
            SetupError: Setup failed; the message is what the operator sees.
        """
        ...

    def shutdown(self) -> None:
        """Release everything the extension holds (browsers, threads)."""
        ...


class LiveViewItem(BaseModel):
    """One agent's latest output, for the operator's read-only live view.

    Attributes:
        agent_id: The agent it belongs to.
        image_path: The exact image the model was sent (inside the data dir).
        taken_at: When it was captured (UTC).
        command: The command that produced it.
        url: Page URL.
        title: Page title.
        caption_lines: The result lines that describe the image.
    """

    agent_id: str
    image_path: Path
    taken_at: datetime
    command: str
    url: str
    title: str
    caption_lines: list[str]


@runtime_checkable
class SupportsLiveView(Protocol):
    """Optional: an extension whose manifest sets ``live_view`` implements this."""

    def live_view(self) -> list[LiveViewItem]:
        """Return each agent's latest item, newest first."""
        ...


@runtime_checkable
class SupportsPromptState(Protocol):
    """Optional: an extension that tells the agent its current state every turn."""

    def prompt_state(self, agent: Agent) -> str | None:
        """Return one line of the agent's live state, or ``None`` for nothing.

        Called while the working prompt is built, in the runtime worker.
        It must not start anything (a browser, a thread) just to answer.
        """
        ...
