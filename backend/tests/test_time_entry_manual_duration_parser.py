"""Tests for the manual-duration free-text parser (May 24 revised).

The May 24 review reversed the prior decimal-hours interpretation:
``2.30`` in the manual_duration field now means **2 hours 30
minutes**, not 2.3 decimal hours. The dot is a time separator, same
as the colon in 2:30. This file pins every documented format from
the spec.

The numeric ``hours`` field on the API payload is unchanged — that
path still treats 1.5 as 1.5 decimal hours for backward compat with
existing automation. Only the free-text manual_duration field uses
the new H.MM rule.

Synthetic fixtures only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
from services.time_entry_input import (  # noqa: E402
    TimeEntryInputError,
    parse_manual_duration,
    parse_time_entry_payload,
)


# ---------------------------------------------------------------------------
# H + M combo form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,minutes", [
    ("6h15m", 375),
    ("6h 15m", 375),
    ("6 hr 15 min", 375),
    ("6 hours 15 minutes", 375),
    ("2h 3m", 123),
    ("2h3m", 123),
    ("2 hrs 3 mins", 123),
    ("0h 45m", 45),
    ("4h", 240),
    ("4 hours", 240),
    ("1h", 60),
])
def test_hours_minutes_combo_examples(raw, minutes):
    hours, mins = parse_manual_duration(raw)
    assert mins == minutes
    assert abs(hours - minutes / 60.0) < 1e-9


def test_minute_part_60_or_more_rejected_in_hm_form():
    with pytest.raises(TimeEntryInputError, match="Minutes must be 00"):
        parse_manual_duration("2h 75m")


# ---------------------------------------------------------------------------
# H:MM form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,minutes", [
    ("2:03", 123),
    ("6:15", 375),
    ("0:45", 45),
    ("12:30", 750),
    ("1:00", 60),
])
def test_hh_mm_examples(raw, minutes):
    hours, mins = parse_manual_duration(raw)
    assert mins == minutes


def test_hh_mm_minutes_75_is_rejected_not_normalised():
    """2:75 must NOT silently become 3:15. Hard reject."""
    with pytest.raises(TimeEntryInputError, match="Minutes must be 00"):
        parse_manual_duration("2:75")


def test_hh_mm_minutes_60_is_rejected():
    with pytest.raises(TimeEntryInputError, match="Minutes must be 00"):
        parse_manual_duration("2:60")


def test_hh_mm_single_digit_minutes_rejected():
    """H:MM requires two-digit minutes — "2:5" is ambiguous."""
    with pytest.raises(TimeEntryInputError, match="valid duration"):
        parse_manual_duration("2:5")


# ---------------------------------------------------------------------------
# H.MM form — THE rule that changed on 2026-05-24
# ---------------------------------------------------------------------------


def test_h_dot_mm_2_30_is_150_minutes_not_138():
    """The single most important parser rule: 2.30 in the manual
    duration field means 2 hours 30 minutes (150 minutes), NOT
    2.3 decimal hours (138 minutes). The May 24 review explicitly
    reversed the prior interpretation."""
    hours, mins = parse_manual_duration("2.30")
    assert mins == 150
    assert hours == 2.5
    # Pin the wrong answer explicitly so a future regression is
    # caught at the assertion site.
    assert mins != 138


def test_h_dot_mm_2_03_is_123_minutes():
    """2.03 = 2 hours 3 minutes."""
    hours, mins = parse_manual_duration("2.03")
    assert mins == 123
    assert abs(hours - 2.05) < 1e-9


def test_h_dot_mm_6_15_is_375_minutes():
    hours, mins = parse_manual_duration("6.15")
    assert mins == 375
    assert hours == 6.25


def test_h_dot_mm_requires_two_digit_minutes():
    """2.3 must be rejected. The user must type 2.30 if they mean
    2h30m. This avoids the historical decimal-hours ambiguity."""
    with pytest.raises(TimeEntryInputError, match="valid duration"):
        parse_manual_duration("2.3")


def test_h_dot_mm_minutes_75_is_rejected():
    """2.75 must NOT silently become 3:15."""
    with pytest.raises(TimeEntryInputError, match="Minutes must be 00"):
        parse_manual_duration("2.75")


def test_h_dot_mm_1_5_rejected_in_manual_duration():
    """1.5 typed into the manual_duration field is rejected — the
    free-text parser does NOT do decimal hours. Automation that
    needs 1.5 hours keeps using the numeric `hours` field on the
    payload (path unchanged)."""
    with pytest.raises(TimeEntryInputError, match="valid duration"):
        parse_manual_duration("1.5")


def test_h_dot_mm_minutes_part_60_is_rejected():
    with pytest.raises(TimeEntryInputError, match="Minutes must be 00"):
        parse_manual_duration("2.60")


# ---------------------------------------------------------------------------
# Minutes-only form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,minutes", [
    ("375m", 375),
    ("375 minutes", 375),
    ("375 min", 375),
    ("375 mins", 375),
    ("123m", 123),
    ("45 minutes", 45),
    ("1 minute", 1),
])
def test_minutes_only_examples(raw, minutes):
    hours, mins = parse_manual_duration(raw)
    assert mins == minutes


# ---------------------------------------------------------------------------
# Whole-number HOURS form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,minutes", [
    ("6", 360),
    ("2", 120),
    ("8", 480),
    ("1", 60),
])
def test_whole_number_means_hours(raw, minutes):
    """Bare integer (no dot, no colon, no suffix) means hours."""
    hours, mins = parse_manual_duration(raw)
    assert mins == minutes
    assert hours == minutes / 60.0


# ---------------------------------------------------------------------------
# Rejection cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    None,
    "",
    "   ",
])
def test_empty_or_whitespace_rejected(raw):
    with pytest.raises(TimeEntryInputError, match="valid duration"):
        parse_manual_duration(raw)


@pytest.mark.parametrize("raw", [
    "0",
    "0:00",
    "0.00",
    "0m",
    "0 hours",
    "0h 0m",
])
def test_zero_rejected(raw):
    with pytest.raises(TimeEntryInputError, match="greater than 0"):
        parse_manual_duration(raw)


@pytest.mark.parametrize("raw", [
    "abc",
    "five hours",
    "6h15m 30s",     # seconds not supported
    "1d",            # days not supported
    ":30",           # bare :MM without H
    "1::30",         # double colon
    "1.5.0",         # not a valid form
    "-2",            # negative
    "2 hours plus",  # trailing word
])
def test_malformed_rejected(raw):
    with pytest.raises(TimeEntryInputError):
        parse_manual_duration(raw)


# ---------------------------------------------------------------------------
# Integration with parse_time_entry_payload
# ---------------------------------------------------------------------------


def test_manual_duration_takes_precedence_over_numeric_hours():
    """When both manual_duration and numeric hours are sent, the
    text input wins — it's the more deliberate user action."""
    parsed = parse_time_entry_payload({
        "manual_duration": "2h 3m",
        "hours": 1,
    })
    assert parsed.duration_source == "manual_duration"
    assert parsed.raw_minutes == 123
    assert parsed.manual_duration_text == "2h 3m"


def test_manual_duration_2h3m_stores_exact_minutes_not_snapped():
    """Critical post-2026-05-24 behavior: manual durations are NEVER
    snapped. The user's exact 123 minutes is what gets persisted on
    both raw_minutes AND hours."""
    parsed = parse_time_entry_payload({"manual_duration": "2h 3m"})
    assert parsed.raw_minutes == 123
    assert abs(parsed.hours - 2.05) < 1e-9
    # No snap happened, so hours_input_raw is not populated.
    assert parsed.hours_input_raw is None


def test_manual_duration_6h15m_stores_exact_minutes():
    """6h15m = 375 minutes / 6.25 hours, both stored exactly."""
    parsed = parse_time_entry_payload({"manual_duration": "6h 15m"})
    assert parsed.raw_minutes == 375
    assert parsed.hours == 6.25


def test_manual_duration_2_30_h_dot_mm_stores_150_minutes():
    """2.30 = 2h30m = 150 minutes / 2.5 hours."""
    parsed = parse_time_entry_payload({"manual_duration": "2.30"})
    assert parsed.raw_minutes == 150
    assert parsed.hours == 2.5
    # Make absolutely sure the old (decimal-hours) answer isn't
    # produced anywhere — 2.3 decimal hours would have been 138min.
    assert parsed.raw_minutes != 138


def test_manual_duration_2_03_h_dot_mm_stores_123_minutes():
    parsed = parse_time_entry_payload({"manual_duration": "2.03"})
    assert parsed.raw_minutes == 123
    assert abs(parsed.hours - 2.05) < 1e-9


def test_manual_duration_375m_stores_exact_minutes():
    parsed = parse_time_entry_payload({"manual_duration": "375m"})
    assert parsed.raw_minutes == 375
    assert parsed.hours == 6.25


def test_manual_duration_blank_falls_through_to_numeric_hours():
    """Blank manual_duration must not short-circuit; chip-driven
    submissions with numeric hours still work."""
    parsed = parse_time_entry_payload({"manual_duration": "", "hours": 1.5})
    assert parsed.duration_source == "hours"
    assert parsed.raw_minutes == 90
    assert parsed.manual_duration_text is None


def test_manual_duration_rejected_value_bubbles_up_as_400():
    """A bad manual_duration must raise TimeEntryInputError so the
    route returns HTTP 400 with the parser's message — NOT silently
    fall back to chip values."""
    with pytest.raises(TimeEntryInputError):
        parse_time_entry_payload({"manual_duration": "2:75"})


def test_numeric_hours_path_still_decimal_for_backward_compat():
    """API-side callers that POST hours=1.5 (numeric) still get
    1.5 decimal hours. Only the manual_duration STRING field uses
    the new H.MM rule."""
    parsed = parse_time_entry_payload({"hours": 1.5})
    assert parsed.duration_source == "hours"
    # Snapped to 10-min for the chip path (1.5h = 90 min already on
    # a 10-min boundary; no change).
    assert parsed.hours == 1.5
    assert parsed.raw_minutes == 90


# ---------------------------------------------------------------------------
# start/end pickers must also preserve exact minutes (no snap)
# ---------------------------------------------------------------------------


def test_start_end_2h_03m_preserved_exactly():
    """09:00 -> 11:03 = 2h3m = 123 minutes. The May 24 (revised)
    rules require exact preservation — picker steps at 1 minute
    (step=60 in the UI) and the server must not silently round."""
    parsed = parse_time_entry_payload({
        "start_time": "09:00",
        "end_time": "11:03",
    })
    assert parsed.duration_source == "start_end"
    assert parsed.raw_minutes == 123
    assert abs(parsed.hours - 2.05) < 1e-9
    assert parsed.hours_input_raw is None  # no snap delta
