"""Policy Engine — CRUD API for managing policies and field definitions."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session
from repositories.policies import PolicyRepository, policy_to_api_dict
from repositories.roles import RolesRepository
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
async def get_policy_targets(
    current_user: User = Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Targets available for policy creation — native modules + ingested apps.

    Sources: in-memory field_registry for natives; `policy_targets` table
    (Postgres) for ingested apps (populated by ingestion pipeline). The
    frontend uses this to populate the policy-create dropdown so admins can
    target any registered (module, entity, event) tuple without a code deploy.
    """
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    targets = await list_targets_for_admin_ui(session=pg)
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
    pg: AsyncSession = Depends(get_session),
):
    """List all policies (excluding soft-deleted).

    ``search`` is a case-insensitive substring over name/description
    (formerly an escaped ``$regex``; now ILIKE, trigram-indexed).
    """
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    policies = await PolicyRepository(pg).list_policies(
        module=module or None,
        entity=entity or None,
        enabled=enabled,
        search=search or None,
    )
    items = [policy_to_api_dict(p) for p in policies]
    return {"items": items, "total": len(items)}


@router.get("/policies/{policy_id}")
async def get_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Get a single policy by ID."""
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    policy = await PolicyRepository(pg).get_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy_to_api_dict(policy)


@router.post("/policies")
async def create_policy(
    data: PolicyCreate,
    current_user: User = Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Create a new policy."""
    if not has_perm(current_user, "policies.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

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
        # NOTE (pre-existing, reported in the 2b report, not fixed here):
        # PolicyCreate declares no applies_to_roles field, so this access
        # raises AttributeError exactly as it did on Mongo.
        "applies_to_roles": data.applies_to_roles,
        "priority": data.priority,
        "created_by": current_user.user_id,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }

    await PolicyRepository(pg).create_policy(
        Policy(**{k: v for k, v in doc.items() if k != "applies_to_roles"})
    )
    invalidate_policy_cache()

    await log_action(
        action="policy.create",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="policy",
        resource_id=policy_id,
        details={"name": data.name, "module": data.module, "entity": data.entity},
    )

    return doc


@router.put("/policies/{policy_id}")
async def update_policy(
    policy_id: str,
    data: PolicyUpdate,
    current_user: User = Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Update an existing policy."""
    if not has_perm(current_user, "policies.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    repo = PolicyRepository(pg)
    existing = await repo.get_policy(policy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")

    # System policies: require policies.manage_system permission for structural edits
    SYSTEM_LOCKED_FIELDS = {"module", "entity", "trigger", "conditions", "actions", "condition_logic", "condition_pattern"}

    update_fields = {}
    for field_name, value in data.model_dump(exclude_unset=True).items():
        if existing.is_system and field_name in SYSTEM_LOCKED_FIELDS:
            if not has_perm(current_user, "policies.manage_system"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot modify '{field_name}' on a system policy without the 'Manage System Policies' permission.",
                )
        if value is not None:
            update_fields[field_name] = value

    if not update_fields:
        return policy_to_api_dict(existing)

    update_fields["updated_at"] = datetime.now(timezone.utc)

    updated = await repo.update_policy(policy_id, update_fields)
    invalidate_policy_cache()

    await log_action(
        action="policy.update",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="policy",
        resource_id=policy_id,
        details={"updated_fields": list(update_fields.keys())},
    )

    return policy_to_api_dict(updated) if updated else None


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Soft-delete a policy. System policies cannot be deleted."""
    if not has_perm(current_user, "policies.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    repo = PolicyRepository(pg)
    existing = await repo.get_policy(policy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")

    if existing.is_system and not has_perm(current_user, "policies.manage_system"):
        raise HTTPException(
            status_code=403,
            detail="Cannot delete a system policy without the 'Manage System Policies' permission.",
        )

    await repo.soft_delete_policy(policy_id)
    invalidate_policy_cache()

    await log_action(
        action="policy.delete",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="policy",
        resource_id=policy_id,
        details={"name": existing.name},
    )

    return {"message": "Policy deleted"}


@router.patch("/policies/{policy_id}/toggle")
async def toggle_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Toggle a policy's enabled state.

    One atomic ``UPDATE … SET enabled = NOT enabled RETURNING`` — two
    concurrent toggles can no longer both read the same state (§A6).
    """
    if not has_perm(current_user, "policies.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    toggled = await PolicyRepository(pg).toggle_enabled(policy_id)
    if not toggled:
        raise HTTPException(status_code=404, detail="Policy not found")
    invalidate_policy_cache()

    await log_action(
        action="policy.toggle",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="policy",
        resource_id=policy_id,
        details={"name": toggled.name, "enabled": toggled.enabled},
    )

    return {"policy_id": policy_id, "enabled": toggled.enabled}


# ==================== POLICY OPTIONS ====================

@router.get("/policy-options/roles")
async def get_available_roles(
    current_user: User = Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Return all roles for the policy role-targeting dropdown."""
    if not has_perm(current_user, "policies.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    roles = await RolesRepository(pg).list_roles(order="name", limit=100)
    return [{"value": r.name, "label": r.display_name or r.name} for r in roles]
