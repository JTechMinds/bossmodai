"""BossMod AI — Compact BossMod CLI contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BossModCliCall(BaseModel):
    """Validated internal representation of a BossMod CLI call."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["bm_cli"]
    command: str
    content: str | None = None
    thought: str = Field(default="")

    @model_validator(mode="after")
    def _validate_shape(self) -> "BossModCliCall":
        if not self.command.strip():
            raise ValueError('"bm_cli" requires a non-empty "command"')
        if self.content is not None and not isinstance(self.content, str):
            raise ValueError('"bm_cli" content must be a string when provided')
        return self


def maybe_parse_bm_cli_call(payload: Any) -> BossModCliCall | None:
    """Return a validated BossMod CLI call from the model-facing compact payload."""
    if not isinstance(payload, dict):
        return None

    if payload.get("act") == "cli":
        extra_root = set(payload) - {"act", "data", "th"}
        if extra_root:
            raise ValueError(f'unexpected top-level keys: {", ".join(sorted(extra_root))}')
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError('"data" must be an object for act="cli"')
        extra_data = set(data) - {"cmd", "body"}
        if extra_data:
            raise ValueError(f'unexpected cli data keys: {", ".join(sorted(extra_data))}')
        return BossModCliCall.model_validate(
            {
                "action": "bm_cli",
                "command": data.get("cmd"),
                "content": data.get("body"),
                "thought": payload.get("th", ""),
            }
        )

    return None
