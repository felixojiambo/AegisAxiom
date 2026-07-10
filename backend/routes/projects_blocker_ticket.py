"""
Projects module — BlockerTicket CRUD (waves_threads methodology only).

Adapted from Davies-PMO-app `app/api/blocker_tickets.py` for the AEGISAXIOM ingestion stack.
Collection: pm_blocker_tickets. ID prefix: blocker_.

Status flow: open -> acknowledged -> resolved | escalated.
- Transition into "resolved" stamps `resolved_at`.
- Transition out of "resolved" clears `resolved_at`.

Wholesale-replacement note: this file replaces the legacy
`backend/routes/project_management_blockers.py`.
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
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
register_fields("projects", "blocker_ticket", [
    {"name": "blocker_ticket_id", "type": "string", "label": "Blocker Ticket ID", "description": "Primary key (blocker_<uuid>)"},
    {"name": "thread_id", "type": "string", "label": "Thread ID", "description": "FK to pm_threads.thread_id (filer)"},
    {"name": "target_module", "type": "string", "label": "Target Module", "description": "Module the blocker is filed against"},
    {"name": "target_thread_id", "type": "string", "label": "Target Thread ID", "description": "FK to pm_threads.thread_id (target, optional)"},
    {"name": "description", "type": "string", "label": "Description"},
    {"name": "status", "type": "string", "label": "Status",
     "options": [
         {"value": "open", "label": "Open"},
         {"value": "acknowledged", "label": "Acknowledged"},
         {"value": "resolved", "label": "Resolved"},
         {"value": "escalated", "label": "Escalated"},
     ],
     "description": "Blocker lifecycle status"},
    {"name": "resolved_at", "type": "datetime", "label": "Resolved At", "description": "Stamped when status transitions into resolved; cleared on re-open"},
    {"name": "created_at", "type": "datetime", "label": "Created At"},
    {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    {"name": "created_by", "type": "string", "label": "Created By"},
])

register_module_config("projects", "pm_blocker_tickets", "blocker_ticket_id", "Custom")
register_module_events("projects", "blocker_ticket", ["created", "updated", "deleted", "status_changed"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
BLOCKER_STATUS_ENUM = {"open", "acknowledged", "resolved", "escalated"}

# Allowed status transitions (mirrors Davies-PMO source semantics).
_ALLOWED_TRANSITIONS = {
    "open": {"acknowledged", "resolved", "escalated"},
    "acknowledged": {"resolved", "escalated", "open"},
    "resolved": {"acknowledged"},  # re-open via acknowledged only
    "escalated": {"acknowledged"},
}


class BlockerTicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1)
    target_module: str = Field(min_length=1, max_length=120)
    target_thread_id: Optional[str] = None
    description: str = Field(min_length=1, max_length=2000)
    # Wave 4 / A3: BlockersView posts {wave_id, thread_id, ...}. wave_id is
    # informational on create — the parent wave is derivable from thread_id
    # — but accepting it here matches the frontend payload shape so
    # extra="forbid" stops 422-ing valid client requests.
    wave_id: Optional[str] = None


class BlockerTicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[str] = None
    target_module: Optional[str] = Field(default=None, min_length=1, max_length=120)
    target_thread_id: Optional[str] = None
    description: Optional[str] = Field(default=None, min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_transition(old_status: str, new_status: str) -> None:
    if old_status == new_status:
        return
    allowed = _ALLOWED_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Invalid blocker transition {old_status} -> {new_status}. "
                f"Allowed: {sorted(allowed)}"
            ),
        )


async def _ensure_thread_exists(
    db, thread_id: str, field_name: str, tenant_id: str
) -> None:
    if not await db["pm_threads"].find_one(
        {"thread_id": thread_id, "tenant_id": tenant_id},
        {"thread_id": 1, "_id": 0},
    ):
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} {thread_id} does not match any thread",
        )


_FROZEN_PROJECT_STATUSES = {"closed", "on_hold"}


async def _refuse_if_thread_project_frozen(db, thread_id: str, tenant_id: str) -> None:
    """Wave 4 / A4: reject blocker create on closed/on_hold projects.
    Walks thread -> wave -> project."""
    thread = await db["pm_threads"].find_one(
        {"thread_id": thread_id, "tenant_id": tenant_id},
        {"_id": 0, "wave_id": 1},
    )
    if not thread:
        return
    wave = await db["pm_waves"].find_one(
        {"wave_id": thread.get("wave_id"), "tenant_id": tenant_id},
        {"_id": 0, "project_id": 1},
    )
    if not wave:
        return
    proj = await db["projects"].find_one(
        {"project_id": wave.get("project_id"), "tenant_id": tenant_id},
        {"_id": 0, "status": 1},
    )
    status = (proj or {}).get("status")
    if status in _FROZEN_PROJECT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is {status}. Cannot create new blocker tickets. "
                "Reopen the project first."
            ),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/blocker-tickets",
    dependencies=[Depends(require_ai_permission("projects.blocker_ticket.view"))],
)
async def list_blocker_tickets(
    thread_id: Optional[str] = Query(None),
    target_thread_id: Optional[str] = Query(None),
    target_module: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    wave_id: Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.blocker_ticket.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    # Phase 1E.3c — every query starts tenant-scoped.
    query: dict = {"tenant_id": tenant_id}
    if thread_id:
        query["thread_id"] = thread_id
    if target_thread_id:
        query["target_thread_id"] = target_thread_id
    if target_module:
        query["target_module"] = target_module
    if status:
        if status not in BLOCKER_STATUS_ENUM:
            raise HTTPException(status_code=400, detail=f"Invalid status; must be one of {sorted(BLOCKER_STATUS_ENUM)}")
        query["status"] = status

    # Wave 4 / A3: resolve `?wave_id=` by looking up which threads belong to
    # the wave, then filtering blockers whose thread_id is in that set.
    # Blockers don't carry wave_id directly (it's on the parent thread).
    if wave_id:
        thread_ids_in_wave = [
            d["thread_id"]
            async for d in db["pm_threads"].find(
                {"wave_id": wave_id, "tenant_id": tenant_id},
                {"_id": 0, "thread_id": 1},
            )
        ]
        if not thread_ids_in_wave:
            return {"items": [], "total": 0, "skip": skip, "limit": limit}
        # AND with any caller-supplied thread_id filter (intersection only).
        if "thread_id" in query:
            if query["thread_id"] not in thread_ids_in_wave:
                return {"items": [], "total": 0, "skip": skip, "limit": limit}
        else:
            query["thread_id"] = {"$in": thread_ids_in_wave}

    total = await db["pm_blocker_tickets"].count_documents(query)
    items = (
        await db["pm_blocker_tickets"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get(
    "/blocker-tickets/{blocker_ticket_id}",
    dependencies=[Depends(require_ai_permission("projects.blocker_ticket.view"))],
)
async def get_blocker_ticket(
    blocker_ticket_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.blocker_ticket.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    doc = await db["pm_blocker_tickets"].find_one(
        {"blocker_ticket_id": blocker_ticket_id, "tenant_id": tenant_id},
        {"_id": 0},
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="BlockerTicket not found")
    return doc


@router.post(
    "/blocker-tickets",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.blocker_ticket.create"))],
)
async def create_blocker_ticket(
    payload: BlockerTicketCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.blocker_ticket.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    await _ensure_thread_exists(db, payload.thread_id, "thread_id", tenant_id)
    await _refuse_if_thread_project_frozen(db, payload.thread_id, tenant_id)
    if payload.target_thread_id is not None:
        await _ensure_thread_exists(
            db, payload.target_thread_id, "target_thread_id", tenant_id
        )

    now = _now_iso()
    doc = {
        "blocker_ticket_id": gen_id("blocker_"),
        # Phase 1E.3c — stamp owning tenant.
        "tenant_id": tenant_id,
        "thread_id": payload.thread_id,
        "target_module": payload.target_module.strip(),
        "target_thread_id": payload.target_thread_id,
        "description": payload.description,
        "status": "open",
        "resolved_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects", entity="blocker_ticket", action="created",
        resource_id=doc["blocker_ticket_id"], current_user=current_user, db=db, request=request,
    ) as ctx:
        await db["pm_blocker_tickets"].insert_one(doc)
        doc.pop("_id", None)
        ctx.set_after(doc)

    return doc


@router.patch(
    "/blocker-tickets/{blocker_ticket_id}",
    dependencies=[Depends(require_ai_permission("projects.blocker_ticket.edit"))],
)
async def update_blocker_ticket(
    blocker_ticket_id: str,
    payload: BlockerTicketUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.blocker_ticket.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_blocker_tickets"].find_one(
        {"blocker_ticket_id": blocker_ticket_id, "tenant_id": tenant_id},
        {"_id": 0},
    )
    if before is None:
        raise HTTPException(status_code=404, detail="BlockerTicket not found")

    dumped = payload.model_dump(exclude_unset=True)

    # Phase 1E.3c — defensive: drop tenant_id from update payload so a
    # client can never reparent a blocker to another tenant via PATCH.
    dumped.pop("tenant_id", None)

    if "status" in dumped and dumped["status"] is not None:
        new_status = dumped["status"]
        if new_status not in BLOCKER_STATUS_ENUM:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status; must be one of {sorted(BLOCKER_STATUS_ENUM)}",
            )
        _check_transition(before["status"], new_status)

        # resolved_at lifecycle
        if new_status == "resolved" and before["status"] != "resolved":
            dumped["resolved_at"] = _now_iso()
        elif before["status"] == "resolved" and new_status != "resolved":
            dumped["resolved_at"] = None

    if "target_thread_id" in dumped and dumped["target_thread_id"] is not None:
        await _ensure_thread_exists(
            db, dumped["target_thread_id"], "target_thread_id", tenant_id
        )

    if "target_module" in dumped and isinstance(dumped["target_module"], str):
        dumped["target_module"] = dumped["target_module"].strip()

    dumped["updated_at"] = _now_iso()

    async with crud_envelope(
        module="projects", entity="blocker_ticket", action="updated",
        resource_id=blocker_ticket_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_blocker_tickets"].update_one(
            {"blocker_ticket_id": blocker_ticket_id, "tenant_id": tenant_id},
            {"$set": dumped},
        )
        updated = await db["pm_blocker_tickets"].find_one(
            {"blocker_ticket_id": blocker_ticket_id, "tenant_id": tenant_id},
            {"_id": 0},
        )
        ctx.set_after(updated)

    if before.get("status") != updated.get("status"):
        async with crud_envelope(
            module="projects", entity="blocker_ticket", action="status_changed",
            resource_id=blocker_ticket_id, current_user=current_user, db=db,
            before=before, request=request,
        ) as ctx:
            ctx.set_after(updated)

    return updated


@router.delete(
    "/blocker-tickets/{blocker_ticket_id}",
    dependencies=[Depends(require_ai_permission("projects.blocker_ticket.delete"))],
)
async def delete_blocker_ticket(
    blocker_ticket_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.blocker_ticket.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_blocker_tickets"].find_one(
        {"blocker_ticket_id": blocker_ticket_id, "tenant_id": tenant_id},
        {"_id": 0},
    )
    if before is None:
        raise HTTPException(status_code=404, detail="BlockerTicket not found")

    async with crud_envelope(
        module="projects", entity="blocker_ticket", action="deleted",
        resource_id=blocker_ticket_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_blocker_tickets"].delete_one(
            {"blocker_ticket_id": blocker_ticket_id, "tenant_id": tenant_id}
        )
        ctx.set_after(None)

    return {"ok": True, "deleted_id": blocker_ticket_id}
