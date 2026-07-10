"""Distributor Management — hardware/material suppliers that products can be sourced from.

Permission check uses has_perm() — the canonical pattern in this repo. routes/products.py
uses an inline `"*" in permissions` check instead, but that is the outlier; every other
module (contract_templates, contractor_businesses, sales_ops_procurement, etc.) uses
has_perm(). Don't "fix" this to match products.py.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import uuid

from pymongo.errors import DuplicateKeyError

from database import get_db
from auth_helper import get_current_user, has_perm
from models import Distributor, DistributorCreate, DistributorUpdate
from services.field_registry import register_fields, register_module_events, register_module_config
from services.event_bus import emit_event

router = APIRouter()

register_module_config("distributors", "distributors", "distributor_id", "Products & Services")
register_module_events("distributors", "distributor", ["created", "updated", "deleted"])
register_fields("distributors", "distributor", [
    {"name": "name",          "type": "string",  "label": "Name",          "description": "Distributor company name"},
    {"name": "contact_name",  "type": "string",  "label": "Contact Name",  "description": "Primary contact at the distributor"},
    {"name": "contact_email", "type": "string",  "label": "Contact Email", "description": "Primary contact email"},
    {"name": "contact_phone", "type": "string",  "label": "Contact Phone", "description": "Primary contact phone"},
    {"name": "website",       "type": "string",  "label": "Website",       "description": "Distributor website URL"},
    {"name": "notes",         "type": "string",  "label": "Notes",         "description": "Internal notes about the distributor relationship"},
    {"name": "status",        "type": "string",  "label": "Status",        "description": "Lifecycle status (active, inactive)"},
])


VALID_STATUSES = ("active", "inactive")


@router.get("/distributors")
async def list_distributors(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(get_current_user),
):
    """List distributors. Defaults to active only unless status=all or a specific status is passed."""
    if not has_perm(current_user, "distributors.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    db = get_db()

    if status is None:
        filt = {"status": "active"}
    elif status == "all":
        filt = {}
    elif status in VALID_STATUSES:
        filt = {"status": status}
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be 'all' or one of: {', '.join(VALID_STATUSES)}",
        )

    total = await db.distributors.count_documents(filt)
    distributors = await (
        db.distributors.find(filt, {"_id": 0})
        .sort("name", 1)
        .skip(offset)
        .limit(limit)
        .to_list(limit)
    )
    return {"items": distributors, "total": total, "limit": limit, "offset": offset}


@router.get("/distributors/{distributor_id}")
async def get_distributor(
    distributor_id: str,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "distributors.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    # Admins need to see inactive distributors (to reactivate them), so don't
    # filter on status here. The `deleted_at: None` clause is forward-compat
    # with a hard-delete flag; today no distributor has `deleted_at` set so it
    # acts as a pass-through but remains correct once one is introduced.
    distributor = await db.distributors.find_one(
        {"distributor_id": distributor_id, "deleted_at": None}, {"_id": 0},
    )
    if not distributor:
        raise HTTPException(status_code=404, detail="Distributor not found")

    return distributor


@router.get("/distributors/{distributor_id}/stats")
async def get_distributor_stats(
    distributor_id: str,
    current_user=Depends(get_current_user),
):
    """Return counts that the Procurement tab cards display.

    open_request_count is zero-filled here and populated by T39 once the
    hardware_material_requests collection exists. See docs/procurement-handoff-plan.md
    section 13 — T38 ships option (a): always-zero, extended in T39.
    """
    if not has_perm(current_user, "distributors.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    distributor = await db.distributors.find_one(
        {"distributor_id": distributor_id, "deleted_at": None}, {"_id": 0},
    )
    if not distributor:
        raise HTTPException(status_code=404, detail="Distributor not found")

    linked_product_count = await db.products.count_documents({
        "distributor_id": distributor_id,
        "is_active": True,
    })

    open_request_count = await db.hardware_material_requests.count_documents({
        "distributor_id": distributor_id,
        "status": {"$in": ["open", "sourcing"]},
        "deleted_at": None,
    })

    return {
        "distributor_id": distributor_id,
        "linked_product_count": linked_product_count,
        "open_request_count": open_request_count,
    }


@router.post("/distributors")
async def create_distributor(
    data: DistributorCreate,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "distributors.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if data.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")

    db = get_db()

    distributor_id = f"dist_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    distributor = {
        "distributor_id": distributor_id,
        "name": data.name,
        "contact_name": data.contact_name,
        # EmailStr is a str subclass; AnyUrl is not — coerce to str for BSON.
        "contact_email": data.contact_email,
        "contact_phone": data.contact_phone,
        "website": str(data.website) if data.website else None,
        "notes": data.notes,
        "status": data.status,
        "created_at": now,
        "updated_at": now,
    }

    try:
        await db.distributors.insert_one(distributor)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Distributor with this name already exists")
    distributor.pop("_id", None)

    await emit_event(
        "distributors", "created",
        record=distributor,
        user_id=current_user.user_id,
        user_email=current_user.email,
    )

    return {"message": "Distributor created successfully", "distributor": distributor}


@router.put("/distributors/{distributor_id}")
async def update_distributor(
    distributor_id: str,
    data: DistributorUpdate,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "distributors.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    distributor = await db.distributors.find_one({"distributor_id": distributor_id}, {"_id": 0})
    if not distributor:
        raise HTTPException(status_code=404, detail="Distributor not found")

    if data.status is not None and data.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")

    update_data = {"updated_at": datetime.now(timezone.utc)}
    for field, value in data.model_dump(exclude_unset=True).items():
        # AnyUrl (website) serializes to a Url object under default mode —
        # coerce to str so BSON can encode it.
        if field == "website" and value is not None:
            value = str(value)
        update_data[field] = value

    try:
        await db.distributors.update_one({"distributor_id": distributor_id}, {"$set": update_data})
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Distributor with this name already exists")

    updated = await db.distributors.find_one({"distributor_id": distributor_id}, {"_id": 0})

    await emit_event(
        "distributors", "updated",
        record=updated, old_record=distributor,
        user_id=current_user.user_id,
        user_email=current_user.email,
    )

    return {"message": "Distributor updated successfully", "distributor": updated}


@router.delete("/distributors/{distributor_id}")
async def delete_distributor(
    distributor_id: str,
    current_user=Depends(get_current_user),
):
    """Soft delete by flipping status to inactive. Matches the Products module's
    is_active soft-delete pattern rather than the opportunities deleted_at pattern —
    Distributor has no lifecycle beyond active/inactive."""
    if not has_perm(current_user, "distributors.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    distributor = await db.distributors.find_one({"distributor_id": distributor_id}, {"_id": 0})
    if not distributor:
        raise HTTPException(status_code=404, detail="Distributor not found")

    await db.distributors.update_one(
        {"distributor_id": distributor_id},
        {"$set": {"status": "inactive", "updated_at": datetime.now(timezone.utc)}},
    )

    updated = await db.distributors.find_one({"distributor_id": distributor_id}, {"_id": 0})

    await emit_event(
        "distributors", "deleted",
        record=updated, old_record=distributor,
        user_id=current_user.user_id,
        user_email=current_user.email,
    )

    return {"message": "Distributor deleted successfully"}
