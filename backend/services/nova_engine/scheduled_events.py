"""Generic scheduled-cash-event helpers for Nova projections.

The cash-flow engine already supports dated events via the
``Override`` model (``type="new_cost"`` / ``"new_revenue"`` with a
``week_number``). This module is the bridge between user-supplied
timing rules and that primitive:

  * ``compute_payroll_schedule`` — biweekly payroll dates.
  * ``compute_ar_schedule`` — open invoices, paid ``N`` days after
    their due date.
  * ``compute_ap_schedule`` — open bills, paid on their due date.

Each helper is a pure function: it takes raw rows / timing config and
returns ``(overrides, metadata)``. The dispatcher in nova_tools
fetches the rows (via the same data paths the read tools already
expose) and passes them in. That separation lets the test surface
exercise the date arithmetic, the metadata shape, and the
double-count semantics without any Mongo dependency.

Hard rules baked in:

  * No hardcoded payroll amount, payroll start date, AR/AP timing
    rule, or account prefix. Every numeric and date parameter comes
    from the caller.
  * Events outside the projection horizon are kept out of the
    returned override list but listed in ``metadata.warnings`` so a
    reviewer can see what got skipped.
  * Rows without a usable due date contribute to ``warnings`` and
    are excluded from the override list — we never invent a date.
  * Amounts are deterministic (the row's ``open_balance`` for AR/AP,
    the caller's ``amount`` for payroll). The LLM is never asked to
    multiply or guess.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .models import Override


# Frequencies the payroll helper recognises. Biweekly is the v1
# requirement; ``weekly`` and ``monthly`` are listed so a future
# expansion is wired through the dispatcher's enum without touching
# this module's tests. The list lives here (not in nova_tools)
# because the dispatcher delegates the enum check to the helper.
_SUPPORTED_PAYROLL_FREQUENCIES: frozenset[str] = frozenset(
    {"biweekly", "weekly", "monthly"}
)

# Step (in days) for each frequency. Monthly is approximate (28 days
# rolling) — sufficient for cash-flow projection bucketing without
# pulling in dateutil. Tenants needing calendar-month payroll should
# pass explicit dates via overrides, not via the schedule helper.
_FREQUENCY_STEP_DAYS: dict[str, int] = {
    "weekly": 7,
    "biweekly": 14,
    "monthly": 28,
}


# ---------------------------------------------------------------------------
# Date / week arithmetic
# ---------------------------------------------------------------------------


def _parse_iso_date(value: Any) -> date | None:
    """Parse YYYY-MM-DD (or ISO 8601 with timezone) to a date, or
    return None if unparseable. We never raise — a malformed input
    becomes a warning surfaced by the caller, never a 500."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _classify_due_date(value: Any) -> tuple[date | None, str]:
    """Split ``_parse_iso_date``'s None bucket into two reasons:

    Returns ``(parsed_date, reason)`` where:
      * ``parsed_date`` is the date when parseable, else None.
      * ``reason`` is one of: ``"ok"``, ``"missing"`` (None / empty),
        ``"invalid"`` (non-empty string that doesn't parse, or
        unexpected type). The chat surface uses ``reason`` to keep
        ``excluded_missing_due_date_count`` and
        ``excluded_invalid_date_count`` distinct so a reviewer can
        tell a stale snapshot (rows without due dates) apart from
        a malformed-input bug.
    """
    if value is None or value == "":
        return None, "missing"
    parsed = _parse_iso_date(value)
    if parsed is None:
        return None, "invalid"
    return parsed, "ok"


def _projection_start_date_from_month(projection_start_month: str) -> date:
    """First day of ``projection_start_month`` (YYYY-MM)."""
    year_str, mo_str = projection_start_month.split("-")
    return date(int(year_str), int(mo_str), 1)


def week_number_for_date(
    event_date: date,
    projection_start_month: str,
    horizon_weeks: int,
) -> int | None:
    """Convert a calendar date into the projection's 1-based week
    index. Returns None when the date falls outside the horizon.

    The engine treats each forecast week as exactly 7 days starting
    from the first day of ``projection_start_month`` (see
    ``engine._week_starting_for``). We mirror that convention here so
    the override placement is identical to what the engine would
    compute internally.
    """
    start = _projection_start_date_from_month(projection_start_month)
    delta_days = (event_date - start).days
    if delta_days < 0:
        return None
    week_index = delta_days // 7
    if week_index >= horizon_weeks:
        return None
    return week_index + 1  # Override.week_number is 1-based


# ---------------------------------------------------------------------------
# Payroll schedule
# ---------------------------------------------------------------------------


def compute_payroll_schedule(
    *,
    amount: float,
    start_date: str,
    frequency: str,
    projection_start_month: str,
    horizon_weeks: int,
) -> tuple[list[Override], dict[str, Any]]:
    """Generate a biweekly (or weekly / monthly) payroll cadence and
    return the resulting overrides plus an audit block.

    Args:
      amount: positive cash outflow per payroll run. Comes from the
        caller; never defaulted in this module.
      start_date: YYYY-MM-DD of the first payroll within the horizon.
      frequency: one of ``"biweekly"`` / ``"weekly"`` / ``"monthly"``.
      projection_start_month: YYYY-MM (the projection's anchor).
      horizon_weeks: total projection length in 7-day weeks.

    Returns:
      ``(overrides, metadata)``. ``overrides`` is a list of
      ``Override(type="new_cost", target="PAYROLL", week_number=…,
      amount=amount, description=…)`` for every payroll date that
      falls inside the horizon. ``metadata`` carries the resolved
      dates, the method label, and warnings for inputs the helper
      had to reject.

    The helper does NOT raise. Invalid amount / start_date /
    frequency → empty overrides + a warning the chat tool surfaces
    verbatim. This matches the contract on the data-loader side
    where bad inputs produce a structured response, not a 500.
    """
    metadata: dict[str, Any] = {
        "amount": amount,
        "start_date": start_date,
        "frequency": frequency,
        "payment_dates_used": [],
        "method": "scheduled_cash_event",
        # Idempotency audit: source marker every override carries
        # + counts the dispatcher (and Nova) can quote when re-runs
        # of the same projection collapse duplicates.
        "generated_source": "dynamic_payroll_schedule",
        "generated_event_count": 0,
        "deduped_duplicate_count": 0,
        "warnings": [],
    }

    if not isinstance(amount, (int, float)) or amount <= 0:
        metadata["warnings"].append(
            "payroll_schedule.amount must be a positive number; "
            "scheduling skipped."
        )
        return [], metadata
    if frequency not in _SUPPORTED_PAYROLL_FREQUENCIES:
        metadata["warnings"].append(
            f"payroll_schedule.frequency {frequency!r} is not "
            f"supported. Supported: "
            f"{sorted(_SUPPORTED_PAYROLL_FREQUENCIES)}."
        )
        return [], metadata
    start = _parse_iso_date(start_date)
    if start is None:
        metadata["warnings"].append(
            "payroll_schedule.start_date must be YYYY-MM-DD; "
            "scheduling skipped."
        )
        return [], metadata

    step_days = _FREQUENCY_STEP_DAYS[frequency]
    horizon_end = (
        _projection_start_date_from_month(projection_start_month)
        + timedelta(weeks=horizon_weeks)
    )

    overrides: list[Override] = []
    payment_dates: list[str] = []
    seen_dedupe_keys: set[str] = set()
    deduped = 0
    current = start
    # Guard with a sane upper bound so a future "monthly + tiny step"
    # config can't loop forever.
    for _ in range(horizon_weeks * 7 + 1):
        if current >= horizon_end:
            break
        week_no = week_number_for_date(
            current, projection_start_month, horizon_weeks,
        )
        if week_no is None:
            # Payroll date before the horizon (start_date pre-dates
            # projection_start_month) — log + skip rather than slide
            # into week 1, which would distort the timing.
            metadata["warnings"].append(
                f"payroll date {current.isoformat()} falls outside "
                "the projection horizon; skipped."
            )
        else:
            # Stable dedupe key — date + amount + target. Two
            # payroll entries that collide on these three are
            # genuinely the same event, so the second one is
            # collapsed silently and counted.
            dedupe_key = (
                f"dynamic_payroll_schedule:"
                f"{current.isoformat()}:{float(amount)}:PAYROLL"
            )
            if dedupe_key in seen_dedupe_keys:
                deduped += 1
            else:
                seen_dedupe_keys.add(dedupe_key)
                overrides.append(Override(
                    type="new_cost",
                    target="PAYROLL",
                    week_number=week_no,
                    amount=float(amount),
                    description=(
                        f"scheduled payroll {current.isoformat()} "
                        f"({frequency})"
                    ),
                    generated_source="dynamic_payroll_schedule",
                    dedupe_key=dedupe_key,
                ))
                payment_dates.append(current.isoformat())
        current = current + timedelta(days=step_days)

    metadata["payment_dates_used"] = payment_dates
    metadata["generated_event_count"] = len(overrides)
    metadata["deduped_duplicate_count"] = deduped
    return overrides, metadata


# ---------------------------------------------------------------------------
# AR receipts schedule
# ---------------------------------------------------------------------------


def compute_ar_schedule(
    *,
    open_invoice_rows: Iterable[dict[str, Any]],
    days_after_due_date: int,
    projection_start_month: str,
    horizon_weeks: int,
    fetch_meta: dict[str, Any] | None = None,
) -> tuple[list[Override], dict[str, Any]]:
    """Schedule AR receipts on ``due_date + days_after_due_date``.

    Args:
      open_invoice_rows: iterable of row dicts. Each row must carry
        ``due_date`` (YYYY-MM-DD string) and ``open_balance`` (float).
        Rows without a usable due_date contribute to ``warnings``
        and are excluded from the override list.
      days_after_due_date: caller-supplied integer offset. Must be
        non-negative; 0 means "pay on due date".
      fetch_meta: optional audit block from the data-access layer
        (``source``, ``fetched_row_count``, ``total_available_rows``,
        ``schedule_cap``, ``page_size``, ``truncated``). When passed,
        the helper splices it into the returned metadata so the chat
        surface can quote "fetched X of Y open invoices, capped at
        Z". When omitted (tests with synthetic rows), the helper
        derives the same fields from the row list itself.

    Returns:
      ``(overrides, metadata)``. Overrides use
      ``type="new_revenue"`` / ``target="OTHER_REVENUE"`` so the
      receipt registers on the revenue side of the WeekRow without
      being mistaken for new PS/FS revenue. ``metadata`` lists every
      scheduled receipt with its date, amount, and source, plus
      warnings for skipped rows.

    Double-count semantics: the helper is ADDITIVE on top of the
    engine's revenue baseline. The baseline projects NEW invoices
    going forward via PS/FS averages; this helper schedules
    collections of EXISTING open invoices. They are distinct cash
    flows. The metadata surfaces ``double_count_note`` for the chat
    tool to quote.
    """
    # Default fetch_meta is derived from the row list so synthetic
    # callers (tests) get the same metadata shape as the live
    # paginated fetcher. The live fetcher overrides these values
    # with real source counts.
    rows_list = list(open_invoice_rows or [])
    fm = dict(fetch_meta or {})
    fm.setdefault("source", "open_invoice_snapshot")
    fm.setdefault("fetched_row_count", len(rows_list))
    fm.setdefault("total_available_rows", len(rows_list))
    fm.setdefault("schedule_cap", None)
    fm.setdefault("page_size", None)
    fm.setdefault("truncated", False)

    metadata: dict[str, Any] = {
        "method": "due_date_plus_days",
        "days_after_due_date": days_after_due_date,
        "row_count": 0,
        # Paginated-fetch audit block; chat surface quotes
        # fetched_row_count / total_available_rows / truncated so a
        # reviewer can see whether AR was clipped at the cap.
        "source": fm["source"],
        "fetched_row_count": fm["fetched_row_count"],
        "total_available_rows": fm["total_available_rows"],
        "schedule_cap": fm["schedule_cap"],
        "page_size": fm["page_size"],
        "truncated": fm["truncated"],
        # Disposition counters — every fetched row lands in exactly
        # ONE of: scheduled / outside_horizon / missing_due_date /
        # invalid_date / non_positive_amount / other_excluded.
        # `reconciliation` sums them all and asserts the total
        # equals fetched_row_count so a reviewer can verify nothing
        # was silently dropped.
        "scheduled_row_count": 0,
        "excluded_outside_horizon_count": 0,
        "excluded_missing_due_date_count": 0,
        "excluded_invalid_date_count": 0,
        "excluded_non_positive_amount_count": 0,
        "other_excluded_count": 0,
        "scheduled_total_amount": 0.0,
        "scheduled_receipts": [],
        "reconciliation": {
            "fetched_row_count": 0,
            "accounted_for_count": 0,
            "is_reconciled": True,
        },
        # Idempotency audit fields — see compute_payroll_schedule.
        "generated_source": "dynamic_ar_receipt_schedule",
        "generated_event_count": 0,
        "deduped_duplicate_count": 0,
        "warnings": [],
        "double_count_note": (
            "AR receipts are additive on top of the engine's "
            "revenue baseline. The baseline projects NEW invoices "
            "(PS/FS averages); this schedule captures collection "
            "of EXISTING open invoices. They are distinct cash "
            "flows by construction."
        ),
    }
    if fm["truncated"]:
        metadata["warnings"].append(
            f"AR fetch truncated: collected {fm['fetched_row_count']} "
            f"of {fm['total_available_rows']} open invoices (cap "
            f"{fm['schedule_cap']}). Pass a tighter date filter or "
            "raise the schedule cap to capture the remaining rows."
        )

    if not isinstance(days_after_due_date, int) or days_after_due_date < 0:
        metadata["warnings"].append(
            "ar_receipts_timing.days_after_due_date must be a "
            "non-negative integer; scheduling skipped."
        )
        return [], metadata

    overrides: list[Override] = []
    scheduled: list[dict[str, Any]] = []
    skipped_no_due_date = 0
    skipped_invalid_date = 0
    skipped_outside_horizon = 0
    skipped_non_positive = 0
    other_excluded = 0
    # ``row_count`` keeps the legacy semantic (every iterable entry
    # the helper saw — including non-dicts). ``fetched_row_count``
    # below is the ONLY count the reconciliation invariant uses; it
    # mirrors the data-layer's fetched_row_count when supplied.
    seen_dedupe_keys: set[str] = set()
    deduped = 0
    for row_index, row in enumerate(rows_list):
        if not isinstance(row, dict):
            other_excluded += 1
            metadata["row_count"] += 1
            continue
        metadata["row_count"] += 1
        due, due_reason = _classify_due_date(row.get("due_date"))
        if due_reason == "missing":
            skipped_no_due_date += 1
            continue
        if due_reason == "invalid":
            skipped_invalid_date += 1
            continue
        try:
            amount = float(row.get("open_balance") or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            # A zero / negative balance contributes nothing — track
            # it explicitly so reviewers can see why fetched > scheduled.
            skipped_non_positive += 1
            continue
        schedule_date = due + timedelta(days=days_after_due_date)
        week_no = week_number_for_date(
            schedule_date, projection_start_month, horizon_weeks,
        )
        if week_no is None:
            skipped_outside_horizon += 1
            continue
        # Stable dedupe key — prefer doc_num/invoice_number as the
        # source identifier so two invoices that happen to share
        # the same scheduled date and amount do NOT collapse into
        # one entry. Fall back to row_index when neither id is
        # present; customer/vendor names are deliberately excluded.
        source_id = (
            row.get("doc_num")
            or row.get("invoice_number")
            or f"row_index_{row_index}"
        )
        dedupe_key = (
            f"dynamic_ar_receipt_schedule:"
            f"{source_id}:{schedule_date.isoformat()}:"
            f"{amount}:OTHER_REVENUE"
        )
        if dedupe_key in seen_dedupe_keys:
            deduped += 1
            continue
        seen_dedupe_keys.add(dedupe_key)
        overrides.append(Override(
            type="new_revenue",
            target="OTHER_REVENUE",
            week_number=week_no,
            amount=amount,
            description=(
                f"scheduled AR receipt {schedule_date.isoformat()} "
                f"(due {due.isoformat()} + "
                f"{days_after_due_date}d)"
            ),
            generated_source="dynamic_ar_receipt_schedule",
            dedupe_key=dedupe_key,
        ))
        scheduled.append({
            "date": schedule_date.isoformat(),
            "amount": amount,
            "source": "open_ar",
        })

    metadata["scheduled_receipts"] = scheduled
    metadata["scheduled_row_count"] = len(scheduled)
    metadata["excluded_outside_horizon_count"] = skipped_outside_horizon
    metadata["excluded_missing_due_date_count"] = skipped_no_due_date
    metadata["excluded_invalid_date_count"] = skipped_invalid_date
    metadata["excluded_non_positive_amount_count"] = skipped_non_positive
    metadata["other_excluded_count"] = other_excluded
    metadata["scheduled_total_amount"] = round(
        sum(s["amount"] for s in scheduled), 2,
    )
    metadata["generated_event_count"] = len(overrides)
    metadata["deduped_duplicate_count"] = deduped

    # Reconciliation invariant: every fetched row lands in EXACTLY
    # one bucket. fetched_row_count is the data-layer's count when
    # the dispatcher supplied a fetch_meta block (the live path);
    # for callers that didn't, it equals row_count.
    fetched_total = (
        fm.get("fetched_row_count")
        if fm.get("fetched_row_count") is not None
        else metadata["row_count"]
    )
    accounted_for = (
        metadata["scheduled_row_count"]
        + metadata["excluded_outside_horizon_count"]
        + metadata["excluded_missing_due_date_count"]
        + metadata["excluded_invalid_date_count"]
        + metadata["excluded_non_positive_amount_count"]
        + metadata["other_excluded_count"]
        # Duplicates collapsed during dedupe are real fetched rows
        # that we chose to ignore — include them in the
        # accounted-for total so the reconciliation invariant
        # still holds.
        + metadata["deduped_duplicate_count"]
    )
    is_reconciled = (fetched_total == accounted_for)
    metadata["reconciliation"] = {
        "fetched_row_count": fetched_total,
        "accounted_for_count": accounted_for,
        "is_reconciled": is_reconciled,
    }
    if not is_reconciled:
        metadata["warnings"].append(
            f"AR schedule did NOT reconcile: fetched "
            f"{fetched_total} rows but accounted for "
            f"{accounted_for}. {fetched_total - accounted_for} "
            "row(s) unaccounted. Inspect the source rows for an "
            "exclusion reason that did not land in a known bucket."
        )

    if skipped_no_due_date:
        metadata["warnings"].append(
            f"{skipped_no_due_date} open AR row(s) had no usable "
            "due_date and were excluded from the schedule."
        )
    if skipped_outside_horizon:
        metadata["warnings"].append(
            f"{skipped_outside_horizon} AR receipt(s) fell outside "
            "the projection horizon and were excluded."
        )
    return overrides, metadata


# ---------------------------------------------------------------------------
# AP payments schedule
# ---------------------------------------------------------------------------


def compute_ap_schedule(
    *,
    open_bill_rows: Iterable[dict[str, Any]],
    projection_start_month: str,
    horizon_weeks: int,
    fetch_meta: dict[str, Any] | None = None,
) -> tuple[list[Override], dict[str, Any]]:
    """Schedule AP payments on each bill's ``due_date``.

    Args:
      open_bill_rows: iterable of row dicts. Each row must carry
        ``due_date`` (YYYY-MM-DD string) and either ``open_balance``
        or ``balance`` (float). Rows without a usable due_date
        contribute to ``warnings`` and are excluded.
      fetch_meta: optional audit block from the data-access layer
        (``source``, ``fetched_row_count``, ``total_available_rows``,
        ``schedule_cap``, ``page_size``, ``truncated``). Mirrors the
        AR helper so the response carries the same shape.

    Returns:
      ``(overrides, metadata)``. Overrides use
      ``type="new_cost"`` / ``target="OTHER_COSTS"`` so the payment
      lands in a generic cost bucket that does not collide with the
      revenue-driven COGS projections.

    Double-count semantics: same as AR — additive on top of the
    engine's baseline. The baseline projects future operational
    costs via revenue-derived COGS ratios; this helper schedules
    payment of EXISTING open bills.
    """
    rows_list = list(open_bill_rows or [])
    fm = dict(fetch_meta or {})
    fm.setdefault("source", "ap_bills")
    fm.setdefault("fetched_row_count", len(rows_list))
    fm.setdefault("total_available_rows", len(rows_list))
    fm.setdefault("schedule_cap", None)
    fm.setdefault("page_size", None)
    fm.setdefault("truncated", False)

    metadata: dict[str, Any] = {
        "method": "due_date",
        "row_count": 0,
        "source": fm["source"],
        "fetched_row_count": fm["fetched_row_count"],
        "total_available_rows": fm["total_available_rows"],
        "schedule_cap": fm["schedule_cap"],
        "page_size": fm["page_size"],
        "truncated": fm["truncated"],
        # Disposition counters — mirror the AR shape so chat surface
        # can quote the same fields for both blocks. Reconciliation
        # invariant below asserts every fetched row lands in exactly
        # one bucket.
        "scheduled_row_count": 0,
        "excluded_outside_horizon_count": 0,
        "excluded_missing_due_date_count": 0,
        "excluded_invalid_date_count": 0,
        "excluded_non_positive_amount_count": 0,
        "other_excluded_count": 0,
        "scheduled_total_amount": 0.0,
        "scheduled_payments": [],
        "reconciliation": {
            "fetched_row_count": 0,
            "accounted_for_count": 0,
            "is_reconciled": True,
        },
        # Idempotency audit fields — see compute_payroll_schedule.
        "generated_source": "dynamic_ap_payment_schedule",
        "generated_event_count": 0,
        "deduped_duplicate_count": 0,
        "warnings": [],
        "double_count_note": (
            "AP payments are additive on top of the engine's cost "
            "baseline. The baseline projects future costs via "
            "revenue-derived COGS ratios; this schedule captures "
            "payment of EXISTING open bills. They are distinct cash "
            "flows by construction."
        ),
    }
    if fm["truncated"]:
        metadata["warnings"].append(
            f"AP fetch truncated: collected {fm['fetched_row_count']} "
            f"of {fm['total_available_rows']} open bills (cap "
            f"{fm['schedule_cap']}). Pass a tighter date filter or "
            "raise the schedule cap to capture the remaining rows."
        )

    overrides: list[Override] = []
    scheduled: list[dict[str, Any]] = []
    skipped_no_due_date = 0
    skipped_invalid_date = 0
    skipped_outside_horizon = 0
    skipped_non_positive = 0
    other_excluded = 0
    seen_dedupe_keys: set[str] = set()
    deduped = 0
    for row_index, row in enumerate(rows_list):
        if not isinstance(row, dict):
            other_excluded += 1
            metadata["row_count"] += 1
            continue
        metadata["row_count"] += 1
        due, due_reason = _classify_due_date(row.get("due_date"))
        if due_reason == "missing":
            skipped_no_due_date += 1
            continue
        if due_reason == "invalid":
            skipped_invalid_date += 1
            continue
        # Open bills in the AEGISAXIOM schema may surface the open amount
        # under ``open_balance`` (Nova-facing rows) or ``balance``
        # (raw ap_bills rows). Check both so the dispatcher doesn't
        # have to normalise upstream.
        raw_amount = row.get("open_balance")
        if raw_amount is None:
            raw_amount = row.get("balance")
        try:
            amount = float(raw_amount or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            skipped_non_positive += 1
            continue
        week_no = week_number_for_date(
            due, projection_start_month, horizon_weeks,
        )
        if week_no is None:
            skipped_outside_horizon += 1
            continue
        # Stable dedupe key — prefer doc_num as the bill identifier
        # so two bills with the same due-date + amount stay distinct.
        # Customer/vendor names deliberately excluded.
        source_id = (
            row.get("doc_num")
            or row.get("bill_number")
            or f"row_index_{row_index}"
        )
        dedupe_key = (
            f"dynamic_ap_payment_schedule:"
            f"{source_id}:{due.isoformat()}:"
            f"{amount}:OTHER_COSTS"
        )
        if dedupe_key in seen_dedupe_keys:
            deduped += 1
            continue
        seen_dedupe_keys.add(dedupe_key)
        overrides.append(Override(
            type="new_cost",
            target="OTHER_COSTS",
            week_number=week_no,
            amount=amount,
            description=(
                f"scheduled AP payment {due.isoformat()} (on due date)"
            ),
            generated_source="dynamic_ap_payment_schedule",
            dedupe_key=dedupe_key,
        ))
        scheduled.append({
            "date": due.isoformat(),
            "amount": amount,
            "source": "open_ap",
        })

    metadata["scheduled_payments"] = scheduled
    metadata["scheduled_row_count"] = len(scheduled)
    metadata["excluded_outside_horizon_count"] = skipped_outside_horizon
    metadata["excluded_missing_due_date_count"] = skipped_no_due_date
    metadata["excluded_invalid_date_count"] = skipped_invalid_date
    metadata["excluded_non_positive_amount_count"] = skipped_non_positive
    metadata["other_excluded_count"] = other_excluded
    metadata["scheduled_total_amount"] = round(
        sum(s["amount"] for s in scheduled), 2,
    )
    metadata["generated_event_count"] = len(overrides)
    metadata["deduped_duplicate_count"] = deduped

    fetched_total = (
        fm.get("fetched_row_count")
        if fm.get("fetched_row_count") is not None
        else metadata["row_count"]
    )
    accounted_for = (
        metadata["scheduled_row_count"]
        + metadata["excluded_outside_horizon_count"]
        + metadata["excluded_missing_due_date_count"]
        + metadata["excluded_invalid_date_count"]
        + metadata["excluded_non_positive_amount_count"]
        + metadata["other_excluded_count"]
        # Duplicates collapsed during dedupe are real fetched rows
        # that we chose to ignore — include them in the
        # accounted-for total so the reconciliation invariant
        # still holds.
        + metadata["deduped_duplicate_count"]
    )
    is_reconciled = (fetched_total == accounted_for)
    metadata["reconciliation"] = {
        "fetched_row_count": fetched_total,
        "accounted_for_count": accounted_for,
        "is_reconciled": is_reconciled,
    }
    if not is_reconciled:
        metadata["warnings"].append(
            f"AP schedule did NOT reconcile: fetched "
            f"{fetched_total} rows but accounted for "
            f"{accounted_for}. {fetched_total - accounted_for} "
            "row(s) unaccounted. Inspect the source rows for an "
            "exclusion reason that did not land in a known bucket."
        )

    if skipped_no_due_date:
        metadata["warnings"].append(
            f"{skipped_no_due_date} open AP row(s) had no usable "
            "due_date and were excluded from the schedule."
        )
    if skipped_outside_horizon:
        metadata["warnings"].append(
            f"{skipped_outside_horizon} AP payment(s) fell outside "
            "the projection horizon and were excluded."
        )
    return overrides, metadata


__all__ = [
    "compute_ap_schedule",
    "compute_ar_schedule",
    "compute_payroll_schedule",
    "week_number_for_date",
]
