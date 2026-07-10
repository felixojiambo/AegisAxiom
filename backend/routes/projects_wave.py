"""
Projects module — Wave CRUD (waves_threads methodology only).

Adapted from Davies-PMO-app `app/api/waves.py` for the AEGISAXIOM ingestion stack.
Collection: pm_waves. ID prefix: wave_. Applies to project_type=waves_threads.

Per-project wave_number sequence is allocated from `db.pm_counters` keyed
`f"project:{project_id}:wave"`. Stored in pm_counters (not the global
`db.counters`) to keep Projects' counter namespace isolated.

Wholesale-replacement note: this file replaces the legacy
`backend/routes/project_management_waves.py`.
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any, Set, Tuple
from datetime import datetime, date, timezone

from pymongo import ReturnDocument

from database import get_db
from models import gen_id
from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from services.ingestion.route_helpers import crud_envelope
from services.field_registry import register_fields, register_module_events, register_module_config


router = APIRouter()


# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------
register_fields("projects", "wave", [
    {"name": "wave_id", "type": "string", "label": "Wave ID", "description": "Primary key (wave_<uuid>)"},
    {"name": "project_id", "type": "string", "label": "Project ID", "description": "FK to projects.project_id (must be waves_threads project)"},
    {"name": "wave_number", "type": "number", "label": "Wave Number", "description": "Per-project monotonic sequence allocated from db.pm_counters"},
    {"name": "start_date", "type": "date", "label": "Start Date"},
    {"name": "end_date", "type": "date", "label": "End Date"},
    {"name": "status", "type": "string", "label": "Status",
     "options": [
         {"value": "planning", "label": "Planning"},
         {"value": "executing", "label": "Executing"},
         {"value": "postmortem", "label": "Postmortem"},
         {"value": "closed", "label": "Closed"},
     ],
     "description": "Wave lifecycle status"},
    {"name": "goal", "type": "string", "label": "Goal"},
    {"name": "postmortem", "type": "list", "label": "Postmortem", "description": "Postmortem object — buckets, attendees, notes"},
    {"name": "depends_on_wave_ids", "type": "list", "label": "Depends On Wave IDs", "description": "FKs to other waves in the same project"},
    {"name": "created_at", "type": "datetime", "label": "Created At"},
    {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    {"name": "created_by", "type": "string", "label": "Created By"},
])

register_module_config("projects", "pm_waves", "wave_id", "Custom")
register_module_events("projects", "wave", ["created", "updated", "deleted", "status_changed"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
WAVE_STATUS_ENUM = {"planning", "executing", "postmortem", "closed"}


class WaveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    goal: Optional[str] = Field(default=None, max_length=240)
    depends_on_wave_ids: List[str] = Field(default_factory=list)
    postmortem: Optional[Dict[str, Any]] = None
    status: Optional[str] = "planning"


class WaveUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    goal: Optional[str] = Field(default=None, max_length=240)
    depends_on_wave_ids: Optional[List[str]] = None
    postmortem: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_to_iso(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


async def _load_wt_project(db, project_id: str, tenant_id: str) -> dict:
    project = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}
    )
    if project is None:
        raise HTTPException(status_code=422, detail=f"project_id {project_id} does not match any project")
    if project.get("project_type") != "waves_threads":
        raise HTTPException(
            status_code=422,
            detail=(
                "Waves are only valid on waves_threads projects "
                f"(this project is {project.get('project_type')})"
            ),
        )
    return project


_FROZEN_PROJECT_STATUSES = {"closed", "on_hold"}


def _refuse_if_project_frozen(project: dict) -> None:
    """Wave 4 / A4: reject wave create on closed/on_hold projects."""
    status = (project or {}).get("status")
    if status in _FROZEN_PROJECT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is {status}. Cannot create new waves. "
                "Reopen the project first."
            ),
        )


async def _validate_depends_on(
    db, project_id: str, depends_on_wave_ids: List[str], tenant_id: str
) -> None:
    if not depends_on_wave_ids:
        return
    unique = list(set(depends_on_wave_ids))
    cnt = await db["pm_waves"].count_documents(
        {"wave_id": {"$in": unique}, "project_id": project_id, "tenant_id": tenant_id}
    )
    if cnt != len(unique):
        raise HTTPException(
            status_code=422,
            detail="All depends_on_wave_ids must reference waves in this project",
        )


async def _detect_dep_cycle(
    db,
    project_id: str,
    tenant_id: str,
    *,
    candidate_wave_id: Optional[str],
    candidate_depends_on: List[str],
) -> Optional[List[str]]:
    """Wave 4 / A6: Wave dependency cycle check.

    Builds the dependency graph from the project's existing waves, applies
    the candidate's `depends_on_wave_ids` as if the candidate were already
    persisted (or already updated, when `candidate_wave_id` is supplied),
    and runs a DFS from the candidate looking for a path back to itself.

    Returns the cycle path (list of wave_ids) if a cycle is detected,
    else None. Caller is responsible for raising 422 with a friendly
    message.

    Phase 1E.3c — graph build is tenant-scoped. Cross-tenant waves can't
    exist in the same project (project_id is tenant-unique), but the
    filter is defensive belt-and-braces.
    """
    if not candidate_depends_on:
        return None

    # Pull the existing dep graph for the project once. Cheap on small
    # wave counts, and waves per project rarely exceed double digits.
    graph: Dict[str, List[str]] = {}
    async for w in db["pm_waves"].find(
        {"project_id": project_id, "tenant_id": tenant_id},
        {"_id": 0, "wave_id": 1, "depends_on_wave_ids": 1},
    ):
        wid = w["wave_id"]
        graph[wid] = list(w.get("depends_on_wave_ids") or [])

    # Apply the candidate's deps. For a new wave, candidate_wave_id is None
    # — give it a stable placeholder so the DFS below has a starting node.
    candidate_id = candidate_wave_id or "__candidate__"
    graph[candidate_id] = list(candidate_depends_on)

    # DFS from the candidate. A cycle exists iff we can reach
    # candidate_id again by following edges.
    stack: List[Tuple[str, List[str]]] = [(candidate_id, [candidate_id])]
    visited: Set[str] = set()
    while stack:
        node, path = stack.pop()
        for dep in graph.get(node, []):
            if dep == candidate_id:
                return path + [dep]
            if dep in visited:
                continue
            visited.add(dep)
            stack.append((dep, path + [dep]))
    return None


async def _next_wave_number(db, project_id: str, tenant_id: str) -> int:
    """Atomically allocate the next per-project wave_number from pm_counters.

    Stored in `db.pm_counters` (NOT global `db.counters`) keyed by
    `project:{project_id}:wave` so Projects' counter docs are isolated from
    other modules' counters.

    Phase 1E.3c — counter doc carries tenant_id. project_id is already
    tenant-unique so the key is naturally isolated; tenant_id in the
    filter is defensive.
    """
    result = await db["pm_counters"].find_one_and_update(
        {"_id": f"project:{project_id}:wave", "tenant_id": tenant_id},
        {"$inc": {"seq": 1}, "$setOnInsert": {"tenant_id": tenant_id}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(result["seq"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/waves", dependencies=[Depends(require_ai_permission("projects.wave.view"))])
async def list_waves(
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.wave.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    # Phase 1E.3c — every query starts tenant-scoped.
    query: dict = {"tenant_id": tenant_id}
    if project_id:
        query["project_id"] = project_id
    if status:
        if status not in WAVE_STATUS_ENUM:
            raise HTTPException(status_code=400, detail=f"Invalid status; must be one of {sorted(WAVE_STATUS_ENUM)}")
        query["status"] = status
    total = await db["pm_waves"].count_documents(query)
    items = (
        await db["pm_waves"]
        .find(query, {"_id": 0})
        .sort("wave_number", 1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/waves/{wave_id}", dependencies=[Depends(require_ai_permission("projects.wave.view"))])
async def get_wave(
    wave_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.wave.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    doc = await db["pm_waves"].find_one(
        {"wave_id": wave_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Wave not found")
    return doc


@router.post(
    "/waves",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.wave.create"))],
)
async def create_wave(
    payload: WaveCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.wave.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    project = await _load_wt_project(db, payload.project_id, tenant_id)
    _refuse_if_project_frozen(project)

    if payload.start_date and payload.end_date and payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")

    await _validate_depends_on(
        db, payload.project_id, list(payload.depends_on_wave_ids), tenant_id
    )

    # Wave 4 / A6: cycle check. The candidate doesn't have an id yet, so
    # the helper substitutes a placeholder — sufficient to catch a path
    # from the new wave back to itself via existing dependency edges.
    cycle = await _detect_dep_cycle(
        db,
        payload.project_id,
        tenant_id,
        candidate_wave_id=None,
        candidate_depends_on=list(payload.depends_on_wave_ids),
    )
    if cycle is not None:
        raise HTTPException(
            status_code=422,
            detail=f"depends_on_wave_ids would create a dependency cycle: {' -> '.join(cycle)}",
        )

    status_value = payload.status or "planning"
    if status_value not in WAVE_STATUS_ENUM:
        raise HTTPException(status_code=422, detail=f"Invalid status; must be one of {sorted(WAVE_STATUS_ENUM)}")

    wave_number = await _next_wave_number(db, payload.project_id, tenant_id)
    now = _now_iso()
    doc = {
        "wave_id": gen_id("wave_"),
        # Phase 1E.3c — stamp owning tenant.
        "tenant_id": tenant_id,
        "project_id": payload.project_id,
        "wave_number": wave_number,
        "start_date": _date_to_iso(payload.start_date),
        "end_date": _date_to_iso(payload.end_date),
        "status": status_value,
        "goal": payload.goal,
        "postmortem": payload.postmortem,
        "depends_on_wave_ids": list(payload.depends_on_wave_ids),
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects", entity="wave", action="created",
        resource_id=doc["wave_id"], current_user=current_user, db=db, request=request,
    ) as ctx:
        await db["pm_waves"].insert_one(doc)
        doc.pop("_id", None)
        ctx.set_after(doc)

    return doc


@router.patch(
    "/waves/{wave_id}",
    dependencies=[Depends(require_ai_permission("projects.wave.edit"))],
)
async def update_wave(
    wave_id: str,
    payload: WaveUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.wave.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_waves"].find_one(
        {"wave_id": wave_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Wave not found")

    dumped = payload.model_dump(exclude_unset=True)

    # Phase 1E.3c — defensive: drop tenant_id from update payload so a
    # client can never reparent a wave to another tenant via PATCH.
    dumped.pop("tenant_id", None)

    # Date sanity
    sd = dumped.get("start_date", before.get("start_date"))
    ed = dumped.get("end_date", before.get("end_date"))
    sd_iso = _date_to_iso(sd) if not isinstance(sd, str) else sd
    ed_iso = _date_to_iso(ed) if not isinstance(ed, str) else ed
    if sd_iso and ed_iso and ed_iso < sd_iso:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")

    if "status" in dumped and dumped["status"] is not None and dumped["status"] not in WAVE_STATUS_ENUM:
        raise HTTPException(status_code=422, detail=f"Invalid status; must be one of {sorted(WAVE_STATUS_ENUM)}")

    if "depends_on_wave_ids" in dumped and dumped["depends_on_wave_ids"] is not None:
        await _validate_depends_on(
            db, before["project_id"], list(dumped["depends_on_wave_ids"]), tenant_id
        )
        cycle = await _detect_dep_cycle(
            db,
            before["project_id"],
            tenant_id,
            candidate_wave_id=wave_id,
            candidate_depends_on=list(dumped["depends_on_wave_ids"]),
        )
        if cycle is not None:
            raise HTTPException(
                status_code=422,
                detail=f"depends_on_wave_ids would create a dependency cycle: {' -> '.join(cycle)}",
            )

    for key in ("start_date", "end_date"):
        if key in dumped and isinstance(dumped[key], date):
            dumped[key] = _date_to_iso(dumped[key])

    dumped["updated_at"] = _now_iso()

    new_status = dumped.get("status", before.get("status"))
    status_changed = "status" in dumped and new_status != before.get("status")

    # Wave 4 / A5: write audit row inside the SAME envelope as the wave
    # update; surfaces in dashboard recent_activity.
    async with crud_envelope(
        module="projects", entity="wave", action="updated",
        resource_id=wave_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_waves"].update_one(
            {"wave_id": wave_id, "tenant_id": tenant_id}, {"$set": dumped}
        )
        if status_changed:
            await db["pm_wave_status_history"].insert_one(
                {
                    # Phase 1E.3c — append-only audit stamped with tenant.
                    "tenant_id": tenant_id,
                    "wave_id": wave_id,
                    "project_id": before.get("project_id"),
                    "from_status": before.get("status"),
                    "to_status": new_status,
                    "changed_at": datetime.now(timezone.utc),
                    "changed_by": current_user.user_id,
                }
            )
        updated = await db["pm_waves"].find_one(
            {"wave_id": wave_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(updated)

    if status_changed:
        async with crud_envelope(
            module="projects", entity="wave", action="status_changed",
            resource_id=wave_id, current_user=current_user, db=db,
            before=before, request=request,
        ) as ctx:
            ctx.set_after(updated)

    return updated


@router.delete(
    "/waves/{wave_id}",
    dependencies=[Depends(require_ai_permission("projects.wave.delete"))],
)
async def delete_wave(
    wave_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.wave.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_waves"].find_one(
        {"wave_id": wave_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Wave not found")

    # Cascade runs INSIDE the envelope so a policy deny short-circuits without
    # touching child threads/blocker_tickets. Pre-collect thread_ids before the
    # parent delete so the blocker_ticket cascade has the FK list.
    # Phase 1E.3c — every cascade query is tenant-scoped. wave_id is
    # already tenant-unique so the sub-queries are naturally isolated,
    # but the filter is defensive.
    cascade: Dict[str, int] = {"threads": 0, "blockers": 0}

    async with crud_envelope(
        module="projects", entity="wave", action="deleted",
        resource_id=wave_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        thread_ids = [
            d["thread_id"]
            async for d in db["pm_threads"].find(
                {"wave_id": wave_id, "tenant_id": tenant_id},
                {"thread_id": 1, "_id": 0},
            )
        ]
        await db["pm_waves"].delete_one(
            {"wave_id": wave_id, "tenant_id": tenant_id}
        )
        if thread_ids:
            cascade["threads"] = (
                await db["pm_threads"].delete_many(
                    {"wave_id": wave_id, "tenant_id": tenant_id}
                )
            ).deleted_count
            cascade["blockers"] = (
                await db["pm_blocker_tickets"].delete_many(
                    {
                        "tenant_id": tenant_id,
                        "$or": [
                            {"thread_id": {"$in": thread_ids}},
                            {"target_thread_id": {"$in": thread_ids}},
                        ],
                    }
                )
            ).deleted_count
        # Clear depends_on references on sibling waves.
        await db["pm_waves"].update_many(
            {"depends_on_wave_ids": wave_id, "tenant_id": tenant_id},
            {"$pull": {"depends_on_wave_ids": wave_id}},
        )
        ctx.set_after(None)

    return {
        "ok": True,
        "deleted_id": wave_id,
        "threads_deleted": cascade["threads"],
        "blockers_deleted": cascade["blockers"],
    }
