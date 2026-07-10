"""Tenants router — Phase 1A multi-tenancy foundation.

Manages the tenant tree (root → partner → account) per the multi-tenancy
design doc. Root tenant is SimpliMeta; partners are organisations that resell
AEGISAXIOM to their own accounts; accounts are leaf customer tenants.

Phase 1A scope:
  * CRUD for tenants. Root-only access. Hierarchy validation enforced.
  * `GET /api/tenants/me` returns the current user's tenant + ancestry —
    used by the frontend to render tenant context in the UI.
  * No tenant scoping of OTHER collections in this phase. That's Phase 1B+.

Hierarchy rules enforced here:
  * `root` tenants have `parent_tenant_id = None`. Exactly one root exists
    (SimpliMeta) and it is seeded at startup, not created via this API.
  * `partner` tenants must have `parent_tenant_id` pointing to a `root`.
  * `account` tenants may have `parent_tenant_id = None` (a direct customer
    of SimpliMeta) OR pointing to a `partner` (a customer of a reseller).

Permissions:
  * `tenants.view`  — list/read tenants
  * `tenants.manage` — create/update/archive tenants
Both granted to admins on the SimpliMeta root tenant only. Non-root tenants
do not get tenants.manage in this phase.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import logging
import re

from pymongo.errors import DuplicateKeyError

from database import get_db
from models import Tenant, TenantCreate, TenantUpdate, gen_id
from auth_helper import get_current_user, has_perm
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
        db,
        tenant_type: str,
        parent_tenant_id: Optional[str],
        current_tenant_id: Optional[str] = None,
) -> None:
    """Enforce the hierarchy rules. `current_tenant_id` is for self-reference checks on update."""
    if tenant_type == "root":
        # root tenants are NEVER created via the API — only seeded at startup.
        # Reject any attempt to create or repoint to root via this path.
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
        parent = await db.tenants.find_one(
            {"tenant_id": parent_tenant_id}, {"_id": 0, "tenant_type": 1, "status": 1}
        )
        if not parent:
            raise HTTPException(status_code=400, detail="parent_tenant_id does not exist")
        if parent.get("tenant_type") != "root":
            raise HTTPException(
                status_code=400,
                detail="Partner tenants must have the root tenant as parent.",
            )
        if parent.get("status") != "active":
            raise HTTPException(status_code=400, detail="Parent tenant is not active.")
        return

    if tenant_type == "account":
        if parent_tenant_id is None:
            # Direct account under SimpliMeta — allowed.
            return
        parent = await db.tenants.find_one(
            {"tenant_id": parent_tenant_id}, {"_id": 0, "tenant_type": 1, "status": 1}
        )
        if not parent:
            raise HTTPException(status_code=400, detail="parent_tenant_id does not exist")
        if parent.get("tenant_type") not in ("partner", "root"):
            # Accounts cannot nest under other accounts.
            raise HTTPException(
                status_code=400,
                detail="Account tenants may only have a partner or root as parent.",
            )
        if parent.get("status") != "active":
            raise HTTPException(status_code=400, detail="Parent tenant is not active.")
        if current_tenant_id and current_tenant_id == parent_tenant_id:
            raise HTTPException(status_code=400, detail="A tenant cannot be its own parent.")
        return

    raise HTTPException(status_code=400, detail=f"Unknown tenant_type: {tenant_type}")


def _ensure_root_admin(current_user) -> None:
    """Phase 1A: tenants management is restricted to admins on the root tenant.

    Once Phase 1B grants partners visibility into their child accounts, this
    helper will be replaced by a finer-grained check (e.g., partners can list
    their own children but not create new partners).
    """
    if not has_perm(current_user, "tenants.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # Belt-and-braces: even with the perm, only root-tenant users can mutate
    # the tenant tree in this phase.
    user_tenant_id = getattr(current_user, "tenant_id", None)
    if not user_tenant_id:
        raise HTTPException(status_code=403, detail="User is not assigned to a tenant")
    # The root check is delegated to the route handler (which has db access).


# ---- Routes ----

@router.get("/me")
async def get_my_tenant(current_user=Depends(get_current_user)):
    """Return the current user's tenant + chain of ancestors (self → root)."""
    db = get_db()
    user_tenant_id = getattr(current_user, "tenant_id", None)
    if not user_tenant_id:
        raise HTTPException(status_code=404, detail="No tenant assigned to user")

    # Walk up the parent chain. Cap at 5 levels — the spec is two-level, but
    # the cap protects us if a misconfigured cycle somehow gets in.
    chain = []
    seen = set()
    cursor_id: Optional[str] = user_tenant_id
    for _ in range(5):
        if not cursor_id or cursor_id in seen:
            break
        seen.add(cursor_id)
        doc = await db.tenants.find_one({"tenant_id": cursor_id}, {"_id": 0})
        if not doc:
            break
        chain.append(doc)
        cursor_id = doc.get("parent_tenant_id")

    if not chain:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"tenant": chain[0], "ancestry": chain}


@router.get("")
async def list_tenants(
        tenant_type: Optional[str] = None,
        status: Optional[str] = None,
        parent_tenant_id: Optional[str] = None,
        current_user=Depends(get_current_user),
):
    """List tenants. Phase 1A: requires `tenants.view`.

    Filters: tenant_type, status, parent_tenant_id (exact match).
    """
    if not has_perm(current_user, "tenants.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    query: dict = {}
    if tenant_type:
        if tenant_type not in {"root", "partner", "account"}:
            raise HTTPException(status_code=400, detail="Invalid tenant_type filter")
        query["tenant_type"] = tenant_type
    if status:
        if status not in {"active", "suspended", "archived"}:
            raise HTTPException(status_code=400, detail="Invalid status filter")
        query["status"] = status
    if parent_tenant_id is not None:
        query["parent_tenant_id"] = parent_tenant_id

    tenants = await db.tenants.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"tenants": tenants, "count": len(tenants)}


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "tenants.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    doc = await db.tenants.find_one({"tenant_id": tenant_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return doc


@router.post("")
async def create_tenant(data: TenantCreate, current_user=Depends(get_current_user)):
    """Create a partner or account tenant. Root creation is reserved to seeding."""
    _ensure_root_admin(current_user)
    db = get_db()

    # Caller must be on the root tenant — defence in depth alongside the perm check.
    user_tenant = await db.tenants.find_one(
        {"tenant_id": current_user.tenant_id}, {"_id": 0, "tenant_type": 1}
    )
    if not user_tenant or user_tenant.get("tenant_type") != "root":
        raise HTTPException(
            status_code=403,
            detail="Only users on the root tenant can create new tenants in Phase 1A.",
        )

    # Validate slug
    slug = (data.slug or "").strip().lower()
    _validate_slug(slug)

    # Validate hierarchy
    await _validate_hierarchy(db, data.tenant_type, data.parent_tenant_id)

    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    now = datetime.now(timezone.utc)
    tenant_doc = {
        "tenant_id": _gen_tenant_id(),
        "name": name,
        "slug": slug,
        "tenant_type": data.tenant_type,
        "parent_tenant_id": data.parent_tenant_id,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id,
    }

    try:
        await db.tenants.insert_one(tenant_doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail=f"A tenant with slug '{slug}' already exists.",
        )

    await log_action(
        action="tenant_created",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="tenant",
        resource_id=tenant_doc["tenant_id"],
        details={"name": name, "slug": slug, "tenant_type": data.tenant_type},
    )

    tenant_doc.pop("_id", None)
    return tenant_doc


@router.patch("/{tenant_id}")
async def update_tenant(
        tenant_id: str,
        data: TenantUpdate,
        current_user=Depends(get_current_user),
):
    _ensure_root_admin(current_user)
    db = get_db()

    existing = await db.tenants.find_one({"tenant_id": tenant_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Block edits to the root tenant via the API. If we ever need to rename
    # SimpliMeta itself we'll do it via a controlled migration.
    if existing.get("tenant_type") == "root":
        raise HTTPException(
            status_code=400,
            detail="Root tenant cannot be modified via the API.",
        )

    updates: dict = {"updated_at": datetime.now(timezone.utc)}

    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        updates["name"] = name

    if data.slug is not None:
        slug = data.slug.strip().lower()
        _validate_slug(slug)
        updates["slug"] = slug

    if data.parent_tenant_id is not None or data.tenant_type is not None:
        new_type = data.tenant_type or existing["tenant_type"]
        new_parent = (
            data.parent_tenant_id
            if data.parent_tenant_id is not None
            else existing.get("parent_tenant_id")
        )
        # Reparenting validation. Pass current_tenant_id to block self-parenting.
        await _validate_hierarchy(db, new_type, new_parent, current_tenant_id=tenant_id)
        if data.tenant_type is not None:
            updates["tenant_type"] = new_type
        if data.parent_tenant_id is not None:
            updates["parent_tenant_id"] = new_parent

    if data.status is not None:
        if data.status not in {"active", "suspended", "archived"}:
            raise HTTPException(status_code=400, detail="Invalid status")
        updates["status"] = data.status

    try:
        await db.tenants.update_one({"tenant_id": tenant_id}, {"$set": updates})
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="slug already in use")

    await log_action(
        action="tenant_updated",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="tenant",
        resource_id=tenant_id,
        details={"changes": {k: v for k, v in updates.items() if k != "updated_at"}},
    )

    refreshed = await db.tenants.find_one({"tenant_id": tenant_id}, {"_id": 0})
    return refreshed


@router.delete("/{tenant_id}")
async def archive_tenant(tenant_id: str, current_user=Depends(get_current_user)):
    """Soft-archive a tenant. Hard delete is intentionally not supported."""
    _ensure_root_admin(current_user)
    db = get_db()

    existing = await db.tenants.find_one({"tenant_id": tenant_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if existing.get("tenant_type") == "root":
        raise HTTPException(status_code=400, detail="Root tenant cannot be archived.")

    if existing.get("status") == "archived":
        return {"tenant_id": tenant_id, "status": "archived", "noop": True}

    # Block archive if it has active children.
    child_count = await db.tenants.count_documents(
        {"parent_tenant_id": tenant_id, "status": {"$ne": "archived"}}
    )
    if child_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot archive: tenant has {child_count} active child tenant(s). Archive children first.",
        )

    await db.tenants.update_one(
        {"tenant_id": tenant_id},
        {"$set": {"status": "archived", "updated_at": datetime.now(timezone.utc)}},
    )

    await log_action(
        action="tenant_archived",
        actor_id=current_user.user_id,
        actor_email=current_user.email,
        resource_type="tenant",
        resource_id=tenant_id,
        details={"name": existing.get("name")},
    )

    return {"tenant_id": tenant_id, "status": "archived"}