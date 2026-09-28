"""BossMod AI — extension ``manifest.json`` schema and loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
    """Whether a one-click setup step (a download) must run before enabling."""

    required: bool
    label: str | None = None


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
    defaults: dict[str, Any] = Field(default_factory=dict)

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
