"""Projects — Saved Views CRUD (v1.2 / Thread 2B).

Personal, user-scoped named filter sets for quick-filter pills.

Collection: `pm_user_saved_views`
Auth: `projects.dashboard.view` (owner-only access enforced per route)

Design doc: .waves/projects-v1.2-smart-views-design.md §2

Routes:
  GET    /api/projects/saved-views              — list caller's views
  POST   /api/projects/saved-views              — create a view
  PATCH  /api/projects/saved-views/{id}         — partial update (owner only)
  DELETE /api/projects/saved-views/{id}         — hard delete (owner only)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.tenant_scoping import get_current_tenant_id
from models import gen_id

logger = logging.getLogger(__name__)
router = APIRouter()

# Pill IDs accepted by the system — mirrors frontend pillRegistry.js
VALID_PILL_IDS = frozenset([
    "mine",
    "due_this_week",
    "overdue",
    "blocked",
    "at_risk",
    "unassigned",
    "high_priority",
    "mine_pm",
])

VALID_VIEW_TYPES = frozenset(["board", "backlog", "list", "calendar", "myday"])
VALID_SCOPES = frozenset(["global", "project"])
MAX_SAVED_VIEWS_PER_USER = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scrub(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    out = {**doc}
    out.pop("_id", None)
    return out


async def _get_owned_view(
    db, saved_view_id: str, user_id: str, tenant_id: str
) -> dict:
    """Fetch a saved view that belongs to the caller; 404 if missing/not owned.

    Phase 1E.3c — tenant-scoped. A saved view created under one tenant
    context is not visible under another (e.g. root admin's home-tenant
    views are not visible while they're operating under override).
    """
    doc = await db["pm_user_saved_views"].find_one(
        {"saved_view_id": saved_view_id, "tenant_id": tenant_id},
        {"_id": 0},
    )
    # Return 404 regardless of whether it exists or belongs to another user
    # to avoid info-leak about other users' saved view IDs.
    if not doc or doc.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Saved view not found")
    return doc


async def _flip_default(
    db, user_id: str, view_type: str, exclude_id: str, tenant_id: str
) -> None:
    """If a default already exists for (user, view_type), set it to false.

    This is the "clear" half of the atomic flip. Callers MUST follow the
    ordering:

        1) update_many({user_id, view_type, is_default: True, _id != target},
                       {$set: {is_default: False}})
        2) update_one (or insert) the target with is_default: True

    Reversing those two steps can produce two defaults under concurrent
    writes. The route handler also relies on the unique partial index
    ensured by ``_ensure_default_unique_index`` to make the database the
    final arbiter: if two concurrent operations both reach step (2), one
    will fail with DuplicateKey and surface as 409 to the second caller.

    Phase 1E.3c — scoped per tenant.
    """
    await db["pm_user_saved_views"].update_many(
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "view_type": view_type,
            "is_default": True,
            "saved_view_id": {"$ne": exclude_id},
        },
        {"$set": {"is_default": False, "updated_at": _now_iso()}},
    )


# Track index-ensure attempts so we only run it once per worker process.
_DEFAULT_UNIQUE_INDEX_ENSURED = False


async def _ensure_default_unique_index(db) -> None:
    """Best-effort partial unique index on (user_id, view_type) where is_default==True.

    Guarantees at-most-one default per (user_id, view_type) at the database
    layer. The route handler clears existing defaults before setting the
    new one, but two concurrent PATCHes targeting different rows could both
    flip-then-set and produce two defaults. With this index in place the
    second writer's update_one fires DuplicateKeyError, the route catches
    it and returns 409.

    If the index can't be created (older Mongo, permission issue), we log
    and continue — the application-level ordering is the primary defense.
    """
    global _DEFAULT_UNIQUE_INDEX_ENSURED
    if _DEFAULT_UNIQUE_INDEX_ENSURED:
        return
    try:
        await db["pm_user_saved_views"].create_index(
            [("user_id", 1), ("view_type", 1)],
            name="pm_user_saved_views_one_default",
            unique=True,
            partialFilterExpression={"is_default": True},
            background=True,
        )
        _DEFAULT_UNIQUE_INDEX_ENSURED = True
    except Exception as e:
        logger.warning(
            "pm_user_saved_views default-unique index ensure failed (non-fatal): %s", e
        )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class SavedViewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    scope: Literal["global", "project"]
    project_id: Optional[str] = None
    view_type: str
    pills: List[str] = Field(default_factory=list)
    is_default: bool = False

    @field_validator("view_type")
    @classmethod
    def validate_view_type(cls, v: str) -> str:
        if v not in VALID_VIEW_TYPES:
            raise ValueError(f"view_type must be one of {sorted(VALID_VIEW_TYPES)}")
        return v

    @field_validator("pills")
    @classmethod
    def validate_pills(cls, v: List[str]) -> List[str]:
        invalid = [p for p in v if p not in VALID_PILL_IDS]
        if invalid:
            raise ValueError(f"Unknown pill id(s): {invalid}")
        # Deduplicate while preserving order
        seen: set = set()
        deduped = []
        for p in v:
            if p not in seen:
                deduped.append(p)
                seen.add(p)
        return deduped


class SavedViewPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    pills: Optional[List[str]] = None
    is_default: Optional[bool] = None

    @field_validator("pills")
    @classmethod
    def validate_pills(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        invalid = [p for p in v if p not in VALID_PILL_IDS]
        if invalid:
            raise ValueError(f"Unknown pill id(s): {invalid}")
        seen: set = set()
        deduped = []
        for p in v:
            if p not in seen:
                deduped.append(p)
                seen.add(p)
        return deduped


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/saved-views")
async def list_saved_views(
    view_type: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """List saved views owned by the calling user.

    Optional filters: view_type, scope, project_id.
    When project_id is provided, returns views with scope='project' AND that
    project_id, plus all scope='global' views.
    """
    if not has_perm(current_user, "projects.dashboard.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    # Phase 1E.3c — saved views are per-user AND per-tenant. Views
    # created under one tenant context are invisible under another (e.g.
    # root admin's home-tenant views aren't visible during override).
    query: dict = {"user_id": current_user.user_id, "tenant_id": tenant_id}

    if view_type:
        if view_type not in VALID_VIEW_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid view_type: {view_type}")
        query["view_type"] = view_type

    if scope:
        if scope not in VALID_SCOPES:
            raise HTTPException(status_code=400, detail=f"Invalid scope: {scope}")
        query["scope"] = scope

    if project_id:
        # Return project-scoped views for this project OR global views
        query.pop("scope", None)  # override any scope filter
        query["$or"] = [
            {"scope": "global"},
            {"scope": "project", "project_id": project_id},
        ]

    cursor = (
        db["pm_user_saved_views"]
        .find(query, {"_id": 0})
        .sort("updated_at", -1)
        .limit(MAX_SAVED_VIEWS_PER_USER + 1)
    )
    items = [_scrub(doc) async for doc in cursor]
    return {"items": items, "total": len(items)}


@router.post("/saved-views", status_code=201)
async def create_saved_view(
    body: SavedViewCreate,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Create a named saved view for the calling user."""
    if not has_perm(current_user, "projects.dashboard.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    if body.scope == "project" and not body.project_id:
        raise HTTPException(
            status_code=400,
            detail="project_id is required when scope is 'project'",
        )

    db = get_db()
    user_id = current_user.user_id

    # Enforce 50-view soft cap (per tenant per user — quota is independent
    # in each tenant context for a user with multi-tenant access).
    count = await db["pm_user_saved_views"].count_documents(
        {"user_id": user_id, "tenant_id": tenant_id}
    )
    if count >= MAX_SAVED_VIEWS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum of {MAX_SAVED_VIEWS_PER_USER} saved views reached. Delete some before creating more.",
        )

    now = _now_iso()
    saved_view_id = gen_id("sv_")

    doc = {
        "saved_view_id": saved_view_id,
        # Phase 1E.3c — stamp owning tenant.
        "tenant_id": tenant_id,
        "user_id": user_id,
        "name": body.name,
        "scope": body.scope,
        "project_id": body.project_id,
        "view_type": body.view_type,
        "pills": body.pills,
        "advanced_filters": {},
        "is_default": body.is_default,
        "created_at": now,
        "updated_at": now,
    }

    await _ensure_default_unique_index(db)

    if body.is_default:
        await _flip_default(db, user_id, body.view_type, saved_view_id, tenant_id)

    try:
        await db["pm_user_saved_views"].insert_one(doc)
    except Exception as e:
        # DuplicateKeyError on the partial unique index means another
        # concurrent write already set is_default=True for (user, view_type).
        # Surface as 409 so the caller can retry.
        if "duplicate key" in str(e).lower() or e.__class__.__name__ == "DuplicateKeyError":
            raise HTTPException(
                status_code=409,
                detail="Another saved view was just set as default for this view type. Retry.",
            )
        raise

    return _scrub(doc)


@router.patch("/saved-views/{saved_view_id}")
async def update_saved_view(
    saved_view_id: str,
    body: SavedViewPatch,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Partial update — only name, pills, is_default are mutable.

    scope, project_id, view_type, user_id are immutable — return 400 if
    the client sends them. (Pydantic strips unknown fields so we don't need
    to check explicitly; any field outside SavedViewPatch is silently ignored.)
    """
    if not has_perm(current_user, "projects.dashboard.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    await _get_owned_view(db, saved_view_id, current_user.user_id, tenant_id)

    await _ensure_default_unique_index(db)

    updates: dict = {"updated_at": _now_iso()}
    if body.name is not None:
        updates["name"] = body.name
    if body.pills is not None:
        updates["pills"] = body.pills
    if body.is_default is not None:
        updates["is_default"] = body.is_default
        if body.is_default:
            # Read the existing doc to know view_type for the flip query
            existing = await db["pm_user_saved_views"].find_one(
                {"saved_view_id": saved_view_id, "tenant_id": tenant_id},
                {"view_type": 1},
            )
            if existing:
                # Order matters: clear other defaults FIRST, then set the
                # target. With the partial unique index in place a
                # concurrent racer that ran clear-then-set on a different
                # target would fail this find_one_and_update with
                # DuplicateKey, which we surface as 409.
                await _flip_default(
                    db,
                    current_user.user_id,
                    existing["view_type"],
                    saved_view_id,
                    tenant_id,
                )

    try:
        updated = await db["pm_user_saved_views"].find_one_and_update(
            {"saved_view_id": saved_view_id, "tenant_id": tenant_id},
            {"$set": updates},
            return_document=True,
            projection={"_id": 0},
        )
    except Exception as e:
        if "duplicate key" in str(e).lower() or e.__class__.__name__ == "DuplicateKeyError":
            raise HTTPException(
                status_code=409,
                detail="Another saved view was just set as default for this view type. Retry.",
            )
        raise

    if not updated:
        raise HTTPException(status_code=404, detail="Saved view not found")

    return _scrub(updated)


@router.delete("/saved-views/{saved_view_id}", status_code=204)
async def delete_saved_view(
    saved_view_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Hard delete. Returns 204. 404 if not found or not owned by caller."""
    if not has_perm(current_user, "projects.dashboard.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    await _get_owned_view(db, saved_view_id, current_user.user_id, tenant_id)

    await db["pm_user_saved_views"].delete_one(
        {"saved_view_id": saved_view_id, "tenant_id": tenant_id}
    )
    return None
