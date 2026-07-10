"""Phase 1B.5 — AI Employee catalog + tenant assignment + provisioning API.

The three-collection multi-tenancy model gets its REST surface:

  * ``ai_employee_definitions`` — the catalog of blueprints. Tenant-
    agnostic but with visibility flags (`is_active`, `is_published`).
  * ``tenant_app_assignments`` — entitlement records. One per
    (tenant, definition) pair (enforced by the compound unique index
    in server.py from 1B.1).
  * ``ai_employees`` — the deployed instances; provisioned from an
    assignment via this module's `instances` endpoint.

## Authorization model

Phase 1B.5 kept all write operations SimpliMeta-root-only. Phase 1C.3
opens the tenant-side write surface:

  * Tenant_admins holding ``ai_employee_assignments.delegate`` can
    create sub-assignments for child tenants (target tenant's
    ``parent_tenant_id`` must equal the caller's tenant_id, and the
    caller must hold an active assignment for the same definition).
  * Tenant_admins holding ``ai_employee_instances.provision`` can
    provision instances from any assignment granted to their own
    tenant.

Phase 1C.4 extends ``.delegate`` to cover REVOKE / EDIT of
delegations the caller's tenant created. A tenant admin can modify
any field on assignments where ``granted_by_tenant_id == caller.tenant_id``,
which automatically excludes upstream grants (those have
``granted_by_tenant_id`` set to the granter, not the receiver).

Revoke-side chain check (1C.4): transitions to ``status="revoked"``
fail with 409 if active sub-delegations depend on the assignment.
Applies symmetrically to root and tenant callers — cascading
destructive ops require explicit intent.

Catalog mutation (creating / editing definitions) remains
SimpliMeta-root-only; cross-tenant grants likewise remain
``ai_employee_assignments.grant`` and root-only.

The single exception is read scoping on `GET /tenants/{tenant_id}/...`:
non-root callers can read their OWN tenant but not others. Root admins
can read any tenant.

## What this module does NOT do

  * Cross-tenant catalog views for tenant_admins (1C.6+).
  * Cascade revoke (``?cascade=true`` query param). Future work.
  * ``max_instances`` budget enforcement across the delegation chain.
    Today a parent can delegate more capacity than they themselves
    hold; proper cross-chain summing is a future workstream.
  * Edits to existing instance rows — the existing PUT/PATCH endpoints
    in routes/ai_employees.py stay as-is for now. The new
    catalog-driven `POST .../instances` here is the additive path; the
    legacy import flow continues to work.

Refs: Phase 1B Locked Decisions, 8 May 2026; Phase 1C plan, 13 May 2026.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_current_user, has_perm
from database import get_db
from models import (
    AIEmployeeDefinition,
    AIEmployeeDefinitionCreate,
    AIEmployeeDefinitionUpdate,
    TenantAppAssignment,
    TenantAppAssignmentCreate,
    TenantAppAssignmentUpdate,
    gen_id,
)
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from services.permission_registry import register_module_permissions

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Permission registration
# ---------------------------------------------------------------------------
# Phase 1C.2 — Register the permissions this module checks at runtime.
#
# Phase 1B.5 introduced the catalog / assignment / instance permission
# keys directly in `has_perm(...)` calls but never registered them with
# the permission registry. That left them invisible to the admin
# permission picker and missing from the implication graph. Registering
# them here (a) cleans up that 1B oversight and (b) provides the home
# for the two new tenant-side delegation perms 1C introduces:
#
#   * `ai_employee_assignments.delegate` — the tenant-side analog of
#     `ai_employee_assignments.grant`. Lets a tenant_admin carve their
#     own assignment into sub-assignments for child tenants. Wired
#     into the assignment-create route in 1C.3.
#   * `ai_employee_instances.provision` (already existed in code) —
#     listed here so it shows up in the picker. Added to the
#     tenant_admin role in routes/roles.py.
#
# Implication edges (`.manage`/`.grant`/`.delegate` imply `.view`) match
# the convention established by `tenants.manage` → `tenants.view` in
# routes/tenants.py. The closure resolver in services/permission_registry
# picks them up automatically.
@register_module_permissions("ai_employee_catalog")
def _ai_employee_catalog_permissions():
    return [
        # Catalog — definitions / blueprints
        {
            "key": "ai_employee_catalog.view",
            "group": "AI Employee Catalog",
            "label": "View AI Employee catalog (definitions)",
            "implies": [],
        },
        {
            "key": "ai_employee_catalog.manage",
            "group": "AI Employee Catalog",
            "label": "Create, edit, and archive catalog definitions",
            "implies": ["ai_employee_catalog.view"],
        },
        # Assignments — tenant entitlements
        {
            "key": "ai_employee_assignments.view",
            "group": "AI Employee Catalog",
            "label": "View AI Employee assignments (entitlements)",
            "implies": [],
        },
        {
            "key": "ai_employee_assignments.grant",
            "group": "AI Employee Catalog",
            "label": "Grant assignments to tenants (SimpliMeta-side)",
            "implies": ["ai_employee_assignments.view"],
        },
        {
            "key": "ai_employee_assignments.delegate",
            "group": "AI Employee Catalog",
            "label": "Delegate sub-assignments to child tenants (tenant-side)",
            "implies": ["ai_employee_assignments.view"],
        },
        # Instances — deployed AI Employees provisioned from assignments
        {
            "key": "ai_employee_instances.provision",
            "group": "AI Employee Catalog",
            "label": "Provision new AI Employee instances from an assignment",
            "implies": [],
        },
    ]


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------

def _is_root_tenant(user) -> bool:
    """True if the caller belongs to the SimpliMeta root tenant.

    Used as the coarse gate for catalog mutation and cross-tenant
    operations. Membership in the root tenant is necessary but not
    sufficient — RBAC permissions are still checked per-endpoint.
    """
    return getattr(user, "tenant_id", None) == SIMPLIMETA_ROOT_TENANT_ID


def _require_root(user) -> None:
    """Raise 403 if the caller is not in the SimpliMeta root tenant.

    Used by write endpoints on definitions + assignments. Provisioning
    instances has a separate path that also accepts root admins.
    """
    if not _is_root_tenant(user):
        raise HTTPException(
            status_code=403,
            detail="This operation is restricted to SimpliMeta administrators.",
        )


def _require_tenant_read_access(tenant_id: str, user) -> None:
    """Raise 403 if the caller cannot read this tenant's data.

    Root admins can read any tenant. Other users can read only their
    own. 404 vs 403 — we use 403 here because the tenant_id is in the
    URL and is therefore already known to the caller; hiding existence
    would be theatre.
    """
    if _is_root_tenant(user):
        return
    if getattr(user, "tenant_id", None) != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="You can only access assignments for your own tenant.",
        )


def _require_own_tenant_or_root(tenant_id: str, user) -> None:
    """Raise 403 if the caller cannot write to this tenant's surface.

    Phase 1C.3 — the write-side analog of `_require_tenant_read_access`.
    Used by the provisioning route, which is now open to tenant_admins
    operating against their OWN tenant's assignments. The permission
    check (`ai_employee_instances.provision`) is layered on top.

    Same 403-not-404 rationale as the read-side helper: the tenant_id
    is in the URL and therefore already known to the caller.
    """
    if _is_root_tenant(user):
        return
    if getattr(user, "tenant_id", None) != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="You can only provision instances for your own tenant.",
        )


# ---------------------------------------------------------------------------
# Internal query helpers
# ---------------------------------------------------------------------------

async def _load_definition_or_404(db, definition_id: str) -> dict:
    doc = await db.ai_employee_definitions.find_one(
        {"definition_id": definition_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Definition not found.")
    return doc


async def _load_assignment_or_404(
        db, tenant_id: str, assignment_id: str
) -> dict:
    """Load an assignment by `assignment_id`, ensuring it belongs to the
    URL-named tenant. Returns 404 if the assignment doesn't exist or
    doesn't match the tenant — we don't want to leak the existence of
    an assignment in another tenant to a caller scoped to this one.
    """
    doc = await db.tenant_app_assignments.find_one(
        {"assignment_id": assignment_id, "tenant_id": tenant_id},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    return doc


# ===========================================================================
# DEFINITIONS — the tenant-agnostic catalog
# ===========================================================================

@router.get("/ai-employee-definitions")
async def list_definitions(
        type: Optional[str] = None,
        current_user=Depends(get_current_user),
):
    """List AI Employee definitions in the catalog.

    Visibility:
      * SimpliMeta root admins see every definition, including
        `is_active=False` archived ones and `is_published=False` drafts.
      * Everyone else sees only published, active definitions.
    """
    if not has_perm(current_user, "ai_employee_catalog.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    query: dict = {}
    if not _is_root_tenant(current_user):
        # Non-root callers see only the public catalog
        query["is_active"] = True
        query["is_published"] = True
    if type:
        query["type"] = type

    docs = (
        await db.ai_employee_definitions.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(500)
    )
    return docs


@router.get("/ai-employee-definitions/{definition_id}")
async def get_definition(
        definition_id: str,
        current_user=Depends(get_current_user),
):
    """Get one definition by ID.

    Same visibility rule as the list endpoint: non-root callers get 404
    (not 403) for unpublished or inactive definitions — they shouldn't
    know those exist.
    """
    if not has_perm(current_user, "ai_employee_catalog.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    doc = await _load_definition_or_404(db, definition_id)

    if not _is_root_tenant(current_user):
        if not doc.get("is_active") or not doc.get("is_published"):
            raise HTTPException(status_code=404, detail="Definition not found.")

    return doc


@router.post("/ai-employee-definitions")
async def create_definition(
        data: AIEmployeeDefinitionCreate,
        current_user=Depends(get_current_user),
):
    """Create a new catalog definition. SimpliMeta root admins only.

    The 1B.4 migration created definitions automatically from existing
    instances. New definitions created via this endpoint are blueprints
    that can be assigned to one or more tenants and provisioned into
    instance rows.
    """
    if not has_perm(current_user, "ai_employee_catalog.manage"):
        raise HTTPException(status_code=403, detail="Permission denied")
    _require_root(current_user)

    db = get_db()
    now = datetime.now(timezone.utc)

    # Build the full definition doc, layering caller input over schema defaults
    doc = AIEmployeeDefinition(
        definition_id=gen_id("def_"),
        name=data.name,
        role=data.role,
        type=data.type,
        description=data.description,
        default_avatar=data.default_avatar or "🤖",
        default_system_prompt=data.default_system_prompt,
        default_router_description=data.default_router_description,
        default_model_provider=data.default_model_provider or "google",
        default_model_name=data.default_model_name or "gemini-2.5-flash",
        default_tools=data.default_tools or [],
        default_knowledge_base_filter=data.default_knowledge_base_filter,
        default_state_schema=data.default_state_schema,
        default_n8n_template_url=data.default_n8n_template_url,
        default_capabilities=data.default_capabilities or [],
        default_metric_labels=data.default_metric_labels or {},
        default_metrics_source=data.default_metrics_source or "manual",
        provisioning_mode=data.provisioning_mode or "native",
        is_active=True,
        is_published=data.is_published if data.is_published is not None else True,
        created_by_user_id=current_user.user_id,
        created_by_tenant_id=SIMPLIMETA_ROOT_TENANT_ID,
        created_at=now,
        updated_at=now,
    ).model_dump()

    await db.ai_employee_definitions.insert_one(doc)
    logger.info(
        "Catalog definition created: %s (definition_id=%s, type=%s, by=%s)",
        doc["name"], doc["definition_id"], doc["type"], current_user.user_id,
    )
    # motor mutates doc to add an _id (ObjectId) post-insert; strip it so
    # FastAPI's JSON encoder doesn't choke on the un-serializable type.
    doc.pop("_id", None)
    return doc


@router.patch("/ai-employee-definitions/{definition_id}")
async def update_definition(
        definition_id: str,
        data: AIEmployeeDefinitionUpdate,
        current_user=Depends(get_current_user),
):
    """Update a catalog definition. SimpliMeta root admins only.

    Updating a default does NOT retroactively change instances that have
    already been deployed — the instance row's stored values are
    authoritative for that instance, and the override mechanism means
    instances stick to what they were provisioned with.
    """
    if not has_perm(current_user, "ai_employee_catalog.manage"):
        raise HTTPException(status_code=403, detail="Permission denied")
    _require_root(current_user)

    db = get_db()
    await _load_definition_or_404(db, definition_id)  # 404 if missing

    patch = data.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update.")
    patch["updated_at"] = datetime.now(timezone.utc)

    await db.ai_employee_definitions.update_one(
        {"definition_id": definition_id},
        {"$set": patch},
    )
    doc = await db.ai_employee_definitions.find_one(
        {"definition_id": definition_id}, {"_id": 0}
    )
    logger.info(
        "Catalog definition updated: %s (fields=%s, by=%s)",
        definition_id, sorted(patch.keys()), current_user.user_id,
    )
    return doc


# ===========================================================================
# ASSIGNMENTS — entitlements granting a tenant access to a definition
# ===========================================================================

@router.get("/tenants/{tenant_id}/assignments")
async def list_assignments(
        tenant_id: str,
        status: Optional[str] = None,
        current_user=Depends(get_current_user),
):
    """List all assignments granted to a tenant.

    Caller must either be a SimpliMeta root admin OR a member of the
    target tenant. Non-root callers querying another tenant get 403.
    """
    if not has_perm(current_user, "ai_employee_assignments.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    _require_tenant_read_access(tenant_id, current_user)

    db = get_db()
    query: dict = {"tenant_id": tenant_id}
    if status:
        query["status"] = status

    docs = (
        await db.tenant_app_assignments.find(query, {"_id": 0})
        .sort("granted_at", -1)
        .to_list(500)
    )
    return docs


@router.post("/tenants/{tenant_id}/assignments")
async def create_assignment(
        tenant_id: str,
        data: TenantAppAssignmentCreate,
        current_user=Depends(get_current_user),
):
    """Create an assignment granting a definition to a tenant.

    Two distinct caller flows share this endpoint:

      * **Root grant** — SimpliMeta root admins holding
        ``ai_employee_assignments.grant`` can create an assignment for
        ANY tenant. This is the platform-level entitlement path
        (root grants Orion to Acme, Nova to BetaCorp, etc.).
      * **Tenant delegation (Phase 1C.3)** — non-root callers holding
        ``ai_employee_assignments.delegate`` can create an assignment
        for a tenant that is a child of their own tenant in the
        hierarchy. They MUST themselves hold an active assignment for
        the same ``definition_id``; you cannot delegate what you do
        not have. This is the "Acme delegates Orion to its sub-account
        AcmeSub" path.

    The compound unique index on (tenant_id, definition_id) means a
    second grant for the same pair fails at the DB layer with a
    DuplicateKey error. We catch that and return 409 Conflict with a
    pointer to the existing assignment so the caller can PATCH it
    (e.g., to re-activate a revoked one) instead of POSTing again.

    Revoke-side chain validation (preventing a tenant_admin from
    revoking the parent assignment that backs their delegations,
    which would orphan downstream instances) lands in Phase 1C.4.
    """
    # Phase 1C.3 — Split authorization. Root admins use `.grant` (any
    # tenant); tenant admins use `.delegate` (own-tenant-children only,
    # and only if they hold the parent assignment).
    is_root_caller = _is_root_tenant(current_user)
    caller_has_grant = has_perm(current_user, "ai_employee_assignments.grant")
    caller_has_delegate = has_perm(current_user, "ai_employee_assignments.delegate")

    if is_root_caller:
        if not caller_has_grant:
            raise HTTPException(status_code=403, detail="Permission denied")
    else:
        if not caller_has_delegate:
            raise HTTPException(status_code=403, detail="Permission denied")

    # The URL tenant_id is the authority; the body's tenant_id (if any)
    # must match to avoid a confused-deputy bug.
    if data.tenant_id and data.tenant_id != tenant_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Body tenant_id ({data.tenant_id}) does not match "
                f"URL tenant_id ({tenant_id})."
            ),
        )

    db = get_db()

    # Verify the target tenant exists. The compound unique index catches
    # the assignment dupe case; tenant existence is a separate FK we
    # have to enforce in app code since Mongo doesn't do FKs.
    target_tenant = await db.tenants.find_one(
        {"tenant_id": tenant_id},
        {"_id": 0, "tenant_id": 1, "parent_tenant_id": 1},
    )
    if not target_tenant:
        raise HTTPException(status_code=404, detail="Target tenant not found.")

    # Phase 1C.3 — Tenant-side delegation chain check (create surface).
    #
    # Non-root callers may only delegate downward. Two constraints:
    #
    #   1. The TARGET tenant must be a direct child of the caller's
    #      tenant (target.parent_tenant_id == caller.tenant_id).
    #      Self-delegation and cross-hierarchy delegation are blocked.
    #      Self-delegation has no semantic meaning (the compound unique
    #      index on (tenant_id, definition_id) would catch it anyway,
    #      but failing fast is friendlier). Cross-hierarchy delegation
    #      would be a privilege-escalation vector.
    #
    #   2. The CALLER must themselves hold an active assignment for the
    #      same definition_id. You cannot delegate Orion to AcmeSub if
    #      Acme doesn't have Orion in the first place. This is the
    #      create-side leg of the chain validation; the revoke-side leg
    #      (preventing breakage of an existing delegation chain) is
    #      enforced in 1C.4.
    if not is_root_caller:
        caller_tenant_id = getattr(current_user, "tenant_id", None)
        if target_tenant.get("parent_tenant_id") != caller_tenant_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Tenant delegation is only permitted to direct child "
                    "tenants in your hierarchy."
                ),
            )

        parent_assignment = await db.tenant_app_assignments.find_one(
            {
                "tenant_id": caller_tenant_id,
                "definition_id": data.definition_id,
                "status": "active",
            },
            {"_id": 0, "assignment_id": 1, "max_instances": 1},
        )
        if not parent_assignment:
            raise HTTPException(
                status_code=403,
                detail=(
                    "You can only delegate definitions you yourself hold "
                    "as an active assignment."
                ),
            )

    # Verify the definition exists + is active. Granting an inactive
    # definition is a footgun — block it here.
    definition = await _load_definition_or_404(db, data.definition_id)
    if not definition.get("is_active"):
        raise HTTPException(
            status_code=400,
            detail="Cannot grant an inactive definition.",
        )

    now = datetime.now(timezone.utc)
    doc = TenantAppAssignment(
        assignment_id=gen_id("asn_"),
        definition_id=data.definition_id,
        tenant_id=tenant_id,
        granted_by_user_id=current_user.user_id,
        granted_by_tenant_id=getattr(current_user, "tenant_id", SIMPLIMETA_ROOT_TENANT_ID),
        status="active",
        granted_at=now,
        max_instances=data.max_instances,
        expires_at=data.expires_at,
        notes=data.notes,
        updated_at=now,
    ).model_dump()

    try:
        await db.tenant_app_assignments.insert_one(doc)
    except Exception as e:
        # DuplicateKey error from the compound unique index
        if "duplicate key" in str(e).lower():
            existing = await db.tenant_app_assignments.find_one(
                {"tenant_id": tenant_id, "definition_id": data.definition_id},
                {"_id": 0, "assignment_id": 1, "status": 1},
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "This tenant already has an assignment for that "
                        "definition. PATCH the existing assignment instead."
                    ),
                    "existing_assignment_id": existing.get("assignment_id") if existing else None,
                    "existing_status": existing.get("status") if existing else None,
                },
            )
        raise

    logger.info(
        "Assignment %s: tenant=%s, definition=%s → assignment_id=%s (by=%s, by_tenant=%s)",
        "delegated" if not is_root_caller else "granted",
        tenant_id, data.definition_id, doc["assignment_id"],
        current_user.user_id, doc["granted_by_tenant_id"],
    )
    # motor mutates doc to add an _id (ObjectId) post-insert; strip it so
    # FastAPI's JSON encoder doesn't choke on the un-serializable type.
    doc.pop("_id", None)
    return doc


@router.patch("/tenants/{tenant_id}/assignments/{assignment_id}")
async def update_assignment(
        tenant_id: str,
        assignment_id: str,
        data: TenantAppAssignmentUpdate,
        current_user=Depends(get_current_user),
):
    """Modify an existing assignment.

    Two distinct caller flows share this endpoint:

      * **Root** — SimpliMeta root admins holding
        ``ai_employee_assignments.grant`` can modify any assignment,
        any field.
      * **Tenant delegation owner (Phase 1C.4)** — non-root callers
        holding ``ai_employee_assignments.delegate`` can modify only
        assignments their own tenant delegated (i.e. where
        ``granted_by_tenant_id == caller.tenant_id``). Same field
        surface as root.

    The ownership rule (``granted_by_tenant_id`` must equal the
    caller's tenant) is what blocks the dangerous case — an Acme
    admin trying to revoke root's grant of Orion to Acme — because
    that grant has ``granted_by_tenant_id == ROOT``, not Acme. No
    special "can't revoke upstream" rule needed; the symmetric
    ownership rule covers it.

    Most common use: revoke an assignment via ``{"status": "revoked"}``.
    Revocation is a status transition (per 1B.1's design) rather than
    a DELETE so the audit chain stays intact and deployed instances
    retain their back-references.

    **Revoke-side chain check (Phase 1C.4)** — if the caller is
    transitioning to ``status="revoked"`` AND active sub-delegations
    exist underneath this assignment (i.e. assignments where
    ``granted_by_tenant_id == this.tenant_id`` AND
    ``definition_id == this.definition_id`` AND ``status=="active"``),
    the request fails with 409 and the response body lists the
    blocking sub-assignment IDs. The caller must revoke those first.
    The check applies to root admins too — cascading destructive ops
    are opt-in (future ``?cascade=true``), not implicit.
    """
    # Phase 1C.4 — Split authorization. Root admins use `.grant` (any
    # assignment); tenant admins use `.delegate` (only assignments they
    # themselves delegated, validated post-load).
    is_root_caller = _is_root_tenant(current_user)
    required_perm = (
        "ai_employee_assignments.grant"
        if is_root_caller
        else "ai_employee_assignments.delegate"
    )
    if not has_perm(current_user, required_perm):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    existing = await _load_assignment_or_404(db, tenant_id, assignment_id)

    # Phase 1C.4 — Ownership check for tenant admins. They can only
    # modify assignments their OWN tenant delegated. This is the load-
    # bearing rule that prevents a child tenant_admin from revoking
    # an upstream grant: upstream grants have granted_by_tenant_id set
    # to the granter (root or some parent tenant), not the child.
    if not is_root_caller:
        caller_tenant_id = getattr(current_user, "tenant_id", None)
        if existing.get("granted_by_tenant_id") != caller_tenant_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "You can only modify assignments your tenant "
                    "delegated. This assignment was granted by another "
                    "tenant."
                ),
            )

    patch = data.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update.")

    # Phase 1C.4 — Revoke-side chain check. Only fires on the
    # transition active→revoked (not idempotent re-revoke, not
    # other field updates). Applies symmetrically to root and tenant
    # callers — cascading destructive ops require explicit intent.
    is_revoking = (
            patch.get("status") == "revoked"
            and existing.get("status") != "revoked"
    )
    if is_revoking:
        sub_delegations_cursor = db.tenant_app_assignments.find(
            {
                "granted_by_tenant_id": existing["tenant_id"],
                "definition_id": existing["definition_id"],
                "status": "active",
            },
            {"_id": 0, "assignment_id": 1, "tenant_id": 1},
        )
        sub_delegations = await sub_delegations_cursor.to_list(length=100)
        if sub_delegations:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        f"Cannot revoke: {len(sub_delegations)} active "
                        "sub-delegation(s) depend on this assignment. "
                        "Revoke them first, then retry."
                    ),
                    "blocking_sub_assignments": sub_delegations,
                },
            )

        # Stamp the revoke audit trail. revoked_at existed pre-1C.4;
        # revoked_by_user_id and revoked_by_tenant_id are new.
        patch["revoked_at"] = datetime.now(timezone.utc)
        patch["revoked_by_user_id"] = current_user.user_id
        patch["revoked_by_tenant_id"] = getattr(
            current_user, "tenant_id", SIMPLIMETA_ROOT_TENANT_ID
        )

    patch["updated_at"] = datetime.now(timezone.utc)

    await db.tenant_app_assignments.update_one(
        {"assignment_id": assignment_id, "tenant_id": tenant_id},
        {"$set": patch},
    )
    doc = await db.tenant_app_assignments.find_one(
        {"assignment_id": assignment_id}, {"_id": 0}
    )
    logger.info(
        "Assignment patched: assignment_id=%s, fields=%s "
        "(by=%s, by_tenant=%s, action=%s)",
        assignment_id, sorted(patch.keys()),
        current_user.user_id,
        getattr(current_user, "tenant_id", SIMPLIMETA_ROOT_TENANT_ID),
        "revoke" if is_revoking else "update",
    )
    return doc


# ===========================================================================
# INSTANCES — provisioning from an assignment
# ===========================================================================

def _gen_ai_employee_id() -> str:
    return f"ai_{uuid.uuid4().hex[:12]}"


def _gen_webhook_token() -> str:
    return secrets.token_urlsafe(32)


@router.post("/tenants/{tenant_id}/assignments/{assignment_id}/instances")
async def provision_instance(
        tenant_id: str,
        assignment_id: str,
        overrides: Optional[dict] = None,
        current_user=Depends(get_current_user),
):
    """Provision a new AI Employee instance for a tenant from an
    active assignment.

    Body is an optional dict of overrides — fields that override the
    definition's defaults for this specific instance. Common ones:

      * ``name``: per-tenant brand for the instance (e.g. "Acme's Orion")
      * ``system_prompt``: tenant-customized prompt
      * ``model_provider``, ``model_name``: per-tenant model selection
      * ``allowed_roles``, ``allowed_user_ids``: within-tenant access control
      * ``n8n_instance_url``, ``n8n_api_key``, ``n8n_workflow_id``: for
        n8n-type instances cloned per-tenant

    Validates:
      * Caller holds ``ai_employee_instances.provision`` and is either
        a SimpliMeta root admin (any tenant) or operating against their
        OWN tenant.
      * Assignment exists, belongs to this tenant, and has
        ``status="active"``.
      * Definition referenced by the assignment exists and is active.
      * ``max_instances`` cap on the assignment is not exceeded.

    Side effects:
      * Inserts the new instance row in ``ai_employees`` with full
        linkage (`definition_id`, `tenant_id`, `assignment_id`).
      * Creates a backing user record stamped with the same `tenant_id`
        as the instance (so the AI authz middleware finds the right
        tenant when an AI request lands).
      * Registers the new instance with the agent marketplace so the
        chat router can route to it immediately, without needing a
        server restart.

    Phase 1B.5 — root admins only.
    Phase 1C.3 — opened to tenant_admins for their own tenant. The
    `_load_assignment_or_404` helper already scopes the assignment
    lookup by `tenant_id`, so a cross-tenant assignment_id is
    indistinguishable from a non-existent one in the response. The
    own-tenant guard below adds defense in depth at the URL level.
    """
    if not has_perm(current_user, "ai_employee_instances.provision"):
        raise HTTPException(status_code=403, detail="Permission denied")
    _require_own_tenant_or_root(tenant_id, current_user)

    overrides = overrides or {}
    db = get_db()

    assignment = await _load_assignment_or_404(db, tenant_id, assignment_id)
    if assignment.get("status") != "active":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Assignment is not active (status={assignment.get('status')}). "
                f"Re-activate it via PATCH before provisioning new instances."
            ),
        )

    definition = await _load_definition_or_404(db, assignment["definition_id"])
    if not definition.get("is_active"):
        raise HTTPException(
            status_code=400,
            detail="Definition is inactive; cannot provision new instances.",
        )

    # Enforce max_instances cap (the "Acme can deploy up to 3" guardrail)
    max_instances = assignment.get("max_instances")
    if max_instances is not None:
        current_count = await db.ai_employees.count_documents(
            {"assignment_id": assignment_id}
        )
        if current_count >= max_instances:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Assignment has reached its max_instances cap "
                    f"({current_count}/{max_instances}). Revoke an existing "
                    f"instance before provisioning a new one."
                ),
            )

    # Build the instance document — start from definition defaults,
    # layer the caller's overrides, then add linkage.
    employee_id = _gen_ai_employee_id()
    webhook_token = _gen_webhook_token()
    now = datetime.now(timezone.utc)

    # Import here to avoid a circular module dependency at import time.
    # routes.ai_employees imports SIMPLIMETA_ROOT_TENANT_ID from seeds,
    # which doesn't import this module — but we still want to keep the
    # backing-user helper colocated with the other employee mechanics.
    from routes.ai_employees import _ensure_backing_user

    name = overrides.get("name") or definition["name"]
    backing_user_id = await _ensure_backing_user(
        db, employee_id, name, tenant_id=tenant_id
    )

    type_ = definition["type"]
    employee = {
        "employee_id": employee_id,
        "user_id": backing_user_id,
        "name": name,
        "role": overrides.get("role") or definition.get("role", "AI Employee"),
        "description": overrides.get("description") or definition.get("description", "") or "",
        "avatar": overrides.get("avatar") or definition.get("default_avatar", "🤖"),
        "type": type_,
        "status": overrides.get("status") or "active",
        "mode": "manual" if type_ == "conversational" else "automatic",

        # Phase 1B.5 linkage — set at creation, never changed after
        "definition_id": definition["definition_id"],
        "tenant_id": tenant_id,
        "assignment_id": assignment_id,

        # Connection details
        "connection": {},

        # Metrics — labels lift from the definition default, values start at 0
        "metrics_config": {
            "metric1_label": (definition.get("default_metric_labels") or {}).get(
                "metric1", "Sessions" if type_ == "conversational" else "Executions"
            ),
            "metric2_label": (definition.get("default_metric_labels") or {}).get(
                "metric2", "Avg Response Time" if type_ == "conversational" else "Success Rate"
            ),
            "metric3_label": (definition.get("default_metric_labels") or {}).get(
                "metric3", "Satisfaction" if type_ == "conversational" else "Tasks Completed"
            ),
            "source": definition.get("default_metrics_source", "manual"),
        },
        "metrics": {"metric1": 0, "metric2": 0, "metric3": 0},
        "execution_count": 0,
        "success_count": 0,
        "error_count": 0,
        "last_execution_at": None,
        "execution_log": [],

        # Catalog metadata lifted as instance defaults
        "specialties": overrides.get("specialties") or definition.get("default_capabilities") or [],
        "contractor": {"name": "", "agreement_id": ""},

        # Webhook (for N8N → AEGISAXIOM communication, even for conversational
        # types — keeps shape consistent with the legacy import path)
        "webhook_token": webhook_token,

        # Access control — defaults sized to the target tenant.
        #
        # Phase 1D.8 fix: previously this defaulted unconditionally to
        # ["admin"], which is correct for SimpliMeta root provisions
        # (root users hold the `admin` role) but wrong for partner /
        # account provisions, where the tenant's primary user role is
        # `tenant_admin`. With the old default, a tenant_admin logging
        # in could not see instances provisioned for their own tenant
        # because `_user_can_access_employee` matched neither
        # allowed_roles (["admin"]) nor any other grant path. Caught
        # during 1D.8 manual QA — the first end-to-end tenant_admin
        # login surfaced it immediately.
        #
        # Root tenant → admin (existing behaviour, no regression).
        # Other tenants → tenant_admin (the role the 1D.8 invite flow
        # actually stamps on invited users).
        "allowed_roles": (
            overrides.get("allowed_roles")
            or (["admin"] if tenant_id == SIMPLIMETA_ROOT_TENANT_ID else ["tenant_admin"])
        ),
        "allowed_user_ids": overrides.get("allowed_user_ids") or [],

        # Audit
        "created_by": current_user.user_id,
        "created_at": now,
        "updated_at": now,
    }

    # Conversational vs n8n connection details
    if type_ == "conversational":
        employee["connection"] = {
            "embedded_url": overrides.get("embedded_url") or "",
            "embed_code": overrides.get("embed_code") or "",
        }
        # Conversational AI fields — apply override first, fall back to default
        for field, default_key in [
            ("system_prompt", "default_system_prompt"),
            ("router_description", "default_router_description"),
            ("model_provider", "default_model_provider"),
            ("model_name", "default_model_name"),
            ("tools", "default_tools"),
            ("state_schema", "default_state_schema"),
        ]:
            value = overrides.get(field)
            if value is None:
                value = definition.get(default_key)
            if value is not None:
                employee[field] = value
        kb_filter = overrides.get("knowledge_base_filter") or definition.get(
            "default_knowledge_base_filter"
        )
        if kb_filter:
            employee["knowledge_base"] = {
                "collection": "ai_employee_knowledge",
                "filter": kb_filter,
            }
    elif type_ == "n8n":
        employee["connection"] = {
            "n8n_instance_url": overrides.get("n8n_instance_url") or "",
            "n8n_api_key": overrides.get("n8n_api_key") or "",
            "workflow_id": overrides.get("n8n_workflow_id") or "",
        }

    await db.ai_employees.insert_one(employee)

    # Register with the agent marketplace so the chat router picks it up
    # immediately (no server restart needed). Import here so module load
    # doesn't pull the agents subsystem unless an instance gets provisioned.
    try:
        from services.ai.agents.marketplace import register_ai_employee
        await register_ai_employee(db, employee)
    except Exception as e:  # pragma: no cover
        logger.warning(
            "Provisioned %s but failed to register with marketplace: %s",
            employee_id, e,
        )

    logger.info(
        "Instance provisioned: employee_id=%s, name=%s, tenant=%s, "
        "definition=%s, assignment=%s (by=%s)",
        employee_id, name, tenant_id, definition["definition_id"],
        assignment_id, current_user.user_id,
    )

    # Strip _id from response (insert_one doesn't return the doc)
    employee.pop("_id", None)
    return employee


@router.get("/tenants/{tenant_id}/instances")
async def list_tenant_instances(
        tenant_id: str,
        current_user=Depends(get_current_user),
):
    """List AI Employee instances belonging to a tenant.

    Phase 1D.6 — admin read surface for the Tenant Detail page.

    Distinct from the existing /api/ai-employees endpoint, which is
    strictly caller-scoped (Phase 1B.3 — `_employee_visible_to_tenant`
    intentionally rejects cross-tenant reads even for root admins).
    This endpoint is target-scoped, with the standard
    `_require_own_tenant_or_root` gate:

      * Root admins can list any tenant's instances.
      * Tenant admins can list only their own tenant's instances.
      * Other callers get 403.

    Used by the Tenant Detail page to compute `count / max_instances`
    per assignment row. Returns a bare array sorted by created_at desc.

    The n8n_api_key field is masked on response, mirroring the existing
    /api/ai-employees behaviour so the secret never leaves the server
    unredacted on this surface either.
    """
    if not has_perm(current_user, "ai_employees.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    _require_own_tenant_or_root(tenant_id, current_user)

    db = get_db()
    docs = (
        await db.ai_employees.find(
            {"tenant_id": tenant_id}, {"_id": 0}
        )
        .sort("created_at", -1)
        .to_list(1000)
    )

    for emp in docs:
        conn = emp.get("connection")
        if conn and "n8n_api_key" in conn:
            conn["n8n_api_key"] = (
                conn["n8n_api_key"][:4] + "****" if conn["n8n_api_key"] else ""
            )
    return docs


@router.delete("/tenants/{tenant_id}/instances/{employee_id}")
async def delete_tenant_instance(
        tenant_id: str,
        employee_id: str,
        current_user=Depends(get_current_user),
):
    """Delete an AI Employee instance belonging to a tenant.

    Phase 1D.7 — admin write surface for per-instance deletion.

    Distinct from DELETE /api/ai-employees/{id}, which is caller-scoped
    (Phase 1B.3 — `_employee_visible_to_tenant` rejects cross-tenant
    deletes even for root admins). This endpoint is target-scoped with
    the standard `_require_own_tenant_or_root` gate, symmetric to
    GET /api/tenants/{id}/instances added in 1D.6.

    Frees one slot against the parent assignment's max_instances cap.
    The next provision call will re-count and the new instance will fit.

    Side effects:
      * Unregisters the employee from the conductor's agent marketplace
      * Deletes the ai_employees row
      * Logs a security event for the audit trail
      * Does NOT delete the backing user record — that's stamped on
        the tenant and may be referenced by audit logs/other surfaces.
        Backing user cleanup is a separate concern (P3 backlog).
    """
    if not has_perm(current_user, "ai_employees.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    _require_own_tenant_or_root(tenant_id, current_user)

    db = get_db()
    existing = await db.ai_employees.find_one({"employee_id": employee_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Defense in depth — the URL claims this instance belongs to
    # tenant_id; verify before deleting. Prevents the bug where a
    # malformed URL ("delete instance X from tenant Y" when X really
    # belongs to Z) succeeds because the only filter was employee_id.
    if existing.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Mirror the conductor unregister behaviour from the original
    # DELETE /ai-employees endpoint. Lazy import to avoid circulars.
    try:
        from services.ai.agents.marketplace import unregister_ai_employee
        unregister_ai_employee(employee_id)
    except Exception as exc:
        logger.warning(
            "Marketplace unregister failed for %s: %s", employee_id, exc
        )

    # Audit trail. Lazy imports for the same reason.
    try:
        from services.security_logger import log_security_event
        await log_security_event(
            event_type="ai_employee_deleted",
            severity="warning",
            user_id=current_user.user_id,
            user_email=current_user.email,
            resource="ai_employee",
            resource_id=employee_id,
            action="delete",
            outcome="success",
            details={
                "name": existing.get("name", ""),
                "tenant_id": tenant_id,
                "assignment_id": existing.get("assignment_id"),
                "source": "tenant_admin_ui",
            },
        )
    except Exception as exc:
        logger.warning("Audit log failed for delete %s: %s", employee_id, exc)

    await db.ai_employees.delete_one({"employee_id": employee_id})

    logger.info(
        "AI Employee instance deleted: employee_id=%s tenant_id=%s "
        "(by=%s, by_tenant=%s)",
        employee_id, tenant_id,
        current_user.user_id,
        getattr(current_user, "tenant_id", SIMPLIMETA_ROOT_TENANT_ID),
    )

    return {"message": "Instance deleted", "employee_id": employee_id}


@router.get("/tenants/{tenant_id}/users")
async def list_tenant_users(
        tenant_id: str,
        current_user=Depends(get_current_user),
):
    """List users belonging to a tenant.

    Phase 1D.8 — admin read surface for the Tenant Detail page's Users
    tab. Mirrors the pattern of /tenants/{id}/assignments and
    /tenants/{id}/instances: target-scoped via
    `_require_own_tenant_or_root`, distinct from the generic /api/users
    listing (which is caller-scoped and would 404 a root admin trying
    to view a partner tenant's users).

    Returns a bare array with the fields the Users tab needs:
    user_id, email, name, role, is_active, is_email_verified,
    has_password (derived from password_hash presence), and created_at.
    Sensitive fields (password_hash, invite_token, MFA secrets) are
    NEVER returned on this endpoint.

    The derived `status` is a UI convenience: 'invited' (no password
    set yet), 'active' (password set, is_active true), or 'inactive'
    (was deactivated by an admin). Computed client-side from
    is_active + has_password so the API stays close to the raw model.
    """
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    _require_own_tenant_or_root(tenant_id, current_user)

    db = get_db()

    # Projection: pull only what the Users tab needs. Explicitly exclude
    # password_hash and invite_token so a slip on the frontend can't
    # leak them. last_login_at is included if present (some user records
    # may not have it yet — those just don't show one in the UI).
    projection = {
        "_id": 0,
        "user_id": 1,
        "email": 1,
        "name": 1,
        "role": 1,
        "is_active": 1,
        "is_email_verified": 1,
        "created_at": 1,
        "last_login_at": 1,
        # password_hash and invite_token DELIBERATELY omitted.
        # has_password is derived below from existence, not value.
    }
    docs = (
        await db.users.find({"tenant_id": tenant_id}, projection)
        .sort("created_at", -1)
        .to_list(1000)
    )

    # Derive has_password from the raw user records, without exposing
    # the hash itself. Two queries would be cleaner but slower; instead
    # we fetch the full doc projection without password_hash, then add
    # the flag via a second small projection. Practically, fold into a
    # single query with a $cond pipeline. For clarity and small N, do
    # it simply: per-user existence check.
    if docs:
        # Single batch query for the has_password flag set, scoped to
        # the tenant we already verified. Returns just user_ids that
        # have a non-empty password_hash.
        ids_with_password = set()
        async for u in db.users.find(
            {
                "tenant_id": tenant_id,
                "user_id": {"$in": [d["user_id"] for d in docs]},
                "password_hash": {"$nin": [None, ""]},
            },
            {"_id": 0, "user_id": 1},
        ):
            ids_with_password.add(u["user_id"])
        for d in docs:
            d["has_password"] = d["user_id"] in ids_with_password

    return docs