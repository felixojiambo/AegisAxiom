"""Tenants router — Phase 1A multi-tenancy foundation.

Manages the tenant tree (root → partner → account) per the multi-tenancy
design doc. Root tenant is SimpliMeta; partners are organisations that resell
AEGISAXIOM to their own accounts; accounts are leaf customer tenants.

Phase 2a: all tenant reads/writes go through the Postgres tenants repository.

Hierarchy rules enforced here:
  * `root` tenants have `parent_tenant_id = None`. Exactly one root exists
    (SimpliMeta) and it is seeded at startup, not created via this API.
  * `partner` tenants must have `parent_tenant_id` pointing to a `root`.
  * `account` tenants may have `parent_tenant_id = None` (a direct customer
    of SimpliMeta) OR pointing to a `partner` (a customer of a reseller).

Permissions:
  * `tenants.view`  — list/read tenants
  * `tenants.manage` — create/update/archive tenants
Both granted to admins on the SimpliMeta root tenant only.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session
from models import Tenant, TenantCreate, TenantUpdate, gen_id
from auth_helper import get_current_user, has_perm
from repositories.tenants import TenantRepository
from services.audit_service import log_action
from services.field_registry import register_fields, register_module_events, register_module_config
from services.permission_registry import register_module_permissions

logger = logging.getLogger(__name__)
router = APIRouter()


# ---- Module registration ----

@register_module_permissions("tenants")
def _tenants_permissions():
    return [
        {
            "key": "tenants.view",
            "group": "Tenants",
            "label": "View tenants",
            "implies": [],
        },
        {
            "key": "tenants.manage",
            "group": "Tenants",
            "label": "Create, update, and archive tenants",
            "implies": ["tenants.view"],
        },
    ]


register_module_config("tenants", "tenants", "tenant_id", "Tenants")
register_module_events("tenants", "tenant", ["created", "updated", "archived"])
register_fields("tenants", "tenant", [
    {"name": "name", "type": "string", "label": "Name",
     "description": "Display name of the tenant organisation"},
    {"name": "slug", "type": "string", "label": "Slug",
     "description": "URL-safe identifier; must be unique"},
    {"name": "tenant_type", "type": "string", "label": "Type",
     "description": "root | partner | account"},
    {"name": "parent_tenant_id", "type": "string", "label": "Parent tenant",
     "description": "ID of the parent tenant (None for root and direct accounts)"},
    {"name": "status", "type": "string", "label": "Status",
     "description": "active | suspended | archived"},
])


# ---- Helpers ----

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def _gen_tenant_id() -> str:
    return gen_id("ten_")


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail=(
                "slug must be 1-64 chars, lowercase alphanumeric or hyphens, "
                "and may not start or end with a hyphen"
            ),
        )


async def _validate_hierarchy(
        repo: TenantRepository,
        tenant_type: str,
        parent_tenant_id: Optional[str],
        current_tenant_id: Optional[str] = None,
) -> None:
    """Enforce the hierarchy rules. `current_tenant_id` is for self-reference checks on update."""
    if tenant_type == "root":
        raise HTTPException(
            status_code=400,
            detail="Root tenants cannot be created via the API. Exactly one root exists (SimpliMeta).",
        )

    if tenant_type == "partner":
        if not parent_tenant_id:
            raise HTTPException(
                status_code=400,
                detail="Partner tenants must have a parent_tenant_id pointing to the root tenant.",
            )
        parent = await repo.get_tenant(parent_tenant_id)
        if not parent:
            raise HTTPException(status_code=400, detail="parent_tenant_id does not exist")
        if parent.tenant_type != "root":
            raise HTTPException(
                status_code=400,
                detail="Partner tenants must have the root tenant as parent.",
            )
        if parent.status != "active":
            raise HTTPException(status_code=400, detail="Parent tenant is not active.")
        return

    if tenant_type == "account":
        if parent_tenant_id is None:
            return
        parent = await repo.get_tenant(parent_tenant_id)
        if not parent:
            raise HTTPException(status_code=400, detail="parent_tenant_id does not exist")
        if parent.tenant_type not in ("partner", "root"):
            raise HTTPException(
                status_code=400,
                detail="Account tenants may only have a partner or root as parent.",
            )
        if parent.status != "active":
            raise HTTPException(status_code=400, detail="Parent tenant is not active.")
        if current_tenant_id and current_tenant_id == parent_tenant_id:
            raise HTTPException(status_code=400, detail="A tenant cannot be its own parent.")
        return

    raise HTTPException(status_code=400, detail=f"Unknown tenant_type: {tenant_type}")


def _ensure_root_admin(current_user) -> None:
    """Phase 1A: tenants management is restricted to admins on the root tenant."""
    if not has_perm(current_user, "tenants.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    user_tenant_id = getattr(current_user, "tenant_id", None)
    if not user_tenant_id:
        raise HTTPException(status_code=403, detail="User is not assigned to a tenant")


# ---- Routes ----

@router.get("/me")
async def get_my_tenant(
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Return the current user's tenant + chain of ancestors (self → root)."""
    repo = TenantRepository(pg)
    user_tenant_id = getattr(current_user, "tenant_id", None)
    if not user_tenant_id:
        raise HTTPException(status_code=404, detail="No tenant assigned to user")

    # Walk up the parent chain. Cap at 5 levels — protects against a cycle.
    chain = []
    seen = set()
    cursor_id: Optional[str] = user_tenant_id
    for _ in range(5):
        if not cursor_id or cursor_id in seen:
            break
        seen.add(cursor_id)
        t = await repo.resolve_tenant(cursor_id)
        if not t:
            break
        chain.append(t)
        cursor_id = t.parent_tenant_id

    if not chain:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"tenant": chain[0], "ancestry": chain}


@router.get("")
async def list_tenants(
        tenant_type: Optional[str] = None,
        status: Optional[str] = None,
        parent_tenant_id: Optional[str] = None,
        current_user=Depends(get_current_user),
        pg: AsyncSession = Depends(get_session),
):
    """List tenants. Phase 1A: requires `tenants.view`."""
    if not has_perm(current_user, "tenants.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if tenant_type and tenant_type not in {"root", "partner", "account"}:
        raise HTTPException(status_code=400, detail="Invalid tenant_type filter")
    if status and status not in {"active", "suspended", "archived"}:
        raise HTTPException(status_code=400, detail="Invalid status filter")

    tenants = await TenantRepository(pg).list_tenants(
        tenant_type=tenant_type,
        status=status,
        parent_tenant_id=parent_tenant_id,
    )
    return {"tenants": tenants, "count": len(tenants)}


@router.get("/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    if not has_perm(current_user, "tenants.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    t = await TenantRepository(pg).get_tenant(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t


@router.post("")
async def create_tenant(
    data: TenantCreate,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Create a partner or account tenant. Root creation is reserved to seeding."""
    _ensure_root_admin(current_user)
    repo = TenantRepository(pg)

    # Caller must be on the root tenant — defence in depth alongside the perm check.
    user_tenant = await repo.get_tenant(current_user.tenant_id)
    if not user_tenant or user_tenant.tenant_type != "root":
        raise HTTPException(
            status_code=403,
            detail="Only users on the root tenant can create new tenants in Phase 1A.",
        )

    slug = (data.slug or "").strip().lower()
    _validate_slug(slug)
    await _validate_hierarchy(repo, data.tenant_type, data.parent_tenant_id)

    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    if await repo.get_by_slug(slug):
        raise HTTPException(status_code=409, detail=f"A tenant with slug '{slug}' already exists.")

    now = datetime.now(timezone.utc)
    tenant = await repo.create_tenant(Tenant(
        tenant_id=_gen_tenant_id(),
        name=name,
        slug=slug,
        tenant_type=data.tenant_type,
        parent_tenant_id=data.parent_tenant_id,
        status="active",
        created_at=now,
        updated_at=now,
        created_by=current_user.user_id,
    ))

    await log_action(
        action="tenant_created",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="tenant",
        resource_id=tenant.tenant_id,
        details={"name": name, "slug": slug, "tenant_type": data.tenant_type},
    )
    return tenant


@router.patch("/{tenant_id}")
async def update_tenant(
        tenant_id: str,
        data: TenantUpdate,
        current_user=Depends(get_current_user),
        pg: AsyncSession = Depends(get_session),
):
    _ensure_root_admin(current_user)
    repo = TenantRepository(pg)

    existing = await repo.get_tenant(tenant_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if existing.tenant_type == "root":
        raise HTTPException(
            status_code=400,
            detail="Root tenant cannot be modified via the API.",
        )

    updates: dict = {}

    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        updates["name"] = name

    if data.slug is not None:
        slug = data.slug.strip().lower()
        _validate_slug(slug)
        clash = await repo.get_by_slug(slug)
        if clash and clash.tenant_id != tenant_id:
            raise HTTPException(status_code=409, detail="slug already in use")
        updates["slug"] = slug

    if data.parent_tenant_id is not None or data.tenant_type is not None:
        new_type = data.tenant_type or existing.tenant_type
        new_parent = (
            data.parent_tenant_id
            if data.parent_tenant_id is not None
            else existing.parent_tenant_id
        )
        await _validate_hierarchy(repo, new_type, new_parent, current_tenant_id=tenant_id)
        if data.tenant_type is not None:
            updates["tenant_type"] = new_type
        if data.parent_tenant_id is not None:
            updates["parent_tenant_id"] = new_parent

    if data.status is not None:
        if data.status not in {"active", "suspended", "archived"}:
            raise HTTPException(status_code=400, detail="Invalid status")
        updates["status"] = data.status

    refreshed = await repo.update_tenant(tenant_id, updates)

    await log_action(
        action="tenant_updated",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="tenant",
        resource_id=tenant_id,
        details={"changes": updates},
    )
    return refreshed


@router.delete("/{tenant_id}")
async def archive_tenant(
    tenant_id: str,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Soft-archive a tenant. Hard delete is intentionally not supported."""
    _ensure_root_admin(current_user)
    repo = TenantRepository(pg)

    existing = await repo.get_tenant(tenant_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if existing.tenant_type == "root":
        raise HTTPException(status_code=400, detail="Root tenant cannot be archived.")

    if existing.status == "archived":
        return {"tenant_id": tenant_id, "status": "archived", "noop": True}

    # Block archive if it has active (non-archived) children.
    active_children = [
        t for t in await repo.list_tenants(parent_tenant_id=tenant_id)
        if t.status != "archived"
    ]
    if active_children:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot archive: tenant has {len(active_children)} active child tenant(s). Archive children first.",
        )

    await repo.update_tenant(tenant_id, {"status": "archived"})

    await log_action(
        action="tenant_archived",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="tenant",
        resource_id=tenant_id,
        details={"name": existing.name},
    )

    return {"tenant_id": tenant_id, "status": "archived"}
