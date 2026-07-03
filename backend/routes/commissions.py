"""
Commission Management Routes

Three-tier commission structure:
- Tier 1: Top Line Revenue Commission (% of revenue)
- Tier 2: Director Override Commission (% of revenue - cost - tier1)
- Tier 3: Sales Rep Commission (% of tier2_base - tier2_commission)

Each tier supports multiple users with customizable splits.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Literal
from datetime import datetime, timezone
from uuid import uuid4

from auth_helper import get_current_user, has_perm, get_commission_filter
from database import get_db
from services.event_bus import emit_event
from services.field_registry import register_fields, register_module_events, register_module_config
from services.response_filter import filter_response_fields

router = APIRouter()

register_module_config("commissions", "commissions", "commission_id", "CRM & Sales")
register_module_events("commissions", "commission", ["created", "updated"])
register_fields("commissions", "commission", [
    {"name": "revenue", "type": "number", "label": "Revenue", "description": "Total deal revenue the commission is based on"},
    {"name": "cost_type", "type": "string", "label": "Cost Type", "description": "Cost calculation method (actual, percentage)"},
    {"name": "cost_value", "type": "number", "label": "Cost Value", "description": "Cost amount or percentage used in margin calculation"},
    {"name": "status", "type": "string", "label": "Status", "description": "Commission plan status (pending, active, completed, cancelled)"},
    {"name": "deal_id", "type": "string", "label": "Deal", "description": "Associated deal this commission plan is for"},
])


# ============ MODELS ============

class CommissionUser(BaseModel):
    user_id: str
    user_name: Optional[str] = None
    commission_amount: float = 0  # Calculated by backend
    split_percentage: float  # Custom split %


class CommissionTier(BaseModel):
    tier_number: int
    tier_name: str
    percentage: float
    base_amount: float = 0  # Calculated by backend
    commission_total: float = 0  # Calculated by backend
    users: List[CommissionUser]


class CommissionTotals(BaseModel):
    tier1_commission: float
    tier2_commission: float
    tier3_commission: float
    total_commission: float
    cost: float
    revenue: float
    profit_after_commission: float
    profit_margin: float
    profit_percentage: float


class CommissionPlanCreate(BaseModel):
    deal_id: str
    template_id: Optional[str] = None
    revenue: float
    cost_type: Literal["actual", "percentage"]
    cost_value: float
    tiers: List[CommissionTier]


class CommissionTemplateCreate(BaseModel):
    name: str
    tier1_percentage: float
    tier2_percentage: float
    tier3_percentage: float
    cost_type: Literal["actual", "percentage"]
    cost_default: float
    is_active: bool = True
    # Default users for each tier (can be user_id or dynamic variable like {deal_owner}, {agent_of_record})
    tier1_default_user: Optional[str] = None
    tier2_default_user: Optional[str] = None
    tier3_default_user: Optional[str] = None


# ============ CALCULATION ENGINE ============

def calculate_commission_plan(revenue: float, cost_type: str, cost_value: float, tiers: List[dict]) -> dict:
    """
    Calculate commission for all tiers and return totals.
    
    Tier 1: % of revenue
    Tier 2: % of (revenue - cost - tier1)
    Tier 3: % of (tier2_base - tier2_commission)
    """
    
    # Calculate actual cost
    if cost_type == "percentage":
        actual_cost = revenue * (cost_value / 100)
    else:
        actual_cost = cost_value
    
    # Tier 1 calculations
    tier1 = next((t for t in tiers if t.get("tier_number") == 1), None)
    tier1_commission = 0
    if tier1:
        tier1_commission = revenue * (tier1.get("percentage", 0) / 100)
        tier1["base_amount"] = revenue
        tier1["commission_total"] = tier1_commission
        
        # Calculate user splits
        total_split = sum(u.get("split_percentage", 0) for u in tier1.get("users", []))
        if total_split > 0:
            for user in tier1.get("users", []):
                user["commission_amount"] = tier1_commission * (user.get("split_percentage", 0) / total_split)
    
    # Tier 2 calculations
    tier2_base = revenue - actual_cost - tier1_commission
    tier2 = next((t for t in tiers if t.get("tier_number") == 2), None)
    tier2_commission = 0
    if tier2:
        tier2_commission = tier2_base * (tier2.get("percentage", 0) / 100)
        tier2["base_amount"] = tier2_base
        tier2["commission_total"] = tier2_commission
        
        # Calculate user splits
        total_split = sum(u.get("split_percentage", 0) for u in tier2.get("users", []))
        if total_split > 0:
            for user in tier2.get("users", []):
                user["commission_amount"] = tier2_commission * (user.get("split_percentage", 0) / total_split)
    
    # Tier 3 calculations
    tier3_base = tier2_base - tier2_commission
    tier3 = next((t for t in tiers if t.get("tier_number") == 3), None)
    tier3_commission = 0
    if tier3:
        tier3_commission = tier3_base * (tier3.get("percentage", 0) / 100)
        tier3["base_amount"] = tier3_base
        tier3["commission_total"] = tier3_commission
        
        # Calculate user splits
        total_split = sum(u.get("split_percentage", 0) for u in tier3.get("users", []))
        if total_split > 0:
            for user in tier3.get("users", []):
                user["commission_amount"] = tier3_commission * (user.get("split_percentage", 0) / total_split)
    
    # Calculate totals
    total_commission = tier1_commission + tier2_commission + tier3_commission
    profit_after_commission = revenue - actual_cost - total_commission
    profit_percentage = (profit_after_commission / revenue * 100) if revenue > 0 else 0
    
    return {
        "tiers": tiers,
        "totals": {
            "tier1_commission": tier1_commission,
            "tier2_commission": tier2_commission,
            "tier3_commission": tier3_commission,
            "total_commission": total_commission,
            "cost": actual_cost,
            "revenue": revenue,
            "profit_after_commission": profit_after_commission,
            "profit_margin": round(profit_percentage, 2),
            "profit_percentage": round(profit_percentage, 2)
        }
    }


# ============ COMMISSION PLANS ============

@router.post("/plans")
async def create_commission_plan(data: CommissionPlanCreate, current_user=Depends(get_current_user)):
    """Create a new commission plan for a deal"""
    if not has_perm(current_user, "commissions.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    db = get_db()
    
    # Calculate commissions
    calculated = calculate_commission_plan(
        data.revenue,
        data.cost_type,
        data.cost_value,
        [t.model_dump() for t in data.tiers]
    )
    
    # Create commission plan
    commission_id = f"comm_{uuid4().hex[:12]}"
    commission_doc = {
        "commission_id": commission_id,
        "deal_id": data.deal_id,
        "template_id": data.template_id,
        "revenue": data.revenue,
        "cost_type": data.cost_type,
        "cost_value": data.cost_value,
        "tiers": calculated["tiers"],
        "totals": calculated["totals"],
        "status": "pending",
        "created_by": current_user.user_id,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.commissions.insert_one(commission_doc)
    commission_doc.pop("_id", None)
    await emit_event("commissions", "commission.created", record=commission_doc, user_id=current_user.user_id, user_email=current_user.email)

    # Update deal with commission_id
    await db.deals.update_one(
        {"deal_id": data.deal_id},
        {"$set": {"commission_id": commission_id, "commission_plan_id": commission_id}}
    )
    
    return {"commission_id": commission_id, "totals": calculated["totals"]}


@router.get("/plans")
async def get_commission_plans(current_user=Depends(get_current_user)):
    """Get all commission plans"""
    if not has_perm(current_user, "commissions.view.all") and not has_perm(current_user, "commissions.view.own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    # Apply RBAC filter so .view.own users only see their own commissions
    filt = get_commission_filter(current_user)
    plans = await db.commissions.find(filt, {"_id": 0}).sort("created_at", -1).to_list(1000)
    plans = filter_response_fields(plans, current_user, "commissions")
    return {"items": plans}


@router.get("/plans/{commission_id}")
async def get_commission_plan(commission_id: str, current_user=Depends(get_current_user)):
    """Get a specific commission plan"""
    if not has_perm(current_user, "commissions.view.all") and not has_perm(current_user, "commissions.view.own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    # Apply RBAC filter so .view.own users can only see their own plans
    rbac_filter = get_commission_filter(current_user)
    query = {"commission_id": commission_id, **rbac_filter}
    plan = await db.commissions.find_one(query, {"_id": 0})
    if not plan:
        raise HTTPException(status_code=404, detail="Commission plan not found")

    return plan


# ============ COMMISSION TEMPLATES ============

@router.post("/templates")
async def create_commission_template(data: CommissionTemplateCreate, current_user=Depends(get_current_user)):
    """Create a default commission template"""
    if not has_perm(current_user, "commissions.template.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    template_id = f"tpl_{uuid4().hex[:12]}"
    template_doc = {
        "template_id": template_id,
        "name": data.name,
        "tier1_percentage": data.tier1_percentage,
        "tier2_percentage": data.tier2_percentage,
        "tier3_percentage": data.tier3_percentage,
        "cost_type": data.cost_type,
        "cost_default": data.cost_default,
        "is_active": data.is_active,
        "tier1_default_user": data.tier1_default_user,
        "tier2_default_user": data.tier2_default_user,
        "tier3_default_user": data.tier3_default_user,
        "created_by": current_user.user_id,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.commission_templates.insert_one(template_doc)
    return {"template_id": template_id}


@router.get("/templates")
async def get_commission_templates(current_user=Depends(get_current_user)):
    """Get all commission templates"""
    if not has_perm(current_user, "commissions.template.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    templates = await db.commission_templates.find(
        {"is_active": True}, 
        {"_id": 0}
    ).sort("name", 1).to_list(100)
    return {"items": templates}


@router.put("/templates/{template_id}")
async def update_commission_template(
    template_id: str, 
    data: CommissionTemplateCreate, 
    current_user=Depends(get_current_user)
):
    """Update a commission template"""
    if not has_perm(current_user, "commissions.template.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    update_data = data.model_dump()
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    result = await db.commission_templates.update_one(
        {"template_id": template_id},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return {"message": "Template updated"}


@router.delete("/templates/{template_id}")
async def delete_commission_template(template_id: str, current_user=Depends(get_current_user)):
    """Soft delete a commission template"""
    if not has_perm(current_user, "commissions.template.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    result = await db.commission_templates.update_one(
        {"template_id": template_id},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return {"message": "Template deleted"}
