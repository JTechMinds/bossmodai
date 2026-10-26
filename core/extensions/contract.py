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
- ``agent_view`` → :class:`SupportsAgentView` (read-only per-agent record
  lists, one per declared view, plus item detail, opened from the agent's desk);
- ``wake`` → :class:`SupportsWake` (new events per configured agent, polled
  by the runtime worker's wake service, which wakes the agent with an
  ``extension_event`` trigger);
- ``model_provider`` → :class:`core.extensions.model_provider.SupportsModelProvider`
  (operator controls and model calls);
- ``command`` → :class:`SupportsCommand` (the extension's CLI namespace for
  agents; an extension without one gives agents nothing);
- ``setup.required`` → :class:`SupportsSetup` (the one-click download);
- ``service`` → :class:`SupportsService` (something long-lived the app process
  runs while the extension is on, started and stopped by
  ``core.extensions.service_host``);
- ``panel`` → :class:`SupportsPanel` (a status and actions the card renders,
  see ``core.extensions.panel``);
- ``settings`` → :class:`SupportsSettings` (extension-wide values the host
  stores wrapped at rest and verifies through the extension on save; the
  extension reads them only through ``ExtensionContext.read_settings``);
- :class:`SupportsPromptState`, :class:`SupportsAgentConfigChanged` and
  :class:`SupportsAgentReconcile` need no manifest flag (the last two are
  called in the runtime worker by ``core.extensions.agent_hooks``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from core.extensions.manifest import ExtensionManifest
from core.extensions.model_provider import ProviderServices
from core.extensions.panel import PanelStatus

if TYPE_CHECKING:
    from starlette.types import ASGIApp

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


def _not_supplied(name: str) -> Callable[..., Any]:
    """Return a stand-in for a host service the context was built without; calling it raises."""

    def missing(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(f"ExtensionContext.{name} was not supplied by the host")

    return missing


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
        model_provider: Host services for a ``model_provider`` extension;
            ``None`` for any other.
        read_settings: Returns every declared ``settings`` field's stored
            value (decrypted), else its manifest default. Raises ``KeyError``
            for a field with neither, and ``ValueError`` on a corrupt stored
            row. Bound by the loader so the extension never imports ``db``.
        seal: Wraps a string with the host's at-rest secret wrap, so an
            extension can protect a secret it keeps in its own data dir.
        unseal: Reverses ``seal``.
    """

    manifest: ExtensionManifest
    data_dir: Path
    # Required, so it sits before the defaulted field.
    read_agent_config: Callable[[str], dict[str, str] | None]
    runtime_worker: bool = False
    # Only supplied to a manifest declaring model_provider. These callbacks
    # are scoped to that extension; no database access is handed to it.
    model_provider: ProviderServices | None = None
    # Host-bound by the loader for every extension. The defaults raise, so a
    # context built by hand without them fails loudly if one is used.
    read_settings: Callable[[], dict[str, str]] = _not_supplied("read_settings")
    seal: Callable[[str], str] = _not_supplied("seal")
    unseal: Callable[[str], str] = _not_supplied("unseal")


class Extension(Protocol):
    """What ``create(ctx)`` must return: the one method every extension has.

    Everything else is an optional capability the manifest declares (see the
    module docstring); the loader refuses an instance that lacks one.
    """

    def shutdown(self) -> None:
        """Release everything the extension holds (browsers, threads)."""
        ...


@runtime_checkable
class SupportsCommand(Protocol):
    """Optional: an extension whose manifest has a ``command`` block implements this."""

    def handle(
        self,
        ctx: CliExecutionContext,
        parsed: ParsedCliCommand,
        body: str | None,
    ) -> BossModCliResult:
        """Run one command in the extension's namespace for one agent."""
        ...


@runtime_checkable
class SupportsSetup(Protocol):
    """Optional: an extension whose manifest sets ``setup.required`` implements this."""

    def run_setup(self, log_path: Path) -> None:
        """Perform setup, blocking, writing progress to ``log_path``.

        Raises:
            SetupError: Setup failed; the message is what the operator sees.
        """
        ...


class ServiceStartError(Exception):
    """A service could not start; the message is shown to the operator verbatim.

    Raise it for an expected, explainable failure (a port already in use).
    Any other exception is logged by the host with its traceback and shown
    as a generic "could not start" message.
    """


class PanelError(Exception):
    """A panel read or action failed; the message is shown to the operator verbatim."""


@runtime_checkable
class SupportsService(Protocol):
    """Optional: an extension whose manifest sets ``service`` implements this.

    Called only in the app process, on its event loop, by
    ``core.extensions.service_host``. ``create(ctx)`` stays inert; nothing
    runs until ``start_service``.
    """

    async def start_service(self, app: ASGIApp) -> None:
        """Start serving; return once it is running.

        Args:
            app: The app process's ASGI application, for an extension that
                exposes it elsewhere.

        Raises:
            ServiceStartError: It could not start (the operator-safe reason).
        """
        ...

    async def stop_service(self) -> None:
        """Stop serving and release what ``start_service`` took. Idempotent."""
        ...


@runtime_checkable
class SupportsPanel(Protocol):
    """Optional: an extension whose manifest sets ``panel`` implements this."""

    def panel(self) -> PanelStatus:
        """Return what the card shows now. Cheap: local state only, no network."""
        ...

    def panel_action(self, key: str, values: Mapping[str, str]) -> None:
        """Run one of the actions the last :meth:`panel` offered.

        Args:
            key: The chosen :class:`core.extensions.panel.PanelAction` key.
            values: That action's ``values``, unchanged.

        Raises:
            PanelError: The action failed (the operator-safe reason).
        """
        ...


class LiveViewFact(BaseModel):
    """One fact about a live-view image, shown inline in the extension's order.

    Invariant: ``value`` is the exact text the agent's result carried.
    Extensions must not format it separately for the viewer.

    Attributes:
        label: The fact's name, shown as ``label: value``, e.g. ``marks``;
            ``None`` when the value describes itself (e.g. ``full page``)
            and is shown alone.
        value: The verbatim value, e.g. ``3 (hidden on the image)``.

    Raises:
        pydantic.ValidationError: ``value`` is empty, or ``label`` is an
            empty string (an extension bug, refused at construction rather
            than shown blank; leave it ``None`` instead).
    """

    label: str | None = Field(default=None, min_length=1)
    value: str = Field(min_length=1)


class LiveViewItem(BaseModel):
    """One agent's latest output, for the operator's read-only live view.

    Attributes:
        agent_id: The agent it belongs to.
        image_path: The exact image the model was sent (inside the data dir).
        taken_at: When it was captured (UTC).
        command: The command that produced it.
        url: Page URL; ``None`` for an output with no address (a desktop).
        title: Page title.
        facts: Facts that describe the image, in display order; each
            value is the exact text the agent's result carried (see
            :class:`LiveViewFact`).
    """

    agent_id: str
    image_path: Path
    taken_at: datetime
    command: str
    url: str | None
    title: str
    facts: list[LiveViewFact]


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


@runtime_checkable
class SupportsAgentConfigChanged(Protocol):
    """Optional: an extension that acts when one agent's settings for it change.

    Needs no manifest flag. Only an extension that holds something per agent
    in the runtime worker (e.g. a running desktop started with the old
    settings) implements it.
    """

    def agent_config_changed(self, agent_id: str) -> None:
        """React to one agent's settings for this extension being saved or removed.

        Called in the runtime worker, off its loop, after the app saved or
        removed this agent's settings for this extension. Read the new value
        with ``read_agent_config`` (``None`` means removed: the agent was
        turned off).

        Args:
            agent_id: The agent whose settings changed.

        Raises:
            Exception: The extension's own errors. The host logs them with
                the traceback and carries on; the save itself already
                succeeded.
        """
        ...


@runtime_checkable
class SupportsAgentReconcile(Protocol):
    """Optional: an extension that holds resources per agent outside the database.

    Needs no manifest flag. Deleting an agent deletes its database rows; an
    extension holding anything else for it (a container, a volume, files)
    learns here that the agent is gone.
    """

    def reconcile_agents(self, existing_agent_ids: frozenset[str]) -> None:
        """Drop everything held for agents not in ``existing_agent_ids``.

        Called in the runtime worker, off its loop, at boot and after agents
        are deleted (also after any extension is turned on or off, which
        files the same reconcile). Must be idempotent: it may run with the
        same set again.

        Args:
            existing_agent_ids: The ids of every agent that exists now.

        Raises:
            Exception: The extension's own errors. The host logs them with
                the traceback and moves on to the next extension.
        """
        ...


class ConfigError(Exception):
    """A config (per-agent or extension-wide) failed verification; the message is shown to the operator verbatim."""


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
            ConfigError: Verification failed; nothing is stored.
        """
        ...


@runtime_checkable
class SupportsSettings(Protocol):
    """Optional: an extension whose manifest has a ``settings`` block implements this."""

    def verify_settings(self, values: Mapping[str, str]) -> str:
        """Check candidate values before they are stored (the verify-before-store contract).

        Called by the app process on save, off the request loop, only while
        the extension is on (a disabled extension is never imported, so its
        values are stored after the host's schema checks alone).

        Args:
            values: Every declared field's value (a blank secret already
                resolved to the stored one by the host).

        Returns:
            A one-line success detail, e.g. ``Port 38473 is available``.

        Raises:
            ConfigError: Verification failed; nothing is stored.
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
    """Optional: an extension whose manifest has an ``agent_view`` block implements this.

    ``view`` is always one of the manifest's ``agent_view.views`` keys; the
    host refuses any other before calling.
    """

    def agent_view(self, agent_id: str, *, view: str, skip: int, top: int) -> AgentViewPage:
        """Return one page of one of the agent's record lists.

        Raises:
            AgentViewError: The read failed.
        """
        ...

    def agent_view_item(self, agent_id: str, item_id: str, *, view: str) -> AgentViewItem:
        """Return one record of that list in full.

        Raises:
            AgentViewError: The read failed or ``item_id`` is unknown.
        """
        ...


class WakeBatch(BaseModel):
    """New events for one agent, handed from ``poll_wake`` to ``commit_wake``.

    Attributes:
        agent_id: The agent the events are for.
        title: One line naming what arrived, e.g. "New email in shared@contoso.com".
        lines: One line per item, e.g. "[m3f9a21c] Alice Doe <alice@x.com> — Re: Daily report".
        cursor: Opaque to the host; handed back to ``commit_wake`` unchanged.
    """

    agent_id: str
    title: str
    lines: list[str]
    cursor: str


@runtime_checkable
class SupportsWake(Protocol):
    """Optional: an extension whose manifest has a ``wake`` block implements this.

    Two-phase: the host persists the agent's trigger first and commits the
    batch only after, so a failed enqueue never loses events. Every method
    runs in the runtime worker, off its event loop, and may block.
    """

    def poll_wake(self, agent_id: str) -> WakeBatch | None:
        """Return the events since this agent's committed cursor, or ``None``.

        Must not advance the committed cursor (except to record where
        watching starts, which delivers nothing and so loses nothing).

        Raises:
            Exception: The extension's own error types; the host records
                ``describe_wake_error`` of it and moves on.
        """
        ...

    def commit_wake(self, batch: WakeBatch) -> None:
        """Persist ``batch.cursor`` as this agent's committed cursor."""
        ...

    def skip_wake(self, agent_id: str) -> None:
        """Advance this agent's cursor to now without delivering anything (no network call).

        Called instead of ``poll_wake`` while the agent is on vacation, so
        what arrives meanwhile never wakes it.
        """
        ...

    def describe_wake_error(self, exc: Exception) -> str:
        """Return one plain sentence for the operator about a ``poll_wake`` failure.

        It is shown on the agent's desk, so it must never carry a secret or
        raw service text.
        """
        ...
