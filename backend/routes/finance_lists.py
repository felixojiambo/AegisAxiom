"""Finance FCC — Customer & Vendor list routes.

Provides:
  GET /lists/customers?search=&limit=200
  GET /lists/customers/{customer_id}
  GET /lists/vendors?search=&limit=200
  GET /lists/vendors/{vendor_id}

Customer detail joins reports_customer_balance × reports_customer_income ×
reports_ar_aging × reports_ar_aging_detail × contacts (the QBO contacts
collection) — same join logic as the source app.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import register_module_events
from services.nova_engine.db import get_qbo_db

from .finance_statements import _get_report, _line_items, to_float

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_events("finance", "lists", [])

_PERM = "finance.lists.view"


def _check_perm(user_dict: dict) -> None:
    if not has_perm(user_dict, _PERM):
        raise HTTPException(status_code=403, detail=f"{_PERM} permission required")


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

@router.get(
    "/lists/customers",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def list_customers(
    search: str = "",
    limit: int = Query(default=200, ge=1, le=1000),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """List customers with balance and income data joined from two QBO reports."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    cb = await _get_report("reports_customer_balance")
    ci = await _get_report("reports_customer_income")

    income_map: dict[str, dict] = {}
    for li in _line_items(ci):
        if li.get("is_summary"):
            continue
        cid = str(li.get("Customer_id")) if li.get("Customer_id") else li.get("Customer", "")
        income_map[cid] = {
            "income": to_float(li.get("Income")),
            "expenses": to_float(li.get("Expenses")),
            "net_income": to_float(li.get("Net Income")),
        }

    rows = []
    for li in _line_items(cb):
        if li.get("is_summary"):
            continue
        name = li.get("Customer") or li.get("account_name") or ""
        if search and search.lower() not in str(name).lower():
            continue
        cid = str(li.get("Customer_id")) if li.get("Customer_id") else name
        ic = income_map.get(cid, {})
        rows.append(
            {
                "customer_id": cid,
                "name": name,
                "balance": to_float(li.get("Total")),
                "income": ic.get("income", 0.0),
                "expenses": ic.get("expenses", 0.0),
                "net_income": ic.get("net_income", 0.0),
            }
        )

    rows.sort(key=lambda x: x["balance"], reverse=True)
    return {"rows": rows[:limit], "total": len(rows)}


@router.get(
    "/lists/customers/{customer_id}",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def customer_detail(customer_id: str, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Return full detail for a single customer: balance, income, AR aging bucket, open invoices, contact."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()

    cb = await _get_report("reports_customer_balance")
    ci = await _get_report("reports_customer_income")
    aging = await _get_report("reports_ar_aging")
    detail = await _get_report("reports_ar_aging_detail")

    info: dict = {
        "customer_id": customer_id,
        "name": None,
        "balance": 0.0,
        "income": 0.0,
        "expenses": 0.0,
        "net_income": 0.0,
    }

    for li in _line_items(cb):
        if str(li.get("Customer_id")) == str(customer_id):
            info["name"] = li.get("Customer")
            info["balance"] = to_float(li.get("Total"))
            break

    for li in _line_items(ci):
        if str(li.get("Customer_id")) == str(customer_id):
            info["income"] = to_float(li.get("Income"))
            info["expenses"] = to_float(li.get("Expenses"))
            info["net_income"] = to_float(li.get("Net Income"))
            break

    aging_row = None
    for li in _line_items(aging):
        if str(li.get("Customer_id")) == str(customer_id):
            aging_row = {
                "current": to_float(li.get("Current")),
                "d_1_30": to_float(li.get("1 - 30")),
                "d_31_60": to_float(li.get("31 - 60")),
                "d_61_90": to_float(li.get("61 - 90")),
                "d_91_plus": to_float(li.get("91 and over")),
                "total": to_float(li.get("Total")),
            }
            break

    open_invoices = []
    for li in _line_items(detail):
        if str(li.get("Customer_id")) == str(customer_id):
            open_invoices.append(
                {
                    "date": li.get("Date"),
                    "txn_type": li.get("Transaction Type"),
                    "num": li.get("Num"),
                    "due_date": li.get("Due Date"),
                    "amount": to_float(li.get("Amount")),
                    "open_balance": to_float(li.get("Open Balance")),
                    "bucket": li.get("section"),
                }
            )

    contact = await qbo_db["contacts"].find_one(
        {"account_name": info.get("name")}, {"_id": 0}
    )

    return {
        "info": info,
        "aging": aging_row,
        "open_invoices": open_invoices,
        "contact": contact,
    }


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------

@router.get(
    "/lists/vendors",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def list_vendors(
    search: str = "",
    limit: int = Query(default=200, ge=1, le=1000),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """List vendors with balance and YTD expense data joined from two QBO reports."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    vb = await _get_report("reports_vendor_balance")
    ve = await _get_report("reports_vendor_expenses")

    expense_map: dict[str, float] = {}
    for li in _line_items(ve):
        if li.get("is_summary"):
            continue
        vid = str(li.get("Vendor_id")) if li.get("Vendor_id") else li.get("Vendor", "")
        expense_map[vid] = to_float(li.get("Total"))

    rows = []
    for li in _line_items(vb):
        if li.get("is_summary"):
            continue
        name = li.get("Vendor") or li.get("account_name") or ""
        if search and search.lower() not in str(name).lower():
            continue
        vid = str(li.get("Vendor_id")) if li.get("Vendor_id") else name
        rows.append(
            {
                "vendor_id": vid,
                "name": name,
                "balance": to_float(li.get("Total")),
                "ytd_expense": expense_map.get(vid, 0.0),
            }
        )

    rows.sort(key=lambda x: x["balance"], reverse=True)
    return {"rows": rows[:limit], "total": len(rows)}


@router.get(
    "/lists/vendors/{vendor_id}",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def vendor_detail(vendor_id: str, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Return full detail for a single vendor: balance, YTD expense, AP aging bucket, open bills."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    vb = await _get_report("reports_vendor_balance")
    ve = await _get_report("reports_vendor_expenses")
    aging = await _get_report("reports_ap_aging")
    detail = await _get_report("reports_ap_aging_detail")

    info: dict = {"vendor_id": vendor_id, "name": None, "balance": 0.0, "ytd_expense": 0.0}

    for li in _line_items(vb):
        if str(li.get("Vendor_id")) == str(vendor_id):
            info["name"] = li.get("Vendor")
            info["balance"] = to_float(li.get("Total"))
            break

    for li in _line_items(ve):
        if str(li.get("Vendor_id")) == str(vendor_id):
            info["ytd_expense"] = to_float(li.get("Total"))
            break

    aging_row = None
    for li in _line_items(aging):
        if str(li.get("Vendor_id")) == str(vendor_id):
            aging_row = {
                "current": to_float(li.get("Current")),
                "d_1_30": to_float(li.get("1 - 30")),
                "d_31_60": to_float(li.get("31 - 60")),
                "d_61_90": to_float(li.get("61 - 90")),
                "d_91_plus": to_float(li.get("91 and over")),
                "total": to_float(li.get("Total")),
            }
            break

    open_bills = []
    for li in _line_items(detail):
        if str(li.get("Vendor_id")) == str(vendor_id):
            open_bills.append(
                {
                    "date": li.get("Date"),
                    "txn_type": li.get("Transaction Type"),
                    "num": li.get("Num"),
                    "due_date": li.get("Due Date"),
                    "past_due_days": li.get("Past Due"),
                    "amount": to_float(li.get("Amount")),
                    "open_balance": to_float(li.get("Open Balance")),
                    "bucket": li.get("section"),
                }
            )

    return {"info": info, "aging": aging_row, "open_bills": open_bills}
