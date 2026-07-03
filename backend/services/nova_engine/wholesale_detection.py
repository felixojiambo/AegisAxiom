"""Deterministic wholesale-revenue auto-detection for Nova.

Earlier comments in this package (data_loader docstring + line 262) read:
"QBO cannot reliably identify wholesale invoices, wholesale revenue flows
through manual input only." That position has been overtaken by reality:

  - account_categories.py:30 already maps GL accounts starting with
    50400 to WHOLESALE_REVENUE. When a GL row posts to that account,
    the wholesale signal is schema-level certain.
  - Several AEGISAXIOM app fields explicitly carry "wholesale_mrr" as a
    structured value (Opportunity.lines_of_business, Contract.contract_type,
    SOW.line_of_business, Product.item_category). When any of those
    fields equals "wholesale_mrr", the record is wholesale.

This module turns those signals into a classifier (record-level) plus a
history aggregator (3-month wholesale revenue from the GL). The
classifier is the rule-set; the aggregator is the projection-input hook.

Policy choices (deliberate, locked in by tests):
  - LLM classification of accounting data is forbidden. Every rule
    here matches a structured field exactly - no fuzzy contains, no
    case-insensitive name-match against arbitrary customer strings
    unless the field semantically encodes the category.
  - When no rule fires, classify_record returns ``UNKNOWN`` and the
    history aggregator returns an empty month. Better to mark a record
    as unattributed than to silently bucket it as wholesale.
  - Auto-population of projection inputs only fires when the GL has
    consistent wholesale signal across the history window
    (>= ``MIN_MONTHS_FOR_AUTO_FILL`` of the 3 history months are
    non-zero). One stray $5 invoice in a single month is not enough
    to seed a recurring assumption. Caller-supplied WHOLESALE entries
    always win.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Literal

from motor.motor_asyncio import AsyncIOMotorDatabase

from ._gl_helpers import _GL_QUERY_LIMIT, _build_history_pipeline
from .account_categories import categorize
from .models import RecurringRevenueAssumption


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-record classifier
# ---------------------------------------------------------------------------


WholesaleSignal = Literal["WHOLESALE", "NOT_WHOLESALE", "UNKNOWN"]


# Canonical structured value used across AEGISAXIOM app schemas to denote
# wholesale. Found on Opportunity.lines_of_business, Contract.contract_type,
# SOW.line_of_business, SOWProductItem.item_category, Product.item_category.
_WHOLESALE_LOB_VALUE = "wholesale_mrr"


def classify_record(
    *,
    account_name: str | None = None,
    contract_type: str | None = None,
    line_of_business: str | None = None,
    lines_of_business: Iterable[str] | None = None,
    item_category: str | None = None,
) -> dict[str, Any]:
    """Classify a single record as WHOLESALE / NOT_WHOLESALE / UNKNOWN.

    Inputs are the structured fields AEGISAXIOM already stores (no fuzzy text
    matching against free-form descriptions). At least one signal must
    fire for a non-UNKNOWN result. ``signals`` lists which inputs voted
    for the chosen classification so callers can audit attribution.

    The function is intentionally side-effect free and synchronous so
    tests and the GL aggregator can both reuse it.
    """
    signals: list[str] = []

    # Signal 1: GL account category. account_categories.categorize()
    # returns "WHOLESALE_REVENUE" when account_name starts with 50400.
    # Any other revenue category counts as NOT_WHOLESALE - a record
    # explicitly tagged with a different revenue line cannot also be
    # wholesale.
    if account_name:
        category = categorize(account_name)
        if category == "WHOLESALE_REVENUE":
            signals.append("gl_account_50400")
        elif category in ("PS_REVENUE", "FS_REVENUE", "MRR", "OTHER_REVENUE"):
            return {
                "classification": "NOT_WHOLESALE",
                "signals": [f"gl_account_category={category}"],
            }

    # Signal 2: contract_type. The SOW/Contract schema treats
    # "wholesale_mrr" as a first-class contract type.
    if contract_type and contract_type.strip().lower() == _WHOLESALE_LOB_VALUE:
        signals.append("contract_type=wholesale_mrr")

    # Signal 3: SOW line_of_business (single value). NOT a fuzzy text
    # match - the field's enum is field_services / professional_services /
    # wholesale_mrr.
    if line_of_business and line_of_business.strip().lower() == _WHOLESALE_LOB_VALUE:
        signals.append("line_of_business=wholesale_mrr")

    # Signal 4: Opportunity lines_of_business (list). Wholesale
    # opportunities tag the whole row.
    if lines_of_business:
        for lob in lines_of_business:
            if isinstance(lob, str) and lob.strip().lower() == _WHOLESALE_LOB_VALUE:
                signals.append("opportunity_lines_of_business=wholesale_mrr")
                break

    # Signal 5: Product / SOW item category.
    if item_category and item_category.strip().lower() == _WHOLESALE_LOB_VALUE:
        signals.append("item_category=wholesale_mrr")

    if signals:
        return {"classification": "WHOLESALE", "signals": signals}
    return {"classification": "UNKNOWN", "signals": []}


# ---------------------------------------------------------------------------
# GL history aggregator
# ---------------------------------------------------------------------------


async def detect_wholesale_history_from_gl(
    qbo_db: AsyncIOMotorDatabase,
    history_months: list[str],
    basis: str = "accrual",
) -> dict[str, float]:
    """Sum WHOLESALE_REVENUE rows from the GL per month.

    Reuses _build_history_pipeline from _gl_helpers so the date/basis
    semantics match every other GL aggregation in this package - no risk
    of date-format drift between modules. Revenue is credit-balance in
    AEGISAXIOM GL (negative ``amount``); we sign-flip so totals come out
    positive, mirroring load_revenue_history.

    Returns ``{month: total_wholesale_revenue}`` keyed by every month in
    history_months. Months with no wholesale rows return 0.0.
    """
    if not history_months:
        return {}

    pipeline = _build_history_pipeline(history_months, basis)
    rows = await qbo_db.general_ledger.aggregate(pipeline).to_list(
        length=_GL_QUERY_LIMIT
    )
    if len(rows) >= _GL_QUERY_LIMIT:
        logger.warning(
            "GL aggregation in detect_wholesale_history_from_gl hit length cap "
            "of %d rows; result may be truncated.",
            _GL_QUERY_LIMIT,
        )

    buckets: dict[str, float] = {m: 0.0 for m in history_months}
    for row in rows:
        month = row["_id"]["month"]
        if month not in buckets:
            continue
        account_name = row["_id"]["account_name"]
        if categorize(account_name) != "WHOLESALE_REVENUE":
            continue
        amount = row.get("amount", 0.0)
        # Same sign convention as load_revenue_history: GL `amount` is
        # natural-balance signed (positive on revenue accounts), no flip.
        buckets[month] += amount
    return buckets


# ---------------------------------------------------------------------------
# Auto-fill assumption gate
# ---------------------------------------------------------------------------


# Confidence threshold for auto-populating projection inputs. Wholesale
# in only 1 of 3 history months is too sparse to seed a recurring
# assumption from - the customer may have churned or it could be a
# one-off transfer. 2+ months means there's a pattern.
MIN_MONTHS_FOR_AUTO_FILL = 2

# Marker prefix used to identify an auto-detected entry in
# manual_recurring_revenue. The wrapper in data_loader looks for this
# prefix when merging caller-supplied scenario state so auto-detection
# survives chat-tool round trips.
AUTO_DETECTED_NAME_PREFIX = "[auto-detected]"
AUTO_DETECTED_WHOLESALE_NAME = f"{AUTO_DETECTED_NAME_PREFIX} wholesale revenue (GL 50400)"


def compute_auto_wholesale_assumption(
    monthly_wholesale: dict[str, float],
    history_months: list[str],
    *,
    starts_month: str | None = None,
) -> RecurringRevenueAssumption | None:
    """Return a wholesale RecurringRevenueAssumption when GL signal is
    consistent across history; otherwise None.

    "Consistent" = at least MIN_MONTHS_FOR_AUTO_FILL of the history
    months have a positive wholesale total AND the average across the
    full history window is > $0. The amount used is the average across
    every history month (including zeros), which matches how the engine
    treats other recurring assumptions.

    starts_month, when provided, becomes the assumption's start month so
    the engine applies it to the projection horizon. None means the
    assumption is open-ended at both ends.
    """
    if not monthly_wholesale or not history_months:
        return None

    months_with_signal = sum(
        1 for m in history_months if monthly_wholesale.get(m, 0.0) > 0
    )
    if months_with_signal < MIN_MONTHS_FOR_AUTO_FILL:
        return None

    total = sum(monthly_wholesale.get(m, 0.0) for m in history_months)
    avg = total / len(history_months)
    if avg <= 0:
        return None

    return RecurringRevenueAssumption(
        name=AUTO_DETECTED_WHOLESALE_NAME,
        amount_per_month=round(avg, 2),
        line_type="WHOLESALE",
        starts_month=starts_month,
        ends_month=None,
        weekly_dist=None,
    )


def is_auto_detected(assumption: RecurringRevenueAssumption) -> bool:
    """True when the assumption was injected by detect-and-autofill.

    Used by the wrapper in data_loader to decide whether to preserve a
    given recurring-revenue entry across the caller-supplied scenario
    splice.
    """
    return bool(
        assumption.name
        and assumption.name.startswith(AUTO_DETECTED_NAME_PREFIX)
    )


__all__ = [
    "classify_record",
    "detect_wholesale_history_from_gl",
    "compute_auto_wholesale_assumption",
    "is_auto_detected",
    "MIN_MONTHS_FOR_AUTO_FILL",
    "AUTO_DETECTED_WHOLESALE_NAME",
    "AUTO_DETECTED_NAME_PREFIX",
]
