"""
DEPRECATED — Time Locking Automation

This module is deprecated as of Branch 02 (Policy Engine).
Time locking is now enforced by the 'sys_time_locking' system policy
in the Policy Engine (services/policy_seeds.py).
These routes remain for backward compatibility — the _is_locked() helper
in time_entries.py still uses the time_locking_rules collection to compute
the lock state, which is then passed to the policy engine as a computed field.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
import uuid
import logging

from database import get_db
from auth_helper import get_current_user, has_perm
from services.audit_service import log_action
from models import TimeLockingRule, TimeLockingRuleUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/time-locking/rules")
async def get_time_locking_rules(current_user=Depends(get_current_user)):
    """Get all time locking rules."""
    if not has_perm(current_user, "time.manage_rules"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    
    rules = await db.time_locking_rules.find({}, {"_id": 0}).to_list(100)
    
    # If no rules exist, return default rule
    if not rules:
        return {
            "items": [{
                "rule_id": "default",
                "name": "Weekly Timesheet Lock",
                "description": "Timesheets lock at Monday 5pm ET after the week ends",
                "lock_after_days": 8,
                "lock_time_hour": 22,
                "lock_time_minute": 0,
                "exempt_roles": ["admin"],
                "exempt_permissions": ["time.entry.admin"],
                "enabled": True,
            }],
            "total": 1
        }
    
    return {"items": rules, "total": len(rules)}


@router.get("/time-locking/rules/active")
async def get_active_time_locking_rule(current_user=Depends(get_current_user)):
    """Get the currently active time locking rule (used by time_entries.py)."""
    db = get_db()
    
    rule = await db.time_locking_rules.find_one({"enabled": True}, {"_id": 0})
    
    if not rule:
        # Return default rule
        return {
            "rule_id": "default",
            "name": "Weekly Timesheet Lock",
            "lock_after_days": 8,
            "lock_time_hour": 22,
            "lock_time_minute": 0,
            "exempt_roles": ["admin"],
            "exempt_permissions": ["time.entry.admin"],
            "enabled": True,
        }
    
    return rule


@router.post("/time-locking/rules")
async def create_time_locking_rule(data: dict, current_user=Depends(get_current_user)):
    """Create a new time locking rule."""
    if not has_perm(current_user, "*") and not has_perm(current_user, "time.manage_rules"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    # Only one rule can be enabled at a time
    if data.get("enabled", True):
        await db.time_locking_rules.update_many({}, {"$set": {"enabled": False}})
    
    rule_id = f"tlock_{uuid.uuid4().hex[:12]}"
    doc = {
        "rule_id": rule_id,
        "name": data.get("name", "Weekly Timesheet Lock"),
        "description": data.get("description"),
        "lock_after_days": data.get("lock_after_days", 8),
        "lock_time_hour": data.get("lock_time_hour", 22),
        "lock_time_minute": data.get("lock_time_minute", 0),
        "exempt_roles": data.get("exempt_roles", ["admin"]),
        "exempt_permissions": data.get("exempt_permissions", ["time.entry.admin"]),
        "enabled": data.get("enabled", True),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": current_user.user_id,
    }
    
    await db.time_locking_rules.insert_one(doc)
    doc.pop("_id", None)
    
    await log_action("time_locking_rule.create", current_user.user_id, current_user.email, "time_locking_rule", rule_id)
    return doc


@router.put("/time-locking/rules/{rule_id}")
async def update_time_locking_rule(rule_id: str, data: TimeLockingRuleUpdate, current_user=Depends(get_current_user)):
    """Update time locking rule."""
    if not has_perm(current_user, "*") and not has_perm(current_user, "time.manage_rules"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    rule = await db.time_locking_rules.find_one({"rule_id": rule_id}, {"_id": 0})
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    
    update_doc = {k: v for k, v in data.dict(exclude_unset=True).items() if v is not None}
    
    # If enabling this rule, disable all others
    if data.enabled:
        await db.time_locking_rules.update_many({"rule_id": {"$ne": rule_id}}, {"$set": {"enabled": False}})
    
    update_doc["updated_at"] = datetime.now(timezone.utc)
    
    await db.time_locking_rules.update_one({"rule_id": rule_id}, {"$set": update_doc})
    
    return await db.time_locking_rules.find_one({"rule_id": rule_id}, {"_id": 0})


@router.delete("/time-locking/rules/{rule_id}")
async def delete_time_locking_rule(rule_id: str, current_user=Depends(get_current_user)):
    """Delete time locking rule."""
    if not has_perm(current_user, "*") and not has_perm(current_user, "time.manage_rules"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    result = await db.time_locking_rules.delete_one({"rule_id": rule_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    
    await log_action("time_locking_rule.delete", current_user.user_id, current_user.email, "time_locking_rule", rule_id)
    return {"message": "Rule deleted"}
