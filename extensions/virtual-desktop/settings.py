"""Virtual Desktop — the manifest's tunables and each agent's desktop settings.

``DesktopDefaults`` is the manifest's ``defaults`` block, validated when the
extension is created. ``DesktopSettings`` is what one desktop is started with
(screen, memory and CPU limits); it is read from the agent's stored
``agent_config`` through the host's ``agent_config_value``, so a field added
to the manifest later reads as its declared default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from core.extensions.manifest import AgentConfigSpec, agent_config_value

# The agent_config field keys (manifest.json).
FIELD_SCREEN_WIDTH = "screen_width"
FIELD_SCREEN_HEIGHT = "screen_height"
FIELD_MEMORY_MB = "memory_mb"
FIELD_CPUS = "cpus"
FIELD_REMINDER_MINUTES = "power_down_reminder_minutes"


class DesktopDefaults(BaseModel):
    """The manifest's ``defaults`` block.

    Attributes:
        image_version: The image contract version; containers labelled with
            another one are recreated on it (the home volume is kept).
        image_repo: The image repository; the tag is ``image_version``.
        shm_size_mb: ``/dev/shm`` size; browsers crash on Docker's 64 MB.
        image_max_px: A screenshot whose longest side is larger is shrunk to it.
        boot_timeout_s: How long a start may take before it is an error.
        stop_timeout_s: How long a stop waits before the container is killed.
        settle_ms: The pause after an action before its screenshot.
        launch_settle_ms: The pause after ``vm launch`` before its screenshot.
        type_delay_ms: The delay between typed characters.
        scroll_clicks: ``vm scroll``'s default number of wheel clicks.
        wait_default_s: ``vm wait``'s default limit.
        wait_poll_ms: How often ``vm wait`` looks at the screen.
        wait_settle_ms: How long the screen must hold still after a change.
        run_timeout_s: ``vm run``'s time limit (exit 124 when reached).
        run_max_output_bytes: ``vm run`` output beyond this is cut, per stream.
        screenshots_keep: Screenshots kept per agent.

    Raises:
        pydantic.ValidationError: A key is missing, unknown, or out of bounds.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    image_version: str = Field(pattern=r"^[0-9]+$")
    image_repo: str = Field(min_length=1)
    shm_size_mb: int = Field(gt=0)
    image_max_px: int = Field(gt=0)
    boot_timeout_s: int = Field(gt=0)
    stop_timeout_s: int = Field(ge=0)
    settle_ms: int = Field(ge=0)
    launch_settle_ms: int = Field(ge=0)
    type_delay_ms: int = Field(ge=0)
    scroll_clicks: int = Field(ge=1)
    wait_default_s: float = Field(gt=0)
    wait_poll_ms: int = Field(gt=0)
    wait_settle_ms: int = Field(ge=0)
    run_timeout_s: int = Field(gt=0)
    run_max_output_bytes: int = Field(gt=0)
    screenshots_keep: int = Field(ge=1)

    @property
    def image_tag(self) -> str:
        """The image every desktop runs, e.g. ``bossmod/virtual-desktop:1``."""
        return f"{self.image_repo}:{self.image_version}"


@dataclass(frozen=True)
class DesktopSettings:
    """What one desktop is started with.

    Equality decides whether a settings save restarts a running desktop:
    only a difference here does (the reminder minutes are not part of it).

    Attributes:
        width: Screen width in pixels.
        height: Screen height in pixels.
        memory_mb: Memory ceiling in MB (not reserved).
        cpus: CPU ceiling in cores (not reserved).
    """

    width: int
    height: int
    memory_mb: int
    cpus: int


def settings_from_config(spec: AgentConfigSpec, stored: Mapping[str, str]) -> DesktopSettings:
    """Read an agent's desktop settings from its stored config.

    Args:
        spec: The manifest's ``agent_config`` block.
        stored: The agent's stored values (or candidate values on save).

    Returns:
        The settings; a field missing from ``stored`` reads as its default.

    Raises:
        ValueError: A stored value is not a whole number (a corrupt row).
    """
    return DesktopSettings(
        width=_int_field(spec, stored, FIELD_SCREEN_WIDTH),
        height=_int_field(spec, stored, FIELD_SCREEN_HEIGHT),
        memory_mb=_int_field(spec, stored, FIELD_MEMORY_MB),
        cpus=_int_field(spec, stored, FIELD_CPUS),
    )


def reminder_minutes_from_config(spec: AgentConfigSpec, stored: Mapping[str, str]) -> int:
    """Read an agent's power-down reminder, in minutes (0 = never).

    Raises:
        ValueError: The stored value is not a whole number (a corrupt row).
    """
    return _int_field(spec, stored, FIELD_REMINDER_MINUTES)


def _int_field(spec: AgentConfigSpec, stored: Mapping[str, str], key: str) -> int:
    raw = agent_config_value(spec, stored, key)
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"stored desktop setting {key} is not a whole number: {raw!r}") from None
