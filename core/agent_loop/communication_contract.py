"""Closed-enum communication contract for packs, hires, and the role prompt.

Four tiny fields only: tone, density, jargon, audience. No free-text essays.
Omitted fields fill from the hire specialty (auditor: precise-but-scannable;
planner: product-clear; other roles get a small family default).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from core.agent_loop.specialty import specialty_family, tokenize

Tone = Literal["precise-but-scannable", "product-clear", "direct", "warm"]
Density = Literal["scannable", "compact", "thorough"]
Jargon = Literal["none", "light", "field"]
Audience = Literal["operator", "implementer", "mixed"]

COMMUNICATION_KEYS = ("tone", "density", "jargon", "audience")
TONE_VALUES: frozenset[str] = frozenset(
    {"precise-but-scannable", "product-clear", "direct", "warm"}
)
DENSITY_VALUES: frozenset[str] = frozenset({"scannable", "compact", "thorough"})
JARGON_VALUES: frozenset[str] = frozenset({"none", "light", "field"})
AUDIENCE_VALUES: frozenset[str] = frozenset({"operator", "implementer", "mixed"})
_ENUMS: dict[str, frozenset[str]] = {
    "tone": TONE_VALUES,
    "density": DENSITY_VALUES,
    "jargon": JARGON_VALUES,
    "audience": AUDIENCE_VALUES,
}

_AUDITOR_TOKENS = frozenset({
    "auditor", "audit", "reviewer", "review", "qa", "tester", "test",
})
_PLANNER_TOKENS = frozenset({"planner", "planning", "plan", "pm"})


@dataclass(frozen=True)
class CommunicationContract:
    """One closed-enum communication block. Always four keys; never prose."""

    tone: Tone
    density: Density
    jargon: Jargon
    audience: Audience

    def as_dict(self) -> dict[str, str]:
        return {
            "tone": self.tone,
            "density": self.density,
            "jargon": self.jargon,
            "audience": self.audience,
        }

    def prompt_block(self) -> str:
        """Role-prompt injection. Short labeled lines, not an essay."""
        return (
            "Communication:\n"
            f"- tone: {self.tone}\n"
            f"- density: {self.density}\n"
            f"- jargon: {self.jargon}\n"
            f"- audience: {self.audience}"
        )


AUDITOR_DEFAULT = CommunicationContract(
    tone="precise-but-scannable",
    density="scannable",
    jargon="field",
    audience="operator",
)
PLANNER_DEFAULT = CommunicationContract(
    tone="product-clear",
    density="scannable",
    jargon="light",
    audience="mixed",
)
_FAMILY_DEFAULTS: dict[str, CommunicationContract] = {
    "review": AUDITOR_DEFAULT,
    "coordinate": PLANNER_DEFAULT,
    "write": CommunicationContract(
        tone="product-clear", density="scannable", jargon="none", audience="operator",
    ),
    "implement": CommunicationContract(
        tone="direct", density="compact", jargon="field", audience="implementer",
    ),
    "research": CommunicationContract(
        tone="precise-but-scannable", density="thorough", jargon="field", audience="operator",
    ),
    "design": CommunicationContract(
        tone="product-clear", density="scannable", jargon="light", audience="operator",
    ),
}
FALLBACK_DEFAULT = CommunicationContract(
    tone="direct",
    density="scannable",
    jargon="light",
    audience="operator",
)


class CommunicationContractError(ValueError):
    """Raised when a communication block is an essay or an unknown enum."""

    def __init__(self, message: str, *, code: str = "invalid_schema") -> None:
        super().__init__(message)
        self.code = code


def default_communication(specialty: str | None) -> CommunicationContract:
    """Return the locked default block for one hire specialty."""
    tokens = tokenize(specialty)
    if tokens & _AUDITOR_TOKENS:
        return AUDITOR_DEFAULT
    if tokens & _PLANNER_TOKENS:
        return PLANNER_DEFAULT
    family = specialty_family(specialty)
    if family and family in _FAMILY_DEFAULTS:
        return _FAMILY_DEFAULTS[family]
    return FALLBACK_DEFAULT


def parse_communication(
    value: Any,
    *,
    specialty: str | None = None,
) -> CommunicationContract:
    """Parse a mapping of closed enums. Essays and unknown values raise.

    ``None`` or an empty mapping fills every field from ``specialty``.
    A partial mapping fills only the missing keys. Extra keys are ignored.
    """
    if value is None:
        return default_communication(specialty)
    if isinstance(value, CommunicationContract):
        return value
    if isinstance(value, str):
        raise CommunicationContractError(
            "Pack field 'communication' must be a mapping of closed enums, "
            "not a free-text essay.",
        )
    if not isinstance(value, dict):
        raise CommunicationContractError(
            "Pack field 'communication' must be a mapping of closed enums.",
        )
    defaults = default_communication(specialty)
    parsed: dict[str, str] = {}
    for key in COMMUNICATION_KEYS:
        raw = value.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            parsed[key] = getattr(defaults, key)
            continue
        if not isinstance(raw, str):
            raise CommunicationContractError(
                f"Pack field 'communication.{key}' must be one of: "
                + ", ".join(sorted(_ENUMS[key]))
                + ".",
            )
        token = raw.strip().lower()
        if token not in _ENUMS[key]:
            raise CommunicationContractError(
                f"Pack field 'communication.{key}' must be one of: "
                + ", ".join(sorted(_ENUMS[key]))
                + ".",
            )
        parsed[key] = token
    return CommunicationContract(**parsed)  # type: ignore[arg-type]


def communication_from_agent(agent: Any) -> CommunicationContract:
    """Resolve the contract stored on an agent, or the specialty default."""
    stored = getattr(agent, "communication", None)
    role = getattr(agent, "role", None)
    return parse_communication(stored, specialty=role)


def load_communication_value(value: Any, *, specialty: str | None = None) -> dict[str, str] | None:
    """Coerce a stored JSON string or mapping into the four-key dict.

    ``None`` and empty stay ``None`` so an unset hire still resolves from
    specialty at prompt time rather than writing a default the operator never
    chose.
    """
    if value is None:
        return None
    if isinstance(value, CommunicationContract):
        return value.as_dict()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise CommunicationContractError(
                "communication must be a JSON object of closed enums.",
            ) from exc
    return parse_communication(value, specialty=specialty).as_dict()


def dump_communication_json(value: Any, *, specialty: str | None = None) -> str | None:
    """Serialize a communication mapping for the TEXT column."""
    loaded = load_communication_value(value, specialty=specialty)
    if loaded is None:
        return None
    return json.dumps(loaded, separators=(",", ":"))


def format_communication_block(contract: CommunicationContract) -> str:
    """Render the role-prompt communication section."""
    return contract.prompt_block()
