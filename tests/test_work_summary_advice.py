"""work_summary_advice: an over-limit step summary becomes a setting to raise, with numbers.

Pure: no database. The measurement is the stored ``work_summary_overflows``
row; the limits are the current work-context settings. Every size is in
tokens. The Large case is the plan's: a 40,000-token summary against a
29,280-token limit at Large's 146,400-token Work budget.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.agent_loop.work_summary import SummaryLimits
from core.agent_loop.work_summary_advice import advise_summary_limit
from core.models import WorkSummaryOverflow

_LARGE_WORK = 146_400


def _overflow(
    summary_tokens: int, *, limit_tokens: int = 29_280, budget_tokens: int = _LARGE_WORK,
) -> WorkSummaryOverflow:
    return WorkSummaryOverflow(
        agent_id="agent-eng-a",
        model="gpt-6.1-sol",
        summary_tokens=summary_tokens,
        limit_tokens=limit_tokens,
        budget_tokens=budget_tokens,
        created_at=datetime(2026, 11, 7, 4, 28, 51, tzinfo=timezone.utc),
    )


def _limits(budget: int = _LARGE_WORK, trim_percent: int = 60, summary_percent: int = 20) -> SummaryLimits:
    """The limits ``read_summary_limits`` would return for these settings."""
    return SummaryLimits(
        budget=budget,
        target=budget * trim_percent // 100,
        cap=budget * summary_percent // 100,
        trim_percent=trim_percent,
        summary_percent=summary_percent,
    )


def test_the_large_case_says_to_raise_work_summary_size_to_28_percent() -> None:
    advice = advise_summary_limit(agent_name="Eng-A", overflow=_overflow(40_000), limits=_limits())

    assert advice is not None
    assert advice.setting_key == "work_snapshot_summary_percent"
    assert advice.title == "Eng-A's work summary is too long for your settings"
    assert advice.sub == (
        "Its model (gpt-6.1-sol) wrote a 40,000-token summary of its earlier steps; the limit was 29,280. "
        "Set Work summary size to 28% or more (it is 20%). Summaries can then use up to 40,992 tokens, and "
        "recent steps kept word for word go from 58,560 to 46,848 tokens. Total context stays the same."
    )


def test_no_advice_once_the_settings_already_fit_what_the_model_wrote() -> None:
    assert advise_summary_limit(agent_name="Eng-A", overflow=_overflow(40_000), limits=_limits(summary_percent=28)) is None


def test_the_percentage_comes_from_the_budget_the_summary_was_written_under() -> None:
    # 20,000 at Medium's 69,600 needs ceil(28.74) = 29%; at today's Large 146,400 that is 42,456.
    overflow = _overflow(20_000, limit_tokens=13_920, budget_tokens=69_600)

    advice = advise_summary_limit(agent_name="Scooter-Atoms", overflow=overflow, limits=_limits())

    assert advice is not None and advice.setting_key == "work_snapshot_summary_percent"
    assert "Set Work summary size to 29% or more (it is 20%)." in advice.sub
    assert "Summaries can then use up to 42,456 tokens" in advice.sub


def test_when_no_single_setting_fixes_it_the_advice_points_at_work_kept_after_summarizing() -> None:
    # 100,000 at 146,400 needs ceil(68.31) = 69%, not below the 60% trim share.
    advice = advise_summary_limit(agent_name="Eng-A", overflow=_overflow(100_000), limits=_limits())

    assert advice is not None
    assert advice.setting_key == "work_snapshot_trim_percent"
    assert advice.title == "Eng-A's work summary is too long for your settings"
    assert advice.sub == (
        "Its model (gpt-6.1-sol) wrote a 100,000-token summary of its earlier steps; the limit was 29,280. "
        "That needs Work summary size of 69%, which has to stay below Work kept after summarizing (60%). "
        "Raise Work kept after summarizing above 69% first, or give Eng-A a model that writes shorter summaries."
    )


def test_the_advice_speaks_only_in_tokens() -> None:
    advice = advise_summary_limit(agent_name="Eng-A", overflow=_overflow(40_000), limits=_limits())

    assert advice is not None
    assert "character" not in advice.sub
