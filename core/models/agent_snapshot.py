"""BossMod AI — Agent snapshot model.

One current copy of an agent's SETUP — the fields the create form fills — kept
so that an agent can be recreated after it was deleted, or started again from
one that still exists. There is one row per agent and no version history:
every capture overwrites the last. Add agent's Recent scope lists these.

Never a secret. The model mirrors the table's explicit column list, which
leaves out ``api_key``, ``api_base_url`` and ``extra_body``: those come from an
AI connection and may carry credentials. A recreate re-links a connection by
the model NAMES kept here, the way the Edit form already matches them.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

# The validator Agent itself runs over the same JSON TEXT column: one reading
# of a stored communication block, so a snapshot cannot accept a block an agent
# would refuse, or the reverse.
from core.models.agent import _coerce_communication


class AgentSnapshot(BaseModel):
    """One agent's setup as last captured, and whether that agent is gone.

    ``agent_id`` is the agent it was taken from; it is not a foreign key, so
    the row outlives a delete. ``captured_at`` is when it was last written —
    Recent lists newest first by it — and ``deleted_at`` is set only by the
    final capture a delete makes. ``prompt_history_policy`` is the four policy
    fields the form edits (``last_n_histories``, ``max_allowed_history_tokens``,
    ``earliest_ts_allowed``, ``include_notifications``), or ``None`` when the
    agent had no policy row when it was captured.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    name: str
    role: str | None = None
    description: str | None = None
    done_fail_bar: str | None = None
    communication: dict[str, str] | None = None
    prompt_template: str | None = None
    color: str | None = None
    model_social: str | None = None
    model_work: str | None = None
    model_reasoning: str | None = None
    model_extraction: str | None = None
    model_self_queue: str | None = None
    desk_x: int | None = None
    desk_y: int | None = None
    prompt_history_policy: dict[str, Any] | None = None
    captured_at: datetime
    deleted_at: datetime | None = None

    @field_validator("communication", mode="before")
    @classmethod
    def _normalize_communication(cls, value: Any) -> dict[str, str] | None:
        return _coerce_communication(value)

    @field_validator("prompt_history_policy", mode="before")
    @classmethod
    def _parse_prompt_history_policy(cls, value: Any) -> Any:
        """Decode the ``prompt_history_policy`` JSON ``TEXT`` column.

        Accepts an already-decoded mapping unchanged so the model can be built
        in Python as well as from a row, and ``None`` for an agent that had no
        policy when it was captured.

        Raises ``ValueError`` — surfaced by Pydantic as a ``ValidationError`` —
        when the column is not a string, is not valid JSON, or does not decode
        to an object. A corrupt row is reported rather than read as "no
        policy", which would silently hand the create form the defaults.
        """
        if value is None or isinstance(value, dict):
            return value
        if not isinstance(value, str):
            raise ValueError(
                f"prompt_history_policy must be a JSON object string, got {type(value).__name__}"
            )
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"prompt_history_policy is not valid JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ValueError("prompt_history_policy must decode to a JSON object")
        return decoded
