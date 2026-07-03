"""SOW Milestone Templates - Reusable milestone presets sortable by Line of Business."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import uuid
from database import get_db
from auth_helper import get_current_user, has_perm
from models import SOWMilestoneTemplateCreate, SOWMilestoneTemplateUpdate
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

VALID_LOBS = {"professional_services", "field_services", "wholesale_mrr"}

register_module_config("sow_milestone_templates", "sow_milestone_templates", "template_id", "Sales Ops")
register_module_events("sow_milestone_templates", "sow_milestone_template", ["created", "updated", "deleted"])
register_fields("sow_milestone_templates", "sow_milestone_template", [
    {"name": "template_name", "type": "string", "label": "Template Name", "description": "Name of the milestone template"},
    {"name": "line_of_business", "type": "string", "label": "Line of Business", "description": "PS, FS, or Wholesale MRR"},
    {"name": "is_active", "type": "boolean", "label": "Is Active", "description": "Whether template is available for use"},
])


@router.get("/sow-milestone-templates")
async def list_templates(
    lob: Optional[str] = None,
    is_active: Optional[bool] = True,
    current_user=Depends(get_current_user)
):
    """List milestone templates, optionally filtered by Line of Business."""
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    filt = {}
    if lob:
        if lob not in VALID_LOBS:
            raise HTTPException(status_code=400, detail=f"Invalid LOB. Must be one of: {', '.join(sorted(VALID_LOBS))}")
        filt["line_of_business"] = lob
    if is_active is not None:
        filt["is_active"] = is_active

    templates = await db.sow_milestone_templates.find(filt, {"_id": 0}).sort([
        ("line_of_business", 1),
        ("template_name", 1),
    ]).to_list(500)
    return {"items": templates, "total": len(templates)}


@router.get("/sow-milestone-templates/{template_id}")
async def get_template(template_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "sales_ops.solutions_design.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    template = await db.sow_milestone_templates.find_one({"template_id": template_id}, {"_id": 0})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("/sow-milestone-templates")
async def create_template(data: SOWMilestoneTemplateCreate, current_user=Depends(get_current_user)):
    """Create a new milestone template. Admin only."""
    if not has_perm(current_user, "contract_templates.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if data.line_of_business not in VALID_LOBS:
        raise HTTPException(status_code=400, detail=f"Invalid LOB. Must be one of: {', '.join(sorted(VALID_LOBS))}")
    if not data.template_name.strip():
        raise HTTPException(status_code=400, detail="Template name is required")

    db = get_db()
    existing = await db.sow_milestone_templates.find_one({"template_name": data.template_name}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Template with this name already exists")

    template_id = f"smt_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    template = {
        "template_id": template_id,
        "template_name": data.template_name.strip(),
        "description": data.description,
        "line_of_business": data.line_of_business,
        "milestones": [m.model_dump() for m in (data.milestones or [])],
        "is_active": True,
        "created_by": current_user.user_id,
        "created_by_name": current_user.name,
        "created_at": now,
        "updated_at": now,
    }
    await db.sow_milestone_templates.insert_one(template)
    template.pop("_id", None)
    return {"message": "Template created successfully", "template": template}


@router.put("/sow-milestone-templates/{template_id}")
async def update_template(template_id: str, data: SOWMilestoneTemplateUpdate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "contract_templates.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    template = await db.sow_milestone_templates.find_one({"template_id": template_id}, {"_id": 0})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    update_data = {"updated_at": datetime.now(timezone.utc)}
    payload = data.model_dump(exclude_unset=True)

    if "line_of_business" in payload and payload["line_of_business"] not in VALID_LOBS:
        raise HTTPException(status_code=400, detail=f"Invalid LOB. Must be one of: {', '.join(sorted(VALID_LOBS))}")

    if "template_name" in payload and payload["template_name"] != template["template_name"]:
        existing = await db.sow_milestone_templates.find_one({"template_name": payload["template_name"]}, {"_id": 0})
        if existing:
            raise HTTPException(status_code=400, detail="Template with this name already exists")

    if "milestones" in payload and payload["milestones"] is not None:
        # serialize Pydantic models if needed
        payload["milestones"] = [
            m.model_dump() if hasattr(m, "model_dump") else m for m in payload["milestones"]
        ]

    update_data.update({k: v for k, v in payload.items() if v is not None})

    await db.sow_milestone_templates.update_one({"template_id": template_id}, {"$set": update_data})
    updated = await db.sow_milestone_templates.find_one({"template_id": template_id}, {"_id": 0})
    return {"message": "Template updated successfully", "template": updated}


@router.delete("/sow-milestone-templates/{template_id}")
async def delete_template(template_id: str, current_user=Depends(get_current_user)):
    """Soft delete (set is_active=False)."""
    if not has_perm(current_user, "contract_templates.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    template = await db.sow_milestone_templates.find_one({"template_id": template_id}, {"_id": 0})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.sow_milestone_templates.update_one(
        {"template_id": template_id},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}}
    )
    return {"message": "Template deleted successfully"}
