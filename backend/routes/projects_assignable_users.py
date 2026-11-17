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

from sqlalchemy.ext.asyncio import AsyncSession

from auth_helper import get_current_user, has_perm
from db.session import get_session
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from repositories.users import UserRepository

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

# The projection and the tier→filter mapping now live in
# `UserRepository.list_assignable_users` (Phase 2a completion): the picker reads
# Postgres, and the tier semantics — including `members` keeping rows whose tier
# is NULL, which Mongo's `$ne: "none"` did implicitly — are asserted there.


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
    pg: AsyncSession = Depends(get_session),
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

    # Phase 1E.3c — tenant-scope the user picker so a partner tenant's PM
    # picker can't enumerate the root tenant's users (information disclosure
    # + would allow assigning root-tenant users to partner-tenant work which
    # then fails validation downstream anyway).
    #
    # The per-row `setdefault` defence the Mongo path needed is gone: these are
    # NOT NULL columns with server defaults, so every key is always present.
    return await UserRepository(pg).list_assignable_users(
        tenant_id=tenant_id, tier=resolved_tier
    )
