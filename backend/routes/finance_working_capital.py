"""Finance FCC working capital routes.

Provides.
  GET /working-capital/ar-aging
  GET /working-capital/ap-aging
  GET /working-capital/ar-aging-detail?customer_id=
  GET /working-capital/ap-aging-detail?vendor_id=
  GET /working-capital/dso-dpo?months=12
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import register_module_events
from services.nova_engine.account_categories import categorize, is_cost, is_revenue
from services.nova_engine.db import get_qbo_db
from services.nova_engine.formulas import compute_reversal_metadata

from .finance_statements import _get_report, _line_items, safe_doc, to_float

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_events("finance", "working_capital", [])

_PERM = "finance.working_capital.view"


def _check_perm(user_dict: dict) -> None:
    if not has_perm(user_dict, _PERM):
        raise HTTPException(status_code=403, detail=f"{_PERM} permission required")


# ---------------------------------------------------------------------------
# AR / AP aging summaries
# ---------------------------------------------------------------------------

@router.get(
    "/working-capital/ar-aging",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def ar_aging(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_ar_aging")
    return safe_doc(doc) if doc else {"line_items": [], "column_names": []}


@router.get(
    "/working-capital/ap-aging",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def ap_aging(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_ap_aging")
    return safe_doc(doc) if doc else {"line_items": [], "column_names": []}


# ---------------------------------------------------------------------------
# AR / AP aging detail. Optionally filtered to a single customer or vendor.
# ---------------------------------------------------------------------------

@router.get(
    "/working-capital/ar-aging-detail",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def ar_aging_detail(
    customer_id: Optional[str] = None,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_ar_aging_detail")
    if not doc:
        return {"line_items": []}
    items = _line_items(doc)
    if customer_id:
        items = [li for li in items if str(li.get("Customer_id")) == str(customer_id)]
    return {"line_items": items, "column_names": doc.get("column_names", [])}


@router.get(
    "/working-capital/ap-aging-detail",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def ap_aging_detail(
    vendor_id: Optional[str] = None,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_ap_aging_detail")
    if not doc:
        return {"line_items": []}
    items = _line_items(doc)
    if vendor_id:
        items = [li for li in items if str(li.get("Vendor_id")) == str(vendor_id)]
    return {"line_items": items, "column_names": doc.get("column_names", [])}


# ---------------------------------------------------------------------------
# DSO / DPO trend.
# ---------------------------------------------------------------------------

@router.get(
    "/working-capital/dso-dpo",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def dso_dpo(
    months: int = Query(default=12, ge=1, le=60),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Approximate DSO and DPO over the last N months from GL aggregation.

    Returned series carries one row per month within the lookback
    window. Each row reports revenue, expense, DSO, and DPO. The two
    ratios are computed as follows.

      DSO formula. AR_total divided by month_revenue, then multiplied
      by 30. AR_total comes from the current AR aging report.
      DPO formula. AP_total divided by month_expense, then multiplied
      by 30. AP_total comes from the current AP aging report.

    Revenue and expense are classified by GL account category through
    account_categories.categorize and not by txn_type. A row posted to
    an income account contributes signed revenue no matter whether it
    arrived as an Invoice, Bill, Expense, or Journal Entry. A row on a
    true expense or COGS account contributes signed expense. This
    classification matches the Nova canonical rule and prevents
    reversal rows on income accounts from being moved into the expense
    bucket purely because their txn_type is Bill or Expense.

    Reversal handling. Per month revenue amounts feed
    compute_reversal_metadata so the signed total carries the same
    shape used by the other three finance surfaces. A month where
    reversals exceed gross sales lands at a signed_total that is zero
    or negative, the DSO guard rejects non positive revenue, and the
    row reports DSO equal to zero rather than a misleading negative
    number.

    Proxy assumptions. The route uses GL category mapping as a proxy
    for full statement of cash flow analysis. Bookings that bypass
    the income or expense accounts are not captured here.
    """
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()
    today = datetime.now(timezone.utc).date()

    months_list: list[tuple[int, int]] = []
    cur_y, cur_m = today.year, today.month
    for i in range(months):
        y, m = cur_y, cur_m - i
        while m <= 0:
            m += 12
            y -= 1
        months_list.append((y, m))
    months_list.reverse()

    earliest = f"{months_list[0][0]}-{months_list[0][1]:02d}-01"

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
    rows = await qbo_db["general_ledger"].aggregate(pipeline).to_list(length=10000)

    # Per month collection of revenue amounts feeds compute_reversal_metadata
    # after the row loop. Mirrors the pattern in finance_revenue.revenue_trend
    # so the four finance surfaces share one reversal aggregator. The earlier
    # inline accumulation here was the fourth duplicate of that loop and the
    # prior cleanup missed it.
    ym_keys = [f"{y}-{m:02d}" for (y, m) in months_list]
    revenue_amounts: dict[str, list[float]] = {ym: [] for ym in ym_keys}
    expense_totals: dict[str, float] = {ym: 0.0 for ym in ym_keys}

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

    ar_doc = await _get_report("reports_ar_aging")
    ap_doc = await _get_report("reports_ap_aging")
    ar_total = sum(to_float(li.get("Total")) for li in _line_items(ar_doc) if not li.get("is_summary"))
    ap_total = sum(to_float(li.get("Total")) for li in _line_items(ap_doc) if not li.get("is_summary"))

    series = []
    for ym in sorted(revenue_amounts.keys()):
        meta = compute_reversal_metadata(revenue_amounts[ym])
        revenue_signed = meta["signed_total"]
        expense_signed = expense_totals[ym]
        # DSO guard rejects non positive revenue. A month where reversals
        # exceed gross sales lands at a non positive signed_total and a
        # negative quotient would otherwise leak into the dashboard. The
        # same guard shape applies to DPO for symmetry, since a negative
        # expense bucket is also nonsensical for the days payable ratio.
        dso = (ar_total / revenue_signed * 30) if revenue_signed > 0 else 0.0
        dpo = (ap_total / expense_signed * 30) if expense_signed > 0 else 0.0
        series.append(
            {
                "month": ym,
                "revenue": round(revenue_signed, 2),
                "expense": round(expense_signed, 2),
                "dso": round(dso, 1),
                "dpo": round(dpo, 1),
            }
        )

    return {
        "series": series,
        "ar_total": round(ar_total, 2),
        "ap_total": round(ap_total, 2),
    }
