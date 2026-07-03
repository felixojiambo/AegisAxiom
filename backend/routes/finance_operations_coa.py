"""Finance FCC — Read-only Operations routes (CoA + Invoices + Bills).

Provides:
  GET /operations/coa?active_only=true&kind=
  GET /operations/invoices?search=&bucket=&location=&class_name=&limit=&skip=
  GET /operations/bills?search=&open_only=&location=&class_name=&limit=&skip=

All routes are read-only (QBO data lake source). The writable CoA-mapping
entity (/coa-mappings) is owned by Thread 2A (finance_coa_mapping.py) and
is NOT touched here.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import register_module_events
from services.nova_engine.db import get_qbo_db
from .finance_statements import _get_report, _line_items, to_float

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_events("finance", "coa", [])
register_module_events("finance", "invoices", [])
register_module_events("finance", "bills", [])

_PERM = "finance.coa.view"
_INVOICES_PERM = "finance.ar.view"
_BILLS_PERM = "finance.ap.view"


# ---------------------------------------------------------------------------
# GET /operations/coa
# ---------------------------------------------------------------------------

@router.get(
    "/operations/coa",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def get_coa(
    active_only: bool = True,
    kind: Optional[str] = None,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Return QBO Chart of Accounts. Optionally filter to active records and/or classification kind."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, _PERM):
        raise HTTPException(status_code=403, detail=f"{_PERM} permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()

    q: Dict[str, Any] = {}
    if active_only:
        q["active"] = True
    if kind:
        q["classification"] = kind

    cursor = qbo_db["chart_of_accounts"].find(q, {"_id": 0}).sort([("name", 1)])
    docs = await cursor.to_list(length=500)
    return {"rows": docs, "total": len(docs)}


# ---------------------------------------------------------------------------
# GET /operations/invoices
# Source spec § 5 — open AR invoices joined from reports_ar_aging_detail with
# class + location pulled from ar_invoices.raw_data (line ClassRef + DepartmentRef).
# ---------------------------------------------------------------------------

@router.get(
    "/operations/invoices",
    dependencies=[Depends(require_ai_permission(_INVOICES_PERM))],
)
async def list_invoices(
    search: str = "",
    bucket: Optional[str] = None,
    location: Optional[str] = None,
    class_name: Optional[str] = None,
    limit: int = 100,
    skip: int = 0,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, _INVOICES_PERM):
        raise HTTPException(status_code=403, detail=f"{_INVOICES_PERM} permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()
    detail = await _get_report("reports_ar_aging_detail")
    items = _line_items(detail)

    open_doc_nums = {str(li.get("Num")) for li in items if li.get("Num")}
    class_map: Dict[str, str] = {}
    location_map: Dict[str, str] = {}
    if open_doc_nums:
        async for inv in qbo_db["ar_invoices"].find(
            {"raw_data.DocNumber": {"$in": list(open_doc_nums)}},
            {"_id": 0, "raw_data.DocNumber": 1, "raw_data.Line": 1, "raw_data.DepartmentRef": 1},
        ):
            raw = inv.get("raw_data") or {}
            dn = str(raw.get("DocNumber") or "")
            if not dn:
                continue
            for line in raw.get("Line") or []:
                cls = ((line or {}).get("SalesItemLineDetail") or {}).get("ClassRef") or {}
                if cls.get("name"):
                    class_map[dn] = cls["name"]
                    break
            dept = raw.get("DepartmentRef") or {}
            if dept.get("name"):
                location_map[dn] = dept["name"]

    rows = []
    for li in items:
        if li.get("is_summary"):
            continue
        cust = li.get("Customer") or ""
        bk = str(li.get("section") or "")
        num = str(li.get("Num") or "")
        cls_v = class_map.get(num)
        loc_v = location_map.get(num)
        if search and search.lower() not in cust.lower() and search.lower() not in num.lower():
            continue
        if bucket and bucket.lower() not in bk.lower():
            continue
        if class_name and (cls_v or "") != class_name:
            continue
        if location and (loc_v or "") != location:
            continue
        rows.append(
            {
                "id": f"{li.get('Transaction Type_id')}",
                "date": li.get("Date"),
                "txn_type": li.get("Transaction Type"),
                "num": num,
                "customer_id": str(li.get("Customer_id")) if li.get("Customer_id") else None,
                "customer": cust,
                "class_name": cls_v,
                "location": loc_v,
                "due_date": li.get("Due Date"),
                "amount": to_float(li.get("Amount")),
                "open_balance": to_float(li.get("Open Balance")),
                "bucket": bk,
                "sync_status": "Synced",
            }
        )
    rows.sort(key=lambda x: x["date"] or "", reverse=True)
    total = len(rows)
    return {"rows": rows[skip : skip + limit], "total": total}


# ---------------------------------------------------------------------------
# GET /operations/bills
# Source spec § 5 — AP bills with class + location from raw_data line items
# and top-level DepartmentRef.
# ---------------------------------------------------------------------------

@router.get(
    "/operations/bills",
    dependencies=[Depends(require_ai_permission(_BILLS_PERM))],
)
async def list_bills(
    search: str = "",
    location: Optional[str] = None,
    class_name: Optional[str] = None,
    limit: int = 100,
    skip: int = 0,
    open_only: bool = False,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, _BILLS_PERM):
        raise HTTPException(status_code=403, detail=f"{_BILLS_PERM} permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()
    q: Dict[str, Any] = {}
    if open_only:
        q["balance"] = {"$gt": 0}
    if class_name:
        q["raw_data.Line.AccountBasedExpenseLineDetail.ClassRef.name"] = class_name
    if location:
        q["raw_data.DepartmentRef.name"] = location

    cursor = qbo_db["ap_bills"].find(q, {"_id": 0}).sort([("due_date", -1)]).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)
    total = await qbo_db["ap_bills"].count_documents(q)

    rows = []
    for d in docs:
        raw = d.get("raw_data") or {}
        vendor = (raw.get("VendorRef") or {}).get("name") if isinstance(raw.get("VendorRef"), dict) else None
        amount = to_float(raw.get("TotalAmt") or raw.get("Balance"))
        bill_class = None
        for line in raw.get("Line") or []:
            cls = ((line or {}).get("AccountBasedExpenseLineDetail") or {}).get("ClassRef") or {}
            if cls.get("name"):
                bill_class = cls["name"]
                break
        bill_loc = (raw.get("DepartmentRef") or {}).get("name")
        if search:
            if (
                search.lower() not in str(vendor or "").lower()
                and search.lower() not in str(raw.get("DocNumber", "")).lower()
            ):
                continue
        rows.append(
            {
                "id": d.get("bill_id"),
                "doc_num": raw.get("DocNumber"),
                "vendor": vendor,
                "txn_date": raw.get("TxnDate"),
                "due_date": d.get("due_date") or raw.get("DueDate"),
                "amount": amount,
                "balance": to_float(d.get("balance") or raw.get("Balance")),
                "class_name": bill_class,
                "location": bill_loc,
                "memo": raw.get("PrivateNote"),
                "sync_status": "Synced",
            }
        )
    return {"rows": rows, "total": total}
