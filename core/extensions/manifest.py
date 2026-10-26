"""BossMod AI — extension ``manifest.json`` schema and loader."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MANIFEST_FILE = "manifest.json"

# An id names a data folder and a settings entry, so it stays a plain slug.
_ID_PATTERN = r"^[a-z][a-z0-9-]{1,40}$"
# A command name is typed by agents as the first CLI token.
_COMMAND_PATTERN = r"^[a-z][a-z0-9-]{1,15}$"
# A no_retry entry is matched word by word against the parsed (lowercased)
# command, so it is lowercase words with single spaces.
_NO_RETRY_PATTERN = r"^[a-z0-9-]+( [a-z0-9-]+)*$"


class ManifestError(Exception):
    """A manifest is missing, unreadable, not JSON, or fails the schema."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandSpec(_Strict):
    """The one CLI command namespace an extension adds (``bv …``).

    ``no_retry`` lists subcommand prefixes (the words after ``name``) whose
    replay is harmful; a failed turn that ran one is not retried
    (``core.extensions.cli_bridge`` marks the result).
    """

    name: str = Field(pattern=_COMMAND_PATTERN)
    summary: str = Field(min_length=1)
    usage: str = Field(min_length=1)
    help: str = Field(min_length=1)
    no_retry: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _no_retry_are_subcommand_prefixes(self) -> "CommandSpec":
        for entry in self.no_retry:
            if not re.fullmatch(_NO_RETRY_PATTERN, entry):
                raise ValueError(f"no_retry entry {entry!r} must be lowercase words separated by single spaces")
            if entry.split()[0] == self.name:
                raise ValueError(f"no_retry entry {entry!r} must not repeat the command name {self.name!r}")
        duplicates = sorted({entry for entry in self.no_retry if self.no_retry.count(entry) > 1})
        if duplicates:
            raise ValueError(f"duplicate no_retry entries: {', '.join(duplicates)}")
        return self


class RequiresSpec(_Strict):
    """What must hold before the extension can load or apply to an agent."""

    image_model: bool = False
    python_modules: tuple[str, ...] = ()


class SetupSpec(_Strict):
    """Whether a one-click setup step (a download) must run before enabling.

    ``ready_requires`` names keys ``ready.json`` must hold with exactly these
    string values for setup to count as ready. An extension raises it when
    what it installs changes, so an older install reads as out of date and
    the card offers setup again, without the host importing the extension.
    """

    required: bool
    label: str | None = None
    ready_requires: dict[str, str] = Field(default_factory=dict)


class ConfigField(_Strict):
    """One setting the operator fills in on a form: an agent's (``agent_config``) or the extension's own (``settings``).

    Attributes:
        key: The stored key, a lowercase identifier.
        label: What the form shows.
        kind: ``text``, ``secret`` (never sent back to the UI), ``email``
            (checked for an address shape on save) or ``number`` (a whole
            number within ``min``/``max``; blank on save stores ``default``).
        required: Whether an empty value is refused on save.
        summary: The one field whose value the desk shows once configured.
        min: A ``number`` field's smallest value; ``None`` for no bound.
        max: A ``number`` field's largest value; ``None`` for no bound.
        default: A ``number`` field's value when left blank, and its value
            in a config saved before the field existed. Required for a
            ``number`` field; not allowed on any other kind.
    """

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1)
    kind: Literal["text", "secret", "email", "number"]
    required: bool = True
    summary: bool = False
    min: int | None = None
    max: int | None = None
    default: str | None = None

    @model_validator(mode="after")
    def _number_bounds_and_default(self) -> "ConfigField":
        if self.kind != "number":
            if self.min is not None or self.max is not None or self.default is not None:
                raise ValueError(f"field {self.key}: min, max and default apply only to a number field")
            return self
        if self.default is None:
            raise ValueError(f"number field {self.key} needs a default")
        try:
            value = int(self.default)
        except ValueError as exc:
            raise ValueError(f"number field {self.key}: default {self.default!r} is not a whole number") from exc
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"number field {self.key}: min is greater than max")
        if (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
            raise ValueError(f"number field {self.key}: default {value} is outside min/max")
        return self


class ConfigSpec(_Strict):
    """A declarative settings form, used by ``agent_config`` (``contract.SupportsAgentConfig``,
    one stored set per agent) and by ``settings`` (``contract.SupportsSettings``, one stored set
    for the whole extension).

    Attributes:
        label: The name of the setting group, e.g. "Microsoft 365 mailbox".
        help: Operator guidance shown above the form; paragraphs split on
            blank lines.
        fields: At least one field; keys unique, at most one ``summary``.
    """

    label: str = Field(min_length=1)
    help: str = Field(min_length=1)
    fields: tuple[ConfigField, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_unique_and_one_summary(self) -> "ConfigSpec":
        keys = [item.key for item in self.fields]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate field keys: {', '.join(duplicates)}")
        if sum(1 for item in self.fields if item.summary) > 1:
            raise ValueError("at most one field may set summary")
        return self


class AgentViewTab(_Strict):
    """One tab of a per-agent view, e.g. Inbox or Sent.

    Attributes:
        key: The ``view`` value the routes and ``agent_view`` take.
        label: What the tab shows.
    """

    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    label: str = Field(min_length=1)


class AgentViewSpec(_Strict):
    """A read-only per-agent record list (``contract.SupportsAgentView``).

    Attributes:
        label: The desk action that opens it, e.g. "Open inbox".
        views: The lists it offers, in tab order; at least one, keys unique.
            With one, the viewer shows no tab row.
        requires_config: The view needs the agent's ``agent_config`` stored.
    """

    label: str = Field(min_length=1)
    views: tuple[AgentViewTab, ...] = Field(min_length=1)
    requires_config: bool = True

    @model_validator(mode="after")
    def _view_keys_unique(self) -> "AgentViewSpec":
        keys = [item.key for item in self.views]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate view keys: {', '.join(duplicates)}")
        return self


class WakeSpec(_Strict):
    """How often the host asks an extension for new events (``contract.SupportsWake``).

    Exactly one of ``interval_field`` / ``interval_seconds`` is set.

    Attributes:
        interval_field: The ``agent_config`` ``number`` field holding each
            agent's poll interval in seconds, for a wake whose cadence the
            operator chooses per agent.
        interval_seconds: One fixed poll interval for every agent, for a
            wake whose cadence is the extension's own (e.g. an in-memory
            check), so the operator sees no interval field.
        desk_status: The desk shows the last check's time and health. Set
            false for a wake whose check never reaches an outside service.

    Raises:
        pydantic.ValidationError: Both or neither interval is set, or
            ``interval_seconds`` is not greater than 0.
    """

    interval_field: str | None = Field(default=None, min_length=1)
    interval_seconds: int | None = Field(default=None, gt=0)
    desk_status: bool = True

    @model_validator(mode="after")
    def _exactly_one_interval(self) -> "WakeSpec":
        if (self.interval_field is None) == (self.interval_seconds is None):
            raise ValueError("wake needs exactly one of interval_field or interval_seconds")
        return self


# The icons a live view's chat-header button may wear. A fixed vocabulary, not
# any Lucide name: see LiveViewSpec.icon.
LiveViewIcon = Literal["globe", "monitor"]


class LiveViewSpec(_Strict):
    """The operator's read-only view of each agent's latest output (``contract.SupportsLiveView``).

    Attributes:
        label: What the chat-header button and the viewer title say, e.g.
            "Browser view"; the extension names its own view, so core never
            names an extension.
        icon: The chat-header button's glyph, from the host's fixed
            vocabulary of icons guaranteed to be in the UI's icon subset
            (:data:`LiveViewIcon`). The UI ships only a generated subset of
            Lucide, and ``core/icons.js`` throws on a name outside it, so an
            extension may not name an arbitrary icon: a dropped-in extension
            could otherwise break the chat header. Adding an icon means
            adding it here and to ``ICONS`` in
            ``ui/static/js/extensions/live-status.js``, whose literals keep it
            in the subset; a test holds the two lists equal.
    """

    label: str = Field(min_length=1)
    icon: LiveViewIcon


class ModelProviderSpec(_Strict):
    """The display name of the connection registered by a model extension."""

    connection_name: str = Field(min_length=1)
    # Missing scoped metadata never inherits model-global image flags.
    scoped_capabilities: bool = False


class ExtensionManifest(_Strict):
    """Validated ``manifest.json``. Unknown keys are rejected.

    ``defaults`` is the extension's own tunables; the host does not interpret
    it, the extension validates it when it is created.
    """

    id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    # None: the extension gives agents no CLI command (a service or panel
    # extension). Every agent-facing block below then needs a command.
    command: CommandSpec | None = None
    prompt: str | None = None
    requires: RequiresSpec = RequiresSpec()
    # An extension without a download step says nothing here.
    setup: SetupSpec = SetupSpec(required=False)
    # Set when the instance implements contract.SupportsLiveView; its label
    # names the view in the UI. None: the extension has no live view.
    live_view: LiveViewSpec | None = None
    # Set when the instance implements contract.SupportsAgentConfig.
    agent_config: ConfigSpec | None = None
    # Set when the instance implements contract.SupportsAgentView.
    agent_view: AgentViewSpec | None = None
    # Set when the instance implements contract.SupportsWake.
    wake: WakeSpec | None = None
    model_provider: ModelProviderSpec | None = None
    # Set when the instance implements contract.SupportsSettings: the
    # extension-wide values the operator edits on the extension's card.
    settings: ConfigSpec | None = None
    # Set when the instance implements contract.SupportsService: it runs
    # something in the app process while enabled. A plain flag: it carries no
    # further declarative data.
    service: bool = False
    # Set when the instance implements contract.SupportsPanel: the card shows
    # the extension's status and actions. A plain flag, like ``service``.
    panel: bool = False
    defaults: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _agent_facing_parts_need_a_command(self) -> "ExtensionManifest":
        if self.command is not None:
            return self
        agent_facing = [
            name for name, present in (
                ("prompt", self.prompt is not None),
                ("agent_config", self.agent_config is not None),
                ("agent_view", self.agent_view is not None),
                ("wake", self.wake is not None),
                ("live_view", self.live_view is not None),
                ("requires.image_model", self.requires.image_model),
            ) if present
        ]
        if agent_facing:
            raise ValueError(f"agent-facing parts need a command: {', '.join(agent_facing)}")
        return self

    @model_validator(mode="after")
    def _service_has_no_setup(self) -> "ExtensionManifest":
        # Setup finishing enables the extension from a background thread
        # (setup_runner.start_setup), which would skip the app process's
        # service reconcile. Refused here rather than left as a silent gap.
        if self.service and self.setup.required:
            raise ValueError("service cannot be combined with setup.required")
        return self

    @model_validator(mode="after")
    def _agent_view_config_declared(self) -> "ExtensionManifest":
        if self.agent_view is not None and self.agent_view.requires_config and self.agent_config is None:
            raise ValueError("agent_view.requires_config needs an agent_config block")
        return self

    @model_validator(mode="after")
    def _wake_interval_is_a_number_field(self) -> "ExtensionManifest":
        # Events are per agent and only configured agents are polled, so a
        # wake extension needs per-agent settings, whichever interval it uses.
        if self.wake is None:
            return self
        if self.agent_config is None:
            raise ValueError("wake needs an agent_config block")
        if self.wake.interval_field is None:
            # A fixed interval_seconds names no field.
            return self
        field = next((item for item in self.agent_config.fields if item.key == self.wake.interval_field), None)
        if field is None or field.kind != "number":
            raise ValueError(f"wake.interval_field {self.wake.interval_field!r} must name a number field of agent_config")
        return self

    @field_validator("prompt")
    @classmethod
    def _prompt_is_a_sibling_file(cls, value: str | None) -> str | None:
        # The prompt is read from the extension folder; a path would let a
        # manifest point the prompt block at any file on disk.
        if value is not None and (not value or "/" in value or "\\" in value or value.startswith(".")):
            raise ValueError("prompt must be a file name inside the extension folder")
        return value


def config_value(spec: ConfigSpec, stored: Mapping[str, str], key: str) -> str:
    """Return one declared field's value from a stored config.

    A config saved before a field existed has no key for it; a field with a
    manifest ``default`` then reads as that default. This is the declared
    schema default, not a cover for missing data.

    Args:
        spec: The extension's ``agent_config`` or ``settings`` block.
        stored: The stored values (one agent's, or the extension's).
        key: A field key.

    Returns:
        The stored value, or the field's ``default`` when the key is absent.

    Raises:
        KeyError: ``key`` is not a declared field, or it is absent from
            ``stored`` and the field has no default.
    """
    field = next((item for item in spec.fields if item.key == key), None)
    if field is None:
        raise KeyError(f"{key!r} is not a declared config field")
    if key in stored:
        return stored[key]
    if field.default is None:
        raise KeyError(f"stored config has no {key!r} and the field has no default")
    return field.default


def load_manifest(path: Path) -> ExtensionManifest:
    """Read and validate one ``manifest.json``.

    Args:
        path: The manifest file.

    Returns:
        The validated manifest.

    Raises:
        ManifestError: The file is missing or unreadable, is not JSON, or
            fails the schema (the message names the first problems).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read {path.name}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path.name} is not valid JSON: {exc.msg} (line {exc.lineno})") from exc
    try:
        return ExtensionManifest.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or '(root)'}: {err['msg']}"
            for err in exc.errors()[:5]
        )
        raise ManifestError(f"{path.name} is invalid: {problems}") from exc
