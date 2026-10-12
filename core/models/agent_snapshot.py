"""BossMod AI — Agent snapshot model.

One current copy of an agent's SETUP — the fields the create form fills — kept
so that an agent can be recreated after it was deleted, or started again from
one that still exists. There is one row per agent and no version history:
every capture overwrites the last. Add agent's Recent scope lists these.

Never a secret. The model mirrors the table's explicit column list: an agent
keeps no credentials of its own, only the id of its AI connection, which a
recreate links again (when that connection still exists) with the two
thinking levels.
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
from core.models.thinking import ThinkingChoice


# Policy keys older snapshots still store; see _parse_prompt_history_policy.
_RETIRED_POLICY_KEYS = frozenset({"last_n_histories", "max_allowed_history_tokens"})


class AgentSnapshot(BaseModel):
    """One agent's setup as last captured, and whether that agent is gone.

    ``agent_id`` is the agent it was taken from; it is not a foreign key, so
    the row outlives a delete. ``captured_at`` is when it was last written —
    Recent lists newest first by it — and ``deleted_at`` is set only by the
    final capture a delete makes. ``prompt_history_policy`` is the policy
    fields a snapshot keeps (``earliest_ts_allowed``, ``include_notifications``),
    or ``None`` when the agent had no policy row when it was captured.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    name: str
    role: str | None = None
    description: str | None = None
    done_fail_bar: str | None = None
    communication: dict[str, str] | None = None
    color: str | None = None
    connection_id: str | None = None
    thinking_social: ThinkingChoice = "default"
    thinking_work: ThinkingChoice = "default"
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

        The retired keys ``last_n_histories`` and ``max_allowed_history_tokens``
        are dropped on read: snapshots captured before the per-thread history
        budget replaced them still carry them, and refusing those rows would
        brick every existing snapshot (stored data is validated on read).
        """
        if value is None:
            return value
        if isinstance(value, dict):
            decoded: Any = value
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"prompt_history_policy is not valid JSON: {exc}") from exc
            if not isinstance(decoded, dict):
                raise ValueError("prompt_history_policy must decode to a JSON object")
        else:
            raise ValueError(
                f"prompt_history_policy must be a JSON object string, got {type(value).__name__}"
            )
        return {key: item for key, item in decoded.items() if key not in _RETIRED_POLICY_KEYS}
