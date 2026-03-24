"""BossMod AI — Prompt-visible BossMod CLI contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BossModCliCall(BaseModel):
    """A structured request to query the controlled BossMod CLI surface."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["bm_cli"]
    command: str
    thought: str = Field(default="")

    @model_validator(mode="after")
    def _validate_shape(self) -> "BossModCliCall":
        if not self.command.strip():
            raise ValueError('"bm_cli" requires a non-empty "command"')
        return self


def maybe_parse_bm_cli_call(payload: Any) -> BossModCliCall | None:
    """Return a validated BossMod CLI call when the payload requests one."""
    if not isinstance(payload, dict):
        return None
    if payload.get("action") != "bm_cli":
        return None
    return BossModCliCall.model_validate(payload)


def render_bm_cli_guidance() -> str:
    """Render the authoritative starter guidance for BossMod CLI access."""
    return "\n".join(
        [
            "BOSSMOD CLI:",
            '- Use {"action":"bm_cli","command":"...","thought":"..."} when you need authoritative self/project information.',
            "- BossMod CLI results are turn-local. Use them for the current reply/action; they do not become normal chat memory.",
            "- Starter commands:",
            "  - me get status",
            "  - me get location",
            "  - me ls",
            "  - me cat <file>",
            "  - project <project-name> ls",
            "  - project <project-name> cat <file>",
        ]
    )
