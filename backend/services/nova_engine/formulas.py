"""Pure formula functions — building blocks of the engine.

Every function is pure: deterministic, no I/O, no side effects.

Defensiveness philosophy:
  - STRICT on structural contract: lengths must be 3 where promised.
  - LOOSE on values: zeros and negatives accepted (real edge cases).
  - NO normalization in distribute_weekly. Weights are weights.

These rules come from Friday's call with Asad. The workbook's hardcoded
56/60/72 COGS ratios and /4 averaging are NOT respected — Nova v1
implements Asad's described business logic, not the spreadsheet's frozen
state.

Note: ProjectionInputs enforces length 3 at the model layer. The
duplicate length checks in these formulas are belt-and-suspenders for
callers that pass raw lists (e.g., during testing or future direct use).
"""
from __future__ import annotations

from typing import Optional

from .models import WeeklyDistribution


# ---------------------------------------------------------------------------
# Canonical company-level revenue / COGS category enumeration
# ---------------------------------------------------------------------------
#
# Single source of truth for which categories sum into company-level
# Total Revenue and Total COGS for the gross-profit / gross-margin
# calculation. Mirrors the validated QBO P&L baseline in
# routes/finance_kpis.py: Total Income + Total Cost of Goods Sold from
# the P&L report.
#
# Audit found that query_finance_data was subtracting only
# PS_COGS + FS_COGS while including OTHER_COGS-eligible revenue rows
# in Total Revenue — an asymmetric formula that overstated gross
# profit and gross margin whenever a tenant booked direct cost of
# revenue to a name that fell into the OTHER_COGS bucket. Centralising
# the enumeration here means every company-level GP path uses the
# same coverage.
#
# Per-service-line GP (PS_GP / FS_GP in the projection engine) is a
# separate, intentionally narrower formula and does NOT use these
# constants. Per-job / per-deal margin is also separate.

REVENUE_CATEGORIES: frozenset[str] = frozenset({
    "PS_REVENUE",
    "FS_REVENUE",
    "MRR",
    "WHOLESALE_REVENUE",
    "OTHER_REVENUE",
})

COGS_CATEGORIES: frozenset[str] = frozenset({
    "PS_COGS",
    "FS_COGS",
    "OTHER_COGS",
})


# Bumped whenever the canonical revenue-or-COGS enumeration changes,
# so a stale cached response from an older Nova build can be spotted.
GROSS_PROFIT_FORMULA_VERSION: str = "2026-05-10.gl_canonical_v1"

# Stable label embedded in tool responses so a downstream consumer can
# tell GL-derived GP/GM apart from QBO-P&L-derived GP/GM at a glance.
GROSS_PROFIT_FORMULA_SOURCE_GL: str = "gl_category_canonical"
GROSS_PROFIT_FORMULA_SOURCE_PNL: str = "qbo_pnl_summary"


def compute_gross_profit(
    revenue_by_category: dict[str, float],
    cogs_by_category: dict[str, float],
) -> dict:
    """Canonical company-level gross-profit calculation.

    Sums values in ``revenue_by_category`` whose key is in
    ``REVENUE_CATEGORIES`` and values in ``cogs_by_category`` whose key
    is in ``COGS_CATEGORIES``. Categories outside those sets are
    returned in ``excluded_categories`` so the caller can surface a
    coverage warning rather than silently dropping data.

    Returns:
        {
          "total_revenue": float,
          "total_cogs": float,
          "gross_profit": float,
          "gross_margin": float | None,   # None when total_revenue == 0
          "included_revenue_categories": [...],   # sorted, only those with non-empty input
          "included_cogs_categories": [...],      # sorted, only those with non-empty input
          "excluded_categories": [...],           # sorted, anything not in either canonical set
          "formula_version": GROSS_PROFIT_FORMULA_VERSION,
          "formula_source": GROSS_PROFIT_FORMULA_SOURCE_GL,
        }

    The function does no I/O. Negative values in either dict are
    accepted (refund-heavy month, write-off). gross_margin is reported
    as a fraction (0.42, not 42%); per-product display layers can
    multiply by 100 if they prefer percent.
    """
    if not isinstance(revenue_by_category, dict) or not isinstance(cogs_by_category, dict):
        raise TypeError(
            "compute_gross_profit requires dict inputs for both revenue and COGS"
        )

    included_revenue: list[str] = []
    total_revenue = 0.0
    for category, amount in revenue_by_category.items():
        if category in REVENUE_CATEGORIES:
            total_revenue += float(amount)
            included_revenue.append(category)

    included_cogs: list[str] = []
    total_cogs = 0.0
    for category, amount in cogs_by_category.items():
        if category in COGS_CATEGORIES:
            total_cogs += float(amount)
            included_cogs.append(category)

    excluded: list[str] = []
    for category in revenue_by_category:
        if category not in REVENUE_CATEGORIES:
            excluded.append(category)
    for category in cogs_by_category:
        if category not in COGS_CATEGORIES:
            excluded.append(category)

    gross_profit = total_revenue - total_cogs
    gross_margin: Optional[float]
    if total_revenue == 0:
        gross_margin = None
    else:
        gross_margin = gross_profit / total_revenue

    return {
        "total_revenue": total_revenue,
        "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
        "included_revenue_categories": sorted(set(included_revenue)),
        "included_cogs_categories": sorted(set(included_cogs)),
        "excluded_categories": sorted(set(excluded)),
        "formula_version": GROSS_PROFIT_FORMULA_VERSION,
        "formula_source": GROSS_PROFIT_FORMULA_SOURCE_GL,
    }


def mean_last_3_months(values: list[float]) -> float:
    """Arithmetic mean of exactly 3 monthly values.

    STRICT: input must have length exactly 3. Function name promises 3.
    Zeros and negatives accepted (slow quarter, refund-heavy month).

    Raises:
      ValueError: if len(values) != 3.
    """
    if len(values) != 3:
        raise ValueError(
            f"mean_last_3_months requires exactly 3 values, got {len(values)}"
        )
    return sum(values) / 3.0


def gp_ratio(revenue: list[float], gross_profit: list[float]) -> float:
    """Gross profit ratio = sum(gross_profit) / sum(revenue) over the period.

    STRICT: both lists must have length 3.
    ZERO-SAFE: returns 0.0 if total revenue is 0 (no service-line activity).
    Negative gross profit accepted (loss-making period — return is negative).

    Raises:
      ValueError: if either list is not length 3.
    """
    if len(revenue) != 3:
        raise ValueError(
            f"gp_ratio requires revenue of length 3, got {len(revenue)}"
        )
    if len(gross_profit) != 3:
        raise ValueError(
            f"gp_ratio requires gross_profit of length 3, got {len(gross_profit)}"
        )
    total_revenue = sum(revenue)
    if total_revenue == 0:
        return 0.0
    return sum(gross_profit) / total_revenue


def cogs_ratio(revenue: list[float], gross_profit: list[float]) -> float:
    """COGS ratio = 1.0 - gp_ratio, with explicit zero-safety.

    If total revenue is 0, returns 0.0 (NOT 1.0). Returning 1.0 would be
    misleading metadata ("100% COGS"). 0.0 is the honest representation:
    nothing happened, neither GP nor COGS is meaningful.

    Raises:
      ValueError: if either list is not length 3.
    """
    if len(revenue) != 3:
        raise ValueError(
            f"cogs_ratio requires revenue of length 3, got {len(revenue)}"
        )
    if len(gross_profit) != 3:
        raise ValueError(
            f"cogs_ratio requires gross_profit of length 3, got {len(gross_profit)}"
        )
    total_revenue = sum(revenue)
    if total_revenue == 0:
        return 0.0
    return 1.0 - (sum(gross_profit) / total_revenue)


def distribute_weekly(
    monthly_amount: float, dist: WeeklyDistribution
) -> list[float]:
    """Spread a monthly amount across 4 weeks per the distribution weights.

    Returns 4 floats. NO NORMALIZATION. If weights don't sum to 1.0,
    result won't sum to monthly_amount — that's the caller's choice.

    Negative monthly_amount accepted (refund/credit case).
    """
    return [
        monthly_amount * dist.week1_pct,
        monthly_amount * dist.week2_pct,
        monthly_amount * dist.week3_pct,
        monthly_amount * dist.week4_pct,
    ]


# ---------------------------------------------------------------------------
# K4. Shared reversal metadata accumulator
# ---------------------------------------------------------------------------
#
# The signed total accumulation loop was duplicated across three call
# sites. routes/finance_revenue.py per month, services/nova_engine/
# finance_driver_analysis.py per scope, and services/ai/agents/tools/
# nova_tools.py per query. The three loops shared an identical core.
# Sum positive amounts and count them, sum negative amounts and count
# them, derive signed total from positive plus negative. Drift risk
# across three copies was real because every reversal bug fix had to
# land in three places.
#
# This helper centralises the core. Callers pass a flat iterable of
# already filtered signed amounts. Category filtering, location
# filtering, txn type collection, and any other per row predicate
# stay at the call site because they vary by site. The return shape is
# a plain dict that callers merge into their own response payload.
#
# Bot reviewers have flagged this duplication more than once. Do not
# reintroduce inline loops at the existing three sites without first
# checking whether the helper covers the case.


def compute_reversal_metadata(amounts) -> dict[str, float | int]:
    """Aggregate signed amounts into the canonical reversal metadata
    shape used across the finance and Nova surfaces.

    Args.
      amounts. An iterable of signed floats. The caller is responsible
        for category filtering and row coercion before passing the
        amounts in. Non numeric entries are not tolerated; callers
        should coerce upstream with try except float as the three
        existing sites already do.

    Returns.
      A dict with five fields.
        signed_total. Net of positive plus negative.
        positive_total. Sum of amounts strictly greater than zero.
        negative_total. Sum of amounts strictly less than zero. Stays
          negative so positive plus negative equals signed_total.
        positive_row_count. Count of amounts strictly greater than
          zero.
        negative_row_count. Count of amounts strictly less than zero.

    Zero amounts contribute to neither bucket. The helper is pure and
    deterministic, no I/O.
    """
    positive_total = 0.0
    negative_total = 0.0
    positive_row_count = 0
    negative_row_count = 0
    for amount in amounts:
        if amount > 0:
            positive_total += amount
            positive_row_count += 1
        elif amount < 0:
            negative_total += amount
            negative_row_count += 1
    return {
        "signed_total": positive_total + negative_total,
        "positive_total": positive_total,
        "negative_total": negative_total,
        "positive_row_count": positive_row_count,
        "negative_row_count": negative_row_count,
    }
