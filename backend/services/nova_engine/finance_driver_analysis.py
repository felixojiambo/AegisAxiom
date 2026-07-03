"""Driver / variance explanation for finance metrics.

Nova needs to answer "why did revenue go up?" with deterministic
month-by-month buckets, top accounts, and a comparison-period variance
— not a free-form LLM commentary. This helper computes all of that
from general_ledger directly. The LLM's job is to format and explain;
this module's job is to compute.

Pure-ish: the only I/O is Mongo reads against the supplied database
handle. No QBO calls, no n8n calls. Reads only ``general_ledger``.

Supported metrics in v1:
  * ``revenue`` — sum of REVENUE_CATEGORIES rows
  * ``expenses`` — sum of every expense category (PS_COGS, FS_COGS,
    OTHER_COGS, PAYROLL, ADMIN_EXPENSES, OTHER_OPEX)
  * ``cogs`` — sum of COGS_CATEGORIES rows
  * ``gross_profit`` — revenue - cogs, using the canonical company-level
    formula in ``services.nova_engine.formulas.compute_gross_profit``

Out of scope for v1 (returned with a warning rather than inventing):
  * ``cash``, ``ar``, ``ap`` — these need bank-balance, AR-aging and
    AP-aging snapshots respectively. Direct the caller to the existing
    snapshot tools (``get_finance_data_status`` / get_open_invoices /
    get_open_bills).
  * Per-customer / per-vendor ranking out of GL rows — the AEGISAXIOM GL
    schema does not reliably carry customer / vendor on a per-row
    basis (those names live in the ``reports_customer_income`` and
    ``reports_vendor_expenses`` QBO reports, surfaced by
    ``get_top_customers_by_income`` / ``get_top_vendors_by_spend``).
    The helper returns ``top_customers: []`` and ``top_vendors: []``
    with a warning so the LLM does not invent names.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Iterable, Optional

from .account_categories import categorize
from .formulas import (
    COGS_CATEGORIES,
    REVENUE_CATEGORIES,
    compute_gross_profit,
    compute_reversal_metadata,
)


logger = logging.getLogger(__name__)


# Categories that count as "an expense" in the OpEx-inclusive sense
# (everything south of the gross-profit line on the P&L). Mirrors the
# expense-bucket grouping used by query_finance_data's metric=expense.
_EXPENSE_CATEGORIES: frozenset[str] = frozenset(
    {
        "PS_COGS",
        "FS_COGS",
        "OTHER_COGS",
        "PAYROLL",
        "ADMIN_EXPENSES",
        "OTHER_OPEX",
    }
)


_SUPPORTED_METRICS: frozenset[str] = frozenset(
    {"revenue", "expenses", "cogs", "gross_profit"}
)

_NOT_SUPPORTED_METRICS: dict[str, str] = {
    "cash": (
        "cash driver analysis is not implemented in this helper — call "
        "get_finance_data_status for current cash balance / sync state "
        "and run_projection for a forward-looking weekly cash flow."
    ),
    "ar": (
        "AR driver analysis is not implemented in this helper — call "
        "get_ar_aging for current AR aging buckets and "
        "get_open_invoices for per-customer AR detail."
    ),
    "ap": (
        "AP driver analysis is not implemented in this helper — call "
        "get_ap_aging for current AP aging buckets and "
        "get_open_bills for per-vendor AP detail."
    ),
}


# ---------------------------------------------------------------------------
# Date / window helpers
# ---------------------------------------------------------------------------


def _parse_date(value: Optional[str]) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _ymd(d: date) -> str:
    return d.isoformat()


def _shift_year(d: date, years: int) -> date:
    """Return ``d`` shifted by ``years`` calendar years. Handles
    Feb 29 by stepping back to Feb 28 — same convention as
    ``calendar``-aware date arithmetic, no calendar import needed."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Feb 29 on a non-leap year — fall back to Feb 28.
        return d.replace(year=d.year + years, day=28)


def _resolve_comparison_window(
    date_from: date,
    date_to: date,
    compare_to: Optional[str],
) -> Optional[tuple[date, date]]:
    """Return (cmp_from, cmp_to) for the requested comparison, or None
    when compare_to is unset or unrecognised.

    Full-calendar-month special case: when the current period is
    exactly a calendar month AND compare_to is ``previous_period``,
    snap the comparison to the FULL previous calendar month rather
    than a same-length rolling window. The user intent for "compare
    May 2099 to the previous period" is "vs April 2099" (a 30-day
    full month), not "vs the 31 days ending April 30". The mode
    resolver downstream then labels this as `previous_month`. For
    non-calendar-month current periods the same-length-rolling
    semantics are preserved (`prior_period_same_length`).
    """
    if compare_to is None:
        return None
    if compare_to == "previous_period":
        if _is_full_calendar_month(date_from, date_to):
            return _previous_calendar_month_window(date_from)
        span = (date_to - date_from).days
        cmp_to = date_from - timedelta(days=1)
        cmp_from = cmp_to - timedelta(days=span)
        return cmp_from, cmp_to
    if compare_to == "same_period_prior_year":
        return _shift_year(date_from, -1), _shift_year(date_to, -1)
    return None


# ---------------------------------------------------------------------------
# Canonical comparison-mode resolution
# ---------------------------------------------------------------------------
#
# The user-facing wording bug: Nova was calling the
# immediately-prior same-length range "April 2026" when it was actually
# something like "Apr 5 → May 5". This block normalises the comparison
# into one of five canonical modes the LLM is required to quote
# verbatim, and detects the special case where the prior-period window
# happens to line up exactly with the previous calendar month so the
# label can be the more specific `previous_month`.

# Display labels paired with each mode so the LLM has a single string
# it can quote without paraphrasing.
_COMPARISON_MODE_LABELS: dict[str, str] = {
    "previous_month": "previous calendar month",
    "prior_period_same_length": "prior same-length period",
    "prior_n_days": "prior N-day period",
    "same_period_prior_year": "same period prior year",
    "custom_range": "custom comparison range",
}


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _is_full_calendar_month(df: date, dt: date) -> bool:
    """True iff [df, dt] inclusive is exactly one calendar month."""
    if df.day != 1:
        return False
    if df.year != dt.year or df.month != dt.month:
        return False
    return dt == _last_day_of_month(df.year, df.month)


def _previous_calendar_month_window(df: date) -> tuple[date, date]:
    """Given the first-of-month start date, return (first, last) of the
    previous calendar month."""
    first = df - timedelta(days=1)  # last day of the previous month
    pcm_start = date(first.year, first.month, 1)
    return pcm_start, first


def _resolve_comparison_mode(
    df: date,
    dt: date,
    cmp_window: Optional[tuple[date, date]],
    compare_to: Optional[str],
) -> dict[str, Any]:
    """Map (current window, comparison window, requested compare_to)
    into the canonical mode + label + n_days for the response."""
    if cmp_window is None:
        return {
            "comparison_mode": None,
            "comparison_label": None,
            "comparison_n_days": None,
        }

    if compare_to == "same_period_prior_year":
        return {
            "comparison_mode": "same_period_prior_year",
            "comparison_label": _COMPARISON_MODE_LABELS["same_period_prior_year"],
            "comparison_n_days": None,
        }

    # Otherwise compare_to is "previous_period" — auto-detect whether
    # the window happens to be exactly the previous calendar month, in
    # which case the more specific `previous_month` label applies.
    if _is_full_calendar_month(df, dt):
        expected = _previous_calendar_month_window(df)
        if cmp_window == expected:
            return {
                "comparison_mode": "previous_month",
                "comparison_label": _COMPARISON_MODE_LABELS["previous_month"],
                "comparison_n_days": None,
            }

    # Default: prior same-length period, with the inclusive day count
    # so the LLM can disclose "prior 31-day period" if the user asks.
    span_inclusive = (dt - df).days + 1
    return {
        "comparison_mode": "prior_period_same_length",
        "comparison_label": _COMPARISON_MODE_LABELS["prior_period_same_length"],
        "comparison_n_days": span_inclusive,
    }


# ---------------------------------------------------------------------------
# Source / scope / formula descriptors
# ---------------------------------------------------------------------------


_METRIC_INCLUDED_CATEGORIES: dict[str, frozenset[str]] = {
    "revenue": REVENUE_CATEGORIES,
    "cogs": COGS_CATEGORIES,
}


def _scope_descriptor_for_metric(metric: str) -> dict[str, Any]:
    """Returns a (source / account_filter_label / included_categories /
    excluded_categories) descriptor the response surfaces under
    ``scope`` so the LLM can quote the revenue-bucket definition
    verbatim instead of inventing one."""
    if metric == "revenue":
        return {
            "source": {
                "collection": "general_ledger",
                "scope": "gl_revenue_categories",
            },
            "account_filter_label": "canonical revenue categories",
            "included_revenue_categories": sorted(REVENUE_CATEGORIES),
            "excluded_categories": [],
        }
    if metric == "expenses":
        return {
            "source": {
                "collection": "general_ledger",
                "scope": "gl_expense_categories",
            },
            "account_filter_label": "canonical expense categories (COGS + OpEx)",
            "included_revenue_categories": [],
            "included_expense_categories": sorted(_EXPENSE_CATEGORIES),
            "excluded_categories": [],
        }
    if metric == "cogs":
        return {
            "source": {
                "collection": "general_ledger",
                "scope": "gl_cogs_categories",
            },
            "account_filter_label": "canonical COGS categories",
            "included_revenue_categories": [],
            "included_cogs_categories": sorted(COGS_CATEGORIES),
            "excluded_categories": [],
        }
    if metric == "gross_profit":
        return {
            "source": {
                "collection": "general_ledger",
                "scope": "gl_revenue_minus_cogs",
            },
            "account_filter_label": (
                "canonical gross-profit scope (revenue minus COGS)"
            ),
            "included_revenue_categories": sorted(REVENUE_CATEGORIES),
            "included_cogs_categories": sorted(COGS_CATEGORIES),
            "excluded_categories": [],
        }
    return {
        "source": {"collection": "general_ledger", "scope": "unknown"},
        "account_filter_label": "unknown metric scope",
        "included_revenue_categories": [],
        "excluded_categories": [],
    }


def _format_variance_percent_display(value: Optional[float]) -> str:
    """Human-readable rendering for ``variance_percent`` so the LLM can
    quote one consistent shape. Returns ``"N/A"`` when the value is
    None (zero comparison total or no comparison)."""
    if value is None:
        return "N/A"
    return f"{value:+.1%}"


def _month_key(date_str: str) -> str:
    """YYYY-MM extracted from a YYYY-MM-DD tx_date. Defensive for
    malformed values — returns the input slice anyway so the caller
    can still bucket non-conforming rows somewhere visible."""
    return date_str[:7] if isinstance(date_str, str) and len(date_str) >= 7 else "?"


# ---------------------------------------------------------------------------
# Mongo aggregation
# ---------------------------------------------------------------------------


async def _fetch_rows(
    db: Any,
    date_from: date,
    date_to: date,
    basis: str,
) -> list[dict]:
    """Pull the in-scope GL rows. Project only the fields the helper
    needs so the response payload is small even on large tenants."""
    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "tx_date": {"$gte": _ymd(date_from), "$lte": _ymd(date_to)},
                "basis": basis,
            }
        },
        {
            "$project": {
                "_id": 0,
                "tx_date": 1,
                "account_name": 1,
                # txn_type is projected so the reversal_summary block
                # can surface transaction_types alongside the signed
                # totals. The field is metadata only and never
                # influences whether a row counts toward revenue. A
                # row whose account maps to revenue is always included
                # regardless of txn_type.
                "txn_type": 1,
                "amount": {"$ifNull": ["$amount", 0]},
            }
        },
    ]
    try:
        cursor = db["general_ledger"].aggregate(pipeline)
        return await cursor.to_list(length=None)
    except Exception as exc:  # noqa: BLE001 — never crash Nova on a query
        logger.warning("analyze_finance_drivers fetch failed: %s", exc)
        return []


def _category_amount(row: dict, included: Iterable[str]) -> float:
    """Return ``amount`` if ``row``'s account_name categorises into one
    of ``included``, else 0.0. Centralised so revenue / expense / cogs
    paths agree on signing and the unknown-category bucket."""
    name = row.get("account_name") or ""
    cat = categorize(name) if name else "UNCATEGORIZED"
    if cat in included:
        try:
            return float(row.get("amount", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _aggregate(
    rows: list[dict],
    included: Iterable[str],
) -> tuple[float, dict[str, float], dict[str, float]]:
    """Walk rows once, returning (total, by_month, by_account).

    Signed sum is preserved. Reversal rows posted to income accounts
    appear as negative amounts and reduce the bucket total exactly as
    the QBO Transaction Report does. The helper never calls abs and
    never filters by txn_type.
    """
    included_set = set(included)
    total = 0.0
    by_month: dict[str, float] = {}
    by_account: dict[str, float] = {}
    for row in rows:
        amount = _category_amount(row, included_set)
        if amount == 0.0:
            continue
        total += amount
        month = _month_key(row.get("tx_date") or "")
        by_month[month] = by_month.get(month, 0.0) + amount
        account = (row.get("account_name") or "").strip() or "(unnamed account)"
        by_account[account] = by_account.get(account, 0.0) + amount
    return total, by_month, by_account


def _reversal_metadata(
    rows: list[dict],
    included: Iterable[str],
) -> dict[str, Any]:
    """Compute reversal coverage stats for the supplied scope.

    A row is reversal like when its signed amount is negative and the
    account maps into the included category set. The result is meant
    to ride alongside the existing summary so a reconciler can see
    how much of the net came from positive activity versus reversals
    without re reading the row list. txn_types is sorted so the
    response is stable.
    """
    # K4. Category filtering and txn type collection stay here because
    # they vary per call site. The pure accumulation moves into the
    # shared compute_reversal_metadata helper in formulas so all three
    # finance surfaces aggregate signed amounts the same way.
    included_set = set(included)
    amounts: list[float] = []
    txn_types: set[str] = set()
    for row in rows:
        name = row.get("account_name") or ""
        cat = categorize(name) if name else "UNCATEGORIZED"
        if cat not in included_set:
            continue
        try:
            amount = float(row.get("amount", 0) or 0)
        except (TypeError, ValueError):
            continue
        amounts.append(amount)
        tt = row.get("txn_type")
        if tt is not None:
            txn_types.add(str(tt))
    meta = compute_reversal_metadata(amounts)
    return {
        "signed_total": meta["signed_total"],
        "positive_total": meta["positive_total"],
        "negative_total": meta["negative_total"],
        "positive_row_count": meta["positive_row_count"],
        "negative_row_count": meta["negative_row_count"],
        "transaction_types": sorted(txn_types),
        "reversal_note": (
            "Negative rows posted to income accounts are included as "
            "signed reductions to revenue."
        ),
    }


def _aggregate_by_category(
    rows: list[dict],
    included: Iterable[str],
) -> dict[str, float]:
    """Sum amounts per category for the supplied set. Used for the
    canonical gross-profit formula so the helper can pass per-category
    totals into ``formulas.compute_gross_profit`` rather than a
    summed scalar."""
    included_set = set(included)
    out: dict[str, float] = {}
    for row in rows:
        name = row.get("account_name") or ""
        cat = categorize(name) if name else "UNCATEGORIZED"
        if cat not in included_set:
            continue
        try:
            amount = float(row.get("amount", 0) or 0)
        except (TypeError, ValueError):
            continue
        out[cat] = out.get(cat, 0.0) + amount
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def analyze_finance_drivers(
    db: Any,
    metric: str,
    date_from: str,
    date_to: str,
    basis: str = "accrual",
    compare_to: Optional[str] = "previous_period",
    group_by: Optional[list[str]] = None,
    limit: int = 10,
    filters: Optional[dict] = None,
) -> dict[str, Any]:
    """Driver / variance breakdown for a single metric over a window.

    See module docstring for supported metrics and the reasons the
    others return warnings rather than inventing numbers.
    """
    # ---- Validation --------------------------------------------------
    if metric in _NOT_SUPPORTED_METRICS:
        return {
            "metric": metric,
            "error": "metric_not_supported_in_driver_analysis",
            "detail": _NOT_SUPPORTED_METRICS[metric],
        }
    if metric not in _SUPPORTED_METRICS:
        return {
            "metric": metric,
            "error": "unknown_metric",
            "detail": (
                f"metric={metric!r} is not supported. Use one of "
                f"{sorted(_SUPPORTED_METRICS)} or one of the known "
                f"unsupported labels {sorted(_NOT_SUPPORTED_METRICS)} "
                "to receive a routing hint."
            ),
        }
    if basis not in ("accrual", "cash"):
        return {
            "metric": metric,
            "error": "invalid_basis",
            "detail": f"basis must be 'accrual' or 'cash', got {basis!r}",
        }

    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if df is None or dt is None:
        return {
            "metric": metric,
            "error": "invalid_date_range",
            "detail": (
                "date_from and date_to are required and must both be "
                "YYYY-MM-DD."
            ),
        }
    if df > dt:
        return {
            "metric": metric,
            "error": "invalid_date_range",
            "detail": "date_from must be on or before date_to.",
        }
    if compare_to is not None and compare_to not in (
        "previous_period",
        "same_period_prior_year",
    ):
        return {
            "metric": metric,
            "error": "invalid_compare_to",
            "detail": (
                f"compare_to must be 'previous_period', "
                f"'same_period_prior_year', or null. Got {compare_to!r}."
            ),
        }

    warnings: list[str] = []

    # The v1 spec accepts ``group_by`` and ``filters`` but the helper
    # only honours the canonical month + account axes today. Surface
    # any other request as a warning rather than silently dropping it.
    if group_by:
        unknown_axes = [
            axis for axis in group_by if axis not in ("month", "account")
        ]
        if unknown_axes:
            warnings.append(
                "group_by includes axes the driver helper does not yet "
                f"emit: {sorted(unknown_axes)}. The response carries the "
                "monthly + top_accounts breakdown only."
            )
    if filters:
        warnings.append(
            "filters are recognized but the driver helper does not yet "
            "narrow the scope by them. Result reflects the full scope "
            "of the date / basis window."
        )

    # ---- Comparison window ------------------------------------------
    cmp_window = _resolve_comparison_window(df, dt, compare_to)
    cmp_payload: dict[str, Any] = {
        "compare_to": compare_to,
        "comparison_date_from": _ymd(cmp_window[0]) if cmp_window else None,
        "comparison_date_to": _ymd(cmp_window[1]) if cmp_window else None,
    }

    # ---- Fetch rows --------------------------------------------------
    current_rows = await _fetch_rows(db, df, dt, basis)
    comparison_rows: list[dict] = []
    if cmp_window is not None:
        comparison_rows = await _fetch_rows(db, cmp_window[0], cmp_window[1], basis)

    # ---- Compute totals + monthly + top_accounts --------------------
    if metric == "revenue":
        included = REVENUE_CATEGORIES
        current_total, current_monthly, current_by_account = _aggregate(current_rows, included)
        comparison_total, comparison_monthly, _ = _aggregate(comparison_rows, included)
    elif metric == "expenses":
        included = _EXPENSE_CATEGORIES
        current_total, current_monthly, current_by_account = _aggregate(current_rows, included)
        comparison_total, comparison_monthly, _ = _aggregate(comparison_rows, included)
    elif metric == "cogs":
        included = COGS_CATEGORIES
        current_total, current_monthly, current_by_account = _aggregate(current_rows, included)
        comparison_total, comparison_monthly, _ = _aggregate(comparison_rows, included)
    else:
        # gross_profit — derive from the canonical formula so coverage
        # stays in lockstep with query_finance_data and finance_kpis.py.
        current_revenue_by_cat = _aggregate_by_category(current_rows, REVENUE_CATEGORIES)
        current_cogs_by_cat = _aggregate_by_category(current_rows, COGS_CATEGORIES)
        gp_now = compute_gross_profit(current_revenue_by_cat, current_cogs_by_cat)
        current_total = gp_now["gross_profit"]

        # Monthly gross profit: revenue per month - cogs per month
        _, current_rev_monthly, _ = _aggregate(current_rows, REVENUE_CATEGORIES)
        _, current_cogs_monthly, _ = _aggregate(current_rows, COGS_CATEGORIES)
        all_months = set(current_rev_monthly) | set(current_cogs_monthly)
        current_monthly = {
            m: current_rev_monthly.get(m, 0.0) - current_cogs_monthly.get(m, 0.0)
            for m in all_months
        }

        # Top accounts: revenue accounts ranked positive, cogs accounts
        # ranked negative (cost contribution to GP). Both go into the
        # by_account dict so the LLM sees the actual P&L drivers.
        _, _, rev_accounts = _aggregate(current_rows, REVENUE_CATEGORIES)
        _, _, cogs_accounts = _aggregate(current_rows, COGS_CATEGORIES)
        current_by_account = dict(rev_accounts)
        for account, amount in cogs_accounts.items():
            current_by_account[account] = current_by_account.get(account, 0.0) - amount

        if comparison_rows:
            comparison_revenue_by_cat = _aggregate_by_category(
                comparison_rows, REVENUE_CATEGORIES
            )
            comparison_cogs_by_cat = _aggregate_by_category(
                comparison_rows, COGS_CATEGORIES
            )
            gp_then = compute_gross_profit(comparison_revenue_by_cat, comparison_cogs_by_cat)
            comparison_total = gp_then["gross_profit"]
            _, comparison_rev_monthly, _ = _aggregate(comparison_rows, REVENUE_CATEGORIES)
            _, comparison_cogs_monthly, _ = _aggregate(comparison_rows, COGS_CATEGORIES)
            cmp_months = set(comparison_rev_monthly) | set(comparison_cogs_monthly)
            comparison_monthly = {
                m: comparison_rev_monthly.get(m, 0.0)
                - comparison_cogs_monthly.get(m, 0.0)
                for m in cmp_months
            }
        else:
            comparison_total = 0.0
            comparison_monthly = {}

    # ---- Variance ---------------------------------------------------
    # Canonical formula per the readiness spec:
    #   variance_amount  = current_total - comparison_total
    #   variance_percent = variance_amount / comparison_total
    # The earlier abs(comparison_total) denominator silently flipped
    # the sign of the percent when comparison_total was negative
    # (legitimate refund-heavy month or loss-making prior period).
    # Drop abs() — the percent now carries the correct sign.
    if cmp_window is not None:
        variance_amount: Optional[float] = current_total - comparison_total
        if comparison_total != 0:
            variance_pct: Optional[float] = (
                (current_total - comparison_total) / comparison_total
            )
        else:
            variance_pct = None
    else:
        variance_amount = None
        variance_pct = None
        comparison_monthly = {}

    summary = {
        # Canonical names from the readiness spec.
        "current_period_total": current_total,
        "comparison_period_total": (
            comparison_total if cmp_window is not None else None
        ),
        "variance_amount": variance_amount,
        "variance_percent": variance_pct,
        "variance_percent_display": _format_variance_percent_display(variance_pct),
        # Backward-compatibility aliases — older callers / tests still
        # read these. Kept as exact mirrors of the canonical fields.
        "current_total": current_total,
        "comparison_total": (
            comparison_total if cmp_window is not None else None
        ),
        "variance_pct": variance_pct,
    }

    # ---- Monthly buckets --------------------------------------------
    monthly_keys = sorted(set(current_monthly) | set(comparison_monthly))
    monthly: list[dict[str, Any]] = []
    for ym in monthly_keys:
        cur = current_monthly.get(ym, 0.0)
        cmp_amt = comparison_monthly.get(ym) if cmp_window is not None else None
        monthly.append(
            {
                "month": ym,
                "amount": cur,
                "comparison_amount": cmp_amt,
                "variance_amount": (cur - cmp_amt) if cmp_amt is not None else None,
            }
        )

    # ---- Top accounts -----------------------------------------------
    try:
        clamped_limit = max(int(limit), 1)
    except (TypeError, ValueError):
        clamped_limit = 10
    ranked_accounts = sorted(
        current_by_account.items(),
        key=lambda kv: abs(kv[1]),
        reverse=True,
    )[:clamped_limit]
    total_abs = sum(abs(v) for v in current_by_account.values()) or 1.0
    top_accounts = [
        {
            "account_name": name,
            "category": (categorize(name) if name else "UNCATEGORIZED"),
            "amount": amount,
            "share_pct": abs(amount) / total_abs,
        }
        for name, amount in ranked_accounts
    ]

    # ---- Top customers / vendors ------------------------------------
    # GL rows do not reliably carry per-row customer/vendor labels in
    # the AEGISAXIOM schema; the canonical names live in the QBO
    # reports_customer_income / reports_vendor_expenses collections,
    # surfaced by get_top_customers_by_income / get_top_vendors_by_spend.
    top_customers: list[dict[str, Any]] = []
    top_vendors: list[dict[str, Any]] = []
    warnings.append(
        "Per-customer and per-vendor ranking are not derived from the "
        "general_ledger row schema. For per-customer income, call "
        "get_top_customers_by_income; for per-vendor spend, call "
        "get_top_vendors_by_spend."
    )

    # ---- Drivers ----------------------------------------------------
    drivers: list[dict[str, Any]] = []
    # Top contributing accounts framed as drivers — natural-language
    # explanation comes from the deterministic share/category info.
    for entry in top_accounts[:5]:
        drivers.append(
            {
                "label": entry["account_name"],
                "amount": entry["amount"],
                "explanation": (
                    f"{entry['account_name']} ({entry['category']}) "
                    f"contributes {entry['share_pct']:.1%} of the "
                    f"absolute {metric} for this window."
                ),
            }
        )
    # Comparison-period delta as an additional driver framing, when
    # available, so the LLM can quote the variance verbatim instead of
    # paraphrasing.
    if cmp_window is not None and variance_amount is not None:
        if variance_pct is None:
            pct_text = "(comparison period total is 0; pct undefined)"
        else:
            pct_text = f"({variance_pct:+.1%} vs comparison period)"
        drivers.append(
            {
                "label": "period_variance",
                "amount": variance_amount,
                "explanation": (
                    f"{metric} moved by {variance_amount:+.2f} "
                    f"{pct_text}."
                ),
            }
        )

    if not current_rows:
        warnings.append(
            "No GL rows match the supplied scope; the helper cannot "
            "compute drivers for an empty window."
        )

    # ---- Canonical scope block --------------------------------------
    # Single source of truth the LLM is required to quote verbatim:
    # current/comparison dates, comparison_mode + label, basis,
    # source collection, account-filter label, included revenue
    # categories. The previous response left labelling to the LLM,
    # which produced "April 2026" / "previous month" drift — see the
    # comparison-mode resolver above.
    mode_block = _resolve_comparison_mode(df, dt, cmp_window, compare_to)
    scope_descriptor = _scope_descriptor_for_metric(metric)
    scope_block: dict[str, Any] = {
        "metric": metric,
        "basis": basis,
        "current_period_start": _ymd(df),
        "current_period_end": _ymd(dt),
        "comparison_period_start": (
            _ymd(cmp_window[0]) if cmp_window is not None else None
        ),
        "comparison_period_end": (
            _ymd(cmp_window[1]) if cmp_window is not None else None
        ),
        **mode_block,
        **scope_descriptor,
    }

    formula_block = {
        "variance_amount": "current_period_total - comparison_period_total",
        "variance_percent": "variance_amount / comparison_period_total",
    }

    response: dict[str, Any] = {
        "metric": metric,
        "date_from": date_from,
        "date_to": date_to,
        "basis": basis,
        "comparison": cmp_payload,
        "scope": scope_block,
        "formula": formula_block,
        "summary": summary,
        "monthly": monthly,
        "top_accounts": top_accounts,
        "top_customers": top_customers,
        "top_vendors": top_vendors,
        "drivers": drivers,
        "warnings": warnings,
    }

    # Reversal metadata. Surface positive_total, negative_total and
    # row counts so a reconciler can verify the signed sum matches
    # the QBO Transaction Report without re reading every row. Wired
    # for revenue and gross_profit since those are the metrics where
    # income account reversals materially shift the net.
    if metric == "revenue":
        response["reversal_summary"] = _reversal_metadata(
            current_rows, REVENUE_CATEGORIES
        )
    elif metric == "gross_profit":
        response["reversal_summary"] = _reversal_metadata(
            current_rows, REVENUE_CATEGORIES
        )

    return response


__all__ = [
    "analyze_finance_drivers",
]
