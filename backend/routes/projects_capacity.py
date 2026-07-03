"""Projects module — PM capacity admin routes.

Wave 5 / Thread B 5B3 (Contract 3). Admin-only surface for managing the
new `pm_capacity_tier` field on user documents.

Endpoints:
    GET   /api/projects/users/capacity
        Returns every user's current tier + role + active state. Gated on
        `projects.capacity.manage` (admin tier-control surface).

    PATCH /api/projects/users/{user_id}/capacity-tier
        Body: {"tier": "main_pm | overflow_pm | member_only | none"}
        Re-derives `is_assignable_as_pm` / `is_assignable_as_member` from the
        new tier so the picker endpoint stays consistent. Wraps in
        `crud_envelope` so policy + audit + event emission flow through the
        same pipeline as other ingested CRUD ops. Emits
        `projects.capacity.tier_changed` with before/after.

Permissions:
    projects.capacity.view   — read-only access to the capacity matrix
                               (project_manager + admin)
    projects.capacity.manage — flip tiers (admin only)

Both keys are registered in `seed_projects_rbac.py` and live alongside the
other `projects.*` permissions in the dynamic permission registry.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from services.field_registry import register_module_events
from services.ingestion.route_helpers import crud_envelope

logger = logging.getLogger(__name__)
router = APIRouter()


# Capacity is a virtual entity (no collection of its own — it's a field on
# users), but we register the event surface for discoverability so the
# audit/event pipeline knows about `tier_changed`.
register_module_events("projects", "capacity", ["tier_changed"])


_VALID_TIERS = {"main_pm", "overflow_pm", "member_only", "none"}

_PROJECTION = {
    "_id": 0,
    "user_id": 1,
    "name": 1,
    "email": 1,
    "role": 1,
    "pm_capacity_tier": 1,
    "is_assignable_as_pm": 1,
    "is_assignable_as_member": 1,
    "is_active": 1,
    "picture": 1,
}


def _flags_for_tier(tier: str, is_active: bool) -> Dict[str, bool]:
    """Derive the assignability flags from a tier + active state.

    Mirrors `seeds.seed_assignable_flags._flags_for_tier`. Duplicated here
    so the route file has no runtime dep on the seed module — they evolve
    together; keep in sync if rules change.
    """
    pm_eligible = tier in {"main_pm", "overflow_pm"}
    member_eligible = (tier != "none") and bool(is_active)
    return {
        "is_assignable_as_pm": bool(pm_eligible),
        "is_assignable_as_member": bool(member_eligible),
    }


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
class CapacityTierUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Literal["main_pm", "overflow_pm", "member_only", "none"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/users/capacity",
    dependencies=[Depends(require_ai_permission("projects.capacity.manage"))],
)
async def list_capacity(
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
) -> List[Dict[str, Any]]:
    """Return every user's current capacity tier (admin tier-control surface)."""
    if not has_perm(current_user, "projects.capacity.manage"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    # Phase 1E.3c — admin sees only users in their own tenant's capacity
    # matrix. Root admin operating under override sees the target tenant's
    # users, not their own.
    cursor = db["users"].find({"tenant_id": tenant_id}, _PROJECTION).sort("name", 1)
    users: List[Dict[str, Any]] = []
    async for u in cursor:
        u.setdefault("pm_capacity_tier", None)
        u.setdefault("is_assignable_as_pm", False)
        u.setdefault("is_assignable_as_member", True)
        u.setdefault("is_active", True)
        users.append(u)
    return users


@router.patch(
    "/users/{user_id}/capacity-tier",
    dependencies=[Depends(require_ai_permission("projects.capacity.manage"))],
)
async def update_capacity_tier(
    user_id: str,
    payload: CapacityTierUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
) -> Dict[str, Any]:
    """Flip a user's `pm_capacity_tier`. Admin-only. Audited via crud_envelope."""
    if not has_perm(current_user, "projects.capacity.manage"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()

    # Phase 1E.3c — refuse to flip the tier of a user belonging to a
    # different tenant. A tenant-admin cannot reach across tenants to
    # mutate another tenant's user.
    before = await db["users"].find_one(
        {"user_id": user_id, "tenant_id": tenant_id}, _PROJECTION
    )
    if before is None:
        raise HTTPException(status_code=404, detail="User not found")

    new_tier = payload.tier
    if new_tier not in _VALID_TIERS:
        # Belt-and-suspenders — Pydantic Literal already enforces this.
        raise HTTPException(
            status_code=422,
            detail=f"tier must be one of {sorted(_VALID_TIERS)} (got {new_tier!r})",
        )

    is_active = bool(before.get("is_active", True))
    flags = _flags_for_tier(new_tier, is_active)

    updates = {
        "pm_capacity_tier": new_tier,
        "is_assignable_as_pm": flags["is_assignable_as_pm"],
        "is_assignable_as_member": flags["is_assignable_as_member"],
    }

    async with crud_envelope(
        module="projects",
        entity="capacity",
        action="tier_changed",
        resource_id=user_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["users"].update_one(
            {"user_id": user_id, "tenant_id": tenant_id},
            {"$set": updates},
        )
        after = await db["users"].find_one(
            {"user_id": user_id, "tenant_id": tenant_id}, _PROJECTION
        )
        ctx.set_after(after)

    if after is None:
        # Race / hard-delete in flight — surface as 404.
        raise HTTPException(status_code=404, detail="User not found after update")
    return after
