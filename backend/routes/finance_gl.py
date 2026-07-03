"""Finance FCC — General Ledger Explorer routes.

Provides:
  GET /gl                 — paginated GL with optional filters + joined class/location/deal_id
  GET /gl/accounts        — distinct account_name values
  GET /gl/txn-types       — distinct txn_type values
  GET /gl/classes         — distinct class names (from ar_invoices + ap_bills line items)
  GET /gl/locations       — distinct location names (from ar_invoices + ap_bills DepartmentRef)

Filters are applied server-side. Class and location filters require a two-pass
join against ar_invoices/ap_bills to resolve doc_nums, then filter the GL query
by those doc_nums. This preserves the source-app logic verbatim.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import register_module_events
from services.nova_engine.db import get_qbo_db

from .finance_statements import safe_doc

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_events("finance", "gl", [])

_PERM = "finance.gl.view"
_GL_DEFAULT_LIMIT = 100
_GL_MAX_LIMIT = 1000


def _check_perm(user_dict: dict) -> None:
    if not has_perm(user_dict, _PERM):
        raise HTTPException(status_code=403, detail=f"{_PERM} permission required")


# ---------------------------------------------------------------------------
# GL Explorer
# ---------------------------------------------------------------------------

@router.get(
    "/gl",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def gl_explorer(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account: Optional[str] = None,
    txn_type: Optional[str] = None,
    location: Optional[str] = None,
    class_name: Optional[str] = None,
    search: Optional[str] = None,
    basis: str = Query(default="accrual", pattern="^(accrual|cash)$"),
    limit: int = Query(default=_GL_DEFAULT_LIMIT, ge=1, le=_GL_MAX_LIMIT),
    skip: int = Query(default=0, ge=0),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()

    q: Dict[str, Any] = {"basis": basis}
    if date_from:
        q.setdefault("tx_date", {})["$gte"] = date_from
    if date_to:
        q.setdefault("tx_date", {})["$lte"] = date_to
    if account:
        q["account_name"] = {"$regex": re.escape(account), "$options": "i"}
    if txn_type:
        q["txn_type"] = txn_type

    # -- Class filter: resolve doc_nums from invoice/bill line ClassRef --
    if class_name:
        class_doc_nums: List[str] = []
        async for inv in qbo_db["ar_invoices"].find(
            {"raw_data.Line.SalesItemLineDetail.ClassRef.name": class_name},
            {"_id": 0, "raw_data.DocNumber": 1},
        ):
            dn = (inv.get("raw_data") or {}).get("DocNumber")
            if dn:
                class_doc_nums.append(dn)
        async for bill in qbo_db["ap_bills"].find(
            {"raw_data.Line.AccountBasedExpenseLineDetail.ClassRef.name": class_name},
            {"_id": 0, "raw_data.DocNumber": 1},
        ):
            dn = (bill.get("raw_data") or {}).get("DocNumber")
            if dn:
                class_doc_nums.append(dn)
        q["doc_num"] = {"$in": class_doc_nums or ["__no_match__"]}

    # -- Location filter: resolve doc_nums from invoice/bill DepartmentRef --
    if location:
        loc_doc_nums: List[str] = []
        async for inv in qbo_db["ar_invoices"].find(
            {"raw_data.DepartmentRef.name": location},
            {"_id": 0, "raw_data.DocNumber": 1},
        ):
            dn = (inv.get("raw_data") or {}).get("DocNumber")
            if dn:
                loc_doc_nums.append(dn)
        async for bill in qbo_db["ap_bills"].find(
            {"raw_data.DepartmentRef.name": location},
            {"_id": 0, "raw_data.DocNumber": 1},
        ):
            dn = (bill.get("raw_data") or {}).get("DocNumber")
            if dn:
                loc_doc_nums.append(dn)

        # Intersect with class filter if both are active
        if "doc_num" in q and isinstance(q["doc_num"], dict) and "$in" in q["doc_num"]:
            intersected = list(set(q["doc_num"]["$in"]).intersection(set(loc_doc_nums)))
            q["doc_num"]["$in"] = intersected or ["__no_match__"]
        else:
            q["doc_num"] = {"$in": loc_doc_nums or ["__no_match__"]}

    # -- Free-text search: memo, name, doc_num, or sales custom field on invoices --
    if search:
        sales_match_doc_nums: List[str] = []
        try:
            async for inv in qbo_db["ar_invoices"].find(
                {
                    "raw_data.CustomField": {
                        "$elemMatch": {
                            "StringValue": {"$regex": re.escape(search), "$options": "i"}
                        }
                    }
                },
                {"_id": 0, "raw_data.DocNumber": 1},
            ):
                dn = (inv.get("raw_data") or {}).get("DocNumber")
                if dn:
                    sales_match_doc_nums.append(dn)
        except Exception:
            sales_match_doc_nums = []

        or_conds: List[Dict[str, Any]] = [
            {"memo": {"$regex": re.escape(search), "$options": "i"}},
            {"name": {"$regex": re.escape(search), "$options": "i"}},
            {"doc_num": {"$regex": re.escape(search), "$options": "i"}},
        ]
        if sales_match_doc_nums:
            or_conds.append({"doc_num": {"$in": sales_match_doc_nums[:500]}})
        q["$or"] = or_conds

    total = await qbo_db["general_ledger"].count_documents(q)
    cursor = (
        qbo_db["general_ledger"]
        .find(q, {"_id": 0})
        .sort([("tx_date", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)

    # -- Enrich each GL row with joined class / location / deal_id from invoices/bills --
    inv_doc_nums = list(
        {
            d.get("doc_num")
            for d in docs
            if d.get("doc_num") and "invoice" in (d.get("txn_type") or "").lower()
        }
    )
    bill_doc_nums = list(
        {
            d.get("doc_num")
            for d in docs
            if d.get("doc_num") and "bill" == (d.get("txn_type") or "").lower()
        }
    )

    sales1_map: Dict[str, str] = {}
    class_map: Dict[str, str] = {}
    location_map: Dict[str, str] = {}

    if inv_doc_nums:
        async for inv in qbo_db["ar_invoices"].find(
            {"raw_data.DocNumber": {"$in": inv_doc_nums}},
            {
                "_id": 0,
                "raw_data.DocNumber": 1,
                "raw_data.CustomField": 1,
                "raw_data.Line": 1,
                "raw_data.DepartmentRef": 1,
            },
        ):
            raw = inv.get("raw_data") or {}
            dn = raw.get("DocNumber")
            if not dn:
                continue
            for cf in raw.get("CustomField") or []:
                if str(cf.get("Name", "")).lower().startswith("sales"):
                    val = cf.get("StringValue") or cf.get("NumberValue")
                    if val:
                        sales1_map[dn] = str(val)
                        break
            for line in raw.get("Line") or []:
                cls = ((line or {}).get("SalesItemLineDetail") or {}).get("ClassRef") or {}
                if cls.get("name"):
                    class_map[dn] = cls["name"]
                    break
            dept = raw.get("DepartmentRef") or {}
            if dept.get("name"):
                location_map[dn] = dept["name"]

    if bill_doc_nums:
        async for bill in qbo_db["ap_bills"].find(
            {"raw_data.DocNumber": {"$in": bill_doc_nums}},
            {
                "_id": 0,
                "raw_data.DocNumber": 1,
                "raw_data.Line": 1,
                "raw_data.DepartmentRef": 1,
            },
        ):
            raw = bill.get("raw_data") or {}
            dn = raw.get("DocNumber")
            if not dn:
                continue
            for line in raw.get("Line") or []:
                cls = (
                    (line or {}).get("AccountBasedExpenseLineDetail") or {}
                ).get("ClassRef") or {}
                if cls.get("name"):
                    class_map[dn] = cls["name"]
                    break
            dept = raw.get("DepartmentRef") or {}
            if dept.get("name"):
                location_map[dn] = dept["name"]

    rows = []
    for d in docs:
        d2 = safe_doc(d)
        ttype = str(d.get("txn_type") or "").lower()
        d2["sales1"] = sales1_map.get(d.get("doc_num")) if "invoice" in ttype else None
        d2["class_name"] = class_map.get(d.get("doc_num"))
        d2["location"] = location_map.get(d.get("doc_num"))
        rows.append(d2)

    return {"rows": rows, "total": total}


# ---------------------------------------------------------------------------
# GL dimension lookups
# ---------------------------------------------------------------------------

@router.get(
    "/gl/accounts",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def gl_accounts(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()
    names = await qbo_db["general_ledger"].distinct("account_name")
    return {"rows": sorted([n for n in names if n])}


@router.get(
    "/gl/txn-types",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def gl_txn_types(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()
    types = await qbo_db["general_ledger"].distinct("txn_type")
    return {"rows": sorted([t for t in types if t])}


@router.get(
    "/gl/classes",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def gl_classes(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Distinct class names found on invoice and bill line items."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()
    classes: set[str] = set()

    pipeline_inv = [
        {"$unwind": {"path": "$raw_data.Line", "preserveNullAndEmptyArrays": False}},
        {"$group": {"_id": "$raw_data.Line.SalesItemLineDetail.ClassRef.name"}},
    ]
    async for r in qbo_db["ar_invoices"].aggregate(pipeline_inv):
        if r.get("_id"):
            classes.add(r["_id"])

    pipeline_bill = [
        {"$unwind": {"path": "$raw_data.Line", "preserveNullAndEmptyArrays": False}},
        {"$group": {"_id": "$raw_data.Line.AccountBasedExpenseLineDetail.ClassRef.name"}},
    ]
    async for r in qbo_db["ap_bills"].aggregate(pipeline_bill):
        if r.get("_id"):
            classes.add(r["_id"])

    return {"rows": sorted(classes)}


@router.get(
    "/gl/locations",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def gl_locations(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Distinct location (QBO Department) names from invoices and bills."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    _check_perm(user_dict)
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()
    locations: set[str] = set()

    for coll in ("ar_invoices", "ap_bills"):
        async for r in qbo_db[coll].aggregate(
            [
                {"$match": {"raw_data.DepartmentRef.name": {"$exists": True}}},
                {"$group": {"_id": "$raw_data.DepartmentRef.name"}},
            ]
        ):
            if r.get("_id"):
                locations.add(r["_id"])

    return {"rows": sorted(locations)}
