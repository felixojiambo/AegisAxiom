from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
import uuid

from database import get_db
from models import PartnerApplicationCreate
from rate_limiter import limiter
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

register_module_config("partners", "partner_applications", "application_id", "Admin")
register_module_events("partners", "partner_application", ["created"])
register_fields("partners", "partner_application", [
    {"name": "company_name", "type": "string", "label": "Company Name", "description": "Applicant's company name"},
    {"name": "contact_name", "type": "string", "label": "Contact Name", "description": "Primary contact person"},
    {"name": "email", "type": "string", "label": "Email", "description": "Contact email address"},
    {"name": "status", "type": "string", "label": "Status", "description": "Application status (pending, approved, rejected)"},
])


@router.post("/apply")
@limiter.limit("3/hour")  # SECURITY FIX H-4: Add rate limiting to prevent application spam
async def apply_partner(data: PartnerApplicationCreate, request: Request):
    db = get_db()
    existing = await db.partner_applications.find_one({"email": data.email.lower()}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Application already submitted for this email")
    app_id = f"app_{uuid.uuid4().hex[:12]}"
    doc = {
        "application_id": app_id,
        **data.model_dump(),
        "email": data.email.lower(),
        "status": "pending",
        "applied_at": datetime.now(timezone.utc),
        "reviewed_at": None,
        "reviewed_by": None,
        "notes": None,
    }
    await db.partner_applications.insert_one(doc)
    return {"message": "Application submitted successfully", "application_id": app_id}
