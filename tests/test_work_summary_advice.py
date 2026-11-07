"""work_summary_advice: an over-limit step summary becomes a setting to raise, with numbers.

Pure: no database. The measurement is the stored ``work_summary_overflows``
row; the limits are the current work-context settings. The Eng-A case uses
the live figures from the 11-07 stall (49,587 characters against 35,000 at a
350,000 Work budget).
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.agent_loop.work_summary import SummaryLimits
from core.agent_loop.work_summary_advice import _tokens, advise_summary_limit
from core.models import WorkSummaryOverflow


def _overflow(summary_chars: int, *, limit_chars: int = 35_000, budget_chars: int = 350_000) -> WorkSummaryOverflow:
    return WorkSummaryOverflow(
        agent_id="agent-eng-a",
        model="gpt-6.1-sol",
        summary_chars=summary_chars,
        limit_chars=limit_chars,
        budget_chars=budget_chars,
        created_at=datetime(2026, 11, 7, 4, 28, 51, tzinfo=timezone.utc),
    )


def _limits(budget: int = 350_000, trim_percent: int = 50, summary_percent: int = 10) -> SummaryLimits:
    """The limits ``read_summary_limits`` would return for these settings."""
    return SummaryLimits(
        budget=budget,
        target=budget * trim_percent // 100,
        cap=budget * summary_percent // 100,
        trim_percent=trim_percent,
        summary_percent=summary_percent,
    )


def test_the_eng_a_case_says_to_raise_work_summary_size_to_15_percent() -> None:
    advice = advise_summary_limit(agent_name="Eng-A", overflow=_overflow(49_587), limits=_limits())

    assert advice is not None
    assert advice.setting_key == "work_snapshot_summary_percent"
    assert advice.title == "Eng-A's work summary is too long for your settings"
    assert advice.sub == (
        "Its model (gpt-6.1-sol) wrote a 49,587-character summary of its earlier steps; the limit was 35,000. "
        "Set Work summary size to 15% or more (it is 10%). Summaries can then use up to 52,500 characters "
        "(about 13K tokens), and recent steps kept word for word go from 140,000 to 122,500 characters "
        "(about 35K → 31K tokens). Total context stays the same."
    )


def test_no_advice_once_the_settings_already_fit_what_the_model_wrote() -> None:
    assert advise_summary_limit(agent_name="Eng-A", overflow=_overflow(49_587), limits=_limits(summary_percent=15)) is None


def test_the_percentage_comes_from_the_budget_the_summary_was_written_under() -> None:
    # 40,390 at a 250,000 budget needs ceil(16.156) = 17%; at today's 350,000 that is 59,500.
    overflow = _overflow(40_390, limit_chars=25_000, budget_chars=250_000)

    advice = advise_summary_limit(agent_name="Scooter-Atoms", overflow=overflow, limits=_limits())

    assert advice is not None and advice.setting_key == "work_snapshot_summary_percent"
    assert "Set Work summary size to 17% or more (it is 10%)." in advice.sub
    assert "Summaries can then use up to 59,500 characters" in advice.sub


def test_when_no_single_setting_fixes_it_the_advice_points_at_work_kept_after_summarizing() -> None:
    # 180,000 at 350,000 needs ceil(51.43) = 52%, not below the 50% trim share.
    advice = advise_summary_limit(agent_name="Eng-A", overflow=_overflow(180_000), limits=_limits())

    assert advice is not None
    assert advice.setting_key == "work_snapshot_trim_percent"
    assert advice.title == "Eng-A's work summary is too long for your settings"
    assert advice.sub == (
        "Its model (gpt-6.1-sol) wrote a 180,000-character summary of its earlier steps; the limit was 35,000. "
        "That needs Work summary size of 52%, which has to stay below Work kept after summarizing (50%). "
        "Raise Work kept after summarizing above 52% first, or give Eng-A a model that writes shorter summaries."
    )


def test_token_figures_use_four_characters_a_token() -> None:
    assert _tokens(2000) == "500"
    assert _tokens(52_500) == "13K"
