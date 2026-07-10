"""Phase 1E.1 — Tenant App Grants API.

The fourth multi-tenancy collection joins the model:

  * ``ai_employee_definitions``   — AI Employee blueprint catalog (1B.1)
  * ``tenant_app_assignments``    — AI Employee entitlements (1B.1)
  * ``ai_employees``              — deployed AI Employee instances (1B.1)
  * ``tenant_app_grants``         — APP entitlements (1E.1, this module)

Where ``tenant_app_assignments`` grants a tenant an *AI Employee
definition*, ``tenant_app_grants`` grants a tenant an *ingested
application* from the App Ingestion catalog (``app_registry``). The
two collections deliberately mirror each other so the admin UI can
treat them symmetrically.

## Why a separate collection (not extending `tenant_app_assignments`)

Apps are not instance-counted. AI Employees are. Reusing the
assignment collection would have meant adding a discriminator column
plus a bunch of nullable instance-related fields. Two clean
collections, one entity each, is cheaper to reason about and avoids
mixed-shape rows in the DB.

## Authorization model (1E.1 — root-only)

All read and write operations are restricted to SimpliMeta-root
admins for the duration of Phase 1E. The intent is to land the
plumbing first, then open it up to the rest of the SimpliMeta team
(admin, sales_rep, sales_ops, named individuals) as the final step
of 1E before partner onboarding. Two new permission keys:

  * ``tenant_app_grants.view``  — list / read grants for a tenant
  * ``tenant_app_grants.grant`` — create / modify grants

Tenant-side delegation (analog of ``ai_employee_assignments.delegate``)
is deferred to 1F when there's an actual partner asking for it. The
current model just has SimpliMeta granting apps to direct tenants.

## What 1E.1 does NOT do

  * Tenant-side delegation. A tenant_admin cannot sub-grant apps to
    child tenants. Future workstream when delegation demand surfaces.
  * Dynamic sidebar rendering for tenant users. Reading the grant
    list and showing only granted apps in the sidebar is 1E.2.
  * Per-app route-layer tenant scoping. Granting "Projects" to
    AppDirect doesn't yet filter the Projects routes by tenant_id.
    That's 1E.3, scoped just-in-time per app actually granted.
  * Revoke-side chain check. There's no delegation chain in this
    phase so there's nothing to chain-check.

Refs: Phase 1D acceptance run (18 May 2026); Ryan/Samson 1:1
(18 May 2026); 1E plan locked 19 May 2026.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_current_user, has_perm
from database import get_db
from models import (
    TenantAppGrant,
    TenantAppGrantCreate,
    TenantAppGrantUpdate,
    gen_id,
)
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from services.permission_registry import register_module_permissions
from services.permission_seeder import (
    revoke_app_permissions,
    seed_app_permissions,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Permission registration
# ---------------------------------------------------------------------------
@register_module_permissions("tenant_app_grants")
def _tenant_app_grants_permissions():
    """Register the 1E.1 permission keys with the permission registry.

    Convention mirrors ``ai_employee_assignments.*``: a ``.view`` key
    for read access and a ``.grant`` key for write access (implies
    ``.view`` via the closure resolver in services/permission_registry).
    Both are root-only in 1E.1; opening up to other roles is the
    final 1E step.
    """
    return [
        {
            "key": "tenant_app_grants.view",
            "group": "Tenant App Grants",
            "label": "View app grants on a tenant",
            "implies": [],
        },
        {
            "key": "tenant_app_grants.grant",
            "group": "Tenant App Grants",
            "label": "Grant or revoke ingested apps on a tenant",
            "implies": ["tenant_app_grants.view"],
        },
    ]


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------

def _is_root_tenant(user) -> bool:
    """True if the caller belongs to the SimpliMeta root tenant."""
    return getattr(user, "tenant_id", None) == SIMPLIMETA_ROOT_TENANT_ID


def _require_root(user) -> None:
    """Raise 403 if the caller is not in the SimpliMeta root tenant.

    1E.1 is root-only across all endpoints — the team-rollout step
    (last in the 1E sequence) will swap this for a role-based gate.
    """
    if not _is_root_tenant(user):
        raise HTTPException(
            status_code=403,
            detail="Tenant app grants are restricted to SimpliMeta administrators.",
        )


# ---------------------------------------------------------------------------
# Internal query helpers
# ---------------------------------------------------------------------------

async def _load_app_or_404(db, app_id: str) -> dict:
    """Load an app_registry row by app_id. 404 if missing or soft-deleted."""
    doc = await db.app_registry.find_one(
        {"app_id": app_id, "deleted_at": None}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="App not found.")
    return doc


async def _load_grant_or_404(db, tenant_id: str, grant_id: str) -> dict:
    """Load a grant by grant_id, ensuring it belongs to the URL-named tenant.

    Returns 404 if the grant doesn't exist or doesn't match the
    tenant. We don't leak existence of grants in other tenants to a
    caller scoped to this one. Mirrors ``_load_assignment_or_404``.
    """
    doc = await db.tenant_app_grants.find_one(
        {"grant_id": grant_id, "tenant_id": tenant_id},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="App grant not found.")
    return doc


async def _load_tenant_or_404(db, tenant_id: str) -> dict:
    doc = await db.tenants.find_one(
        {"tenant_id": tenant_id},
        {"_id": 0, "tenant_id": 1, "tenant_type": 1, "parent_tenant_id": 1, "status": 1},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Target tenant not found.")
    return doc


# ===========================================================================
# CATALOG — grantable apps (SimpliMeta-root view)
# ===========================================================================

@router.get("/tenant-app-grants/catalog")
async def list_grantable_apps(current_user=Depends(get_current_user)):
    """List apps eligible to be granted to a tenant.

    Returns app_registry rows with ``status == 'active'`` only —
    pending / failed / archived apps are not grantable. The picker on
    the Grant App modal calls this; non-active apps simply don't
    appear in the dropdown.

    Root-only in 1E.1. The endpoint lives in this module (rather than
    in routes/app_ingestion.py) so the gate is the grant-side
    permission, not the ingestion-management permission. They're
    conceptually different operations even if the underlying data
    is the same.
    """
    _require_root(current_user)
    if not has_perm(current_user, "tenant_app_grants.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    docs = (
        await db.app_registry.find(
            {"status": "active", "deleted_at": None},
            {
                "_id": 0,
                "app_id": 1,
                "name": 1,
                "slug": 1,
                "app_slug": 1,
                "description": 1,
                "status": 1,
                "ingestion_mode": 1,
            },
        )
        .sort("name", 1)
        .to_list(500)
    )
    return docs


# ===========================================================================
# GRANTS — per-tenant app entitlements
# ===========================================================================

@router.get("/tenants/{tenant_id}/app-grants")
async def list_app_grants(
        tenant_id: str,
        status: Optional[str] = None,
        current_user=Depends(get_current_user),
):
    """List all app grants on a tenant.

    Root-only in 1E.1. Optional ``status`` filter (active / suspended
    / revoked). Sorts by most recently granted first to match the
    Assignment list UX.
    """
    _require_root(current_user)
    if not has_perm(current_user, "tenant_app_grants.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    query: dict = {"tenant_id": tenant_id}
    if status:
        query["status"] = status

    docs = (
        await db.tenant_app_grants.find(query, {"_id": 0})
        .sort("granted_at", -1)
        .to_list(500)
    )
    return docs


@router.post("/tenants/{tenant_id}/app-grants")
async def create_app_grant(
        tenant_id: str,
        data: TenantAppGrantCreate,
        current_user=Depends(get_current_user),
):
    """Grant an ingested app to a tenant.

    1E.1 is root-only: a SimpliMeta admin selects an app from the
    catalog and grants it to a partner / account tenant. The compound
    unique index on (tenant_id, app_id) enforces idempotent grant
    semantics — a second insert for the same pair fails at the DB
    layer with DuplicateKey. We translate that to 409 with a pointer
    to the existing grant so the caller can PATCH it (typically the
    Reactivate flow) instead of POSTing again.

    Root tenants themselves cannot be granted apps — they own the
    catalog and don't sit inside the tenant-scoped surface. We block
    this at 400 so the UI gets a clear signal rather than silently
    creating a grant that wouldn't do anything useful.
    """
    _require_root(current_user)
    if not has_perm(current_user, "tenant_app_grants.grant"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # URL tenant_id wins over body tenant_id (confused-deputy guard).
    if data.tenant_id and data.tenant_id != tenant_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Body tenant_id ({data.tenant_id}) does not match "
                f"URL tenant_id ({tenant_id})."
            ),
        )

    db = get_db()
    target_tenant = await _load_tenant_or_404(db, tenant_id)
    if target_tenant.get("tenant_type") == "root":
        raise HTTPException(
            status_code=400,
            detail="The root tenant cannot be granted apps.",
        )

    # Verify the app exists + is active. Granting a non-active app is
    # a footgun — pending/failed/archived apps should never get into
    # the grant graph.
    app = await _load_app_or_404(db, data.app_id)
    if app.get("status") != "active":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot grant an app in status '{app.get('status')}'. "
                "Only active apps can be granted to tenants."
            ),
        )

    now = datetime.now(timezone.utc)
    doc = TenantAppGrant(
        grant_id=gen_id("apg_"),
        app_id=data.app_id,
        tenant_id=tenant_id,
        granted_by_user_id=current_user.user_id,
        granted_by_tenant_id=getattr(
            current_user, "tenant_id", SIMPLIMETA_ROOT_TENANT_ID
        ),
        status="active",
        granted_at=now,
        expires_at=data.expires_at,
        notes=data.notes,
        updated_at=now,
    ).model_dump()

    try:
        await db.tenant_app_grants.insert_one(doc)
    except Exception as e:
        if "duplicate key" in str(e).lower():
            existing = await db.tenant_app_grants.find_one(
                {"tenant_id": tenant_id, "app_id": data.app_id},
                {"_id": 0, "grant_id": 1, "status": 1},
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "This tenant already has a grant for that app. "
                        "PATCH the existing grant instead "
                        "(reactivate it if it's currently revoked)."
                    ),
                    "existing_grant_id": existing.get("grant_id") if existing else None,
                    "existing_status": existing.get("status") if existing else None,
                },
            )
        raise

    logger.info(
        "App grant created: tenant=%s, app=%s → grant_id=%s (by=%s)",
        tenant_id, data.app_id, doc["grant_id"], current_user.user_id,
    )

    # Phase 1E.3d — auto-seed the app's permission keys onto the
    # global `role_tenant_admin` role so the target tenant's
    # tenant_admin user can actually exercise the granted app. Without
    # this, the 1E.3b middleware lets the request through (grant
    # exists) but the route 403s inside its handler (`has_perm` fails
    # because the role's permissions list doesn't carry the key).
    # Idempotent — re-runs are no-ops if the keys are already present.
    seed_summary = await seed_app_permissions(
        db,
        data.app_id,
        triggered_by_grant_id=doc["grant_id"],
        actor_user_id=current_user.user_id,
        actor_email=current_user.email,
    )
    logger.info(
        "App grant %s — perm-seed summary: added=%d, already_present=%d",
        doc["grant_id"],
        len(seed_summary.get("keys_added") or []),
        len(seed_summary.get("keys_already_present") or []),
    )

    # Strip the ObjectId motor adds post-insert so FastAPI can serialise.
    doc.pop("_id", None)
    return doc


@router.patch("/tenants/{tenant_id}/app-grants/{grant_id}")
async def update_app_grant(
        tenant_id: str,
        grant_id: str,
        data: TenantAppGrantUpdate,
        current_user=Depends(get_current_user),
):
    """Modify an existing app grant.

    Most common use: revoke a grant via ``{"status": "revoked"}``, or
    reactivate a revoked grant via ``{"status": "active"}``. Revocation
    is a status transition (per the AI Employee assignment convention)
    rather than a DELETE so audit chains stay intact and any
    downstream UI references survive.

    1E.1 is root-only. When tenant-side delegation lands in a later
    phase, this endpoint will gain an ownership check analogous to
    the one on ``update_assignment``. For now: root admins can modify
    any field on any grant.

    Revoke-side chain check: not applicable in 1E.1 (no delegation
    chains yet). When tenant-side sub-grants are introduced, this
    endpoint will need the same 409 + blocking_sub_grants response
    shape that update_assignment uses.

    Reactivate behaviour (1E.1): on ``active`` after a ``revoked``
    status, ``revoked_at`` and ``revoked_by_*`` are left in place as
    historical record. The grant is "currently active" iff
    ``status == 'active'``; the revoked_* fields document the most
    recent revoke event regardless. Mirrors 1D.9's reactivate
    semantics for assignments.
    """
    _require_root(current_user)
    if not has_perm(current_user, "tenant_app_grants.grant"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    existing = await _load_grant_or_404(db, tenant_id, grant_id)

    patch = data.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update.")

    # Stamp the revoke audit trail on the transition active→revoked.
    # Idempotent re-revoke is a no-op on the audit fields (preserve
    # the original revoker / timestamp). Other updates pass through.
    is_revoking = (
            patch.get("status") == "revoked"
            and existing.get("status") != "revoked"
    )
    if is_revoking:
        patch["revoked_at"] = datetime.now(timezone.utc)
        patch["revoked_by_user_id"] = current_user.user_id
        patch["revoked_by_tenant_id"] = getattr(
            current_user, "tenant_id", SIMPLIMETA_ROOT_TENANT_ID
        )

    patch["updated_at"] = datetime.now(timezone.utc)

    await db.tenant_app_grants.update_one(
        {"grant_id": grant_id, "tenant_id": tenant_id},
        {"$set": patch},
    )
    doc = await db.tenant_app_grants.find_one(
        {"grant_id": grant_id}, {"_id": 0}
    )

    # Phase 1E.3d — keep `role_tenant_admin` perms in lockstep with
    # status transitions. Reference-counting in revoke_app_permissions
    # ensures we don't strip keys still claimed by another active grant
    # (apps that share keys, like the projects-dashboard.view-style
    # cross-cutting ones, survive each other's revokes).
    #
    # Transitions handled:
    #   * active → revoked       : revoke perms (this branch)
    #   * active → suspended     : revoke perms (same — handler is gone)
    #   * revoked   → active     : re-seed perms (reactivate)
    #   * suspended → active     : re-seed perms (reactivate)
    #
    # Any other transition (e.g. just changing notes, expires_at, or
    # status flips that don't cross the active boundary) leaves perms
    # alone.
    old_status = existing.get("status")
    new_status = patch.get("status", old_status)
    was_active = old_status == "active"
    now_active = new_status == "active"

    if was_active and not now_active:
        seed_summary = await revoke_app_permissions(
            db,
            existing["app_id"],
            excluded_grant_id=grant_id,
            actor_user_id=current_user.user_id,
            actor_email=current_user.email,
        )
        logger.info(
            "App grant %s deactivated — perm-revoke summary: removed=%d, preserved=%d",
            grant_id,
            len(seed_summary.get("keys_removed") or []),
            len(seed_summary.get("keys_preserved") or []),
        )
    elif not was_active and now_active:
        seed_summary = await seed_app_permissions(
            db,
            existing["app_id"],
            triggered_by_grant_id=grant_id,
            actor_user_id=current_user.user_id,
            actor_email=current_user.email,
        )
        logger.info(
            "App grant %s reactivated — perm-seed summary: added=%d, already_present=%d",
            grant_id,
            len(seed_summary.get("keys_added") or []),
            len(seed_summary.get("keys_already_present") or []),
        )

    logger.info(
        "App grant patched: grant_id=%s, fields=%s "
        "(by=%s, action=%s)",
        grant_id, sorted(patch.keys()),
        current_user.user_id,
        "revoke" if is_revoking else "update",
    )
    return doc
