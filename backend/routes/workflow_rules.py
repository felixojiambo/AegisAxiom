"""
Workflow Rules â€” CRUD API for workflow automation rules + notifications.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timezone
from typing import Optional
import uuid
import logging
import re

from database import get_db
from auth_helper import get_current_user, has_perm
from services.field_registry import get_all_registrations, get_fields, get_all_module_events, get_module_config
from services.workflow_engine import invalidate_workflow_cache, handle_event_dry_run
from services.event_bus import build_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Workflow Rules"])

# Scheduler reference, set from server.py startup
_scheduler_ref = None


def set_scheduler_ref(scheduler):
    """Called from server.py startup to set the APScheduler reference."""
    global _scheduler_ref
    _scheduler_ref = scheduler


# ==================== MODULES & FIELDS ====================

@router.get("/admin/workflow-rules/modules")
async def get_available_modules(current_user=Depends(get_current_user)):
    """Get all registered modules with their events and fields."""
    if not has_perm(current_user, "workflow_rules.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    registrations = get_all_registrations()
    events = get_all_module_events()

    modules_list = []
    for module, entities in registrations.items():
        all_fields = []
        fields_by_entity = {}
        for entity in entities:
            entity_fields = get_fields(module, entity)
            fields_by_entity[entity] = entity_fields
            all_fields.extend(entity_fields)
        config = get_module_config(module)
        modules_list.append({
            "name": module,
            "label": module.replace("_", " ").title(),
            "category": config.get("category", "Other"),
            "entities": entities,
            "events": events.get(module, {}),
            "fields": all_fields,
            "fieldsByEntity": fields_by_entity,
        })
    modules_list.sort(key=lambda m: m["label"])
    return {"modules": modules_list}


# ==================== WORKFLOW RULES CRUD ====================

@router.get("/admin/workflow-rules")
async def list_workflow_rules(
    module: Optional[str] = None,
    trigger_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    search: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "workflow_rules.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    query: dict = {"deleted_at": None}

    if module:
        query["module"] = module
    if trigger_type:
        query["trigger.type"] = trigger_type
    if enabled is not None:
        query["enabled"] = enabled
    if search:
        safe_search = re.escape(search)
        query["$or"] = [
            {"name": {"$regex": safe_search, "$options": "i"}},
            {"description": {"$regex": safe_search, "$options": "i"}},
        ]

    rules = await db.workflow_rules.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"rules": rules, "total": len(rules)}


@router.get("/admin/workflow-rules/{rule_id}")
async def get_workflow_rule(rule_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "workflow_rules.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    rule = await db.workflow_rules.find_one({"rule_id": rule_id, "deleted_at": None}, {"_id": 0})
    if not rule:
        raise HTTPException(status_code=404, detail="Workflow rule not found")
    return rule


@router.post("/admin/workflow-rules")
async def create_workflow_rule(data: dict, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "workflow_rules.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    now = datetime.now(timezone.utc)
    rule_id = f"wfr_{uuid.uuid4().hex[:12]}"

    doc = {
        "rule_id": rule_id,
        "name": data.get("name", "Untitled Rule"),
        "description": data.get("description"),
        "module": data.get("module"),
        "enabled": data.get("enabled", True),
        "trigger": data.get("trigger", {}),
        "condition_logic": data.get("condition_logic", "AND"),
        "condition_pattern": data.get("condition_pattern"),
        "conditions": data.get("conditions", []),
        "instant_actions": data.get("instant_actions", []),
        "scheduled_actions": data.get("scheduled_actions", []),
        "created_by": current_user.user_id,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "last_run_at": None,
        "run_count": 0,
    }

    await db.workflow_rules.insert_one(doc)
    doc.pop("_id", None)
    invalidate_workflow_cache()

    if doc.get("trigger", {}).get("type") == "scheduled":
        await _sync_scheduler()

    logger.info(f"Workflow rule created: {rule_id} '{doc['name']}' by {current_user.email}")
    return doc


@router.put("/admin/workflow-rules/{rule_id}")
async def update_workflow_rule(rule_id: str, data: dict, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "workflow_rules.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    existing = await db.workflow_rules.find_one({"rule_id": rule_id, "deleted_at": None})
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow rule not found")

    update_fields = {}
    allowed = [
        "name", "description", "module", "enabled", "trigger", "condition_logic",
        "condition_pattern", "conditions", "instant_actions", "scheduled_actions",
    ]
    for field in allowed:
        if field in data:
            update_fields[field] = data[field]

    update_fields["updated_at"] = datetime.now(timezone.utc)

    await db.workflow_rules.update_one({"rule_id": rule_id}, {"$set": update_fields})
    invalidate_workflow_cache()
    await _sync_scheduler()

    updated = await db.workflow_rules.find_one({"rule_id": rule_id}, {"_id": 0})
    logger.info(f"Workflow rule updated: {rule_id} by {current_user.email}")
    return updated


@router.delete("/admin/workflow-rules/{rule_id}")
async def delete_workflow_rule(rule_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "workflow_rules.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    result = await db.workflow_rules.update_one(
        {"rule_id": rule_id, "deleted_at": None},
        {"$set": {"deleted_at": datetime.now(timezone.utc), "enabled": False}},
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Workflow rule not found")

    invalidate_workflow_cache()
    await _sync_scheduler()

    logger.info(f"Workflow rule deleted: {rule_id} by {current_user.email}")
    return {"message": "Rule deleted"}


@router.patch("/admin/workflow-rules/{rule_id}/toggle")
async def toggle_workflow_rule(rule_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "workflow_rules.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    rule = await db.workflow_rules.find_one({"rule_id": rule_id, "deleted_at": None})
    if not rule:
        raise HTTPException(status_code=404, detail="Workflow rule not found")

    new_enabled = not rule.get("enabled", True)
    await db.workflow_rules.update_one(
        {"rule_id": rule_id},
        {"$set": {"enabled": new_enabled, "updated_at": datetime.now(timezone.utc)}},
    )
    invalidate_workflow_cache()
    await _sync_scheduler()

    return {"rule_id": rule_id, "enabled": new_enabled}


# ==================== EXECUTION HISTORY ====================

@router.get("/admin/workflow-rules/{rule_id}/executions")
async def get_rule_executions(
    rule_id: str,
    status: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "workflow_rules.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    query: dict = {"rule_id": rule_id}
    if status:
        query["status"] = status

    executions = await db.workflow_rule_executions.find(
        query, {"_id": 0}
    ).sort("triggered_at", -1).to_list(limit)

    return {"executions": executions, "total": len(executions)}


# ==================== DRY RUN / TEST ====================

@router.post("/admin/workflow-rules/{rule_id}/test")
async def test_workflow_rule(rule_id: str, data: dict, current_user=Depends(get_current_user)):
    """Dry-run a workflow rule against a sample payload."""
    if not has_perm(current_user, "workflow_rules.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    rule = await db.workflow_rules.find_one({"rule_id": rule_id, "deleted_at": None}, {"_id": 0})
    if not rule:
        raise HTTPException(status_code=404, detail="Workflow rule not found")

    sample_record = data.get("record", {})
    sample_action = data.get("action", "created")

    event = build_event(
        module=rule.get("module", ""),
        action=sample_action,
        record=sample_record,
        old_record=data.get("old_record"),
        user_id=current_user.user_id,
        user_email=current_user.email,
    )

    result = await handle_event_dry_run(event)
    return result


# ==================== NOTIFICATIONS ====================

@router.get("/notifications")
async def list_notifications(
    limit: int = Query(default=50, le=200),
    current_user=Depends(get_current_user),
):
    """List notifications for the current user."""
    db = get_db()
    notifications = await db.notifications.find(
        {"user_id": current_user.user_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(limit)

    unread_count = await db.notifications.count_documents(
        {"user_id": current_user.user_id, "read": False}
    )

    return {"notifications": notifications, "unread_count": unread_count}


@router.get("/notifications/unread-count")
async def get_unread_count(current_user=Depends(get_current_user)):
    db = get_db()
    count = await db.notifications.count_documents(
        {"user_id": current_user.user_id, "read": False}
    )
    return {"unread_count": count}


@router.patch("/notifications/read-all")
async def mark_all_notifications_read(current_user=Depends(get_current_user)):
    db = get_db()
    result = await db.notifications.update_many(
        {"user_id": current_user.user_id, "read": False},
        {"$set": {"read": True}},
    )
    return {"message": f"Marked {result.modified_count} as read"}


@router.patch("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user=Depends(get_current_user)):
    db = get_db()
    await db.notifications.update_one(
        {"notification_id": notification_id, "user_id": current_user.user_id},
        {"$set": {"read": True}},
    )
    return {"message": "Marked as read"}


# ==================== HELPERS ====================

async def _sync_scheduler():
    """Sync cron jobs with APScheduler if available."""
    if _scheduler_ref:
        try:
            from services.workflow_scheduler import sync_cron_jobs
            await sync_cron_jobs(_scheduler_ref)
        except Exception as e:
            logger.warning(f"Failed to sync scheduler: {e}")
