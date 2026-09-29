"""BossMod AI — extension ``manifest.json`` schema and loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MANIFEST_FILE = "manifest.json"

# An id names a data folder and a settings entry, so it stays a plain slug.
_ID_PATTERN = r"^[a-z][a-z0-9-]{1,40}$"
# A command name is typed by agents as the first CLI token.
_COMMAND_PATTERN = r"^[a-z][a-z0-9-]{1,15}$"


class ManifestError(Exception):
    """A manifest is missing, unreadable, not JSON, or fails the schema."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandSpec(_Strict):
    """The one CLI command namespace an extension adds (``bv …``)."""

    name: str = Field(pattern=_COMMAND_PATTERN)
    summary: str = Field(min_length=1)
    usage: str = Field(min_length=1)
    help: str = Field(min_length=1)


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
        kind: ``text``, ``secret`` (never sent back to the UI) or ``email``
            (checked for an address shape on save).
        required: Whether an empty value is refused on save.
        summary: The one field whose value the desk shows once configured.
    """

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1)
    kind: Literal["text", "secret", "email"]
    required: bool = True
    summary: bool = False


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


class AgentViewSpec(_Strict):
    """A read-only per-agent record list (``contract.SupportsAgentView``).

    Attributes:
        label: The desk action that opens it, e.g. "Open inbox".
        requires_config: The view needs the agent's ``agent_config`` stored.
    """

    label: str = Field(min_length=1)
    requires_config: bool = True


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
    defaults: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _agent_view_config_declared(self) -> "ExtensionManifest":
        if self.agent_view is not None and self.agent_view.requires_config and self.agent_config is None:
            raise ValueError("agent_view.requires_config needs an agent_config block")
        return self

    @field_validator("prompt")
    @classmethod
    def _prompt_is_a_sibling_file(cls, value: str | None) -> str | None:
        # The prompt is read from the extension folder; a path would let a
        # manifest point the prompt block at any file on disk.
        if value is not None and (not value or "/" in value or "\\" in value or value.startswith(".")):
            raise ValueError("prompt must be a file name inside the extension folder")
        return value


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
