"""Phase 1E.3d — Permission auto-seeder.

When a SimpliMeta admin grants an ingested app to a tenant, that
tenant's tenant_admin needs the app's permission keys on their role
so the in-handler `has_perm()` checks pass. Without this, the
1E.3b middleware would let the request through (grant exists) but
the route would still 403 inside the handler (perm absent on role).

This module is the missing link.

## Architectural shape (read carefully — this is not obvious)

The `Role` model is GLOBAL — there's no `tenant_id` field. The
`tenant_admin` role is one canonical row (`role_id: "role_tenant_admin"`)
shared by every tenant's tenant_admin user. The per-tenant gating
happens at the MIDDLEWARE layer (1E.3b), not on the role itself:

  * Middleware: "does this tenant have an active grant for the app
                whose route prefix matches this request?"
  * Handler:    "does the caller's role have the per-action perm?"

So the seeder's job is to ensure the global `tenant_admin` role
carries every permission key declared by every app that ANY tenant
has been granted. Granting Projects to Dexter adds the projects.*
keys to `role_tenant_admin`. Tenants without a grant cannot use
those perms because the middleware blocks the request first.

## Why we don't keep per-tenant roles

The `tenant_admin` role is `is_system: True`. Forking it per tenant
would mean every tenant gets a copy that diverges from the canonical
definition over time — exactly the maintenance nightmare the
single-system-role model exists to prevent. Tenant_admin's intent is
"a SimpliMeta-delegated administrator for one tenant" — the
"delegated administrator" part doesn't change between tenants.

## Idempotency

  * `seed_app_permissions`: uses $addToSet — re-applying a grant
    that's already seeded is a no-op.
  * `revoke_app_permissions`: reference-counts perm keys across all
    OTHER currently-active grants. A key is only removed from the
    role when the LAST active grant claiming that key is revoked.
    Apps that share keys (Projects and Finance both have
    `dashboard.view`-style keys today) survive each other's revokes.

## What this module does NOT do

  * Sync perm keys on app_registry PATCH. If you add a new perm key
    to an already-granted app via the app_ingestion PATCH endpoint,
    the seeder doesn't auto-propagate. Call `reseed_all_grants` from
    a one-off script or scheduled job to reconcile. Auto-on-app-patch
    is in scope for 1F.
  * Touch roles other than `tenant_admin`. The `admin` role
    (SimpliMeta staff) is unaffected — it's wildcard-perm anyway.
    Custom roles a tenant_admin creates are also untouched —
    tenants own those.
  * Add `projects.access` / `finance.access` portal gates. Those
    keys ARE in app_registry.permission_keys (per 1E.3a backfill),
    so they're seeded naturally with the rest of the app's keys.

Refs: 1E.3d plan locked 19 May 2026; depends on 1E.3a
(permission_keys populated on app_registry), 1E.3b (middleware
enforcement of tenant_app_grants), 1E.3c (route-layer tenant
scoping so seeded perms don't leak data).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from services.audit_service import log_action

logger = logging.getLogger(__name__)


# Hardcoded role id — set in seeds/seed_default_roles.py. Importing
# from there would create a circular dependency at boot (the seed
# imports from services already). The id is stable across all envs.
TENANT_ADMIN_ROLE_ID = "role_tenant_admin"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _load_app_permission_keys(db, app_id: str) -> list[str]:
    """Return the permission_keys declared by an app. Empty list if the
    app is missing OR has no permission_keys (apps registered before
    1E.3a are in this state until the backfill stamps them)."""
    app = await db.app_registry.find_one(
        {"app_id": app_id}, {"_id": 0, "permission_keys": 1}
    )
    if not app:
        return []
    return list(app.get("permission_keys") or [])


async def _claimed_by_other_active_grants(
    db,
    excluded_grant_id: str,
) -> set[str]:
    """Union of permission_keys claimed by every active grant EXCEPT
    the one being revoked. The reference set we diff against when
    deciding which keys are safe to strip from `role_tenant_admin`.

    Walks active grants → resolves to their apps → unions
    permission_keys. Scans the (status, granted_at) index naturally.
    """
    claimed: set[str] = set()
    cursor = db.tenant_app_grants.find(
        {"status": "active", "grant_id": {"$ne": excluded_grant_id}},
        {"_id": 0, "app_id": 1},
    )
    seen_apps: set[str] = set()
    async for grant in cursor:
        app_id = grant.get("app_id")
        if not app_id or app_id in seen_apps:
            continue
        seen_apps.add(app_id)
        for key in await _load_app_permission_keys(db, app_id):
            claimed.add(key)
    return claimed


# ---------------------------------------------------------------------------
# Public API — called from tenant_app_grants.create_app_grant / update
# ---------------------------------------------------------------------------

async def seed_app_permissions(
    db,
    app_id: str,
    *,
    triggered_by_grant_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
) -> dict:
    """Ensure `role_tenant_admin` carries every permission key declared
    by `app_id`. Idempotent: re-running for the same app is a no-op.

    Returns a summary describing what changed for the caller / audit
    log:
        {
          "app_id": "...",
          "keys_present_before": N,
          "keys_added": [...],   # only the newly-added perms
          "keys_already_present": [...],
        }

    Safe to call without an existing grant context — useful from
    a reseed script or admin debugger. When `triggered_by_grant_id`
    is provided we include it in the audit trail.
    """
    keys = await _load_app_permission_keys(db, app_id)
    if not keys:
        logger.info(
            "seed_app_permissions(app_id=%s) — no permission_keys on app_registry; skipping",
            app_id,
        )
        return {
            "app_id": app_id,
            "keys_present_before": 0,
            "keys_added": [],
            "keys_already_present": [],
        }

    role = await db.roles.find_one(
        {"role_id": TENANT_ADMIN_ROLE_ID},
        {"_id": 0, "permissions": 1},
    )
    if not role:
        # If the canonical tenant_admin role doesn't exist, the
        # default-role seeder hasn't run yet — propagating an error
        # here masks the real problem. Log loudly and bail.
        logger.error(
            "seed_app_permissions: %s role not found — "
            "did seed_default_roles run?",
            TENANT_ADMIN_ROLE_ID,
        )
        return {
            "app_id": app_id,
            "keys_present_before": 0,
            "keys_added": [],
            "keys_already_present": [],
            "error": "tenant_admin role missing",
        }

    existing = set(role.get("permissions") or [])
    to_add = [k for k in keys if k not in existing]
    already = [k for k in keys if k in existing]

    if not to_add:
        logger.info(
            "seed_app_permissions(app_id=%s) — all %d keys already on tenant_admin; no-op",
            app_id, len(keys),
        )
        return {
            "app_id": app_id,
            "keys_present_before": len(existing),
            "keys_added": [],
            "keys_already_present": already,
        }

    # $addToSet with $each gives the desired set-union semantics in
    # one round-trip. updated_at bumps so any cache invalidation
    # tracking timestamps sees the change.
    await db.roles.update_one(
        {"role_id": TENANT_ADMIN_ROLE_ID},
        {
            "$addToSet": {"permissions": {"$each": to_add}},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )

    logger.info(
        "seed_app_permissions(app_id=%s) — added %d keys to tenant_admin: %s",
        app_id, len(to_add), to_add,
    )

    # Audit. resource_type=role, resource_id=role_tenant_admin so the
    # change shows up under the role's history. details carries the
    # app + the actual key diff for forensic reconstruction.
    if actor_user_id:
        try:
            await log_action(
                action="permissions_seeded",
                actor_id=actor_user_id,
                actor_email=actor_email or "system",
                resource_type="role",
                resource_id=TENANT_ADMIN_ROLE_ID,
                details={
                    "app_id": app_id,
                    "grant_id": triggered_by_grant_id,
                    "keys_added": to_add,
                    "keys_already_present_count": len(already),
                    "reason": "app_grant_created_or_reactivated",
                },
            )
        except Exception:  # noqa: BLE001
            # Audit failure must not block the seed — the seed already
            # happened, the audit row is a postscript. Log and move on.
            logger.exception("Audit log failed for permissions_seeded (non-fatal)")

    return {
        "app_id": app_id,
        "keys_present_before": len(existing),
        "keys_added": to_add,
        "keys_already_present": already,
    }


async def revoke_app_permissions(
    db,
    app_id: str,
    *,
    excluded_grant_id: str,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
) -> dict:
    """Remove perm keys for `app_id` from `role_tenant_admin`, but only
    those NOT claimed by any OTHER currently-active grant.

    `excluded_grant_id` is the grant being revoked — we exclude it
    from the reference-count calculation because the caller has
    already flipped its status (or is about to). Without that
    exclusion we'd count the grant we're tearing down as still-active
    and never remove anything.

    Returns:
        {
          "app_id": "...",
          "keys_removed": [...],
          "keys_preserved": [...],   # held by another active grant
        }
    """
    keys = await _load_app_permission_keys(db, app_id)
    if not keys:
        return {"app_id": app_id, "keys_removed": [], "keys_preserved": []}

    role = await db.roles.find_one(
        {"role_id": TENANT_ADMIN_ROLE_ID}, {"_id": 0, "permissions": 1}
    )
    if not role:
        return {
            "app_id": app_id,
            "keys_removed": [],
            "keys_preserved": [],
            "error": "tenant_admin role missing",
        }

    existing = set(role.get("permissions") or [])

    # Anything claimed by any other active grant is preserved.
    claimed = await _claimed_by_other_active_grants(db, excluded_grant_id)
    to_remove = [k for k in keys if k in existing and k not in claimed]
    preserved = [k for k in keys if k in claimed]

    if not to_remove:
        logger.info(
            "revoke_app_permissions(app_id=%s) — all keys still claimed by other "
            "active grants; no removal",
            app_id,
        )
        return {
            "app_id": app_id,
            "keys_removed": [],
            "keys_preserved": preserved,
        }

    # $pullAll is the inverse of $addToSet with $each — strips each
    # listed key from the array.
    await db.roles.update_one(
        {"role_id": TENANT_ADMIN_ROLE_ID},
        {
            "$pullAll": {"permissions": to_remove},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )

    logger.info(
        "revoke_app_permissions(app_id=%s) — removed %d keys from tenant_admin: %s "
        "(preserved %d still claimed elsewhere)",
        app_id, len(to_remove), to_remove, len(preserved),
    )

    if actor_user_id:
        try:
            await log_action(
                action="permissions_revoked",
                actor_id=actor_user_id,
                actor_email=actor_email or "system",
                resource_type="role",
                resource_id=TENANT_ADMIN_ROLE_ID,
                details={
                    "app_id": app_id,
                    "grant_id": excluded_grant_id,
                    "keys_removed": to_remove,
                    "keys_preserved_count": len(preserved),
                    "reason": "app_grant_revoked_or_suspended",
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Audit log failed for permissions_revoked (non-fatal)")

    return {
        "app_id": app_id,
        "keys_removed": to_remove,
        "keys_preserved": preserved,
    }


# ---------------------------------------------------------------------------
# Reconciliation — used by backfill script + admin debugger endpoint
# ---------------------------------------------------------------------------

async def reseed_all_grants(db) -> dict:
    """Walk every currently-active grant and run `seed_app_permissions`
    once per distinct app_id. Idempotent. Used by:

      * The 1E.3d backfill script — first-time seed for existing
        grants (Dexter's pre-1E.3d Projects grant lands here).
      * An ops escape hatch if `role_tenant_admin` somehow drifts
        from the expected union.

    Does NOT delete perms — that would require knowing which keys
    were ORIGINALLY app-seeded vs admin-added vs base-seeded. Use
    `audit_role_permissions` (admin endpoint, separate ticket) for
    visibility into where each perm came from.
    """
    seen_apps: set[str] = set()
    summaries: list[dict] = []

    cursor = db.tenant_app_grants.find(
        {"status": "active"}, {"_id": 0, "app_id": 1, "grant_id": 1}
    )
    async for grant in cursor:
        app_id = grant.get("app_id")
        if not app_id or app_id in seen_apps:
            continue
        seen_apps.add(app_id)
        summary = await seed_app_permissions(
            db,
            app_id,
            triggered_by_grant_id=grant.get("grant_id"),
            # Backfill is system-initiated — no human actor.
            actor_user_id="system",
            actor_email="system@aegisaxiom.internal",
        )
        summaries.append(summary)

    total_added = sum(len(s.get("keys_added") or []) for s in summaries)
    logger.info(
        "reseed_all_grants — processed %d distinct apps, added %d permissions total",
        len(summaries), total_added,
    )
    return {
        "apps_processed": len(summaries),
        "total_keys_added": total_added,
        "per_app": summaries,
    }
