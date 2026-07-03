from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from datetime import datetime, timezone
import logging
from database import get_db
from auth_helper import get_current_user, has_perm
from models import (
    ContractorBusiness,
    ContractorBusinessCreate,
    ContractorBusinessUpdate,
    gen_id
)
from services.policy_engine import evaluate_policies
from services.policy_helpers import enforce_policy_result
from services.field_registry import register_fields, register_module_events, register_module_config
from services.field_options import CONTRACTOR_BUSINESS_STATUSES, APPROVAL_STATUSES

router = APIRouter()
logger = logging.getLogger(__name__)

register_module_config("contractor_management", "contractor_businesses", "business_id", "Contractors")
register_module_events("contractor_management", "contractor_business", ["created", "updated", "deleted"])

# Register fields available for policy evaluation
register_fields("contractor_management", "contractor_business", [
    {"name": "status", "type": "string", "label": "Business Status", "options": CONTRACTOR_BUSINESS_STATUSES, "description": "Contractor business account status (active, inactive, suspended)"},
    {"name": "active_contractor_count", "type": "number", "label": "Active Contractor Count", "computed": True, "description": "Number of active individual contractors under this business"},
])

register_fields("contractor_management", "contractor_resource_type", [
    {"name": "approval_status", "type": "string", "label": "Approval Status", "options": APPROVAL_STATUSES, "description": "Whether this resource type/rate has been approved for use"},
    {"name": "cost_rate", "type": "number", "label": "Cost Rate", "description": "Hourly cost rate charged by the contractor for this resource type"},
])


@router.get("/contractor-businesses")
async def get_contractor_businesses(
    status: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all contractor businesses. Requires admin.contractors.view permission."""
    if not has_perm(current_user, "admin.contractors.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    query = {}
    if status:
        query["status"] = status
    
    businesses = await db.contractor_businesses.find(query, {"_id": 0}).sort("business_name", 1).to_list(1000)
    
    # Get contractor counts in a single query using aggregation
    business_ids = [b["business_id"] for b in businesses]
    counts = await db.users.aggregate([
        {"$match": {"contractor_business_id": {"$in": business_ids}, "role": "contractor", "is_active": True}},
        {"$group": {"_id": "$contractor_business_id", "count": {"$sum": 1}}}
    ]).to_list(None)
    count_map = {c["_id"]: c["count"] for c in counts}
    for business in businesses:
        business["contractor_count"] = count_map.get(business["business_id"], 0)
    
    return {"items": businesses}


@router.get("/contractor-businesses/{business_id}")
async def get_contractor_business(
    business_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get a specific contractor business by ID."""
    if not has_perm(current_user, "admin.contractors.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    business = await db.contractor_businesses.find_one({"business_id": business_id}, {"_id": 0})
    
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    # Get contractors under this business
    contractors = await db.users.find({
        "contractor_business_id": business_id,
        "role": "contractor"
    }, {"_id": 0}).to_list(100)
    
    business["contractors"] = contractors
    
    return business


@router.post("/contractor-businesses")
async def create_contractor_business(
    data: ContractorBusinessCreate,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Create a new contractor business. Requires admin.contractors.create permission."""
    if not has_perm(current_user, "admin.contractors.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    # Check if business name already exists
    existing = await db.contractor_businesses.find_one({
        "business_name": data.business_name,
        "status": {"$ne": "deleted"}
    })
    
    if existing:
        raise HTTPException(status_code=400, detail="A contractor business with this name already exists")
    
    business_id = gen_id("cb_")
    now = datetime.now(timezone.utc)
    
    business_doc = {
        "business_id": business_id,
        **data.model_dump(),
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id
    }
    
    await db.contractor_businesses.insert_one(business_doc)
    
    business_doc.pop("_id", None)
    return business_doc


@router.put("/contractor-businesses/{business_id}")
async def update_contractor_business(
    business_id: str,
    data: ContractorBusinessUpdate,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Update a contractor business. Requires admin.contractors.edit permission."""
    if not has_perm(current_user, "admin.contractors.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    existing = await db.contractor_businesses.find_one({"business_id": business_id})
    
    if not existing:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    # Check for duplicate business name if changing name
    if data.business_name and data.business_name != existing.get("business_name"):
        duplicate = await db.contractor_businesses.find_one({
            "business_name": data.business_name,
            "business_id": {"$ne": business_id},
            "status": {"$ne": "deleted"}
        })
        if duplicate:
            raise HTTPException(status_code=400, detail="A contractor business with this name already exists")
    
    # Build update payload — allow clearing optional fields by sending null
    clearable_fields = ["phone", "website", "email", "contact_name", "notes",
                        "address_street", "address_city", "address_state", "address_zip",
                        "tax_id_ein", "payment_method", "w9_file_url",
                        "billing_contact_name", "billing_email", "billing_phone",
                        "billing_address_street", "billing_address_city", "billing_address_state", "billing_address_zip",
                        "bank_account_details"]
    update_data = {}
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None:
            update_data[k] = v
        elif k in clearable_fields:
            update_data[k] = None
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {"$set": update_data}
    )
    
    updated = await db.contractor_businesses.find_one({"business_id": business_id}, {"_id": 0})
    return updated


@router.delete("/contractor-businesses/{business_id}")
async def delete_contractor_business(
    business_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Soft delete a contractor business. Requires admin.contractors.delete permission."""
    if not has_perm(current_user, "admin.contractors.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    existing = await db.contractor_businesses.find_one({"business_id": business_id})
    
    if not existing:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    # Policy engine: check deletion guard
    contractor_count = await db.users.count_documents({
        "contractor_business_id": business_id,
        "role": "contractor",
        "is_active": True
    })
    policy_payload = {"active_contractor_count": contractor_count}
    result = await evaluate_policies(db, "contractor_management", "contractor_business", "on_delete", policy_payload, user_role=current_user.role)
    enforce_policy_result(result)
    
    # Soft delete by setting status to inactive
    await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {"$set": {
            "status": "inactive",
            "updated_at": datetime.now(timezone.utc)
        }}
    )
    
    return {"message": "Contractor business deactivated successfully"}


@router.get("/contractor-businesses/{business_id}/contractors")
async def get_business_contractors(
    business_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all contractors under a specific business."""
    if not has_perm(current_user, "admin.contractors.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    business = await db.contractor_businesses.find_one({"business_id": business_id})
    
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    contractors = await db.users.find({
        "contractor_business_id": business_id,
        "role": "contractor"
    }, {"_id": 0}).sort("name", 1).to_list(100)
    
    return {"items": contractors, "business_name": business.get("business_name")}



# ---- GRIEVANCES ----

@router.post("/contractor-businesses/{business_id}/grievances")
async def add_grievance(
    business_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Add a grievance/complaint about a contractor."""
    if not has_perm(current_user, "admin.contractors.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    # Verify contractor exists
    business = await db.contractor_businesses.find_one({"business_id": business_id}, {"_id": 0})
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    # Create grievance
    from models import gen_id
    grievance = {
        "grievance_id": f"grv_{gen_id()}",
        "contractor_business_id": business_id,
        "grievance_date": data.get("grievance_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "reporter_name": data.get("reporter_name"),
        "reporter_type": data.get("reporter_type", "internal"),
        "severity": data.get("severity", "medium"),
        "description": data.get("description"),
        "resolved": False,
        "affects_rating": data.get("affects_rating", False),
        "created_at": datetime.now(timezone.utc),
        "created_by": current_user.user_id
    }
    
    # Add to contractor's grievances array
    await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {
            "$push": {"grievances": grievance},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    
    logger.info(f"Grievance added to contractor {business_id}")
    
    return {"message": "Grievance recorded", "grievance": grievance}


@router.put("/grievances/{grievance_id}/resolve")
async def resolve_grievance(
    grievance_id: str,
    resolution_notes: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Mark a grievance as resolved."""
    if not has_perm(current_user, "admin.contractors.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    # Find and update the grievance
    result = await db.contractor_businesses.update_one(
        {"grievances.grievance_id": grievance_id},
        {
            "$set": {
                "grievances.$.resolved": True,
                "grievances.$.resolution_notes": resolution_notes,
                "grievances.$.resolved_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "grievances.$.resolved_by": current_user.name,
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Grievance not found")
    
    return {"message": "Grievance resolved"}




# ---- RESOURCE TYPES OFFERED (with Procurement Approval) ----

@router.get("/contractor-businesses/{business_id}/resource-types")
async def get_contractor_resource_types(
    business_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all resource types offered by this contractor business."""
    if not has_perm(current_user, "admin.contractors.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    business = await db.contractor_businesses.find_one(
        {"business_id": business_id},
        {"_id": 0, "resource_types_offered": 1}
    )
    
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    resource_types = business.get("resource_types_offered", [])
    return {"items": resource_types}


@router.post("/contractor-businesses/{business_id}/resource-types")
async def add_contractor_resource_type(
    business_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Add a resource type to contractor business.
    Auto-approves if cost_rate <= default_cost_rate.
    Requires Procurement approval if cost_rate > default_cost_rate.
    """
    if not has_perm(current_user, "admin.contractors.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Verify contractor business exists
    business = await db.contractor_businesses.find_one({"business_id": business_id}, {"_id": 0})
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")

    # Verify resource type exists in default_resource_types collection
    resource_type_id = data.get("resource_type_id")
    resource_type = await db.default_resource_types.find_one(
        {"name": resource_type_id, "is_active": True},
        {"_id": 0}
    )
    if not resource_type:
        raise HTTPException(status_code=404, detail="Resource type not found")
    
    # Add resource_type_id for consistency (same as name)
    resource_type["resource_type_id"] = resource_type["name"]
    
    # Get default cost rate from resource type
    default_cost_rate = resource_type.get("default_cost_rate", resource_type.get("cost_rate", 0.0))
    contractor_cost_rate = float(data.get("cost_rate", default_cost_rate))
    
    # ALWAYS PENDING ON FIRST ADD: Business-level resource types always need Procurement approval
    approval_status = "pending"
    
    # Build resource type object
    from models import ContractorBusinessResourceType
    resource_doc = {
        "resource_type_id": resource_type_id,
        "resource_type_name": resource_type.get("name"),
        "pricing_models_accepted": data.get("pricing_models_accepted", ["block_hours", "bill_actuals"]),
        "rate_cards": data.get("rate_cards", {}),
        "cost_rate": contractor_cost_rate,
        "default_cost_rate": default_cost_rate,
        "is_locked": False,  # Will be locked after approval
        "approval_status": approval_status,  # Always pending
        "approved_by": None,
        "approved_by_name": None,
        "approved_date": None,
        "rejection_reason": None,
        "is_active": True,
        "added_at": datetime.now(timezone.utc)
    }
    
    # Check if resource type already exists for this contractor
    existing_rt = next(
        (rt for rt in business.get("resource_types_offered", []) if rt.get("resource_type_id") == resource_type_id),
        None
    )
    
    if existing_rt:
        # Update existing
        await db.contractor_businesses.update_one(
            {"business_id": business_id, "resource_types_offered.resource_type_id": resource_type_id},
            {
                "$set": {
                    "resource_types_offered.$": resource_doc,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        message = f"Resource type updated. Pending Procurement approval"
    else:
        # Add new
        await db.contractor_businesses.update_one(
            {"business_id": business_id},
            {
                "$push": {"resource_types_offered": resource_doc},
                "$set": {"updated_at": datetime.now(timezone.utc)}
            }
        )
        message = f"Resource type added. Pending Procurement approval"
    
    logger.info(f"Resource type {resource_type_id} added to contractor {business_id} with status {approval_status}")
    
    return {"message": message, "approval_status": approval_status}


@router.put("/contractor-businesses/{business_id}/resource-types/{resource_type_id}/approve")
async def approve_contractor_resource_type(
    business_id: str,
    resource_type_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Approve or reject a contractor resource type (Procurement only).
    data: {"action": "approve" | "reject", "rejection_reason": "..."}
    """
    if not has_perm(current_user, "admin.contractors.approve"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    action = data.get("action")
    
    if action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")
    
    # Update the specific resource type in the array
    update_fields = {
        "resource_types_offered.$[elem].approval_status": "approved" if action == "approve" else "rejected",
        "resource_types_offered.$[elem].approved_by": current_user.user_id,
        "resource_types_offered.$[elem].approved_by_name": current_user.name,
        "resource_types_offered.$[elem].approved_date": datetime.now(timezone.utc),
    }
    
    if action == "approve":
        # Lock the rate after approval
        update_fields["resource_types_offered.$[elem].is_locked"] = True
    elif action == "reject":
        update_fields["resource_types_offered.$[elem].rejection_reason"] = data.get("rejection_reason", "")
    
    result = await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {"$set": update_fields},
        array_filters=[{"elem.resource_type_id": resource_type_id}]
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Resource type not found")
    
    logger.info(f"Resource type {resource_type_id} for contractor {business_id} {action}d by {current_user.name}")
    
    return {"message": f"Resource type {action}d successfully"}


@router.delete("/contractor-businesses/{business_id}/resource-types/{resource_type_id}")
async def delete_contractor_resource_type(
    business_id: str,
    resource_type_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Remove a resource type from a contractor business."""
    if not has_perm(current_user, "admin.contractors.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")

    result = await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {
            "$pull": {"resource_types_offered": {"resource_type_id": resource_type_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Resource type not found")

    logger.info(f"Resource type {resource_type_id} removed from contractor {business_id}")

    return {"message": "Resource type removed successfully"}


# ---- PAYMENT ACTIVITY ----

@router.get("/contractor-businesses/{business_id}/payment-activity")
async def get_payment_activity(
    business_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    current_user=Depends(get_current_user)
):
    """Get time entries and AP bills for a contractor business."""
    if not has_perm(current_user, "admin.contractors.view") and not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    # Get all users in this business
    business_users = await db.users.find(
        {"business_id": business_id},
        {"_id": 0, "user_id": 1, "name": 1}
    ).to_list(100)
    user_ids = [u["user_id"] for u in business_users]
    user_names = {u["user_id"]: u["name"] for u in business_users}

    # Build time entry filter
    te_filter = {"user_id": {"$in": user_ids}} if user_ids else {"_id": {"$exists": False}}
    if date_from:
        te_filter.setdefault("date", {})["$gte"] = date_from
    if date_to:
        te_filter.setdefault("date", {})["$lte"] = date_to
    if status:
        te_filter["approval_status"] = status

    time_entries = await db.time_entries.find(te_filter, {"_id": 0}).sort("date", -1).to_list(200)

    # Enrich time entries with user names
    for te in time_entries:
        te["user_name"] = user_names.get(te.get("user_id"), "Unknown")

    # Build AP bill filter — use $and to combine $or with date/status filters
    bill_conditions = [{"$or": [
        {"contractor_business_id": business_id},
        {"vendor_id": business_id}
    ]}]
    if date_from:
        bill_conditions.append({"bill_date": {"$gte": date_from}})
    if date_to:
        bill_conditions.append({"bill_date": {"$lte": date_to}})
    if status:
        bill_conditions.append({"status": status})
    bill_filter = {"$and": bill_conditions} if len(bill_conditions) > 1 else bill_conditions[0]

    # TRANSITIONAL (B3): dual-read legacy ap_bills + new finance_drafts(kind=ap_bill).
    # Wave 2D migration is non-destructive so pre-migration bills remain in ap_bills;
    # all post-migration AP entries land in finance_drafts only. We merge both to give
    # contractors a complete billing history. Remove the legacy read once ap_bills is
    # formally retired (tracked in Wave 5 decommission plan).
    legacy_ap_bills = await db.ap_bills.find(bill_filter, {"_id": 0}).sort("bill_date", -1).to_list(200)

    # finance_drafts uses source_contractor_id instead of contractor_business_id,
    # and amount/sync_status instead of total/status.
    draft_conditions = [{"$or": [
        {"source_contractor_id": business_id},
        {"vendor_id": business_id}
    ], "kind": "ap_bill"}]
    if date_from:
        draft_conditions.append({"bill_date": {"$gte": date_from}})
    if date_to:
        draft_conditions.append({"bill_date": {"$lte": date_to}})
    if status:
        # Map legacy status values to finance_drafts sync_status equivalents
        status_map = {"paid": "Synced", "approved": "Sent", "pending": "Draft", "draft": "Draft"}
        draft_conditions.append({"sync_status": status_map.get(status, status)})
    draft_filter = {"$and": draft_conditions} if len(draft_conditions) > 1 else draft_conditions[0]
    new_ap_drafts = await db["finance_drafts"].find(draft_filter, {"_id": 0}).sort("created_at", -1).to_list(200)

    # Normalize new drafts to the legacy shape so downstream summary code is uniform
    for d in new_ap_drafts:
        d.setdefault("bill_date", d.get("created_at"))
        d.setdefault("status", "paid" if d.get("sync_status") == "Synced" else
                               "pending" if d.get("sync_status") in ("Draft", "Sent") else
                               d.get("sync_status", "unknown"))

    ap_bills = sorted(
        legacy_ap_bills + new_ap_drafts,
        key=lambda b: b.get("bill_date") or b.get("created_at") or "",
        reverse=True,
    )[:200]

    # Summary
    total_hours = sum(te.get("hours", 0) for te in time_entries)
    total_billed = sum(b.get("amount", 0) or b.get("total", 0) for b in ap_bills)
    pending = sum(b.get("amount", 0) or b.get("total", 0) for b in ap_bills if b.get("status") in ("pending", "draft"))
    paid = sum(b.get("amount", 0) or b.get("total", 0) for b in ap_bills if b.get("status") == "paid")

    return {
        "time_entries": time_entries,
        "ap_bills": ap_bills,
        "summary": {
            "total_hours": round(total_hours, 1),
            "total_billed": total_billed,
            "pending_amount": pending,
            "paid_amount": paid,
            "outstanding": total_billed - paid
        }
    }


@router.get("/contractor-businesses/{business_id}/payment-summary")
async def get_payment_summary(
    business_id: str,
    current_user=Depends(get_current_user)
):
    """Get lifetime payment metrics for a contractor business."""
    if not has_perm(current_user, "admin.contractors.view") and not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    # Get users in this business
    business_users = await db.users.find(
        {"business_id": business_id},
        {"_id": 0, "user_id": 1}
    ).to_list(100)
    user_ids = [u["user_id"] for u in business_users]

    # Aggregate time entries
    te_filter = {"user_id": {"$in": user_ids}} if user_ids else {"_id": {"$exists": False}}
    time_entries = await db.time_entries.find(te_filter, {"_id": 0, "hours": 1, "bill_rate": 1, "date": 1}).to_list(5000)

    total_hours = sum(te.get("hours", 0) for te in time_entries)

    # Aggregate AP bills
    # TRANSITIONAL (B3): dual-read legacy ap_bills + new finance_drafts(kind=ap_bill).
    # Merge both so payment summaries include all pre- and post-migration AP data.
    legacy_bill_filter = {"$or": [
        {"contractor_business_id": business_id},
        {"vendor_id": business_id}
    ]}
    legacy_ap_bills = await db.ap_bills.find(
        legacy_bill_filter, {"_id": 0, "amount": 1, "total": 1, "status": 1, "payment_date": 1}
    ).to_list(5000)

    draft_bill_filter = {"$or": [
        {"source_contractor_id": business_id},
        {"vendor_id": business_id}
    ], "kind": "ap_bill"}
    new_ap_drafts = await db["finance_drafts"].find(
        draft_bill_filter, {"_id": 0, "amount": 1, "sync_status": 1, "created_at": 1}
    ).to_list(5000)
    # Normalize new drafts to legacy shape: sync_status="Synced" maps to paid
    for d in new_ap_drafts:
        d["status"] = "paid" if d.get("sync_status") == "Synced" else "pending"
        d.setdefault("payment_date", None)

    ap_bills = legacy_ap_bills + new_ap_drafts

    total_billed = sum(b.get("amount", 0) or b.get("total", 0) for b in ap_bills)
    total_paid = sum(b.get("amount", 0) or b.get("total", 0) for b in ap_bills if b.get("status") == "paid")

    # Find last payment date
    paid_bills = [b for b in ap_bills if b.get("payment_date")]
    last_payment = max((b["payment_date"] for b in paid_bills), default=None) if paid_bills else None

    avg_rate = (total_billed / total_hours) if total_hours > 0 else 0

    return {
        "total_hours": round(total_hours, 1),
        "total_billed": round(total_billed, 2),
        "total_paid": round(total_paid, 2),
        "outstanding": round(total_billed - total_paid, 2),
        "avg_hourly_rate": round(avg_rate, 2),
        "last_payment_date": last_payment
    }
