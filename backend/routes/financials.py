from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from pymongo import ReturnDocument
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from db.session import get_session
from repositories.users import UserRepository
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from models import (
    Invoice, InvoiceCreate, InvoiceUpdate,
    InvoiceApproveRequest, InvoiceRejectRequest,
    Commission, CommissionCreate, CommissionUpdate, CommissionRejectRequest,
)
from auth_helper import get_current_user, get_invoice_filter, get_commission_filter, has_perm
from services.response_filter import filter_response_fields

router = APIRouter()

# Audit-trail fields that must NEVER be set via the generic PUT /invoices/{id}.
# These are managed exclusively by the submit/approve/reject workflow endpoints.
INVOICE_AUDIT_FIELDS = {
    "approved_by", "approved_at", "approval_notes",
    "rejected_by", "rejected_at", "rejection_reason",
    "submitted_for_approval_at", "submitted_by",
}

# Status values that represent the approval workflow. These cannot be set via
# the generic PUT — they're only reachable through submit-for-approval, approve,
# or reject. Normal lifecycle statuses (draft/sent/paid/overdue/void) ARE
# allowed via PUT for the standard Invoices list view.
INVOICE_WORKFLOW_STATUS_VALUES = {"pending_approval", "approved", "rejected"}

# ---- INVOICES ----

@router.get("/invoices")
async def get_invoices(skip: int = 0, limit: int = 100, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    filt = await get_invoice_filter(current_user) or {}
    docs = await db.invoices.find(filt, {"_id": 0}).sort("created_at", -1).skip(skip).limit(min(limit, 500)).to_list(min(limit, 500))
    total = await db.invoices.count_documents(filt)
    docs = filter_response_fields(docs, current_user, "invoices")
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.post("/invoices")
async def create_invoice(data: InvoiceCreate, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    if not has_perm(current_user, "invoices.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    account = await db.accounts.find_one({"account_id": data.account_id}, {"_id": 0})
    count = await db.invoices.count_documents({})
    invoice_id = f"inv_{uuid.uuid4().hex[:12]}"
    doc = {
        "invoice_id": invoice_id,
        "invoice_number": f"INV-{(count + 1):04d}",
        **data.model_dump(),
        "account_name": account["name"] if account else None,
        "assigned_sales_rep_id": account.get("assigned_sales_rep_id") if account else None,
        "total_amount": data.amount + data.tax_amount,
        "status": "draft",
        "paid_date": None,
        "qbo_invoice_id": None,
        "created_at": datetime.now(timezone.utc),
    }
    # Default issued_date to today if not provided (business date invoice was issued)
    if not doc.get("issued_date"):
        doc["issued_date"] = datetime.now(timezone.utc).date().isoformat()
    await db.invoices.insert_one(doc)
    return Invoice(**doc)


# Must precede /{invoice_id} — FastAPI matches parametric routes greedily.
@router.get("/invoices/pending-approval")
async def get_pending_approval_invoices(
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Get all invoices pending director approval."""
    if not has_perm(current_user, "invoices.view.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    filt = {"status": "pending_approval"}
    page_size = min(limit, 500)
    invoices = await db.invoices.find(
        filt,
        {"_id": 0}
    ).sort("submitted_for_approval_at", -1).skip(skip).limit(page_size).to_list(page_size)
    total = await db.invoices.count_documents(filt)

    return {"items": invoices, "total": total, "skip": skip, "limit": limit}
@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    rbac_filter = await get_invoice_filter(current_user) or {}
    query = {"invoice_id": invoice_id, **rbac_filter}

    doc = await db.invoices.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return doc


@router.put("/invoices/{invoice_id}")
async def update_invoice(invoice_id: str, data: InvoiceUpdate, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    if not has_perm(current_user, "invoices.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    rbac_filter = await get_invoice_filter(current_user) or {}
    query = {"invoice_id": invoice_id, **rbac_filter}

    raw_update = data.model_dump(exclude_unset=True)

    # Block any audit-trail field from being set via the generic PUT.
    # These are managed exclusively by the workflow endpoints.
    forbidden_fields = set(raw_update.keys()) & INVOICE_AUDIT_FIELDS
    if forbidden_fields:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot set audit-trail fields directly: {sorted(forbidden_fields)}. "
                f"These are managed by the submit-for-approval, approve, or reject endpoints."
            ),
        )

    # Block setting status to a workflow-controlled value via the generic PUT.
    # Normal lifecycle transitions (draft/sent/paid/overdue/void) are allowed.
    if "status" in raw_update and raw_update["status"] in INVOICE_WORKFLOW_STATUS_VALUES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot set status to '{raw_update['status']}' directly. "
                f"Use the submit-for-approval, approve, or reject endpoints."
            ),
        )

    # Status-based permission checks for invoice lifecycle actions
    if "status" in raw_update:
        new_status = raw_update["status"]
        if new_status == "sent" and not has_perm(current_user, "invoices.send"):
            raise HTTPException(status_code=403, detail="Insufficient permissions to send invoices")
        if new_status == "paid" and not has_perm(current_user, "invoices.mark_paid"):
            raise HTTPException(status_code=403, detail="Insufficient permissions to mark invoices as paid")
        if new_status == "void" and not has_perm(current_user, "invoices.void"):
            raise HTTPException(status_code=403, detail="Insufficient permissions to void invoices")

    update_data = raw_update

    if not update_data:
        existing = await db.invoices.find_one(query, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return existing

    # Atomic guard: prevent edits to invoices currently in a workflow state
    # (pending_approval, approved, rejected). Folding the status check into
    # the filter avoids a TOCTOU race where a status transition could slip
    # in between a separate find_one check and the update.
    updated = await db.invoices.find_one_and_update(
        {**query, "status": {"$nin": list(INVOICE_WORKFLOW_STATUS_VALUES)}},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated:
        return updated

    # Disambiguate 404 (not found / not visible) vs 409 (locked by workflow state)
    existing = await db.invoices.find_one(query, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Invoice not found")
    raise HTTPException(
        status_code=409,
        detail=(
            f"Cannot edit an invoice in workflow state "
            f"(current: {existing.get('status')}). Use the workflow endpoints."
        ),
    )

# ---- COMMISSIONS ----

@router.get("/commissions")
async def get_commissions(skip: int = 0, limit: int = 100, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    filt = get_commission_filter(current_user) or {}
    docs = await db.commissions.find(filt, {"_id": 0}).sort("created_at", -1).skip(skip).limit(min(limit, 500)).to_list(min(limit, 500))
    total = await db.commissions.count_documents(filt)
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.get("/commissions/summary")
async def commission_summary(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    filt = get_commission_filter(current_user) or {}
    comms = await db.commissions.find(filt, {"_id": 0}).to_list(1000)
    return {
        "total_owed": sum(c.get("amount_owed", 0) for c in comms),
        "total_paid": sum(c.get("amount_paid", 0) for c in comms),
        "total_potential": sum(c.get("potential_amount", 0) for c in comms),
        "count": len(comms),
    }


# Must precede /{commission_id} — FastAPI matches parametric routes greedily.
@router.get("/commissions/report")
async def commission_report(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    if not has_perm(current_user, "commissions.view.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    comms = await db.commissions.find({}, {"_id": 0}).to_list(1000)
    # Group by user
    by_user: dict = {}
    for c in comms:
        uid = c.get("user_id", "unknown")
        uname = c.get("user_name", uid)
        if uid not in by_user:
            by_user[uid] = {"user_id": uid, "user_name": uname, "total_owed": 0, "total_paid": 0, "total_potential": 0, "count": 0, "pending": 0, "approved": 0, "paid": 0, "rejected": 0}
        by_user[uid]["total_owed"] += c.get("amount_owed", 0)
        by_user[uid]["total_paid"] += c.get("amount_paid", 0)
        by_user[uid]["total_potential"] += c.get("potential_amount", 0)
        by_user[uid]["count"] += 1
        status = c.get("status", "pending")
        by_user[uid][status] = by_user[uid].get(status, 0) + 1
    return {"by_user": list(by_user.values()), "total_records": len(comms)}


@router.post("/commissions")
async def create_commission(data: CommissionCreate, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "commissions.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    deal = await db.deals.find_one({"deal_id": data.deal_id}, {"_id": 0, "name": 1})
    user = await UserRepository(pg).get_user_by_id(data.user_id)
    comm_id = f"comm_{uuid.uuid4().hex[:12]}"
    doc = {
        "commission_id": comm_id,
        **data.model_dump(),
        "deal_name": deal["name"] if deal else None,
        "user_name": user.name if user else None,
        "amount_paid": 0.0,
        "status": "pending",
        "paid_date": None,
        "created_at": datetime.now(timezone.utc),
    }
    await db.commissions.insert_one(doc)
    return Commission(**doc)


@router.get("/commissions/{commission_id}")
async def get_commission(commission_id: str, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    rbac_filter = get_commission_filter(current_user) or {}
    query = {"commission_id": commission_id, **rbac_filter}

    comm = await db.commissions.find_one(query, {"_id": 0})
    if not comm:
        raise HTTPException(status_code=404, detail="Commission not found")
    return comm


@router.put("/commissions/{commission_id}")
async def update_commission(commission_id: str, data: CommissionUpdate, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    if not has_perm(current_user, "commissions.mark_paid") and not has_perm(current_user, "commissions.edit.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    rbac_filter = get_commission_filter(current_user) or {}
    query = {"commission_id": commission_id, **rbac_filter}

    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        existing = await db.commissions.find_one(query, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Commission not found")
        return existing

    updated = await db.commissions.find_one_and_update(
        query,
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Commission not found")
    return updated


@router.post("/commissions/{commission_id}/approve")
async def approve_commission(commission_id: str, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    if not has_perm(current_user, "commissions.mark_paid"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    # Workflow endpoint: no RBAC ownership filter. The has_perm gate above
    # controls who can approve, and approvals are explicitly cross-user.
    query = {"commission_id": commission_id}

    # Atomic state transition: fold the status precondition into the update
    # filter so two concurrent approvals cannot both succeed.
    updated = await db.commissions.find_one_and_update(
        {**query, "status": "pending"},
        {"$set": {
            "status": "approved",
            "approved_by": current_user.user_id,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated:
        return updated

    existing = await db.commissions.find_one(query, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Commission not found")
    raise HTTPException(
        status_code=409,
        detail=f"Commission is no longer in pending state (current: {existing.get('status')})"
    )


@router.post("/commissions/{commission_id}/reject")
async def reject_commission(commission_id: str, data: CommissionRejectRequest, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    if not has_perm(current_user, "commissions.mark_paid"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    # Workflow endpoint: no RBAC ownership filter. See approve_commission.
    query = {"commission_id": commission_id}

    # Only pending or approved commissions can be rejected.
    # Write to rejection_reason (dedicated field) instead of clobbering notes.
    updated = await db.commissions.find_one_and_update(
        {**query, "status": {"$in": ["pending", "approved"]}},
        {"$set": {
            "status": "rejected",
            "rejection_reason": data.reason,
            "rejected_by": current_user.user_id,
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        }},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated:
        return updated

    existing = await db.commissions.find_one(query, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Commission not found")
    raise HTTPException(
        status_code=409,
        detail=f"Commission cannot be rejected in its current state ({existing.get('status')})"
    )


@router.post("/commissions/{commission_id}/pay")
async def pay_commission(commission_id: str, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    if not has_perm(current_user, "commissions.mark_paid"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    # Workflow endpoint: no RBAC ownership filter. See approve_commission.
    query = {"commission_id": commission_id}

    # Require explicit approval before payment. Enforces the audit trail:
    # every paid commission has both approved_by/approved_at AND
    # paid_by/paid_date. Pending commissions must go through /approve first.
    updated = await db.commissions.find_one_and_update(
        {**query, "status": "approved"},
        [{"$set": {
            "status": "paid",
            "amount_paid": "$amount_owed",
            "paid_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "paid_by": current_user.user_id,
        }}],
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated:
        return updated

    existing = await db.commissions.find_one(query, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Commission not found")
    raise HTTPException(
        status_code=409,
        detail=(
            f"Commission must be approved before payment "
            f"(current status: {existing.get('status')}). Use /approve first."
        ),
    )

# ---- PAST DUE INVOICES WORKFLOW ----

@router.post("/invoices/{invoice_id}/submit-for-approval")
async def submit_invoice_for_approval(invoice_id: str, current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Submit a past due invoice for director approval.

    Submission is a self-action: users submit their OWN invoices for
    approval. Unlike approve/reject (which are intentionally cross-user
    actions performed by directors on sales rep records), this endpoint
    applies the standard RBAC ownership filter via get_invoice_filter so
    a sales rep cannot submit another rep's draft invoice for approval.
    """
    if not has_perm(current_user, "invoices.create"):
        raise HTTPException(
            status_code=403,
            detail="You need invoices.create permission to submit invoices for approval"
        )
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    # Submission is scoped to invoices the user can see/own per RBAC.
    # This differs from approve/reject/pay, which are intentionally
    # cross-user and skip the ownership filter.
    rbac_filter = await get_invoice_filter(current_user) or {}
    query = {"invoice_id": invoice_id, **rbac_filter}

    # Atomic state transition: only draft invoices the user owns can be submitted.
    updated = await db.invoices.find_one_and_update(
        {**query, "status": "draft"},
        {"$set": {
            "status": "pending_approval",
            "submitted_for_approval_at": datetime.now(timezone.utc),
            "submitted_by": current_user.user_id,
        }},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated:
        return updated

    existing = await db.invoices.find_one(query, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Invoice not found")
    raise HTTPException(
        status_code=409,
        detail=f"Can only submit draft invoices. Current status: {existing.get('status')}"
    )
@router.post("/invoices/{invoice_id}/approve")
async def approve_invoice(
    invoice_id: str,
    data: InvoiceApproveRequest = InvoiceApproveRequest(),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Director approves a past due invoice.

    Approval notes are accepted in the JSON request body, not as a query
    parameter, to avoid leaking sensitive business context into proxy/access logs.
    """
    if not has_perm(current_user, "invoices.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    # Workflow endpoint: no RBAC ownership filter. Approvals are explicitly
    # cross-user — directors approve sales rep invoices.
    query = {"invoice_id": invoice_id}

    # Atomic state transition: only pending_approval invoices can be approved.
    updated = await db.invoices.find_one_and_update(
        {**query, "status": "pending_approval"},
        {"$set": {
            "status": "approved",
            "approved_by": current_user.user_id,
            "approved_at": datetime.now(timezone.utc),
            "approval_notes": data.approval_notes,
        }},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated:
        return updated

    existing = await db.invoices.find_one(query, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Invoice not found")
    raise HTTPException(
        status_code=409,
        detail=f"Can only approve pending invoices. Current status: {existing.get('status')}"
    )


@router.post("/invoices/{invoice_id}/reject")
async def reject_invoice(
    invoice_id: str,
    data: InvoiceRejectRequest,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Director rejects a past due invoice with reason.

    Rejection reason is accepted in the JSON request body, not as a query
    parameter, to avoid leaking sensitive business context (which may contain
    PII or confidential dispute details) into proxy/access logs.
    """
    if not has_perm(current_user, "invoices.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if not data.rejection_reason or not data.rejection_reason.strip():
        raise HTTPException(status_code=400, detail="Rejection reason is required")
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    # Workflow endpoint: no RBAC ownership filter. See approve_invoice.
    query = {"invoice_id": invoice_id}

    # Atomic state transition: only pending_approval invoices can be rejected.
    updated = await db.invoices.find_one_and_update(
        {**query, "status": "pending_approval"},
        {"$set": {
            "status": "rejected",
            "rejected_by": current_user.user_id,
            "rejected_at": datetime.now(timezone.utc),
            "rejection_reason": data.rejection_reason,
        }},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated:
        return updated

    existing = await db.invoices.find_one(query, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Invoice not found")
    raise HTTPException(
        status_code=409,
        detail=f"Can only reject pending invoices. Current status: {existing.get('status')}"
    )