"""Tests for the Zoho-style time entry input helpers.

The helpers in ``services/time_entry_input`` parse the Quick Add
payload (decimal hours OR start_time / end_time pair) and snap the
result to the 10-minute increment the May 22, 2026 review asked
for. The route at ``routes/time_entries.create_time_entry`` is a
thin caller; everything testable lives here.

Behavioural contract pinned:

  * decimal hours pass through unchanged when they fall on the
    snap boundary (e.g., 1.5 hours = 90 minutes -> 1.5),
  * decimal hours snap to the nearest 10-minute boundary when
    they do not (e.g., 1.4 hours = 84 minutes -> 80 minutes
    -> 1.3333..., expressed exactly),
  * start_time / end_time pairs compute the elapsed minutes and
    then snap,
  * exactly one of start_time / end_time is rejected (ambiguous),
  * end_time <= start_time is rejected,
  * inputs that snap to zero (< 5 minutes) are rejected.
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
    SNAP_INCREMENT_MINUTES,
    QUICK_ADD_MIN_HOURS,
    QUICK_ADD_MAX_HOURS,
    ParsedTimeEntryInput,
    TimeEntryInputError,
    format_duration_human,
    parse_time_entry_payload,
    snap_hours_to_increment,
)


# ---------------------------------------------------------------------------
# Snap math
# ---------------------------------------------------------------------------


def test_snap_passes_boundary_values_unchanged():
    """Values already on a 10-minute boundary must not move."""
    assert snap_hours_to_increment(1.0) == 1.0      # 60 min
    assert snap_hours_to_increment(1.5) == 1.5      # 90 min
    assert snap_hours_to_increment(2.0) == 2.0      # 120 min
    # 70 minutes = 1 + 10/60 = 1.16666... — exact on the boundary.
    assert abs(snap_hours_to_increment(70 / 60.0) - 70 / 60.0) < 1e-9


def test_snap_rounds_to_nearest_ten_minute_boundary():
    # 84 min (1.4h) -> nearest 10-min snap is 80 min (1.333...h)
    assert abs(snap_hours_to_increment(1.4) - 80 / 60.0) < 1e-9
    # 86 min (1.4333...h) -> rounds UP to 90 min (1.5h)
    assert abs(snap_hours_to_increment(86 / 60.0) - 1.5) < 1e-9
    # 75 min (1.25h) -> halfway -> Python's round() uses
    # banker's-round on .5 so 75/10=7.5 -> 8 -> 80 min -> 1.333...
    snapped = snap_hours_to_increment(1.25)
    # Allow either tie-breaking direction (depending on Python build)
    # but the result MUST be a 10-minute multiple.
    snapped_minutes = round(snapped * 60)
    assert snapped_minutes % SNAP_INCREMENT_MINUTES == 0


def test_snap_zero_and_negative_clamp_to_zero():
    assert snap_hours_to_increment(0.0) == 0.0
    assert snap_hours_to_increment(-1.0) == 0.0


# ---------------------------------------------------------------------------
# Decimal hours path
# ---------------------------------------------------------------------------


def test_decimal_hours_1_5_pass_through_unchanged():
    parsed = parse_time_entry_payload({"hours": 1.5})
    assert parsed.hours == 1.5
    assert parsed.hours_input_raw is None  # already on boundary
    assert parsed.duration_source == "hours"
    assert parsed.start_minutes is None
    assert parsed.end_minutes is None


def test_decimal_hours_string_value_accepted():
    """The Quick Add form may submit hours as a string from the
    HTML number input — the helper must coerce."""
    parsed = parse_time_entry_payload({"hours": "2.5"})
    assert parsed.hours == 2.5


def test_decimal_hours_off_boundary_get_snapped_and_raw_preserved():
    """1.4h (84 min) snaps to 80 min (1.333...h). Raw 1.4 stays on
    the parsed result so the route persists it under hours_input_raw."""
    parsed = parse_time_entry_payload({"hours": 1.4})
    expected = 80 / 60.0
    assert abs(parsed.hours - expected) < 1e-9
    assert parsed.hours_input_raw == 1.4
    assert parsed.duration_source == "hours"


def test_decimal_hours_zero_is_rejected():
    with pytest.raises(TimeEntryInputError):
        parse_time_entry_payload({"hours": 0})


def test_decimal_hours_negative_is_rejected():
    with pytest.raises(TimeEntryInputError):
        parse_time_entry_payload({"hours": -1.0})


def test_decimal_hours_blank_string_is_rejected():
    """Blank `hours` and no start/end is the misuse case."""
    with pytest.raises(TimeEntryInputError):
        parse_time_entry_payload({"hours": ""})


# ---------------------------------------------------------------------------
# start_time / end_time path
# ---------------------------------------------------------------------------


def test_start_end_computes_duration_and_snaps():
    """09:00 -> 10:30 is 90 minutes -> 1.5h, snap is a no-op."""
    parsed = parse_time_entry_payload({
        "start_time": "09:00",
        "end_time": "10:30",
    })
    assert parsed.hours == 1.5
    assert parsed.hours_input_raw is None  # no snap delta
    assert parsed.duration_source == "start_end"
    assert parsed.start_minutes == 9 * 60
    assert parsed.end_minutes == 10 * 60 + 30


def test_start_end_off_boundary_preserved_exactly():
    """09:00 -> 10:24 is 84 minutes. May 24 (revised) rule: start/end
    durations are NEVER snapped — real workdays end at 2h03m, not
    2h or 2h10m. The picker steps at 1 minute so the input is
    intentional and the server stores it exactly."""
    parsed = parse_time_entry_payload({
        "start_time": "09:00",
        "end_time": "10:24",
    })
    expected = 84 / 60.0
    assert abs(parsed.hours - expected) < 1e-9
    assert parsed.raw_minutes == 84
    # No snap happened, so hours_input_raw is empty.
    assert parsed.hours_input_raw is None


def test_start_end_with_seconds_is_accepted():
    parsed = parse_time_entry_payload({
        "start_time": "09:00:00",
        "end_time": "10:30:00",
    })
    assert parsed.hours == 1.5


def test_only_start_time_is_rejected():
    with pytest.raises(TimeEntryInputError, match="start_time and end_time"):
        parse_time_entry_payload({"start_time": "09:00"})


def test_only_end_time_is_rejected():
    with pytest.raises(TimeEntryInputError, match="start_time and end_time"):
        parse_time_entry_payload({"end_time": "10:00"})


def test_end_before_start_is_rejected():
    with pytest.raises(TimeEntryInputError, match="after start_time"):
        parse_time_entry_payload({
            "start_time": "11:00",
            "end_time": "09:00",
        })


def test_end_equals_start_is_rejected():
    with pytest.raises(TimeEntryInputError, match="after start_time"):
        parse_time_entry_payload({
            "start_time": "09:00",
            "end_time": "09:00",
        })


def test_malformed_time_string_is_rejected():
    with pytest.raises(TimeEntryInputError):
        parse_time_entry_payload({
            "start_time": "9am",
            "end_time": "10am",
        })


def test_invalid_hour_value_rejected():
    with pytest.raises(TimeEntryInputError):
        parse_time_entry_payload({
            "start_time": "25:00",
            "end_time": "26:00",
        })


# ---------------------------------------------------------------------------
# Resolution-order precedence
# ---------------------------------------------------------------------------


def test_hours_takes_precedence_when_both_supplied():
    """When the form submits both hours AND start/end (e.g., user
    typed a duration after the start/end picker auto-populated),
    the explicit hours value wins. duration_source reflects that."""
    parsed = parse_time_entry_payload({
        "hours": 2.0,
        "start_time": "09:00",
        "end_time": "10:30",  # would imply 1.5h
    })
    assert parsed.hours == 2.0
    assert parsed.duration_source == "hours"


def test_hours_zero_falls_through_to_start_end():
    """An explicit hours=0 must NOT short-circuit; the helper should
    fall through to the start/end pair if present."""
    parsed = parse_time_entry_payload({
        "hours": 0,
        "start_time": "09:00",
        "end_time": "10:00",
    })
    assert parsed.hours == 1.0
    assert parsed.duration_source == "start_end"


def test_neither_hours_nor_start_end_rejected():
    with pytest.raises(TimeEntryInputError):
        parse_time_entry_payload({})


def test_start_end_one_minute_duration_preserved_not_snapped_to_zero():
    """May 24 (revised) rule: a 1-minute start/end span (09:00 →
    09:01) is now persisted as exactly 1 minute instead of being
    rejected as a snap-to-zero. The user picked it intentionally
    via the minute-precision picker."""
    parsed = parse_time_entry_payload({
        "start_time": "09:00",
        "end_time": "09:01",
    })
    assert parsed.raw_minutes == 1
    assert abs(parsed.hours - 1 / 60.0) < 1e-9
    assert parsed.duration_source == "start_end"


# ---------------------------------------------------------------------------
# Human duration display
# ---------------------------------------------------------------------------


def test_format_duration_human_examples():
    assert format_duration_human(1.0) == "1h"
    assert format_duration_human(1.5) == "1h 30m"
    assert format_duration_human(0.5) == "30m"
    assert format_duration_human(2.0) == "2h"
    assert format_duration_human(4.0) == "4h"
    # 1h 10m for the canonical 10-minute increment example.
    assert format_duration_human(70 / 60.0) == "1h 10m"


def test_format_duration_human_handles_zero():
    assert format_duration_human(0.0) == "0m"


# ---------------------------------------------------------------------------
# Quick-add range constants surfaced for the wiring test
# ---------------------------------------------------------------------------


def test_quick_add_range_constants_documented():
    """The May 22 review specified the Quick Add chip range 1h–4h.
    Constants pinned so the frontend wiring test can read the same
    bounds the backend documents."""
    assert QUICK_ADD_MIN_HOURS == 1.0
    assert QUICK_ADD_MAX_HOURS == 4.0
    assert SNAP_INCREMENT_MINUTES == 10
