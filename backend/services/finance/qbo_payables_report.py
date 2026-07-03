"""QBO payables / receivables weekly report.

Deterministic pure functions called from finance routes and the
finance assistant. Inputs are raw rows the caller already pulled from
Mongo. Outputs are JSON-friendly dicts the caller hands back as the
route response and as the source for the spreadsheet export.

Why this lives outside services/nova_engine. The existing
services/nova_engine/payables_timing.py answers a single user
question. This module answers the broader weekly finance plan that
mixes AR and AP, drives the spreadsheet export, and feeds the data
health audit. Keeping the two modules separate avoids piling
incompatible response shapes onto the older helper.

Functions exported.

  build_weekly_finance_report. Assembles the six section response the
    spec calls out. Summary, AR by week, AP by week, raw AR, raw AP,
    sync audit.

  calculate_payables_metrics. Returns the three timing metrics the
    spec calls out, each with value, count, excluded_count, formula,
    and warnings, plus open_payables_total and
    overdue_open_payables_total.

  bucket_rows_by_week_ending. Groups normalized rows by week-ending
    day. Returns one bucket per week within the requested horizon.

  audit_qbo_finance_data_health. Inspects raw AP and AR rows and the
    sync record and returns counts plus exclusion reasons. Read only.

Conventions.

  Dates are ISO YYYY MM DD strings on the wire. The helpers coerce
  through datetime.date internally.

  Week ending day. The spec leaves the day configurable. Default
  Friday is documented as a default, not a hardcoded business rule.
  Callers pass week_ending_day as a Python weekday integer where
  Monday is zero and Sunday is six. Friday is four.

  Tenant scoping is the caller's job. These functions accept already
  filtered rows and treat tenant_id as a string passed through so
  the response carries the audit trail.

  No row mutation. Inputs are treated read only. Normalized output
  rows are fresh dicts.

  Synthetic test fixtures only in tests. The helper itself never
  reads from Mongo or QuickBooks.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Default week ending day. Friday in Python weekday terms is four
# where Monday is zero. The default is documented as a default rather
# than a business rule. Callers can override via the configurable
# parameter on the helpers and on the route query string.
DEFAULT_WEEK_ENDING_DAY = 4


# Mongo collection names the helper reads. Discovered from the
# existing field_registry registrations and existing route reads.
AP_COLLECTION = "ap_bills"
AR_COLLECTION = "ar_invoices"


# Defensive cap on rows pulled per collection so a runaway dataset
# never blows up a single request. Eight thousand keeps a quarter
# worth of activity comfortably for any one tenant.
MAX_ROWS = 8000


# ---------------------------------------------------------------------------
# Coercion helpers. The Mongo lake mixes Decimal, int, string, and None
# for amount fields. ISO strings, datetime objects, and date objects
# all show up for date fields. The coercions surface None on
# unparseable values so the caller can count the bad rows and explain
# the exclusion rather than silently dropping them.
# ---------------------------------------------------------------------------


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _vendor_name(row: Mapping[str, Any]) -> str | None:
    """Read the vendor display name from either the top level vendor_ref
    object or the raw_data.VendorRef shape. Both shapes appear in the
    lake depending on which sync run wrote the row. None when neither
    carries a name."""
    ref = row.get("vendor_ref")
    if isinstance(ref, Mapping):
        name = ref.get("name") or ref.get("display_name")
        if name:
            return str(name)
    raw = row.get("raw_data")
    if isinstance(raw, Mapping):
        v = raw.get("VendorRef")
        if isinstance(v, Mapping):
            name = v.get("name") or v.get("display_name")
            if name:
                return str(name)
    return None


def _customer_name(row: Mapping[str, Any]) -> str | None:
    ref = row.get("customer_ref")
    if isinstance(ref, Mapping):
        name = ref.get("name") or ref.get("display_name")
        if name:
            return str(name)
    raw = row.get("raw_data")
    if isinstance(raw, Mapping):
        c = raw.get("CustomerRef")
        if isinstance(c, Mapping):
            name = c.get("name") or c.get("display_name")
            if name:
                return str(name)
    return None


def _payment_date(row: Mapping[str, Any]) -> date | None:
    """Best effort payment date for a paid bill.

    AEGISAXIOM does not sync the QBO BillPayment entity, so an exact cash
    settlement date is not always available. Callers should treat
    None as a data gap. The helper tries, in order.

      1. Explicit payment_date or paid_at on the top level row. Some
         sync pipelines materialise this; most do not.
      2. raw_data.LinkedTxn entries of TxnType BillPayment. The QBO
         payload links payment transactions there with their txn
         date.
      3. raw_data.MetaData.LastUpdatedTime as a proxy when the bill
         is fully paid. This is the most recent QBO write to the
         bill record and only loosely correlates with the payment
         date, so the helper only falls back to it when balance is
         zero and the other paths returned nothing.
    """
    direct = _to_date(row.get("payment_date") or row.get("paid_at"))
    if direct is not None:
        return direct
    raw = row.get("raw_data")
    if isinstance(raw, Mapping):
        linked = raw.get("LinkedTxn") or raw.get("linked_txn") or []
        if isinstance(linked, list):
            for link in linked:
                if not isinstance(link, Mapping):
                    continue
                txn_type = (link.get("TxnType") or link.get("txn_type") or "").lower()
                if "billpayment" not in txn_type.replace(" ", "").replace("_", ""):
                    continue
                cand = _to_date(link.get("TxnDate") or link.get("txn_date"))
                if cand is not None:
                    return cand
        # Last resort proxy. The bill is fully paid in QBO if balance
        # is zero. Last-updated then sits close to but not exactly on
        # the payment date.
        balance = _to_float(row.get("balance"))
        total = _to_float(row.get("total_amount"))
        if balance is not None and total is not None and total > 0 and balance == 0:
            meta = raw.get("MetaData") or raw.get("metadata") or {}
            if isinstance(meta, Mapping):
                cand = _to_date(meta.get("LastUpdatedTime") or meta.get("last_updated_time"))
                if cand is not None:
                    return cand
    return None


def _is_paid(row: Mapping[str, Any]) -> bool:
    """A bill is paid when balance is zero AND total_amount is positive.

    Open bills have balance > 0. Voided or zero amount bills carry
    total_amount zero and are ignored by the paid bill metric.
    """
    balance = _to_float(row.get("balance"))
    total = _to_float(row.get("total_amount"))
    if balance is None or total is None:
        return False
    return balance == 0 and total > 0


def _is_open(row: Mapping[str, Any]) -> bool:
    balance = _to_float(row.get("balance"))
    return balance is not None and balance > 0


# ---------------------------------------------------------------------------
# Row normalisation. These produce JSON friendly dicts the route layer
# returns as raw_ar and raw_ap. Source IDs are surfaced so a reviewer
# can trace any number back to QBO without us having to embed a
# secret realm id in the response.
# ---------------------------------------------------------------------------


def _normalize_ap_row(row: Mapping[str, Any]) -> dict[str, Any]:
    bill_date = _to_date(row.get("txn_date"))
    due_date = _to_date(row.get("due_date"))
    payment_date = _payment_date(row)
    balance = _to_float(row.get("balance"))
    total = _to_float(row.get("total_amount"))
    return {
        "qbo_bill_id": row.get("qbo_bill_id"),
        "doc_number": row.get("doc_number"),
        "bill_date": _fmt_date(bill_date),
        "due_date": _fmt_date(due_date),
        "payment_date": _fmt_date(payment_date),
        "balance": balance,
        "total_amount": total,
        "paid_amount": (total - balance) if (balance is not None and total is not None) else None,
        "vendor": _vendor_name(row),
        "is_paid": _is_paid(row),
        "is_open": _is_open(row),
    }


def _normalize_ar_row(row: Mapping[str, Any]) -> dict[str, Any]:
    invoice_date = _to_date(row.get("txn_date"))
    due_date = _to_date(row.get("due_date"))
    balance = _to_float(row.get("balance"))
    total = _to_float(row.get("total_amount"))
    return {
        "qbo_invoice_id": row.get("qbo_invoice_id"),
        "doc_number": row.get("doc_number"),
        "invoice_date": _fmt_date(invoice_date),
        "due_date": _fmt_date(due_date),
        "balance": balance,
        "total_amount": total,
        "paid_amount": (total - balance) if (balance is not None and total is not None) else None,
        "customer": _customer_name(row),
        "is_paid": balance == 0 if balance is not None else None,
        "is_open": balance > 0 if balance is not None else None,
    }


# ---------------------------------------------------------------------------
# Week bucketing.
# ---------------------------------------------------------------------------


def _week_end_for(target: date, week_ending_day: int) -> date:
    """Return the date of the week ending day on or after target.

    week_ending_day is a Python weekday integer 0 to 6 where Monday is
    zero and Sunday is six. Friday is four. Saturday is five.
    """
    target_weekday = target.weekday()
    delta = (week_ending_day - target_weekday) % 7
    return target + timedelta(days=delta)


def _normalise_week_ending(value: Any) -> int:
    """Accept the week ending day as either a weekday integer 0 to 6
    or a case insensitive English name. Falls back to the documented
    default when the input does not parse."""
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    if isinstance(value, str):
        names = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        key = value.strip().lower()
        if key in names:
            return names[key]
    return DEFAULT_WEEK_ENDING_DAY


def bucket_rows_by_week_ending(
    rows: Iterable[Mapping[str, Any]],
    date_field: str,
    week_ending_day: Any,
    *,
    as_of_date: Any = None,
    week_count: int | None = None,
    amount_field: str = "balance",
) -> list[dict[str, Any]]:
    """Group rows into week buckets keyed on the week ending date.

    Args.
      rows. Iterable of normalized AR or AP rows.
      date_field. The field that drives bucketing. Typically
        invoice_date or due_date for AR, bill_date or due_date for AP.
      week_ending_day. Weekday integer or English name.
      as_of_date. Anchors the horizon when week_count is supplied.
      week_count. If set, the returned list spans this many weeks
        starting from the week containing as_of_date and walking
        forward. Buckets with no rows still appear so the UI can
        show zero values.
      amount_field. The field on the row used for weekly_total.

    Returns. A list of dicts with week_ending, week_index, rows, and
    weekly_total. Buckets are sorted ascending by week_ending.
    """
    weekday = _normalise_week_ending(week_ending_day)
    anchor = _to_date(as_of_date) or _today()

    buckets: dict[date, list[dict[str, Any]]] = {}
    if isinstance(week_count, int) and week_count > 0:
        start_week_end = _week_end_for(anchor, weekday)
        for i in range(week_count):
            buckets[start_week_end + timedelta(days=7 * i)] = []

    for row in rows:
        d = _to_date(row.get(date_field))
        if d is None:
            continue
        we = _week_end_for(d, weekday)
        if buckets is not None and week_count is not None and we not in buckets:
            # Outside the requested horizon. Skip rather than emit
            # surprise buckets that the UI did not ask for.
            continue
        buckets.setdefault(we, []).append(dict(row))

    out: list[dict[str, Any]] = []
    for idx, we in enumerate(sorted(buckets.keys())):
        bucket_rows = buckets[we]
        total = 0.0
        for r in bucket_rows:
            amt = _to_float(r.get(amount_field))
            if amt is not None:
                total += amt
        out.append(
            {
                "week_ending": _fmt_date(we),
                "week_index": idx,
                "row_count": len(bucket_rows),
                "weekly_total": round(total, 2),
                "rows": bucket_rows,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Payables metrics. Pure compute over normalized AP rows.
# ---------------------------------------------------------------------------


def _metric_envelope(formula: str) -> dict[str, Any]:
    return {
        "value": None,
        "count": 0,
        "excluded_count": 0,
        "formula": formula,
        "warnings": [],
    }


def calculate_payables_metrics(
    ap_rows: Iterable[Mapping[str, Any]],
    as_of_date: Any,
) -> dict[str, Any]:
    """Return the payables timing block the spec asks for.

    ap_rows must already be normalized by _normalize_ap_row. as_of
    drives both the open age formula and the overdue total.
    """
    as_of = _to_date(as_of_date)
    if as_of is None:
        raise ValueError("as_of_date must be a parseable date")

    paid_cycle = _metric_envelope("payment_date - bill_date")
    open_age = _metric_envelope("as_of_date - bill_date")
    until_due = _metric_envelope("due_date - as_of_date")

    open_total = 0.0
    overdue_total = 0.0

    paid_samples: list[int] = []
    open_age_samples: list[int] = []
    until_due_samples: list[int] = []

    paid_missing_bill_date = 0
    paid_missing_payment_date = 0
    open_missing_bill_date = 0
    open_missing_due_date = 0

    for row in ap_rows:
        if row.get("is_paid"):
            bd = _to_date(row.get("bill_date"))
            pd_ = _to_date(row.get("payment_date"))
            if bd is None:
                paid_missing_bill_date += 1
            if pd_ is None:
                paid_missing_payment_date += 1
            if bd is not None and pd_ is not None:
                paid_samples.append((pd_ - bd).days)
            continue
        if row.get("is_open"):
            balance = _to_float(row.get("balance")) or 0.0
            open_total += balance
            bd = _to_date(row.get("bill_date"))
            dd = _to_date(row.get("due_date"))
            if bd is None:
                open_missing_bill_date += 1
            else:
                open_age_samples.append((as_of - bd).days)
            if dd is None:
                open_missing_due_date += 1
            else:
                until_due_samples.append((dd - as_of).days)
                if dd < as_of:
                    overdue_total += balance

    if paid_samples:
        paid_cycle["value"] = round(sum(paid_samples) / len(paid_samples), 2)
        paid_cycle["count"] = len(paid_samples)
    paid_cycle["excluded_count"] = paid_missing_bill_date + paid_missing_payment_date
    if paid_missing_payment_date:
        paid_cycle["warnings"].append(
            f"{paid_missing_payment_date} paid bill rows had no resolvable payment date. "
            "QBO BillPayment entities are not synced into AEGISAXIOM today so payment dates are "
            "only available when LinkedTxn or LastUpdatedTime proxies fire."
        )
    if paid_missing_bill_date:
        paid_cycle["warnings"].append(
            f"{paid_missing_bill_date} paid bill rows had no bill date and were excluded."
        )
    if not paid_samples and (paid_missing_bill_date or paid_missing_payment_date):
        paid_cycle["warnings"].append(
            "No paid bill rows had both a bill date and a payment date. "
            "Use the open bill age metric for current payables timing."
        )

    if open_age_samples:
        open_age["value"] = round(sum(open_age_samples) / len(open_age_samples), 2)
        open_age["count"] = len(open_age_samples)
    open_age["excluded_count"] = open_missing_bill_date
    if open_missing_bill_date:
        open_age["warnings"].append(
            f"{open_missing_bill_date} open bill rows had no bill date and were excluded."
        )

    if until_due_samples:
        until_due["value"] = round(sum(until_due_samples) / len(until_due_samples), 2)
        until_due["count"] = len(until_due_samples)
    until_due["excluded_count"] = open_missing_due_date
    if open_missing_due_date:
        until_due["warnings"].append(
            f"{open_missing_due_date} open bill rows had no due date and were excluded."
        )

    return {
        "as_of": _fmt_date(as_of),
        "open_payables_total": round(open_total, 2),
        "overdue_open_payables_total": round(overdue_total, 2),
        "average_paid_bill_payment_days": paid_cycle,
        "average_open_bill_age_days": open_age,
        "average_days_until_due": until_due,
    }


# ---------------------------------------------------------------------------
# Data health audit. Counts rows by category and surfaces exclusion
# reasons. Read only.
# ---------------------------------------------------------------------------


def audit_qbo_finance_data_health(
    ap_rows_raw: Iterable[Mapping[str, Any]],
    ar_rows_raw: Iterable[Mapping[str, Any]],
    sync_freshness: Mapping[str, Any] | None,
    *,
    stale_threshold_hours: int | None = None,
) -> dict[str, Any]:
    """Inspect raw AP and AR rows plus the sync record and return
    structured counts the assistant uses to explain whether the
    underlying data can be trusted."""

    bill_total = 0
    open_bill = 0
    paid_bill = 0
    bill_missing_bill_date = 0
    open_bill_missing_due_date = 0
    paid_bill_missing_payment_date = 0

    excluded_reasons: dict[str, int] = {}

    for r in ap_rows_raw:
        bill_total += 1
        if _to_date(r.get("txn_date")) is None:
            bill_missing_bill_date += 1
            excluded_reasons["missing_bill_date"] = excluded_reasons.get("missing_bill_date", 0) + 1
        paid = _is_paid(r)
        opn = _is_open(r)
        if paid:
            paid_bill += 1
            if _payment_date(r) is None:
                paid_bill_missing_payment_date += 1
                excluded_reasons["missing_payment_date"] = excluded_reasons.get("missing_payment_date", 0) + 1
        if opn:
            open_bill += 1
            if _to_date(r.get("due_date")) is None:
                open_bill_missing_due_date += 1
                excluded_reasons["missing_due_date_on_open_bill"] = excluded_reasons.get("missing_due_date_on_open_bill", 0) + 1

    invoice_total = 0
    open_invoice = 0
    invoice_missing_invoice_date = 0
    open_invoice_missing_due_date = 0

    for r in ar_rows_raw:
        invoice_total += 1
        if _to_date(r.get("txn_date")) is None:
            invoice_missing_invoice_date += 1
            excluded_reasons["missing_invoice_date"] = excluded_reasons.get("missing_invoice_date", 0) + 1
        if _is_open(r):
            open_invoice += 1
            if _to_date(r.get("due_date")) is None:
                open_invoice_missing_due_date += 1
                excluded_reasons["missing_due_date_on_open_invoice"] = excluded_reasons.get("missing_due_date_on_open_invoice", 0) + 1

    sync = dict(sync_freshness or {})
    if stale_threshold_hours is not None:
        sync["stale_threshold_hours"] = int(stale_threshold_hours)

    return {
        "sources": [
            {"name": AP_COLLECTION, "kind": "ap_bills"},
            {"name": AR_COLLECTION, "kind": "ar_invoices"},
            {"name": "qbo_sync_requests", "kind": "sync_log"},
        ],
        "sync": sync,
        "ap_counts": {
            "total": bill_total,
            "open": open_bill,
            "paid": paid_bill,
            "missing_bill_date": bill_missing_bill_date,
            "open_missing_due_date": open_bill_missing_due_date,
            "paid_missing_payment_date": paid_bill_missing_payment_date,
        },
        "ar_counts": {
            "total": invoice_total,
            "open": open_invoice,
            "missing_invoice_date": invoice_missing_invoice_date,
            "open_missing_due_date": open_invoice_missing_due_date,
        },
        "excluded_rows_by_reason": excluded_reasons,
    }


# ---------------------------------------------------------------------------
# Public entry point. Builds the six section response.
# ---------------------------------------------------------------------------


async def _fetch_rows(db: Any, collection: str, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
    """Pull rows from the named collection. Caller handles tenant
    scoping. Returns a list rather than a cursor so the helper can be
    called from both async route handlers and sync test paths."""
    cursor = db[collection].find({}, {"_id": 0}).limit(limit)
    return await cursor.to_list(length=limit)


def _today() -> date:
    """Indirected so tests can monkeypatch the clock without poking
    every call site."""
    return datetime.now(timezone.utc).date()


async def build_weekly_finance_report(
    db: Any,
    *,
    tenant_id: str | None = None,
    as_of_date: Any = None,
    week_count: int = 8,
    week_ending_day: Any = DEFAULT_WEEK_ENDING_DAY,
    sync_freshness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the weekly finance report.

    db is the AEGISAXIOM Mongo database that holds the QBO lake. The
    caller is responsible for passing the correct database. tenant_id
    is passed through onto the response so the audit trail records
    which tenant context the report ran under. The report itself does
    not filter rows by tenant_id today because the QBO lake is single
    tenant in the current deployment; when multi tenancy lands the
    filter belongs at the caller layer next to auth.

    Stale sync is reported, not blocked. The assistant decides
    whether to surface the warning prominently or fall through.

    week_count clamps to a sane horizon so a request for a thousand
    weeks does not balloon the response.
    """
    as_of = _to_date(as_of_date) or _today()
    if not isinstance(week_count, int) or week_count < 1:
        week_count = 8
    if week_count > 26:
        week_count = 26

    ap_raw = await _fetch_rows(db, AP_COLLECTION)
    ar_raw = await _fetch_rows(db, AR_COLLECTION)

    ap_norm = [_normalize_ap_row(r) for r in ap_raw]
    ar_norm = [_normalize_ar_row(r) for r in ar_raw]

    payables = calculate_payables_metrics(ap_norm, as_of)
    audit = audit_qbo_finance_data_health(ap_raw, ar_raw, sync_freshness)

    # AR weekly plan is keyed on due_date because Finance plans
    # collections against when the customer is expected to pay, not
    # when the invoice was issued.
    ar_weekly = bucket_rows_by_week_ending(
        (r for r in ar_norm if r.get("is_open")),
        date_field="due_date",
        week_ending_day=week_ending_day,
        as_of_date=as_of,
        week_count=week_count,
        amount_field="balance",
    )
    # AP weekly plan keys on due_date for open bills and on
    # payment_date for paid ones, so the plan reflects both upcoming
    # outflows and recent settlements. Two buckets keep the horizon
    # honest.
    ap_open_weekly = bucket_rows_by_week_ending(
        (r for r in ap_norm if r.get("is_open")),
        date_field="due_date",
        week_ending_day=week_ending_day,
        as_of_date=as_of,
        week_count=week_count,
        amount_field="balance",
    )
    ap_paid_weekly = bucket_rows_by_week_ending(
        (r for r in ap_norm if r.get("is_paid") and r.get("payment_date")),
        date_field="payment_date",
        week_ending_day=week_ending_day,
        as_of_date=as_of,
        week_count=week_count,
        amount_field="paid_amount",
    )

    warnings: list[str] = []
    if sync_freshness and sync_freshness.get("warning"):
        warnings.append(str(sync_freshness["warning"]))
    if audit["ap_counts"]["total"] == 0 and audit["ar_counts"]["total"] == 0:
        warnings.append(
            "No AP or AR rows are present in the synced QBO lake. Confirm the QBO sync has run."
        )

    return {
        "as_of": _fmt_date(as_of),
        "tenant_id": tenant_id,
        "week_ending_day": _normalise_week_ending(week_ending_day),
        "week_count": week_count,
        "summary": {
            "as_of": _fmt_date(as_of),
            "qbo_sync_at": (sync_freshness or {}).get("last_successful_sync_at"),
            "is_stale": bool((sync_freshness or {}).get("is_stale", False)),
            "ap_total_bill_count": audit["ap_counts"]["total"],
            "ap_open_count": audit["ap_counts"]["open"],
            "ap_paid_count": audit["ap_counts"]["paid"],
            "ar_total_invoice_count": audit["ar_counts"]["total"],
            "ar_open_count": audit["ar_counts"]["open"],
            "open_payables_total": payables["open_payables_total"],
            "overdue_open_payables_total": payables["overdue_open_payables_total"],
            "payables_timing": {
                "average_paid_bill_payment_days": payables["average_paid_bill_payment_days"],
                "average_open_bill_age_days": payables["average_open_bill_age_days"],
                "average_days_until_due": payables["average_days_until_due"],
            },
            "warnings": warnings,
        },
        "ar_weekly": ar_weekly,
        "ap_weekly": {
            "open": ap_open_weekly,
            "paid": ap_paid_weekly,
        },
        "raw_ar": ar_norm,
        "raw_ap": ap_norm,
        "sync_audit": audit,
        "warnings": warnings,
    }


__all__ = [
    "DEFAULT_WEEK_ENDING_DAY",
    "AP_COLLECTION",
    "AR_COLLECTION",
    "MAX_ROWS",
    "audit_qbo_finance_data_health",
    "bucket_rows_by_week_ending",
    "build_weekly_finance_report",
    "calculate_payables_metrics",
]
