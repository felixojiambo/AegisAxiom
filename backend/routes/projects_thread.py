"""
Projects module — Thread CRUD (waves_threads methodology only).

Adapted from Davies-PMO-app `app/api/threads.py` for the AEGISAXIOM ingestion stack.
Collection: pm_threads. ID prefix: thread_. Threads are owned by a wave.

Wholesale-replacement note: this file replaces the legacy
`backend/routes/project_management_threads.py`.
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

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
register_fields("projects", "thread", [
    {"name": "thread_id", "type": "string", "label": "Thread ID", "description": "Primary key (thread_<uuid>)"},
    {"name": "wave_id", "type": "string", "label": "Wave ID", "description": "FK to pm_waves.wave_id (parent wave)"},
    {"name": "owner_id", "type": "string", "label": "Owner ID", "description": "FK to users.user_id"},
    {"name": "module", "type": "string", "label": "Module", "description": "Subsystem the thread is scoped to"},
    {"name": "status", "type": "string", "label": "Status",
     "options": [
         {"value": "scoped", "label": "Scoped"},
         {"value": "in_progress", "label": "In Progress"},
         {"value": "shipped", "label": "Shipped"},
         {"value": "blocked", "label": "Blocked"},
     ],
     "description": "Thread lifecycle status"},
    {"name": "interface_contract", "type": "list", "label": "Interface Contract", "description": "Cross-thread contract object (request/response shape, events, etc.)"},
    {"name": "integration_required", "type": "boolean", "label": "Integration Required", "description": "Whether this thread crosses a module boundary requiring an interface contract"},
    {"name": "created_at", "type": "datetime", "label": "Created At"},
    {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    {"name": "created_by", "type": "string", "label": "Created By"},
])

register_module_config("projects", "pm_threads", "thread_id", "Custom")
register_module_events("projects", "thread", ["created", "updated", "deleted", "status_changed", "assigned"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
THREAD_STATUS_ENUM = {"scoped", "in_progress", "shipped", "blocked"}


class StandupAnswers(BaseModel):
    """Daily standup answers persisted on the thread document.

    Wave 4 / A3 introduces this sub-object so StandupHelper.js can PATCH the
    answers via /threads/{id} without tripping ThreadUpdate's
    extra="forbid" gate. All fields optional; the frontend sends a partial
    object on every keystroke (debounced)."""

    model_config = ConfigDict(extra="forbid")
    shipped_yesterday: Optional[str] = None
    working_on_today: Optional[str] = None
    blockers_or_scope_drift: Optional[str] = None


class ThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wave_id: str = Field(min_length=1)
    owner_id: Optional[str] = None
    module: str = Field(min_length=1, max_length=120)
    interface_contract: Dict[str, Any] = Field(default_factory=dict)
    integration_required: bool = False
    status: Optional[str] = "scoped"
    # Wave 4 / A3: ThreadsView.js posts {wave_id, project_id, ...seed}. The
    # project_id is informational at create time — the wave's project is
    # authoritative — but accepting it here matches the frontend payload
    # shape. Validated server-side against the wave's project.
    project_id: Optional[str] = None


class ThreadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: Optional[str] = None
    module: Optional[str] = Field(default=None, min_length=1, max_length=120)
    status: Optional[str] = None
    interface_contract: Optional[Dict[str, Any]] = None
    integration_required: Optional[bool] = None
    # Wave 4 / A3: StandupHelper.js PATCHes /threads/{id} with a `standup`
    # sub-object. Persisted on the thread doc with a last_updated_at /
    # last_updated_by stamp written server-side.
    standup: Optional[StandupAnswers] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_wave_exists(db, wave_id: str, tenant_id: str) -> None:
    doc = await db["pm_waves"].find_one(
        {"wave_id": wave_id, "tenant_id": tenant_id}, {"wave_id": 1, "_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=422, detail=f"wave_id {wave_id} does not match any wave")


async def _wave_project_id(db, wave_id: str, tenant_id: str) -> Optional[str]:
    doc = await db["pm_waves"].find_one(
        {"wave_id": wave_id, "tenant_id": tenant_id},
        {"_id": 0, "project_id": 1},
    )
    return doc.get("project_id") if doc else None


_FROZEN_PROJECT_STATUSES = {"closed", "on_hold"}


async def _refuse_if_wave_project_frozen(db, wave_id: str, tenant_id: str) -> None:
    """Wave 4 / A4: reject thread create when the wave's project is
    closed/on_hold."""
    project_id = await _wave_project_id(db, wave_id, tenant_id)
    if not project_id:
        return
    proj = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id},
        {"_id": 0, "status": 1},
    )
    status = (proj or {}).get("status")
    if status in _FROZEN_PROJECT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is {status}. Cannot create new threads. "
                "Reopen the project first."
            ),
        )


async def _validate_thread_module_against_project(
    db, wave_id: str, module: str, tenant_id: str
) -> None:
    """Reject thread.module values that aren't in the project's
    `methodology_config.modules` list. Tasks created in this thread later
    enforce the same membership rule, so allowing thread create with an
    unknown module produces threads where no tasks can be created.
    """
    project_id = await _wave_project_id(db, wave_id, tenant_id)
    if not project_id:
        return
    proj = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id},
        {"_id": 0, "methodology_config": 1, "project_type": 1},
    )
    if not proj or proj.get("project_type") != "waves_threads":
        return
    project_modules = (proj.get("methodology_config") or {}).get("modules") or []
    if project_modules and module not in project_modules:
        raise HTTPException(
            status_code=422,
            detail=(
                f"module {module!r} is not one of the project's modules "
                f"{project_modules}. Add it to the project's modules list "
                f"in Settings, or pick an existing module."
            ),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/threads", dependencies=[Depends(require_ai_permission("projects.thread.view"))])
async def list_threads(
    wave_id: Optional[str] = Query(None),
    owner_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.thread.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    # Phase 1E.3c — every query starts tenant-scoped.
    query: dict = {"tenant_id": tenant_id}
    if wave_id:
        query["wave_id"] = wave_id
    if owner_id:
        query["owner_id"] = owner_id
    if status:
        if status not in THREAD_STATUS_ENUM:
            raise HTTPException(status_code=400, detail=f"Invalid status; must be one of {sorted(THREAD_STATUS_ENUM)}")
        query["status"] = status
    total = await db["pm_threads"].count_documents(query)
    items = (
        await db["pm_threads"]
        .find(query, {"_id": 0})
        .sort("created_at", 1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/threads/{thread_id}", dependencies=[Depends(require_ai_permission("projects.thread.view"))])
async def get_thread(
    thread_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.thread.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    doc = await db["pm_threads"].find_one(
        {"thread_id": thread_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return doc


@router.post(
    "/threads",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.thread.create"))],
)
async def create_thread(
    payload: ThreadCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.thread.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    await _ensure_wave_exists(db, payload.wave_id, tenant_id)
    await _refuse_if_wave_project_frozen(db, payload.wave_id, tenant_id)

    # Wave 4 / A3: if frontend supplied project_id, verify it matches the
    # wave's project. The wave's project is always authoritative; this is a
    # consistency check for clients sending both fields.
    if payload.project_id is not None:
        wave_project_id = await _wave_project_id(db, payload.wave_id, tenant_id)
        if wave_project_id and wave_project_id != payload.project_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"project_id {payload.project_id} does not match the "
                    f"parent wave's project_id {wave_project_id}"
                ),
            )

    if payload.integration_required and not payload.interface_contract:
        raise HTTPException(
            status_code=422,
            detail="interface_contract is required when integration_required is true",
        )

    module_clean = payload.module.strip()
    await _validate_thread_module_against_project(
        db, payload.wave_id, module_clean, tenant_id
    )

    status_value = payload.status or "scoped"
    if status_value not in THREAD_STATUS_ENUM:
        raise HTTPException(status_code=422, detail=f"Invalid status; must be one of {sorted(THREAD_STATUS_ENUM)}")

    now = _now_iso()
    doc = {
        "thread_id": gen_id("thread_"),
        # Phase 1E.3c — stamp owning tenant.
        "tenant_id": tenant_id,
        "wave_id": payload.wave_id,
        "owner_id": payload.owner_id,
        "module": module_clean,
        "status": status_value,
        "interface_contract": payload.interface_contract,
        "integration_required": payload.integration_required,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects", entity="thread", action="created",
        resource_id=doc["thread_id"], current_user=current_user, db=db, request=request,
    ) as ctx:
        await db["pm_threads"].insert_one(doc)
        doc.pop("_id", None)
        ctx.set_after(doc)

    # owner_id assignment at create time is captured in the "created"
    # envelope's payload. A discrete "assigned" envelope only fires on
    # subsequent owner_id changes (see update_thread).

    return doc


@router.patch(
    "/threads/{thread_id}",
    dependencies=[Depends(require_ai_permission("projects.thread.edit"))],
)
async def update_thread(
    thread_id: str,
    payload: ThreadUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.thread.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_threads"].find_one(
        {"thread_id": thread_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    dumped = payload.model_dump(exclude_unset=True)

    # Phase 1E.3c — defensive: drop tenant_id from update payload so a
    # client can never reparent a thread to another tenant via PATCH.
    dumped.pop("tenant_id", None)

    if "status" in dumped and dumped["status"] is not None and dumped["status"] not in THREAD_STATUS_ENUM:
        raise HTTPException(status_code=422, detail=f"Invalid status; must be one of {sorted(THREAD_STATUS_ENUM)}")

    # integration_required ↔ interface_contract pairing on the merged result
    final_integration = dumped.get("integration_required", before.get("integration_required", False))
    final_contract = dumped.get("interface_contract", before.get("interface_contract") or {})
    if final_integration and not final_contract:
        raise HTTPException(
            status_code=422,
            detail="interface_contract is required when integration_required is true",
        )

    if "module" in dumped and isinstance(dumped["module"], str):
        dumped["module"] = dumped["module"].strip()
        await _validate_thread_module_against_project(
            db, before["wave_id"], dumped["module"], tenant_id
        )

    # Wave 4 / A3: standup sub-object persists with a last_updated_at /
    # last_updated_by stamp so StandupHelper can show "saved at X by Y".
    # Standup answers shallow-merge over existing values so partial PATCHes
    # don't blow away earlier saves.
    if "standup" in dumped and dumped["standup"] is not None:
        incoming_standup = dumped.pop("standup")
        existing_standup = before.get("standup") or {}
        merged_standup = {**existing_standup}
        for k, v in incoming_standup.items():
            if v is not None:
                merged_standup[k] = v
        merged_standup["last_updated_at"] = _now_iso()
        merged_standup["last_updated_by"] = current_user.user_id
        dumped["standup"] = merged_standup
    elif "standup" in dumped and dumped["standup"] is None:
        # Caller explicitly cleared standup.
        dumped.pop("standup")

    dumped["updated_at"] = _now_iso()

    new_status = dumped.get("status", before.get("status"))
    status_changed = "status" in dumped and new_status != before.get("status")

    # Wave 4 / A5: write audit row inside the SAME envelope as the thread
    # update; surfaces in dashboard recent_activity.
    async with crud_envelope(
        module="projects", entity="thread", action="updated",
        resource_id=thread_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_threads"].update_one(
            {"thread_id": thread_id, "tenant_id": tenant_id}, {"$set": dumped}
        )
        if status_changed:
            await db["pm_thread_status_history"].insert_one(
                {
                    # Phase 1E.3c — append-only audit stamped with tenant.
                    "tenant_id": tenant_id,
                    "thread_id": thread_id,
                    "wave_id": before.get("wave_id"),
                    "from_status": before.get("status"),
                    "to_status": new_status,
                    "changed_at": datetime.now(timezone.utc),
                    "changed_by": current_user.user_id,
                }
            )
        updated = await db["pm_threads"].find_one(
            {"thread_id": thread_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(updated)

    if status_changed:
        async with crud_envelope(
            module="projects", entity="thread", action="status_changed",
            resource_id=thread_id, current_user=current_user, db=db,
            before=before, request=request,
        ) as ctx:
            ctx.set_after(updated)

    # owner_id change → discrete "assigned" envelope (mirrors task.assigned).
    if before.get("owner_id") != updated.get("owner_id"):
        async with crud_envelope(
            module="projects", entity="thread", action="assigned",
            resource_id=thread_id, current_user=current_user, db=db,
            before=before, request=request,
        ) as ctx:
            ctx.set_after(updated)

    return updated


@router.delete(
    "/threads/{thread_id}",
    dependencies=[Depends(require_ai_permission("projects.thread.delete"))],
)
async def delete_thread(
    thread_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.thread.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_threads"].find_one(
        {"thread_id": thread_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Cascade runs INSIDE the envelope so a policy deny short-circuits without
    # touching child blocker_tickets.
    blockers_deleted = 0

    async with crud_envelope(
        module="projects", entity="thread", action="deleted",
        resource_id=thread_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_threads"].delete_one(
            {"thread_id": thread_id, "tenant_id": tenant_id}
        )
        blockers_deleted = (
            await db["pm_blocker_tickets"].delete_many(
                {
                    "tenant_id": tenant_id,
                    "$or": [
                        {"thread_id": thread_id},
                        {"target_thread_id": thread_id},
                    ],
                }
            )
        ).deleted_count
        ctx.set_after(None)

    return {
        "ok": True,
        "deleted_id": thread_id,
        "blockers_deleted": blockers_deleted,
    }
