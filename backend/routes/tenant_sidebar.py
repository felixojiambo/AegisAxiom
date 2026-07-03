"""Phase 1E.2 — Tenant sidebar configuration API.

Single endpoint that the frontend calls on login to learn which apps
have been granted to the caller's tenant. The frontend renders a
minimal spine (AI Employees + AI Chat) for every non-root tenant
user, plus an "Apps" group that lists whatever this endpoint returns.

## Design notes

**Why this endpoint exists separately from /tenants/{tid}/app-grants:**
The admin-side endpoint (1E.1) is for SimpliMeta admins inspecting
*another* tenant's grants and is gated by `tenant_app_grants.view`.
This endpoint is for the tenant user themselves, learning what
*they* can see — different consumer, different gate (just
authentication, no permission check), and the response is shaped
for sidebar rendering rather than admin management (no granted_by,
no revoke_at, no notes).

**Active filter applied twice — by design.** A grant is shown only
if BOTH the grant row is `status='active'` AND the underlying app
in `app_registry` is `status='active'`. This means:
  * Revoked grants disappear from the sidebar immediately on the
    next page load (no admin action needed beyond the revoke itself).
  * If an app is archived from the catalog (`status` flipped from
    `active` to `archived`), it disappears from every tenant's
    sidebar that had it granted, without requiring any per-tenant
    cleanup. The grant rows survive in the DB so an un-archive
    operation restores everyone's access automatically.

**Why we don't return the route or icon:** Lucide icons are React
components and don't serialise. Routes for granted apps are a
frontend concern (the SPA owns its own routing table). Backend
returns slug + name + description; frontend has a slug → {route,
icon} map in `lib/tenantAppNav.js`. An unknown slug from the backend
just doesn't render — see the warning behaviour there.

**Spine items not returned by this endpoint.** AI Employees and AI
Chat are always present for non-root tenants. They're hardcoded on
the frontend rather than served from here so we don't pay a network
round-trip for static content. The endpoint is purely for the
dynamic portion (granted apps).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from auth_helper import get_current_user
from database import get_db
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/tenant/sidebar-config")
async def get_tenant_sidebar_config(current_user=Depends(get_current_user)) -> dict[str, Any]:
    """Return the dynamic sidebar payload for the caller's tenant.

    Response shape:

        {
          "tenant_id": "ten_xxx",
          "tenant_name": "Dexter Laboratories",
          "is_root": false,
          "apps": [
            {
              "app_id": "app_xxx",
              "name": "Projects PMO",
              "slug": "projects",
              "description": "Project management module ..."
            },
            ...
          ]
        }

    For root-tenant users (SimpliMeta admins) the endpoint short-
    circuits with `is_root: true` and `apps: []`. The frontend
    shouldn't call this for root users anyway — it branches on
    `useIsRootTenant()` at the Layout level — but the defensive
    early-return is cheap insurance against a refactor that
    forgets the branch.
    """
    db = get_db()
    tenant_id = getattr(current_user, "tenant_id", None)
    is_root = tenant_id == SIMPLIMETA_ROOT_TENANT_ID

    if not tenant_id or is_root:
        return {
            "tenant_id": tenant_id,
            "tenant_name": None,
            "is_root": True,
            "apps": [],
        }

    # Tenant name — small lookup, fine to do unconditionally.
    tenant_doc = await db.tenants.find_one(
        {"tenant_id": tenant_id},
        {"_id": 0, "name": 1},
    )
    tenant_name = (tenant_doc or {}).get("name")

    # Active grants for this tenant. We don't need a fancy aggregation;
    # the grant rows are small (<100 per tenant in realistic scenarios),
    # and the join is by app_id with a separate find on app_registry.
    grant_rows = await db.tenant_app_grants.find(
        {"tenant_id": tenant_id, "status": "active"},
        {"_id": 0, "app_id": 1},
    ).to_list(500)

    granted_app_ids = [g["app_id"] for g in grant_rows]
    if not granted_app_ids:
        return {
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "is_root": False,
            "apps": [],
        }

    # Pull only the metadata the sidebar actually needs. The double
    # status='active' filter (here on app_registry) is the second
    # half of what makes archived apps disappear from sidebars
    # automatically — see module docstring.
    app_docs = await db.app_registry.find(
        {
            "app_id": {"$in": granted_app_ids},
            "status": "active",
            "deleted_at": None,
        },
        {
            "_id": 0,
            "app_id": 1,
            "name": 1,
            "slug": 1,
            "description": 1,
        },
    ).sort("name", 1).to_list(500)

    return {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "is_root": False,
        "apps": app_docs,
    }
