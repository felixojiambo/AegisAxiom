"""Projects module — assignable-users picker endpoint.

Wave 4 / Thread A1 introduced this endpoint so a non-admin PM could
populate the wizard, assignee pickers, and ownership selectors without
holding the broader `users.view` permission.

Wave 5 / Thread B (Contract 3) extends it with the new `tier` query param
backed by `pm_capacity_tier` on the user document (see `backend/models.py`
+ `backend/seeds/seed_assignable_flags.py`).

Permission gate: `projects.project.view` (any project role). Returns a
narrow projection of user fields:

    [{user_id, name, role, pm_capacity_tier,
      is_assignable_as_pm, is_assignable_as_member, is_active, picture}]

Filtering rules (preferred — `tier` query param):
    tier=main     -> pm_capacity_tier == main_pm AND is_active
    tier=overflow -> pm_capacity_tier IN {main_pm, overflow_pm} AND is_active
    tier=all_pms  -> alias for `overflow`
    tier=members  -> pm_capacity_tier != none AND is_active
    tier=all      -> all active users (admin path)

Legacy rules (`role` query param, deprecated — preserved for backwards-compat):
    role=pm     -> mapped to tier=overflow (any user with PM eligibility)
    role=member -> mapped to tier=members
    role=all    -> mapped to tier=all

`is_active` is enforced server-side regardless of the param. The flags
themselves are populated by `backend/seeds/seed_assignable_flags.py`
(derived from `pm_capacity_tier`); the Capacity admin tab flips tiers
via `PATCH /api/projects/users/{user_id}/capacity-tier`.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


_VALID_ROLES = {"pm", "member", "all"}
_VALID_TIERS = {"main", "overflow", "all_pms", "members", "all"}

# Map legacy `role` param → new `tier` semantics.
_LEGACY_ROLE_TO_TIER = {
    "pm": "overflow",   # any user with PM eligibility (main OR overflow)
    "member": "members",
    "all": "all",
}

_PROJECTION = {
    "_id": 0,
    "user_id": 1,
    "name": 1,
    "role": 1,
    "pm_capacity_tier": 1,
    "is_assignable_as_pm": 1,
    "is_assignable_as_member": 1,
    "is_active": 1,
    "picture": 1,
}


def _query_for_tier(tier: str) -> Dict[str, Any]:
    """Build the Mongo filter for a given tier value."""
    base: Dict[str, Any] = {"is_active": True}
    if tier == "main":
        base["pm_capacity_tier"] = "main_pm"
    elif tier in {"overflow", "all_pms"}:
        base["pm_capacity_tier"] = {"$in": ["main_pm", "overflow_pm"]}
    elif tier == "members":
        base["pm_capacity_tier"] = {"$ne": "none"}
    # tier == "all" — no extra filter beyond is_active.
    return base


@router.get(
    "/assignable-users",
    dependencies=[Depends(require_ai_permission("projects.project.view"))],
)
async def list_assignable_users(
    role: Optional[str] = Query(
        None,
        description="DEPRECATED — use `tier`. pm | member | all",
    ),
    tier: Optional[str] = Query(
        None,
        description="main | overflow | all_pms | members | all",
    ),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
) -> List[Dict[str, Any]]:
    if not has_perm(current_user, "projects.project.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Resolve param: tier wins; fall back to legacy role mapping; default `all`.
    if tier is not None:
        if tier not in _VALID_TIERS:
            raise HTTPException(
                status_code=422,
                detail=f"tier must be one of {sorted(_VALID_TIERS)} (got {tier!r})",
            )
        resolved_tier = tier
    elif role is not None:
        if role not in _VALID_ROLES:
            raise HTTPException(
                status_code=422,
                detail=f"role must be one of {sorted(_VALID_ROLES)} (got {role!r})",
            )
        resolved_tier = _LEGACY_ROLE_TO_TIER[role]
    else:
        resolved_tier = "all"

    db = get_db()
    query = _query_for_tier(resolved_tier)
    # Phase 1E.3c — tenant-scope the user picker so a partner tenant's PM
    # picker can't enumerate the root tenant's users (information disclosure
    # + would allow assigning root-tenant users to partner-tenant work which
    # then fails validation downstream anyway).
    query["tenant_id"] = tenant_id

    cursor = db["users"].find(query, _PROJECTION).sort("name", 1)
    users: List[Dict[str, Any]] = []
    async for u in cursor:
        # Defensive defaults — older docs predate the Wave 4 / Wave 5 backfills.
        u.setdefault("pm_capacity_tier", None)
        u.setdefault("is_assignable_as_pm", False)
        u.setdefault("is_assignable_as_member", True)
        u.setdefault("is_active", True)
        users.append(u)
    return users
