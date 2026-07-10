"""Accounts payable timing computation.

Pure deterministic helper. Called from the Nova tool layer to answer
questions like.

  What are the current avg payables time?
  What is our DPO?
  How long are we taking to pay vendors?

The earlier failure mode was that Nova had no tool that returned an
AP timing number. The closest available tool dumped raw AR/AP aging
snapshots and asked the LLM to narrate the trend. The project rule is
tools compute and Nova explains, so the LLM refused to do the
finance math itself and surfaced the refusal text the spec calls out.

This helper does the math and picks the best available metric in
priority order. The Nova tool wraps the result with summary fields
and routing metadata.

Priority order. Highest first.

  paid_bill_cycle. average_days_to_pay equals the mean of payment date
  minus bill date across paid bill rows. average_days_late equals the
  mean of payment date minus due date. Both means skip rows that are
  missing the required dates and count the skips. The metric is
  preferred because it is the only one that uses real cash dates.

  dpo_proxy. DPO equals average_AP divided by daily_purchases. The
  helper accepts beginning_ap and ending_ap explicitly because they
  come from different aging snapshots and we never want the helper to
  re fetch. daily_purchases equals period_purchases divided by
  days_in_period. The metric is labelled as a proxy because we do not
  have true credit purchases.

  open_ap_timing. Weighted by open balance. The weighted average open
  AP age is the sum of open amount times days since the bill date,
  divided by the sum of open amount. The weighted days past due
  clamps each per row days past due to a floor of zero so a not yet
  due bill never reduces the average. Rows with zero or negative open
  balance are excluded and counted as exclusions. Rows missing the
  bill date cannot contribute to the open AP age. Rows missing the
  due date cannot contribute to the past due average. Both shortfalls
  are counted explicitly.

Every metric path returns the same envelope so the caller can render
one consistent answer shape regardless of which path won.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any


# Public metric identifiers. Match the spec literal strings so the tool
# layer can fan its rendering off a known set.
METRIC_PAID_BILL_CYCLE = "paid_bill_cycle"
METRIC_DPO_PROXY = "dpo_proxy"
METRIC_OPEN_AP_TIMING = "open_ap_timing"


# Default lookback used to derive the period bounds when the caller
# does not supply them. Ninety days mirrors the quarter the working
# capital dashboard uses for the related DSO trend. The helper never
# reaches into the global clock itself. The caller passes as_of_date
# so the function stays unit testable without monkeypatching time.
_DEFAULT_PERIOD_DAYS = 90


def _coerce_date(value: Any) -> date | None:
    """Convert a value to a date. None and unparseable values yield None.

    Accepts a date, a datetime, or a YYYY MM DD string. Reject anything
    else by returning None so the row can be counted as a missing date
    case instead of silently miscomputed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return datetime.strptime(candidate[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _coerce_float(value: Any) -> float | None:
    """Float coercion that surfaces None on unparseable input. Required
    because Mongo can store amounts as either float, int, Decimal128, or
    string depending on the upstream ingestion path."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _empty_envelope() -> dict[str, Any]:
    """Canonical payload shape. Every metric path returns this exact
    set of keys so the caller never has to branch on which path ran."""
    return {
        "metric": None,
        "as_of_date": None,
        "basis": None,
        "period_start": None,
        "period_end": None,
        "average_days_to_pay": None,
        "average_days_late": None,
        "dpo": None,
        "weighted_average_open_ap_age_days": 0.0,
        "weighted_average_days_past_due": 0.0,
        "open_ap_total": 0.0,
        "open_bill_count": 0,
        "overdue_bill_count": 0,
        "due_soon_count": 0,
        "missing_bill_date_count": 0,
        "missing_due_date_count": 0,
        "row_count": 0,
        "vendors_count": 0,
        "method": None,
        "data_sources": [],
        "warnings": [],
    }


def _resolve_period_bounds(
    as_of_date: date,
    period_start: Any,
    period_end: Any,
    period_days: int | None,
) -> tuple[date, date, int]:
    """Resolve period_start, period_end, and days_in_period.

    Caller supplied dates win. When only period_days is supplied the
    window ends at as_of_date and starts period_days earlier. When
    nothing is supplied the helper falls back to the default lookback
    so the DPO branch can still run on a freshly initialised tool
    request without any caller cooperation.
    """
    end_raw = _coerce_date(period_end)
    start_raw = _coerce_date(period_start)
    if end_raw is None:
        end_raw = as_of_date
    if start_raw is None:
        window = period_days if isinstance(period_days, int) and period_days > 0 else _DEFAULT_PERIOD_DAYS
        start_raw = date.fromordinal(end_raw.toordinal() - window)
    days = (end_raw.toordinal() - start_raw.toordinal())
    if days <= 0:
        days = _DEFAULT_PERIOD_DAYS
    return start_raw, end_raw, days


# ---------------------------------------------------------------------------
# Branch 1. Paid bill cycle.
# ---------------------------------------------------------------------------


def _compute_paid_bill_cycle(
    envelope: dict[str, Any],
    paid_bill_rows: Iterable[Mapping[str, Any]],
) -> bool:
    """Populate envelope with paid bill cycle metrics when at least one
    paid row carries every required field. Returns True when the metric
    was computed."""
    days_to_pay_samples: list[float] = []
    days_late_samples: list[float] = []
    total_paid = 0.0
    vendors: set[str] = set()
    missing_bill_date = 0
    missing_due_date = 0
    missing_payment_date = 0
    row_count = 0

    for row in paid_bill_rows:
        row_count += 1
        bill_date = _coerce_date(row.get("bill_date") or row.get("txn_date"))
        payment_date = _coerce_date(row.get("payment_date") or row.get("paid_at"))
        due_date = _coerce_date(row.get("due_date"))
        amount = _coerce_float(row.get("amount") or row.get("paid_amount") or row.get("total_amount"))
        vendor = row.get("vendor") or row.get("vendor_id") or row.get("vendor_name")
        if vendor:
            vendors.add(str(vendor))
        if amount is not None:
            total_paid += amount
        if payment_date is None:
            missing_payment_date += 1
            continue
        if bill_date is None:
            missing_bill_date += 1
        else:
            days_to_pay_samples.append(payment_date.toordinal() - bill_date.toordinal())
        if due_date is None:
            missing_due_date += 1
        else:
            days_late_samples.append(payment_date.toordinal() - due_date.toordinal())

    if not days_to_pay_samples and not days_late_samples:
        return False

    envelope["metric"] = METRIC_PAID_BILL_CYCLE
    envelope["method"] = (
        "Average of payment date minus bill date across paid bill rows."
    )
    envelope["row_count"] = row_count
    envelope["vendors_count"] = len(vendors)
    envelope["open_ap_total"] = round(total_paid, 2)
    envelope["data_sources"].append("paid_bill_rows")
    if days_to_pay_samples:
        envelope["average_days_to_pay"] = round(
            sum(days_to_pay_samples) / len(days_to_pay_samples),
            2,
        )
    if days_late_samples:
        envelope["average_days_late"] = round(
            sum(days_late_samples) / len(days_late_samples),
            2,
        )
    envelope["missing_bill_date_count"] = missing_bill_date
    envelope["missing_due_date_count"] = missing_due_date
    if missing_payment_date:
        envelope["warnings"].append(
            f"{missing_payment_date} paid bill rows had no payment date and were skipped."
        )
    if missing_bill_date and not days_to_pay_samples:
        envelope["warnings"].append(
            "No paid bill rows carried a bill date so average_days_to_pay is unavailable."
        )
    if missing_due_date and not days_late_samples:
        envelope["warnings"].append(
            "No paid bill rows carried a due date so average_days_late is unavailable."
        )
    return True


# ---------------------------------------------------------------------------
# Branch 2. DPO proxy.
# ---------------------------------------------------------------------------


def _compute_dpo_proxy(
    envelope: dict[str, Any],
    beginning_ap: Any,
    ending_ap: Any,
    period_purchases: Any,
    days_in_period: int,
    purchases_source: str | None,
) -> bool:
    """Populate envelope with the DPO proxy when both the AP balances
    and the period purchases input are present and the daily purchases
    figure is positive. Returns True when the metric was computed.

    The metric is labelled a proxy because we do not have true credit
    purchases and the helper falls back to period expense or COGS as a
    stand in. The label is surfaced both on the metric key and in the
    method string so the caller can render the disclaimer in place.
    """
    beginning = _coerce_float(beginning_ap)
    ending = _coerce_float(ending_ap)
    purchases = _coerce_float(period_purchases)
    if beginning is None or ending is None or purchases is None:
        return False
    if days_in_period <= 0:
        return False
    daily_purchases = purchases / days_in_period
    if daily_purchases <= 0:
        envelope["warnings"].append(
            "DPO proxy needs positive daily purchases. Skipping the proxy and "
            "falling back to the open AP timing view."
        )
        return False

    average_ap = (beginning + ending) / 2
    if average_ap <= 0:
        envelope["warnings"].append(
            "DPO proxy needs a positive average AP balance. Skipping the proxy "
            "and falling back to the open AP timing view."
        )
        return False

    envelope["metric"] = METRIC_DPO_PROXY
    envelope["dpo"] = round(average_ap / daily_purchases, 2)
    envelope["open_ap_total"] = round(ending, 2)
    envelope["method"] = (
        "DPO proxy. Average AP divided by daily purchases. Labelled proxy "
        "because true credit purchases are unavailable so the helper used "
        f"{purchases_source or 'period expense or COGS'} as the purchases stand in."
    )
    envelope["data_sources"].append("ap_balance_snapshots")
    envelope["data_sources"].append(purchases_source or "period_expense_or_cogs")
    return True


# ---------------------------------------------------------------------------
# Branch 3. Open AP timing fallback.
# ---------------------------------------------------------------------------


def _compute_open_ap_timing(
    envelope: dict[str, Any],
    open_ap_rows: Iterable[Mapping[str, Any]],
    as_of_date: date,
    due_soon_window_days: int,
) -> None:
    """Populate envelope with the open AP timing fallback. Always runs
    when called so the envelope reports a usable answer even when only
    open bill data exists. Mutates envelope in place.

    Rules.

    Rows with zero or negative open balance are excluded and counted as
    exclusions because a zero balance bill has been paid and including
    it would understate the open AP age. A negative balance row is a
    credit memo and is not part of the payables aging the question is
    asking about.

    Rows missing the bill date cannot contribute to the weighted
    average open AP age. They are counted in missing_bill_date_count
    and dropped from that weight only.

    Rows missing the due date cannot contribute to the weighted days
    past due. They are counted in missing_due_date_count and dropped
    from that weight only. They still contribute to the open AP total
    and the open bill count because we know the balance is owed.

    The past due weighting clamps each per row past due value at zero
    so a not yet due bill never reduces the average. A bill that is N
    days past due adds N to the weighted past due number. A bill that
    is twenty days early adds zero.
    """
    weighted_age_numerator = 0.0
    weighted_age_denominator = 0.0
    weighted_past_due_numerator = 0.0
    weighted_past_due_denominator = 0.0

    open_total = 0.0
    open_count = 0
    overdue_count = 0
    due_soon_count = 0
    missing_bill_date = 0
    missing_due_date = 0
    excluded_non_positive = 0
    vendors: set[str] = set()
    total_rows = 0

    for row in open_ap_rows:
        total_rows += 1
        balance = _coerce_float(row.get("balance"))
        if balance is None:
            balance = _coerce_float(row.get("open_balance"))
        if balance is None or balance <= 0:
            excluded_non_positive += 1
            continue

        open_total += balance
        open_count += 1
        vendor = row.get("vendor") or row.get("vendor_id") or row.get("vendor_name")
        if vendor:
            vendors.add(str(vendor))

        bill_date = _coerce_date(row.get("bill_date") or row.get("txn_date"))
        due_date = _coerce_date(row.get("due_date"))

        if bill_date is None:
            missing_bill_date += 1
        else:
            age_days = as_of_date.toordinal() - bill_date.toordinal()
            weighted_age_numerator += balance * age_days
            weighted_age_denominator += balance

        if due_date is None:
            missing_due_date += 1
        else:
            past_due_days = max(0, as_of_date.toordinal() - due_date.toordinal())
            if as_of_date > due_date:
                overdue_count += 1
            else:
                days_until_due = due_date.toordinal() - as_of_date.toordinal()
                if 0 <= days_until_due <= due_soon_window_days:
                    due_soon_count += 1
            weighted_past_due_numerator += balance * past_due_days
            weighted_past_due_denominator += balance

    envelope["metric"] = METRIC_OPEN_AP_TIMING
    envelope["method"] = (
        "Open AP timing fallback. Weighted average open AP age uses open "
        "balance as the weight. Weighted days past due clamps each per "
        "row past due value at zero so not yet due bills do not pull the "
        "average down."
    )
    envelope["data_sources"].append("open_ap_rows")
    envelope["open_ap_total"] = round(open_total, 2)
    envelope["open_bill_count"] = open_count
    envelope["overdue_bill_count"] = overdue_count
    envelope["due_soon_count"] = due_soon_count
    envelope["missing_bill_date_count"] = missing_bill_date
    envelope["missing_due_date_count"] = missing_due_date
    envelope["row_count"] = total_rows
    envelope["vendors_count"] = len(vendors)

    if weighted_age_denominator > 0:
        envelope["weighted_average_open_ap_age_days"] = round(
            weighted_age_numerator / weighted_age_denominator,
            2,
        )
    if weighted_past_due_denominator > 0:
        envelope["weighted_average_days_past_due"] = round(
            weighted_past_due_numerator / weighted_past_due_denominator,
            2,
        )

    if excluded_non_positive:
        envelope["warnings"].append(
            f"{excluded_non_positive} bill rows had a zero or negative open balance "
            "and were excluded from the weighted open AP timing."
        )
    if missing_bill_date:
        envelope["warnings"].append(
            f"{missing_bill_date} bill rows had no bill date and were excluded "
            "from weighted_average_open_ap_age_days."
        )
    if missing_due_date:
        envelope["warnings"].append(
            f"{missing_due_date} bill rows had no due date and were excluded "
            "from weighted_average_days_past_due."
        )
    if open_count == 0:
        envelope["warnings"].append(
            "No open AP rows carried a positive balance so the open AP timing "
            "view is empty. Confirm the AP sync has run."
        )


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def compute_payables_timing(
    *,
    as_of_date: Any,
    basis: str | None = "accrual",
    open_ap_rows: Iterable[Mapping[str, Any]] | None = None,
    paid_bill_rows: Iterable[Mapping[str, Any]] | None = None,
    beginning_ap: Any = None,
    ending_ap: Any = None,
    period_purchases: Any = None,
    purchases_source: str | None = None,
    period_start: Any = None,
    period_end: Any = None,
    period_days: int | None = None,
    due_soon_window_days: int = 14,
) -> dict[str, Any]:
    """Compute the best available accounts payable timing metric.

    Priority order. paid_bill_cycle, then dpo_proxy, then
    open_ap_timing. Whichever branch wins fills the envelope. Lower
    priority branches are skipped silently once a higher one succeeds.
    The envelope shape is identical regardless of which branch runs so
    the caller can render one consistent answer.

    Inputs in detail.

      as_of_date. Required. The reference date for the open AP timing
        fallback. The helper does not consult the system clock.

      basis. Stored on the envelope verbatim. Reported back to the
        caller but does not change the math.

      open_ap_rows. Iterable of dicts that look like the rows
        get_open_bills returns. The helper reads balance or
        open_balance, txn_date or bill_date, due_date, and vendor
        related fields. Other fields are ignored.

      paid_bill_rows. Iterable of dicts that look like a paid bill
        history. The helper reads payment_date or paid_at, bill_date
        or txn_date, due_date, vendor identifiers, and amount or
        paid_amount or total_amount. Other fields are ignored. When no
        rows carry the required dates the branch reports the data
        problem and falls back to the next branch.

      beginning_ap, ending_ap, period_purchases. DPO proxy inputs. All
        three are coerced through float. The branch refuses to run if
        any of them is missing or if daily purchases or average AP
        comes out non positive.

      purchases_source. Free text label that says where the purchases
        figure came from. Surfaced in the method string so the proxy
        disclaimer reads truthfully.

      period_start, period_end, period_days. Period bounds for the DPO
        proxy. When unset the helper picks a default ninety day
        window ending at as_of_date.

      due_soon_window_days. The open AP fallback uses this to classify
        a not yet due bill as due soon. Defaults to fourteen days.
    """
    as_of = _coerce_date(as_of_date)
    if as_of is None:
        raise ValueError("as_of_date must be a parseable date")

    envelope = _empty_envelope()
    envelope["as_of_date"] = _format_date(as_of)
    envelope["basis"] = basis

    period_start_date, period_end_date, days_in_period = _resolve_period_bounds(
        as_of, period_start, period_end, period_days,
    )
    envelope["period_start"] = _format_date(period_start_date)
    envelope["period_end"] = _format_date(period_end_date)

    # Branch 1. Paid bill cycle. Highest priority because it uses real
    # cash dates rather than a balance sheet proxy or an open snapshot.
    if paid_bill_rows is not None:
        if _compute_paid_bill_cycle(envelope, paid_bill_rows):
            return envelope

    # Branch 2. DPO proxy. Runs only when the caller supplied both AP
    # balances and the purchases stand in.
    if _compute_dpo_proxy(
        envelope,
        beginning_ap,
        ending_ap,
        period_purchases,
        days_in_period,
        purchases_source,
    ):
        return envelope

    # Branch 3. Open AP timing fallback. Always runs when the helper
    # reaches this line so the caller never gets the no answer text
    # the spec calls out. An empty open_ap_rows list lands with a
    # warning and a zero envelope rather than a refusal.
    _compute_open_ap_timing(
        envelope,
        open_ap_rows or [],
        as_of,
        due_soon_window_days,
    )
    return envelope


__all__ = [
    "METRIC_DPO_PROXY",
    "METRIC_OPEN_AP_TIMING",
    "METRIC_PAID_BILL_CYCLE",
    "compute_payables_timing",
]
