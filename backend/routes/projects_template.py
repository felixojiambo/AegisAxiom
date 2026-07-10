"""
Projects module — Template CRUD.

Adapted from Davies-PMO-app `app/api/templates.py` for the AEGISAXIOM ingestion stack.
Collection: pm_templates. ID prefix: tmpl_.

Templates are reusable scaffolds for creating projects (waterfall phases, agile
columns, etc.). The `is_system` flag is server-controlled: it is always coerced
to False on create, dropped from update payloads, and prevents deletion when
True. System templates are seeded out-of-band.

Wholesale-replacement note: this file is part of the new Projects module that
replaces the legacy `backend/routes/projects.py` / `pm_projects.py` stack.
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime, timezone

from database import get_db
from models import gen_id
from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from services.ingestion.route_helpers import crud_envelope
from services.field_registry import register_fields, register_module_events, register_module_config
from services.projects_template_engine import (
    snapshot_project,
    hydrate_template,
    build_preview,
    PREVIEW_TASK_CAP,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Field registry — surfaces template fields to policy engine + AI schema layer.
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

PROJECT_TYPE_ENUM = {"waterfall", "agile", "waves_threads"}
SERVICE_LINE_ENUM = {"ucaas", "networking", "cloud", "security", "ai_product", "other"}


register_fields("projects", "template", [
    {"name": "template_id", "type": "string", "label": "Template ID", "description": "Primary key (tmpl_<uuid>)"},
    {"name": "name", "type": "string", "label": "Name", "description": "Template name"},
    {"name": "project_type", "type": "string", "label": "Project Type",
     "options": PROJECT_TYPE_OPTIONS,
     "description": "Methodology this template applies to"},
    {"name": "service_line", "type": "string", "label": "Service Line",
     "options": SERVICE_LINE_OPTIONS,
     "description": "Service line this template targets"},
    {"name": "task_definitions", "type": "list", "label": "Task Definitions",
     "description": "Array of task templates (title, description, estimated_duration_hours, etc.)"},
    {"name": "default_phases", "type": "list", "label": "Default Phases",
     "description": "Waterfall-only: array of {name, start_offset_days, end_offset_days}"},
    {"name": "default_columns", "type": "list", "label": "Default Columns",
     "description": "Agile-only: array of column names for the kanban board"},
    {"name": "default_sprints", "type": "list", "label": "Default Sprints",
     "description": "Agile-only: array of sprint scaffolds with day offsets from project start"},
    {"name": "default_waves", "type": "list", "label": "Default Waves",
     "description": "Waves+threads-only: array of wave scaffolds with day offsets + depends_on_indexes"},
    {"name": "description", "type": "string", "label": "Description"},
    {"name": "version", "type": "number", "label": "Version",
     "description": "Monotonic integer; bumped on every PATCH so instantiated projects can record a provenance snapshot"},
    {"name": "config", "type": "object", "label": "Config",
     "description": "Template-level config — currently {include_dates: bool, skip_weekends: bool}"},
    {"name": "is_system", "type": "boolean", "label": "Is System",
     "description": "Server-managed flag; system templates cannot be deleted and the value cannot be set by clients"},
    {"name": "created_at", "type": "datetime", "label": "Created At"},
    {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    {"name": "created_by", "type": "string", "label": "Created By", "description": "FK to users.user_id"},
])

register_module_config("projects", "pm_templates", "template_id", "Custom")
register_module_events("projects", "template", ["created", "updated", "deleted"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class TemplateColumn(BaseModel):
    """Agile kanban column shape. Wave 4 / A3 — frontend ColumnManager and
    TemplatesTab send `[{id, name}]`; the previous bare `List[str]` shape
    silently dropped column ids and broke the round-trip with TasksTab's
    Column dropdown."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class TemplateConfig(BaseModel):
    """Hydration-time options stored on a template. Only keys the engine
    actually reads are accepted — `extra='forbid'` catches typos at the
    API boundary instead of silently storing dead fields. Both flags
    documented at `services.projects_template_engine` module level."""

    model_config = ConfigDict(extra="forbid")

    include_dates: bool = False
    skip_weekends: bool = False


class TemplateCreate(BaseModel):
    """Body for POST /api/templates. `is_system` is always coerced to False
    server-side and is not accepted from clients (extra='forbid')."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    project_type: str
    service_line: str
    task_definitions: List[Dict[str, Any]] = Field(default_factory=list)
    default_phases: Optional[List[Dict[str, Any]]] = None
    default_columns: Optional[List[TemplateColumn]] = None
    # v1.3: agile sprint scaffold + waves+threads wave scaffold. Optional;
    # only honored when project_type matches.
    default_sprints: Optional[List[Dict[str, Any]]] = None
    default_waves: Optional[List[Dict[str, Any]]] = None
    config: Optional[TemplateConfig] = None


class TemplateUpdate(BaseModel):
    """Body for PATCH /api/templates/{id}. `is_system` and `version` cannot
    be modified by clients — version is server-managed (bumped on PATCH)."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    project_type: Optional[str] = None
    service_line: Optional[str] = None
    task_definitions: Optional[List[Dict[str, Any]]] = None
    default_phases: Optional[List[Dict[str, Any]]] = None
    default_columns: Optional[List[TemplateColumn]] = None
    default_sprints: Optional[List[Dict[str, Any]]] = None
    default_waves: Optional[List[Dict[str, Any]]] = None
    config: Optional[TemplateConfig] = None


# ---------------------------------------------------------------------------
# v1.3 — Save-current-project-as-template
# ---------------------------------------------------------------------------
class SaveAsTemplateBody(BaseModel):
    """Body for POST /projects/{project_id}/save-as-template.

    `scope` is reserved for v1.4 (user-private vs org-wide templates). For
    now we accept only `'org'` to avoid the silent information-disclosure
    risk of storing `'user'` but ignoring it at read-time: a PM who picks
    'Just me' would get no privacy. When v1.4 adds row-level filtering at
    the template-list endpoint, the literal can expand to `Literal['org',
    'user']`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    scope: Literal["org"] = "org"
    include_dates: bool = False


class TemplateInstantiateBody(BaseModel):
    """Body for POST /templates/{template_id}/instantiate.

    Smoother than the legacy /projects/from-template route: project_type +
    service_line + start_date come from the template itself, not the client.
    Frontend Wave 2D should prefer this endpoint over the legacy one.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    start_date: str
    pm_user_id: Optional[str] = None
    description: Optional[str] = None
    overrides: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_enums(project_type: Optional[str], service_line: Optional[str]) -> None:
    if project_type is not None and project_type not in PROJECT_TYPE_ENUM:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid project_type; must be one of {sorted(PROJECT_TYPE_ENUM)}",
        )
    if service_line is not None and service_line not in SERVICE_LINE_ENUM:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid service_line; must be one of {sorted(SERVICE_LINE_ENUM)}",
        )


# Phase 1E.3c — Template visibility helper.
#
# Templates have two flavours that coexist:
#
#   * **System templates** (`is_system=True`, no tenant_id) — the
#     read-only library seeded out-of-band. Visible to every tenant,
#     modifiable by none. These are how SimpliMeta ships methodology
#     defaults (Waterfall, Scrum, etc.) that every tenant gets for
#     free without having to recreate them.
#
#   * **Tenant templates** (`is_system=False`, has tenant_id) —
#     created by a tenant for their own use. Visible only to that
#     tenant, modifiable only by users in that tenant.
#
# Read paths use `_readable_template_query` to combine both. Write
# paths (update / delete) filter directly by tenant_id, which
# naturally excludes system templates (they have no tenant_id field
# to match) — so the existing `is_system` write guards become belt-
# and-braces rather than the sole protection.
def _readable_template_query(tenant_id: str) -> dict:
    """Mongo filter clause for templates visible to the current tenant.

    Returns a dict with an `$or` clause that matches either system
    templates or this tenant's own templates. Compose with other
    filters via dict-merge:

        query = {"project_type": "waterfall"}
        query.update(_readable_template_query(tenant_id))
    """
    return {"$or": [{"is_system": True}, {"tenant_id": tenant_id}]}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/templates",
    dependencies=[Depends(require_ai_permission("projects.template.view"))],
)
async def list_templates(
    project_type: Optional[str] = Query(None),
    service_line: Optional[str] = Query(None),
    is_system: Optional[bool] = Query(None),
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.template.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    query: dict = {}
    if project_type is not None:
        if project_type not in PROJECT_TYPE_ENUM:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid project_type; must be one of {sorted(PROJECT_TYPE_ENUM)}",
            )
        query["project_type"] = project_type
    if service_line is not None:
        if service_line not in SERVICE_LINE_ENUM:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid service_line; must be one of {sorted(SERVICE_LINE_ENUM)}",
            )
        query["service_line"] = service_line
    if is_system is not None:
        # Caller explicitly asked for system-only or tenant-only — honour
        # it, but still scope tenant templates to the current tenant.
        query["is_system"] = is_system
        if is_system is False:
            query["tenant_id"] = tenant_id
    else:
        # Default — show tenant's own templates + system templates.
        query.update(_readable_template_query(tenant_id))

    total = await db["pm_templates"].count_documents(query)
    items = (
        await db["pm_templates"]
        .find(query, {"_id": 0})
        .sort("name", 1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get(
    "/templates/{template_id}",
    dependencies=[Depends(require_ai_permission("projects.template.view"))],
)
async def get_template(
    template_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.template.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    # Readable templates = tenant's own + system templates.
    doc = await db["pm_templates"].find_one(
        {"template_id": template_id, **_readable_template_query(tenant_id)},
        {"_id": 0},
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return doc


@router.post(
    "/templates",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.template.create"))],
)
async def create_template(
    payload: TemplateCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.template.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    _validate_enums(payload.project_type, payload.service_line)

    body = payload.model_dump()
    # Always coerce is_system to False — clients cannot create system templates.
    body["is_system"] = False

    now = _now_iso()
    doc = {
        "template_id": gen_id("tmpl_"),
        # Phase 1E.3c — stamp the owning tenant. System templates have
        # no tenant_id (seeded out-of-band); client-created templates
        # always carry tenant_id and is_system=False.
        "tenant_id": tenant_id,
        **body,
        # v1.3: every template carries a monotonic version. Bumped on PATCH.
        # Projects instantiated from a template snapshot this value as
        # `source_template_version` for audit / provenance.
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects", entity="template", action="created",
        resource_id=doc["template_id"], current_user=current_user, db=db, request=request,
    ) as ctx:
        await db["pm_templates"].insert_one(doc)
        doc.pop("_id", None)
        ctx.set_after(doc)

    return doc


@router.patch(
    "/templates/{template_id}",
    dependencies=[Depends(require_ai_permission("projects.template.edit"))],
)
async def update_template(
    template_id: str,
    payload: TemplateUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.template.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Phase 1E.3c — write paths filter by tenant_id directly. This
    # naturally excludes system templates (which have no tenant_id)
    # so the existing `is_system` write guard becomes belt-and-braces
    # rather than the sole protection. Cross-tenant edit attempts get
    # the same 404 as missing rows — we don't leak existence.
    before = await db["pm_templates"].find_one(
        {"template_id": template_id, "tenant_id": tenant_id},
        {"_id": 0},
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Template not found")

    dumped = payload.model_dump(exclude_unset=True)
    # is_system is not in TemplateUpdate (extra='forbid' rejects it from clients),
    # but defensively drop it in case a future schema change adds it.
    dumped.pop("is_system", None)
    # Same defence for tenant_id — clients must not be able to reparent a
    # template via PATCH.
    dumped.pop("tenant_id", None)

    _validate_enums(dumped.get("project_type"), dumped.get("service_line"))

    if not dumped:
        # No-op update: still bump updated_at to record the touch.
        dumped = {}
    dumped["updated_at"] = _now_iso()

    # v1.3 versioning: every successful PATCH bumps version. Projects
    # instantiated from this template after the bump record the new value
    # as `source_template_version`, so audit can distinguish "instantiated
    # from v2" vs "instantiated from v3."
    current_version = int(before.get("version") or 1)
    dumped["version"] = current_version + 1

    async with crud_envelope(
        module="projects", entity="template", action="updated",
        resource_id=template_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_templates"].update_one(
            {"template_id": template_id, "tenant_id": tenant_id},
            {"$set": dumped},
        )
        updated = await db["pm_templates"].find_one(
            {"template_id": template_id, "tenant_id": tenant_id},
            {"_id": 0},
        )
        ctx.set_after(updated)

    return updated


@router.delete(
    "/templates/{template_id}",
    dependencies=[Depends(require_ai_permission("projects.template.delete"))],
)
async def delete_template(
    template_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.template.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Phase 1E.3c — tenant_id filter blocks system templates (no
    # tenant_id) AND cross-tenant deletes (different tenant_id). The
    # subsequent is_system check is belt-and-braces.
    before = await db["pm_templates"].find_one(
        {"template_id": template_id, "tenant_id": tenant_id},
        {"_id": 0},
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Template not found")

    if before.get("is_system") is True:
        raise HTTPException(status_code=409, detail="System templates cannot be deleted")

    # Wave 4 / A4: refuse delete if any project still references this
    # template via project.template_id. The project's `template_id` is a
    # nullable FK, so deleting a referenced template would silently leave
    # the field pointing at nothing. Scope the check to this tenant —
    # template_ids are unique across tenants so this is belt-and-braces
    # against an accidental cross-tenant FK.
    in_use = await db["projects"].count_documents(
        {"template_id": template_id, "tenant_id": tenant_id}, limit=1
    )
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot delete template — at least one project still references "
                "it via template_id. Detach those projects first or duplicate "
                "the template before delete."
            ),
        )

    async with crud_envelope(
        module="projects", entity="template", action="deleted",
        resource_id=template_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_templates"].delete_one(
            {"template_id": template_id, "tenant_id": tenant_id}
        )
        ctx.set_after(None)

    return {"ok": True, "deleted_id": template_id}


# ---------------------------------------------------------------------------
# v1.3 Theme D — Template instantiation polish
#
# Three new endpoints layered on top of the existing CRUD:
#   1. POST /projects/{project_id}/save-as-template — snapshot a live project
#   2. GET  /templates/{template_id}/preview         — dry-run before instantiate
#   3. POST /templates/{template_id}/instantiate     — smooth instantiation
#
# All snapshot / hydrate / preview logic lives in services.projects_template_engine
# as pure functions. These handlers do the Mongo I/O only.
# ---------------------------------------------------------------------------
@router.post(
    "/projects/{project_id}/save-as-template",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.template.create"))],
)
async def save_project_as_template(
    project_id: str,
    payload: SaveAsTemplateBody,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Snapshot a live project + its tasks/phases/sprints/waves into a new
    pm_templates document.

    Smoother than the alternative of having the PM manually re-enter every
    task — this is the "save current project as template" flow surfaced in
    the v1.3 template gallery UI.
    """
    if not has_perm(current_user, "projects.template.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Caller must also be able to *read* the source project. We don't require
    # full edit — viewing is sufficient to snapshot — but a user without
    # view perm shouldn't be able to derive a template from a project they
    # can't otherwise see.
    if not has_perm(current_user, "projects.project.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Phase 1E.3c — all reads scoped to the calling tenant. Cross-tenant
    # snapshot attempts get 404, not 403 — we don't leak existence.
    project = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    project_type = project.get("project_type")
    tasks = await db["pm_tasks"].find(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    ).to_list(None)

    phases: Optional[List[Dict[str, Any]]] = None
    sprints: Optional[List[Dict[str, Any]]] = None
    waves: Optional[List[Dict[str, Any]]] = None

    if project_type == "waterfall":
        phases = list(((project.get("methodology_config") or {}).get("phases")) or [])
    elif project_type == "agile":
        sprints = await db["pm_sprints"].find(
            {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
        ).to_list(None)
    elif project_type == "waves_threads":
        waves = await db["pm_waves"].find(
            {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
        ).to_list(None)

    try:
        snapshot = snapshot_project(
            project,
            tasks,
            phases=phases,
            sprints=sprints,
            waves=waves,
            include_dates=payload.include_dates,
            name=payload.name,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Scope is currently restricted to org-wide templates. Row-level
    # user-private filtering can be added safely when v1.4 expands this literal.
    snapshot["scope"] = payload.scope

    now = _now_iso()
    template_id = gen_id("tmpl_")
    doc = {
        "template_id": template_id,
        # Phase 1E.3c — stamp the owning tenant. Snapshots are always
        # tenant-owned (never system templates).
        "tenant_id": tenant_id,
        **snapshot,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects", entity="template", action="created",
        resource_id=template_id, current_user=current_user, db=db, request=request,
    ) as ctx:
        await db["pm_templates"].insert_one(doc)
        doc.pop("_id", None)
        ctx.set_after(doc)

    return {
        "template_id": template_id,
        "name": doc["name"],
        "task_count": len(snapshot.get("task_definitions") or []),
        "phase_count": len(snapshot.get("default_phases") or []),
        "sprint_count": len(snapshot.get("default_sprints") or []),
        "wave_count": len(snapshot.get("default_waves") or []),
    }


@router.get(
    "/templates/{template_id}/preview",
    dependencies=[Depends(require_ai_permission("projects.template.view"))],
)
async def preview_template(
    template_id: str,
    start_date: str = Query(..., description="ISO date anchor for offset resolution"),
    pm_user_id: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Dry-run preview of what `instantiate` would produce.

    Returns computed dates relative to `start_date`, capped task list (50
    rows by default), and any warnings the engine raised. Frontend renders
    this in a preview pane before the "Create from Template" button.
    """
    if not has_perm(current_user, "projects.template.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Phase 1E.3c — readable template = tenant-owned or system.
    template = await db["pm_templates"].find_one(
        {"template_id": template_id, **_readable_template_query(tenant_id)},
        {"_id": 0},
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    try:
        return build_preview(template, start_date=start_date, pm_user_id=pm_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/templates/{template_id}/instantiate",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.project.create"))],
)
async def instantiate_template(
    template_id: str,
    payload: TemplateInstantiateBody,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Create a project from a template, with methodology seeding +
    provenance + dependency rewiring.

    Differences vs. the v1.2 /projects/from-template route:
      * project_type / service_line come from the template, not the request
      * supports per-task overrides (skip, prefix titles, assignee mapping)
      * seeds sprints (agile) and waves (waves_threads) — not just phases
      * stamps source_template_id + source_template_version on the project

    The v1.2 endpoint is unchanged and remains available for backwards
    compatibility with the existing template UI.
    """
    if not has_perm(current_user, "projects.project.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Phase 1E.3c — readable template = tenant-owned or system. The
    # produced project (and all its tasks/sprints/waves) is always
    # stamped with the caller's tenant_id, regardless of whether the
    # source template was system or tenant-owned.
    template = await db["pm_templates"].find_one(
        {"template_id": template_id, **_readable_template_query(tenant_id)},
        {"_id": 0},
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    effective_pm = payload.pm_user_id or current_user.user_id
    # FK check on pm_user_id — must be a user in the caller's tenant.
    # Users not belonging to this tenant cannot be assigned as PM, even
    # if their user_id happens to be valid in another tenant.
    if not await db["users"].find_one(
        {"user_id": effective_pm, "tenant_id": tenant_id}, {"_id": 1}
    ):
        raise HTTPException(
            status_code=422,
            detail=f"pm_user_id {effective_pm} does not match any user in this tenant",
        )

    try:
        blueprint = hydrate_template(
            template,
            start_date=payload.start_date,
            pm_user_id=effective_pm,
            name=payload.name,
            description=payload.description,
            overrides=payload.overrides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Local imports to avoid the import cycle that would happen if
    # projects_project imported this module at top level — and to reuse the
    # canonical task-doc builder + methodology validator.
    from routes.projects_project import _build_task_doc  # type: ignore
    from services.projects_methodology import validate_project_methodology_config

    project_bp = blueprint["project"]
    project_bp["methodology_config"] = validate_project_methodology_config(
        project_bp["project_type"], project_bp.get("methodology_config") or {}
    )

    now_iso = _now_iso()
    project_id = gen_id("proj_")
    project_doc: Dict[str, Any] = {
        "project_id": project_id,
        # Phase 1E.3c — stamp tenant_id on the produced project.
        "tenant_id": tenant_id,
        **project_bp,
        "template_id": template_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects", entity="project", action="created",
        resource_id=project_id, current_user=current_user, db=db, request=request,
    ) as ctx:
        await db["projects"].insert_one(project_doc)

        # ---- Tasks: two-pass insert so dependencies + parent_task_id can
        # be rewired from template_task_id to real task_id.
        template_id_to_task_id: Dict[str, str] = {}
        prepared_tasks: List[Dict[str, Any]] = []
        for tbp in blueprint["tasks"]:
            # Strip engine-only keys; carry the rest to _build_task_doc.
            payload_dict = {k: v for k, v in tbp.items()
                            if k not in ("template_task_id", "_parent_template_task_id", "dependencies")}
            # _build_task_doc (in projects_project.py) now accepts a
            # tenant_id parameter as of 1E.3c-projects-project. Pass
            # the calling tenant so the returned task doc carries
            # tenant_id and the counter allocation is tenant-scoped.
            task_doc = await _build_task_doc(
                db,
                project_doc,
                payload_dict,
                current_user_id=current_user.user_id,
                tenant_id=tenant_id,
                validate=True,
            )
            template_id_to_task_id[tbp["template_task_id"]] = task_doc["task_id"]
            prepared_tasks.append({**task_doc,
                                   "_template_task_id": tbp["template_task_id"],
                                   "_dep_ttids": list(tbp.get("dependencies") or []),
                                   "_parent_ttid": tbp.get("_parent_template_task_id")})

        # Rewire deps + parent ids now that we know all real task_ids.
        for t in prepared_tasks:
            t["dependencies"] = [
                template_id_to_task_id[d]
                for d in t.pop("_dep_ttids") or []
                if d in template_id_to_task_id
            ]
            parent_ttid = t.pop("_parent_ttid", None)
            if parent_ttid and parent_ttid in template_id_to_task_id:
                t["parent_task_id"] = template_id_to_task_id[parent_ttid]
            t.pop("_template_task_id", None)
        if prepared_tasks:
            await db["pm_tasks"].insert_many(prepared_tasks)

        # ---- Sprints (agile only).
        sprint_ids: List[str] = []
        for sbp in blueprint["sprints"]:
            sprint_doc = {
                "sprint_id": gen_id("sprint_"),
                # Phase 1E.3c — stamp tenant_id on every produced row.
                "tenant_id": tenant_id,
                "project_id": project_id,
                "name": sbp.get("name") or "Sprint",
                "start_date": sbp.get("start_date"),
                "end_date": sbp.get("end_date"),
                "goal": sbp.get("goal"),
                "capacity_points": sbp.get("capacity_points"),
                "completed_points": None,
                "status": sbp.get("status") or "planned",
                "created_at": now_iso,
                "updated_at": now_iso,
                "created_by": current_user.user_id,
            }
            await db["pm_sprints"].insert_one(sprint_doc)
            sprint_ids.append(sprint_doc["sprint_id"])

        # ---- Waves (waves_threads only). Two-pass like tasks: insert,
        # then rewrite depends_on_indexes → wave_ids.
        prepared_waves: List[Dict[str, Any]] = []
        for wbp in blueprint["waves"]:
            wave_doc = {
                "wave_id": gen_id("wave_"),
                # Phase 1E.3c — stamp tenant_id on every produced row.
                "tenant_id": tenant_id,
                "project_id": project_id,
                "goal": wbp.get("goal"),
                "start_date": wbp.get("start_date"),
                "end_date": wbp.get("end_date"),
                "depends_on_wave_ids": [],
                "postmortem": None,
                "status": wbp.get("status") or "planning",
                "created_at": now_iso,
                "updated_at": now_iso,
                "created_by": current_user.user_id,
            }
            prepared_waves.append({**wave_doc, "_depends_on_indexes": list(wbp.get("_depends_on_indexes") or [])})
        for i, w in enumerate(prepared_waves):
            w["depends_on_wave_ids"] = [
                prepared_waves[idx]["wave_id"]
                for idx in w.pop("_depends_on_indexes") or []
                if 0 <= idx < len(prepared_waves) and idx != i
            ]
        if prepared_waves:
            await db["pm_waves"].insert_many(prepared_waves)

        ctx.set_after({
            "project_id": project_id,
            "name": project_doc["name"],
            "task_count": len(prepared_tasks),
            "sprint_count": len(sprint_ids),
            "wave_count": len(prepared_waves),
            "source_template_id": template_id,
            "source_template_version": project_doc.get("source_template_version"),
        })

    return {
        "project_id": project_id,
        "task_count_created": len(prepared_tasks),
        "sprint_count_created": len(sprint_ids),
        "wave_count_created": len(prepared_waves),
        "source_template_id": template_id,
        "source_template_version": project_doc.get("source_template_version"),
        "summary": (
            f"Created project '{payload.name}' from template "
            f"'{template.get('name')}' v{project_doc.get('source_template_version')} — "
            f"{len(prepared_tasks)} tasks, {len(prepared_waves)} waves, "
            f"{len(sprint_ids)} sprints."
        ),
    }