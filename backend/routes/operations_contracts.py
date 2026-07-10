"""Operations namespace — contract financial analytics.

Lifted from backend/routes/finance.py::/contracts (lines 743-850) as part of the
operations spinout (Wave 2G).

Route prefix: /api/operations  →  full paths served by this module:
  GET /api/operations/contracts/financials   (replaces GET /api/finance/contracts)
  GET /api/operations/contracts/summary      (replaces GET /api/finance/contracts/summary)

DO NOT touch:
  - backend/routes/finance.py          (Thread 3A handles deprecation)
  - backend/routes/contracts.py        (separate full-CRUD module, left as-is)
  - backend/services/contract_financials.py  (Thread 3A handles cleanup)

Permissions:
  operations.access           — base namespace access
  operations.contracts.view   — required for all endpoints in this module
  * (admin wildcard)          — always overrides
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_contract_filter, get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from services.field_registry import register_fields, register_module_config, register_module_events
from services.field_options import CONTRACT_STATUSES, CONTRACT_TYPES
from services.operations.contract_financials import calculate_contract_financials  # noqa: F401 (imported for re-use in recalc if needed)

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Field / module registration
# ---------------------------------------------------------------------------

register_module_config("operations", "contracts", "contract_id", "Operations")
register_module_events("operations", "contract", ["created", "updated", "deleted"])

register_fields("operations", "contract", [
    {
        "name": "contract_id",
        "type": "string",
        "label": "Contract ID",
        "description": "Primary key — operations namespace canonical ID.",
    },
    {
        "name": "status",
        "type": "string",
        "label": "Contract Status",
        "options": CONTRACT_STATUSES,
        "description": "Current contract lifecycle status.",
    },
    {
        "name": "contract_type",
        "type": "string",
        "label": "Contract Type",
        "options": CONTRACT_TYPES,
        "description": "Billing model type (BOH, BOA, fixed_fee, monthly, etc.).",
    },
    {
        "name": "account_id",
        "type": "string",
        "label": "Account ID",
        "description": "FK to accounts collection.",
    },
    {
        "name": "account_name",
        "type": "string",
        "label": "Account Name",
        "description": "Denormalised display name of the client account.",
    },
    {
        "name": "ar_summary",
        "type": "object",
        "label": "AR Summary",
        "description": "Accounts Receivable summary: total_contract_value, invoiced_to_date, outstanding, etc.",
    },
    {
        "name": "ap_summary",
        "type": "object",
        "label": "AP Summary",
        "description": "Accounts Payable summary: total_cost_estimate, billed_to_date, outstanding, resource_breakdown.",
    },
    {
        "name": "margin_summary",
        "type": "object",
        "label": "Margin Summary",
        "description": "Profitability: gross_margin_dollars, gross_margin_percent, margin_health (excellent/good/fair/low/negative).",
    },
    {
        "name": "resources",
        "type": "array",
        "label": "Resources",
        "description": "Contractors assigned to this contract, each with cost_rate and resource_type_id.",
    },
    {
        "name": "resource_types",
        "type": "array",
        "label": "Resource Types",
        "description": "Billing categories (e.g. Senior Engineer) with sell_rate and hours_purchased.",
    },
    {
        "name": "deal_ids",
        "type": "array",
        "label": "Deal IDs",
        "description": "Closed-Won deals linked to this contract.",
    },
    {
        "name": "start_date",
        "type": "date",
        "label": "Start Date",
    },
    {
        "name": "end_date",
        "type": "date",
        "label": "End Date",
        "description": "Null for BOH contracts (no fixed end date).",
    },
    {
        "name": "created_at",
        "type": "datetime",
        "label": "Created At",
    },
    {
        "name": "updated_at",
        "type": "datetime",
        "label": "Updated At",
    },
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_view(user) -> None:
    """Raise 403 if the user cannot read operations contract data."""
    if not (has_perm(user, "operations.contracts.view") or has_perm(user, "*")):
        raise HTTPException(
            status_code=403,
            detail="operations.contracts.view permission required",
        )


async def _compose_with_visibility(current_user, base: Optional[dict] = None) -> dict:
    """Compose the per-route base predicate with the same caller-visibility
    filter /api/contracts uses (``get_contract_filter``).

    The previous behaviour issued ``find({})`` / ``find({"is_test_data":
    {"$ne": True}})`` against the contracts collection — those queries
    aggregated AR/AP/margin numbers and hour-health rows across every
    contract in the database, regardless of which contracts the caller
    could see via /api/contracts. Composing the visibility filter at
    the BASE of the query keeps the operations surfaces in sync with
    the canonical list endpoint, so a sales rep whose /api/contracts
    list returns N rows also sees those exact N rows here.

    Returns the merged Mongo predicate. ``$and`` composition matches
    routes/contracts.py:get_contracts so a ``$or`` returned by
    ``get_contract_filter`` (sales rep / agent path) stays well-formed
    after merging.
    """
    filt = await get_contract_filter(current_user)
    base = dict(base) if base else {}
    if not filt or filt == {}:
        return base
    if not base:
        return dict(filt)
    return {"$and": [base, filt]}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/contracts/financials",
    dependencies=[Depends(require_ai_permission("operations.contracts.view"))],
    summary="Operations contract analytics (financial view)",
)
async def get_operations_contract_financials(
    status: Optional[str] = None,
    margin_health: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """
    Return all contracts with AR/AP/Margin financial summaries.

    Replaces the legacy GET /api/finance/contracts endpoint.
    Read-only analytics view — no state mutations.

    Query params:
      status       — filter by contract.status (e.g. 'active', 'completed')
      margin_health — filter by margin_summary.margin_health
                      (excellent | good | fair | low | negative)

    Response shape:
      {
        "contracts": [...],
        "summary": {
          "total_contracts": int,
          "total_ar": float,
          "total_ap": float,
          "total_margin_dollars": float,
          "avg_margin_percent": float,
          "health_counts": { "excellent": int, "good": int, ... }
        },
        "top_performers":    [...],   # top 5 by gross_margin_percent
        "bottom_performers": [...],   # bottom 5
        "at_risk":           [...]    # margin_health in [low, negative]
      }
    """
    if not (
        has_perm(current_user, "operations.contracts.view")
        or has_perm(current_user, "*")
    ):
        raise HTTPException(
            status_code=403,
            detail="operations.contracts.view permission required",
        )

    db = get_db()

    query: dict = {}
    if status:
        query["status"] = status

    contracts = await db.contracts.find(query, {"_id": 0}).to_list(10000)

    # Optional filter on computed margin_health
    if margin_health:
        contracts = [
            c for c in contracts
            if c.get("margin_summary", {}).get("margin_health") == margin_health
        ]

    # Aggregate totals
    total_ar = sum(c.get("ar_summary", {}).get("total_contract_value", 0) for c in contracts)
    total_ap = sum(c.get("ap_summary", {}).get("total_cost_estimate", 0) for c in contracts)
    total_margin_dollars = total_ar - total_ap
    avg_margin_percent = (total_margin_dollars / total_ar * 100) if total_ar > 0 else 0

    health_counts: dict = {
        "excellent": 0,
        "good": 0,
        "fair": 0,
        "low": 0,
        "negative": 0,
    }
    for c in contracts:
        health = c.get("margin_summary", {}).get("margin_health")
        if health in health_counts:
            health_counts[health] += 1

    contracts_with_margin = [
        c for c in contracts
        if c.get("margin_summary", {}).get("gross_margin_percent") is not None
    ]
    sorted_by_margin = sorted(
        contracts_with_margin,
        key=lambda c: c.get("margin_summary", {}).get("gross_margin_percent", 0),
        reverse=True,
    )

    return {
        "contracts": contracts,
        "summary": {
            "total_contracts": len(contracts),
            "total_ar": round(total_ar, 2),
            "total_ap": round(total_ap, 2),
            "total_margin_dollars": round(total_margin_dollars, 2),
            "avg_margin_percent": round(avg_margin_percent, 1),
            "health_counts": health_counts,
        },
        "top_performers": sorted_by_margin[:5],
        "bottom_performers": sorted_by_margin[-5:],
        "at_risk": [
            c for c in contracts
            if c.get("margin_summary", {}).get("margin_health") in ("low", "negative")
        ],
    }


def _contract_total_hours(c: dict) -> float | None:
    """Operations-facing total-hours resolver. Matches the Contract
    Financials UI field-fallback chain:
        total_hours -> total_hours_purchased -> imported_total_hours
        -> imported_total_hours_alt.
    Returns None when no usable hour value exists (Fixed Fee / BOA
    contracts without hour data — the UI shows N/A for them)."""
    for key in ("total_hours", "total_hours_purchased",
                "imported_total_hours", "imported_total_hours_alt"):
        v = c.get(key)
        try:
            f = float(v)
            if f > 0:
                return f
        except (TypeError, ValueError):
            continue
    return None


def _contract_remaining_hours(c: dict) -> float | None:
    for key in ("remaining_hours", "imported_remaining_hours"):
        v = c.get(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _contract_consumed_hours(c: dict) -> float | None:
    for key in ("hours_consumed", "imported_consumed_hours"):
        v = c.get(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _contract_consumed_percent(c: dict) -> float | None:
    for key in ("consumed_hours_percent", "imported_consumed_percent"):
        v = c.get(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


# Hour-based contract type set — these are the contracts that
# bill by hour consumption. A contract in this set MUST be
# classified as missing_data if we don't have enough hour signal
# to prove healthy/warning/over_consumed.
_BLOCK_DEBIT_TYPES = frozenset({"boh", "boh_with_boa", "boa", "other"})

# Non-hour-consumption contract types — they don't have a "block
# of hours" lifecycle so n_a is the correct answer regardless of
# whether hour fields happen to be populated.
_NON_HOUR_TYPES = frozenset({
    "fixed_fee", "monthly", "wholesale_mrr",
    "simplisecure", "simplicomply",
})


def _classify_hour_health(c: dict) -> str:
    """Hour-based contract health (May 25, 2026 — reconciliation
    fix following the May 24 investigation report).

    Returns exactly one of:

      "over_consumed" — remaining_hours < 0 (block exhausted)
      "warning"       — consumed_hours_percent >= 90 (within block)
      "healthy"       — block-type contract with at least one
                        consumption signal (consumed / remaining /
                        percent) below the warning threshold
      "missing_data"  — block-type contract but the consumption
                        signals needed to classify it as healthy
                        are missing. Two sub-cases collapse into
                        this bucket:
                          (a) only total_hours is set, with no
                              consumed / percent / remaining
                              (R4 fix — previously misclassified
                              as healthy);
                          (b) no hour fields at all (the operator
                              needs to chase Finance about this
                              contract).
      "n_a"           — contract type isn't hour-consumption-based
                        (fixed_fee, monthly, wholesale_mrr,
                        simplisecure, simplicomply).
      "unknown"       — contract type isn't in either known set
                        AND we have no consumption signal. This
                        is the bucket that catches typos / future
                        types — we deliberately don't guess.

    Every contract is classified into exactly one bucket. The
    summary's `health_counts` therefore always sums to the
    filtered total — see `test_health_counts_sum_to_total`.
    """
    ctype = (c.get("contract_type") or "").strip().lower()

    if ctype in _NON_HOUR_TYPES:
        return "n_a"

    remaining = _contract_remaining_hours(c)
    if remaining is not None and remaining < 0:
        return "over_consumed"

    pct = _contract_consumed_percent(c)
    if pct is not None and pct >= 90:
        return "warning"

    consumed = _contract_consumed_hours(c)

    if ctype in _BLOCK_DEBIT_TYPES:
        # Block-debit type: need at least one consumption signal
        # to claim "healthy". Lone total_hours is NOT enough — the
        # contract is set up but nobody is tracking usage, which
        # the operator needs to know about. That's the R4 fix.
        if pct is not None or remaining is not None or consumed is not None:
            return "healthy"
        return "missing_data"

    # Type isn't in either known set. If we have any consumption
    # signal at all, give it the benefit of the doubt as healthy.
    # Otherwise unknown.
    if pct is not None or remaining is not None or consumed is not None:
        return "healthy"
    return "unknown"


# Wire-shape constants for the API contract. Both the route and
# the tests import these so a typo can't sneak through.
_ALL_HEALTH_BUCKETS = (
    "over_consumed",
    "warning",
    "healthy",
    "missing_data",
    "unknown",
    "n_a",
)

ALLOWED_PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 50


def _coerce_page(value) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def _coerce_page_size(value) -> int:
    """Snap to an allowed page size, defaulting to 50. Out-of-set
    values fall back to DEFAULT_PAGE_SIZE so the response shape
    stays deterministic and pagination math is reliable."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return size if size in ALLOWED_PAGE_SIZES else DEFAULT_PAGE_SIZE


@router.get(
    "/contracts/summary",
    dependencies=[Depends(require_ai_permission("operations.contracts.view"))],
    summary="Operations contract financial quick-summary",
)
async def get_operations_contracts_summary(
    current_user=Depends(get_current_user),
):
    """
    Quick summary of contract financials — suitable for dashboard overview widgets.

    Replaces the legacy GET /api/finance/contracts/summary endpoint.
    Response shape is identical to the legacy endpoint so cutover is clean.

    Response shape:
      {
        "total_contracts":    int,
        "total_ar":           float,
        "total_ap":           float,
        "total_margin":       float,
        "ar_outstanding":     float,
        "ap_outstanding":     float,
        "avg_margin_percent": float
      }
    """
    if not (
        has_perm(current_user, "operations.contracts.view")
        or has_perm(current_user, "*")
    ):
        raise HTTPException(
            status_code=403,
            detail="operations.contracts.view permission required",
        )

    db = get_db()
    # Caller-visibility scope first; without it ``find({})`` summed
    # AR/AP/margin across the entire contracts collection and exposed
    # cross-tenant financial totals through this widget.
    query = await _compose_with_visibility(current_user)
    contracts = await db.contracts.find(query, {"_id": 0}).to_list(10000)

    total_ar = sum(c.get("ar_summary", {}).get("total_contract_value", 0) for c in contracts)
    total_ap = sum(c.get("ap_summary", {}).get("total_cost_estimate", 0) for c in contracts)
    ar_outstanding = sum(c.get("ar_summary", {}).get("outstanding", 0) for c in contracts)
    ap_outstanding = sum(c.get("ap_summary", {}).get("outstanding", 0) for c in contracts)

    return {
        "total_contracts": len(contracts),
        "total_ar": round(total_ar, 2),
        "total_ap": round(total_ap, 2),
        "total_margin": round(total_ar - total_ap, 2),
        "ar_outstanding": round(ar_outstanding, 2),
        "ap_outstanding": round(ap_outstanding, 2),
        "avg_margin_percent": round(
            (total_ar - total_ap) / total_ar * 100, 1
        ) if total_ar > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Hours-based Contract Financials (May 24, 2026 — replaces the placeholder
# OperationsContractsPage). Uses the hydrated Zoho contract fields populated
# by 14a78a8c.
# ---------------------------------------------------------------------------


@router.get(
    "/contracts/hours",
    dependencies=[Depends(require_ai_permission("operations.contracts.view"))],
    summary="Hour-based contract financials (Contract Financials page)",
)
async def get_operations_contracts_hours(
    q: Optional[str] = None,
    contract_type: Optional[str] = None,
    status: Optional[str] = None,
    health: Optional[str] = None,
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """API-response-paginated hour-based contract financials for
    the Contract Financials UI.

    Read-only. Never mutates DB. Never touches resources /
    resource_types / hourly_rate / project_ids / project links.

    Pagination scope (be precise so the next reader doesn't
    misread this):

      The page / page_size params slice the RESPONSE — they do
      NOT push down into a Mongo `.skip().limit()` cursor. The
      route still reads the filtered contract set into memory
      via `to_list(20000)` because `hour_health` is computed
      from a four-field fallback chain plus a contract-type
      branch (`_classify_hour_health`). Pushing that into a
      Mongo aggregation pipeline is a larger refactor than this
      task scopes.

      Tenant-scale today is < ~100 contracts so the response-
      level slice is the right cost/complexity trade. If the
      contract count grows past a few thousand, the right next
      step is to denormalise `hour_health` as a stored field on
      the contract document (recomputed on write) and switch to
      a real DB-level paginated query.

    Filter / pagination order (locked — out-of-order processing
    would break totals or hide rows):

      1. Base Mongo query: is_test_data != true, plus the cheap
         exact-match filters (contract_type, status).
      2. total_unfiltered counts the baseline set BEFORE q /
         health are applied. That's the "this tenant has N
         contracts overall" number.
      3. q (case-insensitive substring over contract_number /
         name / account_name) filters in Python — searches three
         alternate fields, cheaper here than a Mongo $or $regex
         at tenant scale.
      4. Per-row classification (`_classify_hour_health`) and
         hour resolution.
      5. health filter (post-classification — hour_health is
         computed, not stored).
      6. `total` and `summary` are computed against the FILTERED
         set (steps 1-5). NOT against the current page.
      7. RESPONSE slice: page * page_size, applied last.

    Pagination params:
      page         1-indexed; values < 1, non-numeric, or
                   missing all clamp to 1. Declared as
                   Optional[str] so FastAPI doesn't 422 on
                   `?page=abc` before our coercer runs — the
                   coercer snaps invalid values to defaults so
                   typos in a query string never break the page.
      page_size    one of {25, 50, 100}; anything else (including
                   non-numeric or out-of-set values like 33) snaps
                   to 50. Same Optional[str] treatment as page.

    Filters:
      q              substring (case-insensitive) over
                     contract_number / name / account_name
      contract_type  exact match (boh / boh_with_boa / boa /
                     fixed_fee / other / ...)
      status         exact match against contract.status
      health         one of over_consumed / warning / healthy /
                     missing_data / unknown / n_a

    Response shape (May 25, 2026 reshape):
      {
        "items": [...current page only...],
        "summary": {
          "total_contracts":         int,   # = total (filtered)
          "purchased_hours":         float, # Σ total_hours,    None skipped
          "consumed_hours":          float, # Σ consumed_hours, None skipped
          "remaining_hours":         float, # Σ remaining_hours,None skipped
          "derived_remaining_hours": float, # = purchased - consumed
          "health_counts": {
            "over_consumed": int, "warning": int, "healthy": int,
            "missing_data":  int, "unknown": int, "n_a":     int
          },
          # Backward-compat aliases (also exposed at the top of
          # the summary dict so old callers still work):
          "over_consumed", "warning", "healthy",
          "missing_data", "unknown", "n_a",
          "total_purchased_hours", "total_consumed_hours",
          "total_remaining_hours", "missing_financial_data"
        },
        "page":             int,           # echoed back, clamped
        "page_size":        int,           # echoed back, snapped
        "total":            int,           # filtered total (= total_contracts)
        "total_unfiltered": int,           # tenant baseline before q/health
        "filters":          {<echoed args>}
      }

    The sum of health_counts.values() equals total. Every
    filtered contract belongs to exactly one bucket.
    """
    if not (
        has_perm(current_user, "operations.contracts.view")
        or has_perm(current_user, "*")
    ):
        raise HTTPException(
            status_code=403,
            detail="operations.contracts.view permission required",
        )

    db = get_db()

    # --- Step 1: base Mongo query ---
    # Order locked: caller visibility (same helper /api/contracts uses)
    # FIRST, then the cheap exact-match filters. Without the visibility
    # composition this endpoint paged rows the caller could not see via
    # /api/contracts and counted them into total / total_unfiltered /
    # health_counts. Composing here means a sales rep whose contracts
    # list returns N rows sees those exact N rows on Contract
    # Financials too.
    base: dict = {"is_test_data": {"$ne": True}}
    if contract_type:
        base["contract_type"] = contract_type
    if status:
        base["status"] = status
    query = await _compose_with_visibility(current_user, base=base)

    raw = await db.contracts.find(query, {"_id": 0}).to_list(20000)

    # --- Step 2: total_unfiltered (after type/status, before q/health) ---
    # Type/status filters are part of the base "what set are we
    # looking at" question — applying them changes the meaning of
    # "unfiltered" only for the user-friendly q/health filters,
    # which is what the UI bar exposes as the live filter inputs.
    total_unfiltered = len(raw)

    # --- Step 3: q filter ---
    if q:
        needle = q.strip().lower()
        if needle:
            raw = [
                c for c in raw
                if needle in (c.get("contract_number") or "").lower()
                or needle in (c.get("name") or "").lower()
                or needle in (c.get("account_name") or "").lower()
            ]

    # --- Step 4 + 5: classify + health filter (computed bucket
    # cannot be a Mongo filter). Build the per-row item AND the
    # parallel filtered-rows list in one pass. ---
    filtered_rows = []
    for c in raw:
        h = _classify_hour_health(c)
        if health and h != health:
            continue
        total_h     = _contract_total_hours(c)
        remaining_h = _contract_remaining_hours(c)
        consumed_h  = _contract_consumed_hours(c)
        pct         = _contract_consumed_percent(c)
        filtered_rows.append({
            "contract_id":         c.get("contract_id"),
            "contract_number":     c.get("contract_number"),
            "name":                c.get("name"),
            "account_name":        c.get("account_name"),
            "contract_type":       c.get("contract_type"),
            "contract_type_raw":   c.get("contract_type_raw"),
            "total_hours":         total_h,
            "remaining_hours":     remaining_h,
            "consumed_hours":      consumed_h,
            "consumed_hours_percent": pct,
            "deal_name":           c.get("deal_name"),
            "invoice_type_raw":    c.get("invoice_type_raw"),
            "contract_status_raw": c.get("contract_status_raw"),
            "status":              c.get("status"),
            "ap_issues_raw":       c.get("ap_issues_raw"),
            "number_of_invoices":  c.get("number_of_invoices"),
            "bill_on_actual_hours": c.get("bill_on_actual_hours"),
            "hours_to_be_paid":    c.get("hours_to_be_paid"),
            "paid_hours":          c.get("paid_hours"),
            "source_created_by":   c.get("source_created_by"),
            "source_created_at":   c.get("source_created_at"),
            "created_at":          c.get("created_at"),
            "hour_health":         h,
        })

    # --- Step 6: filtered total + summary ---
    total = len(filtered_rows)
    health_counts = {bucket: 0 for bucket in _ALL_HEALTH_BUCKETS}
    purchased_sum = 0.0
    consumed_sum = 0.0
    remaining_sum = 0.0
    for row in filtered_rows:
        health_counts[row["hour_health"]] += 1
        if row["total_hours"] is not None:
            purchased_sum += row["total_hours"]
        if row["consumed_hours"] is not None:
            consumed_sum += row["consumed_hours"]
        if row["remaining_hours"] is not None:
            remaining_sum += row["remaining_hours"]

    purchased_sum = round(purchased_sum, 2)
    consumed_sum = round(consumed_sum, 2)
    remaining_sum = round(remaining_sum, 2)
    derived_remaining = round(purchased_sum - consumed_sum, 2)

    # New canonical summary shape. Backward-compat keys are mirrored
    # at the top level so older callers that read summary["over_consumed"]
    # or summary["total_purchased_hours"] still work without change.
    summary = {
        # Canonical (May 25 reshape)
        "total_contracts":         total,
        "purchased_hours":         purchased_sum,
        "consumed_hours":          consumed_sum,
        "remaining_hours":         remaining_sum,
        "derived_remaining_hours": derived_remaining,
        "health_counts":           dict(health_counts),
        # Backward-compat mirrors — populated identically so a
        # consumer that switches reads gets the same numbers.
        "over_consumed":           health_counts["over_consumed"],
        "warning":                 health_counts["warning"],
        "healthy":                 health_counts["healthy"],
        "missing_data":            health_counts["missing_data"],
        "unknown":                 health_counts["unknown"],
        "n_a":                     health_counts["n_a"],
        "total_purchased_hours":   purchased_sum,
        "total_consumed_hours":    consumed_sum,
        "total_remaining_hours":   remaining_sum,
        "missing_financial_data":  health_counts["missing_data"],
    }

    # --- Step 7: pagination (always last) ---
    coerced_page = _coerce_page(page)
    coerced_size = _coerce_page_size(page_size)
    start = (coerced_page - 1) * coerced_size
    end = start + coerced_size
    page_items = filtered_rows[start:end]

    return {
        "items":            page_items,
        "summary":          summary,
        "page":             coerced_page,
        "page_size":        coerced_size,
        "total":            total,
        "total_unfiltered": total_unfiltered,
        "filters": {
            "q": q,
            "contract_type": contract_type,
            "status": status,
            "health": health,
        },
    }
