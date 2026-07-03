"""Contract Template Management - Admin-only CRUD for reusable contract configurations."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import uuid

from database import get_db
from auth_helper import get_current_user, has_perm
from models import ContractTemplate, ContractTemplateCreate, ContractTemplateUpdate
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

register_module_config("contract_templates", "contract_templates", "template_id", "Products & Services")
register_module_events("contract_templates", "contract_template", ["created", "updated", "deleted"])
register_fields("contract_templates", "contract_template", [
    {"name": "template_name", "type": "string", "label": "Template Name", "description": "Name of the contract template"},
    {"name": "contract_type", "type": "string", "label": "Contract Type", "description": "Type of contract (boh, boa, fixed_fee, monthly)"},
    {"name": "default_hours", "type": "number", "label": "Default Hours", "description": "Default hours for BOH contracts"},
    {"name": "default_sell_rate", "type": "number", "label": "Default Sell Rate", "description": "Default hourly rate for customers"},
    {"name": "default_fixed_amount", "type": "number", "label": "Default Fixed Amount", "description": "Default fixed amount for fixed-fee contracts"},
    {"name": "billing_terms", "type": "string", "label": "Billing Terms", "description": "Billing frequency (monthly, weekly)"},
    {"name": "payment_terms", "type": "string", "label": "Payment Terms", "description": "Payment conditions (net_30, net_45)"},
    {"name": "is_active", "type": "boolean", "label": "Is Active", "description": "Whether template is available for use"},
    {"name": "description", "type": "string", "label": "Description", "description": "Template purpose and use case"},
])


@router.get("/contract-templates")
async def get_contract_templates(
    is_active: Optional[bool] = None,
    contract_type: Optional[str] = None,
    current_user=Depends(get_current_user)
):
    """Get all contract templates. Available to all authenticated users."""
    if not has_perm(current_user, "contract_templates.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    
    filt = {}
    if is_active is not None:
        filt["is_active"] = is_active
    if contract_type:
        filt["contract_type"] = contract_type
    
    templates = await db.contract_templates.find(filt, {"_id": 0}).sort("template_name", 1).to_list(500)
    return {"items": templates, "total": len(templates)}


@router.get("/contract-templates/{template_id}")
async def get_contract_template(
    template_id: str,
    current_user=Depends(get_current_user)
):
    """Get single contract template by ID."""
    if not has_perm(current_user, "contract_templates.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    
    template = await db.contract_templates.find_one({"template_id": template_id}, {"_id": 0})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return template


@router.post("/contract-templates")
async def create_contract_template(
    data: ContractTemplateCreate,
    current_user=Depends(get_current_user)
):
    """Create new contract template. Admin only."""
    if not has_perm(current_user, "contract_templates.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    # Validate contract type
    valid_types = ["boh", "boa", "fixed_fee", "monthly"]
    if data.contract_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid contract type. Must be one of: {', '.join(valid_types)}")
    
    # Check for duplicate name
    existing = await db.contract_templates.find_one({"template_name": data.template_name}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Template with this name already exists")
    
    template_id = f"ct_{uuid.uuid4().hex[:12]}"
    
    template = {
        "template_id": template_id,
        "template_name": data.template_name,
        "description": data.description,
        "contract_type": data.contract_type,
        "default_hours": data.default_hours,
        "default_sell_rate": data.default_sell_rate,
        "default_fixed_amount": data.default_fixed_amount,
        "default_monthly_rate": data.default_monthly_rate,
        "billing_terms": data.billing_terms,
        "payment_terms": data.payment_terms,
        "convert_to_boa_at_exhaustion": data.convert_to_boa_at_exhaustion,
        "default_contract_duration_days": data.default_contract_duration_days,
        "created_by": current_user.user_id,
        "created_by_name": current_user.name,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "is_active": True
    }
    
    await db.contract_templates.insert_one(template)
    template.pop("_id", None)
    
    return {"message": "Contract template created successfully", "template": template}


@router.put("/contract-templates/{template_id}")
async def update_contract_template(
    template_id: str,
    data: ContractTemplateUpdate,
    current_user=Depends(get_current_user)
):
    """Update contract template. Admin only."""
    if not has_perm(current_user, "contract_templates.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    template = await db.contract_templates.find_one({"template_id": template_id}, {"_id": 0})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Check for duplicate name if changing name
    if data.template_name and data.template_name != template["template_name"]:
        existing = await db.contract_templates.find_one({"template_name": data.template_name}, {"_id": 0})
        if existing:
            raise HTTPException(status_code=400, detail="Template with this name already exists")
    
    # Build update dict (only include provided fields)
    update_data = {"updated_at": datetime.now(timezone.utc)}
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            update_data[field] = value
    
    await db.contract_templates.update_one(
        {"template_id": template_id},
        {"$set": update_data}
    )
    
    updated = await db.contract_templates.find_one({"template_id": template_id}, {"_id": 0})
    return {"message": "Template updated successfully", "template": updated}


@router.delete("/contract-templates/{template_id}")
async def delete_contract_template(
    template_id: str,
    current_user=Depends(get_current_user)
):
    """Delete (soft delete) contract template. Admin only."""
    if not has_perm(current_user, "contract_templates.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    template = await db.contract_templates.find_one({"template_id": template_id}, {"_id": 0})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Soft delete
    await db.contract_templates.update_one(
        {"template_id": template_id},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}}
    )
    
    return {"message": "Template deleted successfully"}
