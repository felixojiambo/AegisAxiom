"""Time-entry input parsing + 10-minute snap helpers (Zoho-style UX).

The May 22, 2026 review asked for Zoho-parity time entry input:

  * decimal hours (already worked: e.g., ``1.5`` -> 1.5 hours),
  * optional start_time / end_time pickers,
  * 10-minute increments (snap, not reject — see below),
  * quick-add range 1h–4h.

This module is the pure-function layer the route at
``routes/time_entries.create_time_entry`` calls before any DB work.
Isolating the logic keeps the route's policy/permission code path
unchanged and lets us pin the new behaviour with focused tests.

Snap policy (chosen carefully — see PR discussion):
  * 10-minute snap is **non-breaking by default**. The input is
    rounded to the nearest 10-minute boundary using banker's-round-
    free integer-minute math; the original input is preserved under
    ``hours_input_raw`` for audit.
  * No request is rejected purely because the input was off a
    10-minute boundary. Rejecting would break existing automation
    and legacy time-entry forms that already use 0.25h ticks.
  * When ``start_time`` and ``end_time`` are both provided, the
    helper computes a duration in minutes, then snaps that.

Source: ``services.time_entry_input``. Used by:
  - ``routes/time_entries.create_time_entry`` (Quick Add POST)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# 10-minute snap increment. Pinned here so tests can read the same
# constant the route uses. Changing this is a deliberate code review.
SNAP_INCREMENT_MINUTES = 10

# Quick-add UI chip range. The route does not enforce a cap on
# `hours` — long entries are valid for admins — but the documented
# UX cap for Quick Add is 4h. Surfaced here for the wiring test.
QUICK_ADD_MIN_HOURS = 1.0
QUICK_ADD_MAX_HOURS = 4.0


def parse_manual_duration(raw: Any) -> tuple[float, int]:
    """Parse a free-text duration string into ``(hours, minutes)``.

    May 24, 2026 (revised): the manual_duration field is **human
    time notation**, not decimal hours. The dot is a separator
    between hours and minutes, NOT a decimal point. ``2.30`` means
    2 hours 30 minutes, exactly like ``2:30``. ``2.03`` means
    2 hours 3 minutes. Users who want to enter decimal hours
    (e.g. 1.5 hours via API automation) keep using the numeric
    ``hours`` field on the payload — that path is unchanged.

    Accepted formats (case-insensitive, whitespace tolerant):

      "6h15m"               -> (6.25, 375)
      "6h 15m"              -> (6.25, 375)
      "6 hr 15 min"         -> (6.25, 375)
      "6 hours 15 minutes"  -> (6.25, 375)
      "2h 3m"               -> (2.05, 123)
      "2 hrs 3 mins"        -> (2.05, 123)
      "2:03"                -> (2.05, 123)   # H:MM
      "6:15"                -> (6.25, 375)
      "2.03"                -> (2.05, 123)   # H.MM (2 digits required)
      "2.30"                -> (2.5,  150)   # H.MM (NOT decimal hours)
      "6.15"                -> (6.25, 375)
      "375m"                -> (6.25, 375)
      "375 minutes"         -> (6.25, 375)
      "6"                   -> (6.0,  360)   # whole number = hours
      "6h"                  -> (6.0,  360)

    Rejected (raises ``TimeEntryInputError``):
      empty / whitespace / None
      zero / negative
      "abc" / "2 hours plus" — unparseable
      "2:75" / "2.75"  — minutes must be < 60
      "2.3"            — H.MM requires exactly two minute digits;
                         the user should type "2.30" if they mean
                         2 hours 30 minutes
      "1.5"            — same single-digit rejection; this avoids
                         the historical decimal-hours ambiguity that
                         caused the May 24 confusion

    Returns ``(hours_decimal, total_minutes)`` where ``total_minutes``
    is the exact integer-minute count the caller persists on
    ``raw_minutes``. ``hours_decimal`` is the derived numeric form
    (``total_minutes / 60.0``) for callers that need the legacy
    field.
    """
    if raw is None:
        raise TimeEntryInputError("Enter a valid duration.")
    s = str(raw).strip().lower()
    if not s:
        raise TimeEntryInputError("Enter a valid duration.")

    import re as _re

    # ---- H + M combo form (most explicit; matched first so an
    # accidental "2.30 hours" doesn't slip into the H.MM rule).
    m = _re.fullmatch(
        r"(\d+)\s*(?:h|hr|hrs|hour|hours)"
        r"(?:\s*(\d+)\s*(?:m|min|mins|minute|minutes))?",
        s,
    )
    if m:
        hours_part = int(m.group(1))
        minutes_part = int(m.group(2)) if m.group(2) is not None else 0
        if minutes_part >= 60:
            raise TimeEntryInputError("Minutes must be 00–59.")
        total_minutes = hours_part * 60 + minutes_part
        if total_minutes <= 0:
            raise TimeEntryInputError("Duration must be greater than 0.")
        return total_minutes / 60.0, total_minutes

    # ---- H:MM form. Minutes MUST be exactly two digits and < 60.
    m = _re.fullmatch(r"(\d{1,3}):(\d{2})", s)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        if mm >= 60:
            raise TimeEntryInputError("Minutes must be 00–59.")
        total_minutes = hh * 60 + mm
        if total_minutes <= 0:
            raise TimeEntryInputError("Duration must be greater than 0.")
        return total_minutes / 60.0, total_minutes
    # Reject H:M with single-digit minutes ("2:5") and >= 60 minutes
    # ("2:75") — both end up here because the strict regex above did
    # not match. The error message names the offending input so the
    # UX preview can echo it.
    bad_colon = _re.fullmatch(r"(\d{1,3}):(\d{1,3})", s)
    if bad_colon:
        mm_raw = bad_colon.group(2)
        if len(mm_raw) != 2:
            raise TimeEntryInputError("Enter a valid duration.")
        if int(mm_raw) >= 60:
            raise TimeEntryInputError("Minutes must be 00–59.")

    # ---- H.MM form — the May 24 (revised) rule. Treat the dot as
    # a time separator, NOT a decimal point. Minutes MUST be exactly
    # two digits and < 60. "2.30" -> 2h30m. "2.3" -> rejected.
    m = _re.fullmatch(r"(\d+)\.(\d{2})", s)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        if mm >= 60:
            raise TimeEntryInputError("Minutes must be 00–59.")
        total_minutes = hh * 60 + mm
        if total_minutes <= 0:
            raise TimeEntryInputError("Duration must be greater than 0.")
        return total_minutes / 60.0, total_minutes
    # Reject one-digit-after-dot ("2.3") and minutes >= 60 ("2.75").
    bad_dot = _re.fullmatch(r"(\d+)\.(\d+)", s)
    if bad_dot:
        mm_raw = bad_dot.group(2)
        if len(mm_raw) != 2:
            raise TimeEntryInputError("Enter a valid duration.")
        if int(mm_raw) >= 60:
            raise TimeEntryInputError("Minutes must be 00–59.")

    # ---- Minutes-only form: "375m" / "375 minutes" / "375 min".
    m = _re.fullmatch(r"(\d+)\s*(?:m|min|mins|minute|minutes)", s)
    if m:
        total_minutes = int(m.group(1))
        if total_minutes <= 0:
            raise TimeEntryInputError("Duration must be greater than 0.")
        return total_minutes / 60.0, total_minutes

    # ---- Whole-number HOURS form (no dot, no colon, no suffix):
    # "6" / "2". Must be a positive integer.
    m = _re.fullmatch(r"(\d+)", s)
    if m:
        hours_part = int(m.group(1))
        if hours_part <= 0:
            raise TimeEntryInputError("Duration must be greater than 0.")
        return float(hours_part), hours_part * 60

    raise TimeEntryInputError("Enter a valid duration.")


@dataclass
class ParsedTimeEntryInput:
    """Result of normalising a Quick Add payload.

    Attributes:
      hours:               snapped value the route persists (10-min
                           increment).
      hours_input_raw:     original numeric value before snap (None
                           when start/end were supplied and no raw
                           hours field was present).
      duration_source:     "manual_duration" if the user typed a free-
                           text duration; "hours" if the chip path;
                           "start_end" if start/end pickers were used.
      start_minutes:       minute-of-day for start_time (informational).
      end_minutes:         minute-of-day for end_time (informational).
      raw_minutes:         exact total minutes from the user input
                           when source is "manual_duration" (e.g. 123
                           for 2h3m). None for the other source paths.
                           Downstream callers that need precise minute
                           accuracy (AP/AR reporting, audit trails)
                           read this — billing-snapshot tests continue
                           to read ``hours``.
      manual_duration_text: verbatim user input when source is
                           "manual_duration"; persisted on the entry
                           for audit so a future bug report ("the form
                           said 6h15m but the entry has 6.17h") can
                           be reconciled without re-asking the user.
    """
    hours: float
    hours_input_raw: float | None
    duration_source: str
    start_minutes: int | None = None
    end_minutes: int | None = None
    raw_minutes: int | None = None
    manual_duration_text: str | None = None


class TimeEntryInputError(ValueError):
    """Raised when the payload's hours / start_time / end_time fail
    the basic sanity checks (negative duration, end <= start,
    inverted range, malformed time strings). The route layer maps
    this to an HTTP 400."""


def _parse_hhmm(value: Any, *, field: str) -> int:
    """Parse a ``HH:MM`` (or ``HH:MM:SS``) string to minute-of-day.

    Returns an int in ``[0, 24*60]``. The 24:00 sentinel is accepted
    because Zoho allows end_time=24:00 to mean "end of day"; everything
    else outside ``[0, 24*60]`` raises.
    """
    if value is None:
        raise TimeEntryInputError(f"{field} is required.")
    if isinstance(value, (int, float)):
        # Caller passed minutes-of-day directly (test convenience).
        m = int(value)
    else:
        s = str(value).strip()
        if not s:
            raise TimeEntryInputError(f"{field} must be HH:MM, not blank.")
        parts = s.split(":")
        if len(parts) not in (2, 3):
            raise TimeEntryInputError(
                f"{field}={s!r} must be HH:MM (or HH:MM:SS); got {len(parts)} parts."
            )
        try:
            hh = int(parts[0])
            mm = int(parts[1])
        except ValueError as e:
            raise TimeEntryInputError(
                f"{field}={s!r} could not be parsed: {e}"
            ) from e
        if not (0 <= hh <= 24) or not (0 <= mm <= 59):
            raise TimeEntryInputError(
                f"{field}={s!r} is outside the valid HH:MM range."
            )
        m = hh * 60 + mm
    if m < 0 or m > 24 * 60:
        raise TimeEntryInputError(
            f"{field} resolved to {m} minutes-of-day, outside [0, 1440]."
        )
    return m


def _coerce_positive_float(value: Any) -> float | None:
    """Return value as a non-negative float, or None when missing /
    invalid. Treats blank strings as missing."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f


def snap_hours_to_increment(hours: float, *, increment_minutes: int = SNAP_INCREMENT_MINUTES) -> float:
    """Snap ``hours`` to the nearest ``increment_minutes`` boundary.

    Math runs in integer minutes so floating-point rounding never
    sneaks the result onto the wrong side of the snap boundary.
    Negative durations are NOT snapped here — the caller raises a
    ``TimeEntryInputError`` upstream.

    Examples (with increment_minutes=10):
      1.0    -> 1.0           (60 min, on boundary)
      1.5    -> 1.5           (90 min, on boundary)
      1.4    -> 1.4           (84 min -> 80 min snap -> 1.333... but
                                rendered as 1.33 -> exposed below)
      1.17   -> 1.17          (70.2 min -> 70 min snap -> 1.1666...)
    """
    if hours <= 0:
        return 0.0
    total_minutes = round(hours * 60)
    snapped_minutes = round(total_minutes / increment_minutes) * increment_minutes
    return snapped_minutes / 60.0


def parse_time_entry_payload(data: dict[str, Any]) -> ParsedTimeEntryInput:
    """Normalise the Quick Add payload to a snapped ``hours`` value.

    Resolution order:
      1. If ``manual_duration`` text is provided, parse it through
         ``parse_manual_duration`` (exact minutes preserved on the
         result's ``raw_minutes`` field). This is the
         "real durations like 2h3m" path added 2026-05-24.
      2. Else if ``hours`` is provided and > 0, use it.
      3. Else if both ``start_time`` and ``end_time`` are provided,
         compute ``end - start`` in minutes.
      4. Otherwise raise — the route returns 400.

    The result's ``hours`` is always snapped to the 10-minute
    increment for billing-consistency with the existing snap path;
    ``hours_input_raw`` carries the pre-snap value and
    ``raw_minutes`` carries the exact minute count from the user
    input. Downstream code that needs the precise duration reads
    ``raw_minutes``; existing billing-snapshot tests continue to
    read ``hours``.
    """
    raw_manual = data.get("manual_duration")
    raw_hours = _coerce_positive_float(data.get("hours"))
    has_start = data.get("start_time") not in (None, "")
    has_end = data.get("end_time") not in (None, "")

    if has_start ^ has_end:
        # Exactly one of start/end provided — ambiguous; reject so the
        # user gets a clear UX prompt rather than a silent zero.
        raise TimeEntryInputError(
            "Provide both start_time and end_time, or neither — "
            "supplying just one is ambiguous."
        )

    raw_minutes: int | None = None
    manual_text: str | None = None
    if raw_manual is not None and str(raw_manual).strip():
        manual_text = str(raw_manual).strip()
        parsed_hours, parsed_minutes = parse_manual_duration(manual_text)
        source = "manual_duration"
        unsnapped = parsed_hours
        raw_minutes = parsed_minutes
        start_min: int | None = None
        end_min: int | None = None
    elif raw_hours is not None and raw_hours > 0:
        source = "hours"
        unsnapped = raw_hours
        start_min = None
        end_min = None
    elif has_start and has_end:
        start_min = _parse_hhmm(data.get("start_time"), field="start_time")
        end_min = _parse_hhmm(data.get("end_time"), field="end_time")
        if end_min <= start_min:
            raise TimeEntryInputError(
                f"end_time ({end_min} min) must be after start_time ({start_min} min)."
            )
        unsnapped = (end_min - start_min) / 60.0
        source = "start_end"
    else:
        raise TimeEntryInputError(
            "Time entry requires either a positive `hours` value or both "
            "start_time and end_time."
        )

    # Snap policy:
    #   * manual_duration → NEVER snap. The user typed an exact
    #     value (6h15m, 2:03, 2.30, 375m); persisting a snapped
    #     value would silently change the entry the user submitted.
    #   * start_end       → NEVER snap. Real-world workdays end at
    #     2h03m, not 2h or 2h10m. The picker steps at minute
    #     precision (step=60) so the input is intentional.
    #   * hours (numeric) → keep the 10-min snap for backward
    #     compatibility with the chip path; chip values are already
    #     on the boundary so no real entry is changed.
    if source in ("manual_duration", "start_end"):
        final_hours = unsnapped
        hours_input_raw_field = None
    else:
        snapped = snap_hours_to_increment(unsnapped)
        if snapped <= 0:
            raise TimeEntryInputError(
                f"Time entry duration {unsnapped:.4f}h snaps to zero at the "
                f"{SNAP_INCREMENT_MINUTES}-minute increment. "
                "Increase the duration."
            )
        final_hours = snapped
        hours_input_raw_field = unsnapped if abs(unsnapped - snapped) > 1e-9 else None

    # For manual_duration / start_end the helper above already gave
    # us an exact minute count; for the numeric hours path we derive
    # raw_minutes from the snapped value so every entry has the
    # field populated.
    if raw_minutes is None:
        if source == "start_end" and start_min is not None and end_min is not None:
            raw_minutes = end_min - start_min
        else:
            raw_minutes = int(round(final_hours * 60))

    if final_hours <= 0:
        raise TimeEntryInputError("Duration must be greater than 0.")

    return ParsedTimeEntryInput(
        hours=final_hours,
        hours_input_raw=hours_input_raw_field,
        duration_source=source,
        start_minutes=start_min,
        end_minutes=end_min,
        raw_minutes=raw_minutes,
        manual_duration_text=manual_text,
    )


def format_duration_human(hours: float) -> str:
    """Return a Zoho-style ``Xh Ym`` display string.

    Examples:
      1.0  -> "1h"
      1.5  -> "1h 30m"
      0.5  -> "30m"
      2.0  -> "2h"
    """
    total_minutes = round(hours * 60)
    h, m = divmod(total_minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"
