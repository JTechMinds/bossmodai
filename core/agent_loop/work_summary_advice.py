"""Turn an over-limit step summary into advice the operator can act on.

Pure: no I/O. ``api/routes/needs.py`` reads each stored
``work_summary_overflows`` row and the current work-context settings and
asks this module for the Needs card's text, so the numbers always reflect
the settings as they are now, and the card hides itself once they fit.

The setting names in the text ("Work summary size", "Work kept after
summarizing") are the Settings labels (``ui/static/js/settings/
settings-context-meta.js``), so the operator finds them by name. Every size
is in tokens, the unit of every context budget (``core/context_budget.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.agent_loop.work_summary import SUMMARY_PERCENT_SETTING, TRIM_PERCENT_SETTING, SummaryLimits
from core.models import WorkSummaryOverflow


@dataclass(frozen=True, slots=True)
class SummaryAdvice:
    """The Needs card for one over-limit summary.

    Attributes:
        title: The card's heading, naming the agent.
        sub: The card's text: what the model wrote, which setting to change,
            to what, and what that changes.
        setting_key: The setting the card's button opens.
    """

    title: str
    sub: str
    setting_key: str


def advise_summary_limit(
    *, agent_name: str, overflow: WorkSummaryOverflow, limits: SummaryLimits,
) -> SummaryAdvice | None:
    """Say which setting to raise so the agent's next step summary fits.

    The suggested Work summary size is the smallest whole percent whose
    limit fits the model's first summary at the Work budget it was written
    under: ``ceil(summary_tokens * 100 / budget_tokens)``. The first summary,
    not the shortest rewrite, so the next one fits without rewrites and the
    rewrites become margin. A percentage stays right when the Work budget has
    changed since, because a summary grows with the folded input, which
    grows with the budget.

    When that percentage is not below Work kept after summarizing (which the
    summary share must stay under), no single setting fixes it: the advice
    says so and points at Work kept after summarizing instead.

    Args:
        agent_name: The agent's display name, for the card.
        overflow: The stored measurement of the summary that did not fit.
        limits: The current work-context settings
            (``work_summary.read_summary_limits``).

    Returns:
        The card's title, text and setting, or ``None`` when the current
        Work summary size already fits what the model wrote.
    """
    needed = math.ceil(overflow.summary_tokens * 100 / overflow.budget_tokens)
    if needed <= limits.summary_percent:
        return None
    title = f"{agent_name}'s work summary is too long for your settings"
    wrote = (
        f"Its model ({overflow.model}) wrote a {overflow.summary_tokens:,}-token summary of its earlier steps; "
        f"the limit was {overflow.limit_tokens:,}."
    )
    if needed < limits.trim_percent:
        new_limit = limits.budget * needed // 100
        kept_now = limits.target - limits.cap
        kept_new = limits.target - new_limit
        sub = (
            f"{wrote} Set Work summary size to {needed}% or more (it is {limits.summary_percent}%). "
            f"Summaries can then use up to {new_limit:,} tokens, and recent steps kept word for word "
            f"go from {kept_now:,} to {kept_new:,} tokens. Total context stays the same."
        )
        return SummaryAdvice(title=title, sub=sub, setting_key=SUMMARY_PERCENT_SETTING)
    sub = (
        f"{wrote} That needs Work summary size of {needed}%, which has to stay below Work kept after "
        f"summarizing ({limits.trim_percent}%). Raise Work kept after summarizing above {needed}% first, "
        f"or give {agent_name} a model that writes shorter summaries."
    )
    return SummaryAdvice(title=title, sub=sub, setting_key=TRIM_PERCENT_SETTING)
