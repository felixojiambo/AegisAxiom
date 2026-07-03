"""Tests for the deterministic scenario-timing resolver.

The resolver turns three inputs (a phrase, an explicit YYYY-MM, and a
cash_receipt_timing choice) into a single ScenarioTimingResult. Nova's
add_manual_assumption tool calls it before constructing the assumption
so the LLM cannot silently pick when cash hits the bank.

All expected values are derived from synthetic fixtures (date offsets
from today's UTC date) so the tests survive any future date shift.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")

import pytest

from services.nova_engine.scenario_timing import (
    ScenarioTimingResult,
    _next_month,
    resolve_scenario_timing,
)


# ---------------------------------------------------------------------------
# Helpers — derive expected months from synthetic fixtures, never hardcoded.
# ---------------------------------------------------------------------------


def _today_anchor() -> date:
    """A date deliberately offset from "now" so the test can compute
    expected months via _next_month and never paste a year/month
    literal."""
    return date.today() - timedelta(days=120)


def _month_str(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


# ---------------------------------------------------------------------------
# Phrase resolution
# ---------------------------------------------------------------------------


def test_starting_next_month_resolves_from_current_date():
    anchor = _today_anchor()
    expected_recognition = _next_month(_month_str(anchor))

    result = resolve_scenario_timing(
        phrase="Add MRR starting next month",
        current_date=anchor,
        cash_receipt_timing="same_month",
    )

    assert result.recognition_start_month == expected_recognition
    assert result.cash_receipt_timing == "same_month"
    assert result.cash_receipt_start_month == expected_recognition
    assert result.requires_confirmation is False


def test_starting_next_month_resolves_from_projection_start_month():
    """projection_start_month wins over current_date as the anchor."""
    anchor = _today_anchor()
    projection_anchor = _next_month(_next_month(_month_str(anchor)))
    expected_recognition = _next_month(projection_anchor)

    result = resolve_scenario_timing(
        phrase="starting next month",
        current_date=anchor,
        projection_start_month=projection_anchor,
        cash_receipt_timing="same_month",
    )

    assert result.recognition_start_month == expected_recognition


def test_starting_this_month_resolves_to_anchor_month():
    anchor = _today_anchor()
    expected = _month_str(anchor)

    result = resolve_scenario_timing(
        phrase="starting this month",
        current_date=anchor,
        cash_receipt_timing="same_month",
    )

    assert result.recognition_start_month == expected


def test_month_after_next_resolves_two_months_forward():
    anchor = _today_anchor()
    expected = _next_month(_next_month(_month_str(anchor)))

    result = resolve_scenario_timing(
        phrase="starting the month after next",
        current_date=anchor,
        cash_receipt_timing="same_month",
    )

    assert result.recognition_start_month == expected


def test_phrase_and_no_anchor_requires_confirmation():
    result = resolve_scenario_timing(
        phrase="starting next month",
        cash_receipt_timing="same_month",
    )

    assert result.requires_confirmation is True
    assert result.recognition_start_month is None
    assert result.confirmation_question
    assert "month" in result.confirmation_question.lower()


def test_unmatched_phrase_falls_back_to_anchor_with_warning():
    anchor = _today_anchor()
    expected = _month_str(anchor)

    result = resolve_scenario_timing(
        phrase="kicking off whenever, your call",
        current_date=anchor,
        cash_receipt_timing="same_month",
    )

    assert result.recognition_start_month == expected
    assert any("phrase" in w.lower() or "infer" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Explicit starts_month wins over phrase
# ---------------------------------------------------------------------------


def test_explicit_starts_month_overrides_phrase():
    anchor = _today_anchor()
    explicit = _next_month(_next_month(_next_month(_month_str(anchor))))

    result = resolve_scenario_timing(
        phrase="starting next month",
        current_date=anchor,
        explicit_starts_month=explicit,
        cash_receipt_timing="same_month",
    )

    assert result.recognition_start_month == explicit
    assert result.requires_confirmation is False


def test_explicit_invalid_starts_month_raises():
    with pytest.raises(ValueError):
        resolve_scenario_timing(
            explicit_starts_month="May 2026",  # not YYYY-MM
            cash_receipt_timing="same_month",
        )


def test_invalid_projection_start_month_raises():
    with pytest.raises(ValueError):
        resolve_scenario_timing(
            projection_start_month="2026/05",
            cash_receipt_timing="same_month",
        )


# ---------------------------------------------------------------------------
# Cash receipt timing branches
# ---------------------------------------------------------------------------


def test_same_month_maps_cash_to_recognition_month():
    anchor = _today_anchor()
    starts = _next_month(_month_str(anchor))

    result = resolve_scenario_timing(
        explicit_starts_month=starts,
        cash_receipt_timing="same_month",
    )

    assert result.cash_receipt_timing == "same_month"
    assert result.cash_receipt_start_month == starts
    assert result.cash_receipt_date is None


def test_next_month_maps_cash_to_following_month():
    anchor = _today_anchor()
    starts = _next_month(_month_str(anchor))
    expected_cash = _next_month(starts)

    result = resolve_scenario_timing(
        explicit_starts_month=starts,
        cash_receipt_timing="next_month",
    )

    assert result.cash_receipt_timing == "next_month"
    assert result.cash_receipt_start_month == expected_cash
    assert starts in result.timing_summary
    assert expected_cash in result.timing_summary


def test_specific_date_requires_cash_receipt_date():
    anchor = _today_anchor()
    starts = _next_month(_month_str(anchor))

    result = resolve_scenario_timing(
        explicit_starts_month=starts,
        cash_receipt_timing="specific_date",
    )

    assert result.requires_confirmation is True
    assert result.confirmation_question
    assert "date" in result.confirmation_question.lower()


def test_specific_date_with_valid_date_resolves():
    anchor = _today_anchor()
    starts = _next_month(_month_str(anchor))
    explicit_date = f"{_next_month(starts)}-15"  # mid-month, derived from fixture

    result = resolve_scenario_timing(
        explicit_starts_month=starts,
        cash_receipt_timing="specific_date",
        cash_receipt_date=explicit_date,
    )

    assert result.requires_confirmation is False
    assert result.cash_receipt_date == explicit_date
    assert result.cash_receipt_start_month == explicit_date[:7]


def test_specific_date_invalid_format_raises():
    with pytest.raises(ValueError):
        resolve_scenario_timing(
            explicit_starts_month="2099-01",
            cash_receipt_timing="specific_date",
            cash_receipt_date="2099/01/15",  # wrong format
        )


def test_missing_cash_receipt_timing_requires_confirmation():
    """The audited bug: an MRR add without explicit cash timing must
    not be silently materialized — the helper asks the user."""
    anchor = _today_anchor()
    starts = _next_month(_month_str(anchor))

    result = resolve_scenario_timing(
        explicit_starts_month=starts,
        cash_receipt_timing=None,
    )

    assert result.requires_confirmation is True
    assert result.recognition_start_month == starts  # known
    assert result.cash_receipt_timing is None
    assert result.cash_receipt_start_month is None
    assert "same_month" in result.confirmation_question
    assert "next_month" in result.confirmation_question


def test_invalid_cash_receipt_timing_raises():
    with pytest.raises(ValueError):
        resolve_scenario_timing(
            explicit_starts_month="2099-01",
            cash_receipt_timing="quarterly",  # not in the Literal
        )


# ---------------------------------------------------------------------------
# timing_summary content
# ---------------------------------------------------------------------------


def test_timing_summary_quotes_recognition_and_cash_months():
    anchor = _today_anchor()
    starts = _next_month(_month_str(anchor))

    result = resolve_scenario_timing(
        explicit_starts_month=starts,
        cash_receipt_timing="same_month",
    )

    assert starts in result.timing_summary
    assert "same_month" in result.timing_summary


def test_timing_summary_for_next_month_quotes_both_months():
    anchor = _today_anchor()
    starts = _next_month(_month_str(anchor))
    cash = _next_month(starts)

    result = resolve_scenario_timing(
        explicit_starts_month=starts,
        cash_receipt_timing="next_month",
    )

    assert starts in result.timing_summary
    assert cash in result.timing_summary


# ---------------------------------------------------------------------------
# Year boundary
# ---------------------------------------------------------------------------


def test_next_month_wraps_year_boundary():
    """A December recognition month must roll into January of the next
    year for cash. Pinned with a non-current-year fixture so the test
    doesn't depend on what month it runs in."""
    starts = "2099-12"
    expected_cash = "2100-01"

    result = resolve_scenario_timing(
        explicit_starts_month=starts,
        cash_receipt_timing="next_month",
    )

    assert result.recognition_start_month == starts
    assert result.cash_receipt_start_month == expected_cash


# ---------------------------------------------------------------------------
# Result is frozen / immutable
# ---------------------------------------------------------------------------


def test_result_is_frozen_dataclass():
    result = resolve_scenario_timing(
        explicit_starts_month="2099-06",
        cash_receipt_timing="same_month",
    )

    with pytest.raises(Exception):
        # frozen dataclass — assignment must fail
        result.recognition_start_month = "2099-07"  # type: ignore[misc]


def test_returns_scenario_timing_result_instance():
    result = resolve_scenario_timing(
        explicit_starts_month="2099-06",
        cash_receipt_timing="same_month",
    )

    assert isinstance(result, ScenarioTimingResult)
