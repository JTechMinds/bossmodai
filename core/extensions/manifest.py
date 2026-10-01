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


class AgentConfigField(_Strict):
    """One per-agent setting the operator fills in at an agent's desk.

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
    def _number_bounds_and_default(self) -> "AgentConfigField":
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


class AgentConfigSpec(_Strict):
    """Declarative per-agent settings (``contract.SupportsAgentConfig``).

    Attributes:
        label: The name of the setting group, e.g. "Microsoft 365 mailbox".
        help: Operator guidance shown above the form; paragraphs split on
            blank lines.
        fields: At least one field; keys unique, at most one ``summary``.
    """

    label: str = Field(min_length=1)
    help: str = Field(min_length=1)
    fields: tuple[AgentConfigField, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_unique_and_one_summary(self) -> "AgentConfigSpec":
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

    Attributes:
        interval_field: The ``agent_config`` ``number`` field holding each
            agent's poll interval in seconds.
    """

    interval_field: str = Field(min_length=1)


class ExtensionManifest(_Strict):
    """Validated ``manifest.json``. Unknown keys are rejected.

    ``defaults`` is the extension's own tunables; the host does not interpret
    it, the extension validates it when it is created.
    """

    id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    command: CommandSpec
    prompt: str | None = None
    requires: RequiresSpec = RequiresSpec()
    setup: SetupSpec
    # True when the instance implements contract.SupportsLiveView.
    live_view: bool = False
    # Set when the instance implements contract.SupportsAgentConfig.
    agent_config: AgentConfigSpec | None = None
    # Set when the instance implements contract.SupportsAgentView.
    agent_view: AgentViewSpec | None = None
    # Set when the instance implements contract.SupportsWake.
    wake: WakeSpec | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _agent_view_config_declared(self) -> "ExtensionManifest":
        if self.agent_view is not None and self.agent_view.requires_config and self.agent_config is None:
            raise ValueError("agent_view.requires_config needs an agent_config block")
        return self

    @model_validator(mode="after")
    def _wake_interval_is_a_number_field(self) -> "ExtensionManifest":
        # Events are per agent and only configured agents are polled, so a
        # wake extension needs per-agent settings holding its interval.
        if self.wake is None:
            return self
        if self.agent_config is None:
            raise ValueError("wake needs an agent_config block")
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


def agent_config_value(spec: AgentConfigSpec, stored: Mapping[str, str], key: str) -> str:
    """Return one declared field's value from an agent's stored config.

    A config saved before a field existed has no key for it; a field with a
    manifest ``default`` then reads as that default. This is the declared
    schema default, not a cover for missing data.

    Args:
        spec: The extension's ``agent_config`` block.
        stored: The agent's stored values.
        key: A field key.

    Returns:
        The stored value, or the field's ``default`` when the key is absent.

    Raises:
        KeyError: ``key`` is not a declared field, or it is absent from
            ``stored`` and the field has no default.
    """
    field = next((item for item in spec.fields if item.key == key), None)
    if field is None:
        raise KeyError(f"{key!r} is not a declared agent_config field")
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
