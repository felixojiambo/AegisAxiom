"""Projects ingestion — Task entity routes.

Adapted from Davies-PMO-app `backend/app/api/tasks.py` and `services/task_deps.py`.
Differences from Davies:
  * Mongo ObjectIds replaced with AEGISAXIOM string IDs (`gen_id("task_")`).
  * Tasks live in `db["pm_tasks"]` (mapping rule: PK collection is `projects`,
    every other collection in this module gets the `pm_` prefix).
  * Per-project `task_number` sequence persists in `db["pm_counters"]` keyed
    by `project:<project_id>:task`. The pm_-prefixed namespace keeps Projects
    counter docs separate from the global `db.counters` collection (which
    other modules like chat.py use with simpler `_id` keys).
  * State changes route through `crud_envelope`; status changes audit to
    `pm_task_status_history`.
  * `methodology_fields` validation is inlined here (Thread 1E will eventually
    own a shared helper at `services.projects_methodology`; until then the
    rules live with the route to keep the router self-contained).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

from sqlalchemy.ext.asyncio import AsyncSession

from auth_helper import get_current_user, has_perm
from database import get_db
from db.session import get_session
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from repositories.users import UserRepository
from models import gen_id
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope
from services.projects_methodology import (
    DONE_STATUSES,
    WATERFALL_STATUSES,
    WAVES_THREADS_STATUSES,
    validate_task_methodology_fields,
)
from services.projects_realtime_bus import project_topic, realtime_bus

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Realtime emission helper (v1.3 Wave 1 / Thread 1A)
# ---------------------------------------------------------------------------
async def _emit_task_event(
    *,
    event_type: str,                # "task.created" / ".updated" / ".deleted" / ".status_changed" / ".assigned"
    task_doc: Optional[Dict[str, Any]],
    project_id: str,
    entity_id: str,
    current_user,
) -> None:
    """Publish a task mutation to the project's realtime topic.

    The same task doc the API just returned is carried in `payload` so the
    SSE consumer can merge optimistically without a follow-up GET. For
    delete events `task_doc` is None and `payload` is the empty dict —
    the consumer keys off `type` to drop the row.

    actor_type: defaults to "user". The AI write tools override this in
    their own emission path (Thread 1B owns those tools and will wrap the
    same `_emit_task_event` helper with `actor_type="pmo_ai"`).

    Never raises: a bus publish failure must not poison a successful
    Mongo write. We log and swallow.
    """
    try:
        await realtime_bus.publish(
            project_topic(project_id),
            {
                "type": event_type,
                "entity_type": "task",
                "entity_id": entity_id,
                "project_id": project_id,
                "actor_user_id": getattr(current_user, "user_id", None),
                "actor_type": "user",
                "at": _utc_now_iso(),
                "payload": task_doc or {},
            },
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "realtime_bus publish failed event=%s task=%s project=%s",
            event_type, entity_id, project_id,
        )


# ---------------------------------------------------------------------------
# Constants. WATERFALL_STATUSES / WAVES_THREADS_STATUSES / DONE_STATUSES are
# canonical in services.projects_methodology and re-exported above so other
# modules can import either path. Local-only constants stay here.
# ---------------------------------------------------------------------------
PRIORITIES: Set[str] = {"low", "medium", "high", "critical"}


# ---------------------------------------------------------------------------
# Field / event / module registration
# ---------------------------------------------------------------------------
register_fields(
    "projects",
    "task",
    [
        {"name": "project_id", "type": "string", "label": "Project ID"},
        {"name": "title", "type": "string", "label": "Title"},
        {"name": "description", "type": "string", "label": "Description"},
        {"name": "assignee_id", "type": "string", "label": "Assignee (user_id)"},
        {
            "name": "status",
            "type": "string",
            "label": "Status",
            "description": "Enum varies by methodology — waterfall/waves_threads use fixed sets, agile = column name",
        },
        {
            "name": "priority",
            "type": "string",
            "label": "Priority",
            "options": [
                {"value": "low", "label": "Low"},
                {"value": "medium", "label": "Medium"},
                {"value": "high", "label": "High"},
                {"value": "critical", "label": "Critical"},
            ],
        },
        {
            "name": "estimated_duration_hours",
            "type": "number",
            "label": "Estimated Duration (hours)",
        },
        {"name": "parent_task_id", "type": "string", "label": "Parent Task ID"},
        {"name": "start_date", "type": "date", "label": "Start Date"},
        {"name": "due_date", "type": "date", "label": "Due Date"},
        {"name": "dependencies", "type": "list", "label": "Dependencies"},
        {"name": "methodology_fields", "type": "object", "label": "Methodology Fields"},
        {"name": "labels", "type": "list", "label": "Labels"},
        {
            "name": "task_number",
            "type": "number",
            "label": "Task Number",
            "description": "Per-project sequential number assigned at create",
        },
        {"name": "sort_index", "type": "number", "label": "Sort Index"},
    ],
)
register_module_config("projects", "pm_tasks", "task_id", "Custom")
register_module_events(
    "projects",
    "task",
    ["created", "updated", "deleted", "status_changed", "assigned"],
)


# ---------------------------------------------------------------------------
# Pydantic DTOs
# ---------------------------------------------------------------------------
class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    title: str = Field(min_length=1)
    description: Optional[str] = None
    assignee_id: Optional[str] = None
    status: Optional[str] = None  # defaulted/derived by methodology
    priority: str = "medium"
    estimated_duration_hours: Optional[float] = Field(default=None, ge=0)
    parent_task_id: Optional[str] = None
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)
    methodology_fields: Dict[str, Any] = Field(default_factory=dict)
    labels: List[str] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    assignee_id: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    estimated_duration_hours: Optional[float] = Field(default=None, ge=0)
    parent_task_id: Optional[str] = None
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    methodology_fields: Optional[Dict[str, Any]] = None
    labels: Optional[List[str]] = None
    sort_index: Optional[int] = None


class TaskDependencyAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depends_on_task_id: str


class TaskReorderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    column_id: str
    ordered_task_ids: List[str] = Field(min_length=1)


# Wave 6 / Thread A / Contract 2: bulk task ops payload models.
# `BulkTaskCreateItem` mirrors TaskCreate but without project_id (the parent
# bulk request carries one project_id and applies it to every row); rows that
# include their own project_id are rejected to keep the validator narrow.
class BulkTaskCreateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: Optional[str] = None
    assignee_id: Optional[str] = None
    status: Optional[str] = None
    priority: str = "medium"
    estimated_duration_hours: Optional[float] = Field(default=None, ge=0)
    parent_task_id: Optional[str] = None
    start_date: Optional[str] = None
    due_date: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)
    methodology_fields: Dict[str, Any] = Field(default_factory=dict)
    labels: List[str] = Field(default_factory=list)


class BulkTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    tasks: List[BulkTaskCreateItem] = Field(min_length=1, max_length=200)
    on_error: Literal["fail_fast", "skip", "report"] = "report"


class BulkTaskAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ids: List[str] = Field(min_length=1, max_length=500)
    assignee_id: Optional[str] = None  # None = unassign


class BulkTaskStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ids: List[str] = Field(min_length=1, max_length=500)
    status: str = Field(min_length=1)


class BulkTaskSprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ids: List[str] = Field(min_length=1, max_length=500)
    # sprint_id None == move to backlog (clear sprint assignment).
    sprint_id: Optional[str] = None


# ---------------------------------------------------------------------------
# v1.2 / Theme B bulk endpoint response shape
#
# All bulk mutation endpoints (reorder, bulk-assign, bulk-status,
# bulk-sprint) return this envelope. Mirrors the single-task PATCH shape so
# the frontend can feed `updated[]` directly into the `onTaskMutated`
# optimistic-merge reducer instead of falling back to a full refetch.
#
# Error codes used in `failed[]`:
#   * "not_found"        — task_id doesn't exist
#   * "forbidden"        — caller lacks per-row RBAC (e.g., task is in a
#                          project they can't edit). The top-level endpoint
#                          perm check is separate; this is per-row scoping
#                          so a 50-task bulk with 1 unauthorized row still
#                          updates the other 49.
#   * "project_frozen"   — task's project is closed/on_hold
#   * "invalid_status"   — status rejected by methodology validator
#   * "invalid_assignee" — assignee_id does not match any user (top-level)
#   * "invalid_sprint"   — sprint_id provided but doesn't exist or is in a
#                          different project than the task
#   * "validation"       — generic 422 from the validator with detail
#   * "unexpected"       — defensive catch-all for 500-class errors
# ---------------------------------------------------------------------------
_BULK_ERR_NOT_FOUND = "not_found"
_BULK_ERR_FORBIDDEN = "forbidden"
_BULK_ERR_PROJECT_FROZEN = "project_frozen"
_BULK_ERR_INVALID_STATUS = "invalid_status"
_BULK_ERR_INVALID_ASSIGNEE = "invalid_assignee"
_BULK_ERR_INVALID_SPRINT = "invalid_sprint"
_BULK_ERR_VALIDATION = "validation"
_BULK_ERR_UNEXPECTED = "unexpected"


def _bulk_response(updated: List[Dict[str, Any]], failed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the canonical bulk response envelope.

    Shape:
        {
          "updated": [<full task doc>, ...],
          "failed":  [{"task_id": "...", "error": "...", "code": "..."}, ...],
          "summary": {"updated_count": int, "failed_count": int}
        }
    """
    return {
        "updated": updated,
        "failed": failed,
        "summary": {
            "updated_count": len(updated),
            "failed_count": len(failed),
        },
    }


def _bulk_failure(task_id: str, error: Any, code: str) -> Dict[str, Any]:
    """Build a `failed[]` entry. `error` is coerced to string so the shape
    is unambiguous for the frontend parser and the LLM narrating bulk ops.
    """
    if isinstance(error, (dict, list)):
        # Carry structured detail through as a string so the frontend's
        # extractTaskErrorMessage parser (which expects string detail) works
        # uniformly. Callers should pick a `code` that explains the category.
        err_str = str(error)
    else:
        err_str = str(error)
    return {"task_id": task_id, "error": err_str, "code": code}


def _caller_can_edit_task(current_user, project: Dict[str, Any]) -> bool:
    """Per-row RBAC: can this user edit a task in this project?

    Mirrors the PATCH /tasks/{id} gate — global `projects.task.edit` OR
    PM of this specific project with `projects.project.view`. Used by bulk
    endpoints to demote a single unauthorized row into `failed[]` rather
    than 403'ing the entire batch.
    """
    if has_perm(current_user, "projects.task.edit"):
        return True
    pm_id = project.get("pm_id")
    if (
        pm_id
        and pm_id == getattr(current_user, "user_id", None)
        and has_perm(current_user, "projects.project.view")
    ):
        return True
    return False


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


def _counter_key(project_id: str) -> str:
    return f"project:{project_id}:task"


async def _next_task_number(db: Any, project_id: str, tenant_id: str) -> int:
    """Atomically increment the per-project task_number sequence.

    Counter doc lives in `db.pm_counters` (NOT the global `db.counters`)
    keyed by `project:{project_id}:task` so Projects' counter docs cannot
    collide with chat.py's `chat_number` doc or any other module's counter.

    Phase 1E.3c — counter docs carry tenant_id. The counter key (project_id)
    is already unique per tenant since project_ids don't cross tenants,
    so adding tenant_id to the filter is defensive belt-and-braces.
    """
    result = await db["pm_counters"].find_one_and_update(
        {"_id": _counter_key(project_id), "tenant_id": tenant_id},
        {"$inc": {"seq": 1}, "$setOnInsert": {"tenant_id": tenant_id}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(result["seq"])


# task_number is assigned at create time via _next_task_number — there is no
# legacy data to backfill in the AEGISAXIOM ingestion DB. If a future migration ever
# imports legacy tasks without task_number, run a one-shot script (NOT a
# read-time backfill) — see backend/seeds/seed_task_numbers.py for the
# template.


# Methodology fields validation is delegated to services.projects_methodology
# (canonical, well-tested module). Imported above as
# `validate_task_methodology_fields`.


async def _load_project_for_task(
    db: Any, project_id: str, tenant_id: str
) -> Dict[str, Any]:
    """Load a project for task FK validation, scoped to the caller's tenant.

    Phase 1E.3c — cross-tenant project_id references return 422 "does not
    match" rather than leaking existence. A tenant's tasks can never
    reference another tenant's projects.
    """
    project = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if project is None:
        raise HTTPException(
            status_code=422,
            detail=f"project_id {project_id} does not match any project",
        )
    return project


_FROZEN_PROJECT_STATUSES = {"closed", "on_hold"}


def _refuse_if_project_frozen(project: Dict[str, Any], action: str = "create") -> None:
    """Wave 4 / A4 state guard: reject create/edit on closed or on_hold
    projects. Update paths can be more permissive (closing-out edits) — pass
    action='create' for hard rejection on creates."""
    status = (project or {}).get("status")
    if status in _FROZEN_PROJECT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is {status}. Cannot {action} new tasks/waves/threads/"
                "blockers. Reopen the project first."
            ),
        )


async def _creates_cycle(
    db: Any, from_id: str, to_id: str, tenant_id: str
) -> bool:
    """Adding edge from_id -> to_id creates a cycle iff there's already a path
    from to_id back to from_id via `dependencies`.

    Phase 1E.3c — the graph walk is tenant-scoped. Cross-tenant edges
    couldn't exist post-1E.3c anyway (dependencies are validated in
    `_validate_dependency` to be within the same project), but adding
    tenant_id to the lookup is defensive belt-and-braces.
    """
    visited: Set[str] = set()
    stack: List[str] = [to_id]
    while stack:
        cur = stack.pop()
        if cur == from_id:
            return True
        if cur in visited:
            continue
        visited.add(cur)
        doc = await db["pm_tasks"].find_one(
            {"task_id": cur, "tenant_id": tenant_id}, {"_id": 0, "dependencies": 1}
        )
        if not doc:
            continue
        for dep in doc.get("dependencies", []) or []:
            if dep not in visited:
                stack.append(dep)
    return False


async def _validate_dependency(
    db: Any, from_task: Dict[str, Any], to_task_id: str, tenant_id: str
) -> Dict[str, Any]:
    """Validate adding a dependency: same project, no self, no cycles, no
    same-wave (waves_threads). Returns the target task dict.

    Phase 1E.3c — every cross-collection lookup is tenant-scoped. The
    "same project" rule combined with tenant-scoped project IDs means
    cross-tenant dependencies are impossible by construction, but the
    explicit filter is defensive.
    """
    if from_task["task_id"] == to_task_id:
        raise HTTPException(status_code=422, detail="A task cannot depend on itself")
    to_task = await db["pm_tasks"].find_one(
        {"task_id": to_task_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if to_task is None:
        raise HTTPException(status_code=404, detail="Dependency target task not found")
    if from_task["project_id"] != to_task["project_id"]:
        raise HTTPException(
            status_code=422,
            detail=(
                "Dependencies must be within the same project (cross-project / "
                "cross-methodology dependencies are not allowed)"
            ),
        )
    project = await db["projects"].find_one(
        {"project_id": from_task["project_id"], "tenant_id": tenant_id},
        {"_id": 0, "project_type": 1},
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.get("project_type") == "waves_threads":
        from_wave = (from_task.get("methodology_fields") or {}).get("wave_id")
        to_wave = (to_task.get("methodology_fields") or {}).get("wave_id")
        if from_wave and to_wave and from_wave == to_wave:
            raise HTTPException(
                status_code=422, detail="Same-wave task dependencies are not allowed"
            )
    if await _creates_cycle(db, from_task["task_id"], to_task_id, tenant_id):
        raise HTTPException(
            status_code=422, detail="Adding this dependency would create a cycle"
        )
    return to_task


# ---------------------------------------------------------------------------
# Reorder (declared first so /tasks/reorder doesn't get shadowed by /tasks/{task_id})
# ---------------------------------------------------------------------------
@router.post(
    "/tasks/reorder",
    dependencies=[Depends(require_ai_permission("projects.task.edit"))],
)
async def reorder_column(
    body: TaskReorderBody,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Reorder tasks within a (project_id, column_id) lane.

    v1.2: returns the standard bulk envelope (`updated[]` of full task docs,
    `failed[]`, `summary`) so the frontend can merge results into local
    state via `onTaskMutated` without a refetch. The single pre-flight
    membership check still 422s the whole batch if any id doesn't belong
    to the lane — reorder is an all-or-nothing operation by design.
    """
    if not has_perm(current_user, "projects.task.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    unique_ids = list(dict.fromkeys(body.ordered_task_ids))
    count = await db["pm_tasks"].count_documents(
        {
            "task_id": {"$in": unique_ids},
            "tenant_id": tenant_id,
            "project_id": body.project_id,
            "methodology_fields.column_id": body.column_id,
        }
    )
    if count != len(unique_ids):
        raise HTTPException(
            status_code=422,
            detail="All ordered_task_ids must belong to (project_id, column_id)",
        )
    now_iso = _utc_now_iso()
    updated: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for i, tid in enumerate(body.ordered_task_ids):
        try:
            await db["pm_tasks"].update_one(
                {"task_id": tid, "tenant_id": tenant_id},
                {"$set": {"sort_index": i, "updated_at": now_iso}},
            )
            after = await db["pm_tasks"].find_one(
                {"task_id": tid, "tenant_id": tenant_id}, {"_id": 0}
            )
            if after is None:
                # Race: task vanished between the membership check and the
                # write. Demote to a per-row failure so the rest of the
                # reorder still lands client-side.
                failed.append(
                    _bulk_failure(tid, "Task not found after update", _BULK_ERR_NOT_FOUND)
                )
                continue
            updated.append(after)
            await _emit_task_event(
                event_type="task.updated",
                task_doc=after,
                project_id=body.project_id,
                entity_id=tid,
                current_user=current_user,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("reorder_column task_id=%s unexpected", tid)
            failed.append(_bulk_failure(tid, f"Unexpected: {exc}", _BULK_ERR_UNEXPECTED))

    return _bulk_response(updated, failed)


# ---------------------------------------------------------------------------
# Bulk task ops (Wave 6 / Thread A / Contract 2)
#
# Declared BEFORE /tasks/{task_id} so the literal `/tasks/bulk*` paths
# don't get captured by the {task_id} param matcher. Three endpoints:
#   * POST  /tasks/bulk          — create N tasks in one project
#   * PATCH /tasks/bulk-assign   — reassign N tasks to one user (or unassign)
#   * PATCH /tasks/bulk-status   — bump N tasks to one status
#
# Per-task envelope only — no parent envelope. Each row's success/failure
# stands alone so a partial failure doesn't poison the audit trail with a
# single rolled-up event. The bulk endpoint returns a row-level error
# report that the frontend renders next to the offending row.
# ---------------------------------------------------------------------------
@router.post(
    "/tasks/bulk",
    status_code=200,
    dependencies=[Depends(require_ai_permission("projects.task.create"))],
)
async def bulk_create_tasks(
    body: BulkTaskCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    """Create up to 200 tasks in one project. Each row is validated through
    the canonical methodology validator (`validate_task_methodology_fields`
    via `_build_task_doc`) plus the same assignee / parent / dependency /
    date-ordering checks as the single-task create.

    on_error semantics:
        * `fail_fast` — first row error aborts the whole batch (no rows
          inserted, returns 422 with the failing row's index + error)
        * `skip` — drop failing rows from the response (returns only
          `created`; `failed` is empty even when rows errored)
        * `report` (default) — return both `created` and `failed`; HTTP
          200 either way so the frontend can render row-level errors

    Returns:
        {created: [task_doc, ...], failed: [{index, error}, ...]}
    """
    if not has_perm(current_user, "projects.task.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Lazy import to avoid a hard import-time cycle (projects_project also
    # lives in this package). _build_task_doc was factored in Wave 4 A2 as
    # the single source of truth for task doc shape — we reuse it here so
    # bulk-created tasks match template-seeded tasks byte-for-byte.
    from routes.projects_project import _build_task_doc  # noqa: PLC0415

    project = await _load_project_for_task(db, body.project_id, tenant_id)
    _refuse_if_project_frozen(project, action="create")

    created: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    # Cache user / parent existence checks across rows in the batch so a
    # 50-row paste against the same assignee doesn't fan out to 50 user
    # find_one calls.
    seen_users: Dict[Optional[str], bool] = {}
    seen_parents: Dict[str, bool] = {}

    for idx, row in enumerate(body.tasks):
        try:
            _validate_enum(row.priority, PRIORITIES, "priority")

            # Assignee FK check (cached per batch). Phase 1E.3c —
            # assignee must belong to the calling tenant.
            if row.assignee_id is not None:
                if row.assignee_id not in seen_users:
                    seen_users[row.assignee_id] = await UserRepository(
                        pg
                    ).user_exists_in_tenant(row.assignee_id, tenant_id)
                if not seen_users[row.assignee_id]:
                    raise HTTPException(
                        status_code=422,
                        detail=f"assignee_id {row.assignee_id} does not match any user",
                    )

            # Parent task FK + same-project check (cached per batch).
            if row.parent_task_id is not None:
                if row.parent_task_id not in seen_parents:
                    parent = await db["pm_tasks"].find_one(
                        {"task_id": row.parent_task_id, "tenant_id": tenant_id},
                        {"_id": 0, "project_id": 1},
                    )
                    seen_parents[row.parent_task_id] = (
                        parent is not None
                        and parent.get("project_id") == body.project_id
                    )
                if not seen_parents[row.parent_task_id]:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"parent_task_id {row.parent_task_id} not found "
                            "or not in same project"
                        ),
                    )

            # Date ordering.
            if (
                row.start_date
                and row.due_date
                and row.start_date > row.due_date
            ):
                raise HTTPException(
                    status_code=422,
                    detail="start_date must be on or before due_date",
                )

            # Dependencies — must exist + same project; cycle impossible
            # because the new task has no id yet.
            dep_ids = list(dict.fromkeys(row.dependencies))
            for dep_id in dep_ids:
                dep = await db["pm_tasks"].find_one(
                    {"task_id": dep_id, "tenant_id": tenant_id}, {"_id": 0}
                )
                if dep is None or dep.get("project_id") != body.project_id:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"dependency {dep_id} not found or not in same project"
                        ),
                    )
                if project["project_type"] == "waves_threads":
                    incoming_wave = (row.methodology_fields or {}).get("wave_id")
                    to_wave = (dep.get("methodology_fields") or {}).get("wave_id")
                    if incoming_wave and to_wave and incoming_wave == to_wave:
                        raise HTTPException(
                            status_code=422,
                            detail="Same-wave task dependencies are not allowed",
                        )

            payload_dict = row.model_dump()
            payload_dict["project_id"] = body.project_id
            payload_dict["dependencies"] = dep_ids
            doc = await _build_task_doc(
                db,
                project,
                payload_dict,
                current_user_id=current_user.user_id,
                tenant_id=tenant_id,
                validate=True,
            )
            task_id = doc["task_id"]

            async with crud_envelope(
                module="projects",
                entity="task",
                action="created",
                resource_id=task_id,
                current_user=current_user,
                db=db,
                request=request,
            ) as ctx:
                await db["pm_tasks"].insert_one(doc)
                await db["pm_task_status_history"].insert_one(
                    {
                        # Phase 1E.3c — status history is append-only audit;
                        # stamp tenant_id so per-tenant queries can scope.
                        "tenant_id": tenant_id,
                        "task_id": task_id,
                        "project_id": body.project_id,
                        "from_status": None,
                        "to_status": doc["status"],
                        "changed_at": _utc_now(),
                        "changed_by": current_user.user_id,
                    }
                )
                ctx.set_after(_scrub(doc))

            scrubbed = _scrub(doc)
            created.append(scrubbed)
            await _emit_task_event(
                event_type="task.created",
                task_doc=scrubbed,
                project_id=body.project_id,
                entity_id=task_id,
                current_user=current_user,
            )
        except HTTPException as exc:
            # v1.2: detail-as-string for the fail_fast HTTPException so the
            # frontend's `extractTaskErrorMessage` parser (which expects a
            # string detail) works uniformly. Row index is prefixed into
            # the string and also echoed in `failed[]` for `report` mode.
            err = {"index": idx, "error": exc.detail}
            if body.on_error == "fail_fast":
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=f"row {idx}: {exc.detail}",
                )
            if body.on_error == "skip":
                continue
            failed.append(err)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("bulk_create_tasks row %d unexpected error", idx)
            err = {"index": idx, "error": f"Unexpected: {exc}"}
            if body.on_error == "fail_fast":
                raise HTTPException(
                    status_code=500, detail=f"row {idx}: Unexpected: {exc}"
                )
            if body.on_error == "skip":
                continue
            failed.append(err)

    return {"created": created, "failed": failed}


@router.patch(
    "/tasks/bulk-assign",
    dependencies=[Depends(require_ai_permission("projects.task.assign"))],
)
async def bulk_assign_tasks(
    body: BulkTaskAssign,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    """Reassign N tasks to a single user (or unassign with assignee_id=null).

    RBAC: top-level gate is `projects.task.assign`. Each row also runs a
    per-project edit check via `_caller_can_edit_task` so a bulk request
    that spans projects (some the caller can edit, some they can't)
    updates the authorized rows and demotes the rest into `failed[]` with
    `code="forbidden"`. Tasks where `assignee_id` is already the target
    are silent no-ops (NOT counted as failures or as updates).

    v1.2: returns the standard bulk envelope so the frontend can merge
    full task docs into local state without a refetch.
    """
    if not has_perm(current_user, "projects.task.assign"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    if body.assignee_id is not None:
        # Phase 1E.3c — assignee must belong to the calling tenant.
        if not await UserRepository(pg).user_exists_in_tenant(
            body.assignee_id, tenant_id
        ):
            raise HTTPException(
                status_code=422,
                detail=f"assignee_id {body.assignee_id} does not match any user",
            )

    updated: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    unique_task_ids = list(dict.fromkeys(body.task_ids))
    project_cache: Dict[str, Dict[str, Any]] = {}

    for tid in unique_task_ids:
        try:
            before = await db["pm_tasks"].find_one(
                {"task_id": tid, "tenant_id": tenant_id}, {"_id": 0}
            )
            if before is None:
                failed.append(_bulk_failure(tid, "Task not found", _BULK_ERR_NOT_FOUND))
                continue
            if before.get("assignee_id") == body.assignee_id:
                # No-op — already assigned to that user (or already unassigned).
                continue

            # Per-row RBAC scoping. Bulk-assign carries the project's edit
            # gate as well as the global assign gate so an attacker can't
            # use the bulk endpoint to assign tasks in projects they can't
            # otherwise edit.
            project_id = before["project_id"]
            if project_id not in project_cache:
                proj = await db["projects"].find_one(
                    {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
                )
                project_cache[project_id] = proj or {}
            project = project_cache[project_id]
            if not project:
                failed.append(
                    _bulk_failure(tid, "Parent project not found", _BULK_ERR_NOT_FOUND)
                )
                continue
            if not _caller_can_edit_task(current_user, project):
                failed.append(
                    _bulk_failure(
                        tid,
                        "Caller cannot edit tasks in this project",
                        _BULK_ERR_FORBIDDEN,
                    )
                )
                continue

            async with crud_envelope(
                module="projects",
                entity="task",
                action="assigned",
                resource_id=tid,
                current_user=current_user,
                db=db,
                before=before,
                request=request,
            ) as ctx:
                await db["pm_tasks"].update_one(
                    {"task_id": tid, "tenant_id": tenant_id},
                    {
                        "$set": {
                            "assignee_id": body.assignee_id,
                            "updated_at": _utc_now_iso(),
                        }
                    },
                )
                after = await db["pm_tasks"].find_one(
                    {"task_id": tid, "tenant_id": tenant_id}, {"_id": 0}
                )
                ctx.set_after(after)
            if after is not None:
                updated.append(after)
                await _emit_task_event(
                    event_type="task.assigned",
                    task_doc=after,
                    project_id=project_id,
                    entity_id=tid,
                    current_user=current_user,
                )
        except HTTPException as exc:
            failed.append(_bulk_failure(tid, exc.detail, _BULK_ERR_VALIDATION))
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("bulk_assign_tasks task_id=%s unexpected", tid)
            failed.append(_bulk_failure(tid, f"Unexpected: {exc}", _BULK_ERR_UNEXPECTED))

    return _bulk_response(updated, failed)


@router.patch(
    "/tasks/bulk-status",
    dependencies=[Depends(require_ai_permission("projects.task.change_status"))],
)
async def bulk_update_status(
    body: BulkTaskStatus,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Bump N tasks to a single status. Each task is routed through the
    methodology validator so the new status is checked against the parent
    project's methodology (waterfall/agile/waves_threads). Tasks where
    status is already the target are no-ops; tasks whose project's
    methodology rejects the status get a per-row failure entry with
    `code="invalid_status"`.

    Per-row RBAC: top-level gate is `projects.task.change_status`. Each
    row also runs the project edit check via `_caller_can_edit_task` so
    a cross-project bulk demotes unauthorized rows into `failed[]` with
    `code="forbidden"` instead of 403'ing the entire batch.

    A status_changed envelope fires per updated row to keep the audit
    surface identical to the single-task PATCH path.

    v1.2: returns the standard bulk envelope so the frontend can merge
    full task docs into local state without a refetch.
    """
    if not has_perm(current_user, "projects.task.change_status"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    updated: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    unique_task_ids = list(dict.fromkeys(body.task_ids))

    # Cache project lookups per project_id across rows.
    project_cache: Dict[str, Dict[str, Any]] = {}

    for tid in unique_task_ids:
        try:
            before = await db["pm_tasks"].find_one(
                {"task_id": tid, "tenant_id": tenant_id}, {"_id": 0}
            )
            if before is None:
                failed.append(_bulk_failure(tid, "Task not found", _BULK_ERR_NOT_FOUND))
                continue
            old_status = before.get("status")
            if old_status == body.status:
                continue  # no-op

            project_id = before["project_id"]
            if project_id not in project_cache:
                proj = await db["projects"].find_one(
                    {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
                )
                project_cache[project_id] = proj or {}
            project = project_cache[project_id]
            if not project:
                failed.append(
                    _bulk_failure(tid, "Parent project not found", _BULK_ERR_NOT_FOUND)
                )
                continue
            if not _caller_can_edit_task(current_user, project):
                failed.append(
                    _bulk_failure(
                        tid,
                        "Caller cannot edit tasks in this project",
                        _BULK_ERR_FORBIDDEN,
                    )
                )
                continue

            # Run the methodology validator with the new status; reject if
            # invalid for this project's methodology.
            try:
                normalized_fields, final_status = validate_task_methodology_fields(
                    project,
                    before.get("methodology_fields") or {},
                    body.status,
                )
            except HTTPException as exc:
                failed.append(_bulk_failure(tid, exc.detail, _BULK_ERR_INVALID_STATUS))
                continue

            now_iso = _utc_now_iso()
            # Mirror single-task PATCH: also write the normalized methodology
            # fields so agile column_id moves with the status change (without
            # this, the bulk endpoint silently leaves cards in their old
            # column even though `status` updates).
            update_set: Dict[str, Any] = {"status": final_status, "updated_at": now_iso}
            for k, v in normalized_fields.items():
                update_set[f"methodology_fields.{k}"] = v
            async with crud_envelope(
                module="projects",
                entity="task",
                action="updated",
                resource_id=tid,
                current_user=current_user,
                db=db,
                before=before,
                request=request,
            ) as ctx:
                await db["pm_tasks"].update_one(
                    {"task_id": tid, "tenant_id": tenant_id},
                    {"$set": update_set},
                )
                await db["pm_task_status_history"].insert_one(
                    {
                        # Phase 1E.3c — append-only audit stamped with tenant.
                        "tenant_id": tenant_id,
                        "task_id": tid,
                        "project_id": project_id,
                        "from_status": old_status,
                        "to_status": final_status,
                        "changed_at": _utc_now(),
                        "changed_by": current_user.user_id,
                    }
                )
                after = await db["pm_tasks"].find_one(
                    {"task_id": tid, "tenant_id": tenant_id}, {"_id": 0}
                )
                ctx.set_after(after)

            # Discrete status_changed envelope (mirrors single-task PATCH).
            async with crud_envelope(
                module="projects",
                entity="task",
                action="status_changed",
                resource_id=tid,
                current_user=current_user,
                db=db,
                before=before,
                request=request,
            ) as ctx:
                ctx.set_after(after)

            if after is not None:
                updated.append(after)
                await _emit_task_event(
                    event_type="task.status_changed",
                    task_doc=after,
                    project_id=project_id,
                    entity_id=tid,
                    current_user=current_user,
                )
        except HTTPException as exc:
            failed.append(_bulk_failure(tid, exc.detail, _BULK_ERR_VALIDATION))
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("bulk_update_status task_id=%s unexpected", tid)
            failed.append(_bulk_failure(tid, f"Unexpected: {exc}", _BULK_ERR_UNEXPECTED))

    return _bulk_response(updated, failed)


# ---------------------------------------------------------------------------
# Bulk move-to-sprint (v1.2 Theme B)
#
# Move N tasks to a sprint, or back to the backlog (sprint_id=null). The
# sprint assignment lives at `methodology_fields.sprint_id` to match the
# single-task PATCH path; bulk-move-to-sprint just sets that subfield
# uniformly. Validates that:
#   * sprint exists (when sprint_id is not null)
#   * sprint belongs to the same project as the task (per-row check —
#     mixed-project bulk fails the offending row, not the whole batch)
# ---------------------------------------------------------------------------
@router.patch(
    "/tasks/bulk-sprint",
    dependencies=[Depends(require_ai_permission("projects.task.edit"))],
)
async def bulk_move_to_sprint(
    body: BulkTaskSprint,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Move N tasks to a sprint (or to the backlog with sprint_id=null).

    RBAC: top-level gate is `projects.task.edit`. Each row also runs the
    per-project edit check so cross-project bulks demote unauthorized
    rows into `failed[]` with `code="forbidden"`.

    Returns the standard bulk envelope. Tasks already assigned to the
    target sprint are silent no-ops.
    """
    if not has_perm(current_user, "projects.task.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Validate target sprint exists once (when not null). We don't pin the
    # project here because the sprint→project match is enforced per-row
    # below: a bulk that spans projects should only fail the rows whose
    # sprint doesn't match, not the whole batch.
    target_sprint: Optional[Dict[str, Any]] = None
    if body.sprint_id is not None:
        target_sprint = await db["pm_sprints"].find_one(
            {"sprint_id": body.sprint_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        if target_sprint is None:
            raise HTTPException(
                status_code=422,
                detail=f"sprint_id {body.sprint_id} does not match any sprint",
            )

    updated: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    unique_task_ids = list(dict.fromkeys(body.task_ids))
    project_cache: Dict[str, Dict[str, Any]] = {}

    for tid in unique_task_ids:
        try:
            before = await db["pm_tasks"].find_one(
                {"task_id": tid, "tenant_id": tenant_id}, {"_id": 0}
            )
            if before is None:
                failed.append(_bulk_failure(tid, "Task not found", _BULK_ERR_NOT_FOUND))
                continue
            current_sprint = (before.get("methodology_fields") or {}).get("sprint_id")
            if current_sprint == body.sprint_id:
                continue  # no-op

            project_id = before["project_id"]
            if project_id not in project_cache:
                proj = await db["projects"].find_one(
                    {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
                )
                project_cache[project_id] = proj or {}
            project = project_cache[project_id]
            if not project:
                failed.append(
                    _bulk_failure(tid, "Parent project not found", _BULK_ERR_NOT_FOUND)
                )
                continue
            if not _caller_can_edit_task(current_user, project):
                failed.append(
                    _bulk_failure(
                        tid,
                        "Caller cannot edit tasks in this project",
                        _BULK_ERR_FORBIDDEN,
                    )
                )
                continue

            # Sprint must belong to the task's project. We can't have a
            # cross-project sprint assignment — the per-row check makes
            # mixed-project bulks safe.
            if target_sprint is not None and target_sprint.get("project_id") != project_id:
                failed.append(
                    _bulk_failure(
                        tid,
                        (
                            f"sprint_id {body.sprint_id} belongs to a different "
                            "project than this task"
                        ),
                        _BULK_ERR_INVALID_SPRINT,
                    )
                )
                continue

            now_iso = _utc_now_iso()
            async with crud_envelope(
                module="projects",
                entity="task",
                action="updated",
                resource_id=tid,
                current_user=current_user,
                db=db,
                before=before,
                request=request,
            ) as ctx:
                if body.sprint_id is None:
                    # Move to backlog: clear the sub-field.
                    await db["pm_tasks"].update_one(
                        {"task_id": tid, "tenant_id": tenant_id},
                        {
                            "$unset": {"methodology_fields.sprint_id": ""},
                            "$set": {"updated_at": now_iso},
                        },
                    )
                else:
                    await db["pm_tasks"].update_one(
                        {"task_id": tid, "tenant_id": tenant_id},
                        {
                            "$set": {
                                "methodology_fields.sprint_id": body.sprint_id,
                                "updated_at": now_iso,
                            }
                        },
                    )
                after = await db["pm_tasks"].find_one(
                    {"task_id": tid, "tenant_id": tenant_id}, {"_id": 0}
                )
                ctx.set_after(after)
            if after is not None:
                updated.append(after)
                await _emit_task_event(
                    event_type="task.updated",
                    task_doc=after,
                    project_id=project_id,
                    entity_id=tid,
                    current_user=current_user,
                )
        except HTTPException as exc:
            failed.append(_bulk_failure(tid, exc.detail, _BULK_ERR_VALIDATION))
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("bulk_move_to_sprint task_id=%s unexpected", tid)
            failed.append(_bulk_failure(tid, f"Unexpected: {exc}", _BULK_ERR_UNEXPECTED))

    return _bulk_response(updated, failed)


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------
@router.get(
    "/tasks",
    dependencies=[Depends(require_ai_permission("projects.task.view"))],
)
async def list_tasks(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    due_before: Optional[str] = Query(None),
    due_after: Optional[str] = Query(None),
    wave_id: Optional[str] = Query(None),
    thread_id: Optional[str] = Query(None),
    sprint_id: Optional[str] = Query(None),
    label: Optional[str] = Query(None),
    q: Optional[str] = Query(None, min_length=1),
    # v1.3 Thread 1C — server-side smart-view filters. Additive; when all
    # omitted the endpoint behaves exactly as it did pre-v1.3.
    status_in: Optional[str] = Query(
        None,
        description="Comma-separated list of statuses to include",
    ),
    priority_in: Optional[str] = Query(
        None,
        description="Comma-separated list of priorities to include",
    ),
    is_blocked: Optional[bool] = Query(None),
    due_within_days: Optional[int] = Query(None, ge=0, le=365),
    is_overdue: Optional[bool] = Query(None),
    is_at_risk: Optional[bool] = Query(None),
    created_after: Optional[datetime] = Query(None),
    updated_after: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    capped = min(limit, 500)

    # Phase 1E.3c — every query starts tenant-scoped. Caller filters
    # apply on top.
    query: Dict[str, Any] = {"tenant_id": tenant_id}
    if project_id:
        query["project_id"] = project_id

    # `assignee_id` supports the v1.3 smart-view sentinels (`__me`,
    # `__unassigned`); a literal user_id continues to work as before.
    if assignee_id is not None:
        from services.projects_task_filter import (
            ME_SENTINEL,
            UNASSIGNED_SENTINEL,
        )
        if assignee_id == ME_SENTINEL:
            query["assignee_id"] = current_user.user_id
        elif assignee_id == UNASSIGNED_SENTINEL:
            query["assignee_id"] = None
        elif assignee_id:
            query["assignee_id"] = assignee_id
    if status:
        query["status"] = status
    if priority:
        _validate_enum(priority, PRIORITIES, "priority")
        query["priority"] = priority
    if due_before is not None or due_after is not None:
        dd: Dict[str, Any] = {}
        if due_after is not None:
            dd["$gte"] = due_after
        if due_before is not None:
            dd["$lte"] = due_before
        query["due_date"] = dd
    if wave_id:
        query["methodology_fields.wave_id"] = wave_id
    if thread_id:
        query["methodology_fields.thread_id"] = thread_id
    if sprint_id:
        query["methodology_fields.sprint_id"] = sprint_id
    if label:
        query["labels"] = label
    if q:
        query["title"] = {"$regex": q, "$options": "i"}

    # v1.3 smart-view filters. Only invoked when project_id is set (the
    # smart-view UI is always project-scoped). For non-project-scoped
    # callers we silently skip — preserves legacy semantics.
    smart_view_active = any(
        v is not None
        for v in (
            status_in,
            priority_in,
            is_blocked,
            due_within_days,
            is_overdue,
            is_at_risk,
            created_after,
            updated_after,
            search,
        )
    )
    if smart_view_active and project_id:
        from services.projects_task_filter import (
            build_filter_query,
            parse_comma_list,
        )

        priority_list = parse_comma_list(priority_in)
        if priority_list:
            for p in priority_list:
                _validate_enum(p, PRIORITIES, "priority")

        filter_query = build_filter_query(
            project_id=project_id,
            user_id=current_user.user_id,
            status_in=parse_comma_list(status_in),
            priority_in=priority_list,
            is_blocked=is_blocked,
            due_within_days=due_within_days,
            is_overdue=is_overdue,
            is_at_risk=is_at_risk,
            created_after=created_after,
            updated_after=updated_after,
            search=search,
        )
        # Merge filter_query into `query`. Both already constrain
        # `project_id` to the same value, so $and the rest.
        merged: Dict[str, Any] = {}
        merged_and: List[Dict[str, Any]] = []
        for src in (query, filter_query):
            for k, v in src.items():
                if k == "$and":
                    merged_and.extend(v)
                    continue
                if k in merged and merged[k] != v:
                    # Conflict — fold both sides into $and to avoid clobbering.
                    merged_and.append({k: merged.pop(k)})
                    merged_and.append({k: v})
                else:
                    merged[k] = v
        if merged_and:
            merged["$and"] = merged_and
        query = merged

    total = await db["pm_tasks"].count_documents(query)
    items = (
        await db["pm_tasks"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(capped)
        .to_list(capped)
    )
    # `X-Total-Count` lets the frontend show "showing N of M" without a
    # second round-trip when smart-view filters are active.
    try:
        request.state._task_list_total = total  # for debugging / introspection
    except Exception:
        pass
    response_headers = {"X-Total-Count": str(total)}
    from fastapi.responses import JSONResponse

    return JSONResponse(
        content={"items": items, "total": total, "skip": skip, "limit": capped},
        headers=response_headers,
    )


@router.get(
    "/tasks/{task_id}",
    dependencies=[Depends(require_ai_permission("projects.task.view"))],
)
async def get_task(
    task_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    doc = await db["pm_tasks"].find_one(
        {"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return doc


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "/tasks",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.task.create"))],
)
async def create_task(
    payload: TaskCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    if not has_perm(current_user, "projects.task.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    _validate_enum(payload.priority, PRIORITIES, "priority")
    project = await _load_project_for_task(db, payload.project_id, tenant_id)
    _refuse_if_project_frozen(project, action="create")

    normalized_fields, final_status = validate_task_methodology_fields(
        project, payload.methodology_fields, payload.status
    )

    if payload.assignee_id is not None:
        # Phase 1E.3c — assignee must belong to the calling tenant.
        if not await UserRepository(pg).user_exists_in_tenant(
            payload.assignee_id, tenant_id
        ):
            raise HTTPException(
                status_code=422,
                detail=f"assignee_id {payload.assignee_id} does not match any user",
            )

    # Validate parent task exists & is same project (if provided).
    if payload.parent_task_id is not None:
        parent = await db["pm_tasks"].find_one(
            {"task_id": payload.parent_task_id, "tenant_id": tenant_id},
            {"_id": 0, "project_id": 1},
        )
        if parent is None:
            raise HTTPException(
                status_code=422,
                detail=f"parent_task_id {payload.parent_task_id} does not match any task",
            )
        if parent["project_id"] != payload.project_id:
            raise HTTPException(
                status_code=422,
                detail="parent_task_id must belong to the same project",
            )

    # Validate each dependency (cycle impossible since new task has no id yet).
    dep_ids = list(dict.fromkeys(payload.dependencies))
    for dep_id in dep_ids:
        dep = await db["pm_tasks"].find_one(
            {"task_id": dep_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        if dep is None:
            raise HTTPException(
                status_code=422,
                detail=f"dependency {dep_id} does not match any task",
            )
        if dep["project_id"] != payload.project_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Dependencies must be within the same project "
                    "(cross-project / cross-methodology dependencies are not allowed)"
                ),
            )
        if project["project_type"] == "waves_threads":
            to_wave = (dep.get("methodology_fields") or {}).get("wave_id")
            if to_wave and to_wave == normalized_fields.get("wave_id"):
                raise HTTPException(
                    status_code=422,
                    detail="Same-wave task dependencies are not allowed",
                )

    # Date ordering.
    if payload.start_date and payload.due_date and payload.start_date > payload.due_date:
        raise HTTPException(
            status_code=422, detail="start_date must be on or before due_date"
        )

    task_number = await _next_task_number(db, payload.project_id, tenant_id)

    now_iso = _utc_now_iso()
    task_id = gen_id("task_")
    doc: Dict[str, Any] = {
        "task_id": task_id,
        # Phase 1E.3c — stamp the owning tenant on every new task.
        "tenant_id": tenant_id,
        "project_id": payload.project_id,
        "title": payload.title,
        "description": payload.description,
        "assignee_id": payload.assignee_id,
        "status": final_status,
        "priority": payload.priority,
        "estimated_duration_hours": payload.estimated_duration_hours,
        "parent_task_id": payload.parent_task_id,
        "start_date": payload.start_date,
        "due_date": payload.due_date,
        "dependencies": dep_ids,
        "methodology_fields": normalized_fields,
        "labels": list(payload.labels),
        "task_number": task_number,
        "sort_index": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects",
        entity="task",
        action="created",
        resource_id=task_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["pm_tasks"].insert_one(doc)
        await db["pm_task_status_history"].insert_one(
            {
                # Phase 1E.3c — append-only audit stamped with tenant.
                "tenant_id": tenant_id,
                "task_id": task_id,
                "project_id": payload.project_id,
                "from_status": None,
                "to_status": final_status,
                "changed_at": _utc_now(),
                "changed_by": current_user.user_id,
            }
        )
        ctx.set_after(_scrub(doc))

    scrubbed = _scrub(doc)
    await _emit_task_event(
        event_type="task.created",
        task_doc=scrubbed,
        project_id=payload.project_id,
        entity_id=task_id,
        current_user=current_user,
    )
    return scrubbed


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
@router.patch(
    "/tasks/{task_id}",
    dependencies=[Depends(require_ai_permission("projects.task.edit"))],
)
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    db = get_db()

    before = await db["pm_tasks"].find_one(
        {"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # v1.3 Theme A — optimistic concurrency check.
    # If the client sends `If-Match: <updated_at>`, compare against the
    # current task's `updated_at`. A mismatch means someone else (or the
    # PMO AI) touched the task since the client read it; we return 409
    # with the fresh task doc so the frontend can rebase its optimistic
    # update and re-prompt the user. The header is optional — if absent
    # we skip the check (backwards-compatible). Bulk endpoints
    # intentionally don't honor If-Match: a 50-row bulk would otherwise
    # need 50 If-Match values, and last-write-wins is acceptable for
    # batch ops.
    if_match = request.headers.get("If-Match")
    if if_match is not None and if_match != before.get("updated_at"):
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "conflict",
                "current_updated_at": before.get("updated_at"),
                "current_doc": before,
            },
        )

    project = await _load_project_for_task(db, before["project_id"], tenant_id)

    # Wave 4 / A4 PM auto-grant: a PM of the parent project gets implicit
    # task.edit even without the global projects.task.edit permission, as
    # long as they hold projects.project.view.
    if not has_perm(current_user, "projects.task.edit"):
        pm_id = project.get("pm_id")
        if not (
            pm_id
            and pm_id == getattr(current_user, "user_id", None)
            and has_perm(current_user, "projects.project.view")
        ):
            raise HTTPException(status_code=403, detail="Permission denied")

    dumped = payload.model_dump(exclude_unset=True)

    # Phase 1E.3c — defensive: drop tenant_id from update payload so a
    # client can never reparent a task to another tenant via PATCH.
    dumped.pop("tenant_id", None)

    # Methodology fields: partial merge on top of stored sub-doc.
    existing_mf: dict = before.get("methodology_fields") or {}
    mf_set: dict = {}
    mf_unset: dict = {}
    explicit_unset_keys: set = set()

    if "methodology_fields" in dumped:
        incoming_mf = dumped.pop("methodology_fields") or {}
        if not isinstance(incoming_mf, dict):
            raise HTTPException(
                status_code=422, detail="methodology_fields must be an object"
            )
        explicit_unset_keys = {k for k, v in incoming_mf.items() if v is None}
        merged: dict = {**existing_mf}
        for k, v in incoming_mf.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
    else:
        merged = dict(existing_mf)

    status_explicit = "status" in dumped
    requested_status = dumped.get("status", before.get("status"))
    normalized_fields, final_status = validate_task_methodology_fields(
        project, merged, requested_status, status_explicit=status_explicit
    )
    dumped["status"] = final_status

    for k, v in normalized_fields.items():
        mf_set[f"methodology_fields.{k}"] = v
    for k in explicit_unset_keys:
        if k not in normalized_fields:
            mf_unset[f"methodology_fields.{k}"] = ""

    # Assignee existence check (and capture for assigned event).
    assignee_changed = False
    if "assignee_id" in dumped:
        if dumped["assignee_id"] is not None:
            # Phase 1E.3c — assignee must belong to the calling tenant.
            if not await UserRepository(pg).user_exists_in_tenant(
                dumped["assignee_id"], tenant_id
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"assignee_id {dumped['assignee_id']} does not match any user",
                )
        if before.get("assignee_id") != dumped["assignee_id"]:
            assignee_changed = True

    if "priority" in dumped and dumped["priority"] is not None:
        _validate_enum(dumped["priority"], PRIORITIES, "priority")

    if "parent_task_id" in dumped and dumped["parent_task_id"] is not None:
        parent = await db["pm_tasks"].find_one(
            {"task_id": dumped["parent_task_id"], "tenant_id": tenant_id},
            {"_id": 0, "project_id": 1},
        )
        if parent is None:
            raise HTTPException(
                status_code=422,
                detail=f"parent_task_id {dumped['parent_task_id']} does not match any task",
            )
        if parent["project_id"] != before["project_id"]:
            raise HTTPException(
                status_code=422,
                detail="parent_task_id must belong to the same project",
            )

    # Date ordering using effective values.
    effective_start = dumped.get("start_date", before.get("start_date"))
    effective_due = dumped.get("due_date", before.get("due_date"))
    if effective_start and effective_due and effective_start > effective_due:
        raise HTTPException(
            status_code=422, detail="start_date must be on or before due_date"
        )

    dumped["updated_at"] = _utc_now_iso()
    set_payload = {**dumped, **mf_set}
    update_ops: dict = {"$set": set_payload}
    if mf_unset:
        update_ops["$unset"] = mf_unset

    old_status = before.get("status")
    status_changed = final_status != old_status

    # Wave 4 / A5: audit row writes inside the same envelope as the task
    # update so a policy deny doesn't leave the persisted task without an
    # audit row.
    async with crud_envelope(
        module="projects",
        entity="task",
        action="updated",
        resource_id=task_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["pm_tasks"].update_one(
            {"task_id": task_id, "tenant_id": tenant_id}, update_ops
        )
        if status_changed:
            await db["pm_task_status_history"].insert_one(
                {
                    # Phase 1E.3c — append-only audit stamped with tenant.
                    "tenant_id": tenant_id,
                    "task_id": task_id,
                    "project_id": before["project_id"],
                    "from_status": old_status,
                    "to_status": final_status,
                    "changed_at": _utc_now(),
                    "changed_by": current_user.user_id,
                }
            )
        after = await db["pm_tasks"].find_one(
            {"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(after)

    if status_changed:
        async with crud_envelope(
            module="projects",
            entity="task",
            action="status_changed",
            resource_id=task_id,
            current_user=current_user,
            db=db,
            before=before,
            request=request,
        ) as ctx:
            ctx.set_after(after)

    if assignee_changed:
        async with crud_envelope(
            module="projects",
            entity="task",
            action="assigned",
            resource_id=task_id,
            current_user=current_user,
            db=db,
            before=before,
            request=request,
        ) as ctx:
            ctx.set_after(after)

    # Realtime fan-out. Emit the most specific event type first (a
    # status_changed implies an update; the SSE consumer treats them as
    # the same merge). When both status and assignee changed in one
    # PATCH, we emit status_changed (more salient for the board view)
    # and an additional `task.assigned` so per-user notifications still
    # fire. Otherwise emit a single task.updated.
    if after is not None:
        if status_changed:
            await _emit_task_event(
                event_type="task.status_changed",
                task_doc=after,
                project_id=before["project_id"],
                entity_id=task_id,
                current_user=current_user,
            )
        else:
            await _emit_task_event(
                event_type="task.updated",
                task_doc=after,
                project_id=before["project_id"],
                entity_id=task_id,
                current_user=current_user,
            )
        if assignee_changed:
            await _emit_task_event(
                event_type="task.assigned",
                task_doc=after,
                project_id=before["project_id"],
                entity_id=task_id,
                current_user=current_user,
            )

    return after


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@router.delete(
    "/tasks/{task_id}",
    dependencies=[Depends(require_ai_permission("projects.task.delete"))],
)
async def delete_task(
    task_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_tasks"].find_one(
        {"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Task not found")

    async with crud_envelope(
        module="projects",
        entity="task",
        action="deleted",
        resource_id=task_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        # Remove this task_id from any other task's dependencies array.
        # Phase 1E.3c — dependency cleanup is tenant-scoped. The same-
        # project rule in _validate_dependency means cross-tenant
        # dependencies can't exist anyway, but the filter is defensive.
        await db["pm_tasks"].update_many(
            {"dependencies": task_id, "tenant_id": tenant_id},
            {"$pull": {"dependencies": task_id}, "$set": {"updated_at": _utc_now_iso()}},
        )
        await db["pm_tasks"].delete_one(
            {"task_id": task_id, "tenant_id": tenant_id}
        )
        ctx.set_after(None)

    # Realtime fan-out: tell viewers to drop this row. payload is the
    # `before` doc so the consumer has enough context to render a "task
    # X was deleted by Y" toast if it wants to.
    await _emit_task_event(
        event_type="task.deleted",
        task_doc=before,
        project_id=before["project_id"],
        entity_id=task_id,
        current_user=current_user,
    )
    return {"ok": True, "deleted_id": task_id}


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
@router.post(
    "/tasks/{task_id}/dependencies",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.task.edit"))],
)
async def add_dependency(
    task_id: str,
    body: TaskDependencyAdd,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    from_task = await db["pm_tasks"].find_one(
        {"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if from_task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    await _validate_dependency(db, from_task, body.depends_on_task_id, tenant_id)

    # No-op if already present.
    if body.depends_on_task_id in (from_task.get("dependencies") or []):
        return from_task

    async with crud_envelope(
        module="projects",
        entity="task",
        action="updated",
        resource_id=task_id,
        current_user=current_user,
        db=db,
        before=from_task,
        request=request,
    ) as ctx:
        await db["pm_tasks"].update_one(
            {"task_id": task_id, "tenant_id": tenant_id},
            {
                "$addToSet": {"dependencies": body.depends_on_task_id},
                "$set": {"updated_at": _utc_now_iso()},
            },
        )
        after = await db["pm_tasks"].find_one(
            {"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(after)

    if after is not None:
        await _emit_task_event(
            event_type="task.updated",
            task_doc=after,
            project_id=from_task["project_id"],
            entity_id=task_id,
            current_user=current_user,
        )
    return after


@router.delete(
    "/tasks/{task_id}/dependencies/{dep_id}",
    dependencies=[Depends(require_ai_permission("projects.task.edit"))],
)
async def remove_dependency(
    task_id: str,
    dep_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    before = await db["pm_tasks"].find_one(
        {"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Task not found")

    async with crud_envelope(
        module="projects",
        entity="task",
        action="updated",
        resource_id=task_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["pm_tasks"].update_one(
            {"task_id": task_id, "tenant_id": tenant_id},
            {
                "$pull": {"dependencies": dep_id},
                "$set": {"updated_at": _utc_now_iso()},
            },
        )
        after = await db["pm_tasks"].find_one(
            {"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(after)

    if after is not None:
        await _emit_task_event(
            event_type="task.updated",
            task_doc=after,
            project_id=before["project_id"],
            entity_id=task_id,
            current_user=current_user,
        )
    return after
