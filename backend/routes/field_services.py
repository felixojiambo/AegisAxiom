"""
Field Services Management Module
Enhanced job management with scheduling, calendar, operating hours, and time tracking integration.
"""
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid
import logging

from pydantic import BaseModel
from database import get_db
from models import (
    FieldJob, FieldJobCreate, FieldJobUpdate,
    AccountOperatingHours, DayHours, SchedulingRules
)
from auth_helper import get_current_user, get_job_filter, has_perm, verify_ownership


class OperatingHoursUpdate(BaseModel):
    monday: Optional[DayHours] = None
    tuesday: Optional[DayHours] = None
    wednesday: Optional[DayHours] = None
    thursday: Optional[DayHours] = None
    friday: Optional[DayHours] = None
    saturday: Optional[DayHours] = None
    sunday: Optional[DayHours] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None
from services.event_bus import emit_event
from services.policy_engine import evaluate_policies
from services.policy_helpers import enforce_policy_result
from services.field_registry import register_fields, register_module_events, register_module_config
from services.field_options import FIELD_JOB_STATUSES
from services.fs_notifications import (
    send_contractor_job_confirmed, send_customer_job_confirmed,
    send_customer_arrival_notification, send_customer_job_completed
)
from services.job_activity import (
    log_job_activity, get_job_activity, is_notification_enabled,
    get_notification_settings, DEFAULT_NOTIFICATION_SETTINGS
)

logger = logging.getLogger(__name__)

router = APIRouter()

WEEKDAY_MAP = {0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday", 4: "friday", 5: "saturday", 6: "sunday"}

# Register fields available for policy evaluation
register_fields("field_services", "field_job", [
    {"name": "scheduled_date", "type": "date", "label": "Scheduled Date", "description": "Date the field job is scheduled to be performed"},
    {"name": "scheduled_start", "type": "time", "label": "Scheduled Start", "description": "Start time for the scheduled job (HH:MM)"},
    {"name": "scheduled_end", "type": "time", "label": "Scheduled End", "description": "End time for the scheduled job (HH:MM)"},
    {"name": "estimated_hours", "type": "number", "label": "Estimated Hours", "description": "Estimated duration of the job in hours"},
    {"name": "job_type", "type": "string", "label": "Job Type", "description": "Type of field service job (e.g., install, repair, maintenance)"},
    {"name": "status", "type": "string", "label": "Job Status", "options": FIELD_JOB_STATUSES, "description": "Current job lifecycle status (scheduled, in_progress, completed, etc.)"},
    # Computed fields
    {"name": "customer_closed_today", "type": "boolean", "label": "Customer Closed Today", "computed": True, "description": "Whether the customer location is closed on the scheduled date"},
    {"name": "scheduled_start_before_open", "type": "boolean", "label": "Start Before Open", "computed": True, "description": "Whether the job start time is before the customer's opening hours"},
    {"name": "scheduled_end_after_close", "type": "boolean", "label": "End After Close", "computed": True, "description": "Whether the job end time is after the customer's closing hours"},
    {"name": "exceeds_available_hours", "type": "boolean", "label": "Exceeds Available Hours", "computed": True, "description": "Whether estimated hours exceed the customer's available operating hours for the day"},
])

register_module_config("field_services", "field_jobs", "job_id", "Operations")
register_module_events("field_services", "field_job", ["created", "updated", "deleted", "status_changed"])


# ---- SCHEDULING VALIDATION ----

def get_available_hours(day_hours: dict) -> float:
    """Calculate available hours for a given day's operating hours."""
    if day_hours.get("closed", False):
        return 0.0
    open_time = day_hours.get("open", "09:00")
    close_time = day_hours.get("close", "17:00")
    oh, om = map(int, open_time.split(":"))
    ch, cm = map(int, close_time.split(":"))
    return (ch * 60 + cm - oh * 60 - om) / 60.0


def validate_schedule(job_data: dict, operating_hours: dict) -> dict:
    """Validate a job schedule against account operating hours.
    Returns: {valid: bool, violations: list, warnings: list}
    """
    violations = []
    warnings = []

    scheduled_date = job_data.get("scheduled_date")
    scheduled_start = job_data.get("scheduled_start")
    scheduled_end = job_data.get("scheduled_end")
    estimated_hours = job_data.get("estimated_hours", 0)

    if not scheduled_date:
        return {"valid": True, "violations": [], "warnings": ["No date set — skipping validation"]}

    # Parse the date to get day of week
    try:
        dt = datetime.strptime(scheduled_date, "%Y-%m-%d")
    except ValueError:
        return {"valid": False, "violations": ["Invalid date format"], "warnings": []}

    day_name = WEEKDAY_MAP[dt.weekday()]
    day_hours = operating_hours.get(day_name, {})

    # Check if customer is closed that day
    if day_hours.get("closed", False):
        violations.append(f"Customer is closed on {day_name.title()}s")

    if not day_hours.get("closed", False):
        open_time = day_hours.get("open", "09:00")
        close_time = day_hours.get("close", "17:00")
        available = get_available_hours(day_hours)

        # Check if start time is before customer opens
        if scheduled_start and scheduled_start < open_time:
            violations.append(f"Start time {scheduled_start} is before customer opens at {open_time}")

        # Check if end time is after customer closes
        if scheduled_end and scheduled_end > close_time:
            violations.append(f"End time {scheduled_end} is after customer closes at {close_time}")

        # Check if estimated hours exceed available window
        if estimated_hours > 0 and estimated_hours > available:
            violations.append(
                f"Estimated {estimated_hours}h exceeds available {available}h window ({open_time}-{close_time})"
            )

        # Warnings for tight schedules
        if estimated_hours > 0 and estimated_hours > available * 0.9:
            warnings.append(f"Job uses {estimated_hours}/{available}h of available time — very tight schedule")

    return {
        "valid": len(violations) == 0,
        "violations": violations,
        "warnings": warnings,
    }


# ---- OPERATING HOURS ----

@router.get("/field-services/accounts/{account_id}/operating-hours")
async def get_operating_hours(account_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    doc = await db.account_operating_hours.find_one({"account_id": account_id}, {"_id": 0})
    if not doc:
        # Return defaults
        default = AccountOperatingHours(account_id=account_id)
        return default.model_dump()
    return doc


@router.put("/field-services/accounts/{account_id}/operating-hours")
async def update_operating_hours(account_id: str, data: OperatingHoursUpdate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.edit") and not has_perm(current_user, "field_jobs.edit.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Verify account exists
    account = await db.accounts.find_one({"account_id": account_id}, {"_id": 0, "name": 1})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    update_data = {k: v.model_dump() if hasattr(v, 'model_dump') else v for k, v in data.model_dump(exclude_none=True).items()}
    update_data["account_id"] = account_id
    update_data["updated_at"] = datetime.now(timezone.utc)

    await db.account_operating_hours.update_one(
        {"account_id": account_id},
        {"$set": update_data},
        upsert=True
    )
    return await db.account_operating_hours.find_one({"account_id": account_id}, {"_id": 0})


# ---- SCHEDULING RULES ----

@router.get("/field-services/scheduling-rules")
async def get_scheduling_rules(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    doc = await db.scheduling_rules.find_one({"rule_id": "global"}, {"_id": 0})
    if not doc:
        default = SchedulingRules()
        return default.model_dump()
    return doc


@router.put("/field-services/scheduling-rules")
async def update_scheduling_rules(data: dict, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    data["rule_id"] = "global"
    data["updated_at"] = datetime.now(timezone.utc)
    await db.scheduling_rules.update_one({"rule_id": "global"}, {"$set": data}, upsert=True)
    return await db.scheduling_rules.find_one({"rule_id": "global"}, {"_id": 0})


# ---- SCHEDULE VALIDATION ENDPOINT ----

@router.post("/field-services/validate-schedule")
async def validate_job_schedule(data: dict, current_user=Depends(get_current_user)):
    """Validate a proposed schedule against customer operating hours."""
    if not has_perm(current_user, "field_jobs.edit") and not has_perm(current_user, "field_jobs.edit.all") and not has_perm(current_user, "field_jobs.edit.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    account_id = data.get("account_id")
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id required")

    # Get operating hours
    hours_doc = await db.account_operating_hours.find_one({"account_id": account_id}, {"_id": 0})
    if not hours_doc:
        hours_doc = AccountOperatingHours(account_id=account_id).model_dump()

    # Get global rules
    rules_doc = await db.scheduling_rules.find_one({"rule_id": "global"}, {"_id": 0})
    if not rules_doc:
        rules_doc = SchedulingRules().model_dump()

    result = validate_schedule(data, hours_doc)
    result["operating_hours"] = hours_doc
    result["rules"] = rules_doc
    return result


# ---- FIELD JOBS (ENHANCED CRUD) ----

@router.get("/field-services/jobs")
async def list_field_jobs(
    skip: int = 0,
    limit: int = 100,
    status: str = Query(None),
    pm_id: str = Query(None),
    account_id: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    test_data_only: bool = Query(False),
    current_user=Depends(get_current_user)
):
    db = get_db()
    filt = await get_job_filter(current_user)

    # Test data filter
    if test_data_only:
        filt["is_test_data"] = True

    # Apply filters
    if status:
        filt["status"] = status
    if pm_id:
        filt["assigned_pm_id"] = pm_id
    if account_id:
        filt["account_id"] = account_id
    if date_from or date_to:
        date_filt = {}
        if date_from:
            date_filt["$gte"] = date_from
        if date_to:
            date_filt["$lte"] = date_to
        filt["scheduled_date"] = date_filt

    docs = await db.field_jobs.find(filt, {"_id": 0}).sort("scheduled_date", 1).skip(skip).limit(min(limit, 500)).to_list(min(limit, 500))
    total = await db.field_jobs.count_documents(filt)
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.get("/field-services/calendar")
async def get_calendar_data(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    pm_id: str = Query(None),
    account_id: str = Query(None),
    test_data_only: bool = Query(False),
    current_user=Depends(get_current_user)
):
    """Get jobs for calendar view within a date range."""
    db = get_db()
    filt = await get_job_filter(current_user)
    filt["scheduled_date"] = {"$gte": start, "$lte": end}

    if test_data_only:
        filt["is_test_data"] = True
    if pm_id:
        filt["assigned_pm_id"] = pm_id
    if account_id:
        filt["account_id"] = account_id

    jobs = await db.field_jobs.find(filt, {"_id": 0}).sort("scheduled_date", 1).to_list(500)

    # If account_id filter, also fetch operating hours
    operating_hours = None
    if account_id:
        hours_doc = await db.account_operating_hours.find_one({"account_id": account_id}, {"_id": 0})
        if hours_doc:
            operating_hours = hours_doc

    return {
        "jobs": jobs,
        "start": start,
        "end": end,
        "operating_hours": operating_hours
    }


@router.post("/field-services/jobs")
async def create_field_job(data: FieldJobCreate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()

    # Lookup account
    account = await db.accounts.find_one({"account_id": data.account_id}, {"_id": 0, "name": 1, "assigned_sales_rep_id": 1})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Lookup tech name
    tech_name = None
    if data.assigned_tech_id:
        tech = await db.users.find_one({"user_id": data.assigned_tech_id}, {"_id": 0, "name": 1})
        tech_name = tech["name"] if tech else None

    # Lookup PM name
    pm_name = None
    if data.assigned_pm_id:
        pm = await db.users.find_one({"user_id": data.assigned_pm_id}, {"_id": 0, "name": 1})
        pm_name = pm["name"] if pm else None

    # Deal name
    deal_name = None
    if data.deal_id:
        deal = await db.deals.find_one({"deal_id": data.deal_id}, {"_id": 0, "name": 1})
        deal_name = deal["name"] if deal else None

    # Validate schedule against operating hours via policy engine (if not overridden)
    if not data.schedule_override and data.scheduled_date:
        hours_doc = await db.account_operating_hours.find_one({"account_id": data.account_id}, {"_id": 0})
        if hours_doc:
            rules_doc = await db.scheduling_rules.find_one({"rule_id": "global"}, {"_id": 0})
            enforce = (rules_doc or {}).get("enforce_operating_hours", True)
            if enforce:
                validation = validate_schedule(data.model_dump(), hours_doc)
                # Build policy payload from validation results
                policy_payload = {
                    "scheduled_date": data.scheduled_date,
                    "scheduled_start": data.scheduled_start,
                    "scheduled_end": data.scheduled_end,
                    "estimated_hours": data.estimated_hours or 0,
                    "customer_closed_today": not validation["valid"] and any(
                        v.get("type") == "closed" for v in validation.get("violations", [])
                    ),
                    "scheduled_start_before_open": any(
                        "before" in v.get("message", "").lower() or "open" in v.get("message", "").lower()
                        for v in validation.get("violations", [])
                    ),
                    "scheduled_end_after_close": any(
                        "after" in v.get("message", "").lower() or "close" in v.get("message", "").lower()
                        for v in validation.get("violations", [])
                    ),
                    "exceeds_available_hours": any(
                        "capacity" in v.get("message", "").lower() or "exceed" in v.get("message", "").lower()
                        for v in validation.get("violations", [])
                    ),
                }
                result = await evaluate_policies(db, "field_services", "field_job", "on_create", policy_payload, user_role=current_user.role)
                if result["blocked"]:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "message": result["block_messages"][0] if result["block_messages"] else "Schedule violates customer operating hours",
                            "violations": validation.get("violations", []),
                            "warnings": validation.get("warnings", []),
                            "requires_override": True,
                        }
                    )

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    # Auto-populate checklists from job type template
    checklist_checkin = []
    checklist_checkout = []
    job_type_name = None
    if data.job_type:
        jt = await db.job_types.find_one({"type_id": data.job_type}, {"_id": 0})
        if jt:
            job_type_name = jt.get("name")
            checklist_checkin = [{"id": item["id"], "label": item["label"], "type": item.get("type", "boolean"), "options": item.get("options"), "required": item.get("required", False), "value": None, "notes": ""} for item in jt.get("checkin", [])]
            checklist_checkout = [{"id": item["id"], "label": item["label"], "type": item.get("type", "boolean"), "options": item.get("options"), "required": item.get("required", False), "value": None, "notes": ""} for item in jt.get("checkout", [])]

    doc = {
        "job_id": job_id,
        **data.model_dump(),
        "account_name": account["name"],
        "assigned_tech_name": tech_name,
        "assigned_pm_name": pm_name,
        "assigned_sales_rep_id": account.get("assigned_sales_rep_id"),
        "deal_name": deal_name,
        "job_type_name": job_type_name,
        "total_cost": data.labor_cost + data.material_cost,
        "status": "scheduled",
        "actual_hours": 0.0,
        "photos": [],
        "checklist_checkin": checklist_checkin,
        "checklist_checkout": checklist_checkout,
        "created_by": current_user.user_id,
        "created_at": now,
        "updated_at": now,
    }
    await db.field_jobs.insert_one(doc)
    doc.pop("_id", None)
    await emit_event("field_services", "field_job.created", record=doc, user_id=current_user.user_id, user_email=current_user.email)
    return doc


@router.get("/field-services/jobs/{job_id}")
async def get_field_job(job_id: str, current_user=Depends(get_current_user)):
    db = get_db()
    rbac_filter = await get_job_filter(current_user)
    query = {"job_id": job_id}
    if rbac_filter and rbac_filter != {}:
        query = {"$and": [{"job_id": job_id}, rbac_filter]}
    doc = await db.field_jobs.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Job not found")

    # Also fetch time entries linked to this job
    time_entries = await db.time_entries.find(
        {"$or": [{"job_id": job_id}, {"project_id": job_id}]},
        {"_id": 0}
    ).sort("date", -1).to_list(100)
    doc["time_entries"] = time_entries

    # Fetch activity log
    doc["activity"] = await get_job_activity(job_id, 50)

    return doc


@router.put("/field-services/jobs/{job_id}")
async def update_field_job(job_id: str, data: FieldJobUpdate, background_tasks: BackgroundTasks, current_user=Depends(get_current_user)):
    has_edit_all = has_perm(current_user, "field_jobs.edit.all") or has_perm(current_user, "field_jobs.edit")
    has_edit_own = has_perm(current_user, "field_jobs.edit.own")
    if not has_edit_all and not has_edit_own:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()

    existing = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Job not found")

    # Ownership verification for .edit.own users
    if has_edit_own and not has_edit_all:
        if not verify_ownership(existing, current_user, "field_job"):
            raise HTTPException(status_code=403, detail="You can only edit jobs assigned to you")

    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    old_status = existing.get("status")
    new_status = update_data.get("status")

    # Recalculate total cost if costs changed
    if "labor_cost" in update_data or "material_cost" in update_data:
        labor = update_data.get("labor_cost", existing.get("labor_cost", 0))
        material = update_data.get("material_cost", existing.get("material_cost", 0))
        update_data["total_cost"] = labor + material

    # Lookup names if IDs changed
    if "assigned_tech_id" in update_data:
        tech = await db.users.find_one({"user_id": update_data["assigned_tech_id"]}, {"_id": 0, "name": 1})
        update_data["assigned_tech_name"] = tech["name"] if tech else None

    if "assigned_pm_id" in update_data:
        pm = await db.users.find_one({"user_id": update_data["assigned_pm_id"]}, {"_id": 0, "name": 1})
        update_data["assigned_pm_name"] = pm["name"] if pm else None

    # Validate schedule via policy engine if date/time changed and not overridden
    if not update_data.get("schedule_override") and ("scheduled_date" in update_data or "scheduled_start" in update_data or "scheduled_end" in update_data):
        merged = {**existing, **update_data}
        account_id = merged.get("account_id")
        hours_doc = await db.account_operating_hours.find_one({"account_id": account_id}, {"_id": 0})
        if hours_doc:
            rules_doc = await db.scheduling_rules.find_one({"rule_id": "global"}, {"_id": 0})
            enforce = (rules_doc or {}).get("enforce_operating_hours", True)
            if enforce:
                validation = validate_schedule(merged, hours_doc)
                policy_payload = {
                    "scheduled_date": merged.get("scheduled_date"),
                    "scheduled_start": merged.get("scheduled_start"),
                    "scheduled_end": merged.get("scheduled_end"),
                    "estimated_hours": merged.get("estimated_hours") or 0,
                    "customer_closed_today": not validation["valid"] and any(
                        v.get("type") == "closed" for v in validation.get("violations", [])
                    ),
                    "scheduled_start_before_open": any(
                        "before" in v.get("message", "").lower() or "open" in v.get("message", "").lower()
                        for v in validation.get("violations", [])
                    ),
                    "scheduled_end_after_close": any(
                        "after" in v.get("message", "").lower() or "close" in v.get("message", "").lower()
                        for v in validation.get("violations", [])
                    ),
                    "exceeds_available_hours": any(
                        "capacity" in v.get("message", "").lower() or "exceed" in v.get("message", "").lower()
                        for v in validation.get("violations", [])
                    ),
                }
                # Uses on_create event because scheduling policies apply equally to create and reschedule
                fs_result = await evaluate_policies(db, "field_services", "field_job", "on_create", policy_payload, user_role=current_user.role)
                if fs_result["blocked"]:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "message": fs_result["block_messages"][0] if fs_result["block_messages"] else "Schedule violates customer operating hours",
                            "violations": validation.get("violations", []),
                            "warnings": validation.get("warnings", []),
                            "requires_override": True,
                        }
                    )

    update_data["updated_at"] = datetime.now(timezone.utc)
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    updated_job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})

    user_id = current_user.user_id if hasattr(current_user, 'user_id') else str(current_user)
    user_name = current_user.name if hasattr(current_user, 'name') else "System"

    # Log field edits (non-status changes)
    edited_fields = [k for k in update_data if k not in ("status", "updated_at")]
    if edited_fields and not new_status:
        await log_job_activity(
            job_id=job_id, activity_type="edit",
            title="Job updated",
            detail=f"Fields changed: {', '.join(edited_fields)}",
            user_id=user_id, user_name=user_name
        )

    # ---- STATUS CHANGE NOTIFICATIONS ----
    if new_status and new_status != old_status:
        merged = {**existing, **update_data}

        # Always log the status change as activity
        await log_job_activity(
            job_id=job_id, activity_type="status_change",
            title=f"Status changed to {new_status.replace('_', ' ')}",
            detail=f"{old_status} → {new_status}",
            user_id=user_id, user_name=user_name,
            metadata={"old_status": old_status, "new_status": new_status}
        )

        # CONFIRMED: Notify contractor + customer
        if new_status == "confirmed":
            tech_id = merged.get("assigned_tech_id")
            if tech_id and await is_notification_enabled("on_confirmed_contractor"):
                tech = await db.users.find_one({"user_id": tech_id}, {"_id": 0, "email": 1, "name": 1})
                if tech and tech.get("email"):
                    pm_info = {}
                    if merged.get("assigned_pm_id"):
                        pm_user = await db.users.find_one({"user_id": merged["assigned_pm_id"]}, {"_id": 0, "name": 1, "email": 1})
                        if pm_user:
                            pm_info = {"pm_name": pm_user.get("name", ""), "pm_email": pm_user.get("email", "")}
                    background_tasks.add_task(
                        send_contractor_job_confirmed,
                        to=tech["email"], contractor_name=tech.get("name", ""),
                        job_name=merged.get("name", ""), account_name=merged.get("account_name", ""),
                        scheduled_date=merged.get("scheduled_date", ""),
                        scheduled_start=merged.get("scheduled_start", ""),
                        scheduled_end=merged.get("scheduled_end", ""),
                        **pm_info
                    )
                    await log_job_activity(
                        job_id=job_id, activity_type="email_sent",
                        title="Confirmation email sent to contractor",
                        detail=f"To: {tech['email']} ({tech.get('name', '')})",
                        user_id="system", user_name="System",
                        metadata={"to": tech["email"], "recipient_type": "contractor", "notification_key": "on_confirmed_contractor"}
                    )

            if merged.get("customer_contact_email") and await is_notification_enabled("on_confirmed_customer"):
                background_tasks.add_task(
                    send_customer_job_confirmed,
                    to=merged["customer_contact_email"],
                    contact_name=merged.get("customer_contact_name", "Customer"),
                    job_name=merged.get("name", ""), account_name=merged.get("account_name", ""),
                    scheduled_date=merged.get("scheduled_date", ""),
                    scheduled_start=merged.get("scheduled_start", ""),
                    scheduled_end=merged.get("scheduled_end", "")
                )
                await log_job_activity(
                    job_id=job_id, activity_type="email_sent",
                    title="Confirmation email sent to customer",
                    detail=f"To: {merged['customer_contact_email']} ({merged.get('customer_contact_name', '')})",
                    user_id="system", user_name="System",
                    metadata={"to": merged["customer_contact_email"], "recipient_type": "customer", "notification_key": "on_confirmed_customer"}
                )

        # IN_PROGRESS: Notify customer that tech arrived
        elif new_status == "in_progress":
            if merged.get("customer_contact_email") and await is_notification_enabled("on_arrival_customer"):
                background_tasks.add_task(
                    send_customer_arrival_notification,
                    to=merged["customer_contact_email"],
                    contact_name=merged.get("customer_contact_name", "Customer"),
                    job_name=merged.get("name", ""),
                    tech_name=merged.get("assigned_tech_name", "Our technician")
                )
                await log_job_activity(
                    job_id=job_id, activity_type="email_sent",
                    title="Arrival notification sent to customer",
                    detail=f"To: {merged['customer_contact_email']}",
                    user_id="system", user_name="System",
                    metadata={"to": merged["customer_contact_email"], "recipient_type": "customer", "notification_key": "on_arrival_customer"}
                )

        # COMPLETED: Notify customer that job is done
        elif new_status == "completed":
            if merged.get("customer_contact_email") and await is_notification_enabled("on_completed_customer"):
                background_tasks.add_task(
                    send_customer_job_completed,
                    to=merged["customer_contact_email"],
                    contact_name=merged.get("customer_contact_name", "Customer"),
                    job_name=merged.get("name", ""),
                    account_name=merged.get("account_name", "")
                )
                await log_job_activity(
                    job_id=job_id, activity_type="email_sent",
                    title="Completion email sent to customer",
                    detail=f"To: {merged['customer_contact_email']}",
                    user_id="system", user_name="System",
                    metadata={"to": merged["customer_contact_email"], "recipient_type": "customer", "notification_key": "on_completed_customer"}
                )

    await emit_event("field_services", "field_job.updated", record=updated_job, old_record=existing, user_id=current_user.user_id, user_email=current_user.email)
    if new_status and new_status != old_status:
        await emit_event("field_services", "field_job.status_changed", record=updated_job, old_record=existing, user_id=current_user.user_id, user_email=current_user.email)
    return updated_job


@router.delete("/field-services/jobs/{job_id}")
async def delete_field_job(job_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    existing = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Job not found")

    # Ownership verification for non-admin users
    if not has_perm(current_user, "field_jobs.edit.all") and not has_perm(current_user, "*"):
        if not verify_ownership(existing, current_user, "field_job"):
            raise HTTPException(status_code=403, detail="You can only delete jobs assigned to you")

    await db.field_jobs.delete_one({"job_id": job_id})
    await emit_event("field_services", "field_job.deleted", record=existing, user_id=current_user.user_id, user_email=current_user.email)
    return {"message": "Job deleted"}


# ---- PM LIST (for PM filter dropdown) ----

@router.get("/field-services/pms")
async def list_pms(current_user=Depends(get_current_user)):
    """Get list of users who are assigned as PMs on any field job."""
    db = get_db()
    # Get distinct PM IDs from jobs
    pipeline = [
        {"$match": {"assigned_pm_id": {"$ne": None}}},
        {"$group": {"_id": "$assigned_pm_id", "name": {"$first": "$assigned_pm_name"}, "job_count": {"$sum": 1}}},
        {"$sort": {"name": 1}}
    ]
    pms = await db.field_jobs.aggregate(pipeline).to_list(100)

    # Also get all users who could be PMs (admin, director, sales_rep roles)
    pm_roles = ["admin", "director", "sales_rep"]
    all_pms = await db.users.find(
        {"role": {"$in": pm_roles}, "is_active": {"$ne": False}},
        {"_id": 0, "user_id": 1, "name": 1, "role": 1}
    ).to_list(100)

    return {"assigned_pms": pms, "available_pms": all_pms}


# ---- CONTRACTORS LIST (for tech assignment) ----

@router.get("/field-services/contractors")
async def list_contractors(current_user=Depends(get_current_user)):
    """Get list of available contractors for assignment."""
    db = get_db()
    contractors = await db.users.find(
        {"role": "contractor", "is_active": {"$ne": False}},
        {"_id": 0, "user_id": 1, "name": 1, "email": 1, "cost_rate": 1, "contractor_business_id": 1}
    ).sort("name", 1).to_list(200)
    return {"items": contractors}



# ---- JOB TYPES + CHECKLISTS ----

@router.get("/field-services/job-types")
async def list_job_types(current_user=Depends(get_current_user)):
    """Get all job types with their checklist templates."""
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    types = await db.job_types.find({}, {"_id": 0}).sort("name", 1).to_list(50)
    return {"items": types}


@router.get("/field-services/job-types/{type_id}")
async def get_job_type(type_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    doc = await db.job_types.find_one({"type_id": type_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Job type not found")
    return doc


@router.put("/field-services/job-types/{type_id}")
async def update_job_type(type_id: str, data: dict, current_user=Depends(get_current_user)):
    """Update a job type template (admin only)."""
    if not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    data["updated_at"] = datetime.now(timezone.utc)
    data.pop("type_id", None)
    data.pop("_id", None)
    await db.job_types.update_one({"type_id": type_id}, {"$set": data})
    return await db.job_types.find_one({"type_id": type_id}, {"_id": 0})


@router.put("/field-services/jobs/{job_id}/checklist")
async def update_job_checklist(job_id: str, data: dict, current_user=Depends(get_current_user)):
    """Update checklist items on a job. data = {phase: 'checkin'|'checkout', items: [{id, value, notes}]}"""
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    phase = data.get("phase", "checkin")
    items = data.get("items", [])
    key = f"checklist_{phase}"

    # Merge with existing checklist
    existing = job.get(key, [])
    existing_map = {item["id"]: item for item in existing}
    for item in items:
        existing_map[item["id"]] = {**existing_map.get(item["id"], {}), **item, "updated_at": datetime.now(timezone.utc).isoformat()}
    merged = list(existing_map.values())

    await db.field_jobs.update_one({"job_id": job_id}, {"$set": {key: merged, "updated_at": datetime.now(timezone.utc)}})

    user_id = current_user.user_id if hasattr(current_user, 'user_id') else str(current_user)
    user_name = current_user.name if hasattr(current_user, 'name') else "System"
    completed = sum(1 for i in merged if i.get("value") is not None and i.get("value") != "")
    await log_job_activity(
        job_id=job_id, activity_type="checklist_update",
        title=f"{phase.title()} checklist updated",
        detail=f"{completed}/{len(merged)} items completed",
        user_id=user_id, user_name=user_name,
        metadata={"phase": phase, "completed": completed, "total": len(merged)}
    )

    return await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})


# ---- SITE PROFILE ----

@router.get("/field-services/accounts/{account_id}/site-profile")
async def get_site_profile(account_id: str, current_user=Depends(get_current_user)):
    """Get site infrastructure profile for an account."""
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    doc = await db.site_profiles.find_one({"account_id": account_id}, {"_id": 0})
    if not doc:
        return {
            "account_id": account_id,
            "general": {"mdf_location": "", "isp": "", "offices": 0, "conference_rooms": 0, "cubicles": 0},
            "cabling": {"existing_types": [], "patch_panel": False, "patch_panel_ports_used": 0, "patch_panel_ports_open": 0},
            "network_equipment": {"rack_type": "", "rack_us_available": 0, "rack_locked": False, "room_locked": False, "equipment_list": ""},
            "access_points": {"count": 0, "make_model": ""},
            "security": {"cameras": False, "camera_count": 0, "camera_make_model": "", "door_access": False, "access_type": "", "alarm_system": False},
            "fiber": {"backbone": False, "sm_or_mm": "", "strand_count": 0, "available_strands": 0, "panel": False, "connector_type": ""},
            "access_control": {"type": "", "notes": ""},
            "notes": "",
        }
    return doc


@router.put("/field-services/accounts/{account_id}/site-profile")
async def update_site_profile(account_id: str, data: dict, current_user=Depends(get_current_user)):
    """Update site infrastructure profile."""
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    account = await db.accounts.find_one({"account_id": account_id}, {"_id": 0, "name": 1})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    data["account_id"] = account_id
    data["account_name"] = account.get("name", "")
    data["updated_at"] = datetime.now(timezone.utc)
    data.pop("_id", None)

    await db.site_profiles.update_one({"account_id": account_id}, {"$set": data}, upsert=True)
    return await db.site_profiles.find_one({"account_id": account_id}, {"_id": 0})


# ---- STATS ----

@router.get("/field-services/stats")
async def get_field_service_stats(current_user=Depends(get_current_user)):
    """Dashboard stats for field services."""
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    filt = await get_job_filter(current_user)

    total = await db.field_jobs.count_documents(filt)
    scheduled = await db.field_jobs.count_documents({**filt, "status": "scheduled"})
    confirmed = await db.field_jobs.count_documents({**filt, "status": "confirmed"})
    in_progress = await db.field_jobs.count_documents({**filt, "status": "in_progress"})
    completed = await db.field_jobs.count_documents({**filt, "status": "completed"})
    cancelled = await db.field_jobs.count_documents({**filt, "status": "cancelled"})

    # This week's jobs
    today = datetime.now(timezone.utc).date()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end = (today + timedelta(days=6 - today.weekday())).isoformat()
    this_week = await db.field_jobs.count_documents({
        **filt,
        "scheduled_date": {"$gte": week_start, "$lte": week_end}
    })

    return {
        "total": total,
        "scheduled": scheduled,
        "confirmed": confirmed,
        "in_progress": in_progress,
        "completed": completed,
        "cancelled": cancelled,
        "this_week": this_week,
    }


# ---- NOTIFICATION ENDPOINTS ----

@router.post("/field-services/jobs/{job_id}/notify-arrival")
async def notify_arrival(job_id: str, background_tasks: BackgroundTasks, current_user=Depends(get_current_user)):
    """Contractor notifies that they've arrived — auto-notifies customer."""
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    user_id = current_user.user_id if hasattr(current_user, 'user_id') else str(current_user)
    user_name = current_user.name if hasattr(current_user, 'name') else "System"

    # Move to in_progress
    await db.field_jobs.update_one(
        {"job_id": job_id},
        {"$set": {"status": "in_progress", "check_in_time": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}}
    )

    await log_job_activity(
        job_id=job_id, activity_type="status_change",
        title="Status changed to in progress",
        detail="Contractor arrived on-site",
        user_id=user_id, user_name=user_name,
        metadata={"old_status": job.get("status"), "new_status": "in_progress", "trigger": "arrival_notification"}
    )

    # Notify customer
    if job.get("customer_contact_email") and await is_notification_enabled("on_arrival_customer"):
        background_tasks.add_task(
            send_customer_arrival_notification,
            to=job["customer_contact_email"],
            contact_name=job.get("customer_contact_name", "Customer"),
            job_name=job.get("name", ""),
            tech_name=job.get("assigned_tech_name", "Our technician")
        )
        await log_job_activity(
            job_id=job_id, activity_type="email_sent",
            title="Arrival notification sent to customer",
            detail=f"To: {job['customer_contact_email']}",
            user_id="system", user_name="System",
            metadata={"to": job["customer_contact_email"], "recipient_type": "customer"}
        )

    return {"message": "Arrival notification sent, job moved to in_progress"}


# ---- JOB ACTIVITY LOG ----

@router.get("/field-services/jobs/{job_id}/activity")
async def get_activity(job_id: str, limit: int = Query(100), current_user=Depends(get_current_user)):
    """Get activity/communication log for a job."""
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    activity = await get_job_activity(job_id, limit)
    return {"items": activity, "total": len(activity)}


@router.post("/field-services/jobs/{job_id}/activity")
async def add_activity_note(job_id: str, data: dict, current_user=Depends(get_current_user)):
    """Manually add a note/activity to a job."""
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0, "job_id": 1})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    user_id = current_user.user_id if hasattr(current_user, 'user_id') else str(current_user)
    user_name = current_user.name if hasattr(current_user, 'name') else "Unknown"

    entry = await log_job_activity(
        job_id=job_id,
        activity_type=data.get("type", "note"),
        title=data.get("title", "Note added"),
        detail=data.get("detail", ""),
        user_id=user_id, user_name=user_name,
        metadata=data.get("metadata")
    )
    entry.pop("_id", None)
    return entry


# ---- NOTIFICATION SETTINGS ----

@router.get("/field-services/notification-settings")
async def get_notif_settings(current_user=Depends(get_current_user)):
    """Get notification settings for field services."""
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    settings = await get_notification_settings()
    return settings


@router.put("/field-services/notification-settings")
async def update_notif_settings(data: dict, current_user=Depends(get_current_user)):
    """Update notification settings (admin only)."""
    if not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    data["settings_id"] = "fs_notifications"
    data["updated_at"] = datetime.now(timezone.utc)
    await db.fs_notification_settings.update_one(
        {"settings_id": "fs_notifications"},
        {"$set": data},
        upsert=True
    )
    return await db.fs_notification_settings.find_one({"settings_id": "fs_notifications"}, {"_id": 0})


@router.get("/field-services/notifications")
async def get_notification_log(
    job_id: str = Query(None),
    limit: int = Query(50),
    current_user=Depends(get_current_user)
):
    """Get notification history."""
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    filt = {}
    if job_id:
        filt["job_id"] = job_id
    logs = await db.notification_log.find(filt, {"_id": 0}).sort("timestamp", -1).limit(limit).to_list(limit)
    return {"items": logs}


@router.post("/field-services/send-reminders")
async def trigger_reminders_manually(current_user=Depends(get_current_user)):
    """Manually trigger day-before reminders (admin only)."""
    if not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Admin only")
    from services.fs_notifications import run_day_before_reminders
    count = await run_day_before_reminders()
    return {"message": f"Sent {count} reminder emails"}



# ========================================
# PHASE 3A: SURVEY FORMS & SOW GENERATION
# ========================================

@router.post("/v2/field-services/jobs/{job_id}/survey-response")
async def submit_survey_response(
    job_id: str,
    survey_data: dict,
    current_user=Depends(get_current_user)
):
    """
    Submit survey form responses for a survey-type job.
    This stores the survey data and can optionally auto-generate an SOW.
    """
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.get("job_category") != "survey":
        raise HTTPException(status_code=400, detail="Job must be a survey-type job")
    
    # Store survey responses
    update_data = {
        "survey_form_data": survey_data,
        "updated_at": datetime.now(timezone.utc),
    }
    
    # Optionally auto-complete the survey job
    if survey_data.get("_auto_complete"):
        update_data["status"] = "completed"
    
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    
    # Log activity
    await log_job_activity(
        job_id, "survey_submitted",
        f"Survey form completed by {current_user.name}",
        current_user.user_id
    )
    
    updated_job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    return {"success": True, "job": updated_job}


@router.post("/v2/field-services/jobs/{job_id}/generate-sow")
async def generate_sow(
    job_id: str,
    current_user=Depends(get_current_user)
):
    """
    Auto-generate a Statement of Work from survey data.
    The SOW can be edited, downloaded, and emailed.
    """
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.get("job_category") != "survey":
        raise HTTPException(status_code=400, detail="Job must be a survey-type job")
    
    survey_data = job.get("survey_form_data")
    if not survey_data:
        raise HTTPException(status_code=400, detail="No survey data found. Submit survey first.")
    
    # Get account data
    account = await db.accounts.find_one({"account_id": job["account_id"]}, {"_id": 0})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # Generate SOW
    from services.sow_generator import generate_sow_from_survey
    
    sow_data = generate_sow_from_survey(survey_data, job, account)
    
    # Store SOW in job
    update_data = {
        "sow_data": sow_data,
        "sow_generated_at": datetime.now(timezone.utc),
        "sow_version": job.get("sow_version", 0) + 1,
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    
    # Log activity
    await log_job_activity(
        job_id, "sow_generated",
        f"SOW auto-generated by {current_user.name}",
        current_user.user_id
    )
    
    return {"success": True, "sow": sow_data}


@router.get("/v2/field-services/jobs/{job_id}/sow")
async def get_sow(
    job_id: str,
    format: str = Query("json", description="Format: json, markdown"),
    current_user=Depends(get_current_user)
):
    """
    Retrieve the SOW for a job.
    Supports multiple formats: json (default), markdown.
    """
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    sow_data = job.get("sow_data")
    if not sow_data:
        raise HTTPException(status_code=404, detail="No SOW generated yet")
    
    if format == "markdown":
        from services.sow_generator import sow_to_markdown
        markdown_content = sow_to_markdown(sow_data)
        return {"format": "markdown", "content": markdown_content, "sow": sow_data}
    
    elif format == "json":
        from services.sow_generator import sow_to_json
        json_content = sow_to_json(sow_data)
        return {"format": "json", "content": json_content, "sow": sow_data}
    
    else:
        raise HTTPException(status_code=400, detail="Invalid format. Use: json, markdown")


@router.put("/v2/field-services/jobs/{job_id}/sow")
async def update_sow(
    job_id: str,
    sow_updates: dict,
    current_user=Depends(get_current_user)
):
    """
    Edit the SOW data.
    PMs can manually adjust sections, costs, scope, etc. before finalizing.
    """
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    current_sow = job.get("sow_data")
    if not current_sow:
        raise HTTPException(status_code=404, detail="No SOW to edit. Generate one first.")
    
    # Merge updates into existing SOW
    updated_sow = {**current_sow, **sow_updates}
    
    update_data = {
        "sow_data": updated_sow,
        "sow_edited_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    
    # Log activity
    await log_job_activity(
        job_id, "sow_edited",
        f"SOW manually edited by {current_user.name}",
        current_user.user_id
    )
    
    return {"success": True, "sow": updated_sow}


@router.get("/v2/field-services/jobs/{job_id}/sow/download")
async def download_sow(
    job_id: str,
    format: str = Query("pdf", description="Format: pdf, docx, markdown, json"),
    current_user=Depends(get_current_user)
):
    """
    Download the SOW in various formats.
    Supports: PDF, DOCX, Markdown, JSON.
    """
    from fastapi.responses import StreamingResponse
    
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    sow_data = job.get("sow_data")
    if not sow_data:
        raise HTTPException(status_code=404, detail="No SOW generated yet")
    
    job_name = job.get("name", "SOW").replace(" ", "_")
    
    if format == "pdf":
        from services.sow_document_generator import generate_sow_pdf
        try:
            pdf_buffer = generate_sow_pdf(sow_data)
            return StreamingResponse(
                pdf_buffer,
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename=SOW_{job_name}_{job_id}.pdf"}
            )
        except ImportError as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    elif format == "docx":
        from services.sow_document_generator import generate_sow_docx
        try:
            docx_buffer = generate_sow_docx(sow_data)
            return StreamingResponse(
                docx_buffer,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f"attachment; filename=SOW_{job_name}_{job_id}.docx"}
            )
        except ImportError as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    elif format == "markdown":
        from services.sow_generator import sow_to_markdown
        markdown_content = sow_to_markdown(sow_data)
        from io import BytesIO
        buffer = BytesIO(markdown_content.encode('utf-8'))
        return StreamingResponse(
            buffer,
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename=SOW_{job_name}_{job_id}.md"}
        )
    
    elif format == "json":
        from services.sow_generator import sow_to_json
        json_content = sow_to_json(sow_data)
        from io import BytesIO
        buffer = BytesIO(json_content.encode('utf-8'))
        return StreamingResponse(
            buffer,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=SOW_{job_name}_{job_id}.json"}
        )
    
    else:
        raise HTTPException(status_code=400, detail="Invalid format. Use: pdf, docx, markdown, json")


@router.post("/v2/field-services/jobs/{job_id}/sow/email")
async def email_sow(
    job_id: str,
    email_data: dict,
    current_user=Depends(get_current_user)
):
    """
    Email the SOW to specified recipients.
    Uses Resend integration.
    
    Body:
    {
      "recipients": ["email1@example.com", "email2@example.com"],
      "cc": ["optional@example.com"],
      "subject": "Optional custom subject",
      "message": "Optional custom message body",
      "format": "pdf"  // pdf, docx, markdown, json
    }
    """
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    sow_data = job.get("sow_data")
    if not sow_data:
        raise HTTPException(status_code=404, detail="No SOW generated yet")
    
    recipients = email_data.get("recipients", [])
    if not recipients:
        raise HTTPException(status_code=400, detail="At least one recipient required")
    
    cc = email_data.get("cc", [])
    subject = email_data.get("subject") or f"Statement of Work - {job.get('name')}"
    message = email_data.get("message") or f"Please find attached the Statement of Work for {job.get('name')}."
    format_type = email_data.get("format", "pdf")
    
    # Generate attachment
    attachment_buffer = None
    filename = None
    mimetype = None
    
    job_name = job.get("name", "SOW").replace(" ", "_")
    
    if format_type == "pdf":
        from services.sow_document_generator import generate_sow_pdf
        try:
            attachment_buffer = generate_sow_pdf(sow_data)
            filename = f"SOW_{job_name}_{job_id}.pdf"
            mimetype = "application/pdf"
        except ImportError as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    elif format_type == "docx":
        from services.sow_document_generator import generate_sow_docx
        try:
            attachment_buffer = generate_sow_docx(sow_data)
            filename = f"SOW_{job_name}_{job_id}.docx"
            mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except ImportError as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    elif format_type == "markdown":
        from services.sow_generator import sow_to_markdown
        markdown_content = sow_to_markdown(sow_data)
        from io import BytesIO
        attachment_buffer = BytesIO(markdown_content.encode('utf-8'))
        filename = f"SOW_{job_name}_{job_id}.md"
        mimetype = "text/markdown"
    
    elif format_type == "json":
        from services.sow_generator import sow_to_json
        json_content = sow_to_json(sow_data)
        from io import BytesIO
        attachment_buffer = BytesIO(json_content.encode('utf-8'))
        filename = f"SOW_{job_name}_{job_id}.json"
        mimetype = "application/json"
    
    # Send email using Resend
    try:
        import os
        import resend
        resend.api_key = os.environ.get("RESEND_API_KEY")
        
        # Prepare attachment
        import base64
        attachment_buffer.seek(0)
        attachment_content = base64.b64encode(attachment_buffer.read()).decode()
        
        # Send email
        email_response = resend.Emails.send({
            "from": "AegisAxiom <noreply@bosscrm.emergentai.tech>",
            "to": recipients,
            "cc": cc if cc else None,
            "subject": subject,
            "html": f"""
            <p>{message}</p>
            <hr/>
            <p><small>Generated by AegisAxiom Field Services Module</small></p>
            """,
            "attachments": [{
                "filename": filename,
                "content": attachment_content
            }]
        })
        
        # Log activity
        await log_job_activity(
            job_id, "sow_emailed",
            f"SOW emailed to {', '.join(recipients)} by {current_user.name}",
            current_user.user_id
        )
        
        return {
            "success": True,
            "email_id": email_response.get("id"),
            "recipients": recipients,
            "message": "SOW emailed successfully"
        }
    
    except Exception as e:
        logger.error(f"Failed to send SOW email: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send email: {str(e)}")


@router.get("/v2/field-services/surveys")
async def get_survey_jobs(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    with_sow: Optional[bool] = None,
    test_only: bool = False,
    current_user=Depends(get_current_user)
):
    """
    Get all survey-type jobs.
    Can filter by status and whether they have generated SOWs.
    """
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    filt = {"job_category": "survey"}
    
    if status:
        filt["status"] = status
    
    if with_sow is True:
        filt["sow_data"] = {"$exists": True, "$ne": None}
    elif with_sow is False:
        filt["$or"] = [
            {"sow_data": {"$exists": False}},
            {"sow_data": None}
        ]
    
    # Test data filter: default shows real data only, toggle shows test data only
    filt["is_test_data"] = True if test_only else {"$ne": True}

    jobs = await db.field_jobs.find(filt, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.field_jobs.count_documents(filt)
    
    return {"items": jobs, "total": total, "skip": skip, "limit": limit}



# ========================================
# PHASE 3B: CHECK-IN/CHECK-OUT FORMS & AUTO TIME ENTRIES
# ========================================

@router.post("/v2/field-services/jobs/{job_id}/check-in")
async def check_in_job(
    job_id: str,
    check_in_data: dict,
    current_user=Depends(get_current_user)
):
    """
    Contractor checks in to a job.
    Records check-in time, checklist responses, and optional photos.
    Sets job status to "in_progress".
    """
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Update checklist responses
    checklist_checkin = job.get("checklist_checkin", [])
    checklist_responses = check_in_data.get("checklist_responses", {})
    
    for item in checklist_checkin:
        item_id = item.get("id")
        if item_id in checklist_responses:
            item["value"] = checklist_responses[item_id].get("value")
            item["notes"] = checklist_responses[item_id].get("notes", "")
    
    # Store check-in data
    check_in_time = datetime.now(timezone.utc)
    update_data = {
        "check_in_time": check_in_time,
        "check_in_notes": check_in_data.get("notes", ""),
        "checklist_checkin": checklist_checkin,
        "status": "in_progress",
        "updated_at": check_in_time,
    }
    
    # Handle photo uploads (if any)
    photos = check_in_data.get("photos", [])
    if photos:
        existing_photos = job.get("photos", [])
        for photo in photos:
            photo["uploaded_at"] = check_in_time.isoformat()
            photo["uploaded_by"] = current_user.user_id
            photo["stage"] = "check_in"
            existing_photos.append(photo)
        update_data["photos"] = existing_photos
    
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    
    # Log activity
    await log_job_activity(
        job_id, "check_in",
        f"Contractor checked in: {current_user.name}",
        current_user.user_id,
        metadata={"check_in_time": check_in_time.isoformat()}
    )
    
    # Trigger arrival notification if enabled
    updated_job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if updated_job.get("customer_contact_email") and await is_notification_enabled("on_arrival_customer"):
        try:
            await send_customer_arrival_notification(
                to=updated_job["customer_contact_email"],
                contact_name=updated_job.get("customer_contact_name", "Customer"),
                job_name=updated_job.get("name", ""),
                tech_name=current_user.name
            )
            await log_job_activity(
                job_id, "email_sent",
                "Arrival notification sent to customer",
                "system"
            )
        except Exception as e:
            logger.error(f"Failed to send arrival notification: {e}")
    
    return {"success": True, "job": updated_job, "check_in_time": check_in_time.isoformat()}


@router.post("/v2/field-services/jobs/{job_id}/check-out")
async def check_out_job(
    job_id: str,
    check_out_data: dict,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user)
):
    """
    Contractor checks out of a job.
    Records check-out time, checklist responses, calculates hours worked,
    and AUTOMATICALLY CREATES A TIME ENTRY in the existing time tracking system.
    Sets job status to "completed".
    """
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.get("check_in_time"):
        raise HTTPException(status_code=400, detail="Must check in before checking out")
    
    # Update checklist responses
    checklist_checkout = job.get("checklist_checkout", [])
    checklist_responses = check_out_data.get("checklist_responses", {})
    
    for item in checklist_checkout:
        item_id = item.get("id")
        if item_id in checklist_responses:
            item["value"] = checklist_responses[item_id].get("value")
            item["notes"] = checklist_responses[item_id].get("notes", "")
    
    # Calculate hours worked
    check_out_time = datetime.now(timezone.utc)
    check_in_time = job["check_in_time"]
    if isinstance(check_in_time, str):
        check_in_time = datetime.fromisoformat(check_in_time)
    if check_in_time.tzinfo is None:
        check_in_time = check_in_time.replace(tzinfo=timezone.utc)
    
    hours_worked = (check_out_time - check_in_time).total_seconds() / 3600
    hours_worked = round(hours_worked, 2)
    
    # Store check-out data
    update_data = {
        "check_out_time": check_out_time,
        "check_out_notes": check_out_data.get("notes", ""),
        "checklist_checkout": checklist_checkout,
        "actual_hours": hours_worked,
        "status": "completed",
        "updated_at": check_out_time,
    }
    
    # Handle photo uploads (if any)
    photos = check_out_data.get("photos", [])
    if photos:
        existing_photos = job.get("photos", [])
        for photo in photos:
            photo["uploaded_at"] = check_out_time.isoformat()
            photo["uploaded_by"] = current_user.user_id
            photo["stage"] = "check_out"
            existing_photos.append(photo)
        update_data["photos"] = existing_photos
    
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    
    # ========================================
    # AUTO-CREATE TIME ENTRY
    # ========================================
    time_entry_id = None
    time_entry_created = False
    
    # Only create time entry if job has a contract
    contract_id = job.get("contract_id")
    if contract_id and hours_worked > 0:
        try:
            # Get contract details for rates
            contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
            
            # Find or create project for this job
            project_id = None
            project_name = f"Field Job: {job.get('name')}"
            
            # Check if there's already a project for this contract
            existing_project = await db.projects.find_one(
                {"contract_id": contract_id, "name": project_name},
                {"_id": 0, "project_id": 1}
            )
            
            if existing_project:
                project_id = existing_project["project_id"]
            else:
                # Create a project for this field job. Conform to the strict
                # projects schema (Wave 2 ingestion): project_type, service_line,
                # pm_id, methodology_config, etc. are required.
                project_id = f"proj_{uuid.uuid4().hex[:12]}"
                project_doc = {
                    "project_id": project_id,
                    "name": project_name,
                    "description": None,
                    "project_type": "waterfall",
                    "service_line": "other",
                    "status": "active",
                    "health": "green",
                    "pm_id": current_user.user_id,
                    "assigned_user_ids": [],
                    "start_date": None,
                    "target_end_date": None,
                    "actual_end_date": None,
                    "template_id": None,
                    "methodology_config": {"phases": []},
                    "contract_id": contract_id,
                    "account_id": job.get("account_id"),
                    "field_job_id": job_id,  # Link back to field job
                    "created_at": check_out_time,
                    "updated_at": check_out_time,
                    "created_by": current_user.user_id,
                }
                await db.projects.insert_one(project_doc)
            
            # Get resource type and rates from contract
            resource_type_id = None
            resource_type_name = "Field Services"
            cost_rate = 0.0
            sell_rate = 0.0
            
            if contract:
                # Try to find matching resource type for the contractor
                resource_types = contract.get("resource_types", [])
                resources = contract.get("resources", [])
                
                # Find contractor's resource assignment
                contractor_resource = next(
                    (r for r in resources if r.get("user_id") == current_user.user_id),
                    None
                )
                
                if contractor_resource:
                    resource_type_id = contractor_resource.get("resource_type_id")
                    cost_rate = float(contractor_resource.get("cost_rate", 0))
                    
                    # Get sell rate from resource type
                    if resource_type_id:
                        matching_rt = next(
                            (rt for rt in resource_types if rt.get("id") == resource_type_id),
                            None
                        )
                        if matching_rt:
                            sell_rate = float(matching_rt.get("sell_rate", 0))
                            resource_type_name = matching_rt.get("name", "Field Services")
                else:
                    # Fallback: use contract's default hourly rate if BOH/BOA
                    if contract.get("contract_type") in ["boh", "boa"]:
                        sell_rate = float(contract.get("hourly_rate", 0))
            
            # Calculate margin
            margin_percent = 0.0
            if sell_rate > 0:
                margin_percent = ((sell_rate - cost_rate) / sell_rate) * 100
            
            # Create time entry
            time_entry_id = f"te_{uuid.uuid4().hex[:12]}"
            date_str = check_in_time.date().isoformat()
            
            # Get week boundaries
            from datetime import timedelta
            check_in_date = check_in_time.date()
            week_start = check_in_date - timedelta(days=check_in_date.weekday())
            week_end = week_start + timedelta(days=6)
            
            time_entry_doc = {
                "entry_id": time_entry_id,
                "user_id": current_user.user_id,
                "user_name": current_user.name,
                "project_id": project_id,
                "project_name": project_name,
                "contract_id": contract_id,
                "account_id": job.get("account_id"),
                "resource_type_id": resource_type_id,
                "resource_type_name": resource_type_name,
                "date": date_str,
                "hours": hours_worked,
                "notes": f"Field Job: {job.get('name')} - Auto-generated from check-in/check-out",
                "note_quality": "good",
                "is_billable": True,
                "status": "submitted",  # Auto-submit
                "cost_rate": cost_rate,
                "sell_rate": sell_rate,
                "margin_percent": margin_percent,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "is_late": False,
                "created_by": "system",  # Auto-created
                "locked": False,
                "submitted_at": check_out_time,
                "requires_admin_approval": False,
                "is_test_data": job.get("is_test_data", False),
                "created_at": check_out_time,
                "updated_at": check_out_time,
            }
            
            await db.time_entries.insert_one(time_entry_doc)
            time_entry_created = True
            
            # Update contract hours if BOH
            if contract and contract.get("contract_type") == "boh":
                await db.contracts.update_one(
                    {"contract_id": contract_id},
                    {
                        "$inc": {"hours_consumed": hours_worked},
                        "$set": {
                            "last_usage_date": check_out_time,
                            "updated_at": check_out_time
                        }
                    }
                )
            
            logger.info(f"Auto-created time entry {time_entry_id} for job {job_id}: {hours_worked} hours")
        
        except Exception as e:
            logger.error(f"Failed to auto-create time entry for job {job_id}: {e}")
            # Continue without failing the check-out
    
    # Log activity
    await log_job_activity(
        job_id, "check_out",
        f"Contractor checked out: {current_user.name} ({hours_worked}h worked)",
        current_user.user_id,
        metadata={
            "check_out_time": check_out_time.isoformat(),
            "hours_worked": hours_worked,
            "time_entry_created": time_entry_created,
            "time_entry_id": time_entry_id
        }
    )
    
    # Trigger completion notification if enabled
    updated_job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if updated_job.get("customer_contact_email") and await is_notification_enabled("on_completed_customer"):
        background_tasks.add_task(
            send_customer_job_completed,
            to=updated_job["customer_contact_email"],
            contact_name=updated_job.get("customer_contact_name", "Customer"),
            job_name=updated_job.get("name", ""),
            account_name=updated_job.get("account_name", "")
        )
        await log_job_activity(
            job_id, "email_sent",
            "Completion notification sent to customer",
            "system"
        )
    
    return {
        "success": True,
        "job": updated_job,
        "check_out_time": check_out_time.isoformat(),
        "hours_worked": hours_worked,
        "time_entry_created": time_entry_created,
        "time_entry_id": time_entry_id
    }


def _check_token_expiry(job: dict):
    """Raise 410 Gone if the public access token has expired."""
    expires = job.get("public_access_token_expires")
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp_dt:
                raise HTTPException(status_code=410, detail="Public access link has expired. Please request a new link.")
        except (ValueError, TypeError):
            pass  # If expiry is malformed, allow access (backward compat with pre-expiry tokens)


@router.get("/v2/field-services/jobs/{job_id}/public-access")
async def get_public_job_access(job_id: str, token: str = Query(...)):
    """
    Get job details for public form access (contractor check-in/check-out).
    Requires a valid access token generated for the job.
    No authentication required - contractors can access via link.
    """
    db = get_db()

    # Verify token
    job = await db.field_jobs.find_one(
        {"job_id": job_id, "public_access_token": token},
        {"_id": 0}
    )

    if not job:
        raise HTTPException(status_code=404, detail="Job not found or invalid access token")

    _check_token_expiry(job)
    
    # Return limited job data for public form
    return {
        "job_id": job["job_id"],
        "name": job.get("name"),
        "account_name": job.get("account_name"),
        "scheduled_date": job.get("scheduled_date"),
        "status": job.get("status"),
        "job_type_name": job.get("job_type_name"),
        "checklist_checkin": job.get("checklist_checkin", []),
        "checklist_checkout": job.get("checklist_checkout", []),
        "check_in_time": job.get("check_in_time"),
        "description": job.get("description"),
    }


@router.post("/v2/field-services/jobs/{job_id}/check-in-public")
async def check_in_job_public(
    job_id: str,
    check_in_data: dict,
    token: str = Query(...)
):
    """
    Public check-in endpoint (no auth required).
    Contractors access this via secure link with token.
    """
    db = get_db()
    
    # Verify token and get job
    job = await db.field_jobs.find_one(
        {"job_id": job_id, "public_access_token": token},
        {"_id": 0}
    )
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or invalid access token")

    _check_token_expiry(job)

    # Get contractor info from token metadata
    contractor_id = job.get("assigned_tech_id", "unknown")
    contractor_name = job.get("assigned_tech_name", "Contractor")

    # Update checklist responses
    checklist_checkin = job.get("checklist_checkin", [])
    checklist_responses = check_in_data.get("checklist_responses", {})
    
    for item in checklist_checkin:
        item_id = item.get("id")
        if item_id in checklist_responses:
            item["value"] = checklist_responses[item_id].get("value")
            item["notes"] = checklist_responses[item_id].get("notes", "")
    
    # Store check-in data
    check_in_time = datetime.now(timezone.utc)
    update_data = {
        "check_in_time": check_in_time,
        "check_in_notes": check_in_data.get("notes", ""),
        "checklist_checkin": checklist_checkin,
        "status": "in_progress",
        "updated_at": check_in_time,
    }
    
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    
    # Log activity
    await log_job_activity(
        job_id, "check_in",
        f"Contractor checked in: {contractor_name} (via public form)",
        contractor_id,
        metadata={"check_in_time": check_in_time.isoformat(), "access_method": "public_form"}
    )
    
    return {"success": True, "check_in_time": check_in_time.isoformat()}


@router.post("/v2/field-services/jobs/{job_id}/check-out-public")
async def check_out_job_public(
    job_id: str,
    check_out_data: dict,
    token: str = Query(...)
):
    """
    Public check-out endpoint (no auth required).
    Contractors access this via secure link with token.
    Automatically creates time entry.
    """
    db = get_db()
    
    # Verify token and get job
    job = await db.field_jobs.find_one(
        {"job_id": job_id, "public_access_token": token},
        {"_id": 0}
    )
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or invalid access token")

    _check_token_expiry(job)

    if not job.get("check_in_time"):
        raise HTTPException(status_code=400, detail="Must check in before checking out")

    # Get contractor info
    contractor_id = job.get("assigned_tech_id", "unknown")
    contractor_name = job.get("assigned_tech_name", "Contractor")
    
    # Update checklist responses
    checklist_checkout = job.get("checklist_checkout", [])
    checklist_responses = check_out_data.get("checklist_responses", {})
    
    for item in checklist_checkout:
        item_id = item.get("id")
        if item_id in checklist_responses:
            item["value"] = checklist_responses[item_id].get("value")
            item["notes"] = checklist_responses[item_id].get("notes", "")
    
    # Calculate hours worked
    check_out_time = datetime.now(timezone.utc)
    check_in_time = job["check_in_time"]
    if isinstance(check_in_time, str):
        check_in_time = datetime.fromisoformat(check_in_time)
    if check_in_time.tzinfo is None:
        check_in_time = check_in_time.replace(tzinfo=timezone.utc)
    
    hours_worked = (check_out_time - check_in_time).total_seconds() / 3600
    hours_worked = round(hours_worked, 2)
    
    # Store check-out data
    update_data = {
        "check_out_time": check_out_time,
        "check_out_notes": check_out_data.get("notes", ""),
        "checklist_checkout": checklist_checkout,
        "actual_hours": hours_worked,
        "status": "completed",
        "updated_at": check_out_time,
    }
    
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    
    # Auto-create time entry (same logic as authenticated endpoint)
    time_entry_created = False
    time_entry_id = None
    
    contract_id = job.get("contract_id")
    if contract_id and hours_worked > 0:
        try:
            # Get contract
            contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
            
            # Find or create project
            project_id = None
            project_name = f"Field Job: {job.get('name')}"
            existing_project = await db.projects.find_one(
                {"contract_id": contract_id, "name": project_name},
                {"_id": 0, "project_id": 1}
            )
            
            if existing_project:
                project_id = existing_project["project_id"]
            else:
                project_id = f"proj_{uuid.uuid4().hex[:12]}"
                # Public endpoint — no current_user. Fall back to the
                # assigned tech (contractor_id is captured above) for
                # both pm_id and created_by; if absent, "system".
                actor_id = contractor_id or "system"
                await db.projects.insert_one({
                    "project_id": project_id,
                    "name": project_name,
                    "description": None,
                    "project_type": "waterfall",
                    "service_line": "other",
                    "status": "active",
                    "health": "green",
                    "pm_id": actor_id,
                    "assigned_user_ids": [],
                    "start_date": None,
                    "target_end_date": None,
                    "actual_end_date": None,
                    "template_id": None,
                    "methodology_config": {"phases": []},
                    "contract_id": contract_id,
                    "account_id": job.get("account_id"),
                    "field_job_id": job_id,
                    "created_at": check_out_time,
                    "updated_at": check_out_time,
                    "created_by": actor_id,
                })
            
            # Get rates
            cost_rate = 0.0
            sell_rate = 0.0
            resource_type_name = "Field Services"
            
            if contract:
                resources = contract.get("resources", [])
                contractor_resource = next(
                    (r for r in resources if r.get("user_id") == contractor_id),
                    None
                )
                if contractor_resource:
                    cost_rate = float(contractor_resource.get("cost_rate", 0))
                    resource_type_id = contractor_resource.get("resource_type_id")
                    if resource_type_id:
                        resource_types = contract.get("resource_types", [])
                        matching_rt = next(
                            (rt for rt in resource_types if rt.get("id") == resource_type_id),
                            None
                        )
                        if matching_rt:
                            sell_rate = float(matching_rt.get("sell_rate", 0))
                            resource_type_name = matching_rt.get("name", "Field Services")
            
            margin_percent = 0.0
            if sell_rate > 0:
                margin_percent = ((sell_rate - cost_rate) / sell_rate) * 100
            
            # Create time entry
            time_entry_id = f"te_{uuid.uuid4().hex[:12]}"
            date_str = check_in_time.date().isoformat()
            
            from datetime import timedelta
            check_in_date = check_in_time.date()
            week_start = check_in_date - timedelta(days=check_in_date.weekday())
            week_end = week_start + timedelta(days=6)
            
            await db.time_entries.insert_one({
                "entry_id": time_entry_id,
                "user_id": contractor_id,
                "user_name": contractor_name,
                "project_id": project_id,
                "project_name": project_name,
                "contract_id": contract_id,
                "account_id": job.get("account_id"),
                "resource_type_id": None,
                "resource_type_name": resource_type_name,
                "date": date_str,
                "hours": hours_worked,
                "notes": f"Field Job: {job.get('name')} - Auto-generated from public check-in/check-out",
                "note_quality": "good",
                "is_billable": True,
                "status": "submitted",
                "cost_rate": cost_rate,
                "sell_rate": sell_rate,
                "margin_percent": margin_percent,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "is_late": False,
                "created_by": "system",
                "locked": False,
                "submitted_at": check_out_time,
                "requires_admin_approval": False,
                "is_test_data": job.get("is_test_data", False),
                "created_at": check_out_time,
                "updated_at": check_out_time,
            })
            time_entry_created = True
            
            # Update BOH contract hours
            if contract and contract.get("contract_type") == "boh":
                await db.contracts.update_one(
                    {"contract_id": contract_id},
                    {
                        "$inc": {"hours_consumed": hours_worked},
                        "$set": {"last_usage_date": check_out_time, "updated_at": check_out_time}
                    }
                )
        
        except Exception as e:
            logger.error(f"Failed to auto-create time entry (public): {e}")
    
    # Log activity
    await log_job_activity(
        job_id, "check_out",
        f"Contractor checked out: {contractor_name} ({hours_worked}h worked) (via public form)",
        contractor_id,
        metadata={
            "check_out_time": check_out_time.isoformat(),
            "hours_worked": hours_worked,
            "time_entry_created": time_entry_created,
            "access_method": "public_form"
        }
    )
    
    return {
        "success": True,
        "check_out_time": check_out_time.isoformat(),
        "hours_worked": hours_worked,
        "time_entry_created": time_entry_created
    }


@router.post("/v2/field-services/jobs/{job_id}/generate-public-link")
async def generate_public_access_link(
    job_id: str,
    current_user=Depends(get_current_user)
):
    """
    Generate a secure public access link for contractors.
    Allows check-in/check-out without full authentication.
    PM or Admin only.
    """
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Generate secure token with 7-day expiration
    import secrets
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    # Store token and expiration in job
    await db.field_jobs.update_one(
        {"job_id": job_id},
        {"$set": {
            "public_access_token": token,
            "public_access_token_expires": expires_at.isoformat(),
            "updated_at": datetime.now(timezone.utc),
        }}
    )

    # Construct public URL (frontend will handle routing)
    public_url = f"/field-services/public/{job_id}?token={token}"

    await log_job_activity(
        job_id, "public_link_generated",
        f"Public access link generated by {current_user.name} (expires {expires_at.strftime('%Y-%m-%d')})",
        current_user.user_id
    )

    return {
        "success": True,
        "public_url": public_url,
        "token": token,
        "expires": expires_at.isoformat()
    }


@router.delete("/v2/field-services/jobs/{job_id}/revoke-public-link")
async def revoke_public_access_link(
    job_id: str,
    current_user=Depends(get_current_user)
):
    """
    Revoke public access link for a job.
    PM or Admin only.
    """
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    await db.field_jobs.update_one(
        {"job_id": job_id},
        {
            "$unset": {"public_access_token": "", "public_access_token_expires": ""},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        }
    )
    
    await log_job_activity(
        job_id, "public_link_revoked",
        f"Public access link revoked by {current_user.name}",
        current_user.user_id
    )
    
    return {"success": True, "message": "Public access link revoked"}
