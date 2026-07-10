"""Hardware & Material Request Management — MEB4-T39.

First-class procurement requests separate from OpportunityLineItem
(see docs/procurement-handoff-analysis.md Q8 for the rationale).

Permission gating uses has_perm() — the canonical repo pattern.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import uuid

from database import get_db
from auth_helper import get_current_user, has_perm
from models import (
    HardwareMaterialRequest,
    HardwareMaterialRequestCreate,
    HardwareMaterialRequestUpdate,
    HARDWARE_MATERIAL_REQUEST_STATUSES,
    HARDWARE_MATERIAL_REQUEST_PRIORITIES,
    HARDWARE_MATERIAL_REQUEST_TERMINAL_STATUSES,
)
from services.field_registry import register_fields, register_module_events, register_module_config
from services.event_bus import emit_event

router = APIRouter()

register_module_config(
    "hardware_material_requests",
    "hardware_material_requests",
    "request_id",
    "Sales Ops",
)
register_module_events(
    "hardware_material_requests",
    "hardware_material_request",
    ["created", "updated", "status_changed", "deleted"],
)
register_fields("hardware_material_requests", "hardware_material_request", [
    {"name": "opportunity_id",   "type": "string",  "label": "Opportunity ID",      "description": "Opportunity this request belongs to"},
    {"name": "distributor_id",   "type": "string",  "label": "Distributor",         "description": "Distributor the item will be sourced from"},
    {"name": "product_id",       "type": "string",  "label": "Product",             "description": "Catalog product (optional)"},
    {"name": "item_description", "type": "string",  "label": "Item Description",    "description": "Free-text description when there is no catalog product"},
    {"name": "quantity",         "type": "number",  "label": "Quantity",            "description": "Number of units requested"},
    {"name": "needed_by_date",   "type": "date",    "label": "Needed By",           "description": "Date the item is needed (ISO)"},
    {"name": "priority",         "type": "string",  "label": "Priority",            "description": "Priority level (low, medium, high)"},
    {"name": "status",           "type": "string",  "label": "Status",              "description": "Lifecycle status (open, sourcing, ordered, received, cancelled)"},
    {"name": "assigned_to",      "type": "string",  "label": "Assigned To",         "description": "User (procurement owner) handling the request"},
    {"name": "requested_by",     "type": "string",  "label": "Requested By",        "description": "User who filed the request"},
    {"name": "notes",            "type": "string",  "label": "Notes",               "description": "Free-form notes from procurement"},
])


def _apply_soft_delete_filter(filt: dict) -> dict:
    """Hide soft-deleted requests. Matches the opportunities module pattern."""
    return {**filt, "deleted_at": None}


async def _denormalise_names(db, doc: dict) -> dict:
    """Attach distributor_name / product_name / opportunity_name / assigned_to_name."""
    if doc.get("distributor_id"):
        dist = await db.distributors.find_one({"distributor_id": doc["distributor_id"]}, {"name": 1, "_id": 0})
        doc["distributor_name"] = dist.get("name") if dist else None
    if doc.get("product_id"):
        prod = await db.products.find_one({"product_id": doc["product_id"]}, {"name": 1, "_id": 0})
        doc["product_name"] = prod.get("name") if prod else None
    if doc.get("opportunity_id"):
        opp = await db.opportunities.find_one({"opportunity_id": doc["opportunity_id"]}, {"name": 1, "_id": 0})
        doc["opportunity_name"] = opp.get("name") if opp else None
    if doc.get("assigned_to"):
        user = await db.users.find_one({"user_id": doc["assigned_to"]}, {"name": 1, "_id": 0})
        doc["assigned_to_name"] = user.get("name") if user else None
    return doc


async def _validate_refs(db, data) -> None:
    """Soft-reference checks — 400 for invalid FKs."""
    if data.opportunity_id:
        opp = await db.opportunities.find_one(
            {"opportunity_id": data.opportunity_id, "deleted_at": None}, {"_id": 1},
        )
        if not opp:
            raise HTTPException(status_code=404, detail="Opportunity not found")

    if getattr(data, "distributor_id", None):
        dist = await db.distributors.find_one(
            {"distributor_id": data.distributor_id, "status": "active", "deleted_at": None},
            {"_id": 1},
        )
        if not dist:
            raise HTTPException(status_code=404, detail="Distributor not found")

    if getattr(data, "product_id", None):
        prod = await db.products.find_one({"product_id": data.product_id}, {"_id": 1})
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")

    if getattr(data, "assigned_to", None):
        user = await db.users.find_one({"user_id": data.assigned_to}, {"_id": 1})
        if not user:
            raise HTTPException(status_code=404, detail="Assignee user not found")


@router.get("/hardware-material-requests")
async def list_requests(
    status: Optional[str] = None,
    opportunity_id: Optional[str] = None,
    distributor_id: Optional[str] = None,
    priority: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "hardware_material_requests.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if status is not None and status not in HARDWARE_MATERIAL_REQUEST_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(HARDWARE_MATERIAL_REQUEST_STATUSES)}",
        )
    if priority is not None and priority not in HARDWARE_MATERIAL_REQUEST_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid priority. Must be one of: {', '.join(HARDWARE_MATERIAL_REQUEST_PRIORITIES)}",
        )
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    db = get_db()
    filt = _apply_soft_delete_filter({})
    if status:
        filt["status"] = status
    if opportunity_id:
        filt["opportunity_id"] = opportunity_id
    if distributor_id:
        filt["distributor_id"] = distributor_id
    if priority:
        filt["priority"] = priority

    total = await db.hardware_material_requests.count_documents(filt)
    docs = await (
        db.hardware_material_requests.find(filt, {"_id": 0})
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
        .to_list(limit)
    )

    # Batch-resolve denormalized names to avoid N+1 on large result sets.
    distributor_ids = {d["distributor_id"] for d in docs if d.get("distributor_id")}
    product_ids = {d["product_id"] for d in docs if d.get("product_id")}
    opportunity_ids = {d["opportunity_id"] for d in docs if d.get("opportunity_id")}
    assignee_ids = {d["assigned_to"] for d in docs if d.get("assigned_to")}

    dist_map = {
        d["distributor_id"]: d.get("name")
        async for d in db.distributors.find(
            {
                "distributor_id": {"$in": list(distributor_ids)},
                "status": "active",
                "deleted_at": None,
            },
            {"distributor_id": 1, "name": 1, "_id": 0},
        )
    } if distributor_ids else {}
    prod_map = {
        d["product_id"]: d.get("name")
        async for d in db.products.find(
            {"product_id": {"$in": list(product_ids)}},
            {"product_id": 1, "name": 1, "_id": 0},
        )
    } if product_ids else {}
    opp_map = {
        d["opportunity_id"]: d.get("name")
        async for d in db.opportunities.find(
            {"opportunity_id": {"$in": list(opportunity_ids)}},
            {"opportunity_id": 1, "name": 1, "_id": 0},
        )
    } if opportunity_ids else {}
    user_map = {
        d["user_id"]: d.get("name")
        async for d in db.users.find(
            {"user_id": {"$in": list(assignee_ids)}},
            {"user_id": 1, "name": 1, "_id": 0},
        )
    } if assignee_ids else {}

    for doc in docs:
        if doc.get("distributor_id"):
            doc["distributor_name"] = dist_map.get(doc["distributor_id"])
        if doc.get("product_id"):
            doc["product_name"] = prod_map.get(doc["product_id"])
        if doc.get("opportunity_id"):
            doc["opportunity_name"] = opp_map.get(doc["opportunity_id"])
        if doc.get("assigned_to"):
            doc["assigned_to_name"] = user_map.get(doc["assigned_to"])

    return {"items": docs, "total": total, "limit": limit, "offset": offset}


@router.get("/hardware-material-requests/{request_id}")
async def get_request(
    request_id: str,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "hardware_material_requests.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    doc = await db.hardware_material_requests.find_one(
        _apply_soft_delete_filter({"request_id": request_id}), {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    await _denormalise_names(db, doc)
    return doc


@router.post("/hardware-material-requests")
async def create_request(
    data: HardwareMaterialRequestCreate,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "hardware_material_requests.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if not data.product_id and not (data.item_description and data.item_description.strip()):
        raise HTTPException(
            status_code=400,
            detail="Either product_id or item_description is required",
        )

    if data.priority not in HARDWARE_MATERIAL_REQUEST_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid priority. Must be one of: {', '.join(HARDWARE_MATERIAL_REQUEST_PRIORITIES)}",
        )

    if data.status not in HARDWARE_MATERIAL_REQUEST_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(HARDWARE_MATERIAL_REQUEST_STATUSES)}",
        )

    if data.quantity is not None and data.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be >= 1")

    db = get_db()
    await _validate_refs(db, data)

    request_id = f"hmr_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    doc = {
        "request_id": request_id,
        "opportunity_id": data.opportunity_id,
        "distributor_id": data.distributor_id,
        "product_id": data.product_id,
        "item_description": data.item_description,
        "quantity": data.quantity,
        "needed_by_date": data.needed_by_date,
        "priority": data.priority,
        "notes": data.notes,
        "status": data.status,
        "assigned_to": data.assigned_to,
        "requested_by": current_user.user_id,
        "requested_by_name": current_user.name,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "deleted_by": None,
    }

    await db.hardware_material_requests.insert_one(doc)
    doc.pop("_id", None)
    # Emit against the clean DB doc — downstream subscribers should not see
    # denormalized join fields (distributor_name etc.) in the event payload.
    event_record = dict(doc)
    await _denormalise_names(db, doc)

    await emit_event(
        "hardware_material_requests", "created",
        record=event_record,
        user_id=current_user.user_id,
        user_email=current_user.email,
    )

    return {"message": "Request created successfully", "request": doc}


@router.patch("/hardware-material-requests/{request_id}")
async def update_request(
    request_id: str,
    data: HardwareMaterialRequestUpdate,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "hardware_material_requests.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    doc = await db.hardware_material_requests.find_one(
        _apply_soft_delete_filter({"request_id": request_id}), {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    if data.status is not None:
        if data.status not in HARDWARE_MATERIAL_REQUEST_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {', '.join(HARDWARE_MATERIAL_REQUEST_STATUSES)}",
            )
        current_status = doc.get("status")
        if current_status in HARDWARE_MATERIAL_REQUEST_TERMINAL_STATUSES and data.status != current_status:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from terminal status '{current_status}' to '{data.status}'",
            )

    if data.priority is not None and data.priority not in HARDWARE_MATERIAL_REQUEST_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid priority. Must be one of: {', '.join(HARDWARE_MATERIAL_REQUEST_PRIORITIES)}",
        )

    if data.quantity is not None and data.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be >= 1")

    # Validate any new refs (Pydantic won't call _validate_refs for us here).
    if data.distributor_id:
        dist = await db.distributors.find_one(
            {"distributor_id": data.distributor_id, "status": "active", "deleted_at": None},
            {"_id": 1},
        )
        if not dist:
            raise HTTPException(status_code=404, detail="Distributor not found")
    if data.product_id:
        prod = await db.products.find_one({"product_id": data.product_id}, {"_id": 1})
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")
    if data.assigned_to:
        user = await db.users.find_one({"user_id": data.assigned_to}, {"_id": 1})
        if not user:
            raise HTTPException(status_code=404, detail="Assignee user not found")

    update_data = {"updated_at": datetime.now(timezone.utc)}
    for field, value in data.model_dump(exclude_unset=True).items():
        update_data[field] = value

    old_status = doc.get("status")
    new_status = update_data.get("status", old_status)

    await db.hardware_material_requests.update_one(
        {"request_id": request_id},
        {"$set": update_data},
    )

    updated = await db.hardware_material_requests.find_one({"request_id": request_id}, {"_id": 0})
    await _denormalise_names(db, updated)

    await emit_event(
        "hardware_material_requests", "updated",
        record=updated, old_record=doc,
        user_id=current_user.user_id,
        user_email=current_user.email,
    )

    if old_status != new_status:
        await emit_event(
            "hardware_material_requests", "status_changed",
            record=updated, old_record=doc,
            user_id=current_user.user_id,
            user_email=current_user.email,
        )

    return {"message": "Request updated successfully", "request": updated}


@router.delete("/hardware-material-requests/{request_id}")
async def delete_request(
    request_id: str,
    current_user=Depends(get_current_user),
):
    """Soft-delete via deleted_at / deleted_by (opportunity-style — requests have
    a real lifecycle and we want audit trail of who killed them, unlike distributors
    which flip status to inactive)."""
    if not has_perm(current_user, "hardware_material_requests.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    doc = await db.hardware_material_requests.find_one(
        _apply_soft_delete_filter({"request_id": request_id}), {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    now = datetime.now(timezone.utc)
    await db.hardware_material_requests.update_one(
        {"request_id": request_id},
        {"$set": {"deleted_at": now, "deleted_by": current_user.user_id, "updated_at": now}},
    )

    await emit_event(
        "hardware_material_requests", "deleted",
        record={**doc, "deleted_at": now, "deleted_by": current_user.user_id},
        old_record=doc,
        user_id=current_user.user_id,
        user_email=current_user.email,
    )

    return {"message": "Request deleted successfully"}
