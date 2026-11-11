"""Prompt-cache figures for model calls, carried beside their prompt tokens.

A provider may report how many of a call's prompt tokens it served from its
cache, or report nothing. "Not reported" is not "0% cached", so the figures
travel as a pair: wherever a step's or sub-call's prompt tokens are counted,
summed or stored, a :class:`PromptCacheUsage` for the same calls goes with
them. The turn's totals are derived from its steps in ``db.create_diagnostic``.

Pure: no I/O, and no litellm import (``LLMResponse`` is a type-only import).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from core.llm.client import LLMResponse


@dataclass(frozen=True, slots=True)
class PromptCacheUsage:
    """Prompt-cache figures for one or more model calls.

    A call that reported nothing adds to neither number, so 0/0 reads "not
    reported", never "0% cached".

    Attributes:
        cached_tokens: Prompt tokens the provider served from cache, summed
            over the calls that reported.
        reported_prompt_tokens: Prompt tokens of the calls whose provider
            reported a cache figure (0 included).

    Raises:
        ValueError: On construction, unless
            ``0 <= cached_tokens <= reported_prompt_tokens``.
    """

    cached_tokens: int
    reported_prompt_tokens: int

    ZERO: ClassVar["PromptCacheUsage"]

    def __post_init__(self) -> None:
        if not 0 <= self.cached_tokens <= self.reported_prompt_tokens:
            raise ValueError(
                f"Prompt cache figures must satisfy 0 <= cached ({self.cached_tokens}) "
                f"<= reported prompt tokens ({self.reported_prompt_tokens})"
            )

    def __add__(self, other: PromptCacheUsage) -> PromptCacheUsage:
        """Sum two sets of calls' figures."""
        if not isinstance(other, PromptCacheUsage):
            return NotImplemented
        return PromptCacheUsage(
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reported_prompt_tokens=self.reported_prompt_tokens + other.reported_prompt_tokens,
        )

    @classmethod
    def of(cls, response: LLMResponse) -> PromptCacheUsage:
        """The figures for one model call.

        Args:
            response: The call's response. Its ``cached_prompt_tokens`` was
                checked against its ``prompt_tokens`` by the client.

        Returns:
            :attr:`ZERO` when the provider reported no figure; otherwise the
            figure with the call's prompt tokens as the reported tokens.

        Raises:
            ValueError: The response's cached figure exceeds its prompt tokens
                (only a hand-built response can carry one).
        """
        if response.cached_prompt_tokens is None:
            return cls.ZERO
        return cls(
            cached_tokens=response.cached_prompt_tokens,
            reported_prompt_tokens=response.prompt_tokens,
        )


PromptCacheUsage.ZERO = PromptCacheUsage(cached_tokens=0, reported_prompt_tokens=0)
