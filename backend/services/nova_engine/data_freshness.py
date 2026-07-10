"""Detects whether GL data is fresh enough to project from.

The engine projects from the last 3 complete months. If the most recent
month is incomplete (e.g., data through April 14 but not April 30),
that month is excluded from the rolling average and a FreshnessWarning
is surfaced in the output.
"""
from __future__ import annotations
from datetime import date
from calendar import monthrange


def last_day_of_month(year: int, month: int) -> date:
    """Last calendar day of (year, month)."""
    return date(year, month, monthrange(year, month)[1])


def is_month_complete(
    month: str,
    latest_tx_date: date,
    *,
    current_date: date | None = None,
) -> bool:
    """True iff ``month`` is fully in the past relative to BOTH the
    source data freshness AND the calendar.

    Two conditions must hold for the helper to return True:

      1. ``latest_tx_date >= last_day_of_month(month)`` — the source
         carries data through the month's last day.
      2. ``last_day_of_month(month) < current_date`` — the calendar
         has actually moved past the month. This is the guard against
         the live bug where a tenant's GL carried tx_date rows on
         the LAST day of the in-progress month (May 31 with a
         current_date of May 12) and the helper falsely flagged
         May as complete.

    ``current_date`` is optional for backward compatibility — when
    omitted, the helper applies only the latest_tx_date check
    (legacy behaviour). New callers should pass ``current_date``
    (or ``date.today()`` at the boundary) to enable the
    calendar-end guard.
    """
    year, mo = (int(x) for x in month.split("-"))
    month_end = last_day_of_month(year, mo)
    if latest_tx_date < month_end:
        return False
    if current_date is not None and month_end >= current_date:
        return False
    return True


def previous_month(month: str) -> str:
    """YYYY-MM string for the month before `month`."""
    year, mo = (int(x) for x in month.split("-"))
    if mo == 1:
        return f"{year - 1}-12"
    return f"{year}-{mo - 1:02d}"


def last_n_complete_months(
    reference_month: str,
    latest_tx_date: date,
    n: int = 3,
    *,
    current_date: date | None = None,
) -> list[str]:
    """The n most recent complete months strictly before reference_month.

    reference_month is the first FORECAST month, so historicals must be
    earlier. Walks backward from the month before reference_month,
    skipping incomplete months. Returns oldest-first to match Asad's
    spreadsheet convention.

    ``current_date`` is forwarded to ``is_month_complete`` so the
    walker honours the calendar-end guard. Defaults to None for
    backward compatibility; the new boundary code paths (data_loader,
    nova_tools dispatcher) pass the real today value.
    """
    result: list[str] = []
    current = previous_month(reference_month)
    max_iterations = n + 36  # safety stop
    while len(result) < n and max_iterations > 0:
        if is_month_complete(current, latest_tx_date, current_date=current_date):
            result.append(current)
        current = previous_month(current)
        max_iterations -= 1
    return list(reversed(result))


def days_stale(latest_tx_date: date, today: date | None = None) -> int:
    """How many days since the last transaction in the data."""
    if today is None:
        today = date.today()
    return (today - latest_tx_date).days
