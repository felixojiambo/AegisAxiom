"""Finance FCC — Revenue Trend & Breakdown routes.

Provides:
  GET /revenue/trend?months=12      — monthly revenue + expense from GL accrual
  GET /revenue/by-customer?limit=15 — top customers by income (from customer-income report)
  GET /revenue/by-vendor?limit=15   — top vendors by expense (from vendor-expenses report)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import register_module_events
from services.nova_engine.account_categories import categorize, is_cost, is_revenue
from services.nova_engine.db import get_qbo_db
from services.nova_engine.formulas import compute_reversal_metadata

from .finance_statements import _get_report, _line_items, to_float

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_events("finance", "revenue", [])

_PERM = "finance.revenue.view"


def _check_perm(user_dict: dict) -> None:
    if not has_perm(user_dict, _PERM):
        raise HTTPException(status_code=403, detail=f"{_PERM} permission required")


# ---------------------------------------------------------------------------
# Revenue trend (GL aggregation)
# ---------------------------------------------------------------------------

@router.get(
    "/revenue/trend",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def revenue_trend(
    months: int = Query(default=12, ge=1, le=60),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Monthly revenue and expense series derived from accrual general_ledger entries.

    Classification is by GL account (via account_categories.categorize), not
    by txn_type. A row posted to a revenue account is revenue with its signed
    amount regardless of whether it arrived as Invoice, Bill, Expense, Journal
    Entry, or any other txn_type — so reversal rows reduce revenue instead of
    being silently dropped or miscategorised as expense.
    """
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()
    today = datetime.now(timezone.utc).date()

    months_list: list[str] = []
    cur_y, cur_m = today.year, today.month
    for i in range(months):
        y, m = cur_y, cur_m - i
        while m <= 0:
            m += 12
            y -= 1
        months_list.append(f"{y}-{m:02d}")
    months_list.reverse()
    earliest = months_list[0] + "-01"

    pipeline = [
        {"$match": {"basis": "accrual", "tx_date": {"$gte": earliest}}},
        {
            "$group": {
                "_id": {
                    "ym": {"$substr": ["$tx_date", 0, 7]},
                    "account_name": "$account_name",
                },
                "amount": {"$sum": "$amount"},
            }
        },
    ]
    rows = await qbo_db["general_ledger"].aggregate(pipeline).to_list(length=20000)

    # K4. Per month collection of revenue amounts feeds the shared
    # compute_reversal_metadata helper after the row loop. Expense is
    # accumulated inline because it is a single signed sum without the
    # positive negative split. The earlier inline reversal math was
    # duplicated across three finance surfaces and bot reviewers
    # flagged the drift risk more than once.
    revenue_amounts: dict[str, list[float]] = {ym: [] for ym in months_list}
    expense_totals: dict[str, float] = {ym: 0.0 for ym in months_list}
    for r in rows:
        ym = r["_id"]["ym"]
        if ym not in revenue_amounts:
            continue
        account_name = r["_id"].get("account_name") or ""
        try:
            amount = float(r.get("amount", 0) or 0)
        except (TypeError, ValueError):
            continue
        category = categorize(account_name)
        if is_revenue(category):
            revenue_amounts[ym].append(amount)
        elif is_cost(category):
            expense_totals[ym] += amount

    # J4. reversal_note lives once on the response envelope. Per row
    # repetition was wasteful and risked becoming a contract callers
    # would lean on. net_total was a redundant alias of revenue and
    # has been dropped per the same review. Downstream readers should
    # use revenue directly.
    reversal_note = (
        "Negative rows posted to income accounts are included as signed "
        "reductions to revenue."
    )
    series = []
    for ym in months_list:
        meta = compute_reversal_metadata(revenue_amounts[ym])
        series.append({
            "month": ym,
            "revenue": round(meta["signed_total"], 2),
            "expense": round(expense_totals[ym], 2),
            "positive_total": round(meta["positive_total"], 2),
            "negative_total": round(meta["negative_total"], 2),
            "negative_row_count": meta["negative_row_count"],
        })
    return {"series": series, "reversal_note": reversal_note}


# ---------------------------------------------------------------------------
# Revenue by customer
# ---------------------------------------------------------------------------

@router.get(
    "/revenue/by-customer",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def revenue_by_customer(
    limit: int = Query(default=15, ge=1, le=500),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Top customers ranked by income, sourced from reports_customer_income."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_customer_income")
    items = _line_items(doc)

    rows = []
    for li in items:
        if li.get("is_summary"):
            continue
        rows.append(
            {
                "customer": li.get("Customer") or li.get("account_name"),
                "customer_id": li.get("Customer_id"),
                "income": to_float(li.get("Income")),
                "expenses": to_float(li.get("Expenses")),
                "net_income": to_float(li.get("Net Income")),
            }
        )
    rows.sort(key=lambda x: x["income"], reverse=True)
    return {"rows": rows[:limit], "total": len(rows)}


# ---------------------------------------------------------------------------
# Expense by vendor
# ---------------------------------------------------------------------------

@router.get(
    "/revenue/by-vendor",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def expense_by_vendor(
    limit: int = Query(default=15, ge=1, le=500),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Top vendors ranked by total spend, sourced from reports_vendor_expenses."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_vendor_expenses")
    items = _line_items(doc)

    rows = []
    for li in items:
        if li.get("is_summary"):
            continue
        rows.append(
            {
                "vendor": li.get("Vendor") or li.get("account_name"),
                "vendor_id": li.get("Vendor_id"),
                "total": to_float(li.get("Total")),
            }
        )
    rows.sort(key=lambda x: x["total"], reverse=True)
    return {"rows": rows[:limit], "total": len(rows)}
