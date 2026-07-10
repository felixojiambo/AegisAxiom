"""
Projects module — Sprint CRUD (agile methodology only).

Adapted from Davies-PMO-app `app/api/sprints.py` for the AEGISAXIOM ingestion stack.
Collection: pm_sprints. ID prefix: sprint_. Applies to project_type=agile.

Wholesale-replacement note: this file replaces the legacy
`backend/routes/project_management_sprints.py`.
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional
from datetime import datetime, date, timezone

from database import get_db
from models import gen_id
from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from services.ingestion.route_helpers import crud_envelope
from services.field_registry import register_fields, register_module_events, register_module_config


router = APIRouter()


# ---------------------------------------------------------------------------
# Field registry — surfaces sprint fields to policy engine + AI schema layer.
# ---------------------------------------------------------------------------
register_fields("projects", "sprint", [
    {"name": "sprint_id", "type": "string", "label": "Sprint ID", "description": "Primary key (sprint_<uuid>)"},
    {"name": "project_id", "type": "string", "label": "Project ID", "description": "FK to projects.project_id (must be agile project)"},
    {"name": "name", "type": "string", "label": "Name", "description": "Sprint name"},
    {"name": "start_date", "type": "date", "label": "Start Date", "description": "Sprint start date"},
    {"name": "end_date", "type": "date", "label": "End Date", "description": "Sprint end date"},
    {"name": "goal", "type": "string", "label": "Goal", "description": "Sprint goal — what this sprint is trying to deliver"},
    {"name": "capacity_points", "type": "number", "label": "Capacity (points)", "description": "Story-point capacity target the team committed to for this sprint"},
    {"name": "completed_points", "type": "number", "label": "Completed (points)", "description": "Story points actually completed; computed when sprint closes"},
    {"name": "retrospective", "type": "object", "label": "Retrospective",
     "description": "Sprint retro: { went_well, didnt_go_well, action_items }"},
    {"name": "status", "type": "string", "label": "Status",
     "options": [
         {"value": "planned", "label": "Planned"},
         {"value": "active", "label": "Active"},
         {"value": "closed", "label": "Closed"},
     ],
     "description": "Sprint lifecycle status"},
    {"name": "created_at", "type": "datetime", "label": "Created At"},
    {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    {"name": "closed_at", "type": "datetime", "label": "Closed At", "description": "Set by /sprints/{id}/close"},
    {"name": "created_by", "type": "string", "label": "Created By", "description": "FK to users.user_id"},
])

register_module_config("projects", "pm_sprints", "sprint_id", "Custom")
register_module_events("projects", "sprint", ["created", "updated", "deleted", "status_changed"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
SPRINT_STATUS_ENUM = {"planned", "active", "closed"}


class SprintRetrospective(BaseModel):
    """Sprint retrospective payload — three free-text sections.

    All fields are optional so the form can be saved progressively. Empty
    strings are accepted and stored — distinguishing "not answered" from
    "intentionally blank" is handled by the UI, not the API.
    """
    model_config = ConfigDict(extra="forbid")
    went_well: Optional[str] = Field(default=None, max_length=4000)
    didnt_go_well: Optional[str] = Field(default=None, max_length=4000)
    action_items: Optional[str] = Field(default=None, max_length=4000)


class SprintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    goal: Optional[str] = Field(default=None, max_length=2000)
    capacity_points: Optional[int] = Field(default=None, ge=0, le=10000)
    status: Optional[str] = "planned"


class SprintUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    goal: Optional[str] = Field(default=None, max_length=2000)
    capacity_points: Optional[int] = Field(default=None, ge=0, le=10000)
    retrospective: Optional[SprintRetrospective] = None
    status: Optional[str] = None


class SprintClose(BaseModel):
    """Optional retrospective payload submitted when closing a sprint.

    When omitted, /close still transitions status, computes velocity, and
    returns a blank retrospective shell the UI can fill in via subsequent
    PATCH /sprints/{id} calls.
    """
    model_config = ConfigDict(extra="forbid")
    retrospective: Optional[SprintRetrospective] = None


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


async def _load_agile_project(db, project_id: str, tenant_id: str) -> dict:
    project = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}
    )
    if project is None:
        raise HTTPException(status_code=422, detail=f"project_id {project_id} does not match any project")
    if project.get("project_type") != "agile":
        raise HTTPException(
            status_code=422,
            detail=(
                "Sprints are only valid on agile projects "
                f"(this project is {project.get('project_type')})"
            ),
        )
    return project


_FROZEN_PROJECT_STATUSES = {"closed", "on_hold"}


def _refuse_if_project_frozen(project: Dict[str, Any], action: str = "create") -> None:
    """Wave 4.5 / SF4 state guard: reject sprint create on closed or on_hold
    projects. Mirrors the helper in `projects_task.py` so frozen agile
    projects can't accept new sprints. Same 409 contract."""
    status = (project or {}).get("status")
    if status in _FROZEN_PROJECT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is {status}. Cannot {action} new tasks/waves/threads/"
                "blockers. Reopen the project first."
            ),
        )


async def _check_sprint_overlap(
    db,
    project_id: str,
    tenant_id: str,
    start_iso: str,
    end_iso: str,
    *,
    exclude_sprint_id: Optional[str] = None,
) -> None:
    """Wave 4 / A6: sprint date-overlap check.

    Refuse with 422 if [start_iso, end_iso] overlaps any existing sprint
    in the same project (excluding the sprint being updated, if any).
    Two sprints overlap iff a.start <= b.end AND a.end >= b.start.

    Phase 1E.3c — overlap scan is tenant-scoped. project_id is already
    tenant-unique but the filter is defensive.
    """
    query: Dict[str, Any] = {
        "project_id": project_id,
        "tenant_id": tenant_id,
        "start_date": {"$lte": end_iso},
        "end_date": {"$gte": start_iso},
    }
    if exclude_sprint_id:
        query["sprint_id"] = {"$ne": exclude_sprint_id}
    overlap = await db["pm_sprints"].find_one(query, {"_id": 0, "sprint_id": 1, "name": 1})
    if overlap:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Sprint dates overlap with existing sprint "
                f"{overlap.get('name')!r} ({overlap.get('sprint_id')})"
            ),
        )


def _check_within_project_window(
    project: Dict[str, Any], start_iso: str, end_iso: str
) -> None:
    """Wave 4 / A6: sprint must fit within the project's date window when
    both ends are present on the project. start_date / target_end_date are
    optional on the project side — when missing, the corresponding bound is
    not enforced."""
    proj_start = project.get("start_date")
    proj_end = project.get("target_end_date")
    if proj_start and start_iso and start_iso < proj_start:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Sprint start_date {start_iso} is before project start_date "
                f"{proj_start}"
            ),
        )
    if proj_end and end_iso and end_iso > proj_end:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Sprint end_date {end_iso} is after project target_end_date "
                f"{proj_end}"
            ),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/sprints", dependencies=[Depends(require_ai_permission("projects.sprint.view"))])
async def list_sprints(
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.sprint.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    # Phase 1E.3c — every query starts tenant-scoped.
    query: dict = {"tenant_id": tenant_id}
    if project_id:
        query["project_id"] = project_id
    if status:
        if status not in SPRINT_STATUS_ENUM:
            raise HTTPException(status_code=400, detail=f"Invalid status; must be one of {sorted(SPRINT_STATUS_ENUM)}")
        query["status"] = status
    total = await db["pm_sprints"].count_documents(query)
    items = (
        await db["pm_sprints"]
        .find(query, {"_id": 0})
        .sort("start_date", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/sprints/{sprint_id}", dependencies=[Depends(require_ai_permission("projects.sprint.view"))])
async def get_sprint(
    sprint_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.sprint.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    doc = await db["pm_sprints"].find_one(
        {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Sprint not found")
    return doc


@router.post(
    "/sprints",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.sprint.create"))],
)
async def create_sprint(
    payload: SprintCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.sprint.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    project = await _load_agile_project(db, payload.project_id, tenant_id)
    _refuse_if_project_frozen(project, action="create")

    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")

    status_value = payload.status or "planned"
    if status_value not in SPRINT_STATUS_ENUM:
        raise HTTPException(status_code=422, detail=f"Invalid status; must be one of {sorted(SPRINT_STATUS_ENUM)}")

    start_iso = _date_to_iso(payload.start_date)
    end_iso = _date_to_iso(payload.end_date)

    # Wave 4 / A6: sprint must not overlap a sibling sprint in the same
    # project AND must fit within the project's start/target_end window.
    await _check_sprint_overlap(db, payload.project_id, tenant_id, start_iso, end_iso)
    _check_within_project_window(project, start_iso, end_iso)

    now = _now_iso()
    doc = {
        "sprint_id": gen_id("sprint_"),
        # Phase 1E.3c — stamp owning tenant.
        "tenant_id": tenant_id,
        "project_id": payload.project_id,
        "name": payload.name,
        "start_date": start_iso,
        "end_date": end_iso,
        "goal": payload.goal,
        "capacity_points": payload.capacity_points,
        "completed_points": None,
        "retrospective": None,
        "closed_at": None,
        "status": status_value,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects", entity="sprint", action="created",
        resource_id=doc["sprint_id"], current_user=current_user, db=db, request=request,
    ) as ctx:
        await db["pm_sprints"].insert_one(doc)
        doc.pop("_id", None)
        ctx.set_after(doc)

    return doc


@router.patch(
    "/sprints/{sprint_id}",
    dependencies=[Depends(require_ai_permission("projects.sprint.edit"))],
)
async def update_sprint(
    sprint_id: str,
    payload: SprintUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.sprint.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_sprints"].find_one(
        {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Sprint not found")

    dumped = payload.model_dump(exclude_unset=True)

    # Phase 1E.3c — defensive: drop tenant_id from update payload so a
    # client can never reparent a sprint to another tenant via PATCH.
    dumped.pop("tenant_id", None)

    # Date sanity
    sd = dumped.get("start_date", before.get("start_date"))
    ed = dumped.get("end_date", before.get("end_date"))
    sd_iso = _date_to_iso(sd) if not isinstance(sd, str) else sd
    ed_iso = _date_to_iso(ed) if not isinstance(ed, str) else ed
    if sd_iso and ed_iso and ed_iso < sd_iso:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")

    if "status" in dumped and dumped["status"] is not None and dumped["status"] not in SPRINT_STATUS_ENUM:
        raise HTTPException(status_code=422, detail=f"Invalid status; must be one of {sorted(SPRINT_STATUS_ENUM)}")

    # Wave 4 / A6: re-check overlap + project-window if either date changed.
    if "start_date" in dumped or "end_date" in dumped:
        if sd_iso and ed_iso:
            await _check_sprint_overlap(
                db,
                before["project_id"],
                tenant_id,
                sd_iso,
                ed_iso,
                exclude_sprint_id=sprint_id,
            )
            project = await db["projects"].find_one(
                {"project_id": before["project_id"], "tenant_id": tenant_id},
                {"_id": 0, "start_date": 1, "target_end_date": 1},
            )
            if project:
                _check_within_project_window(project, sd_iso, ed_iso)

    # Convert date objects to iso strings for storage
    for key in ("start_date", "end_date"):
        if key in dumped and isinstance(dumped[key], date):
            dumped[key] = _date_to_iso(dumped[key])

    # Pydantic dumps SprintRetrospective into a dict already because the
    # whole payload was model_dump'd above — but if the caller sent a
    # bare model object this is a defensive copy.
    if "retrospective" in dumped and dumped["retrospective"] is not None:
        retro = dumped["retrospective"]
        if hasattr(retro, "model_dump"):
            dumped["retrospective"] = retro.model_dump()

    dumped["updated_at"] = _now_iso()

    async with crud_envelope(
        module="projects", entity="sprint", action="updated",
        resource_id=sprint_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_sprints"].update_one(
            {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"$set": dumped}
        )
        updated = await db["pm_sprints"].find_one(
            {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(updated)

    # Status-change side-event
    if before.get("status") != updated.get("status"):
        async with crud_envelope(
            module="projects", entity="sprint", action="status_changed",
            resource_id=sprint_id, current_user=current_user, db=db,
            before=before, request=request,
        ) as ctx:
            ctx.set_after(updated)

    return updated


@router.delete(
    "/sprints/{sprint_id}",
    dependencies=[Depends(require_ai_permission("projects.sprint.delete"))],
)
async def delete_sprint(
    sprint_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.sprint.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_sprints"].find_one(
        {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Sprint not found")

    async with crud_envelope(
        module="projects", entity="sprint", action="deleted",
        resource_id=sprint_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_sprints"].delete_one(
            {"sprint_id": sprint_id, "tenant_id": tenant_id}
        )
        ctx.set_after(None)

    return {"ok": True, "deleted_id": sprint_id}


# ---------------------------------------------------------------------------
# Wave 6 / 6B1 — sprint ceremonies: /close endpoint
# ---------------------------------------------------------------------------
async def _compute_velocity_for_sprint(
    db, sprint_id: str, tenant_id: str
) -> Dict[str, Any]:
    """Sum story_points for tasks in the sprint that landed in a `is_done`
    column on close. Falls back to legacy DONE_STATUSES when the project's
    methodology_config doesn't carry the agile column registry — defensive
    against early agile projects created before column-driven done-tracking
    landed.

    Returns `{completed_points, completed_task_count, total_task_count}`.

    Phase 1E.3c — all cross-collection lookups are tenant-scoped.
    """
    # Pull the sprint's project to find which agile columns count as done.
    sprint = await db["pm_sprints"].find_one(
        {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if sprint is None:
        return {"completed_points": 0, "completed_task_count": 0, "total_task_count": 0}
    project = await db["projects"].find_one(
        {"project_id": sprint["project_id"], "tenant_id": tenant_id},
        {"_id": 0, "methodology_config": 1},
    ) or {}
    columns = (project.get("methodology_config") or {}).get("columns") or []
    done_column_ids = {c.get("id") for c in columns if c.get("is_done")}

    cursor = db["pm_tasks"].find(
        {"methodology_fields.sprint_id": sprint_id, "tenant_id": tenant_id},
        {"_id": 0, "task_id": 1, "status": 1, "methodology_fields": 1},
    )
    completed_points = 0
    completed_count = 0
    total = 0
    async for task in cursor:
        total += 1
        mf = task.get("methodology_fields") or {}
        col_id = mf.get("column_id")
        is_done = (col_id and col_id in done_column_ids) or (
            task.get("status") in {"complete", "shipped"}
        )
        if is_done:
            completed_count += 1
            sp = mf.get("story_points")
            if isinstance(sp, (int, float)) and not isinstance(sp, bool):
                completed_points += sp
    return {
        "completed_points": completed_points,
        "completed_task_count": completed_count,
        "total_task_count": total,
    }


@router.post(
    "/sprints/{sprint_id}/close",
    dependencies=[Depends(require_ai_permission("projects.sprint.change_status"))],
)
async def close_sprint(
    sprint_id: str,
    request: Request,
    payload: Optional[SprintClose] = None,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Close a sprint, compute velocity, and accept (or stub) the retro.

    Idempotent on already-closed sprints in the sense that we re-compute
    velocity from the current task state — useful if tasks were finalised
    after the first close attempt. The status itself only flips on the first
    successful call.
    """
    if not has_perm(current_user, "projects.sprint.change_status"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_sprints"].find_one(
        {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Sprint not found")

    if before.get("status") == "closed":
        # Allow recompute — useful if late tasks shipped — but signal with a
        # specific note in the response.
        already_closed = True
    else:
        already_closed = False

    velocity = await _compute_velocity_for_sprint(db, sprint_id, tenant_id)

    set_payload: Dict[str, Any] = {
        "status": "closed",
        "completed_points": velocity["completed_points"],
        "updated_at": _now_iso(),
    }
    if not already_closed:
        set_payload["closed_at"] = _now_iso()

    if payload and payload.retrospective is not None:
        set_payload["retrospective"] = payload.retrospective.model_dump()
    elif before.get("retrospective") is None:
        # Stub a blank retrospective shell so the UI can render the form
        # immediately without a separate "create retro" round-trip.
        set_payload["retrospective"] = {
            "went_well": "",
            "didnt_go_well": "",
            "action_items": "",
        }

    # Use the shared `status_changed` action — the existing CrudAction
    # Literal doesn't include "closed", and the semantics are accurate
    # ("status changed to closed"). The retro / velocity payload still flows
    # in the `after` doc so downstream consumers can branch on
    # `after.status == "closed"`.
    async with crud_envelope(
        module="projects", entity="sprint", action="status_changed",
        resource_id=sprint_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_sprints"].update_one(
            {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"$set": set_payload}
        )
        updated = await db["pm_sprints"].find_one(
            {"sprint_id": sprint_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(updated)

    return {
        "sprint": updated,
        "velocity": velocity,
        "already_closed": already_closed,
    }
