"""Host-safe capability for one stored catalog entry. Pure: no I/O, no model names.

Publication, request validation and the status line all read capability from
here, so there is one rule. Effort names arrive raw from the account catalog
(``catalog.normalize``) and are filtered through the host's thinking vocabulary
at read time: adding a host level later lights it up for stored catalogs
without a re-fetch. A name the host does not know is omitted and reported,
never published, because the host rejects unknown level keys on every
connection read.
"""
from dataclasses import dataclass

from core.models.thinking import THINKING_LEVELS

# Codex's ReasoningEffort spells the host's ``off`` level ``none``.
_WIRE_OFF, _HOST_OFF = "none", "off"
# A literal ``off`` token is not a Codex effort, so it is not accepted either.
ACCEPTED_EFFORTS = frozenset({_WIRE_OFF, *THINKING_LEVELS} - {_HOST_OFF})


def host_key(effort: str) -> str:
    """The host level key an accepted wire effort publishes as (``none`` → ``off``)."""
    return _HOST_OFF if effort == _WIRE_OFF else effort


@dataclass(frozen=True)
class Capability:
    """What BossMod may offer and send for one model.

    Attributes:
        offered: Accepted wire efforts, in catalog order.
        omitted: Effort names the host vocabulary does not know, in catalog
            order; reported in status, never published.
        default_effort: The effort ChatGPT uses for Server default, raw, or None.
        images: ``supported``, ``unsupported`` or ``unknown``.
        listed: Whether the catalog listed thinking options at all. False for a
            pre-upgrade stored entry, which has no ``efforts`` key.
    """

    offered: tuple[str, ...] = ()
    omitted: tuple[str, ...] = ()
    default_effort: str | None = None
    images: str = "unknown"
    listed: bool = False

    @classmethod
    def from_entry(cls, entry: dict) -> "Capability":
        """Build capability from one stored catalog entry.

        Args:
            entry: A model dict from ``catalog.normalize``, or a pre-upgrade
                stored entry with only ``slug``/``display_name``. Missing
                capability keys are the documented "not listed" / ``unknown``
                state, not an error.

        Returns:
            The entry's capability; never raises for missing fields.
        """
        efforts = entry.get("efforts")
        tokens = tuple(efforts) if efforts is not None else ()
        return cls(
            offered=tuple(t for t in tokens if t in ACCEPTED_EFFORTS),
            omitted=tuple(t for t in tokens if t not in ACCEPTED_EFFORTS),
            default_effort=entry.get("default_effort"),
            images=entry.get("images", "unknown"),
            listed=efforts is not None,
        )

    def thinking_levels(self) -> dict:
        """The host level map this model publishes: host key → Responses fragment."""
        return {host_key(effort): {"reasoning": {"effort": effort}} for effort in self.offered}

    def images_enabled(self, policy: str) -> bool:
        """Whether images may be sent under ``policy``; never for an unsupported model."""
        if self.images == "unsupported":
            return False
        return policy == "enabled" or (policy == "auto" and self.images == "supported")

    def image_conflict(self, policy: str) -> bool:
        """True when the operator chose Enabled but the catalog says no image input."""
        return policy == "enabled" and self.images == "unsupported"

    def published_image_policy(self, policy: str) -> str:
        """The policy to publish: a conflicting Enabled is published as ``disabled``.

        The operator's stored choice is left as it is, so status can show the
        conflict instead of silently resolving it.
        """
        return "disabled" if self.image_conflict(policy) else policy
