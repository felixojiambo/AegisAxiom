"""
App Ingestion — RBAC Generator

Generates permissions and role suggestions for ingested apps.
Two-layer approach:
  Layer 1: Template-driven — deterministic permissions from CRUD routes
  Layer 2: Role suggestions — groupings based on the app's domain

The developer reviews and approves everything before it's written.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from database import get_db
from models import gen_id
from services.ingestion import repository as repo
from services.ingestion.models import PipelineStage, slug_to_app_slug
from services.permission_registry import (
    deactivate_permissions,
    register_permissions,
    SOURCE_INGESTION,
    SOURCE_MANUAL_OVERRIDE,
)
from services.security_logger import log_security_event

logger = logging.getLogger(__name__)


def generate_permissions(app_slug: str, app_display_name: str, catalogues: list[dict]) -> list[dict]:
    """
    Layer 1: Generate template-driven permissions from field catalogues.
    Returns a list of permission dicts matching AEGISAXIOM's ALL_PERMISSIONS format:
    [{"key": "...", "group": "...", "label": "..."}]
    """
    permissions = []
    group_name = app_display_name

    # Top-level app access gate
    permissions.append({
        "key": f"{app_slug}.access",
        "group": group_name,
        "label": f"Access {app_display_name}",
    })

    for cat in catalogues:
        entity = cat.get("entity_name", cat.get("module_name", ""))
        entity_label = entity.replace("_", " ").title()
        routes = cat.get("api_routes", [])
        fields = cat.get("fields", [])

        # Determine which operations exist from the extracted routes
        has_list = any(r.get("operation") == "list" for r in routes)
        has_get = any(r.get("operation") == "get" for r in routes)
        has_create = any(r.get("operation") == "create" for r in routes)
        has_update = any(r.get("operation") == "update" for r in routes)
        has_delete = any(r.get("operation") == "delete" for r in routes)
        has_status = any(f.get("name") == "status" for f in fields)
        has_assignee = any(f.get("name") in ("assignee_id", "assigned_to", "owner_id") for f in fields)

        # View permissions
        if has_list or has_get:
            permissions.append({
                "key": f"{app_slug}.{entity}.view",
                "group": group_name,
                "label": f"View {entity_label}",
            })

        # Create
        if has_create:
            permissions.append({
                "key": f"{app_slug}.{entity}.create",
                "group": group_name,
                "label": f"Create {entity_label}",
            })

        # Update
        if has_update:
            permissions.append({
                "key": f"{app_slug}.{entity}.edit",
                "group": group_name,
                "label": f"Edit {entity_label}",
            })

        # Delete
        if has_delete:
            permissions.append({
                "key": f"{app_slug}.{entity}.delete",
                "group": group_name,
                "label": f"Delete {entity_label}",
            })

        # Conditional: change_status
        if has_status and has_update:
            permissions.append({
                "key": f"{app_slug}.{entity}.change_status",
                "group": group_name,
                "label": f"Change {entity_label} Status",
            })

        # Conditional: assign
        if has_assignee and has_update:
            permissions.append({
                "key": f"{app_slug}.{entity}.assign",
                "group": group_name,
                "label": f"Assign {entity_label}",
            })

    return permissions


def suggest_roles(
    app_slug: str,
    app_display_name: str,
    permissions: list[dict],
    source_roles: list[str],
) -> list[dict]:
    """
    Layer 2: Suggest role groupings based on the app's source roles
    and the generated permissions. Returns role suggestions for
    the developer to review.

    Each suggestion: {name, display_name, description, permissions: [keys], mapping_note}
    """
    all_perm_keys = [p["key"] for p in permissions]
    view_perms = [k for k in all_perm_keys if ".view" in k or k.endswith(".access")]
    edit_perms = [k for k in all_perm_keys if ".edit" in k or ".create" in k or ".change_status" in k or ".assign" in k]
    delete_perms = [k for k in all_perm_keys if ".delete" in k]

    suggestions = []

    # Admin-like role: all permissions
    if "admin" in source_roles:
        suggestions.append({
            "name": f"{app_slug}_admin",
            "display_name": f"{app_display_name} Admin",
            "description": f"Full access to all {app_display_name} modules",
            "permissions": all_perm_keys,
            "mapping_note": "Maps to source role 'admin'. Consider adding these to the existing AEGISAXIOM 'admin' role instead of creating a new role.",
            "source_role": "admin",
        })

    # Manager-like role: view + edit + status changes, no delete
    manager_roles = [r for r in source_roles if r in ("project_manager", "manager", "lead", "supervisor")]
    if manager_roles:
        suggestions.append({
            "name": f"{app_slug}_manager",
            "display_name": f"{app_display_name} Manager",
            "description": f"Manage {app_display_name} — view, create, edit, and change statuses",
            "permissions": view_perms + edit_perms,
            "mapping_note": f"Maps to source role(s): {', '.join(manager_roles)}. Could extend existing AEGISAXIOM 'director' or 'sales_ops' role.",
            "source_role": manager_roles[0],
        })

    # Member/engineer role: view + limited edit on assigned items
    member_roles = [r for r in source_roles if r in ("engineer", "member", "user", "contributor", "staff")]
    if member_roles:
        suggestions.append({
            "name": f"{app_slug}_member",
            "display_name": f"{app_display_name} Member",
            "description": f"View and update assigned items in {app_display_name}",
            "permissions": view_perms + [k for k in edit_perms if ".create" not in k],
            "mapping_note": f"Maps to source role(s): {', '.join(member_roles)}. Could extend existing AEGISAXIOM 'sales_rep' or 'contractor' role.",
            "source_role": member_roles[0],
        })

    # Viewer role: read-only
    suggestions.append({
        "name": f"{app_slug}_viewer",
        "display_name": f"{app_display_name} Viewer",
        "description": f"Read-only access to {app_display_name}",
        "permissions": view_perms,
        "mapping_note": "Read-only access. Good for stakeholders, clients, or external partners.",
        "source_role": None,
    })

    return suggestions


async def apply_rbac(
    app_id: str,
    permissions: list[dict],
    role_decisions: list[dict],
    performed_by: str = None,
) -> dict:
    """
    Write the approved RBAC to the database.

    role_decisions: list of dicts, each with:
      - action: "create_new" | "extend_existing" | "skip"
      - name: role name (for create_new)
      - display_name: (for create_new)
      - permissions: list of permission keys
      - existing_role_name: (for extend_existing) which AEGISAXIOM role to add permissions to
    """
    db = get_db()
    results = {"roles_created": [], "roles_extended": [], "permissions_registered": len(permissions)}

    # Validate submitted permissions: only allow keys that were generated for this app
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")
    generated = app.get("generated_permissions", [])
    if generated:
        valid_keys = {p["key"] for p in generated}
        submitted_keys = {p["key"] for p in permissions if isinstance(p, dict)}
        invalid = submitted_keys - valid_keys
        if invalid:
            raise ValueError(f"Submitted permissions not in generated set: {invalid}")

    # Store the app's permissions in the app_registry for reference
    await db.app_registry.update_one(
        {"app_id": app_id},
        {"$set": {
            "generated_permissions": permissions,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )

    # Register permissions in the global registry so the admin UI and role
    # validation pick them up. Order matters: deactivate stale keys first
    # (re-ingestion may drop entities) and then upsert the current set.
    keys_to_keep = {p["key"] for p in permissions if isinstance(p, dict) and p.get("key")}
    await deactivate_permissions(app_id, keys_to_keep)
    await register_permissions(permissions, source=SOURCE_INGESTION, app_id=app_id)

    # Validate that role decisions only reference approved permission keys
    approved_keys = {p["key"] for p in permissions if isinstance(p, dict)}
    for decision in role_decisions:
        role_perms = set(decision.get("permissions", []))
        invalid_role_perms = role_perms - approved_keys
        if invalid_role_perms:
            raise ValueError(
                f"Role '{decision.get('name', '?')}' references permissions not in approved set: {invalid_role_perms}"
            )

    for decision in role_decisions:
        action = decision.get("action")

        if action == "create_new":
            role_id = gen_id("role_")
            role_doc = {
                "role_id": role_id,
                "name": decision["name"],
                "display_name": decision["display_name"],
                "description": decision.get("description", ""),
                "color": decision.get("color", "#6366F1"),
                "is_system": False,
                "is_default": False,
                "permissions": decision["permissions"],
                "app_id": app_id,  # Track which app generated this role
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            existing = await db.roles.find_one({"name": decision["name"]})
            if existing:
                logger.warning(f"Role '{decision['name']}' already exists, skipping creation")
                continue
            await db.roles.insert_one(role_doc)
            results["roles_created"].append(decision["name"])
            logger.info(f"Created role: {decision['name']} with {len(decision['permissions'])} permissions")

        elif action == "extend_existing":
            existing_role = decision.get("existing_role_name")
            if not existing_role:
                continue
            new_perms = decision.get("permissions", [])
            await db.roles.update_one(
                {"name": existing_role},
                {"$addToSet": {"permissions": {"$each": new_perms}}}
            )
            results["roles_extended"].append({
                "role": existing_role,
                "permissions_added": len(new_perms),
            })
            logger.info(f"Extended role '{existing_role}' with {len(new_perms)} permissions")

    # Log
    await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage.GENERATE_RBAC,
        action="rbac_applied",
        details=results,
        performed_by=performed_by,
    )

    return results


async def link_ai_employee(
    app_id: str,
    employee_id: str,
    permissions: list[str],
    performed_by: str = None,
) -> dict:
    """
    Link an ingested app to an AI employee by adding the app's
    permissions to the employee's access scope.
    """
    db = get_db()

    employee = await db.ai_employees.find_one({"employee_id": employee_id})
    if not employee:
        raise ValueError(f"AI employee {employee_id} not found")

    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    # Validate permissions against the app's generated permission set
    generated = app.get("generated_permissions", [])
    if generated:
        valid_keys = {p["key"] for p in generated}
        invalid = set(permissions) - valid_keys
        if invalid:
            raise ValueError(f"Permissions not in app's generated set: {invalid}")

    # Add app permissions to the employee's scope
    app_access = {
        "app_id": app_id,
        "app_slug": app.get("app_slug", slug_to_app_slug(app["slug"])),
        "app_name": app["name"],
        "permissions": permissions,
        "linked_at": datetime.now(timezone.utc).isoformat(),
    }

    await db.ai_employees.update_one(
        {"employee_id": employee_id},
        {
            "$push": {"app_access": app_access},
            "$set": {"updated_at": datetime.now(timezone.utc).isoformat()},
        }
    )

    # Log
    await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage.LINK_AI,
        action="ai_employee_linked",
        details={
            "employee_id": employee_id,
            "employee_name": employee.get("name"),
            "permissions_granted": len(permissions),
        },
        performed_by=performed_by,
    )

    return {
        "employee_id": employee_id,
        "employee_name": employee.get("name"),
        "permissions_granted": permissions,
    }


# ---------------------------------------------------------------------------
# Post-ingest RBAC editing
#
# These three functions let admins edit an ingested app's RBAC after the
# initial apply_rbac() call without re-running the pipeline. They are
# careful to:
#   - tag manual additions with source='manual_override' so the existing
#     deactivate_permissions(app_id, ...) sweep on re-ingestion (which
#     filters source=SOURCE_INGESTION) leaves them untouched
#   - never delete permission rows or roles — removals deactivate the row
#     and pull the key out of any role.permissions arrays so historical
#     audit trails stay readable
#   - touch only this app's permission keys when reassigning role perms;
#     the role's permissions for OTHER apps (and other roles for THIS
#     app) are left alone
# ---------------------------------------------------------------------------


async def _app_permission_keys(app_id: str, only_active: bool = True) -> set:
    """Return the registered permission keys belonging to this app.

    Reads from the permission registry rather than the app_registry doc so
    manually-added (source='manual_override') keys are included.
    """
    db = get_db()
    query = {"app_id": app_id}
    if only_active:
        query["is_active"] = True
    cursor = db.permissions.find(query, {"_id": 0, "key": 1})
    docs = await cursor.to_list(length=None)
    return {d["key"] for d in docs if d.get("key")}


async def add_app_permission(app_id: str, perm: dict, performed_by: str) -> dict:
    """Add a permission key to an existing ingested app.

    The new permission is tagged with source='manual_override' so the
    deactivate_permissions sweep that runs on re-ingestion (which only
    targets source='ingestion' rows) leaves it intact.

    perm must contain `key`, `group`, and `label`.

    Raises ValueError if the app is not found or the perm payload is
    missing required fields. Returns the registered permission dict.
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    missing = [f for f in ("key", "group", "label") if not perm.get(f)]
    if missing:
        raise ValueError(
            f"Permission is missing required field(s): {', '.join(missing)}"
        )

    perm_doc = {
        "key": perm["key"],
        "group": perm["group"],
        "label": perm["label"],
    }
    await register_permissions([perm_doc], source=SOURCE_MANUAL_OVERRIDE, app_id=app_id)

    await log_security_event(
        event_type="app_permission_edited",
        severity="info",
        user_id=performed_by,
        resource="app_permission",
        resource_id=app_id,
        action="added",
        outcome="success",
        details={
            "app_id": app_id,
            "app_slug": app.get("app_slug"),
            "permission_key": perm_doc["key"],
            "group": perm_doc["group"],
            "label": perm_doc["label"],
            "source": SOURCE_MANUAL_OVERRIDE,
        },
    )
    logger.info(
        f"Added manual_override permission '{perm_doc['key']}' to app {app_id}"
    )
    return perm_doc


async def remove_app_permission(app_id: str, key: str, performed_by: str) -> dict:
    """Mark an app permission inactive and cascade-pull from any roles.

    Does NOT delete the permission document (preserves history for audit).
    Does NOT delete roles, even if pulling the key empties their
    `permissions` array.

    Returns:
        {"removed_from_roles": [role_name, ...], "deactivated": bool}
        `deactivated` is True if a permission row matched and was
        flipped from active to inactive; False if no matching active row
        existed (e.g. already inactive, or wrong app_id).
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    db = get_db()
    now = datetime.now(timezone.utc)

    perm_result = await db.permissions.update_one(
        {"key": key, "app_id": app_id, "is_active": True},
        {"$set": {"is_active": False, "updated_at": now}},
    )
    deactivated = perm_result.modified_count > 0

    affected_roles = await db.roles.find(
        {"permissions": key}, {"_id": 0, "name": 1}
    ).to_list(length=None)
    removed_from_roles = [r["name"] for r in affected_roles if r.get("name")]

    if removed_from_roles:
        await db.roles.update_many(
            {"permissions": key},
            {
                "$pull": {"permissions": key},
                "$set": {"updated_at": now},
            },
        )

    await log_security_event(
        event_type="app_permission_edited",
        severity="info",
        user_id=performed_by,
        resource="app_permission",
        resource_id=app_id,
        action="removed",
        outcome="success",
        details={
            "app_id": app_id,
            "app_slug": app.get("app_slug"),
            "permission_key": key,
            "deactivated": deactivated,
            "removed_from_roles": removed_from_roles,
        },
    )
    logger.info(
        f"Removed permission '{key}' from app {app_id} "
        f"(deactivated={deactivated}, roles_affected={len(removed_from_roles)})"
    )
    return {
        "removed_from_roles": removed_from_roles,
        "deactivated": deactivated,
    }


async def reassign_app_role_perms(
    app_id: str,
    role_name: str,
    perm_keys: list[str],
    performed_by: str,
) -> dict:
    """Set a role's permissions for THIS app's keys to exactly perm_keys.

    Only the subset of the role's permissions that belong to this app
    (i.e. keys currently registered against `app_id`) is replaced. Any
    permissions the role holds for OTHER apps are preserved untouched,
    and no other roles are modified.

    Validates every entry in `perm_keys` is currently registered (and
    active) for this app.

    Raises:
        ValueError if the app is not found, the role does not exist,
        or any submitted key is not registered for this app.

    Returns:
        {"role": role_name, "app_id": app_id, "perms_set": [...]}
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    db = get_db()

    role = await db.roles.find_one({"name": role_name}, {"_id": 0})
    if not role:
        raise ValueError(f"Role '{role_name}' not found")

    app_keys = await _app_permission_keys(app_id, only_active=True)
    submitted = list(perm_keys or [])
    invalid = set(submitted) - app_keys
    if invalid:
        raise ValueError(
            f"Permission key(s) not registered for app {app_id}: {sorted(invalid)}"
        )

    # Preserve everything the role has that does NOT belong to this app,
    # then add the new set for this app.
    existing_perms = role.get("permissions", []) or []
    preserved = [p for p in existing_perms if p not in app_keys]
    # Dedupe submitted while preserving caller-provided order
    seen = set()
    new_for_app = []
    for k in submitted:
        if k not in seen:
            seen.add(k)
            new_for_app.append(k)
    new_perms = preserved + new_for_app

    await db.roles.update_one(
        {"name": role_name},
        {
            "$set": {
                "permissions": new_perms,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    previous_for_app = [p for p in existing_perms if p in app_keys]
    await log_security_event(
        event_type="app_permission_edited",
        severity="info",
        user_id=performed_by,
        resource="app_permission",
        resource_id=app_id,
        action="reassigned",
        outcome="success",
        details={
            "app_id": app_id,
            "app_slug": app.get("app_slug"),
            "role": role_name,
            "previous_perms_for_app": previous_for_app,
            "perms_set": new_for_app,
        },
    )
    logger.info(
        f"Reassigned role '{role_name}' permissions for app {app_id}: "
        f"{len(new_for_app)} key(s)"
    )
    return {
        "role": role_name,
        "app_id": app_id,
        "perms_set": new_for_app,
    }
