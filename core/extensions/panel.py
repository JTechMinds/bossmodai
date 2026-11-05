"""BossMod AI — the status panel an extension shows on its Extensions card.

A manifest ``panel: true`` extension implements ``contract.SupportsPanel`` and
returns a :class:`PanelStatus`: a sentence, an optional error, then titled
groups of facts. The card renders it generically, so core never knows what
the extension is. These are plain data models with no behaviour beyond their
own validation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PanelFact(_Strict):
    """One labelled value on the panel, e.g. an address to type or a code to share.

    Attributes:
        label: What the value is, e.g. "Address".
        value: The value shown, verbatim.
        copyable: The card offers a Copy button for the value.
        secret: The card masks the value until the operator reveals it.
        hint: One plain sentence shown under the value saying what it is
            for or where it goes; ``None`` for a value that needs no
            explanation.
    """

    label: str = Field(min_length=1)
    value: str = Field(min_length=1)
    copyable: bool = False
    secret: bool = False
    hint: str | None = Field(default=None, min_length=1)


class PanelGroup(_Strict):
    """A titled run of facts the card shows together, apart from the other groups.

    Attributes:
        title: The group's heading.
        intro: Plain sentences shown under the heading, before the facts;
            ``None`` when the facts speak for themselves.
        facts: The group's facts, in display order.

    Raises:
        pydantic.ValidationError: The group has neither an intro nor a fact
            (an extension bug, refused at construction rather than shown as
            a bare heading).
    """

    title: str = Field(min_length=1)
    intro: str | None = Field(default=None, min_length=1)
    facts: list[PanelFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_content(self) -> "PanelGroup":
        if self.intro is None and not self.facts:
            raise ValueError(f"panel group {self.title!r} needs an intro or at least one fact")
        return self


class PanelStatus(_Strict):
    """What a panel extension's card shows right now.

    Attributes:
        message: One plain sentence about the current state.
        error: A plain sentence about a problem, shown as an alert; ``None``
            when nothing is wrong. Never carries a secret.
        groups: Titled groups of facts, in display order; empty when the
            sentence says everything (e.g. not running).
    """

    message: str = Field(min_length=1)
    error: str | None = None
    groups: list[PanelGroup] = Field(default_factory=list)
