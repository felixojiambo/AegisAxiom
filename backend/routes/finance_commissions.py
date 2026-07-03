"""Financial Command Center — Commissions routes.

Lifted from legacy `backend/routes/finance.py` (overview, commissions CRUD, zoho-commissions,
users-summary) into the FCC namespace.

Differences from source:
  * Router prefix: /finance (mounted in server.py).
  * Paths: /overview renamed to /commissions/overview to be unambiguous.
  * Permission model: finance.portal → finance.commissions.{view,edit}; finance.access accepted
    as admin bypass on all endpoints.
  * State-changing endpoints (PUT /commissions/{id}, DELETE /commissions/{id},
    DELETE /commissions/bulk) routed through crud_envelope for audit/policy/events.
  * Read endpoints (overview, list, zoho-commissions, users-summary) use _require_commissions_view
    guard; no envelope.
  * Field registry registered under module "finance", entities "commission" and "zoho_commission".
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Field / event / module registration
# ---------------------------------------------------------------------------
register_module_config("finance", "commissions", "commission_id", "Finance")
register_module_events("finance", "commission", ["created", "updated", "deleted"])
register_fields(
    "finance",
    "commission",
    [
        {
            "name": "commission_id",
            "type": "string",
            "label": "Commission ID",
            "description": "Primary key for a commission record.",
        },
        {
            "name": "user_id",
            "type": "string",
            "label": "User ID",
            "description": "ID of the sales rep, agent, or director who earned this commission.",
        },
        {
            "name": "user_name",
            "type": "string",
            "label": "User Name",
            "description": "Display name of the earner.",
        },
        {
            "name": "deal_name",
            "type": "string",
            "label": "Deal Name",
            "description": "Name of the deal this commission is tied to.",
        },
        {
            "name": "commission_type",
            "type": "string",
            "label": "Commission Type",
            "description": "Category of commission (e.g. sales, override, referral).",
        },
        {
            "name": "commission_rate",
            "type": "number",
            "label": "Commission Rate",
            "description": "Rate applied to calculate the commission amount.",
        },
        {
            "name": "amount_owed",
            "type": "number",
            "label": "Amount Owed",
            "description": "Total commission amount owed to the earner.",
        },
        {
            "name": "amount_paid",
            "type": "number",
            "label": "Amount Paid",
            "description": "Amount of the commission that has been paid out.",
        },
        {
            "name": "potential_amount",
            "type": "number",
            "label": "Potential Amount",
            "description": "Projected commission if deal converts or conditions are met.",
        },
        {
            "name": "status",
            "type": "string",
            "label": "Status",
            "description": "Payment status of the commission record.",
            "options": ["pending", "paid", "partial", "cancelled", "on_hold"],
        },
        {
            "name": "period",
            "type": "string",
            "label": "Period",
            "description": "Reporting period (e.g. '2025-Q1') this commission belongs to.",
        },
        {
            "name": "paid_date",
            "type": "string",
            "label": "Paid Date",
            "description": "ISO date string when the commission was paid.",
        },
        {
            "name": "notes",
            "type": "string",
            "label": "Notes",
            "description": "Free-text notes on this commission record.",
        },
        {
            "name": "created_at",
            "type": "datetime",
            "label": "Created At",
            "description": "Timestamp when the commission record was created.",
        },
    ],
)

register_module_config("finance", "zoho_commissions", "zoho_commission_id", "Finance")
register_module_events("finance", "zoho_commission", ["synced"])
register_fields(
    "finance",
    "zoho_commission",
    [
        {
            "name": "zoho_commission_id",
            "type": "string",
            "label": "Zoho Commission ID",
            "description": "Primary key for a Zoho commission record.",
        },
        {
            "name": "sales_rep_name",
            "type": "string",
            "label": "Sales Rep Name",
            "description": "Name of the sales rep in Zoho.",
        },
        {
            "name": "agent_of_records_name",
            "type": "string",
            "label": "Agent of Record Name",
            "description": "Name of the agent of record associated with this commission.",
        },
        {
            "name": "agent_commission",
            "type": "number",
            "label": "Agent Commission",
            "description": "Commission dollar amount for the agent.",
        },
        {
            "name": "agent_commission_status",
            "type": "string",
            "label": "Agent Commission Status",
            "description": "Payment status for agent commission.",
        },
        {
            "name": "sales_rep_commission",
            "type": "number",
            "label": "Sales Rep Commission",
            "description": "Commission dollar amount for the sales rep.",
        },
        {
            "name": "sales_rep_commission_status",
            "type": "string",
            "label": "Sales Rep Commission Status",
            "description": "Payment status for sales rep commission.",
        },
        {
            "name": "dir_commission",
            "type": "number",
            "label": "Director Commission",
            "description": "Commission dollar amount for the director override.",
        },
        {
            "name": "payment_date",
            "type": "string",
            "label": "Payment Date",
            "description": "ISO date string when Zoho recorded payment.",
        },
        {
            "name": "synced_at",
            "type": "datetime",
            "label": "Synced At",
            "description": "Timestamp of the last Zoho sync that touched this record.",
        },
    ],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_dict(current_user: Any) -> dict:
    return current_user.model_dump() if hasattr(current_user, "model_dump") else current_user


def _require_commissions_view(user: Any) -> None:
    u = _user_dict(user)
    if not (has_perm(u, "finance.commissions.view") or has_perm(u, "finance.access")):
        raise HTTPException(
            status_code=403,
            detail="Permission denied: finance.commissions.view required",
        )


def _require_commissions_edit(user: Any) -> None:
    u = _user_dict(user)
    if not (has_perm(u, "finance.commissions.edit") or has_perm(u, "finance.access")):
        raise HTTPException(
            status_code=403,
            detail="Permission denied: finance.commissions.edit required",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "/commissions/overview",
    dependencies=[Depends(require_ai_permission("finance.commissions.view"))],
)
async def commissions_overview(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Aggregated KPIs for the commissions section of the finance dashboard.

    Returns commission totals (owed/paid/potential), Zoho commission aggregations,
    and invoice totals from both the legacy invoices collection and ar_invoices.
    Response shape is preserved from legacy /finance/overview.
    """
    _require_commissions_view(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    comms = await db.commissions.find({}, {"_id": 0}).to_list(5000)
    zoho_comms = await db.zoho_commissions.find({}, {"_id": 0}).to_list(5000)
    # TRANSITIONAL (N4): read all three invoice sources until legacy collections are formally retired.
    # - db.invoices: oldest legacy collection (pre-ar_invoices era).
    # - db.ar_invoices: intermediate collection, deprecated after Wave 2D migration.
    # - db.finance_drafts(kind=ar_invoice): new canonical location post-Wave 2D.
    # Remove db.invoices and db.ar_invoices reads once Wave 5 decommissions those collections.
    legacy_invoices = await db.invoices.find({}, {"_id": 0}).to_list(5000)
    ar_invoices = await db.ar_invoices.find({}, {"_id": 0}).to_list(5000)
    finance_draft_ar = await db["finance_drafts"].find(
        {"kind": "ar_invoice"}, {"_id": 0, "amount": 1, "sync_status": 1}
    ).to_list(10000)

    total_owed = sum(c.get("amount_owed", 0) for c in comms)
    total_paid = sum(c.get("amount_paid", 0) for c in comms)
    total_potential = sum(c.get("potential_amount", 0) for c in comms)

    zoho_agent = sum(c.get("agent_commission", 0) for c in zoho_comms)
    zoho_rep = sum(c.get("sales_rep_commission", 0) for c in zoho_comms)
    zoho_dir = sum(c.get("dir_commission", 0) for c in zoho_comms)

    # Combine all three sources for invoice totals.
    # finance_drafts uses `amount` (new shape); legacy collections use `total` / `total_amount`.
    # sync_status="Synced" is the finance_drafts equivalent of status="paid".
    invoice_total = (
        sum(i.get("total_amount", 0) for i in legacy_invoices)
        + sum(i.get("total", 0) for i in ar_invoices)
        + sum(i.get("amount", 0) for i in finance_draft_ar)
    )
    invoice_paid = (
        sum(i.get("total_amount", 0) for i in legacy_invoices if i.get("status") == "paid")
        + sum(i.get("total", 0) for i in ar_invoices if i.get("status") == "paid")
        + sum(i.get("amount", 0) for i in finance_draft_ar if i.get("sync_status") == "Synced")
    )
    invoice_outstanding = invoice_total - invoice_paid

    # By status
    status_counts: dict = {}
    for c in comms:
        s = c.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    # By user
    by_user: dict = {}
    for c in comms:
        uid = c.get("user_id", "unknown")
        uname = c.get("user_name", uid)
        if uid not in by_user:
            by_user[uid] = {
                "user_id": uid,
                "user_name": uname,
                "owed": 0,
                "paid": 0,
                "potential": 0,
                "count": 0,
            }
        by_user[uid]["owed"] += c.get("amount_owed", 0)
        by_user[uid]["paid"] += c.get("amount_paid", 0)
        by_user[uid]["potential"] += c.get("potential_amount", 0)
        by_user[uid]["count"] += 1

    # Zoho commission by rep
    zoho_by_rep: dict = {}
    for c in zoho_comms:
        name = c.get("sales_rep_name") or "Unknown"
        if name not in zoho_by_rep:
            zoho_by_rep[name] = {"name": name, "agent": 0, "rep": 0, "dir": 0, "count": 0}
        zoho_by_rep[name]["agent"] += c.get("agent_commission", 0)
        zoho_by_rep[name]["rep"] += c.get("sales_rep_commission", 0)
        zoho_by_rep[name]["dir"] += c.get("dir_commission", 0)
        zoho_by_rep[name]["count"] += 1

    return {
        "commissions": {
            "total_owed": total_owed,
            "total_paid": total_paid,
            "total_potential": total_potential,
            "count": len(comms),
            "by_status": status_counts,
            "by_user": list(by_user.values()),
        },
        "zoho_commissions": {
            "agent_total": zoho_agent,
            "rep_total": zoho_rep,
            "dir_total": zoho_dir,
            "count": len(zoho_comms),
            "by_rep": list(zoho_by_rep.values()),
        },
        "invoices": {
            "total": invoice_total,
            "paid": invoice_paid,
            "outstanding": invoice_outstanding,
            "count": len(legacy_invoices) + len(ar_invoices),
        },
    }


@router.get(
    "/commissions",
    dependencies=[Depends(require_ai_permission("finance.commissions.view"))],
)
async def list_commissions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = None,
    user_id: Optional[str] = None,
    commission_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: int = -1,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Paginated, filterable commission list.

    Excludes commission *plans* (documents that have a `tiers` field) — returns
    only raw commission payment records.  Capped at 200 items per page.
    """
    _require_commissions_view(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    filt: dict = {}
    # Exclude commission plans (which have a "tiers" field)
    filt["tiers"] = {"$exists": False}

    if status:
        filt["status"] = status
    if user_id:
        filt["user_id"] = user_id
    if commission_type:
        filt["commission_type"] = commission_type
    if date_from or date_to:
        date_range: dict = {}
        if date_from:
            date_range["$gte"] = date_from
        if date_to:
            date_range["$lte"] = date_to
        filt["paid_date"] = date_range

    effective_limit = min(limit, 200)
    total = await db.commissions.count_documents(filt)
    docs = (
        await db.commissions.find(filt, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip(skip)
        .limit(effective_limit)
        .to_list(effective_limit)
    )
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.put(
    "/commissions/{commission_id}",
    dependencies=[Depends(require_ai_permission("finance.commissions.edit"))],
)
async def update_commission(
    commission_id: str,
    update_data: dict,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Update an editable subset of fields on a commission record.

    Allowed fields: deal_name, user_name, commission_type, amount_owed,
    amount_paid, commission_rate, status, paid_date, period, notes.
    Updates both the `commissions` and `local_commissions` collections.
    """
    _require_commissions_edit(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    allowed_fields = [
        "deal_name",
        "user_name",
        "commission_type",
        "amount_owed",
        "amount_paid",
        "commission_rate",
        "status",
        "paid_date",
        "period",
        "notes",
    ]
    update_doc = {k: v for k, v in update_data.items() if k in allowed_fields}
    if not update_doc:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Fetch current state for crud_envelope before-snapshot
    before = await db.commissions.find_one({"commission_id": commission_id}, {"_id": 0})
    if before is None:
        before = await db.local_commissions.find_one(
            {"commission_id": commission_id}, {"_id": 0}
        )
    if before is None:
        raise HTTPException(status_code=404, detail="Commission record not found")

    async with crud_envelope(
        module="finance",
        entity="commission",
        action="updated",
        resource_id=commission_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        result1 = await db.commissions.update_one(
            {"commission_id": commission_id}, {"$set": update_doc}
        )
        result2 = await db.local_commissions.update_one(
            {"commission_id": commission_id}, {"$set": update_doc}
        )

        if result1.matched_count == 0 and result2.matched_count == 0:
            raise HTTPException(status_code=404, detail="Commission record not found")

        after = {**before, **update_doc}
        ctx.set_after(after)

    return {
        "message": "Commission record updated",
        "commission_id": commission_id,
        "updated": update_doc,
    }


@router.delete(
    "/commissions/bulk",
    dependencies=[Depends(require_ai_permission("finance.commissions.edit"))],
)
async def bulk_delete_commissions(
    commission_ids: List[str],
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Bulk-delete commission records by ID list.

    Deletes from both `commissions` and `local_commissions` collections.
    """
    _require_commissions_edit(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    async with crud_envelope(
        module="finance",
        entity="commission",
        action="deleted",
        resource_id=f"bulk:{len(commission_ids)}",
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        result1 = await db.commissions.delete_many(
            {"commission_id": {"$in": commission_ids}}
        )
        result2 = await db.local_commissions.delete_many(
            {"commission_id": {"$in": commission_ids}}
        )
        ctx.set_after(
            {
                "deleted_ids": commission_ids,
                "deleted_from_main": result1.deleted_count,
                "deleted_from_local": result2.deleted_count,
            }
        )

    return {
        "message": f"Deleted {result1.deleted_count + result2.deleted_count} commission records",
        "deleted_from_main": result1.deleted_count,
        "deleted_from_local": result2.deleted_count,
    }


@router.delete(
    "/commissions/{commission_id}",
    dependencies=[Depends(require_ai_permission("finance.commissions.edit"))],
)
async def delete_commission(
    commission_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Delete a single commission record by ID.

    Deletes from both `commissions` and `local_commissions` collections.
    """
    _require_commissions_edit(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    before = await db.commissions.find_one({"commission_id": commission_id}, {"_id": 0})
    if before is None:
        before = await db.local_commissions.find_one(
            {"commission_id": commission_id}, {"_id": 0}
        )
    if before is None:
        raise HTTPException(status_code=404, detail="Commission record not found")

    async with crud_envelope(
        module="finance",
        entity="commission",
        action="deleted",
        resource_id=commission_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        result1 = await db.commissions.delete_one({"commission_id": commission_id})
        result2 = await db.local_commissions.delete_one({"commission_id": commission_id})

        if result1.deleted_count == 0 and result2.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Commission record not found")

        ctx.set_after(
            {
                "commission_id": commission_id,
                "deleted_from_main": result1.deleted_count,
                "deleted_from_local": result2.deleted_count,
            }
        )

    return {
        "message": "Commission record deleted",
        "commission_id": commission_id,
        "deleted_from_main": result1.deleted_count,
        "deleted_from_local": result2.deleted_count,
    }


@router.get(
    "/zoho-commissions",
    dependencies=[Depends(require_ai_permission("finance.commissions.view"))],
)
async def list_zoho_commissions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = None,
    rep_name: Optional[str] = None,
    agent_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Paginated Zoho commission records.

    `status` matches against both agent_commission_status and
    sales_rep_commission_status (OR logic). `rep_name` and `agent_name`
    are case-insensitive substring matches. Date range filters on payment_date.
    """
    _require_commissions_view(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    filt: dict = {}
    if status:
        filt["$or"] = [
            {"agent_commission_status": status},
            {"sales_rep_commission_status": status},
        ]
    if rep_name:
        filt["sales_rep_name"] = {"$regex": re.escape(rep_name), "$options": "i"}
    if agent_name:
        filt["agent_of_records_name"] = {"$regex": re.escape(agent_name), "$options": "i"}
    if date_from or date_to:
        date_range: dict = {}
        if date_from:
            date_range["$gte"] = date_from
        if date_to:
            date_range["$lte"] = date_to
        filt["payment_date"] = date_range

    effective_limit = min(limit, 200)
    total = await db.zoho_commissions.count_documents(filt)
    docs = (
        await db.zoho_commissions.find(filt, {"_id": 0})
        .sort("synced_at", -1)
        .skip(skip)
        .limit(effective_limit)
        .to_list(effective_limit)
    )
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.get(
    "/users-summary",
    dependencies=[Depends(require_ai_permission("finance.commissions.view"))],
)
async def commissions_users_summary(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """List of sales-eligible users (sales_rep, agent, director) for filter dropdowns.

    Returns user_id, name, email, role sorted by name ascending.
    Capped at 500 users.
    """
    _require_commissions_view(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    users = (
        await db.users.find(
            {"role": {"$in": ["sales_rep", "agent", "director"]}},
            {"_id": 0, "user_id": 1, "name": 1, "email": 1, "role": 1},
        )
        .sort("name", 1)
        .to_list(500)
    )
    return users
