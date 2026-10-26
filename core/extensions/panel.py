"""BossMod AI — the status panel an extension shows on its Extensions card.

A manifest ``panel: true`` extension implements ``contract.SupportsPanel`` and
returns a :class:`PanelStatus`; the card renders it generically, so core never
knows what the extension is. These are plain data models with no behaviour.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PanelFact(_Strict):
    """One labelled value on the panel, e.g. an address to type or a code to share.

    Attributes:
        label: What the value is, e.g. "Address".
        value: The value shown, verbatim.
        copyable: The card offers a Copy button for the value.
        secret: The card masks the value until the operator reveals it.
    """

    label: str = Field(min_length=1)
    value: str = Field(min_length=1)
    copyable: bool = False
    secret: bool = False


class PanelAction(_Strict):
    """One button on the panel; the host passes ``key`` and ``values`` back unchanged.

    Attributes:
        key: The action name ``SupportsPanel.panel_action`` receives.
        label: The button text.
        values: Fixed parameters the extension wants back with the action.
    """

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    values: dict[str, str] = Field(default_factory=dict)


class PanelStatus(_Strict):
    """What a panel extension's card shows right now.

    Attributes:
        message: One plain sentence about the current state.
        error: A plain sentence about a problem, shown as an alert; ``None``
            when nothing is wrong. Never carries a secret.
        facts: Labelled values, in display order.
        actions: Buttons, in display order.
    """

    message: str = Field(min_length=1)
    error: str | None = None
    facts: list[PanelFact] = Field(default_factory=list)
    actions: list[PanelAction] = Field(default_factory=list)
