"""Policy Engine — CRUD API for managing policies and field definitions."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import re as re_module
import uuid
import logging

from database import get_db
from auth_helper import get_current_user, has_perm
from models import Policy, PolicyCreate, PolicyUpdate, User
from services.policy_engine import invalidate_policy_cache, list_targets_for_admin_ui
from services.field_registry import get_fields, get_all_modules, get_entities_for_module, get_all_registrations, get_module_config
from services.audit_service import log_action

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== FIELD DEFINITIONS ====================

@router.get("/field-definitions/modules")
async def get_registered_modules(current_user: User = Depends(get_current_user)):
    """Get all modules that have registered fields for policy evaluation."""
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    registrations = get_all_registrations()
    modules_with_categories = {}
    for module, entities in registrations.items():
        config = get_module_config(module)
        modules_with_categories[module] = {
            "entities": entities,
            "category": config.get("category", "Other"),
        }
    return {"modules": modules_with_categories}


@router.get("/policy-targets")
async def get_policy_targets(current_user: User = Depends(get_current_user)):
    """Targets available for policy creation — native modules + ingested apps.

    Sources: in-memory field_registry for natives; `policy_targets` MongoDB
    collection for ingested apps (populated by ingestion pipeline). The
    frontend uses this to populate the policy-create dropdown so admins can
    target any registered (module, entity, event) tuple without a code deploy.
    """
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    targets = await list_targets_for_admin_ui(db)
    return {"targets": targets}


@router.get("/field-definitions/{module}/{entity}")
async def get_field_definitions(
    module: str,
    entity: str,
    current_user: User = Depends(get_current_user),
):
    """Get registered fields for a module/entity pair."""
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    fields = get_fields(module, entity)
    return {"module": module, "entity": entity, "fields": fields}


# ==================== POLICIES CRUD ====================

@router.get("/policies")
async def list_policies(
    module: Optional[str] = None,
    entity: Optional[str] = None,
    enabled: Optional[bool] = None,
    search: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """List all policies (excluding soft-deleted)."""
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    query = {"deleted_at": None}
    if module:
        query["module"] = module
    if entity:
        query["entity"] = entity
    if enabled is not None:
        query["enabled"] = enabled
    if search:
        safe_search = re_module.escape(search)
        query["$or"] = [
            {"name": {"$regex": safe_search, "$options": "i"}},
            {"description": {"$regex": safe_search, "$options": "i"}},
        ]

    policies = await db.policies.find(query, {"_id": 0}).sort("priority", -1).to_list(1000)
    return {"items": policies, "total": len(policies)}


@router.get("/policies/{policy_id}")
async def get_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get a single policy by ID."""
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    policy = await db.policies.find_one(
        {"policy_id": policy_id, "deleted_at": None}, {"_id": 0}
    )
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.post("/policies")
async def create_policy(
    data: PolicyCreate,
    current_user: User = Depends(get_current_user),
):
    """Create a new policy."""
    if not has_perm(current_user, "policies.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    now = datetime.now(timezone.utc)
    policy_id = f"policy_{uuid.uuid4().hex[:12]}"

    doc = {
        "policy_id": policy_id,
        "name": data.name,
        "description": data.description,
        "module": data.module,
        "entity": data.entity,
        "enabled": data.enabled,
        "is_system": False,
        "trigger": data.trigger.model_dump(),
        "condition_logic": data.condition_logic,
        "condition_pattern": data.condition_pattern,
        "conditions": [c.model_dump() for c in data.conditions],
        "actions": [a.model_dump() for a in data.actions],
        "applies_to_roles": data.applies_to_roles,
        "priority": data.priority,
        "created_by": current_user.user_id,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }

    await db.policies.insert_one(doc)
    invalidate_policy_cache()

    await log_action(
        action="policy.create",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="policy",
        resource_id=policy_id,
        details={"name": data.name, "module": data.module, "entity": data.entity},
    )

    doc.pop("_id", None)
    return doc


@router.put("/policies/{policy_id}")
async def update_policy(
    policy_id: str,
    data: PolicyUpdate,
    current_user: User = Depends(get_current_user),
):
    """Update an existing policy."""
    if not has_perm(current_user, "policies.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    existing = await db.policies.find_one(
        {"policy_id": policy_id, "deleted_at": None}, {"_id": 0}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")

    # System policies: require policies.manage_system permission for structural edits
    SYSTEM_LOCKED_FIELDS = {"module", "entity", "trigger", "conditions", "actions", "condition_logic", "condition_pattern"}

    update_fields = {}
    for field_name, value in data.model_dump(exclude_unset=True).items():
        if existing.get("is_system") and field_name in SYSTEM_LOCKED_FIELDS:
            if not has_perm(current_user, "policies.manage_system"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot modify '{field_name}' on a system policy without the 'Manage System Policies' permission.",
                )
        if field_name == "trigger" and value is not None:
            update_fields["trigger"] = value
        elif field_name == "conditions" and value is not None:
            update_fields["conditions"] = value
        elif field_name == "actions" and value is not None:
            update_fields["actions"] = value
        elif value is not None:
            update_fields[field_name] = value

    if not update_fields:
        return existing

    update_fields["updated_at"] = datetime.now(timezone.utc)

    await db.policies.update_one(
        {"policy_id": policy_id},
        {"$set": update_fields},
    )
    invalidate_policy_cache()

    await log_action(
        action="policy.update",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="policy",
        resource_id=policy_id,
        details={"updated_fields": list(update_fields.keys())},
    )

    updated = await db.policies.find_one({"policy_id": policy_id}, {"_id": 0})
    return updated


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
):
    """Soft-delete a policy. System policies cannot be deleted."""
    if not has_perm(current_user, "policies.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    existing = await db.policies.find_one(
        {"policy_id": policy_id, "deleted_at": None}, {"_id": 0}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")

    if existing.get("is_system") and not has_perm(current_user, "policies.manage_system"):
        raise HTTPException(
            status_code=403,
            detail="Cannot delete a system policy without the 'Manage System Policies' permission.",
        )

    await db.policies.update_one(
        {"policy_id": policy_id},
        {"$set": {"deleted_at": datetime.now(timezone.utc)}},
    )
    invalidate_policy_cache()

    await log_action(
        action="policy.delete",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="policy",
        resource_id=policy_id,
        details={"name": existing.get("name")},
    )

    return {"message": "Policy deleted"}


@router.patch("/policies/{policy_id}/toggle")
async def toggle_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
):
    """Toggle a policy's enabled state."""
    if not has_perm(current_user, "policies.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    existing = await db.policies.find_one(
        {"policy_id": policy_id, "deleted_at": None}, {"_id": 0}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")

    new_enabled = not existing.get("enabled", True)
    await db.policies.update_one(
        {"policy_id": policy_id},
        {"$set": {"enabled": new_enabled, "updated_at": datetime.now(timezone.utc)}},
    )
    invalidate_policy_cache()

    await log_action(
        action="policy.toggle",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="policy",
        resource_id=policy_id,
        details={"name": existing.get("name"), "enabled": new_enabled},
    )

    return {"policy_id": policy_id, "enabled": new_enabled}


# ==================== POLICY OPTIONS ====================

@router.get("/policy-options/roles")
async def get_available_roles(current_user: User = Depends(get_current_user)):
    """Return all roles for the policy role-targeting dropdown."""
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    roles = await db.roles.find({}, {"_id": 0, "role_id": 1, "name": 1, "display_name": 1}).sort("name", 1).to_list(100)
    return [{"value": r["name"], "label": r.get("display_name", r["name"])} for r in roles]
