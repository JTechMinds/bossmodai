"""BossMod AI — the contract between the extension host and an extension.

Setup state is derived from marker files in the extension's data dir, never
from process memory, so the app and the runtime worker read the same answer
and a restart mid-setup reads as "interrupted":

- ``ready.json`` — written by the extension only after a successful launch probe;
- ``setup.lock`` — the pid of the process running setup (host-owned);
- ``setup.error`` — the last failure's message (host-owned);
- ``setup.log`` — the setup output (host creates it, the extension writes it).

Optional capabilities are each a manifest flag or block, a
``runtime_checkable`` Protocol here, and a generic API route; the loader
refuses an instance that lacks the Protocol its manifest declares:

- ``live_view`` → :class:`SupportsLiveView` (each agent's latest output);
- ``agent_config`` → :class:`SupportsAgentConfig` (per-agent settings the
  host stores wrapped at rest and verifies through the extension on save;
  the extension reads them only through ``ExtensionContext.read_agent_config``);
- ``agent_view`` → :class:`SupportsAgentView` (a read-only per-agent record
  list plus item detail, opened from the agent's desk);
- :class:`SupportsPromptState` needs no manifest flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Mapping, Protocol, runtime_checkable

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
        read_agent_config: Returns one agent's stored ``agent_config``
            values for THIS extension (decrypted), or ``None`` when that
            agent has none. Bound by the loader so the extension never
            imports ``db``. Raises ``ValueError`` on a corrupt stored row.
        runtime_worker: This process is the runtime worker, the one that
            runs agents' commands. Only there may an extension treat state
            left by an earlier process as dead (the app process shares the
            data dir with a live worker).
    """

    manifest: ExtensionManifest
    data_dir: Path
    # Required, so it sits before the defaulted field.
    read_agent_config: Callable[[str], dict[str, str] | None]
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


class AgentConfigError(Exception):
    """A per-agent config failed verification; the message is shown to the operator verbatim."""


class AgentViewError(Exception):
    """A per-agent view read failed.

    Attributes:
        code: A stable machine code (e.g. ``MAILBOX_ACCESS_DENIED``).
        message: What the operator sees.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@runtime_checkable
class SupportsAgentConfig(Protocol):
    """Optional: an extension whose manifest has an ``agent_config`` block implements this."""

    def verify_agent_config(self, values: Mapping[str, str]) -> str:
        """Check candidate values against the real service before they are stored.

        Called by the app process on save, off the request loop; it may block
        on the network.

        Args:
            values: Every declared field's value (a blank secret already
                resolved to the stored one by the host).

        Returns:
            A one-line success detail, e.g. ``Connected to reports@contoso.com``.

        Raises:
            AgentConfigError: Verification failed; nothing is stored.
        """
        ...


class AgentViewColumn(BaseModel):
    """One column of a per-agent view."""

    key: str
    label: str


class AgentViewRow(BaseModel):
    """One record of a per-agent view.

    Attributes:
        id: The id ``agent_view_item`` accepts.
        cells: Column key → display text.
        emphasis: Shown emphasised (e.g. unread).
    """

    id: str
    cells: dict[str, str]
    emphasis: bool = False


class AgentViewPage(BaseModel):
    """One page of a per-agent view.

    Attributes:
        columns: The columns, in display order.
        rows: This page's records.
        has_more: Whether a further page exists.
        caption: The table's accessible name, e.g. "Inbox of reports@contoso.com".
    """

    columns: list[AgentViewColumn]
    rows: list[AgentViewRow]
    has_more: bool
    caption: str


class AgentViewItem(BaseModel):
    """One record opened in full.

    Attributes:
        title: The detail's heading.
        facts: Label/value pairs, in display order.
        body_text: Plain text, shown preformatted (never as HTML).
    """

    title: str
    facts: list[tuple[str, str]]
    body_text: str


@runtime_checkable
class SupportsAgentView(Protocol):
    """Optional: an extension whose manifest has an ``agent_view`` block implements this."""

    def agent_view(self, agent_id: str, *, skip: int, top: int) -> AgentViewPage:
        """Return one page of the agent's records.

        Raises:
            AgentViewError: The read failed.
        """
        ...

    def agent_view_item(self, agent_id: str, item_id: str) -> AgentViewItem:
        """Return one record in full.

        Raises:
            AgentViewError: The read failed or ``item_id`` is unknown.
        """
        ...
