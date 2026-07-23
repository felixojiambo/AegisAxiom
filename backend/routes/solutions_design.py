"""
Solutions Design Routes
Manages SOW (Statement of Work) creation for opportunities in Sales Ops.
LOB-Based Architecture: Each opportunity LOB gets its own SOW task.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from db.session import get_session
from repositories.users import UserRepository
from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from models import (
    OpportunitySOW,
    SOWCreate,
    SOWMilestone,
    SOWSectionRegenerateRequest,
    gen_id,
)
from services.automation_engine import AutomationEngine, check_expected_completion_auto_progress  # DEPRECATED: kept for workflow rules until Branch 03
from services.policy_engine import evaluate_policies
from services.policy_helpers import enforce_policy_result
from services.field_registry import register_fields, register_module_events, register_module_config
from services.field_options import SOW_STATUSES, APPROVAL_ACTIONS
from services.audit_service import log_action as audit_log_action


# Field Services SOW readiness contract. The roles listed here must
# be present on the SOW's contact_roles list before the SOW can
# transition to ready_for_review. Each required role must have at
# least a name plus one contact method (email or phone). The exact
# set is the smallest viable Field Services dispatch contract:
# someone to talk to about the work (primary), someone to bill
# (billing), and someone at the site (service_location). Additional
# roles can be added without changing this constant if the deploy
# only needs three to be required.
FS_REQUIRED_CONTACT_ROLES = ("primary", "billing", "service_location")


# Procurement / PMO own these fields on every SOWProductItem. The
# SOW create-or-update path is mutated by Sales Ops and the SOW
# builder; it must never overwrite procurement state that the
# single-item routes (in sales_ops_procurement.py) have set. The
# merge function below preserves these fields from the persisted
# row regardless of what comes in on the wire.
SOW_ITEM_PROCUREMENT_FIELDS = (
    "procurement_status",
    "supplier_source_url",
    "availability_note",
    "procurement_notes",
    "expected_delivery_date",
    "order_reference",
    "tracking_number",
    "tracking_url",
    "ordered_at",
    "ordered_by",
    "delivered_at",
    "delivered_by",
    "delivery_notes",
)


def _preserve_procurement_lifecycle(
    incoming_items: Optional[list],
    existing_items: Optional[list],
) -> list:
    """Merge incoming SOW items against the persisted array so that
    procurement-lifecycle fields are owned by the single-item routes.

    For each incoming item with an ``item_id`` that matches a persisted
    row, copy the lifecycle fields from the persisted row onto the
    incoming dict (overwriting any value the caller supplied). For new
    rows (no match), initialize ``procurement_status`` to
    ``"pending_pricing"`` when ``source`` is ``"needs_sourcing"`` so
    the Procurement worklist picks the row up immediately.
    """
    if not isinstance(incoming_items, list):
        return incoming_items if incoming_items is not None else []
    existing_by_id = {}
    if isinstance(existing_items, list):
        for row in existing_items:
            if isinstance(row, dict) and row.get("item_id"):
                existing_by_id[row["item_id"]] = row
    merged: list = []
    for item in incoming_items:
        if not isinstance(item, dict):
            merged.append(item)
            continue
        match = existing_by_id.get(item.get("item_id"))
        if match:
            for f in SOW_ITEM_PROCUREMENT_FIELDS:
                if f in match:
                    item[f] = match[f]
        else:
            # New row: the SOW create-or-update path must not let a
            # caller pre-stamp procurement state. Strip every
            # lifecycle field so the row enters the world only via
            # the single-item Procurement / PMO routes.
            for f in SOW_ITEM_PROCUREMENT_FIELDS:
                item.pop(f, None)
            if item.get("source") == "needs_sourcing":
                item["procurement_status"] = "pending_pricing"
        merged.append(item)
    return merged


def _has_required_location(loc: Optional[dict]) -> bool:
    """Return True when the SOW carries enough location data to
    dispatch a Field Services job. The smallest viable shape is a
    non-empty street address plus a city; everything else is
    optional. Returning False trips the readiness gate so the SOW
    cannot advance to ready_for_review until the user fills the
    field.
    """
    if not isinstance(loc, dict):
        return False
    address = (loc.get("address") or "").strip()
    city = (loc.get("city") or "").strip()
    return bool(address and city)


def _has_required_contact_roles(roles: Optional[list]) -> tuple[bool, list[str]]:
    """Return (ready, missing_roles). Ready when every entry in
    FS_REQUIRED_CONTACT_ROLES is present in the roles list with a
    name and at least an email or phone. The roles list is
    free-string so a deploy can add custom roles; the required set
    is the canonical subset that gates dispatch readiness.
    """
    by_role: dict[str, dict] = {}
    for r in roles or []:
        role_name = (r or {}).get("role")
        if isinstance(role_name, str) and role_name:
            by_role[role_name] = r or {}

    missing: list[str] = []
    for required in FS_REQUIRED_CONTACT_ROLES:
        entry = by_role.get(required)
        if not entry:
            missing.append(required)
            continue
        name = (entry.get("name") or "").strip()
        email = (entry.get("email") or "").strip()
        phone = (entry.get("phone") or "").strip()
        if not name or not (email or phone):
            missing.append(required)
    return (len(missing) == 0, missing)


def _evaluate_field_services_readiness(sow: dict) -> dict:
    """Build the readiness envelope used by the transition gate and
    the check-stage-blocks preview. Same shape both places so the
    frontend can render a single warning component.

    Shape:
      {
        "ready": bool,
        "missing": [{"field": str, "reason": str}, ...]
      }

    Non-FS SOWs always return ready=True with an empty missing list
    so the same envelope is safe to pass through every LOB path.
    """
    if sow.get("line_of_business") != "field_services":
        return {"ready": True, "missing": []}

    missing: list[dict] = []
    if not _has_required_location(sow.get("service_location")):
        missing.append(
            {
                "field": "service_location",
                "reason": "Service location requires an address and city before validation.",
            }
        )
    contacts_ok, missing_roles = _has_required_contact_roles(sow.get("contact_roles"))
    if not contacts_ok:
        missing.append(
            {
                "field": "contact_roles",
                "reason": (
                    "Required contact roles missing or incomplete: "
                    + ", ".join(missing_roles)
                ),
            }
        )
    return {"ready": not missing, "missing": missing}
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import os
import uuid as _uuid

router = APIRouter()
logger = logging.getLogger(__name__)

register_module_config("sales_ops", "opportunity_sows", "sow_id", "Sales Ops")
register_module_events("sales_ops", "sow", ["created", "updated", "deleted", "status_changed"])

# Register fields available for policy evaluation
register_fields("sales_ops", "sow", [
    {"name": "status", "type": "string", "label": "SOW Status", "options": SOW_STATUSES, "description": "SOW lifecycle status in the sales pipeline"},
    {"name": "margin_percentage", "type": "number", "label": "Margin %", "description": "Profit margin percentage for this SOW based on sell vs cost rates"},
    {"name": "resource_count", "type": "number", "label": "Resource Count", "description": "Number of resource assignments on this SOW"},
    # Computed fields
    {"name": "target_status", "type": "string", "label": "Target Status", "computed": True, "options": SOW_STATUSES, "description": "The status the user is trying to transition the SOW to"},
    {"name": "has_resources", "type": "boolean", "label": "Has Resources", "computed": True, "description": "Whether the SOW has at least one resource assignment"},
    {"name": "has_missing_contractors", "type": "boolean", "label": "Has Missing Contractors", "computed": True, "description": "Whether any resource assignments are missing contractor assignments"},
])

register_fields("sales_ops", "service_approval", [
    {"name": "sell_price", "type": "number", "label": "Sell Price", "description": "Proposed sell price for the service requiring approval"},
    {"name": "status", "type": "string", "label": "Approval Status", "options": APPROVAL_ACTIONS, "description": "Approval workflow status (pending, approved, rejected)"},
])


async def _check_sow_blockers_internal(db, opportunity_id: str, sow_id: str) -> dict:
    """
    Internal helper: Check blockers for a SOW without authentication.
    Returns blocker status for enrichment purposes.
    """
    # Fetch SOW
    sow = await db.opportunity_sows.find_one(
        {"sow_id": sow_id, "opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not sow:
        return {"has_blockers": False, "blockers": []}
    
    # Fetch all resource assignments for this SOW
    selected_assignment_ids = sow.get("selected_resource_assignment_ids", [])
    
    if not selected_assignment_ids or len(selected_assignment_ids) == 0:
        return {"has_blockers": False, "blockers": []}
    
    # Fetch resource assignments
    assignments = await db.opportunity_resource_assignments.find(
        {"assignment_id": {"$in": selected_assignment_ids}},
        {"_id": 0}
    ).to_list(100)
    
    blockers = []
    has_critical_blockers = False
    
    for assignment in assignments:
        # Check for missing contractor
        if assignment.get("status") == "blocked_missing_contractor":
            has_critical_blockers = True
            blockers.append({
                "type": "critical",
                "category": "missing_contractor",
                "resource_type": assignment.get("resource_type_name"),
                "message": f"Missing contractor for {assignment.get('resource_type_name')}"
            })
        
        # Check for negative margins
        profit_margin = assignment.get("profit_margin", 0)
        if profit_margin < 0:
            has_critical_blockers = True
            blockers.append({
                "type": "critical",
                "category": "negative_margin",
                "resource_type": assignment.get("resource_type_name"),
                "message": f"Negative margin ({profit_margin:.1f}%) for {assignment.get('resource_type_name')}"
            })
        
        # Check for low margins (warning, not blocker)
        elif profit_margin < 20:
            blockers.append({
                "type": "warning",
                "category": "low_margin",
                "resource_type": assignment.get("resource_type_name"),
                "message": f"Low margin ({profit_margin:.1f}%) for {assignment.get('resource_type_name')}"
            })
    
    return {
        "has_blockers": has_critical_blockers,
        "blockers": blockers
    }


@router.get("/opportunities/{opportunity_id}/sow-tasks")
async def get_opportunity_sow_tasks(
    opportunity_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Get all SOW tasks for an opportunity (1 per LOB).
    Auto-creates SOW task stubs for each LOB if they don't exist.
    Excludes referral_mrr (routes to Procurement instead).
    Enriches each SOW with real-time blocker status for Kanban display.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify opportunity exists
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    # Get LOBs from opportunity
    lobs = opportunity.get("lines_of_business", [])
    if not lobs:
        return {
            "opportunity": opportunity,
            "sow_tasks": [],
            "referral_mrr_exists": False,
            "needs_procurement_routing": False
        }
    
    # Check if only referral_mrr exists
    non_referral_lobs = [lob for lob in lobs if lob != "referral_mrr"]
    has_referral = "referral_mrr" in lobs
    only_referral = has_referral and len(non_referral_lobs) == 0
    
    # Fetch existing SOWs
    existing_sows = await db.opportunity_sows.find(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    ).to_list(100)
    
    # Build dict of existing SOWs by LOB (handle old schema without line_of_business)
    existing_lobs = {}
    for sow in existing_sows:
        sow_lob = sow.get("line_of_business")
        if sow_lob:
            existing_lobs[sow_lob] = sow
    
    # Create stub SOWs for missing LOBs (exclude referral_mrr)
    sow_tasks = []
    for lob in non_referral_lobs:
        if lob in existing_lobs:
            sow_tasks.append(existing_lobs[lob])
        else:
            # Create stub SOW
            sow_id = f"sow_{gen_id()}"
            stub_sow = {
                "sow_id": sow_id,
                "opportunity_id": opportunity_id,
                "line_of_business": lob,
                "assigned_to": None,
                "assigned_to_name": None,
                "smart_specific": None,
                "smart_measurable": None,
                "smart_achievable": None,
                "smart_relevant": None,
                "smart_time_bound": None,
                "contract_type": None,
                "billing_terms": None,
                "payment_terms": None,
                "start_date": None,
                "end_date": None,
                "total_amount": None,
                "monthly_rate": None,
                "milestones": [],
                "billing_account_email": None,
                "billing_account_phone": None,
                "special_billing_instructions": None,
                "boa_authorized_users": [],
                "selected_resource_assignment_ids": [],
                "selected_line_item_ids": [],
                "status": "not_started",
                "version": 1,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "created_by": current_user.user_id,
                "last_edited_by": current_user.user_id
            }
            
            await db.opportunity_sows.insert_one(stub_sow)
            stub_sow.pop("_id", None)
            sow_tasks.append(stub_sow)
    
    # Enrich each SOW with blocker data for Kanban display
    for sow in sow_tasks:
        blocker_data = await _check_sow_blockers_internal(db, opportunity_id, sow["sow_id"])
        sow["has_critical_blockers"] = blocker_data.get("has_blockers", False)
        sow["blockers"] = [b["message"] for b in blocker_data.get("blockers", [])]
    
    return {
        "opportunity": opportunity,
        "sow_tasks": sow_tasks,
        "referral_mrr_exists": has_referral,
        "needs_procurement_routing": only_referral or has_referral
    }


@router.get("/opportunities/{opportunity_id}/sow")
async def get_opportunity_sow_by_lob(
    opportunity_id: str,
    lob: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get the SOW for a specific LOB within an opportunity."""
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify opportunity exists
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    # Check if LOB exists in opportunity
    if lob not in opportunity.get("lines_of_business", []):
        raise HTTPException(status_code=400, detail=f"LOB '{lob}' not found in opportunity")
    
    # Fetch SOW for this LOB
    sow = await db.opportunity_sows.find_one(
        {"opportunity_id": opportunity_id, "line_of_business": lob},
        {"_id": 0}
    )
    
    if not sow:
        # Create stub SOW
        sow_id = f"sow_{gen_id()}"
        sow = {
            "sow_id": sow_id,
            "opportunity_id": opportunity_id,
            "line_of_business": lob,
            "assigned_to": None,
            "assigned_to_name": None,
            "smart_specific": None,
            "smart_measurable": None,
            "smart_achievable": None,
            "smart_relevant": None,
            "smart_time_bound": None,
            "contract_type": None,
            "billing_terms": None,
            "payment_terms": None,
            "start_date": None,
            "end_date": None,
            "total_amount": None,
            "monthly_rate": None,
            "milestones": [],
            "billing_account_email": None,
            "billing_account_phone": None,
            "special_billing_instructions": None,
            "boa_authorized_users": [],
            "selected_resource_assignment_ids": [],
            "selected_line_item_ids": [],
            "status": "not_started",
            "version": 1
        }
    
    return sow


@router.get("/opportunities/{opportunity_id}/sows/all")
async def get_all_sows_for_opportunity(
    opportunity_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Get all SOWs for an opportunity (all LOBs).
    Returns a list of SOWs with their current status, owner, and basic info.
    Used by OpportunityDetailModal to display Sales Operations Status section.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify opportunity exists
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    # Get all SOWs for this opportunity
    sows = await db.opportunity_sows.find(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    ).to_list(100)
    
    # Return the list of SOWs
    return {
        "opportunity_id": opportunity_id,
        "sows": sows
    }


@router.post("/opportunities/{opportunity_id}/sow")
async def create_or_update_sow(
    opportunity_id: str,
    lob: str,
    data: SOWCreate,
    db=Depends(get_db),
    pg: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user)
):
    """Create or update SOW for a specific LOB."""
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify opportunity exists
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    # Check if LOB exists in opportunity
    if lob not in opportunity.get("lines_of_business", []):
        raise HTTPException(status_code=400, detail=f"LOB '{lob}' not found in opportunity")
    
    # Check if SOW already exists for this LOB
    existing_sow = await db.opportunity_sows.find_one(
        {"opportunity_id": opportunity_id, "line_of_business": lob},
        {"_id": 0}
    )
    
    if existing_sow:
        # Update existing SOW
        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(timezone.utc)
        update_data["last_edited_by"] = current_user.user_id
        update_data["version"] = existing_sow.get("version", 1) + 1

        if "sow_product_items" in update_data:
            update_data["sow_product_items"] = _preserve_procurement_lifecycle(
                update_data["sow_product_items"],
                existing_sow.get("sow_product_items"),
            )

        # Update assigned_to_name if assigned_to changed
        if "assigned_to" in update_data and update_data["assigned_to"]:
            assigned_user = await UserRepository(pg).get_user_by_id(update_data["assigned_to"])
            if assigned_user:
                update_data["assigned_to_name"] = assigned_user.name
        
        await db.opportunity_sows.update_one(
            {"opportunity_id": opportunity_id, "line_of_business": lob},
            {"$set": update_data}
        )
        
        updated_sow = await db.opportunity_sows.find_one(
            {"opportunity_id": opportunity_id, "line_of_business": lob},
            {"_id": 0}
        )
        
        logger.info(f"SOW updated for opportunity {opportunity_id} LOB {lob} by {current_user.email}")
        return updated_sow
    
    else:
        # Create new SOW
        sow_id = f"sow_{gen_id()}"

        # Get assigned_to_name if assigned_to provided
        assigned_to_name = None
        if data.assigned_to:
            assigned_user = await UserRepository(pg).get_user_by_id(data.assigned_to)
            if assigned_user:
                assigned_to_name = assigned_user.name

        dumped = data.model_dump()
        dumped["sow_product_items"] = _preserve_procurement_lifecycle(
            dumped.get("sow_product_items"),
            None,
        )

        sow_doc = {
            "sow_id": sow_id,
            "opportunity_id": opportunity_id,
            "line_of_business": lob,
            "assigned_to_name": assigned_to_name,
            **dumped,
            "status": data.status or "not_started",
            "version": 1,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "created_by": current_user.user_id,
            "last_edited_by": current_user.user_id
        }
        
        await db.opportunity_sows.insert_one(sow_doc)
        
        logger.info(f"SOW created for opportunity {opportunity_id} LOB {lob} by {current_user.email}")
        
        # Return without _id
        return await db.opportunity_sows.find_one(
            {"opportunity_id": opportunity_id, "line_of_business": lob},
            {"_id": 0}
        )


@router.post("/opportunities/{opportunity_id}/sow-tasks/{lob}/assign")
async def assign_sow_task(
    opportunity_id: str,
    lob: str,
    assigned_to: str,
    db=Depends(get_db),
    pg: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user)
):
    """Assign a SOW task to a sales_ops user."""
    if not has_perm(current_user, "sales_ops.solutions_design.assign"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify user exists and is sales_ops
    user = await UserRepository(pg).get_user_by_id(assigned_to)
    if not user or user.role != "sales_ops":
        raise HTTPException(status_code=404, detail="Sales ops user not found")

    # Update SOW assignment
    result = await db.opportunity_sows.update_one(
        {"opportunity_id": opportunity_id, "line_of_business": lob},
        {"$set": {
            "assigned_to": assigned_to,
            "assigned_to_name": user.name,
            "updated_at": datetime.now(timezone.utc)
        }}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="SOW task not found")

    logger.info(f"SOW {opportunity_id}/{lob} assigned to {assigned_to} by {current_user.email}")

    return {"message": "SOW task assigned successfully", "assigned_to_name": user.name}


@router.get("/available-resources")
async def get_available_resources(
    resource_type_id: Optional[str] = None,
    db=Depends(get_db),
    pg: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user)
):
    """
    Get available contractor resources filtered by resource type.
    Used in Solutions Design to assign resources from approved pool.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    resources = await UserRepository(pg).list_resource_pool(resource_type_id=resource_type_id)

    # Enrich with contractor business names
    for res in resources:
        if res.get("contractor_business_id"):
            business = await db.contractor_businesses.find_one(
                {"business_id": res["contractor_business_id"]},
                {"_id": 0, "business_name": 1}
            )
            res["contractor_business_name"] = business.get("business_name") if business else None
    
    return {"items": resources}


@router.get("/materials-catalog")
async def get_materials_catalog(
    category: Optional[str] = None,
    search: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Get approved materials/hardware catalog.
    For now, returns distinct items from opportunity_line_items as a catalog.
    Future: Dedicated materials_catalog collection.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Build aggregation pipeline to get unique materials
    match_stage = {}
    if category:
        match_stage["category"] = category
    
    pipeline = [
        {"$match": match_stage} if match_stage else {"$match": {}},
        {"$group": {
            "_id": {
                "item_name": "$item_name",
                "category": "$category",
                "supplier_name": "$supplier_name"
            },
            "avg_cost_price": {"$avg": "$cost_price"},
            "avg_sell_price": {"$avg": "$sell_price"},
            "typical_unit": {"$first": "$unit"},
            "supplier_sku": {"$first": "$supplier_sku"}
        }},
        {"$project": {
            "_id": 0,
            "item_name": "$_id.item_name",
            "category": "$_id.category",
            "supplier_name": "$_id.supplier_name",
            "avg_cost_price": 1,
            "avg_sell_price": 1,
            "typical_unit": 1,
            "supplier_sku": 1
        }},
        {"$sort": {"item_name": 1}},
        {"$limit": 500}
    ]
    
    catalog = await db.opportunity_line_items.aggregate(pipeline).to_list(500)
    
    # Apply search filter if provided
    if search:
        search_lower = search.lower()
        catalog = [
            item for item in catalog
            if search_lower in item.get("item_name", "").lower() or
               search_lower in (item.get("supplier_name") or "").lower()
        ]
    
    return {"items": catalog}


@router.get("/sales-ops-users")
async def get_sales_ops_users(
    pg: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user)
):
    """Get list of sales_ops users for SOW assignment."""
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    user_records = await UserRepository(pg).list_users(role="sales_ops", is_active=True, limit=100)
    users = [
        {"user_id": u.user_id, "name": u.name, "email": u.email}
        for u in user_records
    ]

    return {"items": users}


@router.put("/opportunities/{opportunity_id}/sows/{sow_id}")
async def update_sow_status(
    opportunity_id: str,
    sow_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Update SOW status and metadata.
    Auto-checks for blockers if status is being updated.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Fetch SOW
    sow = await db.opportunity_sows.find_one(
        {"sow_id": sow_id, "opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not sow:
        raise HTTPException(status_code=404, detail="SOW not found")

    # Policy Engine: evaluate margin policies BEFORE the DB write
    selected_ids = sow.get("selected_resource_assignment_ids", [])
    margin_payload = {
        "margin_percentage": sow.get("margin_percentage", 0),
        "has_resources": len(selected_ids) > 0,
        "resource_count": len(selected_ids),
        "status": data.get("status", sow.get("status")),
    }
    margin_result = await evaluate_policies(db, "sales_ops", "sow", "on_update", margin_payload, user_role=current_user.role)
    warnings = enforce_policy_result(margin_result)

    update_fields = {
        "updated_at": datetime.now(timezone.utc),
        "last_edited_by": current_user.user_id
    }

    # Allow updates to status, blockers, etc.
    if "status" in data:
        update_fields["status"] = data["status"]
    if "has_critical_blockers" in data:
        update_fields["has_critical_blockers"] = data["has_critical_blockers"]
    if "blockers" in data:
        update_fields["blockers"] = data["blockers"]
    if "expected_completion_date" in data:
        update_fields["expected_completion_date"] = data["expected_completion_date"]

    await db.opportunity_sows.update_one(
        {"sow_id": sow_id},
        {"$set": update_fields}
    )

    logger.info(f"SOW {sow_id} updated by {current_user.email}")

    # DEPRECATED WORKFLOW: Check if expected completion date was just filled → auto-progress
    # This is a workflow-type rule that will move to Branch 03 (Workflow Rules Engine)
    if "expected_completion_date" in data and data["expected_completion_date"]:
        await check_expected_completion_auto_progress(db, opportunity_id, sow_id)

    # DEPRECATED WORKFLOW: Evaluate old automation rules for banner/alert generation
    # These workflow-type rules (negative_margin_alert, auto-progress) remain until Branch 03
    engine = AutomationEngine(db)
    await engine.evaluate_opportunity(opportunity_id)

    # Return updated SOW with any policy warnings
    updated_sow = await db.opportunity_sows.find_one(
        {"sow_id": sow_id},
        {"_id": 0}
    )

    response = {**updated_sow}
    if warnings:
        response["_policy_warnings"] = warnings
    return response


@router.get("/opportunities/{opportunity_id}/sows/{sow_id}/blockers")
async def check_sow_blockers(
    opportunity_id: str,
    sow_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Check for blockers in a SOW.
    Returns blocker status and list of blocking issues.

    Blockers include:
    - Missing contractors (status = blocked_missing_contractor)
    - Negative profit margins
    - Pending procurement approvals
    """
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Fetch SOW
    sow = await db.opportunity_sows.find_one(
        {"sow_id": sow_id, "opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not sow:
        raise HTTPException(status_code=404, detail="SOW not found")

    # Fetch all resource assignments for this SOW
    selected_assignment_ids = sow.get("selected_resource_assignment_ids", [])
    
    blockers = []
    has_critical_blockers = False
    
    if not selected_assignment_ids or len(selected_assignment_ids) == 0:
        # No resources assigned yet
        return {
            "sow_id": sow_id,
            "status": "not_started",
            "has_blockers": False,
            "blockers": [],
            "blocker_count": 0,
            "warning_count": 0
        }
    
    # Fetch resource assignments
    assignments = await db.opportunity_resource_assignments.find(
        {"assignment_id": {"$in": selected_assignment_ids}},
        {"_id": 0}
    ).to_list(100)
    
    for assignment in assignments:
        # Check for missing contractor
        if assignment.get("status") == "blocked_missing_contractor":
            has_critical_blockers = True
            blockers.append({
                "type": "critical",
                "category": "missing_contractor",
                "resource_type": assignment.get("resource_type_name"),
                "message": f"Missing contractor for {assignment.get('resource_type_name')}"
            })
        
        # Check for negative margins
        profit_margin = assignment.get("profit_margin", 0)
        if profit_margin < 0:
            has_critical_blockers = True
            blockers.append({
                "type": "critical",
                "category": "negative_margin",
                "resource_type": assignment.get("resource_type_name"),
                "message": f"Negative margin ({profit_margin:.1f}%) for {assignment.get('resource_type_name')}"
            })
        
        # Check for low margins (warning, not blocker)
        elif profit_margin < 20:
            blockers.append({
                "type": "warning",
                "category": "low_margin",
                "resource_type": assignment.get("resource_type_name"),
                "message": f"Low margin ({profit_margin:.1f}%) for {assignment.get('resource_type_name')}"
            })
    
    # Determine auto status
    auto_status = sow.get("status", "not_started")
    if has_critical_blockers:
        auto_status = "blocked"
    elif len(assignments) > 0 and not has_critical_blockers:
        if sow.get("status") == "not_started":
            auto_status = "in_progress"
    
    # Count blocker types
    critical_count = len([b for b in blockers if b["type"] == "critical"])
    warning_count = len([b for b in blockers if b["type"] == "warning"])
    
    return {
        "sow_id": sow_id,
        "status": auto_status,
        "has_blockers": has_critical_blockers,
        "blockers": blockers,
        "blocker_count": critical_count,
        "warning_count": warning_count,
        "total_resources": len(assignments)
    }


@router.patch("/opportunities/{opportunity_id}/sows/{sow_id}/assign")
async def assign_sow(
    opportunity_id: str,
    sow_id: str,
    assigned_to: str,
    db=Depends(get_db),
    pg: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user)
):
    """
    Assign SOW to a Sales Ops user.
    Query param: assigned_to (user_id)
    """
    if not has_perm(current_user, "sales_ops.solutions_design.assign"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Fetch the user to get their name
    user = await UserRepository(pg).get_user_by_id(assigned_to)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Update SOW
    result = await db.opportunity_sows.update_one(
        {"sow_id": sow_id, "opportunity_id": opportunity_id},
        {"$set": {
            "assigned_to": assigned_to,
            "assigned_to_name": user.name,
            "updated_at": datetime.now(timezone.utc),
            "last_edited_by": current_user.user_id
        }}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="SOW not found")

    logger.info(f"SOW {sow_id} assigned to {user.name} by {current_user.email}")

    return {"message": "SOW assigned successfully", "assigned_to": assigned_to, "assigned_to_name": user.name}


@router.patch("/opportunities/{opportunity_id}/sows/{sow_id}/status")
async def change_sow_status(
    opportunity_id: str,
    sow_id: str,
    status: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Change SOW status (for Kanban drag-and-drop).
    Validates status transitions using automation rules (no hardcoded logic).
    """
    if not has_perm(current_user, "sales_ops.solutions_design.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Fetch SOW
    sow = await db.opportunity_sows.find_one(
        {"sow_id": sow_id, "opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not sow:
        raise HTTPException(status_code=404, detail="SOW not found")

    # Field Services readiness gate. Before a Field Services SOW
    # can advance to ready_for_review the dispatch contract requires
    # a service location plus the canonical required contact roles.
    # See docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md and
    # FS_REQUIRED_CONTACT_ROLES above. The check is FS-only by design
    # so PS / wholesale_mrr transitions are not affected.
    if status == "ready_for_review":
        readiness = _evaluate_field_services_readiness(sow)
        if not readiness["ready"]:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "field_services_sow_incomplete",
                    "message": (
                        "Field Services SOW is missing required dispatch "
                        "information. Add the service location and the "
                        "required contact roles before requesting validation."
                    ),
                    "missing": readiness["missing"],
                },
            )

    # Build policy evaluation payload
    selected_ids = sow.get("selected_resource_assignment_ids", [])
    assignments = await db.opportunity_resource_assignments.find(
        {"assignment_id": {"$in": selected_ids}},
        {"_id": 0}
    ).to_list(100) if selected_ids else []

    policy_payload = {
        "status": sow.get("status"),
        "target_status": status,
        "has_missing_contractors": any(
            a.get("status") == "blocked_missing_contractor" for a in assignments
        ),
        "has_resources": len(selected_ids) > 0,
        "resource_count": len(selected_ids),
        "margin_percentage": sow.get("margin_percentage", 0),
    }

    # Policy engine replaces both the valid_statuses check and AutomationEngine.check_stage_blocks
    result = await evaluate_policies(db, "sales_ops", "sow", "on_status_change", policy_payload, user_role=current_user.role)
    enforce_policy_result(result)

    previous_status = sow.get("status")

    # Update status
    result = await db.opportunity_sows.update_one(
        {"sow_id": sow_id},
        {"$set": {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
            "last_edited_by": current_user.user_id
        }}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="SOW not found or status unchanged")

    logger.info(f"SOW {sow_id} status changed to {status} by {current_user.email}")

    # Audit row. Status transitions on a SOW are sensitive enough to
    # log so the activity feed and any future approval audit can
    # trace which role moved a SOW through validation. log_action
    # carries its own try/except so audit-side outages do not break
    # the user's transition.
    await audit_log_action(
        action="sales_ops.sow.status_changed",
        actor_id=getattr(current_user, "user_id", None),
        actor_email=getattr(current_user, "email", "unknown"),
        resource_type="opportunity_sow",
        resource_id=sow_id,
        details={
            "opportunity_id": opportunity_id,
            "line_of_business": sow.get("line_of_business"),
            "previous_status": previous_status,
            "new_status": status,
        },
    )

    # DEPRECATED WORKFLOW: Re-evaluate old automation rules for banner/alert generation
    engine = AutomationEngine(db)
    await engine.evaluate_opportunity(opportunity_id)

    return {"message": "Status updated successfully", "new_status": status}


@router.get("/opportunities/{opportunity_id}/sows/{sow_id}/check-stage-blocks/{target_stage}")
async def check_sow_stage_blocks(
    opportunity_id: str,
    sow_id: str,
    target_stage: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Check if moving to target_stage is blocked by policies.
    Returns blocking messages for frontend display.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Build the same payload as change_sow_status
    sow = await db.opportunity_sows.find_one(
        {"sow_id": sow_id, "opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not sow:
        raise HTTPException(status_code=404, detail="SOW not found")

    selected_ids = sow.get("selected_resource_assignment_ids", [])
    assignments = await db.opportunity_resource_assignments.find(
        {"assignment_id": {"$in": selected_ids}},
        {"_id": 0}
    ).to_list(100) if selected_ids else []

    policy_payload = {
        "status": sow.get("status"),
        "target_status": target_stage,
        "has_missing_contractors": any(
            a.get("status") == "blocked_missing_contractor" for a in assignments
        ),
        "has_resources": len(selected_ids) > 0,
        "resource_count": len(selected_ids),
        "margin_percentage": sow.get("margin_percentage", 0),
    }

    result = await evaluate_policies(db, "sales_ops", "sow", "on_status_change", policy_payload, user_role=current_user.role)

    # Surface the Field Services readiness preview alongside the
    # policy engine result so the frontend can render a single
    # warning component covering both. ready=True for non-FS SOWs.
    readiness = _evaluate_field_services_readiness(sow)
    fs_blocking_rules: list[dict] = []
    if target_stage == "ready_for_review" and not readiness["ready"]:
        for entry in readiness["missing"]:
            fs_blocking_rules.append(
                {"reason": entry["reason"], "field": entry["field"]}
            )

    return {
        "blocked": result["blocked"] or bool(fs_blocking_rules),
        "target_stage": target_stage,
        "blocking_rules": [{"reason": msg} for msg in result["block_messages"]] + fs_blocking_rules,
        "field_services_readiness": readiness,
    }


# ---- PROSE SECTION REGENERATION ----

# Section-kind allowlist regex. The kind is taken from a URL path
# segment, so it goes through FastAPI's path parameter parsing
# (already restricted to non-slash characters), but we re-validate
# server-side to bound the value stored on prose_sections.kind and
# to reject obvious injection attempts like ``../`` or empty kinds.
_SECTION_KIND_PATTERN = __import__("re").compile(r"^[a-z][a-z0-9_]{0,63}$")
#
# Per-section regenerate replaces a single prose section without
# touching the rest of the SOW. The route hands a deterministic
# candidate back to the caller; nothing is written unless the caller
# explicitly posts ``mode="replace"``. That preview-by-default shape
# is the safe path Chandra asked for so a casual click cannot
# overwrite a hand-edited section.


def _find_prose_section_index(sow: dict, kind: str) -> Optional[int]:
    """Return the index of the prose section matching ``kind`` on the
    SOW, or None if missing. Identity is by ``kind`` because that is
    the only stable handle in the existing model — section_id is not
    on SOWProseSection today."""
    sections = sow.get("prose_sections") or []
    for idx, entry in enumerate(sections):
        if isinstance(entry, dict) and entry.get("kind") == kind:
            return idx
    return None


@router.post(
    "/opportunities/{opportunity_id}/sows/{sow_id}/sections/{section_kind}/regenerate",
    dependencies=[Depends(require_ai_permission("sales_ops.solutions_design.edit"))],
)
async def regenerate_sow_section(
    opportunity_id: str,
    sow_id: str,
    section_kind: str,
    body: SOWSectionRegenerateRequest,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Regenerate one Professional Services SOW prose section.

    Authorization mirrors SOW editing
    (``sales_ops.solutions_design.edit``) because the candidate is
    composed from SOW context the caller already has the right to see
    and, in replace mode, the route writes back to the SOW.

    Tenant scoping follows the existing implicit-opportunity pattern:
    the SOW must belong to the opportunity in the path. A SOW from a
    different opportunity returns 404 — never 403 — so a probing
    caller cannot infer existence across tenants.

    The route is Professional Services only. Field Services SOWs
    carry materials/hardware/services lifecycle, not prose sections,
    so calls against a FS SOW return 400.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if not _SECTION_KIND_PATTERN.match(section_kind or ""):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_section_kind",
                "message": (
                    "Section kind must match ^[a-z][a-z0-9_]{0,63}$. "
                    "Valid kinds are lowercase identifiers like 'executive_summary'."
                ),
            },
        )

    sow = await db.opportunity_sows.find_one(
        {"sow_id": sow_id, "opportunity_id": opportunity_id},
        {"_id": 0},
    )
    if not sow:
        raise HTTPException(status_code=404, detail="SOW not found")

    if (sow.get("line_of_business") or "") != "professional_services":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "sow_not_professional_services",
                "message": (
                    "Section regeneration is only available for Professional "
                    "Services SOWs."
                ),
            },
        )

    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id}, {"_id": 0}
    )

    # Compose the candidate. The service is pure: no DB writes, no
    # LLM call, no env reads. See backend/services/sow_section_regeneration.py.
    from services.sow_section_regeneration import build_section_candidate

    candidate_content, citations = build_section_candidate(
        sow,
        opportunity,
        section_kind,
        user_instruction=body.user_instruction,
    )

    applied = False
    new_version: Optional[int] = None
    previous_content: Optional[str] = None
    existing_idx = _find_prose_section_index(sow, section_kind)

    if body.mode == "replace":
        now = datetime.now(timezone.utc)
        actor_id = getattr(current_user, "user_id", None)
        new_version = (sow.get("version") or 1) + 1

        existing_sections = list(sow.get("prose_sections") or [])
        replacement_entry = {
            "kind": section_kind,
            "content": candidate_content,
            "source_citations": citations or None,
        }
        if existing_idx is not None:
            previous_entry = (
                existing_sections[existing_idx]
                if isinstance(existing_sections[existing_idx], dict)
                else {}
            )
            previous_content = previous_entry.get("content")
            # Preserve order/removable from the existing row.
            replacement_entry["order"] = previous_entry.get("order")
            replacement_entry["removable"] = previous_entry.get("removable", True)
            existing_sections[existing_idx] = replacement_entry
        else:
            # New entry — defaults that match SOWProseSection.
            replacement_entry["order"] = None
            replacement_entry["removable"] = True
            existing_sections.append(replacement_entry)

        await db.opportunity_sows.update_one(
            {"sow_id": sow_id, "opportunity_id": opportunity_id},
            {
                "$set": {
                    "prose_sections": existing_sections,
                    "version": new_version,
                    "updated_at": now,
                    "last_edited_by": actor_id,
                }
            },
        )
        applied = True

        try:
            await audit_log_action(
                action="sales_ops.sow.section_regenerated",
                actor_id=actor_id,
                actor_email=getattr(current_user, "email", "unknown"),
                resource_type="opportunity_sow",
                resource_id=sow_id,
                details={
                    "opportunity_id": opportunity_id,
                    "line_of_business": sow.get("line_of_business"),
                    "section_kind": section_kind,
                    "new_version": new_version,
                    "had_previous_content": bool(previous_content),
                    "user_instruction_provided": bool(
                        body.user_instruction and body.user_instruction.strip()
                    ),
                    "citations": citations or [],
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Audit row for sow.section_regenerated failed: %s", e)

    return {
        "sow_id": sow_id,
        "opportunity_id": opportunity_id,
        "kind": section_kind,
        "candidate_content": candidate_content,
        "source_citations": citations,
        "applied": applied,
        "revision_version": new_version,
        "mode": body.mode,
    }


@router.delete("/opportunities/{opportunity_id}/sow")
async def delete_sow(
    opportunity_id: str,
    lob: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Delete SOW for a specific LOB."""
    if not has_perm(current_user, "sales_ops.solutions_design.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    result = await db.opportunity_sows.delete_one({
        "opportunity_id": opportunity_id,
        "line_of_business": lob
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="SOW not found")
    
    logger.info(f"SOW deleted for opportunity {opportunity_id} LOB {lob} by {current_user.email}")
    return {"message": "SOW deleted successfully"}
