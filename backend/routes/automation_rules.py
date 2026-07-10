"""
DEPRECATED — Automation Rules CRUD Routes

This module is deprecated as of Branch 02 (Policy Engine).
Policy management is now done via routes/policies.py.
These routes remain for backward compatibility with existing workflow rules
until Branch 03 (Workflow Rules Engine).
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import List
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from database import get_db
from models import (
    AutomationRule, AutomationRuleCreate, AutomationRuleUpdate,
    TriggeredAlert, User
)
from auth_helper import get_current_user
from services.automation_seeds import seed_builtin_automation_rules

router = APIRouter()

# ==================== INITIALIZATION ====================

@router.post("/automation-rules/seed-defaults")
async def seed_default_rules(
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Initialize built-in automation rules (idempotent - safe to call multiple times)"""
    result = await seed_builtin_automation_rules(db)
    return {
        "message": "Built-in automation rules initialized",
        "seeded_count": result["seeded"],
        "rule_ids": result["rules"]
    }


# ==================== AUTOMATION RULES CRUD ====================

@router.get("/automation-rules")
async def get_automation_rules(
    enabled_only: bool = False,
    category: str = None,
    functional_area: str = None,  # NEW: Filter by functional area
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all automation rules"""
    query = {}
    if enabled_only:
        query["enabled"] = True
    if category:
        query["trigger_category"] = category
    if functional_area:
        query["functional_area"] = functional_area
    
    rules = await db.automation_rules.find(query, {"_id": 0}).sort("priority", -1).to_list(1000)
    return {"items": rules, "total": len(rules)}

@router.get("/automation-rules/{rule_id}")
async def get_automation_rule(
    rule_id: str,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get single automation rule"""
    rule = await db.automation_rules.find_one({"rule_id": rule_id}, {"_id": 0})
    if not rule:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    return rule

@router.post("/automation-rules")
async def create_automation_rule(
    rule: AutomationRuleCreate,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create new automation rule"""
    rule_doc = {
        "rule_id": f"rule_{datetime.now().timestamp()}".replace(".", "_"),
        "name": rule.name,
        "description": rule.description,
        "functional_area": rule.functional_area,  # NEW
        "trigger_category": rule.trigger_category,
        "trigger_type": rule.trigger_type,
        "conditions": [c.model_dump() for c in rule.conditions],
        "actions": [a.model_dump() for a in rule.actions],
        "lob_filter": rule.lob_filter,
        "stage_filter": rule.stage_filter,
        "enabled": rule.enabled,
        "priority": rule.priority,
        "is_built_in": False,  # User-created rules are never built-in
        "created_by": current_user.email,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }
    
    await db.automation_rules.insert_one(rule_doc)
    rule_doc.pop("_id", None)
    return rule_doc

@router.put("/automation-rules/{rule_id}")
async def update_automation_rule(
    rule_id: str,
    updates: AutomationRuleUpdate,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update automation rule"""
    existing = await db.automation_rules.find_one({"rule_id": rule_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    
    update_doc = {}
    if updates.name is not None:
        update_doc["name"] = updates.name
    if updates.description is not None:
        update_doc["description"] = updates.description
    if updates.functional_area is not None:  # NEW
        update_doc["functional_area"] = updates.functional_area
    if updates.conditions is not None:
        update_doc["conditions"] = [c.model_dump() for c in updates.conditions]
    if updates.actions is not None:
        update_doc["actions"] = [a.model_dump() for a in updates.actions]
    if updates.lob_filter is not None:
        update_doc["lob_filter"] = updates.lob_filter
    if updates.stage_filter is not None:
        update_doc["stage_filter"] = updates.stage_filter
    if updates.enabled is not None:
        update_doc["enabled"] = updates.enabled
    if updates.priority is not None:
        update_doc["priority"] = updates.priority
    
    update_doc["updated_at"] = datetime.now(timezone.utc)
    
    await db.automation_rules.update_one({"rule_id": rule_id}, {"$set": update_doc})
    
    updated = await db.automation_rules.find_one({"rule_id": rule_id}, {"_id": 0})
    return updated

@router.delete("/automation-rules/{rule_id}")
async def delete_automation_rule(
    rule_id: str,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete automation rule (cannot delete built-in rules)"""
    # Check if rule is built-in
    rule = await db.automation_rules.find_one({"rule_id": rule_id})
    if not rule:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    
    if rule.get("is_built_in", False):
        raise HTTPException(
            status_code=403, 
            detail="Cannot delete built-in system rules. You can disable them instead."
        )
    
    result = await db.automation_rules.delete_one({"rule_id": rule_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    return {"message": "Automation rule deleted successfully"}

@router.patch("/automation-rules/{rule_id}/toggle")
async def toggle_automation_rule(
    rule_id: str,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Toggle automation rule enabled/disabled"""
    rule = await db.automation_rules.find_one({"rule_id": rule_id})
    if not rule:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    
    new_enabled = not rule.get("enabled", True)
    await db.automation_rules.update_one(
        {"rule_id": rule_id},
        {"$set": {"enabled": new_enabled, "updated_at": datetime.now(timezone.utc)}}
    )
    
    return {"rule_id": rule_id, "enabled": new_enabled}

# ==================== ALERTS API ====================

@router.get("/opportunities/{opportunity_id}/alerts")
async def get_opportunity_alerts(
    opportunity_id: str,
    include_dismissed: bool = False,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get active alerts for an opportunity"""
    query = {"opportunity_id": opportunity_id}
    if not include_dismissed:
        query["dismissed"] = False
    
    alerts = await db.triggered_alerts.find(query, {"_id": 0}).sort("triggered_at", -1).to_list(100)
    return {"items": alerts, "total": len(alerts)}

@router.get("/alerts/dashboard")
async def get_all_alerts(
    severity: str = None,
    acknowledged: bool = None,
    limit: int = 100,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all active alerts across opportunities (for Alerts Dashboard)"""
    query = {"dismissed": False}
    if severity:
        query["severity"] = severity
    if acknowledged is not None:
        query["acknowledged"] = acknowledged
    
    alerts = await db.triggered_alerts.find(query, {"_id": 0}).sort([("severity", -1), ("triggered_at", -1)]).limit(limit).to_list(limit)
    
    # Group by severity for dashboard stats
    stats = {
        "critical": len([a for a in alerts if a.get("severity") == "critical"]),
        "warning": len([a for a in alerts if a.get("severity") == "warning"]),
        "success": len([a for a in alerts if a.get("severity") == "success"]),
        "total": len(alerts)
    }
    
    return {"items": alerts, "stats": stats}

@router.patch("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Acknowledge an alert"""
    result = await db.triggered_alerts.update_one(
        {"alert_id": alert_id},
        {"$set": {
            "acknowledged": True,
            "acknowledged_by": current_user.email,
            "acknowledged_at": datetime.now(timezone.utc)
        }}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    return {"message": "Alert acknowledged"}

@router.patch("/alerts/{alert_id}/dismiss")
async def dismiss_alert(
    alert_id: str,
    db: AsyncIOMotorClient = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Dismiss an alert"""
    result = await db.triggered_alerts.update_one(
        {"alert_id": alert_id},
        {"$set": {"dismissed": True}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    return {"message": "Alert dismissed"}
