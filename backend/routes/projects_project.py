"""Projects ingestion — Project entity routes.

Adapted from Davies-PMO-app `backend/app/api/projects.py`. Differences:
  * Mongo ObjectIds replaced with AEGISAXIOM string IDs (`gen_id("proj_")`).
  * Persistence uses `db["projects"]` (PK collection per the mapping file).
  * Cross-references (pm_id, assigned_user_ids, template_id) are string user_id /
    template_id values — no ObjectId parsing.
  * State changes route through `crud_envelope`; status changes audit to the
    `pm_project_status_history` collection.
  * Methodology config validation is inlined here (Thread 1E will eventually own
    a shared helper at `services.projects_methodology`; until then the rules
    live with the route to keep the router self-contained).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy.ext.asyncio import AsyncSession

from auth_helper import get_current_user, has_perm
from database import get_db
from db.session import get_session
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from repositories.users import UserRepository
from models import gen_id
from pymongo import ReturnDocument

from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope
from services.projects_methodology import (
    validate_project_methodology_config,
    validate_task_methodology_fields,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Field / event / module registration
# ---------------------------------------------------------------------------
PROJECT_TYPE_OPTIONS = [
    {"value": "waterfall", "label": "Waterfall"},
    {"value": "agile", "label": "Agile"},
    {"value": "waves_threads", "label": "Waves + Threads"},
]
SERVICE_LINE_OPTIONS = [
    {"value": "ucaas", "label": "UCaaS"},
    {"value": "networking", "label": "Networking"},
    {"value": "cloud", "label": "Cloud"},
    {"value": "security", "label": "Security"},
    {"value": "ai_product", "label": "AI Product"},
    {"value": "other", "label": "Other"},
]
PROJECT_STATUS_OPTIONS = [
    {"value": "planning", "label": "Planning"},
    {"value": "active", "label": "Active"},
    {"value": "on_hold", "label": "On Hold"},
    {"value": "closed", "label": "Closed"},
]
HEALTH_OPTIONS = [
    {"value": "green", "label": "Green"},
    {"value": "yellow", "label": "Yellow"},
    {"value": "red", "label": "Red"},
]

register_fields(
    "projects",
    "project",
    [
        {"name": "name", "type": "string", "label": "Name"},
        {"name": "description", "type": "string", "label": "Description"},
        {
            "name": "project_type",
            "type": "string",
            "label": "Project Type",
            "options": PROJECT_TYPE_OPTIONS,
            "description": "Methodology — waterfall, agile, or waves+threads (immutable after create)",
        },
        {
            "name": "service_line",
            "type": "string",
            "label": "Service Line",
            "options": SERVICE_LINE_OPTIONS,
        },
        {
            "name": "status",
            "type": "string",
            "label": "Status",
            "options": PROJECT_STATUS_OPTIONS,
        },
        {
            "name": "health",
            "type": "string",
            "label": "Health",
            "options": HEALTH_OPTIONS,
        },
        {"name": "pm_id", "type": "string", "label": "Project Manager (user_id)"},
        {"name": "assigned_user_ids", "type": "list", "label": "Assigned Users"},
        {"name": "start_date", "type": "date", "label": "Start Date"},
        {"name": "target_end_date", "type": "date", "label": "Target End Date"},
        {"name": "actual_end_date", "type": "date", "label": "Actual End Date"},
        {"name": "template_id", "type": "string", "label": "Template ID"},
        {
            "name": "methodology_config",
            "type": "object",
            "label": "Methodology Config",
            "description": "Shape varies by project_type: phases (waterfall) | columns (agile) | wave config (waves_threads)",
        },
        {"name": "contract_id", "type": "string", "label": "Contract ID"},
        {"name": "account_id", "type": "string", "label": "Account ID"},
    ],
)
register_module_config("projects", "projects", "project_id", "Custom")
register_module_events(
    "projects", "project", ["created", "updated", "deleted", "status_changed"]
)


# ---------------------------------------------------------------------------
# Pydantic DTOs
# ---------------------------------------------------------------------------
PROJECT_TYPES = {"waterfall", "agile", "waves_threads"}
SERVICE_LINES = {"ucaas", "networking", "cloud", "security", "ai_product", "other"}
PROJECT_STATUSES = {"planning", "active", "on_hold", "closed"}
HEALTHS = {"green", "yellow", "red"}


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: Optional[str] = None
    project_type: str
    service_line: str
    status: str = "planning"
    health: str = "green"
    # pm_id is optional on the wire — if omitted, the create handler
    # defaults to the calling user's user_id (Wave 4.5 / SF8).
    pm_id: Optional[str] = None
    assigned_user_ids: List[str] = Field(default_factory=list)
    start_date: Optional[str] = None
    target_end_date: Optional[str] = None
    actual_end_date: Optional[str] = None
    template_id: Optional[str] = None
    methodology_config: Dict[str, Any] = Field(default_factory=dict)
    # Bridge fields for contracts/billing/time tracking. Nullable. SHOULD ADD #10.
    contract_id: Optional[str] = None
    account_id: Optional[str] = None


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    service_line: Optional[str] = None
    status: Optional[str] = None
    health: Optional[str] = None
    pm_id: Optional[str] = None
    assigned_user_ids: Optional[List[str]] = None
    start_date: Optional[str] = None
    target_end_date: Optional[str] = None
    actual_end_date: Optional[str] = None
    template_id: Optional[str] = None
    methodology_config: Optional[Dict[str, Any]] = None
    contract_id: Optional[str] = None
    account_id: Optional[str] = None


class ProjectFromTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    project_type: str
    service_line: str
    # pm_id optional — defaults to caller's user_id when omitted (SF8).
    pm_id: Optional[str] = None
    template_id: str
    start_date: Optional[str] = None
    target_end_date: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _scrub(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    out = {**doc}
    out.pop("_id", None)
    return out


def _validate_enum(value: Any, allowed: set, field: str) -> str:
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be one of {sorted(allowed)} (got {value!r})",
        )
    return value


# Methodology config validation lives in services.projects_methodology — the
# canonical, well-tested module. Imported above as
# `validate_project_methodology_config`. Route handlers must use it directly.


async def _next_task_number_for_project(db: Any, project_id: str, tenant_id: str) -> int:
    """Atomically allocate the next per-project task_number.

    Mirrors `projects_task._next_task_number`. Lives here too so the
    from-template path doesn't have to import from projects_task (which would
    create a circular dependency). Both helpers key the same counter doc
    `project:{project_id}:task` in pm_counters so allocations stay monotonic
    regardless of which route writes the task.

    Phase 1E.3c — project_id is already globally unique (proj_xxx), so
    the counter key naturally stays tenant-isolated without tenant_id
    in the key. We still add `tenant_id` to the filter as a belt-and-
    braces measure — if a project_id ever collided across tenants
    (shouldn't happen, but defensive), tenant_id prevents the wrong
    counter being read.
    """
    result = await db["pm_counters"].find_one_and_update(
        {"_id": f"project:{project_id}:task", "tenant_id": tenant_id},
        {"$inc": {"seq": 1}, "$setOnInsert": {"tenant_id": tenant_id}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(result["seq"])


def _user_is_project_pm(current_user: Any, project: Optional[Dict[str, Any]]) -> bool:
    """True if `current_user` is the PM of `project` AND has the projects view
    permission. Wave 4 / A4 PM auto-grant — a PM gets implicit edit on their
    own project without needing the global projects.project.edit perm.

    Returns False if project is None (callers should still 404 separately).
    """
    if project is None:
        return False
    pm_id = project.get("pm_id")
    user_id = getattr(current_user, "user_id", None)
    if not pm_id or not user_id or pm_id != user_id:
        return False
    return has_perm(current_user, "projects.project.view")


def _can_edit_project(current_user: Any, project: Optional[Dict[str, Any]]) -> bool:
    """Permission check for project-scoped edits. Either the user holds the
    global `projects.project.edit` permission OR they are the PM of the
    project and hold `projects.project.view`."""
    if has_perm(current_user, "projects.project.edit"):
        return True
    return _user_is_project_pm(current_user, project)


async def _refuse_if_referenced(db: Any, project_id: str, tenant_id: str) -> None:
    """Project DELETE incoming-FK guard. Raises 409 if any cross-module
    document still references this project_id. Per the Wave 4 audit, the
    cross-module collections that carry `project_id` (or `project_ids`)
    are: time_entries, contracts, opportunities, ap_bills, field_services.

    Phase 1E.3c — every check is tenant-scoped. A tenant shouldn't be
    blocked from deleting their own project by references in another
    tenant's data (defensive — shouldn't happen since project_id is
    globally unique, but cheaper to filter than to debug a phantom
    block later).
    """
    checks = [
        ("time_entries", {"project_id": project_id, "tenant_id": tenant_id}),
        ("contracts", {"project_ids": project_id, "tenant_id": tenant_id}),
        ("opportunities", {"project_id": project_id, "tenant_id": tenant_id}),
        ("ap_bills", {"project_id": project_id, "tenant_id": tenant_id}),
        ("field_services", {"project_id": project_id, "tenant_id": tenant_id}),
    ]
    blockers: List[str] = []
    for coll_name, filt in checks:
        try:
            cnt = await db[coll_name].count_documents(filt, limit=1)
        except Exception:  # collection missing in some envs — treat as zero.
            cnt = 0
        if cnt > 0:
            blockers.append(coll_name)
    if blockers:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot delete project — still referenced by: "
                f"{sorted(blockers)}. Detach or archive those records first."
            ),
        )


async def _build_task_doc(
    db: Any,
    project: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    current_user_id: str,
    tenant_id: str,
    validate: bool = True,
) -> Dict[str, Any]:
    """Build a pm_tasks document from a payload dict, routed through the
    methodology validator and the per-project task_number counter.

    Used by both `create_project_from_template` (template seeded tasks) and
    by callers that want a single source of truth for task shape. The
    `projects_task.create_task` route still inlines the equivalent logic
    against its TaskCreate Pydantic model — both paths must produce the
    same shape and both must call the validator.

    Args:
        db: Mongo handle.
        project: parent project document (must carry project_type and
            methodology_config).
        payload: task payload dict — supports the same keys as
            TaskCreate plus any caller-supplied defaults.
        current_user_id: stamped on `created_by`.
        tenant_id: stamped on the returned doc so the eventual insert
            into pm_tasks carries the owning tenant. The counter
            allocation is also tenant-scoped via this value (1E.3c).
        validate: when True (default), routes the payload through
            `validate_task_methodology_fields` so the resulting status
            and methodology_fields are guaranteed to be valid for the
            project's methodology. Callers should never pass False unless
            they have already run the validator with the same arguments.
    """
    fields_in = payload.get("methodology_fields") or {}
    status_in = payload.get("status")
    if validate:
        normalized_fields, final_status = validate_task_methodology_fields(
            project, fields_in, status_in
        )
    else:
        normalized_fields, final_status = fields_in, status_in or "not_started"

    task_number = await _next_task_number_for_project(
        db, project["project_id"], tenant_id
    )
    now_iso = _utc_now_iso()
    return {
        "task_id": gen_id("task_"),
        # Phase 1E.3c — stamp tenant_id so downstream inserts (template
        # instantiation, from-template route, projects_task callers
        # once that file's refactored) carry the owning tenant.
        "tenant_id": tenant_id,
        "project_id": project["project_id"],
        "title": payload.get("title") or "Untitled",
        "description": payload.get("description"),
        "assignee_id": payload.get("assignee_id"),
        "status": final_status,
        "priority": payload.get("priority", "medium"),
        "estimated_duration_hours": payload.get("estimated_duration_hours"),
        "parent_task_id": payload.get("parent_task_id"),
        "start_date": payload.get("start_date"),
        "due_date": payload.get("due_date"),
        "dependencies": list(payload.get("dependencies") or []),
        "methodology_fields": normalized_fields,
        "labels": list(payload.get("labels") or []),
        "task_number": task_number,
        "sort_index": payload.get("sort_index"),
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": current_user_id,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/projects",
    dependencies=[Depends(require_ai_permission("projects.project.view"))],
)
async def list_projects(
    skip: int = 0,
    limit: int = 100,
    project_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    service_line: Optional[str] = Query(None),
    pm_id: Optional[str] = Query(None),
    assigned_user_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, min_length=1),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.project.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    capped = min(limit, 500)
    # Phase 1E.3c — every query starts tenant-scoped. Caller filters
    # apply on top.
    query: Dict[str, Any] = {"tenant_id": tenant_id}
    if project_type:
        query["project_type"] = project_type
    if status:
        query["status"] = status
    if service_line:
        query["service_line"] = service_line
    if pm_id:
        query["pm_id"] = pm_id
    if assigned_user_id:
        query["assigned_user_ids"] = assigned_user_id
    if q:
        query["name"] = {"$regex": q, "$options": "i"}

    total = await db["projects"].count_documents(query)
    items = (
        await db["projects"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(capped)
        .to_list(capped)
    )
    # Derived pagination fields — see GET /api/contracts for the
    # rationale. skip + capped limit stay unchanged so existing
    # PaginationControls callers keep working.
    page = (skip // capped) + 1 if capped > 0 else 1
    pages = max(1, -(-total // capped)) if capped > 0 else 1
    has_prev = skip > 0
    has_next = (skip + capped) < total
    return {
        "items": items,
        "total": total,
        "skip": skip,
        "limit": capped,
        "page": page,
        "pages": pages,
        "has_prev": has_prev,
        "has_next": has_next,
    }


@router.get(
    "/projects/{project_id}",
    dependencies=[Depends(require_ai_permission("projects.project.view"))],
)
async def get_project(
    project_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.project.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    doc = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return doc


@router.post(
    "/projects",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.project.create"))],
)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    if not has_perm(current_user, "projects.project.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Enum validation.
    _validate_enum(payload.project_type, PROJECT_TYPES, "project_type")
    _validate_enum(payload.service_line, SERVICE_LINES, "service_line")
    _validate_enum(payload.status, PROJECT_STATUSES, "status")
    _validate_enum(payload.health, HEALTHS, "health")

    # Wave 5 / 5D6: pm_id default logic — admins who leave pm_id blank get
    # None so they can create projects on behalf of others and assign later.
    # Non-admins who leave pm_id blank become the PM (existing SF8 behavior).
    # The FK validation only runs when effective_pm_id is not None.
    effective_pm_id = (
        payload.pm_id
        if payload.pm_id
        else (None if has_perm(current_user, "admin.manage") else current_user.user_id)
    )
    pm_id = effective_pm_id
    # Phase 1E.3c — PM must belong to the calling tenant. Users from
    # other tenants are invisible (treated as "does not match any user")
    # to avoid leaking the existence of cross-tenant user_ids.
    if effective_pm_id and not await UserRepository(pg).user_exists_in_tenant(
        effective_pm_id, tenant_id
    ):
        raise HTTPException(
            status_code=422, detail=f"pm_id {effective_pm_id} does not match any user"
        )
    if payload.assigned_user_ids:
        cnt = await UserRepository(pg).count_users_in_tenant(
            list(set(payload.assigned_user_ids)), tenant_id
        )
        if cnt != len(set(payload.assigned_user_ids)):
            raise HTTPException(
                status_code=422,
                detail="One or more assigned_user_ids do not match a user",
            )

    normalized_cfg = validate_project_methodology_config(
        payload.project_type, payload.methodology_config
    )

    now_iso = _utc_now_iso()
    project_id = gen_id("proj_")
    doc: Dict[str, Any] = {
        "project_id": project_id,
        # Phase 1E.3c — stamp the owning tenant on every new project.
        "tenant_id": tenant_id,
        "name": payload.name,
        "description": payload.description,
        "project_type": payload.project_type,
        "service_line": payload.service_line,
        "status": payload.status,
        "health": payload.health,
        "pm_id": pm_id,
        "assigned_user_ids": list(payload.assigned_user_ids),
        "start_date": payload.start_date,
        "target_end_date": payload.target_end_date,
        "actual_end_date": payload.actual_end_date,
        "template_id": payload.template_id,
        "methodology_config": normalized_cfg,
        "contract_id": payload.contract_id,
        "account_id": payload.account_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects",
        entity="project",
        action="created",
        resource_id=project_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["projects"].insert_one(doc)
        ctx.set_after(_scrub(doc))

    return _scrub(doc)


@router.post(
    "/projects/from-template",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.project.create"))],
)
async def create_project_from_template(
    payload: ProjectFromTemplateCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    """Create a project from a pm_templates document.

    Pulls methodology defaults from the template (default_phases for waterfall,
    default_columns for agile, baseline waves_threads scaffold) and bulk-creates
    pm_tasks from the template's task_definitions array. Returns the project
    document with `task_count` of seeded tasks.
    """
    if not has_perm(current_user, "projects.project.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    _validate_enum(payload.project_type, PROJECT_TYPES, "project_type")
    _validate_enum(payload.service_line, SERVICE_LINES, "service_line")

    # Phase 1E.3c — readable template = tenant-owned or system. Same
    # visibility rule as projects_template.py uses. The produced project
    # is always stamped with the caller's tenant_id regardless of whether
    # the source template was system or tenant-owned.
    template = await db["pm_templates"].find_one(
        {
            "template_id": payload.template_id,
            "$or": [{"is_system": True}, {"tenant_id": tenant_id}],
        },
        {"_id": 0},
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.get("project_type") != payload.project_type:
        raise HTTPException(
            status_code=422,
            detail="project_type must match template.project_type",
        )

    # Wave 5 / 5D6: same pm_id default logic as create_project — admins who
    # leave pm_id blank get None (assign PM later); non-admins get themselves.
    # FK validation only runs when effective_pm_id is not None.
    effective_pm_id = (
        payload.pm_id
        if payload.pm_id
        else (None if has_perm(current_user, "admin.manage") else current_user.user_id)
    )
    pm_id = effective_pm_id
    # Phase 1E.3c — PM must belong to the calling tenant.
    if effective_pm_id and not await UserRepository(pg).user_exists_in_tenant(
        effective_pm_id, tenant_id
    ):
        raise HTTPException(
            status_code=422, detail=f"pm_id {effective_pm_id} does not match any user"
        )

    # Build methodology_config from template defaults. The mapping file
    # canonicalizes phases as `List[Dict[name, start_date?, end_date?]]`; the
    # validator accepts both bare strings and dicts and normalizes to dicts.
    methodology_config: Dict[str, Any] = {}
    if payload.project_type == "waterfall":
        methodology_config = {"phases": list(template.get("default_phases") or [])}
    elif payload.project_type == "agile":
        # Wave 4 / A3: default_columns is now List[{id, name}] per Contract 3.
        # Legacy templates might still have bare strings in storage; accept
        # both shapes here so a re-ingest doesn't break older data.
        raw_cols = template.get("default_columns") or [
            {"id": "to_do", "name": "To Do"},
            {"id": "in_progress", "name": "In Progress"},
            {"id": "done", "name": "Done"},
        ]
        normalized: List[Dict[str, Any]] = []
        for i, c in enumerate(raw_cols):
            if isinstance(c, str):
                normalized.append(
                    {
                        "id": c.lower().replace(" ", "_"),
                        "name": c,
                        "order": i,
                    }
                )
            elif isinstance(c, dict):
                normalized.append(
                    {
                        "id": c.get("id") or (c.get("name") or "").lower().replace(" ", "_"),
                        "name": c.get("name") or c.get("id") or f"Column {i+1}",
                        "order": i,
                    }
                )
        methodology_config = {"columns": normalized}
    elif payload.project_type == "waves_threads":
        # Service validator requires `wave_count_target>=1` and non-empty modules.
        # Templates don't currently store these — give a sane scaffold default
        # that the user can edit on the project after creation.
        methodology_config = {"wave_count_target": 1, "modules": ["general"]}

    # Re-validate via shared helper so the result conforms to project schema.
    methodology_config = validate_project_methodology_config(
        payload.project_type, methodology_config
    )

    now_iso = _utc_now_iso()
    project_id = gen_id("proj_")
    project_doc: Dict[str, Any] = {
        "project_id": project_id,
        # Phase 1E.3c — stamp the owning tenant.
        "tenant_id": tenant_id,
        "name": payload.name,
        "description": None,
        "project_type": payload.project_type,
        "service_line": payload.service_line,
        "status": "planning",
        "health": "green",
        "pm_id": pm_id,
        "assigned_user_ids": [],
        "start_date": payload.start_date,
        "target_end_date": payload.target_end_date,
        "actual_end_date": None,
        "template_id": payload.template_id,
        "methodology_config": methodology_config,
        "contract_id": None,
        "account_id": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects",
        entity="project",
        action="created",
        resource_id=project_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["projects"].insert_one(project_doc)
        # Bulk-create tasks from template.task_definitions. Each task is
        # routed through `_build_task_doc` so the methodology validator
        # normalizes status (e.g. waves_threads templates with default
        # `not_started` get bumped to `scoped`) and task_number is allocated
        # atomically from the per-project counter — same path as the
        # individual /tasks POST.
        task_definitions = list(template.get("task_definitions") or [])
        task_count = 0
        for idx, td in enumerate(task_definitions):
            if not isinstance(td, dict):
                continue
            payload_dict = {**td, "sort_index": td.get("sort_index", idx)}
            task_doc = await _build_task_doc(
                db,
                project_doc,
                payload_dict,
                current_user_id=current_user.user_id,
                tenant_id=tenant_id,
                validate=True,
            )
            await db["pm_tasks"].insert_one(task_doc)
            task_count += 1
        ctx.set_after({**_scrub(project_doc), "_task_count_created": task_count})

    return {**_scrub(project_doc), "task_count": task_count}


@router.patch(
    "/projects/{project_id}",
    dependencies=[Depends(require_ai_permission("projects.project.edit"))],
)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    db = get_db()

    before = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Wave 4 / A4 PM auto-grant: a PM with view permission gets edit on
    # their own project even without the global projects.project.edit perm.
    if not _can_edit_project(current_user, before):
        raise HTTPException(status_code=403, detail="Permission denied")

    dumped = payload.model_dump(exclude_unset=True)

    # Phase 1E.3c — defensive: drop tenant_id from update payload so a
    # client can never reparent a project to another tenant via PATCH.
    dumped.pop("tenant_id", None)

    # Enum validation for whatever was provided.
    if "service_line" in dumped and dumped["service_line"] is not None:
        _validate_enum(dumped["service_line"], SERVICE_LINES, "service_line")
    if "status" in dumped and dumped["status"] is not None:
        _validate_enum(dumped["status"], PROJECT_STATUSES, "status")
    if "health" in dumped and dumped["health"] is not None:
        _validate_enum(dumped["health"], HEALTHS, "health")

    # Methodology config — re-validate against the existing immutable project_type.
    if dumped.get("methodology_config") is not None:
        dumped["methodology_config"] = validate_project_methodology_config(
            before["project_type"], dumped["methodology_config"]
        )
        # Agile: reject removal of columns that still have referencing tasks.
        if before["project_type"] == "agile":
            new_ids = {c["id"] for c in dumped["methodology_config"]["columns"]}
            old_ids = {
                c["id"]
                for c in (before.get("methodology_config") or {}).get("columns", [])
            }
            removed = old_ids - new_ids
            if removed:
                in_use = await db["pm_tasks"].count_documents(
                    {
                        "project_id": project_id,
                        "tenant_id": tenant_id,
                        "methodology_fields.column_id": {"$in": list(removed)},
                    }
                )
                if in_use > 0:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Cannot remove column(s) {sorted(removed)} — "
                            f"{in_use} task(s) still reference them"
                        ),
                    )

    if dumped.get("pm_id") is not None:
        # Phase 1E.3c — PM must belong to the calling tenant.
        if not await UserRepository(pg).user_exists_in_tenant(
            dumped["pm_id"], tenant_id
        ):
            raise HTTPException(
                status_code=422,
                detail=f"pm_id {dumped['pm_id']} does not match any user",
            )

    if dumped.get("assigned_user_ids"):
        ids = list(set(dumped["assigned_user_ids"]))
        cnt = await UserRepository(pg).count_users_in_tenant(ids, tenant_id)
        if cnt != len(ids):
            raise HTTPException(
                status_code=422,
                detail="One or more assigned_user_ids do not match a user",
            )

    updates = {**dumped, "updated_at": _utc_now_iso()}
    new_status = updates.get("status", before.get("status"))
    status_changed = "status" in updates and new_status != before.get("status")

    # Wave 4 / A5: write the audit row inside the SAME envelope as the
    # state change. Previously a status flip opened a second envelope after
    # the project was already updated — if a policy denied the second, the
    # state change had already persisted and the audit row was lost
    # (audit BLOCKER B4).
    async with crud_envelope(
        module="projects",
        entity="project",
        action="updated",
        resource_id=project_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["projects"].update_one(
            {"project_id": project_id, "tenant_id": tenant_id}, {"$set": updates}
        )
        if status_changed:
            await db["pm_project_status_history"].insert_one(
                {
                    # Phase 1E.3c — status history is append-only audit;
                    # stamp tenant_id so per-tenant queries can scope.
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "from_status": before.get("status"),
                    "to_status": new_status,
                    "changed_at": _utc_now(),
                    "changed_by": current_user.user_id,
                }
            )
        after = await db["projects"].find_one(
            {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(after)

    # status_changed event still emits separately so downstream subscribers
    # can react to status flips, but the audit row is now safely inside
    # the same envelope as the projects collection write.
    if status_changed:
        async with crud_envelope(
            module="projects",
            entity="project",
            action="status_changed",
            resource_id=project_id,
            current_user=current_user,
            db=db,
            before=before,
            request=request,
        ) as ctx:
            ctx.set_after(after)

    return after


@router.delete(
    "/projects/{project_id}",
    dependencies=[Depends(require_ai_permission("projects.project.delete"))],
)
async def delete_project(
    project_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.project.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Wave 4 / A4: refuse delete if cross-module documents still reference
    # this project_id. Audit identified time_entries, contracts (project_ids
    # array), opportunities, ap_bills, and field_services as the FK holders.
    await _refuse_if_referenced(db, project_id, tenant_id)

    # Cascade collection: gather wave/thread ids first so we can prune
    # threads + blocker_tickets that key off them. Phase 1E.3c — every
    # read/write in the cascade is tenant-scoped. project_id is already
    # unique per tenant so the wave_id/thread_id sub-queries are
    # naturally scoped via project_id, but adding tenant_id is defensive
    # belt-and-braces.
    wave_ids: List[str] = [
        d["wave_id"]
        async for d in db["pm_waves"].find(
            {"project_id": project_id, "tenant_id": tenant_id},
            {"_id": 0, "wave_id": 1},
        )
    ]
    thread_ids: List[str] = []
    if wave_ids:
        thread_ids = [
            d["thread_id"]
            async for d in db["pm_threads"].find(
                {"wave_id": {"$in": wave_ids}, "tenant_id": tenant_id},
                {"_id": 0, "thread_id": 1},
            )
        ]

    cascade: Dict[str, int] = {}

    async with crud_envelope(
        module="projects",
        entity="project",
        action="deleted",
        resource_id=project_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        cascade["tasks"] = (
            await db["pm_tasks"].delete_many(
                {"project_id": project_id, "tenant_id": tenant_id}
            )
        ).deleted_count
        cascade["sprints"] = (
            await db["pm_sprints"].delete_many(
                {"project_id": project_id, "tenant_id": tenant_id}
            )
        ).deleted_count
        cascade["waves"] = (
            await db["pm_waves"].delete_many(
                {"project_id": project_id, "tenant_id": tenant_id}
            )
        ).deleted_count
        cascade["threads"] = (
            (
                await db["pm_threads"].delete_many(
                    {"wave_id": {"$in": wave_ids}, "tenant_id": tenant_id}
                )
            ).deleted_count
            if wave_ids
            else 0
        )
        cascade["blocker_tickets"] = (
            (
                await db["pm_blocker_tickets"].delete_many(
                    {"thread_id": {"$in": thread_ids}, "tenant_id": tenant_id}
                )
            ).deleted_count
            if thread_ids
            else 0
        )

        # Wave 4 / A5: audit trails outlive their subject. The
        # `pm_*_status_history` collections are intentionally NOT cascade-
        # deleted so forensic queries ("who closed project X?") still work
        # after the project is removed. The cascade scope is now:
        #   pm_tasks, pm_sprints, pm_waves, pm_threads, pm_blocker_tickets
        # — operational data only.

        await db["projects"].delete_one(
            {"project_id": project_id, "tenant_id": tenant_id}
        )
        ctx.set_after(None)

    return {"ok": True, "deleted_id": project_id, "cascade": cascade}
