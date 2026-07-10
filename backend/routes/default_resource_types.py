"""Default Resource Types - Admin Management for default pricing templates."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
import logging

from database import get_db
from auth_helper import get_current_user, has_perm
from services.field_registry import register_fields, register_module_events, register_module_config

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_config("default_resource_types", "default_resource_types", "name", "Products & Services")
register_module_events("default_resource_types", "resource_type", ["created", "updated", "deleted"])
register_fields("default_resource_types", "resource_type", [
    {"name": "name", "type": "string", "label": "Name", "description": "Resource type name (e.g., Senior Architect, Junior Engineer)"},
    {"name": "sell_rate", "type": "number", "label": "Sell Rate", "description": "Default billing rate for this resource type"},
    {"name": "description", "type": "string", "label": "Description", "description": "Role and responsibility description"},
    {"name": "is_active", "type": "boolean", "label": "Is Active", "description": "Whether available for new assignments"},
])


@router.get("/default-resource-types")
async def get_default_resource_types(current_user=Depends(get_current_user)):
    """Get all default resource type templates."""
    if not has_perm(current_user, "admin.resource_types.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    types = await db.default_resource_types.find({}, {"_id": 0}).sort("sell_rate", -1).to_list(100)
    
    # Add resource_type_id field for frontend compatibility (using name as ID)
    for rt in types:
        rt["resource_type_id"] = rt["name"]
    
    return {"default_resource_types": types}


@router.post("/default-resource-types")
async def create_default_resource_type(data: dict, current_user=Depends(get_current_user)):
    """Create a new default resource type template. Requires admin.resource_types.create."""
    if not has_perm(current_user, "admin.resource_types.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    # Validate required fields
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="Name is required")
    if not data.get("sell_rate") and data.get("sell_rate") != 0:
        raise HTTPException(status_code=400, detail="Sell rate is required")
    
    # Check for duplicate name
    existing = await db.default_resource_types.find_one({"name": data["name"]})
    if existing:
        raise HTTPException(status_code=400, detail=f"Resource type '{data['name']}' already exists")
    
    doc = {
        "name": data["name"],
        "sell_rate": float(data["sell_rate"]),
        "description": data.get("description", ""),
        "is_active": data.get("is_active", True),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.default_resource_types.insert_one(doc)
    return {"message": "Default resource type created", "resource_type": doc}


@router.put("/default-resource-types/{name}")
async def update_default_resource_type(name: str, data: dict, current_user=Depends(get_current_user)):
    """Update a default resource type template. Requires admin.resource_types.edit."""
    if not has_perm(current_user, "admin.resource_types.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    existing = await db.default_resource_types.find_one({"name": name})
    if not existing:
        raise HTTPException(status_code=404, detail="Resource type not found")
    
    update_data = {}
    if "sell_rate" in data:
        update_data["sell_rate"] = float(data["sell_rate"])
    if "description" in data:
        update_data["description"] = data["description"]
    if "is_active" in data:
        update_data["is_active"] = data["is_active"]
    
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    await db.default_resource_types.update_one({"name": name}, {"$set": update_data})
    
    updated = await db.default_resource_types.find_one({"name": name}, {"_id": 0})
    return {"message": "Default resource type updated", "resource_type": updated}


@router.delete("/default-resource-types/{name}")
async def delete_default_resource_type(name: str, current_user=Depends(get_current_user)):
    """Delete a default resource type template. Requires admin.resource_types.delete."""
    if not has_perm(current_user, "admin.resource_types.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    result = await db.default_resource_types.delete_one({"name": name})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Resource type not found")
    
    return {"message": f"Default resource type '{name}' deleted"}


async def seed_default_resource_types(db):
    """Seed initial default resource types if none exist."""
    count = await db.default_resource_types.count_documents({})
    if count > 0:
        logger.info(f"Default resource types already seeded ({count} types)")
        return
    
    defaults = [
        {"name": "SR Architect", "sell_rate": 225.0, "description": "Senior architect", "is_active": True},
        {"name": "Consultant", "sell_rate": 200.0, "description": "Subject matter expert", "is_active": True},
        {"name": "SR Engineer", "sell_rate": 185.0, "description": "Senior engineer", "is_active": True},
        {"name": "Senior Developer", "sell_rate": 175.0, "description": "Experienced software engineer", "is_active": True},
        {"name": "DevOps Engineer", "sell_rate": 160.0, "description": "Infrastructure & deployment", "is_active": True},
        {"name": "Senior Designer", "sell_rate": 150.0, "description": "Lead UI/UX designer", "is_active": True},
        {"name": "Project Manager", "sell_rate": 140.0, "description": "Project management", "is_active": True},
        {"name": "Mid-Level Developer", "sell_rate": 125.0, "description": "Software engineer with 2-5 years", "is_active": True},
        {"name": "Business Analyst", "sell_rate": 120.0, "description": "Requirements & analysis", "is_active": True},
        {"name": "Designer", "sell_rate": 100.0, "description": "UI/UX designer", "is_active": True},
        {"name": "QA Engineer", "sell_rate": 90.0, "description": "Quality assurance & testing", "is_active": True},
        {"name": "Junior Developer", "sell_rate": 85.0, "description": "Entry-level developer", "is_active": True},
    ]
    
    for default in defaults:
        default["created_at"] = datetime.now(timezone.utc)
        default["updated_at"] = datetime.now(timezone.utc)
    
    await db.default_resource_types.insert_many(defaults)
    logger.info(f"Seeded {len(defaults)} default resource types")
