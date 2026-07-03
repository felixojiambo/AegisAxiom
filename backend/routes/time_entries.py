"""Time Management — weekly timesheets, 15-min increments, approval workflow."""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone, timedelta, date
from typing import Optional, List
import uuid
import logging
import math

from database import get_db
from auth_helper import get_current_user, has_perm
from services.audit_service import log_action
from services.policy_engine import evaluate_policies
from services.policy_helpers import enforce_policy_result
from services.field_registry import register_fields, register_module_events, register_module_config
from services.field_options import BILLING_TYPES, TIME_ENTRY_STATUSES, USER_ROLES
from services.time_entry_rates import resolve_rate_snapshots
from services.contract_debit import (
    VALID_BILLING_TYPES,
    apply_time_entry_to_contract,
)
from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
from services.event_bus import emit_event

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_config("time_tracking", "time_entries", "entry_id", "Operations")
register_module_events("time_tracking", "time_entry", ["created", "updated", "deleted", "submitted", "approved", "rejected"])

# Register fields available for policy evaluation
register_fields("time_tracking", "time_entry", [
    {"name": "hours", "type": "number", "label": "Hours", "description": "Hours worked on this time entry"},
    {"name": "notes", "type": "string", "label": "Notes", "description": "Work description notes for the time entry"},
    {"name": "status", "type": "string", "label": "Status", "options": TIME_ENTRY_STATUSES, "description": "Approval workflow status (draft, submitted, pm_approved, admin_approved)"},
    {"name": "date", "type": "date", "label": "Entry Date", "description": "Date the work was performed"},
    {"name": "is_billable", "type": "boolean", "label": "Is Billable", "description": "Whether this time entry is billable to the client"},
    {"name": "is_test_data", "type": "boolean", "label": "Is Test Data", "description": "Flag indicating this is test/demo data, not real work"},
    # Computed fields (calculated by route handler before policy evaluation)
    {"name": "note_word_count", "type": "number", "label": "Note Word Count", "computed": True, "description": "Number of words in the notes field"},
    {"name": "is_locked", "type": "boolean", "label": "Week Is Locked", "computed": True, "description": "Whether the timesheet week is locked for edits"},
    {"name": "hours_minutes_remainder", "type": "number", "label": "Hours Minute Remainder", "computed": True, "description": "Fractional minutes remainder after rounding to 10-min increments"},
    {"name": "user_role", "type": "string", "label": "User Role", "computed": True, "options": USER_ROLES, "description": "Role of the user submitting the time entry"},
    {"name": "user_has_admin_perm", "type": "boolean", "label": "User Has Admin Permission", "computed": True, "description": "Whether the user has admin-level time tracking permissions"},
    # Wave 0 (contracts-time-mgmt) — field registry only; derivation lives in Wave 3.
    {"name": "billing_type", "type": "string", "label": "Billing Type", "options": BILLING_TYPES, "description": "Per-entry billing classification (billable, non_billable, fixed_fee). Derivation from is_billable lands in Wave 3"},
    {"name": "cost_rate_snapshot", "type": "number", "label": "Cost Rate Snapshot", "description": "Contractor cost rate frozen on the entry for AP reconciliation"},
    {"name": "sell_rate_snapshot", "type": "number", "label": "Sell Rate Snapshot", "description": "Customer sell rate frozen on the entry for AR reconciliation"},
    {"name": "effective_billing_mode", "type": "string", "label": "Effective Billing Mode", "description": "Resolved billing mode the entry was applied under (e.g. boh, boa, overflow)"},
])


def _get_week_bounds(d: date):
    """Return Monday and Sunday of the week containing date d."""
    monday = d - timedelta(days=d.weekday())  # Monday
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def _validate_hours(hours: float):
    """Validate: 10-min increments (0.167) and max 4 hours per entry."""
    if hours <= 0:
        raise HTTPException(status_code=400, detail="Hours must be greater than 0")
    if hours > 4.0:
        raise HTTPException(status_code=400, detail="Maximum 4 hours per time entry to limit dispute exposure")
    # 10 minutes = 1/6 hour = 0.1667
    # Valid increments: 0.17, 0.33, 0.50, 0.67, 0.83, 1.00, etc.
    # Use tolerance for floating-point comparison
    minutes = hours * 60
    if abs(minutes - round(minutes / 10) * 10) > 0.5:
        raise HTTPException(status_code=400, detail="Hours must be in 10-minute increments (0.17, 0.33, 0.50, 0.67, 0.83, 1.00, etc.)")


_LOCK_DEFAULTS = {"lock_after_days": 8, "lock_time_hour": 22, "lock_time_minute": 0}


async def _get_active_lock_rule(db) -> dict:
    """Return the active time-locking rule with safe defaults for missing fields."""
    rule = await db.time_locking_rules.find_one({"enabled": True}, {"_id": 0})
    if not rule:
        return dict(_LOCK_DEFAULTS)
    return {
        "lock_after_days": rule.get("lock_after_days", _LOCK_DEFAULTS["lock_after_days"]),
        "lock_time_hour": rule.get("lock_time_hour", _LOCK_DEFAULTS["lock_time_hour"]),
        "lock_time_minute": rule.get("lock_time_minute", _LOCK_DEFAULTS["lock_time_minute"]),
    }


def _lock_datetime_for_week(week_start_str: str, rule: dict) -> datetime:
    """Compute the UTC datetime at which a given week locks."""
    ws = date.fromisoformat(week_start_str)
    lock_date = ws + timedelta(days=rule["lock_after_days"])
    return datetime(
        lock_date.year, lock_date.month, lock_date.day,
        rule["lock_time_hour"], rule["lock_time_minute"],
        tzinfo=timezone.utc,
    )


async def _is_locked(week_start_str: str, db) -> bool:
    """Check if a week's entries should be locked using configurable rules."""
    try:
        rule = await _get_active_lock_rule(db)
        lock_dt = _lock_datetime_for_week(week_start_str, rule)
        return datetime.now(timezone.utc) > lock_dt
    except (ValueError, TypeError):
        return False


def _check_note_quality(notes: str) -> dict:
    """Basic intelligence around note quality."""
    if not notes or not notes.strip():
        return {"quality": "poor", "message": "Notes are required for every time entry"}
    words = notes.strip().split()
    if len(words) < 3:
        return {"quality": "poor", "message": "Please provide more detail (at least 3 words describing what you worked on)"}
    if len(words) < 6:
        return {"quality": "fair", "message": "Consider adding more detail for clearer billing records"}
    return {"quality": "good", "message": ""}


# ---- CRUD ----

@router.get("/time-entries")
async def get_time_entries(
    skip: int = 0,
    limit: int = 200,
    project_id: Optional[str] = None,
    contract_id: Optional[str] = None,
    user_id: Optional[str] = None,
    week_start: Optional[str] = None,
    status: Optional[str] = None,
    account_id: Optional[str] = None,
    is_test_data: Optional[bool] = None,  # Filter for test data
    current_user=Depends(get_current_user),
):
    db = get_db()
    filt = {}

    # RBAC: Role-based filtering
    is_admin = has_perm(current_user, "*")
    
    if is_admin:
        # Admins: See ALL time entries across all contracts, projects, and PMs
        pass  # No filter needed
    
    elif has_perm(current_user, "time.view.all"):
        # Directors/Finance with time.view.all: See all entries
        pass
    
    else:
        # Build access list based on role
        accessible_conditions = []
        
        # 1. Always see own entries
        accessible_conditions.append({"user_id": current_user.user_id})
        
        # 2. Project Managers: See all entries for their projects
        if has_perm(current_user, "time.approve") or has_perm(current_user, "projects.view.own"):
            my_pm_projects = await db["projects"].find(
                {"pm_id": current_user.user_id},
                {"_id": 0, "project_id": 1}
            ).to_list(500)
            if my_pm_projects:
                my_pm_project_ids = [p["project_id"] for p in my_pm_projects]
                accessible_conditions.append({"project_id": {"$in": my_pm_project_ids}})
        
        # 3. Sales Reps/Deal Owners: See entries for contracts linked to their deals
        if current_user.role in ["sales_rep", "director"]:
            my_deals = await db.deals.find(
                {"$or": [
                    {"deal_owner_id": current_user.user_id},
                    {"assigned_sales_rep_id": current_user.user_id}
                ]},
                {"_id": 0, "deal_id": 1}
            ).to_list(500)
            
            if my_deals:
                my_deal_ids = [d["deal_id"] for d in my_deals]
                # Find contracts linked to these deals
                my_contracts = await db.contracts.find(
                    {"deal_ids": {"$in": my_deal_ids}},
                    {"_id": 0, "contract_id": 1}
                ).to_list(500)
                
                if my_contracts:
                    my_contract_ids = [c["contract_id"] for c in my_contracts]
                    accessible_conditions.append({"contract_id": {"$in": my_contract_ids}})
        
        # 4. Agents of Record: See entries for contracts they're assigned to
        if current_user.role in ["agent", "director"]:
            my_agent_deals = await db.deals.find(
                {"assigned_agent_id": current_user.user_id},
                {"_id": 0, "deal_id": 1}
            ).to_list(500)
            
            if my_agent_deals:
                my_agent_deal_ids = [d["deal_id"] for d in my_agent_deals]
                my_agent_contracts = await db.contracts.find(
                    {"deal_ids": {"$in": my_agent_deal_ids}},
                    {"_id": 0, "contract_id": 1}
                ).to_list(500)
                
                if my_agent_contracts:
                    my_agent_contract_ids = [c["contract_id"] for c in my_agent_contracts]
                    accessible_conditions.append({"contract_id": {"$in": my_agent_contract_ids}})
        
        # 5. Customers/Account Users: See entries for their active contracts
        if current_user.role == "customer" and current_user.account_id:
            my_customer_contracts = await db.contracts.find(
                {"account_id": current_user.account_id, "status": "active"},
                {"_id": 0, "contract_id": 1}
            ).to_list(500)
            
            if my_customer_contracts:
                my_customer_contract_ids = [c["contract_id"] for c in my_customer_contracts]
                accessible_conditions.append({"contract_id": {"$in": my_customer_contract_ids}})
        
        # Combine all accessible conditions with OR
        if accessible_conditions:
            filt["$or"] = accessible_conditions
        else:
            # User has no access - return empty
            filt["_id"] = {"$exists": False}

    # Apply additional filters
    if project_id:
        filt["project_id"] = project_id
    if contract_id:
        filt["contract_id"] = contract_id
    if user_id:
        filt["user_id"] = user_id
    if week_start:
        filt["week_start"] = week_start
    if status:
        filt["status"] = status
    if account_id:
        filt["account_id"] = account_id
    if is_test_data is not None:
        filt["is_test_data"] = is_test_data

    docs = await db.time_entries.find(filt, {"_id": 0}).sort([("date", -1), ("created_at", -1)]).skip(skip).limit(min(limit, 1000)).to_list(min(limit, 1000))
    total = await db.time_entries.count_documents(filt)
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.post("/time-entries")
async def create_time_entry(data: dict, current_user=Depends(get_current_user)):
    db = get_db()

    # Parse hours (decimal) OR derive from start_time/end_time. Snap
    # to the documented 10-minute increment. The pure-function helper
    # in services.time_entry_input is the single source of truth for
    # this normalisation so the route, the tests, and any future
    # bulk-entry path agree. Pre-existing callers that still POST a
    # raw `hours: 1.5` field are unaffected — the helper resolves
    # hours first when present.
    from services.time_entry_input import (
        parse_time_entry_payload, TimeEntryInputError,
    )
    try:
        parsed = parse_time_entry_payload(data)
    except TimeEntryInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    hours = parsed.hours

    notes = data.get("notes", "").strip()
    note_check = _check_note_quality(notes)

    entry_date = data.get("date")
    if not entry_date:
        raise HTTPException(status_code=400, detail="Date is required")

    d = date.fromisoformat(entry_date)
    week_start, week_end = _get_week_bounds(d)

    is_test_data = data.get("is_test_data", False)
    is_locked_week = await _is_locked(week_start, db) if not is_test_data else False

    # Policy engine evaluation — replaces hardcoded _validate_hours, note quality, and lock checks
    minutes = hours * 60
    policy_payload = {
        "hours": hours,
        "hours_minutes_remainder": abs(minutes - round(minutes / 10) * 10),
        "notes": notes,
        "note_word_count": len(notes.split()) if notes else 0,
        "status": "draft",
        "is_locked": is_locked_week,
        "user_role": current_user.role,
        "user_has_admin_perm": has_perm(current_user, "time.entry.admin"),
        "is_test_data": is_test_data,
        "date": entry_date,
    }
    result = await evaluate_policies(db, "time_tracking", "time_entry", "on_create", policy_payload, user_role=current_user.role)
    enforce_policy_result(result)
    # Also evaluate on_submit policies (hour limits, increments, note quality)
    result_submit = await evaluate_policies(db, "time_tracking", "time_entry", "on_submit", policy_payload, user_role=current_user.role)
    enforce_policy_result(result_submit)

    project_id = data.get("project_id")
    contract_id = data.get("contract_id")
    account_id = data.get("account_id")
    resource_type_id = data.get("resource_type_id")  # Which resource type contractor is working as
    
    # Allow PMs/Admins to create entries on behalf of contractors
    target_user_id = data.get("user_id")  # Optional: if provided, create entry for this user
    target_user_name = None
    
    if target_user_id and target_user_id != current_user.user_id:
        # User is creating entry for someone else - check permissions
        if not has_perm(current_user, "time.entry.admin") and not has_perm(current_user, "*"):
            raise HTTPException(status_code=403, detail="You don't have permission to create time entries for other users")
        
        # Fetch target user details
        target_user = await db.users.find_one({"user_id": target_user_id}, {"_id": 0, "name": 1})
        if not target_user:
            raise HTTPException(status_code=404, detail=f"User {target_user_id} not found")
        target_user_name = target_user.get("name", "Unknown")
        
        # Use target user for entry
        entry_user_id = target_user_id
        entry_user_name = target_user_name
    else:
        # User is creating entry for themselves
        entry_user_id = current_user.user_id
        entry_user_name = current_user.name
        target_user_id = None  # Clear to avoid confusion

    # Resolve contract from project if needed
    if project_id and not contract_id:
        project = await db.projects.find_one({"project_id": project_id}, {"_id": 0, "contract_id": 1, "account_id": 1})
        if project:
            contract_id = project.get("contract_id")
            if not account_id:
                account_id = project.get("account_id")
    
    # Validate that the entry user is assigned to this resource type on the contract
    if contract_id and resource_type_id:
        contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0, "resources": 1})
        if contract:
            user_resource_types = [
                r.get("resource_type_id") 
                for r in contract.get("resources", []) 
                if r.get("user_id") == entry_user_id
            ]
            if resource_type_id not in user_resource_types:
                raise HTTPException(
                    status_code=400, 
                    detail=f"{entry_user_name} is not assigned to this resource type on this contract"
                )

    entry_id = f"te_{uuid.uuid4().hex[:12]}"
    # Reuse is_locked_week computed earlier (line 242) — no duplicate DB call
    requires_admin_approval = is_locked_week and has_perm(current_user, "time.entry.admin")

    # Wave 3: billing_type resolution. Allowlist validate when supplied,
    # else derive from contract type / is_billable. is_billable stays
    # in sync on the write so legacy readers continue to work.
    raw_billing_type = data.get("billing_type")
    if raw_billing_type is not None:
        if raw_billing_type not in VALID_BILLING_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid billing_type {raw_billing_type!r}. "
                    f"Must be one of: {', '.join(sorted(VALID_BILLING_TYPES))}"
                ),
            )
        billing_type = raw_billing_type
    else:
        # Pull contract_type once (small projection) so the default
        # branch knows whether we're in a fixed_fee contract.
        contract_doc_for_default = None
        if contract_id:
            contract_doc_for_default = await db.contracts.find_one(
                {"contract_id": contract_id}, {"_id": 0, "contract_type": 1}
            )
        ctype = (contract_doc_for_default or {}).get("contract_type") if contract_doc_for_default else None
        is_billable_in = data.get("is_billable", True)
        if ctype == "fixed_fee":
            billing_type = "fixed_fee"
        elif is_billable_in is False:
            billing_type = "non_billable"
        else:
            billing_type = "billable"
    # Sync is_billable with billing_type so the legacy field stays
    # truthful. fixed_fee bills against a different lane than the BOH
    # block but is still "billable" from the project / cost lens —
    # legacy callers that filter on is_billable get the right answer.
    is_billable_final = billing_type != "non_billable"

    # Wave 3: server-derived rate snapshots. Client-supplied snapshot
    # values are intentionally ignored so a malicious or buggy frontend
    # cannot pin an entry to an off-contract rate.
    #
    # For billable entries with a contract attached, the sell rate is
    # required — without it AR cannot be computed and a downstream
    # invoice batch would be silently zero. The resolver tries the
    # resource_type entry then falls back to contract.hourly_rate;
    # only when BOTH are absent does the route raise 409 with an
    # actionable message. Cost rate may legitimately be missing on a
    # partial contract setup, so it does not gate save.
    #
    # non_billable / fixed_fee entries are allowed to save with
    # missing rates because they do not feed AR.
    rate_snapshots = {"cost_rate_snapshot": None, "sell_rate_snapshot": None}
    if contract_id:
        contract_for_rates = await db.contracts.find_one(
            {"contract_id": contract_id},
            {"_id": 0, "resource_types": 1, "resources": 1, "hourly_rate": 1},
        )
        if contract_for_rates:
            resolved = resolve_rate_snapshots(
                contract_for_rates,
                user_id=entry_user_id,
                resource_type_id=resource_type_id,
            )
            rate_snapshots["cost_rate_snapshot"] = resolved["cost_rate_snapshot"]
            rate_snapshots["sell_rate_snapshot"] = resolved["sell_rate_snapshot"]
            if billing_type == "billable" and resolved["sell_rate_snapshot"] is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Billable time entry cannot be created on this "
                        "contract because no sell rate is configured. "
                        "Add a sell_rate to the matching resource_type or "
                        "set contract.hourly_rate before saving."
                    ),
                )

    doc = {
        "entry_id": entry_id,
        "project_id": project_id,
        "contract_id": contract_id,
        "account_id": account_id,
        "user_id": entry_user_id,
        "user_name": entry_user_name,
        "resource_type_id": resource_type_id,  # Store which type they're working as
        "date": entry_date,
        "hours": hours,
        "notes": notes,
        "note_quality": note_check["quality"],
        "is_billable": is_billable_final,
        "billing_type": billing_type,
        "cost_rate_snapshot": rate_snapshots["cost_rate_snapshot"],
        "sell_rate_snapshot": rate_snapshots["sell_rate_snapshot"],
        "status": "draft",
        "week_start": week_start,
        "week_end": week_end,
        "locked": False,
        "is_late": is_locked_week,
        "requires_admin_approval": requires_admin_approval,
        "created_by": current_user.user_id if target_user_id else None,  # Track who created it
        "created_at": datetime.now(timezone.utc),
        "is_test_data": data.get("is_test_data", False),
        # Zoho-style time input provenance — see
        # services.time_entry_input. `hours` is always the snapped
        # 10-minute boundary value; the raw pre-snap input and the
        # source (hours vs start_end) are kept for audit so a future
        # bug report ("the form said 1.4h but the entry has 1.33h")
        # can be reconciled without re-asking the user.
        "hours_input_raw": parsed.hours_input_raw,
        "duration_source": parsed.duration_source,
        "start_minutes": parsed.start_minutes,
        "end_minutes": parsed.end_minutes,
        # Manual-duration input (May 24, 2026): exact minute count
        # from the user's free-text entry (e.g. 123 for "2h3m"), plus
        # the verbatim text so a future audit can reconcile "the form
        # said 2h3m but the entry has 2h10m" (the 10-min snap on
        # `hours` is intentional for billing). Downstream code that
        # needs precise minutes reads raw_minutes; legacy callers
        # continue to read `hours`.
        "raw_minutes": parsed.raw_minutes,
        "manual_duration_text": parsed.manual_duration_text,
    }

    await db.time_entries.insert_one(doc)
    doc.pop("_id", None)
    await emit_event("time_tracking", "created", record=doc, user_id=current_user.user_id, user_email=current_user.email)
    return {**doc, "note_feedback": note_check}


@router.put("/time-entries/{entry_id}")
async def update_time_entry(entry_id: str, data: dict, current_user=Depends(get_current_user)):
    db = get_db()
    entry = await db.time_entries.find_one({"entry_id": entry_id}, {"_id": 0})
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")

    # Only owner can edit
    if entry["user_id"] != current_user.user_id and not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Only the entry owner can edit")
    if entry.get("locked"):
        raise HTTPException(status_code=400, detail="This entry is locked")

    # Policy engine: check status transition + week locking rules
    is_entry_week_locked = await _is_locked(entry.get("week_start", ""), db)
    policy_payload = {
        "status": entry["status"],
        "is_locked": is_entry_week_locked,
        "user_role": current_user.role,
        "user_has_admin_perm": has_perm(current_user, "time.entry.admin"),
    }
    result = await evaluate_policies(db, "time_tracking", "time_entry", "on_update", policy_payload, user_role=current_user.role)
    enforce_policy_result(result)

    update_data = {}
    if "hours" in data:
        hours = float(data["hours"])
        if hours <= 0:
            raise HTTPException(status_code=400, detail="Hours must be greater than 0")
        # Policy engine: validate hours via on_submit policies
        minutes = hours * 60
        hours_payload = {
            "hours": hours,
            "hours_minutes_remainder": abs(minutes - round(minutes / 10) * 10),
        }
        hours_result = await evaluate_policies(db, "time_tracking", "time_entry", "on_submit", hours_payload, user_role=current_user.role)
        enforce_policy_result(hours_result)
        update_data["hours"] = hours
    if "notes" in data:
        notes = data["notes"].strip()
        note_check = _check_note_quality(notes)
        # Policy engine: validate note quality
        notes_payload = {"note_word_count": len(notes.split()) if notes else 0, "notes": notes}
        notes_result = await evaluate_policies(db, "time_tracking", "time_entry", "on_submit", notes_payload, user_role=current_user.role)
        enforce_policy_result(notes_result)
        update_data["notes"] = notes
        update_data["note_quality"] = note_check["quality"]
    if "is_billable" in data:
        update_data["is_billable"] = bool(data["is_billable"])
    if "project_id" in data:
        update_data["project_id"] = data["project_id"]
    if "date" in data:
        d = date.fromisoformat(data["date"])
        ws, we = _get_week_bounds(d)
        # Policy engine: check lock on target week
        target_locked = await _is_locked(ws, db)
        lock_payload = {
            "is_locked": target_locked,
            "user_has_admin_perm": has_perm(current_user, "time.entry.admin"),
        }
        lock_result = await evaluate_policies(db, "time_tracking", "time_entry", "on_create", lock_payload, user_role=current_user.role)
        enforce_policy_result(lock_result)
        update_data["date"] = data["date"]
        update_data["week_start"] = ws
        update_data["week_end"] = we

    # If rejected, reset to draft on edit
    if entry["status"] == "rejected":
        update_data["status"] = "draft"

    if update_data:
        await db.time_entries.update_one({"entry_id": entry_id}, {"$set": update_data})

    return await db.time_entries.find_one({"entry_id": entry_id}, {"_id": 0})


@router.delete("/time-entries/{entry_id}")
async def delete_time_entry(entry_id: str, current_user=Depends(get_current_user)):
    db = get_db()
    entry = await db.time_entries.find_one({"entry_id": entry_id}, {"_id": 0})
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry["user_id"] != current_user.user_id and not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Only the entry owner can delete")
    if entry["status"] not in ["draft", "rejected"]:
        raise HTTPException(status_code=400, detail="Can only delete draft or rejected entries")
    if entry.get("locked"):
        raise HTTPException(status_code=400, detail="This entry is locked")

    await db.time_entries.delete_one({"entry_id": entry_id})
    await emit_event("time_tracking", "deleted", record=entry, user_id=current_user.user_id, user_email=current_user.email)
    return {"message": "Time entry deleted"}


# ---- Weekly submission ----

@router.post("/time-entries/submit-week")
async def submit_week(data: dict, current_user=Depends(get_current_user)):
    """Submit all draft entries for a given week. Locks them."""
    db = get_db()
    week_start = data.get("week_start")
    if not week_start:
        raise HTTPException(status_code=400, detail="week_start required")

    # Check if any entries are test data (bypass lock for test)
    test_check = await db.time_entries.find_one(
        {"user_id": current_user.user_id, "week_start": week_start, "status": "draft", "is_test_data": True}
    )
    
    if not test_check and await _is_locked(week_start, db):
        # Build dynamic message from the active locking rule
        from zoneinfo import ZoneInfo
        rule = await _get_active_lock_rule(db)
        lock_utc = _lock_datetime_for_week(week_start, rule)
        lock_et = lock_utc.astimezone(ZoneInfo("America/New_York"))
        et_display = lock_et.strftime("%A %-I:%M %p ET")  # e.g. "Tuesday 5:00 PM ET"
        raise HTTPException(
            status_code=400,
            detail=f"This week is past the submission deadline ({et_display})"
        )

    result = await db.time_entries.update_many(
        {"user_id": current_user.user_id, "week_start": week_start, "status": "draft"},
        {"$set": {"status": "submitted", "submitted_at": datetime.now(timezone.utc)}}
    )
    await emit_event("time_tracking", "submitted", record={"user_id": current_user.user_id, "week_start": week_start, "count": result.modified_count}, user_id=current_user.user_id, user_email=current_user.email)
    return {"message": f"Submitted {result.modified_count} entries for week of {week_start}", "count": result.modified_count}


# ---- My weekly view ----

@router.get("/time-entries/my-week")
async def get_my_week(
    week_start: Optional[str] = None,
    is_test_data: Optional[bool] = None,
    current_user=Depends(get_current_user),
):
    """Get the current user's time entries for a specific week."""
    db = get_db()
    if not week_start:
        today = date.today()
        week_start, _ = _get_week_bounds(today)

    filt = {"user_id": current_user.user_id, "week_start": week_start}
    if is_test_data is not None:
        filt["is_test_data"] = is_test_data

    entries = await db.time_entries.find(filt, {"_id": 0}).sort("date", 1).to_list(500)

    total_hours = sum(e.get("hours", 0) for e in entries)
    billable_hours = sum(e.get("hours", 0) for e in entries if e.get("is_billable"))
    statuses = {}
    for e in entries:
        s = e.get("status", "draft")
        statuses[s] = statuses.get(s, 0) + 1

    locked = await _is_locked(week_start, db)

    return {
        "week_start": week_start,
        "entries": entries,
        "total_hours": total_hours,
        "billable_hours": billable_hours,
        "status_counts": statuses,
        "locked": locked,
        "total_entries": len(entries),
    }


# ---- Approval queue (for PMs) ----

@router.get("/time-entries/approval-queue")
async def get_approval_queue(
    project_id: Optional[str] = None,
    week_start: Optional[str] = None,
    is_test_data: Optional[bool] = None,
    current_user=Depends(get_current_user),
):
    """Get submitted entries pending approval. PMs see their projects, admins see all."""
    db = get_db()
    filt = {"status": "submitted"}

    if not has_perm(current_user, "*") and not has_perm(current_user, "contracts.view.all"):
        # PM: only projects they manage
        my_projects = await db["projects"].find(
            {"pm_id": current_user.user_id},
            {"_id": 0, "project_id": 1}
        ).to_list(500)
        pm_project_ids = [p["project_id"] for p in my_projects]
        filt["project_id"] = {"$in": pm_project_ids}

    if project_id:
        filt["project_id"] = project_id
    if week_start:
        filt["week_start"] = week_start
    if is_test_data is not None:
        filt["is_test_data"] = is_test_data

    docs = await db.time_entries.find(filt, {"_id": 0}).sort([("week_start", -1), ("user_name", 1), ("date", 1)]).to_list(1000)

    # Group by user + week for easier display
    grouped = {}
    for e in docs:
        key = f"{e['user_id']}_{e['week_start']}"
        if key not in grouped:
            grouped[key] = {
                "user_id": e["user_id"],
                "user_name": e.get("user_name", ""),
                "week_start": e["week_start"],
                "entries": [],
                "total_hours": 0,
                "billable_hours": 0,
            }
        grouped[key]["entries"].append(e)
        grouped[key]["total_hours"] += e.get("hours", 0)
        if e.get("is_billable"):
            grouped[key]["billable_hours"] += e.get("hours", 0)

    return {"items": list(grouped.values()), "total": len(grouped)}


@router.get("/time-entries/all-unapproved")
async def get_all_unapproved(
    is_test_data: Optional[bool] = None,
    current_user=Depends(get_current_user),
):
    """Get all submitted (unapproved) entries across all weeks, sorted by date."""
    db = get_db()
    filt = {"status": "submitted"}

    if not has_perm(current_user, "*") and not has_perm(current_user, "contracts.view.all"):
        # PM: only projects they manage
        my_projects = await db["projects"].find(
            {"pm_id": current_user.user_id},
            {"_id": 0, "project_id": 1}
        ).to_list(500)
        pm_project_ids = [p["project_id"] for p in my_projects]
        filt["project_id"] = {"$in": pm_project_ids}

    if is_test_data is not None:
        filt["is_test_data"] = is_test_data

    docs = await db.time_entries.find(filt, {"_id": 0}).sort([("date", -1)]).to_list(5000)

    return {"items": docs, "total": len(docs)}


# ---- Approve / Reject ----

@router.post("/time-entries/{entry_id}/approve")
async def approve_time_entry(entry_id: str, data: dict = None, current_user=Depends(get_current_user)):
    db = get_db()
    data = data or {}
    entry = await db.time_entries.find_one({"entry_id": entry_id}, {"_id": 0})
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry["status"] != "submitted":
        raise HTTPException(status_code=400, detail="Only submitted entries can be approved")

    # Check PM has access to this project
    if not has_perm(current_user, "*") and not has_perm(current_user, "contracts.view.all"):
        project = await db["projects"].find_one({"project_id": entry.get("project_id")}, {"_id": 0})
        if not project or project.get("pm_id") != current_user.user_id:
            raise HTTPException(status_code=403, detail="Only the project manager can approve entries")

    is_billable = data.get("is_billable", entry.get("is_billable", True))
    await db.time_entries.update_one(
        {"entry_id": entry_id},
        {"$set": {
            "status": "approved",
            "approved_by": current_user.user_id,
            "approval_notes": data.get("notes", ""),
            "is_billable": is_billable,
            "approved_at": datetime.now(timezone.utc),
        }}
    )

    # Wave 3: route the contract debit through the shared service so
    # single-approve and bulk-approve share one set of rules and one
    # idempotent ledger. The service classifies the entry's
    # effective_billing_mode, splits BOH-with-BOA hours into
    # block vs overflow, writes the contract_time_applications row,
    # and updates contract balances only when appropriate.
    #
    # Hardening: if the debit raises, roll the entry back to
    # "submitted" before propagating so a retry path can re-pick it
    # up. Leaving the entry in "approved" with no ledger row would
    # block the retry permanently (the status check above rejects
    # already-approved entries).
    approved_entry = await db.time_entries.find_one({"entry_id": entry_id}, {"_id": 0})
    debit_result = None
    if approved_entry and approved_entry.get("contract_id"):
        contract = await db.contracts.find_one(
            {"contract_id": approved_entry["contract_id"]}, {"_id": 0}
        )
        if contract:
            try:
                debit_result = await apply_time_entry_to_contract(
                    db,
                    time_entry=approved_entry,
                    contract=contract,
                    actor_user_id=current_user.user_id,
                )
            except Exception as debit_exc:  # noqa: BLE001
                logger.error(
                    "Single approve debit failed for entry %s: %s",
                    entry_id,
                    debit_exc,
                )
                await db.time_entries.update_one(
                    {"entry_id": entry_id},
                    {"$set": {
                        "status": "submitted",
                        "approved_by": None,
                        "approved_at": None,
                        "approval_notes": "",
                    }},
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Contract debit failed during approval. The entry "
                        "has been rolled back to submitted; retry the "
                        "approval after the contract issue is resolved."
                    ),
                )

            # Stamp the resolved effective_billing_mode on the entry so
            # the wire response and downstream summaries can read it
            # without re-joining the ledger row.
            if debit_result and debit_result.get("effective_billing_mode"):
                await db.time_entries.update_one(
                    {"entry_id": entry_id},
                    {"$set": {
                        "effective_billing_mode": debit_result["effective_billing_mode"],
                    }},
                )
                approved_entry["effective_billing_mode"] = debit_result["effective_billing_mode"]

            # Preserve the legacy BOH use_by_date reset / warning-flag
            # clear that the pre-Wave-3 route used to perform inline.
            # Only fires when an actual block debit happened in THIS
            # call (not on idempotent short-circuits), matching the
            # prior intent.
            if (
                contract.get("contract_type") in ("boh", "boh_with_boa")
                and is_billable
                and debit_result
                and debit_result.get("block_hours", 0) > 0
                and not debit_result.get("already_applied")
            ):
                new_use_by = (datetime.now(timezone.utc) + timedelta(days=183)).isoformat()
                resource_types = contract.get("resource_types", [])
                resource_type_id_entry = approved_entry.get("resource_type_id")
                if resource_type_id_entry:
                    for rt in resource_types:
                        if rt.get("id") == resource_type_id_entry:
                            rt["hours_used"] = rt.get("hours_used", 0) + debit_result["block_hours"]
                            rt["hours_remaining"] = rt.get("hours_purchased", 0) - rt["hours_used"]
                            break
                    await db.contracts.update_one(
                        {"contract_id": approved_entry["contract_id"]},
                        {"$set": {
                            "resource_types": resource_types,
                            "use_by_date": new_use_by,
                            "expiration_warning_sent": False,
                        }},
                    )
                else:
                    await db.contracts.update_one(
                        {"contract_id": approved_entry["contract_id"]},
                        {"$set": {
                            "use_by_date": new_use_by,
                            "expiration_warning_sent": False,
                        }},
                    )

    # Update project hours
    if entry.get("project_id"):
        await db.projects.update_one(
            {"project_id": entry["project_id"]},
            {"$inc": {"hours_logged": entry["hours"]}}
        )

    approved_entry = await db.time_entries.find_one({"entry_id": entry_id}, {"_id": 0})

    # Wave 4 (contracts-time-mgmt): sweep billing triggers AFTER the
    # debit is committed. Evaluator failure is logged but never rolls
    # back the approval — triggers are a side effect, not a gate.
    if approved_entry and approved_entry.get("contract_id"):
        try:
            refreshed_contract = await db.contracts.find_one(
                {"contract_id": approved_entry["contract_id"]}, {"_id": 0}
            )
            if refreshed_contract:
                await evaluate_contract_billing_triggers(
                    db, refreshed_contract, actor_user_id=current_user.user_id,
                )
        except Exception as eval_exc:  # noqa: BLE001
            logger.warning(
                "Billing trigger evaluator failed after approve of %s: %s",
                entry_id,
                eval_exc,
            )

    await emit_event("time_tracking", "approved", record=approved_entry, old_record=entry, user_id=current_user.user_id, user_email=current_user.email)
    return approved_entry


@router.post("/time-entries/{entry_id}/reject")
async def reject_time_entry(entry_id: str, data: dict, current_user=Depends(get_current_user)):
    db = get_db()
    entry = await db.time_entries.find_one({"entry_id": entry_id}, {"_id": 0})
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry["status"] != "submitted":
        raise HTTPException(status_code=400, detail="Only submitted entries can be rejected")

    notes = data.get("notes", "")
    if not notes.strip():
        raise HTTPException(status_code=400, detail="Rejection notes are required")

    await db.time_entries.update_one(
        {"entry_id": entry_id},
        {"$set": {
            "status": "rejected",
            "approved_by": current_user.user_id,
            "approval_notes": notes,
            "approved_at": datetime.now(timezone.utc),
        }}
    )
    rejected_entry = await db.time_entries.find_one({"entry_id": entry_id}, {"_id": 0})
    await emit_event("time_tracking", "rejected", record=rejected_entry, old_record=entry, user_id=current_user.user_id, user_email=current_user.email)
    return rejected_entry


@router.post("/time-entries/bulk-approve")
async def bulk_approve(data: dict, current_user=Depends(get_current_user)):
    """Approve multiple entries at once."""
    db = get_db()
    entry_ids = data.get("entry_ids", [])
    if not entry_ids:
        raise HTTPException(status_code=400, detail="entry_ids required")

    approved = 0
    weeks_to_process = set()  # Track weeks for AP bill generation
    # Wave 4: track per-entry failure reasons so the response can show
    # which entries did not approve and the contracts that need a
    # post-batch trigger sweep. failed entries do not count toward
    # approved_count and are rolled back to "submitted" so a retry is
    # possible.
    failed: list[dict] = []
    affected_contract_ids: set = set()

    for eid in entry_ids:
        entry = await db.time_entries.find_one({"entry_id": eid, "status": "submitted"}, {"_id": 0})
        if not entry:
            failed.append({"entry_id": eid, "reason": "not_submitted_or_missing"})
            continue
        await db.time_entries.update_one(
            {"entry_id": eid},
            {"$set": {
                "status": "approved",
                "approved_by": current_user.user_id,
                "is_billable": data.get("is_billable", entry.get("is_billable", True)),
                "approved_at": datetime.now(timezone.utc),
            }}
        )
        # Track week for AP bill generation
        if entry.get("week_start"):
            weeks_to_process.add(entry["week_start"])

        # Wave 3: route bulk-approve through the SAME shared debit
        # service the single-approve route uses. The service is
        # idempotent on entry_id so a retry of the same bulk call
        # against entries that already debited never double-debits.
        if entry.get("contract_id"):
            contract = await db.contracts.find_one(
                {"contract_id": entry["contract_id"]}, {"_id": 0}
            )
            if contract:
                # Re-read the entry to pick up the is_billable change
                # we just persisted above; the debit service classifies
                # off the entry's stored billing_type + is_billable.
                refreshed_entry = await db.time_entries.find_one(
                    {"entry_id": eid}, {"_id": 0}
                ) or entry
                try:
                    debit_result = await apply_time_entry_to_contract(
                        db,
                        time_entry=refreshed_entry,
                        contract=contract,
                        actor_user_id=current_user.user_id,
                    )
                    if debit_result and debit_result.get("effective_billing_mode"):
                        await db.time_entries.update_one(
                            {"entry_id": eid},
                            {"$set": {
                                "effective_billing_mode": debit_result["effective_billing_mode"],
                            }},
                        )
                    # Mirror the legacy BOH use_by reset only when an
                    # actual block debit happened in this call.
                    if (
                        contract.get("contract_type") in ("boh", "boh_with_boa")
                        and debit_result
                        and debit_result.get("block_hours", 0) > 0
                        and not debit_result.get("already_applied")
                    ):
                        new_use_by = (
                            datetime.now(timezone.utc) + timedelta(days=183)
                        ).isoformat()
                        await db.contracts.update_one(
                            {"contract_id": entry["contract_id"]},
                            {"$set": {
                                "use_by_date": new_use_by,
                                "expiration_warning_sent": False,
                            }},
                        )
                except Exception as debit_exc:  # noqa: BLE001
                    logger.error(
                        "Bulk approve debit failed for entry %s: %s", eid, debit_exc
                    )
                    # Roll the entry back to submitted so the retry path
                    # can pick it up. Mirror the single-approve
                    # rollback shape (clear approved_by / approved_at /
                    # approval_notes too) — leaving the approval
                    # metadata stamped on a "submitted" row would falsely
                    # imply the entry had been approved before, and the
                    # retry path would then propagate that ghost
                    # approval into the downstream finance draft.
                    await db.time_entries.update_one(
                        {"entry_id": eid},
                        {"$set": {
                            "status": "submitted",
                            "approved_by": None,
                            "approved_at": None,
                            "approval_notes": "",
                        }},
                    )
                    failed.append({"entry_id": eid, "reason": "debit_failed"})
                    continue
                # Track contracts touched for the post-batch trigger
                # sweep below.
                affected_contract_ids.add(entry["contract_id"])
        if entry.get("project_id"):
            await db.projects.update_one(
                {"project_id": entry["project_id"]},
                {"$inc": {"hours_logged": entry["hours"]}}
            )
        approved += 1
    
    # AUTO-GENERATE AP DRAFTS for each approved week (Wave 3A: repointed from legacy
    # routes.ap_bills to services.finance_draft_autogen after ap_bills.py deletion)
    total_bills_created = 0
    for week_start in weeks_to_process:
        try:
            from services.finance_draft_autogen import build_ap_drafts

            ws = date.fromisoformat(week_start)
            monday = ws
            sunday = monday + timedelta(days=6)

            drafts_written, _skipped, _warnings = await build_ap_drafts(
                db=db,
                period_start=monday.isoformat(),
                period_end=sunday.isoformat(),
                contractor_user_id_filter=None,
                created_by=current_user.user_id,
                dry_run=False,
            )

            total_bills_created += len(drafts_written)
            logger.info(f"Auto-generated {len(drafts_written)} AP drafts for week {week_start}")
        except Exception as e:
            logger.error(f"Failed to auto-generate AP drafts for week {week_start}: {e}")
            # Don't fail the approval, just log the error

    # Wave 4: sweep billing triggers for each contract touched in
    # this batch. Per-contract (not per-entry) so a 50-entry batch
    # against one contract evaluates once. Evaluator failure logs a
    # warning and never affects the approved batch.
    for cid in affected_contract_ids:
        try:
            contract_doc = await db.contracts.find_one(
                {"contract_id": cid}, {"_id": 0}
            )
            if contract_doc:
                await evaluate_contract_billing_triggers(
                    db, contract_doc, actor_user_id=current_user.user_id,
                )
        except Exception as eval_exc:  # noqa: BLE001
            logger.warning(
                "Bulk approve trigger evaluator failed for contract %s: %s",
                cid,
                eval_exc,
            )

    return {
        "message": f"Approved {approved} entries. Generated {total_bills_created} AP drafts.",
        "count": approved,
        "bills_created": total_bills_created,
        "failed": failed,
    }


# ---- Weekly summary for AP/AR ----

@router.get("/time-entries/weekly-summary")
async def weekly_summary(
    week_start: Optional[str] = None,
    is_test_data: Optional[bool] = None,
    current_user=Depends(get_current_user),
):
    """Get weekly summary for QuickBooks AP (contractor pay) and AR (BOA invoices)."""
    if not has_perm(current_user, "*") and not has_perm(current_user, "finance.portal"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    if not week_start:
        today = date.today()
        week_start, _ = _get_week_bounds(today)

    # Build match filter
    ap_match = {"week_start": week_start, "status": "approved"}
    ar_match = {"week_start": week_start, "status": "approved", "is_billable": True, "contract_id": {"$ne": None}}
    
    if is_test_data is not None:
        ap_match["is_test_data"] = is_test_data
        ar_match["is_test_data"] = is_test_data

    # AP: Approved entries grouped by contractor
    ap_pipeline = [
        {"$match": ap_match},
        {"$group": {
            "_id": "$user_id",
            "user_name": {"$first": "$user_name"},
            "total_hours": {"$sum": "$hours"},
            "billable_hours": {"$sum": {"$cond": ["$is_billable", "$hours", 0]}},
            "entry_count": {"$sum": 1},
        }}
    ]
    ap_data = await db.time_entries.aggregate(ap_pipeline).to_list(500)

    # AR: BOA contracts with approved billable hours this week.
    #
    # Wave 3 hardening: sum revenue per entry using the frozen
    # sell_rate_snapshot when present, falling back to live contract
    # rates only for legacy entries that pre-date the snapshot fields.
    # This insulates AR from a post-approval rate edit on the contract.
    from services.time_entry_rates import entry_effective_sell_rate

    ar_entries = await db.time_entries.find(ar_match, {"_id": 0}).to_list(2000)
    ar_by_contract: dict = {}
    for entry in ar_entries:
        cid = entry.get("contract_id")
        if not cid:
            continue
        bucket = ar_by_contract.setdefault(cid, {"total_hours": 0.0, "total_amount": 0.0, "entry_count": 0})
        bucket["total_hours"] += float(entry.get("hours") or 0)
        bucket["entry_count"] += 1
    ar_data = []
    for cid, agg in ar_by_contract.items():
        contract = await db.contracts.find_one({"contract_id": cid}, {"_id": 0})
        if not contract or contract.get("contract_type") != "boa":
            continue
        revenue = 0.0
        for entry in ar_entries:
            if entry.get("contract_id") != cid:
                continue
            sell = entry_effective_sell_rate(entry, contract) or 0.0
            revenue += float(entry.get("hours") or 0) * float(sell)
        ar_data.append({
            "contract_id": cid,
            "contract_name": contract.get("name"),
            "account_name": contract.get("account_name"),
            "billing_period": contract.get("billing_period"),
            "invoice_terms": contract.get("invoice_terms"),
            "hourly_rate": contract.get("hourly_rate", 0),
            "total_hours": agg["total_hours"],
            "total_amount": round(revenue, 2),
        })

    return {
        "week_start": week_start,
        "accounts_payable": {
            "description": "Contractor pay — send to QuickBooks as bills (Net 30)",
            "items": ap_data,
            "total_hours": sum(i.get("total_hours", 0) for i in ap_data),
        },
        "accounts_receivable": {
            "description": "BOA invoices due — send to QuickBooks as invoices",
            "items": ar_data,
            "total_amount": sum(i.get("total_amount", 0) for i in ar_data),
        },
    }


# ---- Account/client time view ----

@router.get("/time-entries/by-account/{account_id}")
async def time_entries_by_account(
    account_id: str,
    week_start: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    db = get_db()
    filt = {"account_id": account_id}
    if week_start:
        filt["week_start"] = week_start

    entries = await db.time_entries.find(filt, {"_id": 0}).sort("date", -1).to_list(1000)

    # Summary
    total = sum(e.get("hours", 0) for e in entries)
    billable = sum(e.get("hours", 0) for e in entries if e.get("is_billable"))

    return {
        "account_id": account_id,
        "entries": entries,
        "total_hours": total,
        "billable_hours": billable,
    }



@router.get("/contractor-resource-types")
async def get_contractor_resource_types(
    contract_id: str = Query(..., description="Contract ID"),
    current_user=Depends(get_current_user),
):
    """
    Get the resource types that the current contractor is assigned to on a contract.
    Returns available types with their rates for the contractor to choose from when logging time.
    """
    db = get_db()
    
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    # Find all resource types this user is assigned to
    user_assignments = [
        r for r in contract.get("resources", []) 
        if r.get("user_id") == current_user.user_id
    ]
    
    if not user_assignments:
        return {"contract_id": contract_id, "resource_types": []}
    
    # Get the full resource type details
    resource_types_map = {rt["id"]: rt for rt in contract.get("resource_types", [])}
    
    available_types = []
    for assignment in user_assignments:
        rt_id = assignment.get("resource_type_id")
        if rt_id and rt_id in resource_types_map:
            rt = resource_types_map[rt_id]
            available_types.append({
                "resource_type_id": rt_id,
                "name": rt.get("name"),
                "sell_rate": rt.get("sell_rate"),
                "cost_rate": assignment.get("cost_rate"),
                "role": assignment.get("role", ""),
                "hours_remaining": rt.get("hours_remaining", 0) if contract.get("contract_type") == "boh" else None,
            })
    
    return {
        "contract_id": contract_id,
        "contract_name": contract.get("name"),
        "contract_type": contract.get("contract_type"),
        "resource_types": available_types,
    }




@router.post("/time-entries/by-ids")
async def get_time_entries_by_ids(
    payload: dict,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Fetch time entries by their IDs.
    Used by Finance Portal to get detailed time entry data for AP bills.
    
    Payload: { "entry_ids": ["te_xxx", "te_yyy", ...] }
    """
    entry_ids = payload.get("entry_ids", [])
    
    if not entry_ids:
        return []
    
    # Fetch entries
    entries = await db.time_entries.find(
        {"entry_id": {"$in": entry_ids}},
        {"_id": 0}
    ).to_list(1000)
    
    return entries
