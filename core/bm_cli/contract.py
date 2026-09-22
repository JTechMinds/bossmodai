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
    # Operator-visible say from the product envelope; host posts before CLI.
    operator_say: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "BossModCliCall":
        if not self.command.strip():
            raise ValueError('"bm_cli" requires a non-empty "command"')
        if self.content is not None and not isinstance(self.content, str):
            raise ValueError('"bm_cli" content must be a string when provided')
        if self.operator_say is not None and (
            not isinstance(self.operator_say, str) or not self.operator_say.strip()
        ):
            raise ValueError('"operator_say" must be a non-empty string when provided')
        return self


def maybe_parse_bm_cli_call(payload: Any) -> BossModCliCall | None:
    """Return a validated BossMod CLI call from the model-facing compact payload."""
    if not isinstance(payload, dict):
        return None

    if payload.get("act") == "cli":
        # ``msg`` is folded away for wire CLI; decision parse may reattach as
        # ``operator_say`` after peel. Reject other invented keys.
        extra_root = set(payload) - {"act", "data", "th", "msg"}
        if extra_root:
            raise ValueError(f'unexpected top-level keys: {", ".join(sorted(extra_root))}')
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError('"data" must be an object for act="cli"')
        extra_data = set(data) - {"cmd", "body"}
        if extra_data:
            raise ValueError(f'unexpected cli data keys: {", ".join(sorted(extra_data))}')
        operator_say = payload.get("msg")
        if operator_say in (None, ""):
            operator_say = None
        return BossModCliCall.model_validate(
            {
                "action": "bm_cli",
                "command": data.get("cmd"),
                "content": data.get("body"),
                "thought": payload.get("th", ""),
                "operator_say": operator_say,
            }
        )

    return None
