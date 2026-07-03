"""Mongo data layer for the Nova engine.

This is the ONLY module in the engine package that touches Mongo or any
external storage. Every other engine module operates on Pydantic models
that data_loader has already assembled.

Default: QBO + AEGISAXIOM app data live in the same DB (DB_NAME). QBO_DB and
QBO_MONGO_URL are optional overrides for tests and future multi-cluster
setups. See db.get_qbo_db() for resolution order.

Pattern (locked in design conversation):
  - Mongo: narrow + group. Filter by date range + basis, project only
    needed fields, group by (month, account_id, account_name).
  - Python: categorize via account_categories.categorize(), apply sign
    handling, roll into PS/FS/admin buckets.

  This keeps account_categories.py as the single source of truth for
  category mapping while letting Mongo do the cheap aggregation work it
  is good at.

Wholesale identification: NOT done in v1. Asad's call: QBO cannot
reliably identify wholesale invoices. Wholesale revenue flows through
RecurringRevenueAssumption (manual input) or stays out of v1.

History window: comes from data_freshness.last_n_complete_months() —
returns the 3 most recent COMPLETE months strictly before
projection_start_month, oldest first.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from ._gl_helpers import (
    _GL_QUERY_LIMIT,
    _build_history_pipeline,
    _safe_float,
    _start_of_next_month,
)
from .account_categories import categorize
from .data_freshness import last_n_complete_months
from .db import get_app_db, get_qbo_db
from .models import InsufficientHistoryError, ProjectionInputs
from .wholesale_detection import (
    compute_auto_wholesale_assumption,
    detect_wholesale_history_from_gl,
    is_auto_detected,
)


logger = logging.getLogger(__name__)


# _GL_QUERY_LIMIT, _build_history_pipeline, and _start_of_next_month are
# re-exported from ._gl_helpers above so existing callers in this module
# and in test_nova_data_loader continue to work without churn. The
# helpers live there (rather than here) to keep this module out of the
# wholesale_detection -> data_loader cycle, since wholesale_detection
# also needs them.


# ---------------------------------------------------------------------------
# Environment / client
# ---------------------------------------------------------------------------

def get_mongo_client() -> AsyncIOMotorClient:
    """DEPRECATED. Use services.nova_engine.db helpers directly.

    WARNING: Do NOT call .close() on the returned client. It is the
    process-wide global client; closing it breaks all subsequent callers.

    This helper is kept for backwards compatibility. Older versions
    constructed a fresh client per call, which leaked connections under
    load. New callers should use the global DB helpers directly.

    Note: get_qbo_db() returns an AsyncIOMotorDatabase, not a Client.
    Migration:
      - client[get_qbo_db_name()]  -> get_qbo_db()       (drop the subscript)
      - client[get_app_db_name()]  -> get_app_db()       (drop the subscript)
      - need both DBs              -> get_both_dbs()
      - need the raw client        -> database.get_client()
    """
    return get_qbo_db().client


def get_qbo_db_name() -> str:
    """Mongo database holding QBO financial data.

    Defaults to DB_NAME (same DB as the rest of AEGISAXIOM — QBO collections are
    co-located in production). Set QBO_DB to override for testing or future
    multi-cluster setups.
    """
    return os.environ.get("QBO_DB") or get_app_db_name()


def get_app_db_name() -> str:
    """Mongo database holding AEGISAXIOM application data. Defaults to 'aegisaxiom_crm'."""
    return os.environ.get("DB_NAME", "aegisaxiom_crm")


# ---------------------------------------------------------------------------
# Latest tx_date
# ---------------------------------------------------------------------------

async def get_latest_tx_date(qbo_db: AsyncIOMotorDatabase) -> date:
    """Return the maximum tx_date in the general_ledger collection.

    Used by the engine to detect incomplete months. Raises ValueError if
    general_ledger is empty.
    """
    pipeline = [
        {"$group": {"_id": None, "max_date": {"$max": "$tx_date"}}},
    ]
    cursor = qbo_db.general_ledger.aggregate(pipeline)
    docs = await cursor.to_list(length=1)
    if not docs:
        raise ValueError(
            "general_ledger is empty — no GL rows. "
            "Cannot determine latest tx_date."
        )
    return _coerce_to_date(docs[0]["max_date"])


def _coerce_to_date(value: Any) -> date:
    """Normalize a Mongo tx_date value (datetime, date, or str) to date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    raise TypeError(
        f"Cannot coerce tx_date value of type {type(value).__name__} to date: {value!r}"
    )


# _build_history_pipeline and _start_of_next_month live in
# ._gl_helpers; re-exported above for the in-file callers below and
# for tests that import them by name.


def _prior_year(month: str) -> str:
    """Given 'YYYY-MM', return '(YYYY-1)-MM'."""
    year_str, mo_str = month.split("-")
    return f"{int(year_str) - 1}-{mo_str}"


def _next_n_months(start_month: str, n: int) -> list[str]:
    """Given 'YYYY-MM' and n, return n consecutive months starting AT start.

    e.g. _next_n_months("YYYY-05", 4) ==
         ["YYYY-05", "YYYY-06", "YYYY-07", "YYYY-08"]
    """
    year_str, mo_str = start_month.split("-")
    year = int(year_str)
    mo = int(mo_str)
    result: list[str] = []
    for _ in range(n):
        result.append(f"{year}-{mo:02d}")
        mo += 1
        if mo > 12:
            mo = 1
            year += 1
    return result


# ---------------------------------------------------------------------------
# Revenue history
# ---------------------------------------------------------------------------

async def load_revenue_history(
    qbo_db: AsyncIOMotorDatabase,
    history_months: list[str],
    basis: str = "accrual",
) -> dict[str, list[float]]:
    """Sum revenue per (PS, FS, OTHER) per month over the history window.

    Returns:
        {"PS_REVENUE":   [m1, m2, m3],
         "FS_REVENUE":   [m1, m2, m3],
         "OTHER_REVENUE":[m1, m2, m3]}
        Months in returned lists match history_months order.

    OTHER_REVENUE captures revenue rows that categorize() routes to the
    OTHER_REVENUE bucket (legacy QBO names without a 5-digit code that
    contain "INCOME"). MRR and WHOLESALE_REVENUE are still excluded
    here — MRR has its own prior-year-same-month loader
    (load_mrr_history) and wholesale flows through manual recurring
    assumptions per Asad's call.
    """
    if not history_months:
        return {"PS_REVENUE": [], "FS_REVENUE": [], "OTHER_REVENUE": []}

    pipeline = _build_history_pipeline(history_months, basis)
    rows = await qbo_db.general_ledger.aggregate(pipeline).to_list(
        length=_GL_QUERY_LIMIT
    )
    if len(rows) >= _GL_QUERY_LIMIT:
        logger.warning(
            "GL aggregation in load_revenue_history hit length cap of "
            "%d rows. Result may be truncated. If this is expected for "
            "your CoA size, raise _GL_QUERY_LIMIT in data_loader.py.",
            _GL_QUERY_LIMIT,
        )

    buckets: dict[str, dict[str, float]] = {
        m: {"PS_REVENUE": 0.0, "FS_REVENUE": 0.0, "OTHER_REVENUE": 0.0}
        for m in history_months
    }

    for row in rows:
        month = row["_id"]["month"]
        account_name = row["_id"]["account_name"]
        amount = row.get("amount", 0.0)

        if month not in buckets:
            continue

        category = categorize(account_name)
        if category not in ("PS_REVENUE", "FS_REVENUE", "OTHER_REVENUE"):
            # WHOLESALE_REVENUE intentionally skipped — Asad's call: QBO cannot
            # reliably identify wholesale invoices at the customer level.
            # Wholesale revenue flows through RecurringRevenueAssumption
            # (auto-detected via wholesale_detection.py from GL 50400 prefix
            # when the signal is consistent). MRR is loaded separately by
            # load_mrr_history from prior-year same-month rows.
            continue

        # GL `amount` is stored as a natural-balance signed value: positive
        # for normal direction (credit on revenue accounts, debit on COGS /
        # expense accounts), negative for reversals. Summing raw `amount`
        # on revenue accounts therefore yields a positive total directly —
        # no sign flip required.
        buckets[month][category] += amount

    return {
        "PS_REVENUE":    [buckets[m]["PS_REVENUE"]    for m in history_months],
        "FS_REVENUE":    [buckets[m]["FS_REVENUE"]    for m in history_months],
        "OTHER_REVENUE": [buckets[m]["OTHER_REVENUE"] for m in history_months],
    }


# ---------------------------------------------------------------------------
# Gross profit history
# ---------------------------------------------------------------------------

async def load_gross_profit_history(
    qbo_db: AsyncIOMotorDatabase,
    history_months: list[str],
    basis: str = "accrual",
) -> dict[str, list[float]]:
    """Per-service-line gross profit history (PS_GP and FS_GP).

    PS_GP = PS_REVENUE - PS_COGS, per month.
    FS_GP = FS_REVENUE - FS_COGS, per month.

    NOT a company-level gross-profit number. The projection engine
    consumes PS_GP and FS_GP separately to model each service line's
    forward gross-profit ratio (see ProjectionInputs.ps_gross_profit_*
    and .fs_gross_profit_*). Treating PS_GP + FS_GP as company gross
    profit would silently exclude OTHER_COGS, which is part of
    company-level Total COGS in the validated QBO P&L baseline
    (routes/finance_kpis.py) and in the canonical company-level
    formula (services/nova_engine/formulas.compute_gross_profit /
    REVENUE_CATEGORIES / COGS_CATEGORIES).

    For company-level gross profit / margin, use the
    query_finance_data tool with metric=gross_profit /
    gross_profit_margin (which sums all canonical revenue and COGS
    categories) or the QBO P&L summary served by
    routes/finance_kpis.py.

    Also returns OTHER_COGS_TOTAL per month so callers can detect
    whether OTHER_COGS activity exists in the history window — a
    non-zero value is a signal that PS_GP + FS_GP will diverge from
    company-level gross profit.

    Returns:
        {
          "PS_GP": [m1, m2, m3],
          "FS_GP": [m1, m2, m3],
          "OTHER_COGS_TOTAL": [m1, m2, m3],
        }
    """
    if not history_months:
        return {"PS_GP": [], "FS_GP": [], "OTHER_COGS_TOTAL": []}

    pipeline = _build_history_pipeline(history_months, basis)
    rows = await qbo_db.general_ledger.aggregate(pipeline).to_list(
        length=_GL_QUERY_LIMIT
    )
    if len(rows) >= _GL_QUERY_LIMIT:
        logger.warning(
            "GL aggregation in load_gross_profit_history hit length cap "
            "of %d rows. Result may be truncated. If this is expected "
            "for your CoA size, raise _GL_QUERY_LIMIT in data_loader.py.",
            _GL_QUERY_LIMIT,
        )

    revenue: dict[str, dict[str, float]] = {
        m: {"PS": 0.0, "FS": 0.0} for m in history_months
    }
    cogs: dict[str, dict[str, float]] = {
        m: {"PS": 0.0, "FS": 0.0, "OTHER": 0.0} for m in history_months
    }

    for row in rows:
        month = row["_id"]["month"]
        account_name = row["_id"]["account_name"]
        amount = row.get("amount", 0.0)

        if month not in revenue:
            continue

        category = categorize(account_name)

        if category == "PS_REVENUE":
            # `amount` is natural-balance: positive on revenue accounts, no flip.
            revenue[month]["PS"] += amount
        elif category == "FS_REVENUE":
            revenue[month]["FS"] += amount
        elif category == "PS_COGS":
            # COGS is debit-balance — natural-balance positive on debit, no flip.
            cogs[month]["PS"] += amount
        elif category == "FS_COGS":
            cogs[month]["FS"] += amount
        elif category == "OTHER_COGS":
            # Tracked but NOT folded into PS_GP / FS_GP — surfaced as
            # OTHER_COGS_TOTAL so callers can detect that PS+FS GP
            # diverges from company-level GP for this history window.
            cogs[month]["OTHER"] += amount

    return {
        "PS_GP": [revenue[m]["PS"] - cogs[m]["PS"] for m in history_months],
        "FS_GP": [revenue[m]["FS"] - cogs[m]["FS"] for m in history_months],
        "OTHER_COGS_TOTAL": [cogs[m]["OTHER"] for m in history_months],
    }


# ---------------------------------------------------------------------------
# MRR (prior-year same-month lookup)
# ---------------------------------------------------------------------------

async def load_mrr_history(
    qbo_db: AsyncIOMotorDatabase,
    forecast_months: list[str],
    basis: str = "accrual",
) -> dict[str, float]:
    """Prior-year same-month MRR for each forecast month.

    Looks up GL rows categorized as MRR (account-prefix 50300,
    "Other Income:Contract Commission Income") in the same calendar
    month one year before each forecast month, sums them, and returns
    a dict keyed by FORECAST month -> prior-year MRR total.

    Example: forecast_months=["YYYY-05", "YYYY-06"] queries the GL
    for rows in the same months of the prior year; the returned
    dict is keyed by the forecast months with prior-year sums.

    Returns an empty dict for empty forecast_months input. A tenant
    whose GL has no 50300 activity in the relevant prior-year months
    also gets an empty dict (or a dict with zero values for months
    where activity simply rolled to zero) - that is a meaningful
    signal, not an error. The engine's _resolve_mrr_base raises
    InsufficientHistoryError when both base and manual MRR are
    missing for a forecast month, surfacing the gap to the caller.

    Sign convention: AEGISAXIOM GL stores `amount` as natural-balance signed —
    positive in the normal direction of the account (credit on revenue,
    debit on expense), negative for reversals. Summing `amount` on a
    revenue account therefore yields a positive total directly. Matches
    load_revenue_history.
    """
    if not forecast_months:
        return {}

    # Map prior-year months back to the forecast months they cover.
    # _build_history_pipeline filters by month range, so we feed it the
    # prior-year months and convert the results back at row time.
    prior_to_forecast: dict[str, str] = {
        _prior_year(fm): fm for fm in forecast_months
    }
    prior_year_months = sorted(prior_to_forecast.keys())

    # Scope at Mongo level so non-MRR rows don't consume the
    # GL_QUERY_LIMIT budget and silently truncate the MRR total.
    # Source of truth for the prefix is account_categories.CATEGORY_RULES
    # (account_categories.py); the Python-side categorize() check below
    # stays as a defensive belt for the case where account_id and
    # account_name diverge or the rule shape changes.
    pipeline = _build_history_pipeline(
        prior_year_months, basis, account_id_prefix="50300",
    )
    rows = await qbo_db.general_ledger.aggregate(pipeline).to_list(
        length=_GL_QUERY_LIMIT
    )
    if len(rows) >= _GL_QUERY_LIMIT:
        logger.warning(
            "GL aggregation in load_mrr_history hit length cap of "
            "%d rows. Result may be truncated. If this is expected for "
            "your CoA size, raise _GL_QUERY_LIMIT in data_loader.py.",
            _GL_QUERY_LIMIT,
        )

    totals: dict[str, float] = {}
    for row in rows:
        prior_month = row["_id"]["month"]
        forecast_month = prior_to_forecast.get(prior_month)
        if forecast_month is None:
            continue
        account_name = row["_id"]["account_name"]
        if categorize(account_name) != "MRR":
            continue
        amount = row.get("amount", 0.0)
        # `amount` is natural-balance: positive on revenue accounts, no flip.
        totals[forecast_month] = totals.get(forecast_month, 0.0) + amount

    return totals


# ---------------------------------------------------------------------------
# MRR baseline with historical run-rate fallback
# ---------------------------------------------------------------------------
#
# Asad confirmed account code 50300 IS the right MRR account ("Other
# Income:Contract Commission Income"). Even so, some tenants do not
# have prior-year same-month activity on 50300 — early-stage tenants
# who started booking contract commission inside the past 12 months,
# or seasonal accounts whose comparator month happens to be a zero.
#
# The previous behaviour was to surface MRR_MAPPING_UNAVAILABLE the
# moment prior-year same-month came back empty, which is wrong: the
# tenant DOES have usable 50300 history, just not on the comparator
# month the engine was looking at.
#
# This helper tries prior-year same-month first; for forecast months
# without prior-year coverage it falls back to a run-rate computed
# from the 3 most-recent calendar months with non-zero 50300
# activity (search window capped at 24 months back so a tenant with
# a single ancient datapoint doesn't anchor the run-rate forever).
# It returns a metadata dict the chat tool layer surfaces verbatim
# so a reviewer can audit which source fed each forecast month.

# How many recent active months feed the run-rate fallback. Matches
# the engine's existing 3-month convention for PS/FS revenue —
# changing this here without also changing the engine's convention
# would silently change scenario semantics.
_MRR_FALLBACK_RUN_RATE_MONTHS = 3

# Hard cap on the lookback window for the fallback search. If 50300
# has nothing in the last 24 months, treating any older datapoint as
# a current run-rate would be misleading — the helper returns
# ``no_history`` so the chat tool can ask the user for a manual
# assumption instead of fabricating one.
_MRR_FALLBACK_LOOKBACK_MONTHS = 24

# Public label the chat tool quotes verbatim to the user. The
# wording is deliberate: this is a RUN-RATE estimate, not a fixed
# contracted amount. Asad and Davies asked for the distinction to
# stay visible end-to-end.
MRR_FALLBACK_METHOD_LABEL = (
    "historical contract commission run-rate "
    "(avg of last 3 months with 50300 activity)"
)


def _months_window_back(reference_month: str, count: int) -> list[str]:
    """Return ``count`` YYYY-MM strings strictly before ``reference_month``,
    newest-first. Mirrors the previous-month walk in
    ``last_n_complete_months`` but does not depend on tx_date freshness —
    the fallback search is purely a calendar walk; per-month activity
    is checked against the Mongo result."""
    year_str, mo_str = reference_month.split("-")
    year = int(year_str)
    mo = int(mo_str)
    result: list[str] = []
    for _ in range(count):
        # Step backwards one month, then record. The first recorded
        # month is the one immediately BEFORE reference_month.
        mo -= 1
        if mo == 0:
            mo = 12
            year -= 1
        result.append(f"{year}-{mo:02d}")
    return result


async def _fetch_mrr_rows_for_window(
    qbo_db: AsyncIOMotorDatabase,
    months: list[str],
    basis: str,
    account_prefix: str = "50300",
) -> list[dict]:
    """Run the configured-prefix history pipeline for an arbitrary
    list of YYYY-MM months and return the raw aggregated rows.
    Centralised so the prior-year and fallback paths share one
    $match shape and one GL_QUERY_LIMIT guard.

    ``account_prefix`` defaults to the product's existing 50300
    mapping but can be overridden at runtime by a caller who knows
    a different code carries MRR in their CoA. Callers thread the
    user-supplied prefix through here without hardcoding it at any
    intermediate layer."""
    if not months:
        return []
    pipeline = _build_history_pipeline(
        sorted(months), basis, account_id_prefix=account_prefix,
    )
    rows = await qbo_db.general_ledger.aggregate(pipeline).to_list(
        length=_GL_QUERY_LIMIT,
    )
    if len(rows) >= _GL_QUERY_LIMIT:
        logger.warning(
            "GL aggregation in _fetch_mrr_rows_for_window hit length "
            "cap of %d rows. Result may be truncated. Raise "
            "_GL_QUERY_LIMIT if this is expected for the CoA size.",
            _GL_QUERY_LIMIT,
        )
    return rows


def _sum_mrr_rows_by_month(rows: list[dict]) -> dict[str, float]:
    """Reduce raw 50300-prefix aggregation rows to {YYYY-MM: total}.

    Applies the same Python-side categorize() belt as
    load_mrr_history so a future tenant whose 50300 prefix happens to
    collide with a non-MRR account (vanishingly unlikely given the
    CoA conventions Asad set, but the belt is here to make that
    obvious if it ever happens) does not silently leak into the
    fallback total."""
    totals: dict[str, float] = {}
    for row in rows:
        account_name = row["_id"].get("account_name") or ""
        if categorize(account_name) != "MRR":
            continue
        month = row["_id"]["month"]
        amount = row.get("amount", 0.0)
        totals[month] = totals.get(month, 0.0) + amount
    return totals


def _build_account_filter(account_prefix: str = "50300") -> dict[str, Any]:
    """Stable shape the chat tool surfaces verbatim. The key fact a
    reviewer needs is *which prefix the loader used*; passing a
    runtime override surfaces in this block automatically so a
    reviewer can confirm the user-supplied prefix won out over the
    50300 default."""
    return {
        "code_prefix": account_prefix,
        "category": "MRR",
        "account_label": (
            "Other Income:Contract Commission Income"
            if account_prefix == "50300"
            else f"runtime-supplied prefix {account_prefix}"
        ),
    }


async def load_mrr_baseline_with_fallback(
    qbo_db: AsyncIOMotorDatabase,
    forecast_months: list[str],
    *,
    basis: str = "accrual",
    reference_month: str | None = None,
    account_prefix: str = "50300",
    use_fallback: bool = True,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Resolve an MRR baseline for each forecast month, with a
    deterministic historical-run-rate fallback when prior-year
    same-month is missing.

    Algorithm:
      1. Run the prior-year same-month lookup for every forecast
         month (the original behaviour). Any forecast month with a
         non-zero prior-year sum on 50300 is resolved with method
         ``prior_year_same_month``.
      2. For forecast months that came back empty (no prior-year
         50300 activity), compute a fallback run-rate: the average
         of the 3 most-recent calendar months that have non-zero
         50300 activity, walking back from ``reference_month`` (or
         the earliest forecast month when ``reference_month`` is
         omitted) up to ``_MRR_FALLBACK_LOOKBACK_MONTHS`` months.
         The same fallback amount is applied to every forecast month
         missing prior-year coverage, with method
         ``historical_run_rate``.
      3. Forecast months still without a value after step 2 are
         marked ``no_history`` and OMITTED from the returned dict.
         The engine's ``_resolve_mrr_base`` then surfaces the
         InsufficientHistoryError (path 6 in the readiness spec),
         which the chat tool turns into a request for a manual MRR
         assumption — but only after the helper has confirmed 50300
         truly has no usable history.

    Returns:
      A tuple ``(monthly_baseline, metadata)``:
        * ``monthly_baseline`` — ``{forecast_month: amount}`` for
          every month the helper resolved (via prior-year OR
          fallback). Empty when neither path produced a value.
        * ``metadata`` — audit block surfaced verbatim by the
          chat tool: ``basis``, ``account_filter``, the prior-year
          lookup window + per-month result, the fallback window +
          months used + average + row counts, an overall
          ``fallback_method`` label, per-forecast-month
          ``method_by_month``, and any warnings.

    The helper does NOT raise. The engine raises
    ``InsufficientHistoryError`` only when both the prior-year and
    fallback paths fail for at least one forecast month AND no
    manual MRR assumption was supplied — that decision lives in
    ``engine._resolve_mrr_base`` exactly as before.
    """
    metadata: dict[str, Any] = {
        "basis": basis,
        "account_filter": _build_account_filter(account_prefix),
        "fallback_method_label": MRR_FALLBACK_METHOD_LABEL,
        "use_fallback": bool(use_fallback),
        "method_by_month": {},
        "warnings": [],
        # Filled in below as each step runs so partial results stay
        # readable when the helper short-circuits on empty input.
        "prior_year": {
            "lookup_window": None,
            "months_queried": [],
            "row_count": 0,
            "monthly_totals": {},
        },
        "fallback": {
            "lookup_window": None,
            "search_window_months": _MRR_FALLBACK_LOOKBACK_MONTHS,
            "months_with_activity": [],
            "months_used": [],
            "monthly_totals": {},
            "row_count": 0,
            "run_rate_monthly_amount": None,
            # Disambiguation: MRR fallback picks the most recent
            # months with 50300 activity. It deliberately does NOT
            # apply the sales/account-average "complete month" rule
            # — a current-month MRR booking IS valid signal for the
            # run-rate. Nova is told to quote this verbatim so the
            # user doesn't confuse the two rules.
            "month_selection_rule": "most_recent_active_months_with_50300_activity",
            "uses_complete_month_rule": False,
        },
        "fallback_method": "none",
    }

    if not forecast_months:
        return {}, metadata

    monthly_baseline: dict[str, float] = {}

    # ── Step 1: prior-year same-month ───────────────────────────
    prior_to_forecast: dict[str, str] = {
        _prior_year(fm): fm for fm in forecast_months
    }
    prior_year_months = sorted(prior_to_forecast.keys())
    prior_rows = await _fetch_mrr_rows_for_window(
        qbo_db, prior_year_months, basis, account_prefix=account_prefix,
    )
    prior_monthly_totals = _sum_mrr_rows_by_month(prior_rows)

    metadata["prior_year"]["lookup_window"] = (
        f"{prior_year_months[0]} .. {prior_year_months[-1]}"
    )
    metadata["prior_year"]["months_queried"] = list(prior_year_months)
    metadata["prior_year"]["row_count"] = len(prior_rows)
    metadata["prior_year"]["monthly_totals"] = dict(prior_monthly_totals)

    # Treat presence in ``prior_monthly_totals`` (i.e. at least one
    # categorised 50300 row) as the real signal — a zero-net month
    # with real activity (an invoice + reversal that cancel) is a
    # deliberate $0 baseline, NOT a missing one. Fallback only kicks
    # in when 50300 had zero rows in the prior-year month.
    for prior_month, total in prior_monthly_totals.items():
        forecast_month = prior_to_forecast.get(prior_month)
        if forecast_month is None:
            continue
        monthly_baseline[forecast_month] = total
        metadata["method_by_month"][forecast_month] = "prior_year_same_month"

    # ── Step 2: historical run-rate fallback ────────────────────
    missing_months = [
        fm for fm in forecast_months if fm not in monthly_baseline
    ]
    if not missing_months:
        metadata["fallback_method"] = "prior_year_same_month"
        return monthly_baseline, metadata

    # When the caller has explicitly opted out of fallback (e.g.
    # the user only wants prior-year same-month numbers), short-
    # circuit BEFORE the fallback fetch. Missing months stay
    # unresolved so the engine raises InsufficientHistoryError
    # for them — same surface as the pre-fallback behaviour.
    if not use_fallback:
        for fm in missing_months:
            metadata["method_by_month"][fm] = "no_history"
        if monthly_baseline:
            metadata["fallback_method"] = "partial_no_history"
        else:
            metadata["fallback_method"] = "no_history"
        metadata["warnings"].append(
            "use_fallback=false: skipped historical run-rate fallback "
            "even though prior-year same-month was missing for "
            f"{len(missing_months)} forecast month(s)."
        )
        return monthly_baseline, metadata

    # Walk backwards from the earliest forecast month (or the
    # explicit reference_month) up to the lookback cap, collecting
    # the months with non-zero 50300 activity. Reference_month is the
    # natural anchor when callers want the search to stop "now"
    # rather than "before the first forecast month"; defaults to the
    # earliest forecast month so the helper is usable without it.
    anchor = reference_month or min(forecast_months)
    candidate_months = _months_window_back(
        anchor, _MRR_FALLBACK_LOOKBACK_MONTHS,
    )
    metadata["fallback"]["lookup_window"] = (
        f"{candidate_months[-1]} .. {candidate_months[0]}"
    )

    fallback_rows = await _fetch_mrr_rows_for_window(
        qbo_db, candidate_months, basis, account_prefix=account_prefix,
    )
    fallback_monthly_totals = _sum_mrr_rows_by_month(fallback_rows)
    metadata["fallback"]["row_count"] = len(fallback_rows)
    metadata["fallback"]["monthly_totals"] = dict(fallback_monthly_totals)

    # candidate_months is newest-first. An "active" month is one
    # that had at least one categorised 50300 row in the fallback
    # window — presence of rows in ``fallback_monthly_totals`` is
    # the test, NOT a non-zero net. A net-zero month (invoice +
    # reversal cancel) is real activity and pulls the run-rate
    # down; silently dropping it would inflate the average for
    # refund-heavy tenants.
    active_months = [
        m for m in candidate_months
        if m in fallback_monthly_totals
    ]
    metadata["fallback"]["months_with_activity"] = list(active_months)

    if not active_months:
        # Neither prior-year nor fallback produced a value. Mark all
        # missing months as ``no_history`` so the chat tool can quote
        # the metadata when it asks for a manual MRR assumption.
        for fm in missing_months:
            metadata["method_by_month"][fm] = "no_history"
        metadata["fallback_method"] = (
            "no_history" if not monthly_baseline else "partial_no_history"
        )
        if not monthly_baseline:
            metadata["warnings"].append(
                "No 50300 activity found in prior-year same-month "
                "lookup and no 50300 activity found in the recent "
                f"{_MRR_FALLBACK_LOOKBACK_MONTHS}-month fallback "
                "window. A manual MRR assumption is required to "
                "proceed."
            )
        return monthly_baseline, metadata

    used = active_months[:_MRR_FALLBACK_RUN_RATE_MONTHS]
    metadata["fallback"]["months_used"] = list(used)
    run_rate = sum(fallback_monthly_totals[m] for m in used) / len(used)
    metadata["fallback"]["run_rate_monthly_amount"] = run_rate

    if len(used) < _MRR_FALLBACK_RUN_RATE_MONTHS:
        # Fewer than 3 active months — still usable, but the user
        # should know the run-rate is anchored on a thin signal.
        metadata["warnings"].append(
            f"Only {len(used)} month(s) of 50300 activity found in "
            f"the recent {_MRR_FALLBACK_LOOKBACK_MONTHS}-month "
            "lookback window; the run-rate fallback is averaged "
            "over fewer months than the usual 3."
        )

    for fm in missing_months:
        monthly_baseline[fm] = run_rate
        metadata["method_by_month"][fm] = "historical_run_rate"

    # Overall label: pure prior-year if nothing fell back, pure
    # fallback if everything fell back, mixed otherwise.
    methods = set(metadata["method_by_month"].values())
    if methods == {"prior_year_same_month"}:
        metadata["fallback_method"] = "prior_year_same_month"
    elif methods == {"historical_run_rate"}:
        metadata["fallback_method"] = "historical_run_rate"
    elif methods == {"no_history"}:
        metadata["fallback_method"] = "no_history"
    elif "historical_run_rate" in methods:
        metadata["fallback_method"] = "mixed"
    else:
        metadata["fallback_method"] = "prior_year_same_month"

    return monthly_baseline, metadata


# ---------------------------------------------------------------------------
# Generic account-prefix averages (last-N-complete-month helper)
# ---------------------------------------------------------------------------
#
# This helper deliberately accepts ALL prefixes from the caller. The
# module does NOT ship a default list — hardcoding tenant-specific
# account codes here would baked Asad-shaped assumptions into the
# product. Nova passes whichever prefixes the user named in the
# chat turn; tests use synthetic 9xxxx prefixes that cannot collide
# with any production CoA shape.

# Deprecated. The original 36-month lookback let the helper silently
# reach prior-year months when the latest_tx_date was stale (the live
# bug had current-year projections silently quoting prior-year
# averages because the lookback walk had no bound). The new
# bounded walk in ``_build_complete_history_months`` examines at most
# ``months + 1`` candidates and warns when fewer than the requested
# count of complete months were found within that window — never
# falling back into a year ago. Constant retained for documentation
# / blame readability; it is no longer referenced.
_ACCOUNT_PREFIX_LOOKBACK_CAP_MONTHS = 36


def _previous_month(month: str) -> str:
    """YYYY-MM for the calendar month immediately before ``month``."""
    year_str, mo_str = month.split("-")
    year = int(year_str)
    mo = int(mo_str) - 1
    if mo == 0:
        mo = 12
        year -= 1
    return f"{year}-{mo:02d}"


def _coerce_reference_month(
    reference_date: str | None,
    latest_tx_date: date,
) -> str:
    """Resolve the YYYY-MM anchor for the lookback walk.

    ``reference_date`` may be YYYY-MM-DD (preferred) OR YYYY-MM; the
    helper strips the day and keeps the calendar-month boundary.
    Falls back to ``latest_tx_date``'s YYYY-MM when the caller passes
    None (matches the existing engine convention of anchoring on the
    freshest GL row).
    """
    if not reference_date:
        return f"{latest_tx_date.year:04d}-{latest_tx_date.month:02d}"
    # Trim to YYYY-MM regardless of whether YYYY-MM-DD was passed.
    return reference_date[:7]


def _build_complete_history_months(
    reference_month: str,
    months: int,
    latest_tx_date: date,
    *,
    current_date: date | None = None,
) -> list[str]:
    """Return up to ``months`` complete calendar months strictly
    before ``reference_month``, oldest first.

    "Complete" applies both the source-freshness check
    (``latest_tx_date >= month_end``) AND the calendar-end check
    (``month_end < current_date``). The second guard is what stops
    the in-progress current month from being averaged on the
    strength of a single tx_date row that happens to sit on the
    month's last day. Without it, the in-progress current month
    with a tx_date row on its last day looked "complete" mid-month.

    Bounded walk: the helper examines at most ``months + 1``
    candidates so it can skip ONE in-progress tail month and then
    take ``months`` consecutive months. Walking further would
    silently reach into prior-year data, which is exactly the bug
    that put 2025 months into a 2026 projection. The chat tool
    surfaces ``months_used`` < ``months_requested`` with a warning
    rather than padding from a year ago.
    """
    from .data_freshness import is_month_complete  # avoid cycle at import

    result: list[str] = []
    current = _previous_month(reference_month)
    # Examine at most months+1 candidates. The +1 allows skipping
    # exactly one incomplete tail month (the in-progress current
    # period) without slipping into months ago.
    candidate_bound = months + 1
    examined = 0
    while len(result) < months and examined < candidate_bound:
        if is_month_complete(current, latest_tx_date, current_date=current_date):
            result.append(current)
        current = _previous_month(current)
        examined += 1
    return list(reversed(result))


def _normalise_prefixes(prefixes: list[str]) -> list[str]:
    """Strip whitespace, drop empties, de-duplicate while preserving
    caller order. The order matters because the response surfaces a
    list keyed by prefix and reviewers expect the same order they
    asked for."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in prefixes:
        if not isinstance(raw, str):
            continue
        prefix = raw.strip()
        if not prefix or prefix in seen:
            continue
        seen.add(prefix)
        out.append(prefix)
    return out


def _build_account_prefix_match_clauses(
    prefix: str,
) -> list[dict[str, Any]]:
    """Return the ``$or`` clauses that match GL rows belonging to
    ``prefix`` without false-matching neighbouring codes.

    Match shapes (all anchored so trailing chars cannot extend the
    match):

      account_id  starts with prefix followed by non-digit or EOL —
                  catches both bare ``"50300"`` and sub-account
                  ``"50300-1"`` shapes while rejecting ``"503001"``.

      account_name has the prefix either at the very start OR
                   immediately after a ``:`` parent-account separator,
                   followed by whitespace or EOL — catches both
                   ``"90000 Synthetic Admin Expense"`` and
                   ``"Synthetic Expense:90000 Synthetic Admin Expense"``
                   without matching ``"190000"`` (preceding digit) or
                   ``"Account 990000"`` (preceding space).
    """
    return [
        # account_id anchored at start, prefix followed by non-digit or EOL
        {"account_id": {"$regex": f"^{prefix}(?:\\D|$)"}},
        # account_name with prefix at start or after a colon
        {"account_name": {"$regex": f"(?:^|:){prefix}(?:\\s|$)"}},
    ]


def _row_matches_prefix(row_account_name: str, prefix: str) -> bool:
    """Python-side belt that mirrors the Mongo regex above. Run on
    every aggregated row so a tenant whose CoA carries the code in a
    surprising slot (e.g. lowercase mismatch, extra padding) does not
    leak between prefix buckets — and so the test surface can exercise
    the matcher without spinning up Mongo."""
    if not row_account_name:
        return False
    import re
    pattern = re.compile(f"(?:^|:){re.escape(prefix)}(?:\\s|$)")
    return bool(pattern.search(row_account_name))


async def load_account_prefix_averages(
    qbo_db: AsyncIOMotorDatabase,
    *,
    basis: str,
    account_prefixes: list[str],
    reference_date: str | None = None,
    months: int = 3,
    current_date: date | None = None,
) -> dict[str, Any]:
    """Compute last-N-complete-month averages for caller-supplied
    account prefixes. Returns a structured audit block.

    Args:
      qbo_db: Motor database with the AEGISAXIOM general_ledger collection.
      basis: ``"accrual"`` or ``"cash"`` — passed verbatim to the
        Mongo $match stage. The helper does NOT validate; the chat
        tool layer rejects invalid values upstream so callers see a
        clean structured error.
      account_prefixes: list of digit-prefixes the user named (e.g.
        ``["90000", "90100"]``). Empty or whitespace-only entries
        are dropped. The list is the ONLY source of prefixes — there
        is no module-level default.
      reference_date: YYYY-MM-DD or YYYY-MM anchor for the walk.
        When None, the helper falls back to the latest GL tx_date.
      months: how many complete months to average over (default 3).

    Returns:
      A dict with shape::

        {
          "basis": <basis>,
          "reference_date": <reference_date>,
          "months_used": ["YYYY-MM", ...],   # oldest-first
          "accounts": [
            {
              "account_prefix": "90000",
              "account_names": [...],
              "monthly_totals": {"YYYY-MM": amount, ...},
              "row_count": <int>,
              "average_monthly_amount": <float>,
              "method": "last_3_complete_month_average",
            },
            ...
          ],
          "warnings": [...],
        }

      ``row_count`` counts AGGREGATED rows (one per (month,
      account_name) tuple). Empty prefixes — prefixes the user
      named but where no GL rows matched in the window — appear
      with ``row_count=0`` and ``average_monthly_amount=0.0`` and
      add a warning to the top-level ``warnings`` list.

    The helper does NOT raise on empty prefix lists, missing tx_date,
    or stale tenants. The chat tool consumes this output and decides
    whether to add manual cost assumptions to the projection or to
    apologise and ask for a different account list.
    """
    normalised = _normalise_prefixes(account_prefixes or [])
    method_label = f"last_{months}_complete_month_average"

    # Reference month is derivable from reference_date even when GL
    # is empty — surface it in the response so a reviewer can see
    # the anchor the helper used regardless of data state.
    reference_month_hint = (
        reference_date[:7] if isinstance(reference_date, str) and reference_date
        else None
    )

    base_response: dict[str, Any] = {
        "basis": basis,
        "reference_date": reference_date,
        # New audit fields: explicit reference_month + latest_tx_date
        # + months_requested so a reviewer can see exactly what
        # window the helper aimed at and what tx date it was anchored
        # against. ``prior_year_logic_used=False`` is constant: this
        # helper NEVER applies prior-year same-month logic — that is
        # a separate path for MRR only (load_mrr_baseline_with_fallback).
        "reference_month": reference_month_hint,
        "latest_tx_date": None,
        # ``current_date_anchor`` is the calendar today the helper
        # used to enforce the "current month is not yet complete"
        # rule. Surfacing it lets Nova quote which date was used
        # when the in-progress month was excluded.
        "current_date_anchor": (
            current_date.isoformat() if current_date is not None else None
        ),
        "months_requested": months,
        "method": method_label,
        "prior_year_logic_used": False,
        "months_used": [],
        "accounts": [],
        "warnings": [],
    }
    if not normalised:
        base_response["warnings"].append(
            "No account prefixes supplied. Pass a list of code "
            "prefixes (e.g. ['90000', '90100']) to compute averages."
        )
        return base_response

    try:
        latest_tx = await get_latest_tx_date(qbo_db)
    except ValueError:
        # GL is empty — every prefix returns zero rows. Surface a
        # single tenant-level warning and a zero-row entry for each
        # asked prefix so the chat tool can pass that on verbatim.
        base_response["warnings"].append(
            "general_ledger is empty; no rows available for any "
            "prefix in the supplied window."
        )
        for prefix in normalised:
            base_response["accounts"].append({
                "account_prefix": prefix,
                "account_names": [],
                "monthly_totals": {},
                "row_count": 0,
                "average_monthly_amount": 0.0,
                "method": method_label,
            })
        return base_response

    reference_month = _coerce_reference_month(reference_date, latest_tx)
    base_response["reference_month"] = reference_month
    base_response["latest_tx_date"] = latest_tx.isoformat()
    history_months = _build_complete_history_months(
        reference_month, months, latest_tx,
        current_date=current_date,
    )
    base_response["months_used"] = list(history_months)
    if len(history_months) < months:
        base_response["warnings"].append(
            f"Only {len(history_months)} complete month(s) available "
            f"in the window of {months + 1} candidates before "
            f"reference_month {reference_month} (latest_tx_date "
            f"{latest_tx.isoformat()}); the average is thinner than "
            "the requested window. The helper deliberately does NOT "
            "reach into prior-year months to backfill — that would "
            "silently mix stale data into a current-period forecast."
        )

    if not history_months:
        for prefix in normalised:
            base_response["accounts"].append({
                "account_prefix": prefix,
                "account_names": [],
                "monthly_totals": {},
                "row_count": 0,
                "average_monthly_amount": 0.0,
                "method": method_label,
            })
        return base_response

    # Build one Mongo pipeline that pulls EVERY prefix's rows in a
    # single round-trip. The Python-side belt re-checks each row
    # against the per-prefix regex so cross-prefix bleed is impossible
    # even if the server-side $or pattern were broadened.
    earliest = history_months[0]
    latest = history_months[-1]
    start_date = f"{earliest}-01"
    end_date = _start_of_next_month(latest)

    or_clauses: list[dict[str, Any]] = []
    for prefix in normalised:
        or_clauses.extend(_build_account_prefix_match_clauses(prefix))

    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "tx_date": {"$gte": start_date, "$lt": end_date},
                "basis": basis,
                "$or": or_clauses,
            }
        },
        {
            "$project": {
                "month": {"$substr": ["$tx_date", 0, 7]},
                "account_id": 1,
                "account_name": 1,
                "amount": 1,
            }
        },
        {
            "$group": {
                "_id": {
                    "month": "$month",
                    "account_id": "$account_id",
                    "account_name": "$account_name",
                },
                "amount": {"$sum": "$amount"},
            }
        },
    ]
    rows = await qbo_db.general_ledger.aggregate(pipeline).to_list(
        length=_GL_QUERY_LIMIT,
    )
    if len(rows) >= _GL_QUERY_LIMIT:
        logger.warning(
            "GL aggregation in load_account_prefix_averages hit length "
            "cap of %d rows. Result may be truncated. Raise "
            "_GL_QUERY_LIMIT if this is expected for the CoA size.",
            _GL_QUERY_LIMIT,
        )

    # Bucket rows by prefix. Each prefix gets monthly_totals
    # (month -> sum) and the set of account_names it pulled in.
    per_prefix: dict[str, dict[str, Any]] = {
        prefix: {
            "monthly_totals": {m: 0.0 for m in history_months},
            "account_names": set(),
            "row_count": 0,
        }
        for prefix in normalised
    }
    for row in rows:
        account_name = (row["_id"].get("account_name") or "").strip()
        month = row["_id"].get("month")
        amount = _safe_float(row.get("amount", 0.0))
        if month not in history_months:
            continue
        # Map the row to its prefix(es). A row could match multiple
        # prefixes only if the caller asked for overlapping ones
        # (e.g. ['9', '90000']) — we apportion to EACH matching
        # prefix so the user sees the rows under whichever bucket
        # they asked about.
        for prefix in normalised:
            if _row_matches_prefix(account_name, prefix):
                bucket = per_prefix[prefix]
                bucket["monthly_totals"][month] += amount
                if account_name:
                    bucket["account_names"].add(account_name)
                bucket["row_count"] += 1

    for prefix in normalised:
        bucket = per_prefix[prefix]
        monthly_totals: dict[str, float] = bucket["monthly_totals"]
        row_count: int = bucket["row_count"]
        if row_count == 0:
            base_response["warnings"].append(
                f"No GL rows matched prefix {prefix!r} in "
                f"{history_months[0]}..{history_months[-1]}."
            )
            avg = 0.0
        else:
            avg = sum(monthly_totals.values()) / len(history_months)
        base_response["accounts"].append({
            "account_prefix": prefix,
            "account_names": sorted(bucket["account_names"]),
            "monthly_totals": dict(monthly_totals),
            "row_count": row_count,
            "average_monthly_amount": avg,
            "method": method_label,
        })

    return base_response


# ---------------------------------------------------------------------------
# Account groupings
# ---------------------------------------------------------------------------

async def load_account_groupings(
    app_db: AsyncIOMotorDatabase,
) -> list[dict]:
    """Read account groupings from the AEGISAXIOM app database.

    Schema is owned by the AEGISAXIOM app. Pass through unchanged. Strips _id
    (ObjectId is not JSON-serializable). Returns empty list if collection
    is empty.
    """
    cursor = app_db.account_groupings.find({})
    # v1 cap. If account_groupings grows past this, switch to pagination.
    docs = await cursor.to_list(length=1000)
    return [{k: v for k, v in d.items() if k != "_id"} for d in docs]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def build_projection_inputs(
    qbo_db: AsyncIOMotorDatabase,
    app_db: AsyncIOMotorDatabase,
    *,
    starting_cash: float,
    projection_start_month: str,
    horizon_weeks: int = 13,
    basis: str = "accrual",
    mrr_account_prefix: str = "50300",
    use_mrr_fallback: bool = True,
    current_date: date | None = None,
) -> ProjectionInputs:
    """Build a ProjectionInputs object from live Mongo data.

    Raises:
      InsufficientHistoryError: if fewer than 3 complete months are
        available before projection_start_month.

    Manual cost / recurring revenue assumptions are NOT loaded here. They
    are caller-supplied via the engine's higher-level API.
    """
    latest = await get_latest_tx_date(qbo_db)

    # Sanity check tx_date storage type. _build_history_pipeline assumes
    # tx_date is stored as YYYY-MM-DD string (AEGISAXIOM's current schema). If
    # it's been migrated to BSON ISODate, $gte/$lt against string bounds
    # will silently return zero rows. Detecting this here surfaces a
    # clear warning instead of an opaque InsufficientHistoryError later.
    sample = await qbo_db.general_ledger.find_one(
        {}, {"tx_date": 1, "_id": 0}
    )
    if sample is not None:
        tx_date_value = sample.get("tx_date")
        if not isinstance(tx_date_value, str):
            logger.warning(
                "tx_date in general_ledger is stored as %s, but "
                "data_loader assumes YYYY-MM-DD string format. "
                "$gte/$lt comparisons in _build_history_pipeline may "
                "return empty results. Update the pipeline to use "
                "datetime objects if storage type has changed.",
                type(tx_date_value).__name__,
            )

    history_months = last_n_complete_months(
        reference_month=projection_start_month,
        latest_tx_date=latest,
        n=3,
        current_date=current_date,
    )
    if len(history_months) < 3:
        raise InsufficientHistoryError(
            line="history",
            available_months=len(history_months),
            required_months=3,
        )

    revenue = await load_revenue_history(qbo_db, history_months, basis)
    gp = await load_gross_profit_history(qbo_db, history_months, basis)

    # Forecast months = ceil(horizon_weeks / 4) months starting at
    # projection_start_month. Used for MRR comparator lookup.
    n_forecast_months = (horizon_weeks + 3) // 4
    forecast_months = _next_n_months(projection_start_month, n_forecast_months)

    # Auto-MRR from the GL: 50300 prior-year same-month rows feed
    # the engine's MRR baseline. When prior-year same-month is missing
    # for a forecast month but 50300 has recent activity, the helper
    # falls back to a 3-month run-rate (clearly labelled as a
    # historical contract commission run-rate, not a guaranteed
    # fixed MRR) so the projection runs end-to-end instead of failing
    # with a misleading "MRR mapping unavailable" message. An empty
    # baseline is still a real state (tenant with no 50300 activity
    # at all); the engine handles it via _resolve_mrr_base, which
    # falls through to manual MRR and raises
    # InsufficientHistoryError only when 50300 truly has no usable
    # history.
    mrr, mrr_baseline_metadata = await load_mrr_baseline_with_fallback(
        qbo_db, forecast_months, basis=basis,
        reference_month=projection_start_month,
        account_prefix=mrr_account_prefix,
        use_fallback=use_mrr_fallback,
    )

    groupings = await load_account_groupings(app_db)

    # Auto-detect wholesale revenue from GL 50400 prefix. The 'QBO cannot
    # reliably identify wholesale' caveat above is true at the customer
    # level (no first-class wholesale_customer flag in QBO), but the
    # account-prefix rule in account_categories.py is schema-encoded and
    # reliable. compute_auto_wholesale_assumption only returns a non-None
    # entry when GL signal is consistent across history (>= 2 of 3
    # months); otherwise we honor the original "skip wholesale" stance.
    monthly_wholesale = await detect_wholesale_history_from_gl(
        qbo_db, history_months, basis
    )
    auto_wholesale = compute_auto_wholesale_assumption(
        monthly_wholesale,
        history_months,
        starts_month=projection_start_month,
    )
    auto_recurring = [auto_wholesale] if auto_wholesale is not None else []

    # Build the sales-baseline audit block. The data is already in
    # hand from load_revenue_history; we just pair each month with
    # its computed bucket totals so Nova can quote "month X
    # contributed Y" without paraphrasing. average_monthly_sales is
    # the simple mean of per-month totals (PS + FS + OTHER) — the
    # same metric the engine projects forward via
    # mean_last_3_months. Categories list mirrors what
    # load_revenue_history scoped to (PS/FS/OTHER); MRR and
    # WHOLESALE are deliberately excluded because they have their
    # own baseline paths and additive entries.
    monthly_sales_totals: dict[str, float] = {}
    for idx, month in enumerate(history_months):
        monthly_sales_totals[month] = (
            float(revenue["PS_REVENUE"][idx])
            + float(revenue["FS_REVENUE"][idx])
            + float(revenue["OTHER_REVENUE"][idx])
        )
    average_monthly_sales = (
        sum(monthly_sales_totals.values()) / len(history_months)
        if history_months
        else 0.0
    )
    sales_baseline_metadata: dict[str, Any] = {
        "method": "last_3_complete_month_average",
        "basis": basis,
        "reference_month": projection_start_month,
        "latest_tx_date": latest.isoformat(),
        # ``current_date_anchor`` is the calendar today the helper
        # used to enforce the "current month is not yet complete"
        # rule. None when the caller did not pass one (legacy code
        # paths); the boundary dispatcher always sets it.
        "current_date_anchor": (
            current_date.isoformat() if current_date is not None else None
        ),
        "months_requested": 3,
        "months_used": list(history_months),
        "revenue_categories": ["PS_REVENUE", "FS_REVENUE", "OTHER_REVENUE"],
        "monthly_totals": monthly_sales_totals,
        "average_monthly_sales": round(average_monthly_sales, 2),
        "warnings": [],
    }

    return ProjectionInputs(
        starting_cash=starting_cash,
        projection_start_month=projection_start_month,
        horizon_weeks=horizon_weeks,
        basis=basis,
        ps_revenue_last_3_months=revenue["PS_REVENUE"],
        fs_revenue_last_3_months=revenue["FS_REVENUE"],
        other_revenue_last_3_months=revenue["OTHER_REVENUE"],
        ps_gross_profit_last_3_months=gp["PS_GP"],
        fs_gross_profit_last_3_months=gp["FS_GP"],
        mrr_last_year_same_months=mrr,
        mrr_baseline_metadata=mrr_baseline_metadata,
        sales_baseline_metadata=sales_baseline_metadata,
        wholesale_invoices=[],   # legacy field; auto-detection uses manual_recurring_revenue
        fixed_costs=[],          # legacy field; manual_cost_assumptions preferred
        manual_cost_assumptions=[],
        manual_recurring_revenue=auto_recurring,
        account_groupings=groupings,
    )


async def build_projection_inputs_from_globals(
    *,
    starting_cash: float,
    projection_start_month: str,
    horizon_weeks: int = 13,
    basis: str = "accrual",
    overrides: list | None = None,
    manual_cost_assumptions: list | None = None,
    manual_recurring_revenue: list | None = None,
    weekly_timing_overrides: dict | None = None,
    mrr_account_prefix: str | None = None,
    use_mrr_fallback: bool | None = None,
    current_date: date | None = None,
) -> ProjectionInputs:
    """Convenience wrapper for FastAPI routes and the chat agent.

    Resolves qbo_db / app_db from the global Motor client (no per-call
    client construction), builds the historical-inputs baseline via
    build_projection_inputs, then splices in caller-supplied scenario
    state (overrides + manual assumptions) via model_copy.

    The base build_projection_inputs intentionally does NOT load scenario
    state — that's caller-supplied. This wrapper is the canonical seam
    for that splicing.

    ``mrr_account_prefix`` / ``use_mrr_fallback`` are runtime
    overrides for the MRR baseline resolver. ``None`` means "use the
    inner function's default" so this wrapper stays
    backward-compatible with callers that never pass them.
    """
    # Build kwargs conditionally so the inner default kwargs win when
    # the caller passes None — keeps the 50300 / fallback-on defaults
    # in ONE place (build_projection_inputs) instead of duplicating
    # them here.
    inner_kwargs: dict[str, Any] = {
        "qbo_db": get_qbo_db(),
        "app_db": get_app_db(),
        "starting_cash": starting_cash,
        "projection_start_month": projection_start_month,
        "horizon_weeks": horizon_weeks,
        "basis": basis,
    }
    if mrr_account_prefix is not None:
        inner_kwargs["mrr_account_prefix"] = mrr_account_prefix
    if use_mrr_fallback is not None:
        inner_kwargs["use_mrr_fallback"] = use_mrr_fallback
    if current_date is not None:
        inner_kwargs["current_date"] = current_date
    base = await build_projection_inputs(**inner_kwargs)

    if (
        overrides is None
        and manual_cost_assumptions is None
        and manual_recurring_revenue is None
        and weekly_timing_overrides is None
    ):
        return base

    # Auto-detected wholesale entries on the base must survive the
    # scenario splice. Without this, chat-tool round trips that always
    # pass manual_recurring_revenue=[] would wipe them. Caller-supplied
    # WHOLESALE explicitly wins (caller knows best); otherwise we
    # prepend the auto-detected entry so the engine still sees it.
    caller_recurring = manual_recurring_revenue if manual_recurring_revenue is not None else []
    base_auto_entries = [a for a in base.manual_recurring_revenue if is_auto_detected(a)]
    caller_has_wholesale = any(
        getattr(a, "line_type", None) == "WHOLESALE" for a in caller_recurring
    )
    merged_recurring = (
        list(caller_recurring)
        if (caller_has_wholesale or not base_auto_entries)
        else list(base_auto_entries) + list(caller_recurring)
    )

    updates = {
        "overrides": overrides or [],
        "manual_cost_assumptions": manual_cost_assumptions or [],
        "manual_recurring_revenue": merged_recurring,
    }

    if weekly_timing_overrides is not None:
        updates["weekly_timing_overrides"] = weekly_timing_overrides

    return base.model_copy(update=updates)