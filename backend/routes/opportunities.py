"""
Opportunity Management Module
Track sales opportunities from prospect to closed won/lost.
Integrates with Deal Management for conversion workflow.
"""
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from fastapi.responses import FileResponse
from datetime import datetime, timezone
from typing import Optional, List
import uuid
import logging
import mimetypes
import os
import re

from database import get_db
from models import (
    Opportunity, OpportunityCreate, OpportunityUpdate,
    OPPORTUNITY_STAGES
)
from auth_helper import get_current_user, has_perm
from services.event_bus import emit_event
from services.security_logger import log_security_event
from services.field_registry import register_fields, register_module_events, register_module_config
from services.opportunity_numbering import generate_opportunity_tracking_number

logger = logging.getLogger(__name__)

register_module_config("opportunities", "opportunities", "opportunity_id", "CRM & Sales")
register_module_events("opportunities", "opportunity", ["created", "updated", "deleted", "stage_changed"])
register_fields("opportunities", "opportunity", [
    {"name": "name", "type": "string", "label": "Opportunity Name", "description": "Name/title of the sales opportunity"},
    {"name": "account_name", "type": "string", "label": "Account Name", "description": "Company or account this opportunity is for"},
    {"name": "stage", "type": "string", "label": "Stage", "description": "Pipeline stage (prospect_opp, proposal_development, proposal_sent, negotiation_review, closed_won, closed_lost)"},
    {"name": "amount", "type": "number", "label": "Amount", "description": "Estimated revenue value of the opportunity"},
    {"name": "priority", "type": "string", "label": "Priority", "description": "Priority level (low, medium, high)"},
    {"name": "opportunity_owner_name", "type": "string", "label": "Owner", "description": "Name of the user who owns this opportunity"},
    {"name": "agent_of_record_name", "type": "string", "label": "Agent of Record", "description": "Partner or agent handling this opportunity"},
    {"name": "assigned_sales_ops_name", "type": "string", "label": "Sales Ops", "description": "Sales operations person managing the process"},
    {"name": "desired_proposal_date", "type": "date", "label": "Desired Proposal Date", "description": "Target date for proposal delivery"},
    {"name": "close_date", "type": "date", "label": "Close Date", "description": "Expected close date for the opportunity. Drives the close-date alert in Opportunity Management; separate from desired_proposal_date."},
    {"name": "lines_of_business", "type": "list", "label": "Lines of Business", "description": "LOBs involved (field_services, professional_services, wholesale_mrr, referral_mrr)"},
    {"name": "description", "type": "string", "label": "Description", "description": "Detailed opportunity description"},
    {"name": "is_test_data", "type": "boolean", "label": "Is Test Data", "description": "Flag indicating test/demo data"},
    {"name": "converted_to_deal_id", "type": "string", "label": "Converted Deal", "description": "Deal ID if opportunity was converted to a closed won deal"},
])

router = APIRouter()


# Source-of-truth filter for "selectable partner" — partner-agency accounts OR
# legacy is_partner=True, minus anything marked terminated. Used by the
# Agent-of-Record dropdown and by the create/update validators so a terminated
# partner can never be newly assigned. Read paths (opportunity detail, list)
# are unaffected: the partner *name* is cached on the opportunity document at
# assignment time, so historical records stay legible even after termination.
# partnership_status is sparse on partner accounts today (most are null for
# partners mid-onboarding); $exists:false keeps them selectable.
_SELECTABLE_PARTNER_FILTER = {
    "$and": [
        {"$or": [{"account_type": "partner_agency"}, {"is_partner": True}]},
        {"$or": [
            {"partnership_status": {"$exists": False}},
            {"partnership_status": None},
            {"partnership_status": {"$ne": "terminated"}},
        ]},
    ]
}


def can_edit_opportunity(current_user: dict, opportunity: dict) -> bool:
    """Check if user can edit an opportunity (owner or admin)."""
    if has_perm(current_user, "opportunities.edit.all"):
        return True
    if has_perm(current_user, "opportunities.edit.own"):
        return opportunity.get("opportunity_owner_id") == current_user.user_id
    return False


def apply_soft_delete_filter(query: dict, include_deleted: bool = False) -> dict:
    """Merge deleted_at: None into a MongoDB filter unless opting in.

    Centralizes the soft-delete read filter. Every production read
    of db.opportunities should route through this helper so we can't
    accidentally leak soft-deleted records into user-facing surfaces.

    Args:
        query: base MongoDB filter dict
        include_deleted: if True, returns query unchanged (for
                         admin /trash and /restore endpoints)

    Returns:
        new dict with {"deleted_at": None} merged unless
        include_deleted is True
    """
    if include_deleted:
        return query
    return {**query, "deleted_at": None}


# ---- CRUD OPERATIONS ----

@router.get("/opportunities")
async def get_opportunities(
    skip: int = 0,
    limit: int = 500,
    stage: Optional[str] = None,
    owner_id: Optional[str] = None,
    account_id: Optional[str] = None,
    priority: Optional[str] = None,
    test_only: bool = False,
    search: Optional[str] = None,
    view_as_user_id: Optional[str] = None,
    view_as_account_id: Optional[str] = None,
    current_user=Depends(get_current_user)
):
    """Get all opportunities with optional filters."""
    db = get_db()

    # Build base filter
    base_filter = {}

    # "View As" logic — admin/director can impersonate another user's or account's view
    view_as_active = False
    if view_as_user_id or view_as_account_id:
        if not has_perm(current_user, "opportunities.view.all"):
            raise HTTPException(status_code=403, detail="Only users with View All Opportunities permission can use View As")
        if view_as_user_id:
            # Validate the target user exists
            target_user = await db.users.find_one({"user_id": view_as_user_id}, {"_id": 0, "user_id": 1})
            if not target_user:
                raise HTTPException(status_code=404, detail="View As user not found")
            base_filter["opportunity_owner_id"] = view_as_user_id
        if view_as_account_id:
            base_filter["account_id"] = view_as_account_id
        view_as_active = True
        await log_security_event(
            event_type="view_as_used", severity="info",
            user_id=current_user.user_id, user_email=current_user.email,
            resource="opportunity", action="view_as", outcome="success",
            details={
                "view_as_user_id": view_as_user_id,
                "view_as_account_id": view_as_account_id,
            },
        )

    # RBAC: Users with view.own can only see their own opportunities
    # Skip normal RBAC when View As is active (already gated by admin/director check above)
    if not view_as_active:
        if not has_perm(current_user, "opportunities.view.all"):
            if has_perm(current_user, "opportunities.view.own"):
                base_filter["opportunity_owner_id"] = current_user.user_id
            else:
                raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Apply filters
    if stage:
        # Accept either a single stage value or a comma-separated list.
        # The Sales Ops workspace needs to widen the view past the
        # proposal_development stage so that SOW and proposal records
        # remain reachable after the opportunity transitions to
        # scoping_complete, proposal_sent, or further downstream
        # stages. The single-value path stays backward compatible with
        # every existing caller.
        if "," in stage:
            values = [s.strip() for s in stage.split(",") if s.strip()]
            if values:
                base_filter["stage"] = {"$in": values}
        else:
            base_filter["stage"] = stage
    if owner_id:
        base_filter["opportunity_owner_id"] = owner_id
    if account_id:
        base_filter["account_id"] = account_id
    if priority:
        base_filter["priority"] = priority
    # Test data filter: default shows real data only, toggle shows test data only
    base_filter["is_test_data"] = True if test_only else {"$ne": True}

    # Search by name or account name
    if search:
        safe_search = re.escape(search)
        base_filter["$or"] = [
            {"name": {"$regex": safe_search, "$options": "i"}},
            {"account_name": {"$regex": safe_search, "$options": "i"}},
            {"description": {"$regex": safe_search, "$options": "i"}}
        ]
    
    capped = min(limit, 1000)
    docs = await db.opportunities.find(apply_soft_delete_filter(base_filter), {"_id": 0}).sort("created_at", -1).skip(skip).limit(capped).to_list(capped)
    total = await db.opportunities.count_documents(apply_soft_delete_filter(base_filter))
    
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.post("/opportunities")
async def create_opportunity(data: OpportunityCreate, current_user=Depends(get_current_user)):
    """Create a new opportunity."""
    if not has_perm(current_user, "opportunities.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    # Validate account exists
    account = await db.accounts.find_one({"account_id": data.account_id}, {"_id": 0})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # Validate contact exists and belongs to account (if provided)
    if data.contact_id:
        contact = await db.contacts.find_one({
            "contact_id": data.contact_id,
            "account_id": data.account_id
        }, {"_id": 0})
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found or does not belong to this account")
        contact_name = contact.get("name")
    else:
        contact_name = None
    
    # Validate agent of record (partner) if provided. Uses the shared
    # _SELECTABLE_PARTNER_FILTER so terminated partners can't be assigned.
    agent_name = None
    if data.agent_of_record_id:
        partner = await db.accounts.find_one(
            {"$and": [{"account_id": data.agent_of_record_id}, _SELECTABLE_PARTNER_FILTER]},
            {"_id": 0},
        )
        if not partner:
            raise HTTPException(status_code=404, detail="Agent of Record (Partner) not found")
        agent_name = partner.get("name")
    
    # Validate assigned sales ops if provided
    sales_ops_name = None
    if data.assigned_sales_ops_id:
        sales_ops = await db.users.find_one({"user_id": data.assigned_sales_ops_id}, {"_id": 0})
        if not sales_ops:
            raise HTTPException(status_code=404, detail="Sales Ops person not found")
        sales_ops_name = sales_ops.get("name")
    
    # Validate stage
    if data.stage not in OPPORTUNITY_STAGES:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {', '.join(OPPORTUNITY_STAGES)}")
    
    opportunity_id = f"opp_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    # Per-opportunity tracking number for Telarus deal-reg / sales-ops reference.
    # Race-safe via the opportunity_counters collection (see services.opportunity_numbering).
    tracking_number = await generate_opportunity_tracking_number()

    doc = {
        "opportunity_id": opportunity_id,
        "tracking_number": tracking_number,
        "name": data.name,
        "account_id": data.account_id,
        "account_name": account.get("name"),
        "contact_id": data.contact_id,
        "contact_name": contact_name,
        "agent_of_record_id": data.agent_of_record_id,
        "agent_of_record_name": agent_name,
        "description": data.description,
        "desired_proposal_date": data.desired_proposal_date,
        # Expected close date. Never derived from desired_proposal_date —
        # the two are distinct concepts and must stay independent so the
        # close-date alert never fires on a stale proposal date.
        "close_date": data.close_date,
        "priority": data.priority,
        "assigned_sales_ops_id": data.assigned_sales_ops_id,
        "assigned_sales_ops_name": sales_ops_name,
        "amount": data.amount,
        "stage": data.stage,
        "commission_payment_method": data.commission_payment_method,
        "lines_of_business": data.lines_of_business,
        "opportunity_owner_id": current_user.user_id,
        "opportunity_owner_name": current_user.name,
        "converted_to_deal_id": None,
        "converted_at": None,
        "notes": [],
        "attachments": [],
        "call_recordings": [],
        "is_test_data": data.is_test_data,
        "created_at": now,
        "updated_at": now
    }
    
    await db.opportunities.insert_one(doc)
    doc.pop("_id", None)
    logger.info(f"Opportunity created: {opportunity_id} by {current_user.email}")
    await emit_event("opportunities", "created", record=doc, user_id=current_user.user_id, user_email=current_user.email)

    return Opportunity(**doc)


@router.get("/opportunities/hierarchy")
async def get_opportunity_hierarchy(current_user=Depends(get_current_user)):
    """Get hierarchy of users/accounts for the View As dropdown."""
    if not has_perm(current_user, "opportunities.view.all") and current_user.role not in ("admin", "director"):
        raise HTTPException(status_code=403, detail="Admin/Director only")

    db = get_db()

    # Get opportunity owners with counts
    opp_pipeline = [
        {"$match": apply_soft_delete_filter({})},
        {"$group": {"_id": "$opportunity_owner_id", "count": {"$sum": 1}}},
        {"$match": {"_id": {"$ne": None}}}
    ]
    opp_owners_raw = await db.opportunities.aggregate(opp_pipeline).to_list(100)

    # Get deal owners with counts of related opportunities
    deal_pipeline = [
        {"$group": {"_id": "$deal_owner_id", "count": {"$sum": 1}}},
        {"$match": {"_id": {"$ne": None}}}
    ]
    deal_owners_raw = await db.deals.aggregate(deal_pipeline).to_list(100)

    # Get accounts with opportunity counts
    acct_pipeline = [
        {"$match": apply_soft_delete_filter({})},
        {"$group": {"_id": "$account_id", "count": {"$sum": 1}}},
        {"$match": {"_id": {"$ne": None}}}
    ]
    acct_owners_raw = await db.opportunities.aggregate(acct_pipeline).to_list(100)

    # Resolve user details
    all_user_ids = list(set(
        [o["_id"] for o in opp_owners_raw] + [d["_id"] for d in deal_owners_raw]
    ))
    users = {}
    if all_user_ids:
        user_docs = await db.users.find(
            {"user_id": {"$in": all_user_ids}},
            {"_id": 0, "user_id": 1, "name": 1, "email": 1, "role": 1}
        ).to_list(200)
        users = {u["user_id"]: u for u in user_docs}

    # Resolve account details
    all_acct_ids = [a["_id"] for a in acct_owners_raw]
    accounts = {}
    if all_acct_ids:
        acct_docs = await db.accounts.find(
            {"account_id": {"$in": all_acct_ids}},
            {"_id": 0, "account_id": 1, "name": 1}
        ).to_list(200)
        accounts = {a["account_id"]: a for a in acct_docs}

    return {
        "opportunity_owners": [
            {**users.get(o["_id"], {"user_id": o["_id"], "name": "Unknown", "email": "", "role": ""}), "count": o["count"]}
            for o in opp_owners_raw if o["_id"] in users
        ],
        "deal_owners": [
            {**users.get(d["_id"], {"user_id": d["_id"], "name": "Unknown", "email": "", "role": ""}), "count": d["count"]}
            for d in deal_owners_raw if d["_id"] in users
        ],
        "accounts": [
            {"account_id": a["_id"], "name": accounts.get(a["_id"], {}).get("name", "Unknown"), "count": a["count"]}
            for a in acct_owners_raw if a["_id"] in accounts
        ]
    }


@router.get("/opportunities/trash")
async def get_opportunity_trash(
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_user),
):
    """List soft-deleted opportunities for admin recovery."""
    if not has_perm(current_user, "opportunities.view.trash"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    filt = {"deleted_at": {"$ne": None}}
    capped = min(limit, 500)

    docs = await db.opportunities.find(
        filt, {"_id": 0}
    ).sort("deleted_at", -1).skip(skip).limit(capped).to_list(capped)

    total = await db.opportunities.count_documents(filt)

    # Enrich with deleted_by user name
    user_ids = list(set(d.get("deleted_by") for d in docs if d.get("deleted_by")))
    if user_ids:
        users = await db.users.find(
            {"user_id": {"$in": user_ids}},
            {"_id": 0, "user_id": 1, "name": 1}
        ).to_list(len(user_ids))
        user_map = {u["user_id"]: u["name"] for u in users}
        for doc in docs:
            doc["deleted_by_name"] = user_map.get(doc.get("deleted_by"), "Unknown")

    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(opportunity_id: str, current_user=Depends(get_current_user)):
    """Get a single opportunity by ID."""
    db = get_db()
    
    opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    # RBAC check
    if not has_perm(current_user, "opportunities.view.all"):
        if has_perm(current_user, "opportunities.view.own"):
            if opp.get("opportunity_owner_id") != current_user.user_id:
                raise HTTPException(status_code=403, detail="You can only view your own opportunities")
        else:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    return opp


@router.put("/opportunities/{opportunity_id}")
async def update_opportunity(
    opportunity_id: str,
    data: OpportunityUpdate,
    current_user=Depends(get_current_user)
):
    """Update an opportunity."""
    db = get_db()
    
    opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    # Check edit permissions
    if not can_edit_opportunity(current_user, opp):
        raise HTTPException(status_code=403, detail="You can only edit your own opportunities")

    # tracking_number is allocated once at creation and immutable thereafter.
    # Silently drop it from the payload (with a warning) so well-meaning clients
    # that echo the full record back don't trip a 4xx.
    if getattr(data, "tracking_number", None) is not None:
        logger.warning(
            "Ignoring tracking_number in update payload for %s; field is immutable",
            opportunity_id,
        )

    # Build update document
    update_data = {}
    
    if data.name is not None:
        update_data["name"] = data.name
    
    if data.account_id is not None:
        # Validate and fetch account name
        account = await db.accounts.find_one({"account_id": data.account_id}, {"_id": 0})
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        update_data["account_id"] = data.account_id
        update_data["account_name"] = account.get("name")
    
    if data.contact_id is not None:
        # Validate contact belongs to the account
        account_id = data.account_id if data.account_id else opp.get("account_id")
        if data.contact_id:
            contact = await db.contacts.find_one({
                "contact_id": data.contact_id,
                "account_id": account_id
            }, {"_id": 0})
            if not contact:
                raise HTTPException(status_code=404, detail="Contact not found or does not belong to this account")
            update_data["contact_id"] = data.contact_id
            update_data["contact_name"] = contact.get("name")
        else:
            update_data["contact_id"] = None
            update_data["contact_name"] = None
    
    if data.agent_of_record_id is not None:
        if data.agent_of_record_id:
            # Same selectable-partner filter as create — terminated partners rejected.
            partner = await db.accounts.find_one(
                {"$and": [{"account_id": data.agent_of_record_id}, _SELECTABLE_PARTNER_FILTER]},
                {"_id": 0},
            )
            if not partner:
                raise HTTPException(status_code=404, detail="Agent of Record not found")
            update_data["agent_of_record_id"] = data.agent_of_record_id
            update_data["agent_of_record_name"] = partner.get("name")
        else:
            update_data["agent_of_record_id"] = None
            update_data["agent_of_record_name"] = None
    
    if data.assigned_sales_ops_id is not None:
        if data.assigned_sales_ops_id:
            sales_ops = await db.users.find_one({"user_id": data.assigned_sales_ops_id}, {"_id": 0})
            if not sales_ops:
                raise HTTPException(status_code=404, detail="Sales Ops person not found")
            update_data["assigned_sales_ops_id"] = data.assigned_sales_ops_id
            update_data["assigned_sales_ops_name"] = sales_ops.get("name")
        else:
            update_data["assigned_sales_ops_id"] = None
            update_data["assigned_sales_ops_name"] = None
    
    if data.description is not None:
        update_data["description"] = data.description
    if data.desired_proposal_date is not None:
        update_data["desired_proposal_date"] = data.desired_proposal_date
    # close_date is editable independently of desired_proposal_date. An
    # explicit empty string is treated as "clear the field" so the UI
    # can remove an existing close date without a separate endpoint;
    # Pydantic's ``exclude_unset`` keeps unset fields out of the patch.
    if data.close_date is not None:
        update_data["close_date"] = data.close_date or None
    if data.priority is not None:
        update_data["priority"] = data.priority
    if data.amount is not None:
        update_data["amount"] = data.amount
    if data.stage is not None:
        if data.stage not in OPPORTUNITY_STAGES:
            raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {', '.join(OPPORTUNITY_STAGES)}")
        update_data["stage"] = data.stage
    if data.commission_payment_method is not None:
        update_data["commission_payment_method"] = data.commission_payment_method
    if data.lines_of_business is not None:
        update_data["lines_of_business"] = data.lines_of_business
    
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    await db.opportunities.update_one(
        apply_soft_delete_filter({"opportunity_id": opportunity_id}),
        {"$set": update_data}
    )
    
    logger.info(f"Opportunity updated: {opportunity_id} by {current_user.email}")

    updated_opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    await emit_event("opportunities", "updated", record=updated_opp, old_record=opp, user_id=current_user.user_id, user_email=current_user.email)
    return updated_opp


@router.delete("/opportunities/{opportunity_id}")
async def delete_opportunity(opportunity_id: str, current_user=Depends(get_current_user)):
    """Soft-delete an opportunity.

    Sets deleted_at + deleted_by on the document. The document is
    not removed from MongoDB — use POST /opportunities/{id}/restore
    to reverse. Admins with opportunities.view.trash can see
    soft-deleted items via GET /opportunities/trash.
    """
    if not has_perm(current_user, "opportunities.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    # Atomic: only soft-delete if not already deleted
    now = datetime.now(timezone.utc)
    result = await db.opportunities.update_one(
        {"opportunity_id": opportunity_id, "deleted_at": None},
        {"$set": {
            "deleted_at": now,
            "deleted_by": current_user.user_id,
        }},
    )

    if result.matched_count == 0:
        # Disambiguate: does it exist at all?
        existing = await db.opportunities.find_one(
            {"opportunity_id": opportunity_id}, {"_id": 0}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        # It exists but already deleted
        raise HTTPException(
            status_code=409,
            detail="Opportunity is already in trash. Use /restore to recover."
        )

    # Fetch the now-soft-deleted record for the event emission
    record = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id}, {"_id": 0}
    )

    logger.info(f"Opportunity soft-deleted: {opportunity_id} by {current_user.email}")
    await emit_event(
        "opportunities", "soft_deleted",
        record=record,
        user_id=current_user.user_id,
    )

    return {
        "message": "Opportunity moved to trash",
        "opportunity_id": opportunity_id,
        "deleted_at": now.isoformat(),
        "restorable": True,
    }


@router.post("/opportunities/{opportunity_id}/restore")
async def restore_opportunity(
    opportunity_id: str,
    current_user=Depends(get_current_user),
):
    """Restore a soft-deleted opportunity from trash."""
    if not has_perm(current_user, "opportunities.restore"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    # Atomic: only restore if currently deleted
    result = await db.opportunities.update_one(
        {"opportunity_id": opportunity_id, "deleted_at": {"$ne": None}},
        {"$set": {"deleted_at": None, "deleted_by": None}},
    )

    if result.matched_count == 0:
        existing = await db.opportunities.find_one(
            {"opportunity_id": opportunity_id}, {"_id": 0}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        raise HTTPException(
            status_code=409,
            detail="Opportunity is not in trash — nothing to restore."
        )

    record = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id}, {"_id": 0}
    )

    logger.info(f"Opportunity restored: {opportunity_id} by {current_user.email}")
    await emit_event(
        "opportunities", "restored",
        record=record,
        user_id=current_user.user_id,
    )

    return {
        "message": "Opportunity restored",
        "opportunity_id": opportunity_id,
    }


@router.patch("/opportunities/{opportunity_id}/stage")
async def update_opportunity_stage(
    opportunity_id: str,
    stage: str,
    current_user=Depends(get_current_user)
):
    """Update only the stage of an opportunity (for Kanban drag-and-drop)."""
    db = get_db()
    
    if stage not in OPPORTUNITY_STAGES:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {', '.join(OPPORTUNITY_STAGES)}")
    
    opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    if not can_edit_opportunity(current_user, opp):
        raise HTTPException(status_code=403, detail="You can only edit your own opportunities")
    
    await db.opportunities.update_one(
        apply_soft_delete_filter({"opportunity_id": opportunity_id}),
        {"$set": {"stage": stage, "updated_at": datetime.now(timezone.utc)}}
    )
    
    logger.info(f"Opportunity stage updated: {opportunity_id} -> {stage} by {current_user.email}")
    await emit_event("opportunities", "stage_changed", record={"opportunity_id": opportunity_id, "stage": stage}, old_record=opp, user_id=current_user.user_id, user_email=current_user.email)

    return {"message": "Stage updated successfully", "opportunity_id": opportunity_id, "stage": stage}


# ---- NOTES ----

@router.post("/opportunities/{opportunity_id}/notes")
async def add_opportunity_note(
    opportunity_id: str,
    note_text: str,
    current_user=Depends(get_current_user)
):
    """Add a note to an opportunity."""
    db = get_db()
    
    opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    if not can_edit_opportunity(current_user, opp):
        raise HTTPException(status_code=403, detail="You can only add notes to your own opportunities")
    
    note = {
        "note_id": f"note_{uuid.uuid4().hex[:8]}",
        "text": note_text,
        "created_by": current_user.user_id,
        "created_by_name": current_user.name,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.opportunities.update_one(
        apply_soft_delete_filter({"opportunity_id": opportunity_id}),
        {
            "$push": {"notes": note},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    
    logger.info(f"Note added to opportunity: {opportunity_id} by {current_user.email}")
    
    return {"message": "Note added successfully", "note": note}


@router.delete("/opportunities/{opportunity_id}/notes/{note_id}")
async def delete_opportunity_note(
    opportunity_id: str,
    note_id: str,
    current_user=Depends(get_current_user)
):
    """Delete a note from an opportunity."""
    db = get_db()
    
    opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    if not can_edit_opportunity(current_user, opp):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    await db.opportunities.update_one(
        apply_soft_delete_filter({"opportunity_id": opportunity_id}),
        {
            "$pull": {"notes": {"note_id": note_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    
    return {"message": "Note deleted successfully"}


# ---- ATTACHMENTS ----

@router.post("/opportunities/{opportunity_id}/attachments")
async def upload_attachment(
    opportunity_id: str,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):
    """Upload an attachment to an opportunity."""
    db = get_db()

    # Validate file extension and size
    ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".txt", ".eml"}
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

    file_ext = os.path.splitext(file.filename or "")[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type '{file_ext}' not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    if not can_edit_opportunity(current_user, opp):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Create uploads directory if it doesn't exist
    upload_dir = f"/app/backend/uploads/opportunities/{opportunity_id}"
    os.makedirs(upload_dir, exist_ok=True)

    # Generate unique filename
    unique_filename = f"{uuid.uuid4().hex[:12]}{file_ext}"
    file_path = os.path.join(upload_dir, unique_filename)

    # Save file with size check
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)} MB")
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Create attachment record
    attachment = {
        "attachment_id": f"att_{uuid.uuid4().hex[:8]}",
        "filename": file.filename,
        "stored_filename": unique_filename,
        "file_path": file_path,
        "file_size": len(content),
        "content_type": file.content_type,
        "uploaded_by": current_user.user_id,
        "uploaded_by_name": current_user.name,
        "uploaded_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.opportunities.update_one(
        apply_soft_delete_filter({"opportunity_id": opportunity_id}),
        {
            "$push": {"attachments": attachment},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    
    logger.info(f"Attachment uploaded to opportunity: {opportunity_id} by {current_user.email}")
    
    return {"message": "Attachment uploaded successfully", "attachment": attachment}


@router.get("/opportunities/{opportunity_id}/attachments/{attachment_id}/download")
async def download_attachment(
    opportunity_id: str,
    attachment_id: str,
    current_user=Depends(get_current_user),
):
    """Serve an opportunity attachment for inline viewing or download."""
    db = get_db()

    opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    # RBAC — mirrors GET /opportunities/{id}: anyone allowed to view the
    # opportunity may view its attachments.
    if not has_perm(current_user, "opportunities.view.all"):
        if has_perm(current_user, "opportunities.view.own"):
            if opp.get("opportunity_owner_id") != current_user.user_id:
                raise HTTPException(status_code=403, detail="You can only view your own opportunities")
        else:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

    attachment = next(
        (att for att in opp.get("attachments", []) if att["attachment_id"] == attachment_id),
        None,
    )
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = attachment.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Stored file not found")

    # Derive the media type from the stored filename extension — never from the
    # upload-supplied content_type, which a client can spoof (e.g. text/html on
    # a .txt file) to get attacker HTML served inline as stored XSS. Every
    # allowed extension resolves to a non-script type; unknown ones fall back to
    # octet-stream so the browser downloads rather than renders them.
    guessed_type, _ = mimetypes.guess_type(attachment.get("filename") or "")
    return FileResponse(
        path=file_path,
        media_type=guessed_type or "application/octet-stream",
        filename=attachment.get("filename"),
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/opportunities/{opportunity_id}/attachments/{attachment_id}")
async def delete_attachment(
    opportunity_id: str,
    attachment_id: str,
    current_user=Depends(get_current_user)
):
    """Delete an attachment from an opportunity."""
    db = get_db()
    
    opp = await db.opportunities.find_one(apply_soft_delete_filter({"opportunity_id": opportunity_id}), {"_id": 0})
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    if not can_edit_opportunity(current_user, opp):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Find the attachment to get file path
    attachment = next((att for att in opp.get("attachments", []) if att["attachment_id"] == attachment_id), None)
    if attachment:
        # Delete physical file
        try:
            if os.path.exists(attachment["file_path"]):
                os.remove(attachment["file_path"])
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
    
    # Remove from database
    await db.opportunities.update_one(
        apply_soft_delete_filter({"opportunity_id": opportunity_id}),
        {
            "$pull": {"attachments": {"attachment_id": attachment_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    
    return {"message": "Attachment deleted successfully"}


# ---- DROPDOWN DATA ----

@router.get("/opportunities/dropdown-data/all")
async def get_dropdown_data(
    account_id: Optional[str] = None,
    current_user=Depends(get_current_user)
):
    """Get all dropdown data for opportunity forms (accounts, contacts, users, partners)."""
    db = get_db()
    
    # Get accounts with is_test_data flag
    accounts = await db.accounts.find(
        {"is_active": {"$ne": False}},
        {"_id": 0, "account_id": 1, "name": 1, "is_test_data": 1}
    ).sort("name", 1).to_list(1000)
    
    # Get contacts (filtered by account if provided) with is_test_data flag
    contact_filter = {"is_active": {"$ne": False}}
    if account_id:
        contact_filter["account_id"] = account_id
    
    contacts = await db.contacts.find(
        contact_filter,
        {"_id": 0, "contact_id": 1, "name": 1, "account_id": 1, "is_test_data": 1}
    ).sort("name", 1).to_list(1000)
    
    # Get users (for opportunity owner and sales ops) with is_test_data flag
    users = await db.users.find(
        {"is_active": True},
        {"_id": 0, "user_id": 1, "name": 1, "email": 1, "role": 1}
    ).sort("name", 1).to_list(500)
    
    # Add is_test_data flag for users (all system users are not test data unless specified)
    for u in users:
        u["is_test_data"] = False  # Users don't have test flag, default to False
    
    # Get partners (for agent of record) with is_test_data flag. Uses the same
    # _SELECTABLE_PARTNER_FILTER as the create/update validators so the dropdown
    # can never offer a terminated partner that the server would then reject.
    # account_id is aliased to partner_id below so the frontend shape is stable.
    partner_accounts = await db.accounts.find(
        _SELECTABLE_PARTNER_FILTER,
        {"_id": 0, "account_id": 1, "name": 1, "is_test_data": 1},
    ).sort("name", 1).to_list(500)
    partners = [
        {
            "partner_id": a["account_id"],
            "name": a.get("name", ""),
            "is_test_data": a.get("is_test_data", False),
        }
        for a in partner_accounts
    ]
    
    return {
        "accounts": accounts,
        "contacts": contacts,
        "users": users,
        "partners": partners,
        "stages": OPPORTUNITY_STAGES,
        "priorities": ["high", "medium", "low"],
        "lines_of_business": [
            "professional_services",
            "field_services",
            "referral_mrr",
            "wholesale_mrr"
        ],
        "commission_payment_methods": [
            "telarus",
            "appdirect",
            "direct",
            "custom"
        ]
    }


# ---- STATISTICS ----

@router.get("/opportunities/stats/by-stage")
async def get_opportunities_by_stage(current_user=Depends(get_current_user)):
    """Get count of opportunities grouped by stage."""
    db = get_db()
    
    # Build base filter based on permissions
    base_filter = {}
    if not has_perm(current_user, "opportunities.view.all"):
        if has_perm(current_user, "opportunities.view.own"):
            base_filter["opportunity_owner_id"] = current_user.user_id
        else:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Initialize stats
    stats = {stage: {"count": 0, "total_amount": 0} for stage in OPPORTUNITY_STAGES}
    
    # Get all opportunities
    opportunities = await db.opportunities.find(apply_soft_delete_filter(base_filter), {"_id": 0, "stage": 1, "amount": 1}).to_list(5000)
    
    for opp in opportunities:
        stage = opp.get("stage", "prospect_opp")
        if stage in stats:
            stats[stage]["count"] += 1
            stats[stage]["total_amount"] += opp.get("amount", 0)
    
    return stats


# ---- OPPORTUNITY TO DEAL CONVERSION ----

@router.post("/opportunities/{opportunity_id}/convert-to-deal")
async def convert_opportunity_to_deal(
    opportunity_id: str,
    current_user=Depends(get_current_user)
):
    """
    Convert a Closed Won opportunity to a Deal in Deal Management.
    Phase 3C Enhancement: Auto-creates Contracts from SOW + Procurement data.
    
    Requirements:
    - Opportunity must be in 'closed_won' stage
    - User must have edit permissions
    - Creates a new Deal with stage 'Closed Won'
    - Creates Contract(s) from OpportunitySOW data (1 per LOB)
    - Maps OpportunityResourceAssignment → Contract.resources
    - Marks opportunity as converted (prevents duplicate conversions)
    """
    db = get_db()
    
    # Fetch the opportunity
    opportunity = await db.opportunities.find_one(
        apply_soft_delete_filter({"opportunity_id": opportunity_id}),
        {"_id": 0}
    )
    
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    # Check permissions
    if not can_edit_opportunity(current_user, opportunity):
        raise HTTPException(status_code=403, detail="Insufficient permissions to convert this opportunity")
    
    # Validate stage
    if opportunity.get("stage") != "closed_won":
        raise HTTPException(
            status_code=400, 
            detail="Only opportunities in 'Closed Won' stage can be converted to deals"
        )
    
    # Check if already converted
    if opportunity.get("converted_to_deal_id"):
        raise HTTPException(
            status_code=400,
            detail=f"This opportunity has already been converted to deal {opportunity.get('converted_to_deal_id')}"
        )
    
    # Create the Deal
    deal_id = f"deal_{uuid.uuid4().hex[:12]}"
    
    # Map opportunity fields to deal fields
    deal_doc = {
        "deal_id": deal_id,
        "name": opportunity.get("name"),
        "account_id": opportunity.get("account_id"),
        "account_name": opportunity.get("account_name"),
        "stage": "Closed Won",  # Fixed stage for all converted deals
        "amount": opportunity.get("amount", 0.0),
        "close_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "deal_owner_id": opportunity.get("opportunity_owner_id"),
        "deal_owner_name": opportunity.get("opportunity_owner_name"),
        "assigned_sales_rep_id": opportunity.get("assigned_sales_ops_id"),  # Map Sales Ops to Sales Rep
        "assigned_sales_rep_name": opportunity.get("assigned_sales_ops_name"),
        "assigned_agent_id": opportunity.get("agent_of_record_id"),  # Agent of Record
        "agent_of_record_name": opportunity.get("agent_of_record_name"),
        "probability": 100,  # Closed Won = 100% probability
        "description": opportunity.get("description", ""),
        "zoho_deal_id": None,  # Not from Zoho
        "source": "opportunity_conversion",  # Track conversion source
        "deal_types": opportunity.get("lines_of_business", []),  # Map LOB to deal_types
        "is_test_data": opportunity.get("is_test_data", False),  # Preserve test data flag
        "created_at": datetime.now(timezone.utc),
        
        # Preserve opportunity metadata
        "converted_from_opportunity_id": opportunity_id,
        "commission_payment_method": opportunity.get("commission_payment_method"),
    }
    
    try:
        # Insert the new Deal
        await db.deals.insert_one(deal_doc)
        
        # Phase 3C: Create Contracts from SOWs
        created_contracts = []
        
        # Fetch all SOWs for this opportunity
        sows = await db.opportunity_sows.find(
            {"opportunity_id": opportunity_id},
            {"_id": 0}
        ).to_list(100)
        
        # Create one Contract per completed SOW
        for sow in sows:
            # Only create contract if SOW is complete
            if sow.get("status") != "complete":
                logger.warning(f"Skipping Contract creation for incomplete SOW {sow.get('sow_id')} (status: {sow.get('status')})")
                continue
            
            contract_id = f"contract_{uuid.uuid4().hex[:12]}"
            lob = sow.get("line_of_business")
            
            # Map contract type (BOH → BOA in contract schema)
            sow_contract_type = sow.get("contract_type", "fixed_fee")
            contract_type = "boa" if sow_contract_type == "boh" else sow_contract_type
            
            # Map billing terms
            billing_period = sow.get("billing_terms", "monthly")  # weekly, biweekly, monthly
            invoice_terms = sow.get("payment_terms", "net_30")  # net_14, net_30, net_45, net_60
            
            # Build assigned resources from SOW's selected resource assignment IDs
            contract_resources = []
            resource_type_map = {}  # Track resource types for aggregation
            
            # Fetch ONLY the resources that were selected in this specific SOW
            selected_assignment_ids = sow.get("selected_resource_assignment_ids", [])
            
            if selected_assignment_ids:
                # Fetch the selected resource assignments
                resource_assignments = await db.opportunity_resource_assignments.find(
                    {
                        "opportunity_id": opportunity_id,
                        "assignment_id": {"$in": selected_assignment_ids}
                    },
                    {"_id": 0}
                ).to_list(100)
                
                for assignment in resource_assignments:
                    # Map resource assignment to contract resource format
                    resource = {
                        "user_id": assignment.get("contractor_user_id") or assignment.get("internal_user_id"),
                        "name": assignment.get("contractor_user_name") or assignment.get("internal_user_name"),
                        "resource_type_id": assignment.get("resource_type_id"),
                        "cost_rate": assignment.get("cost_rate", 0.0),
                        "role": assignment.get("resource_type_name")
                    }
                    
                    if resource["user_id"]:  # Only add if we have a valid user
                        contract_resources.append(resource)
                        
                        # Track resource type for aggregation
                        rt_id = assignment.get("resource_type_id")
                        if rt_id not in resource_type_map:
                            resource_type_map[rt_id] = {
                                "resource_type_id": rt_id,
                                "name": assignment.get("resource_type_name"),
                                "sell_rate": assignment.get("sell_rate", 0.0),
                                "cost_rate": assignment.get("cost_rate", 0.0),
                                "hours_purchased": assignment.get("estimated_hours", 0.0) if assignment.get("pricing_model") == "block_hours" else 0.0,
                                "hours_used": 0.0,
                                "hours_remaining": assignment.get("estimated_hours", 0.0) if assignment.get("pricing_model") == "block_hours" else 0.0
                            }
            
            # Build resource_types array
            contract_resource_types = list(resource_type_map.values())
            
            # Calculate financial totals from SOW
            total_hours_logged = 0.0
            if contract_type == "boa":
                # Sum estimated hours from all milestones
                for milestone in sow.get("milestones", []):
                    for bullet in milestone.get("bullets", []):
                        total_hours_logged += bullet.get("estimated_hours", 0.0)
            
            # Create Contract document
            contract_doc = {
                "contract_id": contract_id,
                "name": f"{opportunity.get('name')} - {lob.replace('_', ' ').title()} - Contract",
                "account_id": opportunity.get("account_id"),
                "account_name": opportunity.get("account_name"),
                "contract_type": contract_type,
                "line_of_business": lob,
                "service_category": lob,  # Match line_of_business
                "status": "active",
                "deal_ids": [deal_id],  # Link to the Deal
                "project_ids": [],
                
                # Sales & Agent assignments
                "assigned_sales_rep_id": opportunity.get("assigned_sales_ops_id"),
                "assigned_agent_id": opportunity.get("agent_of_record_id"),
                
                # Financial from SOW
                "start_date": sow.get("start_date"),
                "end_date": sow.get("end_date"),
                "billing_period": billing_period,
                "invoice_terms": invoice_terms,
                
                # Resource tracking
                "resource_types": contract_resource_types,
                "resources": contract_resources,
                
                # Hours & Cost tracking
                "total_hours_logged": total_hours_logged,
                "total_billed": 0.0,
                
                # Fixed amount or monthly rate
                "total_hours_purchased": 0.0,
                "hours_consumed": 0.0,
                "fixed_amount": sow.get("total_amount", 0.0) if contract_type == "fixed_fee" else 0.0,
                "monthly_rate": sow.get("monthly_rate", 0.0) if contract_type == "monthly" else 0.0,
                
                # Metadata
                "notes": f"Auto-generated from Opportunity {opportunity_id} SOW {sow.get('sow_id')}",
                "is_test_data": opportunity.get("is_test_data", False),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            
            await db.contracts.insert_one(contract_doc)
            created_contracts.append({
                "contract_id": contract_id,
                "line_of_business": lob,
                "sow_id": sow.get("sow_id")
            })
            
            logger.info(f"Contract {contract_id} created from SOW {sow.get('sow_id')} for LOB {lob}")
        
        # Mark opportunity as converted
        await db.opportunities.update_one(
            apply_soft_delete_filter({"opportunity_id": opportunity_id}),
            {
                "$set": {
                    "converted_to_deal_id": deal_id,
                    "converted_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        logger.info(f"Opportunity {opportunity_id} converted to Deal {deal_id} with {len(created_contracts)} contract(s) by user {current_user.user_id}")
        
        return {
            "message": "Opportunity successfully converted to Deal with Contracts",
            "deal_id": deal_id,
            "opportunity_id": opportunity_id,
            "contracts_created": created_contracts,
            "sows_processed": len(sows),
            "sows_converted": len(created_contracts)
        }
        
    except Exception as e:
        logger.error(f"Failed to convert opportunity {opportunity_id} to deal: {e}")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")
