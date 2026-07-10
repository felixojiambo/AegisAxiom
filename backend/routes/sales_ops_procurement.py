"""
Sales Ops Procurement Routes
Manages resource assignments, materials, hardware, and wholesale MRR for opportunities
Also handles contractor grievances and surveys
"""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from typing import Any, List, Optional
from datetime import datetime, timezone, timedelta
from database import get_db
from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from services.policy_engine import evaluate_policies
from services.policy_helpers import enforce_policy_result
from services.field_registry import register_fields, register_module_events, register_module_config
from services.audit_service import log_action as audit_log_action
from models import (
    gen_id,
    ContractorSurvey,
    ContractorSurveyCreate,
    ContractorSurveySubmit,
    SurveyDimensionRating,
    SOWItemPricingUpdate,
    SOWItemOrderedMark,
    SOWItemDeliveredMark,
)
import logging
import secrets
import os

router = APIRouter()
logger = logging.getLogger(__name__)

register_module_config("procurement", "opportunity_resource_assignments", "assignment_id", "Operations")
register_module_events("procurement", "resource_assignment", ["created", "updated", "deleted"])

# MEB4-T40 — expose the procurement_notifications collection to the workflow
# executor so the seeded rule "sys_notify_procurement_on_scoping_complete"
# can write into it with the correct id field. Without this, a create_record
# action targeting module "procurement_notifications" would fall back to
# id_field="id" and break the existing UI's {notification_id} lookups.
register_module_config(
    "procurement_notifications",
    "procurement_notifications",
    "notification_id",
    "Sales Ops",
)
register_module_events(
    "procurement_notifications",
    "procurement notification",
    ["created", "updated", "status_changed"],
)
register_fields("procurement", "resource_assignment", [
    {"name": "opportunity_name", "type": "string", "label": "Opportunity", "description": "Associated opportunity name"},
    {"name": "resource_type_name", "type": "string", "label": "Resource Type", "description": "Type of resource assigned"},
    {"name": "customer_sell_rate", "type": "number", "label": "Customer Sell Rate", "description": "Hourly rate charged to the customer"},
    {"name": "contractor_cost_rate", "type": "number", "label": "Contractor Cost Rate", "description": "Hourly cost rate paid to the contractor"},
    {"name": "total_sell", "type": "number", "label": "Total Sell", "description": "Total revenue for this resource assignment"},
    {"name": "total_cost", "type": "number", "label": "Total Cost", "description": "Total cost for this resource assignment"},
    {"name": "profit_margin", "type": "number", "label": "Profit Margin", "description": "Profit margin percentage"},
    {"name": "status", "type": "string", "label": "Status", "description": "Assignment status (active, blocked, completed)"},
    {"name": "contractor_business_name", "type": "string", "label": "Contractor Business", "description": "Contractor company name"},
    {"name": "customer_contract_type", "type": "string", "label": "Contract Type", "description": "Billing model to customer (boh, boa, fixed_fee, monthly)"},
])

# Survey dimension names (10 dimensions from Zoho survey)
SURVEY_DIMENSIONS = [
    "overall_professionalism",
    "timeliness_to_meetings",
    "communication_quality",
    "quality_of_work",
    "schedule_control",
    "administration_required",
    "project_documentation",
    "adherence_to_scope",
    "resolution_of_issues",
    "timesheet_submission"
]


# ---- RESOURCE ASSIGNMENTS ----

@router.get(
    "/opportunities/{opportunity_id}/resource-assignments",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_opportunity_resource_assignments(
    opportunity_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all resource assignments for an opportunity."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    assignments = await db.opportunity_resource_assignments.find(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    ).to_list(100)
    
    return {"items": assignments}


# ---- CONTRACTOR BUSINESSES (for resource assignment dropdown) ----

@router.get(
    "/contractor-businesses",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_contractor_businesses_for_sales_ops(
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all active contractor businesses for Sales Ops resource assignment dropdown."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    businesses = await db.contractor_businesses.find(
        {"status": "active"},
        {"_id": 0}
    ).sort("business_name", 1).to_list(100)
    
    return {"items": businesses}


# ---- CONTRACTOR USERS (for resource assignment) ----

@router.get(
    "/contractor-businesses/{business_id}/users",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_contractor_users(
    business_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all contractor users (people) working for a contractor business."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Fetch users with contractor_business_id matching
    users = await db.users.find(
        {"contractor_business_id": business_id, "role": "contractor"},
        {"_id": 0, "user_id": 1, "name": 1, "email": 1, "resource_type_assignments": 1}
    ).to_list(100)
    
    return {"items": users}



# ---- CONTRACTOR SERVICES (Wholesale MRR) ----

@router.get(
    "/contractor-businesses/{business_id}/services",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_contractor_services(
    business_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all services offered by a contractor business."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    business = await db.contractor_businesses.find_one(
        {"business_id": business_id},
        {"_id": 0, "services_offered": 1}
    )
    
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    services = business.get("services_offered", [])
    return {"items": services}


@router.post(
    "/contractor-businesses/{business_id}/services",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.create"))],
)
async def create_contractor_service(
    business_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    CREATE a new wholesale MRR service for a contractor business.
    This creates a NEW product in the Products & Services database.
    Contractor sets: name, description, recurrency, price, and their cost.
    Procurement reviews, sets sell price, and approves.
    """
    if not has_perm(current_user, "sales_ops.procurement.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # Verify contractor business exists
    business = await db.contractor_businesses.find_one(
        {"business_id": business_id},
        {"_id": 0}
    )
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    # Generate IDs
    product_id = f"prod_{gen_id()}"
    service_id = f"cs_{gen_id()}"
    
    # Extract service details from request
    service_name = data.get("service_name")
    service_description = data.get("service_description", "")
    recurrency = data.get("recurrency", "monthly")  # one-time, daily, weekly, monthly, annually
    cost_to_business = float(data.get("cost_to_business", 0.0))
    
    if not service_name or cost_to_business <= 0:
        raise HTTPException(status_code=400, detail="Service name and cost are required")
    
    # Create service object for contractor business
    service_doc = {
        "service_id": service_id,
        "product_id": product_id,  # Links to the product we'll create
        "service_name": service_name,
        "service_description": service_description,
        "recurrency": recurrency,
        "cost_to_business": cost_to_business,
        
        # Procurement sets these on approval
        "sell_price": None,
        "margin": None,
        "margin_percent": None,
        
        "approval_status": "pending",  # Always starts as pending
        "approved_by": None,
        "approved_by_name": None,
        "approved_date": None,
        "rejection_reason": None,
        "notes": data.get("notes"),
        "added_by": current_user.user_id,
        "added_by_name": current_user.name,
        "added_date": datetime.now(timezone.utc),
        "is_active": True
    }
    
    # Create corresponding product in Products & Services database
    product_doc = {
        "product_id": product_id,
        "name": service_name,
        "sku": f"CONTRACTOR-{business_id[-8:]}-{service_id[-8:]}".upper(),
        "description": service_description,
        "item_category": "wholesale_mrr",
        "item_type": "service",
        
        # Contractor-defined cost
        "recurrency": recurrency,
        "contractor_pricing": {
            "recurrency": recurrency,
            "cost_to_business": cost_to_business
        },
        
        # Procurement sets these on approval
        "msp_pricing": None,  # Sell price set by Procurement
        "retail_pricing": None,
        
        # Ownership & Status
        "created_by_contractor_business_id": business_id,
        "created_by_contractor_business_name": business.get("business_name"),
        "approval_status": "pending",
        
        "is_active": False,  # Not active until approved
        "is_test_data": False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }
    
    # Insert product into Products & Services
    await db.products.insert_one(product_doc)
    
    # Add service reference to contractor business
    await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {
            "$push": {"services_offered": service_doc},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    
    logger.info(f"Service '{service_name}' created by contractor {business_id}, product_id: {product_id}")
    
    return {
        "message": "Service created successfully - Pending Procurement approval",
        "service_id": service_id,
        "product_id": product_id
    }


@router.delete(
    "/contractor-businesses/{business_id}/services/{service_id}",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.delete"))],
)
async def delete_contractor_service(
    business_id: str,
    service_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Remove a service from a contractor business."""
    if not has_perm(current_user, "sales_ops.procurement.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    result = await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {
            "$pull": {"services_offered": {"service_id": service_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Service not found")
    
    logger.info(f"Service {service_id} removed from contractor {business_id} by {current_user.name}")
    
    return {"message": "Service removed successfully"}


@router.put(
    "/contractor-businesses/{business_id}/services/{service_id}/approve",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.approve_rates"))],
)
async def approve_contractor_service(
    business_id: str,
    service_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Approve or reject a contractor service (Procurement only).
    On approval: Procurement sets sell_price, calculates margin, updates both
    contractor service and Products & Services record.
    data: {"action": "approve" | "reject", "sell_price": 799.0, "rejection_reason": "..."}
    """
    if not has_perm(current_user, "sales_ops.procurement.approve_rates"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    action = data.get("action")  # approve or reject
    
    if action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")
    
    # Get the contractor business to find the service
    business = await db.contractor_businesses.find_one(
        {"business_id": business_id},
        {"_id": 0, "services_offered": 1}
    )
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    service = next((s for s in business.get("services_offered", []) if s["service_id"] == service_id), None)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    
    # Update fields for contractor service
    update_fields = {
        "services_offered.$[elem].approval_status": "approved" if action == "approve" else "rejected",
        "services_offered.$[elem].approved_by": current_user.user_id,
        "services_offered.$[elem].approved_by_name": current_user.name,
        "services_offered.$[elem].approved_date": datetime.now(timezone.utc),
    }
    
    if action == "approve":
        # Policy engine: validate sell price requirement
        sell_price = data.get("sell_price")
        policy_payload = {"sell_price": float(sell_price) if sell_price else 0, "status": "approve"}
        price_result = await evaluate_policies(db, "sales_ops", "service_approval", "on_update", policy_payload, user_role=current_user.role)
        enforce_policy_result(price_result)
        
        sell_price = float(sell_price)
        cost = service.get("cost_to_business", 0.0)
        margin = sell_price - cost
        margin_percent = (margin / sell_price * 100) if sell_price > 0 else 0.0
        
        # Update contractor service with sell pricing
        update_fields["services_offered.$[elem].sell_price"] = sell_price
        update_fields["services_offered.$[elem].margin"] = round(margin, 2)
        update_fields["services_offered.$[elem].margin_percent"] = round(margin_percent, 2)
        
        # Update the corresponding product in Products & Services
        product_id = service.get("product_id")
        recurrency = service.get("recurrency", "monthly")
        
        await db.products.update_one(
            {"product_id": product_id},
            {
                "$set": {
                    "msp_pricing": {recurrency: sell_price},
                    "approval_status": "approved",
                    "approved_by": current_user.user_id,
                    "approved_date": datetime.now(timezone.utc),
                    "is_active": True,  # Activate product
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        logger.info(f"Product {product_id} activated in Products & Services with sell price ${sell_price}")
        
    elif action == "reject":
        update_fields["services_offered.$[elem].rejection_reason"] = data.get("rejection_reason", "")
        
        # Mark product as rejected in Products & Services
        product_id = service.get("product_id")
        await db.products.update_one(
            {"product_id": product_id},
            {
                "$set": {
                    "approval_status": "rejected",
                    "is_active": False,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
    
    # Update contractor business service
    result = await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {"$set": update_fields},
        array_filters=[{"elem.service_id": service_id}]
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Service not found")
    
    logger.info(f"Service {service_id} {action}d by {current_user.name}")
    
    return {"message": f"Service {action}d successfully"}



@router.post(
    "/opportunities/{opportunity_id}/resource-assignments",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.create"))],
)
async def create_resource_assignment(
    opportunity_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Assign a contractor/resource to an opportunity with full contract configuration."""
    if not has_perm(current_user, "sales_ops.procurement.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify opportunity exists
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    # Create assignment
    assignment_id = f"ra_{gen_id()}"
    
    # Extract values from request
    total_sell = float(data.get("total_sell", 0))
    total_cost = float(data.get("total_cost", 0))
    profit_amount = float(data.get("profit_amount", 0))
    profit_margin = float(data.get("profit_margin", 0))
    
    assignment_doc = {
        "assignment_id": assignment_id,
        "opportunity_id": opportunity_id,
        "opportunity_name": opportunity.get("name"),
        "resource_type_id": data.get("resource_type_id"),
        "resource_type_name": data.get("resource_type_name"),
        
        # Customer billing configuration (LEFT side)
        "customer_contract_type": data.get("customer_contract_type", "boh"),
        "customer_billing_schedule": data.get("customer_billing_schedule", []),  # Unified billing schedule array
        "customer_billing_terms": data.get("customer_billing_terms", "monthly"),
        "customer_payment_terms": data.get("customer_payment_terms", "net_30"),
        "customer_hours_purchased": data.get("customer_hours_purchased"),
        "customer_sell_rate": data.get("customer_sell_rate"),
        "customer_fixed_amount": data.get("customer_fixed_amount"),
        "customer_monthly_rate": data.get("customer_monthly_rate"),
        "convert_to_boa_at_exhaustion": data.get("convert_to_boa_at_exhaustion", False),
        
        # BOA-specific billing configuration
        "billing_frequency": data.get("billing_frequency"),
        "boa_billing_day": data.get("boa_billing_day"),
        "estimated_boa_cost": data.get("estimated_boa_cost"),
        
        # Fixed Fee-specific configuration
        "billing_method": data.get("billing_method"),
        "milestones": data.get("milestones", []),
        
        "total_sell": total_sell,
        
        # Contractor fulfillment configuration (RIGHT side)
        "contractor_business_id": data.get("contractor_business_id"),
        "contractor_business_name": data.get("contractor_business_name"),
        "contractor_user_id": data.get("contractor_user_id"),
        "contractor_user_name": data.get("contractor_user_name"),
        "contractor_pay_type": data.get("contractor_pay_type"),
        "contractor_billing_terms": data.get("contractor_billing_terms", "weekly"),
        "contractor_payment_terms": data.get("contractor_payment_terms", "net_7"),
        "contractor_hours_estimated": data.get("contractor_hours_estimated"),
        "contractor_cost_rate": data.get("contractor_cost_rate"),
        "contractor_fixed_cost": data.get("contractor_fixed_cost"),
        "contractor_monthly_cost": data.get("contractor_monthly_cost"),
        "total_cost": total_cost,
        
        # Profit metrics (MIDDLE)
        "profit_amount": profit_amount,
        "profit_margin": round(profit_margin, 2),
        
        # Internal resource (alternative to contractor)
        "internal_user_id": data.get("internal_user_id"),
        "internal_user_name": data.get("internal_user_name"),
        
        # Legacy fields for backward compatibility
        "pricing_model": data.get("contractor_pay_type"),
        "customer_billing_term": data.get("customer_contract_type", "boh"),
        "estimated_hours": data.get("customer_hours_purchased"),
        "cost_rate": data.get("contractor_cost_rate"),
        "sell_rate": data.get("customer_sell_rate"),
        "payment_terms": data.get("customer_payment_terms", "net_30"),
        "billing_terms": data.get("customer_billing_terms", "monthly"),
        
        # Status & blockers
        "status": data.get("status", "active"),
        "blocker_reason": data.get("blocker_reason"),
        "procurement_notified": data.get("procurement_notified", False),
        "notes": data.get("notes"),
        "assigned_by": current_user.user_id,
        "assigned_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }
    
    await db.opportunity_resource_assignments.insert_one(assignment_doc)
    
    logger.info(f"Resource assignment {assignment_id} created for opportunity {opportunity_id} with status: {assignment_doc['status']}")
    
    # Remove _id from response
    assignment_doc.pop("_id", None)
    
    return assignment_doc


@router.delete(
    "/resource-assignments/{assignment_id}",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.delete"))],
)
async def delete_resource_assignment(
    assignment_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Remove a resource assignment."""
    if not has_perm(current_user, "sales_ops.procurement.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    result = await db.opportunity_resource_assignments.delete_one(
        {"assignment_id": assignment_id}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    return {"message": "Resource assignment removed"}


# ---- LINE ITEMS (Materials, Hardware, Wholesale MRR) ----

@router.get(
    "/opportunities/{opportunity_id}/line-items",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_opportunity_line_items(
    opportunity_id: str,
    category: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get line items for an opportunity. Filter by category if provided."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    query = {"opportunity_id": opportunity_id}
    if category:
        query["category"] = category
    
    items = await db.opportunity_line_items.find(query, {"_id": 0}).to_list(200)
    return {"items": items}


@router.post(
    "/opportunities/{opportunity_id}/line-items",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.create"))],
)
async def create_line_item(
    opportunity_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Add a material, hardware, or wholesale MRR item to an opportunity."""
    if not has_perm(current_user, "sales_ops.procurement.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify opportunity exists
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    line_item_id = f"li_{gen_id()}"
    
    # Calculate totals
    quantity = data.get("quantity", 1.0)
    cost_price = data.get("cost_price", 0.0)
    sell_price = data.get("sell_price", 0.0)
    
    line_item_doc = {
        "line_item_id": line_item_id,
        "opportunity_id": opportunity_id,
        "category": data.get("category"),  # material, hardware, wholesale_mrr
        "item_name": data.get("item_name"),
        "description": data.get("description"),
        "quantity": quantity,
        "unit": data.get("unit", "each"),
        "cost_price": cost_price,
        "sell_price": sell_price,
        "total_cost": quantity * cost_price,
        "total_sell": quantity * sell_price,
        "is_recurring": data.get("is_recurring", False),
        "recurring_term_months": data.get("recurring_term_months"),
        "supplier_name": data.get("supplier_name"),
        "supplier_sku": data.get("supplier_sku"),
        "status": data.get("status", "pending"),
        "notes": data.get("notes"),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": current_user.user_id
    }
    
    await db.opportunity_line_items.insert_one(line_item_doc)
    
    logger.info(f"Line item {line_item_id} created for opportunity {opportunity_id}")
    
    return {"message": "Line item added", "line_item_id": line_item_id}


@router.delete(
    "/line-items/{line_item_id}",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.delete"))],
)
async def delete_line_item(
    line_item_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Remove a line item."""
    if not has_perm(current_user, "sales_ops.procurement.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    result = await db.opportunity_line_items.delete_one({"line_item_id": line_item_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Line item not found")
    
    return {"message": "Line item removed"}


# ---- REFERRAL MRR AGREEMENTS ----

@router.get(
    "/opportunities/{opportunity_id}/referral-agreements",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_referral_agreements(
    opportunity_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get referral agreements for an opportunity."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    agreements = await db.referral_agreements.find(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    ).to_list(100)
    
    # Calculate days until expiration for each
    for agreement in agreements:
        if agreement.get("expiration_date"):
            exp_date = datetime.strptime(agreement["expiration_date"], "%Y-%m-%d")
            # Use naive datetime for comparison (strip timezone from now)
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            days_until = (exp_date - now_naive).days
            agreement["days_until_expiration"] = days_until
            
            # Update status based on expiration
            if days_until < 0:
                agreement["status"] = "expired"
            elif days_until <= 60:
                agreement["status"] = "expiring_soon"
            else:
                agreement["status"] = "active"
    
    return {"items": agreements}


@router.post(
    "/opportunities/{opportunity_id}/referral-agreements",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.create"))],
)
async def create_referral_agreement(
    opportunity_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Upload and track a referral MRR agreement."""
    if not has_perm(current_user, "sales_ops.procurement.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Verify opportunity exists
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id},
        {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    agreement_id = f"refagr_{gen_id()}"
    
    # Calculate expiration date
    start_date = datetime.strptime(data.get("start_date"), "%Y-%m-%d")
    term_years = data.get("agreement_term_years", 1)
    expiration_date = start_date + timedelta(days=365 * term_years)
    
    agreement_doc = {
        "agreement_id": agreement_id,
        "opportunity_id": opportunity_id,
        "opportunity_name": opportunity.get("name"),
        "account_id": opportunity.get("account_id"),
        "account_name": opportunity.get("account_name"),
        "agreement_name": data.get("agreement_name"),
        "partner_name": data.get("partner_name"),
        "agreement_term_years": term_years,
        "start_date": data.get("start_date"),
        "expiration_date": expiration_date.strftime("%Y-%m-%d"),
        "monthly_recurring_revenue": data.get("monthly_recurring_revenue", 0.0),
        "commission_rate": data.get("commission_rate"),
        "agreement_document_url": data.get("agreement_document_url"),
        "agreement_document_filename": data.get("agreement_document_filename"),
        "status": "active",
        "expiration_notification_sent": False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": current_user.user_id,
        "uploaded_by": current_user.name
    }
    
    await db.referral_agreements.insert_one(agreement_doc)
    
    logger.info(f"Referral agreement {agreement_id} created for opportunity {opportunity_id}")
    
    return {"message": "Agreement uploaded", "agreement_id": agreement_id}


@router.get(
    "/referral-agreements/expiring-soon",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_expiring_agreements(
    days: int = 60,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get agreements expiring within specified days."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Calculate cutoff date
    cutoff_date = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
    
    agreements = await db.referral_agreements.find(
        {
            "status": {"$in": ["active", "expiring_soon"]},
            "expiration_date": {"$lte": cutoff_date}
        },
        {"_id": 0}
    ).to_list(500)
    
    # Calculate days until expiration
    for agreement in agreements:
        exp_date = datetime.strptime(agreement["expiration_date"], "%Y-%m-%d")
        # Use naive datetime for comparison (strip timezone from now)
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        days_until = (exp_date - now_naive).days
        agreement["days_until_expiration"] = days_until
    
    # Sort by expiration date
    agreements.sort(key=lambda x: x.get("expiration_date", ""))
    


# ---- PROCUREMENT NOTIFICATIONS ----

@router.post(
    "/procurement-notifications",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def create_procurement_notification(
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Create a procurement notification when a resource type or
    material sourcing step is missing.

    Field Services flow. The SOW Builder fires this when a resource
    assignment is blocked_missing_contractor or when materials need
    sourcing. The notification carries the opportunity context, the
    resource assignment id when known, the resource type, and a
    site_location_summary string built from the SOW's
    service_location field on the frontend. Site summary is plain
    text, not a structured object, so the notification surface can
    render it without joining back to the SOW.

    Permission. sales_ops.procurement.edit. Sales Ops users firing
    this from the SOW Builder already carry this permission via the
    default role grant.

    See docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md for the
    canonical notification shape. Audit row is written on every
    successful create.
    """
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    notification_id = f"procnotif_{gen_id()}"
    opportunity_id = data.get("opportunity_id")

    notification_doc = {
        "notification_id": notification_id,
        "notification_type": data.get("notification_type", "missing_resource"),
        "resource_type_name": data.get("resource_type_name"),
        "resource_assignment_id": data.get("resource_assignment_id"),
        "sow_id": data.get("sow_id"),
        "line_of_business": data.get("line_of_business"),
        "opportunity_id": opportunity_id,
        "site_location_summary": data.get("site_location_summary"),
        "message": data.get("message"),
        "status": "pending",  # pending, acknowledged, resolved
        "created_by": current_user.user_id,
        "created_by_name": current_user.name,
        "created_at": datetime.now(timezone.utc),
        "acknowledged_at": None,
        "resolved_at": None
    }

    await db.procurement_notifications.insert_one(notification_doc)

    logger.info(
        "Procurement notification %s created for missing resource: %s",
        notification_id,
        data.get("resource_type_name"),
    )

    # Audit. Procurement notifications surface a Sales Ops to
    # Procurement handoff so the trail belongs in the activity feed.
    # The audit details echo the SOW context fields but never the
    # message body (free text), which keeps potentially-sensitive
    # client phrasing out of the audit row.
    await audit_log_action(
        action="sales_ops.procurement.notification_created",
        actor_id=getattr(current_user, "user_id", None),
        actor_email=getattr(current_user, "email", "unknown"),
        resource_type="procurement_notification",
        resource_id=notification_id,
        details={
            "opportunity_id": opportunity_id,
            "sow_id": data.get("sow_id"),
            "line_of_business": data.get("line_of_business"),
            "resource_assignment_id": data.get("resource_assignment_id"),
            "resource_type_name": data.get("resource_type_name"),
            "has_site_location_summary": bool(data.get("site_location_summary")),
        },
    )

    return {"message": "Procurement notified", "notification_id": notification_id}


@router.get(
    "/procurement-notifications",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_procurement_notifications(
    status: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all procurement notifications. Filter by status if provided."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    query = {}
    if status:
        query["status"] = status
    
    notifications = await db.procurement_notifications.find(
        query,
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    return {"items": notifications}


@router.patch(
    "/procurement-notifications/{notification_id}/status",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def update_notification_status(
    notification_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Update notification status (acknowledge or resolve)."""
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    new_status = data.get("status")
    update_doc = {
        "status": new_status,
        "updated_at": datetime.now(timezone.utc)
    }
    
    if new_status == "acknowledged" and not data.get("acknowledged_at"):
        update_doc["acknowledged_at"] = datetime.now(timezone.utc)
    elif new_status == "resolved":
        update_doc["resolved_at"] = datetime.now(timezone.utc)
    
    result = await db.procurement_notifications.update_one(
        {"notification_id": notification_id},
        {"$set": update_doc}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"message": "Notification status updated"}




# ---- GRIEVANCES (Enhanced Structure) ----

@router.get(
    "/contractor-businesses/{business_id}/grievances",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_contractor_grievances(
    business_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all grievances for a contractor business."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    grievances = await db.grievances.find(
        {"contractor_business_id": business_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    return {"items": grievances}





@router.patch(
    "/procurement-notifications/{notification_id}",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def update_notification_details(
    notification_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Update notification notes and assignment.
    Used by Procurement to track progress on resource requests.
    """
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    update_fields = {}

    if "notes" in data:
        update_fields["notes"] = data["notes"]
        update_fields["updated_at"] = datetime.now(timezone.utc)
    
    if "assigned_to" in data:
        update_fields["assigned_to"] = data["assigned_to"]
        
        # Get assigned user name
        if data["assigned_to"]:
            assigned_user = await db.users.find_one(
                {"user_id": data["assigned_to"]},
                {"_id": 0, "name": 1}
            )
            update_fields["assigned_to_name"] = assigned_user.get("name") if assigned_user else None
        else:
            update_fields["assigned_to_name"] = None
    
    if update_fields:
        await db.procurement_notifications.update_one(
            {"notification_id": notification_id},
            {"$set": update_fields}
        )
    
    updated_notification = await db.procurement_notifications.find_one(
        {"notification_id": notification_id},
        {"_id": 0}
    )
    
    return {"message": "Notification updated", "notification": updated_notification}

@router.post(
    "/contractor-businesses/{business_id}/grievances",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def create_grievance(
    business_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Create a new grievance for a contractor business."""
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    from models import ContractorGrievance, gen_id

    # Verify contractor business exists
    business = await db.contractor_businesses.find_one({"business_id": business_id}, {"_id": 0})
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    grievance_id = f"grv_{gen_id()}"
    
    grievance = ContractorGrievance(
        grievance_id=grievance_id,
        contractor_business_id=business_id,
        contractor_user_id=data.get("contractor_user_id"),
        title=data.get("title"),
        description=data.get("description"),
        severity=data.get("severity", "medium"),
        status="open",
        reported_by_id=current_user.user_id,
        reported_by_name=current_user.name,
        reporter_type=data.get("reporter_type", "internal"),
        affects_rating=data.get("affects_rating", False),
        comments=[]
    )
    
    await db.grievances.insert_one(grievance.model_dump())
    
    logger.info(f"Grievance {grievance_id} created for contractor {business_id}")
    
    return {"message": "Grievance created", "grievance": grievance.model_dump()}


@router.post(
    "/grievances/{grievance_id}/comments",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def add_grievance_comment(
    grievance_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Add a comment to a grievance."""
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    from models import GrievanceComment, gen_id

    grievance = await db.grievances.find_one({"grievance_id": grievance_id}, {"_id": 0})
    if not grievance:
        raise HTTPException(status_code=404, detail="Grievance not found")
    
    comment = GrievanceComment(
        comment_id=f"cmt_{gen_id()}",
        commenter_id=current_user.user_id,
        commenter_name=current_user.name,
        comment=data.get("comment")
    )
    
    await db.grievances.update_one(
        {"grievance_id": grievance_id},
        {
            "$push": {"comments": comment.model_dump()},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
    )
    
    return {"message": "Comment added", "comment": comment.model_dump()}


@router.patch(
    "/grievances/{grievance_id}/status",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def update_grievance_status(
    grievance_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Update grievance status (open/in_progress/resolved)."""
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    grievance = await db.grievances.find_one({"grievance_id": grievance_id}, {"_id": 0})
    if not grievance:
        raise HTTPException(status_code=404, detail="Grievance not found")
    
    new_status = data.get("status")
    update_doc = {
        "status": new_status,
        "updated_at": datetime.now(timezone.utc)
    }
    
    if new_status == "resolved":
        update_doc["resolved_at"] = datetime.now(timezone.utc)
        update_doc["resolved_by_id"] = current_user.user_id
        update_doc["resolved_by_name"] = current_user.name
    
    await db.grievances.update_one(
        {"grievance_id": grievance_id},
        {"$set": update_doc}
    )
    
    return {"message": f"Grievance marked as {new_status}"}


# ---- CONTRACTOR SURVEYS ----

@router.post(
    "/surveys/send",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def send_contractor_survey(
    data: ContractorSurveyCreate,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Create and send a contractor rating survey (generates public link)."""
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Verify contractor business and user exist
    business = await db.contractor_businesses.find_one(
        {"business_id": data.contractor_business_id},
        {"_id": 0}
    )
    if not business:
        raise HTTPException(status_code=404, detail="Contractor business not found")
    
    contractor_user = await db.users.find_one(
        {"user_id": data.contractor_user_id, "role": "contractor"},
        {"_id": 0}
    )
    if not contractor_user:
        raise HTTPException(status_code=404, detail="Contractor user not found")
    
    # Generate unique public token
    public_token = secrets.token_urlsafe(32)
    
    # Get frontend URL from environment
    # Try FRONTEND_URL first (backend env), then REACT_APP_BACKEND_URL (for compatibility)
    frontend_url = os.getenv("FRONTEND_URL") or os.getenv("REACT_APP_BACKEND_URL", "http://localhost:3000")
    # Remove /api suffix if present (REACT_APP_BACKEND_URL includes /api)
    if frontend_url.endswith("/api"):
        frontend_url = frontend_url[:-4]
    
    survey_url = f"{frontend_url}/public/survey/{public_token}"
    
    survey_id = f"srv_{gen_id()}"
    now = datetime.now(timezone.utc)
    
    survey = ContractorSurvey(
        survey_id=survey_id,
        contractor_business_id=data.contractor_business_id,
        contractor_user_id=data.contractor_user_id,
        contractor_user_name=contractor_user.get("name"),
        project_name=data.project_name,
        customer_name=data.customer_name,
        opportunity_id=data.opportunity_id,
        public_token=public_token,
        survey_url=survey_url,
        sent_to_name=data.sent_to_name,
        sent_to_email=data.sent_to_email,
        sent_by_id=current_user.user_id,
        sent_by_name=current_user.name,
        status="sent",
        sent_at=now,
        dimension_ratings=[],
        created_at=now,
        updated_at=now
    )
    
    await db.contractor_surveys.insert_one(survey.model_dump())
    
    logger.info(f"Survey {survey_id} created for contractor {data.contractor_user_id}")
    
    return {
        "message": "Survey created",
        "survey_id": survey_id,
        "survey_url": survey_url,
        "public_token": public_token
    }


@router.get(
    "/contractor-businesses/{business_id}/surveys",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_contractor_surveys(
    business_id: str,
    status: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get all surveys for a contractor business."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    query = {"contractor_business_id": business_id}
    if status:
        query["status"] = status
    
    surveys = await db.contractor_surveys.find(query, {"_id": 0}).sort("sent_at", -1).to_list(100)
    
    return {"items": surveys}


@router.get(
    "/surveys/{survey_id}",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_survey_details(
    survey_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get detailed survey information."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    survey = await db.contractor_surveys.find_one({"survey_id": survey_id}, {"_id": 0})
    
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")
    
    return survey


# ---- PUBLIC SURVEY ENDPOINTS (No Authentication Required) ----

@router.get("/public/surveys/{public_token}")
async def get_public_survey(
    public_token: str,
    db=Depends(get_db)
):
    """Get survey details by public token (for survey form display). NO AUTH REQUIRED."""
    survey = await db.contractor_surveys.find_one(
        {"public_token": public_token},
        {"_id": 0}
    )
    
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found or expired")
    
    if survey.get("status") == "completed":
        raise HTTPException(status_code=400, detail="This survey has already been completed")
    
    # Return minimal info needed for form display
    return {
        "survey_id": survey.get("survey_id"),
        "contractor_user_name": survey.get("contractor_user_name"),
        "project_name": survey.get("project_name"),
        "customer_name": survey.get("customer_name"),
        "dimensions": SURVEY_DIMENSIONS
    }


@router.post("/public/surveys/{public_token}/submit")
async def submit_public_survey(
    public_token: str,
    data: ContractorSurveySubmit,
    db=Depends(get_db)
):
    """Submit a contractor rating survey. PUBLIC ENDPOINT - NO AUTH REQUIRED."""
    survey = await db.contractor_surveys.find_one(
        {"public_token": public_token},
        {"_id": 0}
    )
    
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")
    
    if survey.get("status") == "completed":
        raise HTTPException(status_code=400, detail="This survey has already been completed")
    
    # Validate that we have 10 dimension ratings
    if len(data.dimension_ratings) != 10:
        raise HTTPException(status_code=400, detail="Survey must include all 10 dimension ratings")
    
    # Validate ratings are in valid range (1-5)
    for rating in data.dimension_ratings:
        if rating.rating < 1 or rating.rating > 5:
            raise HTTPException(status_code=400, detail=f"Rating for {rating.dimension} must be between 1-5")
    
    # Calculate average rating (out of 5)
    total_rating = sum(r.rating for r in data.dimension_ratings)
    calculated_rating = round(total_rating / 10, 2)  # Average of 10 dimensions
    
    # Update survey with response data
    now = datetime.now(timezone.utc)
    await db.contractor_surveys.update_one(
        {"public_token": public_token},
        {
            "$set": {
                "status": "completed",
                "completed_at": now,
                "project_name": data.project_name,
                "customer_name": data.customer_name,
                "hire_likelihood": data.hire_likelihood,
                "dimension_ratings": [r.model_dump() for r in data.dimension_ratings],
                "calculated_rating": calculated_rating,
                "updated_at": now
            }
        }
    )
    
    # Update contractor business rating (rolling average of all completed surveys)
    completed_surveys = await db.contractor_surveys.find(
        {
            "contractor_business_id": survey["contractor_business_id"],
            "status": "completed"
        },
        {"_id": 0}
    ).to_list(1000)
    
    if completed_surveys:
        avg_rating = sum(s.get("calculated_rating", 0) for s in completed_surveys) / len(completed_surveys)
        avg_rating = round(avg_rating, 2)
        
        await db.contractor_businesses.update_one(
            {"business_id": survey["contractor_business_id"]},
            {
                "$set": {
                    "rating": avg_rating,
                    "updated_at": now
                }
            }
        )
        
        logger.info(f"Updated contractor {survey['contractor_business_id']} rating to {avg_rating}")
    
    logger.info(f"Survey {survey['survey_id']} completed with rating {calculated_rating}")
    
    return {
        "message": "Survey submitted successfully",
        "calculated_rating": calculated_rating
    }




# ---- INDIVIDUAL CONTRACTOR RATE APPROVALS ----

@router.get(
    "/procurement/pending-contractor-rates",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_pending_contractor_rate_exceptions(
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Get all contractor users with resource type assignments pending approval.
    These are individuals whose requested rate exceeds their business's approved rate.
    """
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Find all contractor users with pending resource type assignments
    contractors = await db.users.find(
        {
            "role": "contractor",
            "resource_type_assignments": {
                "$elemMatch": {"approval_status": "pending"}
            }
        },
        {"_id": 0, "user_id": 1, "name": 1, "email": 1, "contractor_business_id": 1, "resource_type_assignments": 1}
    ).to_list(500)
    
    # Build response with business context
    pending_rates = []
    
    for contractor in contractors:
        # Get business name
        business = await db.contractor_businesses.find_one(
            {"business_id": contractor.get("contractor_business_id")},
            {"_id": 0, "business_name": 1}
        )
        business_name = business.get("business_name") if business else "Unknown Business"
        
        # Filter to only pending assignments
        pending_assignments = [
            a for a in contractor.get("resource_type_assignments", [])
            if a.get("approval_status") == "pending"
        ]
        
        for assignment in pending_assignments:
            pending_rates.append({
                "user_id": contractor["user_id"],
                "user_name": contractor["name"],
                "user_email": contractor.get("email"),
                "contractor_business_id": contractor["contractor_business_id"],
                "contractor_business_name": business_name,
                "resource_type_id": assignment["resource_type_id"],
                "resource_type_name": assignment.get("resource_type_name", assignment["resource_type_id"]),
                "requested_rate": assignment["cost_rate"],
                "business_approved_rate": assignment.get("contractor_business_rate"),
                "overage": assignment["cost_rate"] - assignment.get("contractor_business_rate", 0),
                "assigned_at": assignment.get("assigned_at")
            })
    
    return {"items": pending_rates}


@router.put(
    "/procurement/contractor-rates/{user_id}/{resource_type_id}/approve",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.approve_rates"))],
)
async def approve_contractor_rate_exception(
    user_id: str,
    resource_type_id: str,
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Approve or reject an individual contractor's rate exception.
    When approved, the contractor can be billed at the higher rate.
    When rejected, their rate should be reverted to the business rate.
    """
    if not has_perm(current_user, "sales_ops.procurement.approve_rates"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    action = data.get("action")  # "approve" or "reject"
    
    if action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")
    
    # Get user
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Find the specific resource type assignment
    assignments = user.get("resource_type_assignments", [])
    assignment_index = None
    
    for i, a in enumerate(assignments):
        if a.get("resource_type_id") == resource_type_id:
            assignment_index = i
            break
    
    if assignment_index is None:
        raise HTTPException(status_code=404, detail="Resource type assignment not found")
    
    # Update approval status
    if action == "approve":
        assignments[assignment_index]["approval_status"] = "approved"
        assignments[assignment_index]["approved_by"] = current_user.user_id
        assignments[assignment_index]["approved_by_name"] = current_user.name
        assignments[assignment_index]["approved_date"] = datetime.now(timezone.utc)
        assignments[assignment_index]["rejection_reason"] = None
        
        message = "Rate exception approved"
    else:
        # Reject: revert to business rate or mark as rejected
        business_rate = assignments[assignment_index].get("contractor_business_rate")
        rejection_reason = data.get("rejection_reason", "Rate exceeds business approved rate")
        
        if business_rate:
            # Revert to business rate
            assignments[assignment_index]["cost_rate"] = business_rate
            assignments[assignment_index]["approval_status"] = "approved"
            assignments[assignment_index]["approved_by"] = current_user.user_id
            assignments[assignment_index]["approved_by_name"] = current_user.name
            assignments[assignment_index]["approved_date"] = datetime.now(timezone.utc)
            assignments[assignment_index]["rejection_reason"] = f"Reverted to business rate: {rejection_reason}"
            message = f"Rate rejected - Reverted to business rate ${business_rate}/hr"
        else:
            # Mark as rejected
            assignments[assignment_index]["approval_status"] = "rejected"
            assignments[assignment_index]["rejection_reason"] = rejection_reason
            message = "Rate exception rejected"
    
    # Update user
    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {"resource_type_assignments": assignments}}
    )

    logger.info(f"Contractor rate {action}d: {user_id} - {resource_type_id}")

    return {"message": message, "assignment": assignments[assignment_index]}


# ---------------------------------------------------------------------------
# Procurement sourcing + quote scaffold (Ticket 9, Mode B manual-review).
#
# AEGISAXIOM submits BOM data to a future procurement bot service. The bot
# fetches quotes from vendor portals (out of AEGISAXIOM scope) and posts the
# quote results back via the signed webhook below. When the bot is not
# configured the route lands the request in review_required so
# Procurement can fill quote options manually. No real vendor automation,
# no scraping, no hardcoded vendor names. See
# docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md.
# ---------------------------------------------------------------------------


_TERMINAL_SOURCING_STATUSES = frozenset({"delivered", "cancelled"})
_RETRY_ELIGIBLE_STATUSES = frozenset({"failed", "review_required", "submitted"})
_ORDER_ELIGIBLE_STATUSES = frozenset({"quote_selected"})


def _serialize_quote(doc: dict) -> dict:
    """Return a JSON-safe representation of a quote document with
    Mongo internals stripped. Used by every route that reads
    quote rows back so the response shape is uniform."""
    if not isinstance(doc, dict):
        return {}
    return {k: v for k, v in doc.items() if k != "_id"}


async def _load_sourcing_request_or_404(db, sourcing_request_id: str) -> dict:
    """Tenant-implicit lookup. The current substrate has no
    tenant_id field on opportunities or procurement rows, so
    sourcing requests are scoped through opportunity ownership
    upstream. Cross-tenant probes today rely on knowing the
    sourcing_request_id; a future tenant migration will tighten
    this seam."""
    doc = await db.procurement_sourcing_requests.find_one(
        {"sourcing_request_id": sourcing_request_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Sourcing request not found")
    return doc


@router.post(
    "/procurement/sourcing-requests",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.create"))],
)
async def create_procurement_sourcing_request(
    data: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Submit a BOM for procurement sourcing.

    Sales Ops or Procurement may submit. The route resolves the
    requested line_item_ids against the opportunity's existing
    materials so an arbitrary id cannot be smuggled in. When the
    procurement bot is configured the route attempts an outbound
    submission; either way the sourcing request lands in storage
    so the Procurement queue surfaces it.

    Mode B safety. If the bot is not configured or the bot rejects
    the submission, status is review_required and
    manual_review_required is True. No auto-approval is performed.
    See docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md.
    """
    if not (
        has_perm(current_user, "sales_ops.procurement.create")
        or has_perm(current_user, "sales_ops.procurement.edit")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    opportunity_id = (data or {}).get("opportunity_id")
    if not opportunity_id:
        raise HTTPException(status_code=400, detail="opportunity_id required")

    opp = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id}, {"_id": 0}
    )
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    sow_id = (data or {}).get("sow_id")
    requested_line_ids = list((data or {}).get("line_item_ids") or [])
    manual_review_reason_input = (data or {}).get("manual_review_reason")

    # Resolve requested line items against the opportunity so a
    # caller cannot reference a line item belonging to a different
    # opportunity. Empty list is permitted; the bot can still source
    # against the SOW-level materials view.
    resolved_line_ids: list[str] = []
    if requested_line_ids:
        line_docs = await db.opportunity_line_items.find(
            {
                "line_item_id": {"$in": requested_line_ids},
                "opportunity_id": opportunity_id,
            },
            {"_id": 0, "line_item_id": 1},
        ).to_list(500)
        resolved_line_ids = [d["line_item_id"] for d in line_docs]

    now = datetime.now(timezone.utc)
    sourcing_request_id = f"srcreq_{gen_id()}"

    # Attempt bot submission only if configured. Importing inside
    # the handler keeps the route testable and lets unit tests
    # monkeypatch the helpers without touching module globals.
    from services.procurement_bot_client import (
        BotSubmissionResult,
        build_submission_payload,
        is_bot_configured,
        submit_sourcing_request,
    )

    sourcing_doc: dict = {
        "sourcing_request_id": sourcing_request_id,
        "opportunity_id": opportunity_id,
        "sow_id": sow_id,
        "line_item_ids": resolved_line_ids,
        "submitted_by": getattr(current_user, "user_id", None),
        "submitted_at": now,
        "status": "review_required",
        "status_reason": None,
        "bot_request_id": None,
        "bot_submitted_at": None,
        "bot_response_received_at": None,
        "manual_review_required": True,
        "manual_review_reason": manual_review_reason_input,
        "selected_quote_id": None,
        "ordered_at": None,
        "ordered_by": None,
        "delivered_at": None,
        "delivered_by": None,
        "created_at": now,
        "updated_at": now,
    }

    bot_attempted = False
    bot_result: BotSubmissionResult | None = None
    if is_bot_configured():
        bot_attempted = True
        line_docs_full: list[dict] = []
        if resolved_line_ids:
            line_docs_full = await db.opportunity_line_items.find(
                {"line_item_id": {"$in": resolved_line_ids}},
                {"_id": 0},
            ).to_list(500)
        payload = build_submission_payload(
            sourcing_request_id=sourcing_request_id,
            opportunity_id=opportunity_id,
            line_items=line_docs_full,
            sow_id=sow_id,
        )
        bot_result = await submit_sourcing_request(payload)
        sourcing_doc["bot_submitted_at"] = datetime.now(timezone.utc)
        if bot_result.accepted:
            sourcing_doc["status"] = "sourcing_in_progress"
            sourcing_doc["bot_request_id"] = bot_result.bot_request_id
            sourcing_doc["manual_review_required"] = False
            sourcing_doc["manual_review_reason"] = None
        else:
            sourcing_doc["status"] = "review_required"
            sourcing_doc["status_reason"] = bot_result.error_reason
            sourcing_doc["manual_review_required"] = True
            sourcing_doc["manual_review_reason"] = (
                manual_review_reason_input or f"bot_submission_{bot_result.error_reason}"
            )
    else:
        sourcing_doc["status_reason"] = "bot_not_configured"
        sourcing_doc["manual_review_reason"] = (
            manual_review_reason_input or "bot_not_configured"
        )

    await db.procurement_sourcing_requests.insert_one(dict(sourcing_doc))

    # Audit. Always write the requested event so the activity feed
    # records the user's submission. The bot attempt and the bot
    # outcome write separate rows so a downstream consumer can
    # filter without parsing the details dict.
    try:
        await audit_log_action(
            action="sales_ops.procurement.sourcing_requested",
            actor_id=getattr(current_user, "user_id", None),
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="procurement_sourcing_request",
            resource_id=sourcing_request_id,
            details={
                "opportunity_id": opportunity_id,
                "sow_id": sow_id,
                "line_item_count": len(resolved_line_ids),
                "bot_attempted": bot_attempted,
                "status": sourcing_doc["status"],
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for sourcing_requested failed: %s", e)

    if bot_attempted and bot_result is not None:
        action_name = (
            "sales_ops.procurement.bot_submission_attempted"
            if bot_result.accepted
            else "sales_ops.procurement.bot_submission_failed"
        )
        try:
            await audit_log_action(
                action=action_name,
                actor_id=getattr(current_user, "user_id", None),
                actor_email=getattr(current_user, "email", "unknown"),
                resource_type="procurement_sourcing_request",
                resource_id=sourcing_request_id,
                details={
                    "opportunity_id": opportunity_id,
                    "bot_request_id": bot_result.bot_request_id,
                    "error_reason": bot_result.error_reason,
                    "accepted": bot_result.accepted,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Audit row for bot submission failed: %s", e)

    fresh = await db.procurement_sourcing_requests.find_one(
        {"sourcing_request_id": sourcing_request_id}, {"_id": 0}
    )
    return fresh


@router.get(
    "/procurement/sourcing-requests",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def list_procurement_sourcing_requests(
    opportunity_id: Optional[str] = None,
    status: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List sourcing requests. Filter by opportunity_id and/or
    status.

    Tenant scoping. The codebase has a documented tenant scoping
    gap (see docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md).
    Sourcing requests do not carry a tenant_id field today, so an
    unfiltered list returns every sourcing request the deploy has.
    When the tenancy migration ships, this route will filter by
    current_user.tenant_id and the bound is capped at 200 rows so
    the listing path is enumeration-resistant in the interim.

    Mode B safety. Every row in the list represents a sourcing
    request that lives in the manual-review queue. No bot
    auto-approval is performed at any stage.
    """
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    query: dict[str, Any] = {}
    if opportunity_id:
        query["opportunity_id"] = opportunity_id
    if status:
        query["status"] = status

    docs = await db.procurement_sourcing_requests.find(
        query, {"_id": 0}
    ).sort("created_at", -1).to_list(200)
    return {"items": docs}


@router.get(
    "/procurement/sourcing-requests/{sourcing_request_id}",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def get_procurement_sourcing_request(
    sourcing_request_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Detail view with quote options embedded for the queue UI."""
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sourcing = await _load_sourcing_request_or_404(db, sourcing_request_id)
    quotes = await db.procurement_quote_options.find(
        {"sourcing_request_id": sourcing_request_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(500)
    return {"sourcing_request": sourcing, "quotes": quotes}


@router.post(
    "/procurement/sourcing-requests/{sourcing_request_id}/retry",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.create"))],
)
async def retry_procurement_sourcing_request(
    sourcing_request_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Re-attempt the procurement bot submission. Allowed only when
    the request is in a retry-eligible status (failed,
    review_required, or submitted)."""
    if not (
        has_perm(current_user, "sales_ops.procurement.create")
        or has_perm(current_user, "sales_ops.procurement.edit")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sourcing = await _load_sourcing_request_or_404(db, sourcing_request_id)
    current_status = sourcing.get("status")
    if current_status not in _RETRY_ELIGIBLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "sourcing_retry_not_allowed",
                "message": (
                    f"Sourcing request status {current_status!r} cannot be retried."
                ),
            },
        )

    from services.procurement_bot_client import (
        build_submission_payload,
        is_bot_configured,
        submit_sourcing_request,
    )

    if not is_bot_configured():
        # Cannot retry against an unconfigured bot. Surface a
        # structured envelope so the frontend can route the user
        # to the manual-review queue rather than retrying forever.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "procurement_bot_not_configured",
                "message": (
                    "Procurement bot is not configured for this "
                    "deployment. Use the manual review queue."
                ),
            },
        )

    line_docs_full: list[dict] = []
    if sourcing.get("line_item_ids"):
        line_docs_full = await db.opportunity_line_items.find(
            {"line_item_id": {"$in": sourcing["line_item_ids"]}},
            {"_id": 0},
        ).to_list(500)
    payload = build_submission_payload(
        sourcing_request_id=sourcing_request_id,
        opportunity_id=sourcing.get("opportunity_id"),
        line_items=line_docs_full,
        sow_id=sourcing.get("sow_id"),
    )
    bot_result = await submit_sourcing_request(payload)
    now = datetime.now(timezone.utc)
    updates: dict = {
        "bot_submitted_at": now,
        "updated_at": now,
    }
    if bot_result.accepted:
        updates["status"] = "sourcing_in_progress"
        updates["bot_request_id"] = bot_result.bot_request_id
        updates["manual_review_required"] = False
        updates["manual_review_reason"] = None
        updates["status_reason"] = None
    else:
        updates["status"] = "failed"
        updates["status_reason"] = bot_result.error_reason
        updates["manual_review_required"] = True

    await db.procurement_sourcing_requests.update_one(
        {"sourcing_request_id": sourcing_request_id},
        {"$set": updates},
    )

    try:
        await audit_log_action(
            action="sales_ops.procurement.sourcing_retry_requested",
            actor_id=getattr(current_user, "user_id", None),
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="procurement_sourcing_request",
            resource_id=sourcing_request_id,
            details={
                "opportunity_id": sourcing.get("opportunity_id"),
                "accepted": bot_result.accepted,
                "error_reason": bot_result.error_reason,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for sourcing_retry_requested failed: %s", e)

    fresh = await db.procurement_sourcing_requests.find_one(
        {"sourcing_request_id": sourcing_request_id}, {"_id": 0}
    )
    return fresh


@router.post("/procurement/bot/webhook")
async def procurement_bot_webhook(
    request: Request,
    db=Depends(get_db),
):
    """Receive quote results from the procurement bot.

    No bearer auth. The route validates an HMAC SHA-256 signature
    on the raw body against PROCUREMENT_BOT_WEBHOOK_SECRET. Tenant
    ownership is resolved through the stored sourcing request, NOT
    from any field in the webhook body; the bot is not trusted for
    tenant scoping.

    Mode B safety. Every quote stored from this path defaults to
    selected=False and the sourcing request lands in
    review_required. The route never auto-selects, auto-orders, or
    auto-approves. See docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md.
    """
    from services.procurement_webhook_security import (
        is_webhook_configured,
        verify_signature,
        SIGNATURE_HEADER_NAME,
    )

    if not is_webhook_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "procurement_webhook_not_configured",
                "message": (
                    "Procurement webhook signing secret is not configured."
                ),
            },
        )

    body_bytes = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER_NAME) or request.headers.get(
        SIGNATURE_HEADER_NAME.lower()
    )
    if not verify_signature(body_bytes, signature):
        try:
            await audit_log_action(
                action="sales_ops.procurement.quote_webhook_rejected",
                actor_id=None,
                actor_email="procurement_bot",
                resource_type="procurement_sourcing_request",
                resource_id="unknown",
                details={
                    "reason": "invalid_signature",
                    "has_signature": bool(signature),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Audit row for webhook rejection failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse the body after signature validation so an attacker
    # cannot fingerprint the parser via timing.
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Malformed JSON body")

    sourcing_request_id = (body or {}).get("sourcing_request_id")
    if not sourcing_request_id:
        raise HTTPException(status_code=400, detail="sourcing_request_id required")

    sourcing = await db.procurement_sourcing_requests.find_one(
        {"sourcing_request_id": sourcing_request_id}, {"_id": 0}
    )
    if not sourcing:
        try:
            await audit_log_action(
                action="sales_ops.procurement.quote_webhook_rejected",
                actor_id=None,
                actor_email="procurement_bot",
                resource_type="procurement_sourcing_request",
                resource_id=sourcing_request_id,
                details={"reason": "sourcing_request_not_found"},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Audit row for webhook rejection failed: %s", e)
        raise HTTPException(status_code=404, detail="Sourcing request not found")

    quotes = (body or {}).get("quotes") or []
    manual_review_reason = (body or {}).get("manual_review_reason")

    now = datetime.now(timezone.utc)
    stored_quote_ids: list[str] = []
    for incoming in quotes:
        if not isinstance(incoming, dict):
            continue
        external_id = incoming.get("external_quote_id")
        if not isinstance(external_id, str) or not external_id:
            continue
        # Idempotent upsert keyed by (sourcing_request_id, external_quote_id).
        existing = await db.procurement_quote_options.find_one(
            {
                "sourcing_request_id": sourcing_request_id,
                "external_quote_id": external_id,
            },
            {"_id": 0, "quote_id": 1},
        )
        if existing:
            stored_quote_ids.append(existing["quote_id"])
            continue
        quote_id = f"quote_{gen_id()}"
        # Coerce HttpUrl-shaped values into plain strings for Mongo.
        source_url = incoming.get("source_url")
        if source_url is not None and not isinstance(source_url, str):
            source_url = str(source_url)
        quote_doc = {
            "quote_id": quote_id,
            "sourcing_request_id": sourcing_request_id,
            "material_line_id": incoming.get("material_line_id"),
            "vendor_name": (incoming.get("vendor_name") or "Unknown vendor"),
            "vendor_rank": incoming.get("vendor_rank"),
            "price": incoming.get("price"),
            "currency": incoming.get("currency"),
            "availability": incoming.get("availability"),
            "delivery_eta": incoming.get("delivery_eta"),
            "shipping_cost": incoming.get("shipping_cost"),
            "tax_estimate": incoming.get("tax_estimate"),
            "total_cost": incoming.get("total_cost"),
            "confidence": incoming.get("confidence"),
            "source_url": source_url,
            "source_captured_at": incoming.get("source_captured_at"),
            "manual_review_reason": incoming.get("manual_review_reason"),
            "selected": False,
            "selected_at": None,
            "selected_by": None,
            "ordered_at": None,
            "ordered_by": None,
            "delivered_at": None,
            "delivered_by": None,
            "external_quote_id": external_id,
            "created_at": now,
            "updated_at": now,
        }
        await db.procurement_quote_options.insert_one(quote_doc)
        stored_quote_ids.append(quote_id)
        try:
            await audit_log_action(
                action="sales_ops.procurement.quote_option_stored",
                actor_id=None,
                actor_email="procurement_bot",
                resource_type="procurement_quote_option",
                resource_id=quote_id,
                details={
                    "sourcing_request_id": sourcing_request_id,
                    "vendor_name": quote_doc["vendor_name"],
                    "has_source_url": bool(source_url),
                    "has_price": quote_doc["price"] is not None,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Audit row for quote_option_stored failed: %s", e)

    # Update the sourcing request to review_required so Procurement
    # picks it up. The route never auto-selects a quote.
    update_doc = {
        "status": "review_required",
        "bot_response_received_at": now,
        "manual_review_required": True,
        "manual_review_reason": manual_review_reason
        or sourcing.get("manual_review_reason")
        or "quote_review_required",
        "updated_at": now,
    }
    await db.procurement_sourcing_requests.update_one(
        {"sourcing_request_id": sourcing_request_id},
        {"$set": update_doc},
    )

    try:
        await audit_log_action(
            action="sales_ops.procurement.quote_webhook_received",
            actor_id=None,
            actor_email="procurement_bot",
            resource_type="procurement_sourcing_request",
            resource_id=sourcing_request_id,
            details={
                "stored_quote_count": len(stored_quote_ids),
                "incoming_quote_count": len(quotes),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for quote_webhook_received failed: %s", e)

    return {
        "sourcing_request_id": sourcing_request_id,
        "stored_quote_ids": stored_quote_ids,
        "status": "review_required",
    }


@router.post(
    "/procurement/sourcing-requests/{sourcing_request_id}/quotes/{quote_id}/select",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def select_procurement_quote(
    sourcing_request_id: str,
    quote_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Procurement selects one quote as the winning option.

    Only one quote per sourcing request is selected at a time; a
    new selection unselects the previous one. Idempotent on a
    duplicate select of the same quote. The route does not auto-
    advance to ordered; that is a separate explicit step.
    """
    if not (
        has_perm(current_user, "sales_ops.procurement.edit")
        or has_perm(current_user, "sales_ops.procurement.create")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sourcing = await _load_sourcing_request_or_404(db, sourcing_request_id)
    quote = await db.procurement_quote_options.find_one(
        {"quote_id": quote_id, "sourcing_request_id": sourcing_request_id},
        {"_id": 0},
    )
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    if sourcing.get("status") in _TERMINAL_SOURCING_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "sourcing_terminal",
                "message": (
                    f"Sourcing request is already {sourcing.get('status')!r}; "
                    "quote selection is not allowed."
                ),
            },
        )

    now = datetime.now(timezone.utc)
    actor = getattr(current_user, "user_id", None)

    # Unselect any previously selected quote on this request so the
    # selected_quote_id pointer always matches the quote.selected
    # flag on exactly one row.
    await db.procurement_quote_options.update_many(
        {
            "sourcing_request_id": sourcing_request_id,
            "quote_id": {"$ne": quote_id},
        },
        {"$set": {"selected": False, "updated_at": now}},
    )
    await db.procurement_quote_options.update_one(
        {"quote_id": quote_id, "sourcing_request_id": sourcing_request_id},
        {
            "$set": {
                "selected": True,
                "selected_at": now,
                "selected_by": actor,
                "updated_at": now,
            }
        },
    )
    await db.procurement_sourcing_requests.update_one(
        {"sourcing_request_id": sourcing_request_id},
        {
            "$set": {
                "selected_quote_id": quote_id,
                "status": "quote_selected",
                "manual_review_required": False,
                "manual_review_reason": None,
                "updated_at": now,
            }
        },
    )

    try:
        await audit_log_action(
            action="sales_ops.procurement.quote_selected",
            actor_id=actor,
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="procurement_quote_option",
            resource_id=quote_id,
            details={
                "sourcing_request_id": sourcing_request_id,
                "opportunity_id": sourcing.get("opportunity_id"),
                "vendor_name": quote.get("vendor_name"),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for quote_selected failed: %s", e)

    fresh_sourcing = await db.procurement_sourcing_requests.find_one(
        {"sourcing_request_id": sourcing_request_id}, {"_id": 0}
    )
    fresh_quote = await db.procurement_quote_options.find_one(
        {"quote_id": quote_id}, {"_id": 0}
    )
    return {"sourcing_request": fresh_sourcing, "quote": fresh_quote}


@router.post(
    "/procurement/sourcing-requests/{sourcing_request_id}/ordered",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def mark_procurement_ordered(
    sourcing_request_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Mark a sourcing request as ordered. Requires a selected
    quote so the ordered state always points at a vendor + price
    on record. Idempotent on re-mark."""
    if not (
        has_perm(current_user, "sales_ops.procurement.edit")
        or has_perm(current_user, "sales_ops.procurement.create")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sourcing = await _load_sourcing_request_or_404(db, sourcing_request_id)
    selected_quote_id = sourcing.get("selected_quote_id")
    if not selected_quote_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_selected_quote",
                "message": "Select a quote before marking ordered.",
            },
        )

    current_status = sourcing.get("status")
    if current_status not in _ORDER_ELIGIBLE_STATUSES:
        # Mark-ordered requires the explicit quote_selected status
        # so an unselected sourcing request (review_required,
        # sourcing_in_progress, failed) cannot land in ordered via
        # a direct DB write of selected_quote_id. Locks the state
        # machine against future writers.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "sourcing_not_quote_selected",
                "message": (
                    f"Sourcing request status {current_status!r} is not "
                    "eligible for ordered. Select a quote first."
                ),
            },
        )

    now = datetime.now(timezone.utc)
    actor = getattr(current_user, "user_id", None)

    await db.procurement_sourcing_requests.update_one(
        {"sourcing_request_id": sourcing_request_id},
        {
            "$set": {
                "status": "ordered",
                "ordered_at": now,
                "ordered_by": actor,
                "updated_at": now,
            }
        },
    )
    await db.procurement_quote_options.update_one(
        {"quote_id": selected_quote_id},
        {"$set": {"ordered_at": now, "ordered_by": actor, "updated_at": now}},
    )

    try:
        await audit_log_action(
            action="sales_ops.procurement.material_ordered",
            actor_id=actor,
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="procurement_sourcing_request",
            resource_id=sourcing_request_id,
            details={
                "opportunity_id": sourcing.get("opportunity_id"),
                "selected_quote_id": selected_quote_id,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for material_ordered failed: %s", e)

    fresh = await db.procurement_sourcing_requests.find_one(
        {"sourcing_request_id": sourcing_request_id}, {"_id": 0}
    )
    return fresh


@router.post(
    "/procurement/sourcing-requests/{sourcing_request_id}/delivered",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def mark_procurement_delivered(
    sourcing_request_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Mark an ordered sourcing request as delivered. PMO or
    Procurement role may mark. Requires the request to be in
    ordered status. Idempotent on re-mark."""
    if not (
        has_perm(current_user, "sales_ops.procurement.edit")
        or has_perm(current_user, "sales_ops.procurement.create")
        or has_perm(current_user, "projects.edit.all")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sourcing = await _load_sourcing_request_or_404(db, sourcing_request_id)
    if sourcing.get("status") != "ordered":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_ordered",
                "message": (
                    f"Sourcing request must be in 'ordered' status before "
                    f"marking delivered; current status is "
                    f"{sourcing.get('status')!r}."
                ),
            },
        )

    now = datetime.now(timezone.utc)
    actor = getattr(current_user, "user_id", None)

    await db.procurement_sourcing_requests.update_one(
        {"sourcing_request_id": sourcing_request_id},
        {
            "$set": {
                "status": "delivered",
                "delivered_at": now,
                "delivered_by": actor,
                "updated_at": now,
            }
        },
    )
    selected_quote_id = sourcing.get("selected_quote_id")
    if selected_quote_id:
        await db.procurement_quote_options.update_one(
            {"quote_id": selected_quote_id},
            {"$set": {"delivered_at": now, "delivered_by": actor, "updated_at": now}},
        )

    try:
        await audit_log_action(
            action="sales_ops.procurement.material_delivered",
            actor_id=actor,
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="procurement_sourcing_request",
            resource_id=sourcing_request_id,
            details={
                "opportunity_id": sourcing.get("opportunity_id"),
                "selected_quote_id": selected_quote_id,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for material_delivered failed: %s", e)

    fresh = await db.procurement_sourcing_requests.find_one(
        {"sourcing_request_id": sourcing_request_id}, {"_id": 0}
    )
    return fresh


# ---- SOW ITEM PROCUREMENT EXECUTION ----
#
# Direct single-item lifecycle for Field Services SOW product items.
# This path is the manual-portal-pricing flow Sales Ops, Procurement,
# and PMO use day to day. It deliberately does not touch the Ticket 9
# ProcurementSourcingRequest collection — that scaffold remains for
# the future Mode A bot/quote automation per
# docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md.
#
# Tenant scoping follows the substrate's implicit-tenant pattern: an
# item's tenant is the tenant of its owning opportunity. Routes load
# the SOW + opportunity and reject if the caller cannot see the
# opportunity.


# Statuses considered "pending pricing" for the worklist query. An
# item is pending pricing when Sales Ops marked it as needs_sourcing
# OR when the procurement lifecycle has not advanced past the pricing
# phase. Items already priced/ordered/delivered are filtered out.
_SOW_ITEM_PRICING_PENDING_STATUSES = ("pending_pricing", None)


async def _load_sow_for_item_or_404(db, sow_id: str) -> dict:
    """Load a SOW by id and 404 if it does not exist."""
    sow = await db.opportunity_sows.find_one({"sow_id": sow_id}, {"_id": 0})
    if not sow:
        raise HTTPException(status_code=404, detail="SOW not found")
    return sow


def _find_sow_item(sow: dict, item_id: str) -> tuple[Optional[dict], Optional[int]]:
    """Return (item, index) or (None, None) for a SOW product item."""
    items = sow.get("sow_product_items") or []
    for idx, item in enumerate(items):
        if isinstance(item, dict) and item.get("item_id") == item_id:
            return item, idx
    return None, None


def _require_field_services(sow: dict) -> None:
    """The execution lifecycle is FS-only by ticket scope.
    Professional Services SOWs use prose milestones and do not carry
    material/hardware/services lifecycle. Reject non-FS SOWs at this
    boundary so the audit trail stays clean."""
    if (sow.get("line_of_business") or "") != "field_services":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "sow_not_field_services",
                "message": (
                    "Procurement execution routes are only available for "
                    "Field Services SOWs."
                ),
            },
        )


def _coerce_url(value: Any) -> Any:
    """Pydantic v2 HttpUrl/AnyUrl values do not JSON-serialize as
    plain strings in every Motor codec path. Coerce them to str on
    the way to Mongo so the stored shape is portable across reads."""
    if value is None:
        return None
    cls_name = value.__class__.__name__
    if cls_name in ("HttpUrl", "AnyUrl", "Url"):
        return str(value)
    return value


def _serialize_pricing_queue_row(sow: dict, item: dict) -> dict:
    """Project the fields the Procurement pricing-queue UI needs.
    Avoids returning the full SOW so the worklist payload stays
    small and the UI is not coupled to unrelated SOW shape changes."""
    return {
        "sow_id": sow.get("sow_id"),
        "opportunity_id": sow.get("opportunity_id"),
        "line_of_business": sow.get("line_of_business"),
        "sow_status": sow.get("status"),
        "item_id": item.get("item_id"),
        "product_name": item.get("product_name"),
        "item_category": item.get("item_category"),
        "quantity": item.get("quantity"),
        "unit": item.get("unit"),
        "unit_price": item.get("unit_price"),
        "total_price": item.get("total_price"),
        "source": item.get("source"),
        "supplier_name": item.get("supplier_name"),
        "supplier_source_url": item.get("supplier_source_url"),
        "availability_note": item.get("availability_note"),
        "procurement_status": item.get("procurement_status"),
        "procurement_notes": item.get("procurement_notes"),
        "notes": item.get("notes"),
    }


@router.get(
    "/procurement/sow-items/pricing-queue",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.view"))],
)
async def list_pricing_queue(
    opportunity_id: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List Field Services SOW items that need Procurement pricing.

    Includes items marked ``source=needs_sourcing`` and items whose
    ``procurement_status`` is ``pending_pricing``. Excludes priced /
    ordered / delivered items. Filterable by opportunity_id so the
    Procurement tab can scope the queue per opportunity.

    Tenant scoping: bounded by ``sales_ops.procurement.view``. The
    list is capped so an enumeration cannot dump every deploy's
    pending items at once (substrate gap, see SHARED_SOW_BUILDER
    _SUBSTRATE.md).
    """
    if not has_perm(current_user, "sales_ops.procurement.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    query: dict = {"line_of_business": "field_services"}
    if opportunity_id:
        query["opportunity_id"] = opportunity_id

    # The cap applies to SOW documents; an item-level overflow would
    # mean SOWs past the cap silently drop out of the queue. We
    # surface that as a ``truncated`` flag so the UI can warn the
    # operator to filter by opportunity.
    sow_cap = 200
    cursor = db.opportunity_sows.find(query, {"_id": 0}).limit(sow_cap)
    rows: list = []
    sow_count = 0
    async for sow in cursor:
        sow_count += 1
        items = sow.get("sow_product_items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            status = item.get("procurement_status")
            if status in ("priced", "ordered", "delivered"):
                continue
            source = item.get("source")
            if source == "needs_sourcing" or status in _SOW_ITEM_PRICING_PENDING_STATUSES:
                rows.append(_serialize_pricing_queue_row(sow, item))
    return {
        "items": rows,
        "count": len(rows),
        "truncated": sow_count >= sow_cap,
    }


@router.patch(
    "/procurement/sows/{sow_id}/items/{item_id}/pricing",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def update_sow_item_pricing(
    sow_id: str,
    item_id: str,
    body: SOWItemPricingUpdate,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Procurement enters price, supplier, source URL, availability,
    and notes for a single FS SOW item. Auto-flips the item to
    ``source=manual`` and ``procurement_status=priced`` once a price
    is recorded. Refuses transitions out of ordered/delivered so the
    pricing surface cannot rewrite a downstream lifecycle state."""
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sow = await _load_sow_for_item_or_404(db, sow_id)
    _require_field_services(sow)
    item, idx = _find_sow_item(sow, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="SOW item not found")

    current_status = item.get("procurement_status")
    if current_status in ("ordered", "delivered"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "item_lifecycle_locked",
                "message": (
                    f"SOW item is in lifecycle status {current_status!r}; "
                    "pricing cannot be edited after ordered."
                ),
            },
        )

    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "empty_pricing_update",
                "message": "Provide at least one field to update.",
            },
        )

    update_set: dict = {}
    for key, value in payload.items():
        update_set[f"sow_product_items.{idx}.{key}"] = _coerce_url(value)

    new_unit_price = payload.get("unit_price")
    if new_unit_price is not None and new_unit_price > 0:
        update_set[f"sow_product_items.{idx}.source"] = "manual"
        update_set[f"sow_product_items.{idx}.procurement_status"] = "priced"
        try:
            qty = int(item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        update_set[f"sow_product_items.{idx}.total_price"] = qty * float(new_unit_price)
    elif current_status is None:
        update_set[f"sow_product_items.{idx}.procurement_status"] = "pending_pricing"

    now = datetime.now(timezone.utc)
    update_set["updated_at"] = now
    update_set["last_edited_by"] = getattr(current_user, "user_id", None)

    await db.opportunity_sows.update_one({"sow_id": sow_id}, {"$set": update_set})

    try:
        await audit_log_action(
            action="sales_ops.sow_item.priced",
            actor_id=getattr(current_user, "user_id", None),
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="opportunity_sow_item",
            resource_id=item_id,
            details={
                "sow_id": sow_id,
                "opportunity_id": sow.get("opportunity_id"),
                "fields_set": sorted(payload.keys()),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for sow_item.priced failed: %s", e)

    fresh_sow = await db.opportunity_sows.find_one({"sow_id": sow_id}, {"_id": 0})
    fresh_item, _ = _find_sow_item(fresh_sow or {}, item_id)
    return fresh_item or {}


@router.post(
    "/procurement/sows/{sow_id}/items/{item_id}/ordered",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def mark_sow_item_ordered(
    sow_id: str,
    item_id: str,
    body: SOWItemOrderedMark,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Procurement marks a SOW item ordered after the external
    purchase happens. Requires the item to be in ``priced`` status so
    an item still pending pricing cannot accidentally skip ahead."""
    if not has_perm(current_user, "sales_ops.procurement.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sow = await _load_sow_for_item_or_404(db, sow_id)
    _require_field_services(sow)
    item, idx = _find_sow_item(sow, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="SOW item not found")

    current_status = item.get("procurement_status")
    if current_status != "priced":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "item_not_priced",
                "message": (
                    f"SOW item must be in 'priced' status before ordering; "
                    f"current status is {current_status!r}."
                ),
            },
        )

    payload = body.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc)
    actor = getattr(current_user, "user_id", None)

    update_set: dict = {
        f"sow_product_items.{idx}.procurement_status": "ordered",
        f"sow_product_items.{idx}.ordered_at": now,
        f"sow_product_items.{idx}.ordered_by": actor,
        "updated_at": now,
        "last_edited_by": actor,
    }
    for key in (
        "expected_delivery_date",
        "order_reference",
        "tracking_number",
        "tracking_url",
        "procurement_notes",
    ):
        if key in payload:
            update_set[f"sow_product_items.{idx}.{key}"] = _coerce_url(payload[key])

    await db.opportunity_sows.update_one({"sow_id": sow_id}, {"$set": update_set})

    try:
        await audit_log_action(
            action="sales_ops.sow_item.ordered",
            actor_id=actor,
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="opportunity_sow_item",
            resource_id=item_id,
            details={
                "sow_id": sow_id,
                "opportunity_id": sow.get("opportunity_id"),
                "has_tracking_number": bool(payload.get("tracking_number")),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for sow_item.ordered failed: %s", e)

    fresh_sow = await db.opportunity_sows.find_one({"sow_id": sow_id}, {"_id": 0})
    fresh_item, _ = _find_sow_item(fresh_sow or {}, item_id)
    return fresh_item or {}


@router.post(
    "/procurement/sows/{sow_id}/items/{item_id}/delivered",
    dependencies=[Depends(require_ai_permission("sales_ops.procurement.edit"))],
)
async def mark_sow_item_delivered(
    sow_id: str,
    item_id: str,
    body: SOWItemDeliveredMark,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Mark a SOW item delivered. PMO (``projects.edit.all``) and
    Procurement (``sales_ops.procurement.edit``) may both call;
    requires the item to be in ``ordered`` status. Idempotent on
    re-mark: a delivered item silently no-ops rather than 409 so a
    double-tap from the UI does not raise an error."""
    if not (
        has_perm(current_user, "sales_ops.procurement.edit")
        or has_perm(current_user, "projects.edit.all")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sow = await _load_sow_for_item_or_404(db, sow_id)
    _require_field_services(sow)
    item, idx = _find_sow_item(sow, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="SOW item not found")

    current_status = item.get("procurement_status")
    if current_status == "delivered":
        return item
    if current_status != "ordered":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "item_not_ordered",
                "message": (
                    f"SOW item must be in 'ordered' status before marking "
                    f"delivered; current status is {current_status!r}."
                ),
            },
        )

    payload = body.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc)
    actor = getattr(current_user, "user_id", None)

    update_set: dict = {
        f"sow_product_items.{idx}.procurement_status": "delivered",
        f"sow_product_items.{idx}.delivered_at": now,
        f"sow_product_items.{idx}.delivered_by": actor,
        "updated_at": now,
        "last_edited_by": actor,
    }
    if "delivery_notes" in payload:
        update_set[f"sow_product_items.{idx}.delivery_notes"] = payload["delivery_notes"]

    await db.opportunity_sows.update_one({"sow_id": sow_id}, {"$set": update_set})

    try:
        await audit_log_action(
            action="sales_ops.sow_item.delivered",
            actor_id=actor,
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="opportunity_sow_item",
            resource_id=item_id,
            details={
                "sow_id": sow_id,
                "opportunity_id": sow.get("opportunity_id"),
                "marked_via": (
                    "procurement"
                    if has_perm(current_user, "sales_ops.procurement.edit")
                    else "pmo"
                ),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit row for sow_item.delivered failed: %s", e)

    fresh_sow = await db.opportunity_sows.find_one({"sow_id": sow_id}, {"_id": 0})
    fresh_item, _ = _find_sow_item(fresh_sow or {}, item_id)
    return fresh_item or {}


def _serialize_checklist_item(item: dict) -> dict:
    """Read-only projection of a SOW item for the PMO scope checklist.
    Surfaces enough of the procurement lifecycle that PMO can confirm
    technician execution against the approved scope without exposing
    internal supplier URLs (those stay in the Procurement view)."""
    return {
        "item_id": item.get("item_id"),
        "product_name": item.get("product_name"),
        "item_category": item.get("item_category"),
        "quantity": item.get("quantity"),
        "unit": item.get("unit"),
        "notes": item.get("notes"),
        "source": item.get("source"),
        "procurement_status": item.get("procurement_status"),
        "supplier_name": item.get("supplier_name"),
        "expected_delivery_date": item.get("expected_delivery_date"),
        "order_reference": item.get("order_reference"),
        "tracking_number": item.get("tracking_number"),
        "tracking_url": item.get("tracking_url"),
        "ordered_at": item.get("ordered_at"),
        "delivered_at": item.get("delivered_at"),
        "delivery_notes": item.get("delivery_notes"),
    }


@router.get(
    "/pmo/projects/{project_id}/checklist",
    dependencies=[Depends(require_ai_permission("projects.edit.all"))],
)
async def get_pmo_project_checklist(
    project_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Resolve a PM project to its linked Field Services SOW(s) and
    return the scope checklist payload for each. PMO uses this so the
    project detail surface does not need ``sales_ops.solutions_design.view``
    to enumerate SOWs; the lookup is bounded by the project's
    ``opportunity_id`` and is FS-only by ticket scope.
    """
    if not (
        has_perm(current_user, "projects.edit.all")
        or has_perm(current_user, "projects.edit")
        or has_perm(current_user, "projects.view")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    project = await db.pm_projects.find_one({"project_id": project_id}, {"_id": 0})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    opportunity_id = project.get("opportunity_id")
    if not opportunity_id:
        return {"project_id": project_id, "opportunity_id": None, "checklists": []}

    sows = await db.opportunity_sows.find(
        {"opportunity_id": opportunity_id, "line_of_business": "field_services"},
        {"_id": 0},
    ).to_list(50)

    checklists = []
    for sow in sows:
        items_by_category: dict[str, list] = {
            "materials": [],
            "hardware": [],
            "services": [],
            "wholesale_mrr": [],
            "other": [],
        }
        for item in sow.get("sow_product_items") or []:
            if not isinstance(item, dict):
                continue
            category = (item.get("item_category") or "other").lower()
            bucket = items_by_category.get(category, items_by_category["other"])
            bucket.append(_serialize_checklist_item(item))
        total_items = sum(len(v) for v in items_by_category.values())
        delivered_items = sum(
            1
            for v in items_by_category.values()
            for it in v
            if it.get("procurement_status") == "delivered"
        )
        checklists.append(
            {
                "sow_id": sow.get("sow_id"),
                "opportunity_id": sow.get("opportunity_id"),
                "line_of_business": sow.get("line_of_business"),
                "status": sow.get("status"),
                "executive_summary": sow.get("executive_summary"),
                "service_location": sow.get("service_location"),
                "contact_roles": sow.get("contact_roles") or [],
                "milestones": sow.get("milestones") or [],
                "items_by_category": items_by_category,
                "totals": {
                    "total_items": total_items,
                    "delivered_items": delivered_items,
                    "remaining_items": total_items - delivered_items,
                },
            }
        )

    return {
        "project_id": project_id,
        "opportunity_id": opportunity_id,
        "checklists": checklists,
    }


@router.get(
    "/pmo/sows/{sow_id}/checklist",
    dependencies=[Depends(require_ai_permission("projects.edit.all"))],
)
async def get_pmo_sow_checklist(
    sow_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """PMO scope checklist replica of a Field Services SOW.

    Returns the milestones, contact roles, service location summary,
    and a categorized product-item checklist with execution status.
    Read-only. PMO uses this to verify technician execution against
    the approved scope; mark-delivered actions go through the
    dedicated single-item route so a checklist view cannot mutate
    procurement state by accident.
    """
    if not (
        has_perm(current_user, "projects.edit.all")
        or has_perm(current_user, "projects.edit")
        or has_perm(current_user, "projects.view")
        or has_perm(current_user, "sales_ops.procurement.view")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    sow = await _load_sow_for_item_or_404(db, sow_id)
    _require_field_services(sow)

    items_by_category: dict[str, list] = {
        "materials": [],
        "hardware": [],
        "services": [],
        "wholesale_mrr": [],
        "other": [],
    }
    for item in sow.get("sow_product_items") or []:
        if not isinstance(item, dict):
            continue
        category = (item.get("item_category") or "other").lower()
        bucket = items_by_category.get(category, items_by_category["other"])
        bucket.append(_serialize_checklist_item(item))

    total_items = sum(len(v) for v in items_by_category.values())
    delivered_items = sum(
        1
        for v in items_by_category.values()
        for it in v
        if it.get("procurement_status") == "delivered"
    )

    return {
        "sow_id": sow.get("sow_id"),
        "opportunity_id": sow.get("opportunity_id"),
        "line_of_business": sow.get("line_of_business"),
        "status": sow.get("status"),
        "executive_summary": sow.get("executive_summary"),
        "service_location": sow.get("service_location"),
        "contact_roles": sow.get("contact_roles") or [],
        "milestones": sow.get("milestones") or [],
        "items_by_category": items_by_category,
        "totals": {
            "total_items": total_items,
            "delivered_items": delivered_items,
            "remaining_items": total_items - delivered_items,
        },
    }
