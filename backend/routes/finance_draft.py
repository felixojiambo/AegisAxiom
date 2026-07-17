"""Financial Command Center — Draft (AR Invoice / AP Bill) routes.

Adapted from Ryan-Finance-App `backend/server.py` (DraftDoc / DraftCreate section).
Differences from source:
  * UUID ids replaced with AEGISAXIOM gen_id("fcc_draft_") prefix.
  * Persistence uses `db["finance_drafts"]` per the finance.json mapping file.
  * State changes routed through crud_envelope for audit/policy/events.
  * `kind` is immutable after create; validated at creation time.
  * `sync_status` transitions trigger a secondary crud_envelope("status_changed").
  * Mocked sync endpoint sets sync_status=Synced and stamps qbo_id with a
    mock "SYNC-<random_hex>" id. (The qbo_id key is a legacy finance_drafts
    schema field still read by the Cashflow Planner adapter; kept for schema
    compatibility after the external-accounting integration was removed.)
  * `source_origin` defaults to "manual" — auto-draft endpoints (auto-generate-ar /
    auto-generate-ap) are Thread 2D's responsibility and NOT in this file.
  * Legacy /sync-queue alias returns drafts with sync_status in {Draft, Sent}.
  * Permission model: finance.draft.{view,create,edit,delete,sync}.

Wave-1 carry-forward fixes applied:
  * entity name is "draft" (not a split DraftDoc/DraftCreate at the DB layer).
  * All enum options declared in register_fields with `options` key.
  * sync_status change detected in update handler and emitted as separate envelope.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from models import gen_id
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Field / event / module registration
# ---------------------------------------------------------------------------
KIND_OPTIONS = [
    {"value": "ar_invoice", "label": "AR Invoice"},
    {"value": "ap_bill", "label": "AP Bill"},
]
SYNC_STATUS_OPTIONS = [
    {"value": "Draft", "label": "Draft"},
    {"value": "Sent", "label": "Sent"},
    {"value": "Synced", "label": "Synced"},
    {"value": "Failed", "label": "Failed"},
]
SOURCE_ORIGIN_OPTIONS = [
    {"value": "manual", "label": "Manual"},
    {"value": "auto_time_entries", "label": "Auto (Time Entries)"},
]

register_module_config("finance", "finance_drafts", "draft_id", "Finance")
register_module_events(
    "finance", "draft", ["created", "updated", "synced", "sync_failed", "deleted"]
)
register_fields(
    "finance",
    "draft",
    [
        {
            "name": "draft_id",
            "type": "string",
            "label": "Draft ID",
            "description": "Primary key — fcc_draft_ prefix.",
        },
        {
            "name": "kind",
            "type": "string",
            "label": "Kind",
            "options": KIND_OPTIONS,
            "description": "ar_invoice or ap_bill — immutable after create.",
        },
        {
            "name": "counterparty",
            "type": "string",
            "label": "Counterparty",
            "description": "Customer (for ar_invoice) or vendor (for ap_bill) display name.",
        },
        {
            "name": "counterparty_id",
            "type": "string",
            "label": "Counterparty ID",
            "description": "Optional FK to AEGISAXIOM account_id or vendor record.",
        },
        {"name": "amount", "type": "number", "label": "Amount"},
        {"name": "memo", "type": "string", "label": "Memo"},
        {"name": "line_items", "type": "array", "label": "Line Items"},
        {
            "name": "sync_status",
            "type": "string",
            "label": "Sync Status",
            "options": SYNC_STATUS_OPTIONS,
            "description": "Sync lifecycle: Draft → Sent → Synced | Failed.",
        },
        {
            "name": "qbo_id",
            "type": "string",
            "label": "Sync ID",
            "description": (
                "Set when sync_status=Synced. Mocked. Legacy finance_drafts "
                "schema field retained for Cashflow Planner compatibility."
            ),
        },
        {
            "name": "error",
            "type": "string",
            "label": "Error",
            "description": "Failure detail when sync_status=Failed.",
        },
        {
            "name": "source_origin",
            "type": "string",
            "label": "Source Origin",
            "options": SOURCE_ORIGIN_OPTIONS,
            "description": "manual = entered by hand; auto_time_entries = generated from time-entry auto-flow.",
        },
        {
            "name": "source_period_start",
            "type": "date",
            "label": "Source Period Start",
            "description": "For auto_time_entries drafts: time-entry period start.",
        },
        {"name": "source_period_end", "type": "date", "label": "Source Period End"},
        {
            "name": "source_contractor_id",
            "type": "string",
            "label": "Source Contractor ID",
            "description": "For ap_bill auto drafts grouped by contractor.",
        },
        {
            "name": "source_contract_id",
            "type": "string",
            "label": "Source Contract ID",
            "description": "For ar_invoice auto drafts grouped by client contract.",
        },
        {
            "name": "due_date",
            "type": "date",
            "label": "Due Date",
            "description": "Optional due date for the draft. When set, Nova snapshot uses this for AR/AP aging buckets.",
        },
        {"name": "created_at", "type": "datetime", "label": "Created At"},
        {"name": "updated_at", "type": "datetime", "label": "Updated At"},
        {"name": "created_by", "type": "string", "label": "Created By (user_id)"},
    ],
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
KINDS = {"ar_invoice", "ap_bill"}
SYNC_STATUSES = {"Draft", "Sent", "Synced", "Failed"}
SOURCE_ORIGINS = {"manual", "auto_time_entries"}
SYNC_QUEUE_STATUSES = {"Draft", "Sent"}


# ---------------------------------------------------------------------------
# Pydantic DTOs
# ---------------------------------------------------------------------------
class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    counterparty: str = Field(min_length=1)
    counterparty_id: Optional[str] = None
    amount: float
    memo: Optional[str] = None
    line_items: List[Dict[str, Any]] = Field(default_factory=list)
    sync_status: str = "Draft"
    due_date: Optional[date] = None
    source_origin: str = "manual"
    source_period_start: Optional[str] = None
    source_period_end: Optional[str] = None
    source_contractor_id: Optional[str] = None
    source_contract_id: Optional[str] = None


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counterparty: Optional[str] = Field(default=None, min_length=1)
    counterparty_id: Optional[str] = None
    amount: Optional[float] = None
    memo: Optional[str] = None
    line_items: Optional[List[Dict[str, Any]]] = None
    sync_status: Optional[str] = None
    error: Optional[str] = None
    due_date: Optional[date] = None
    source_period_start: Optional[str] = None
    source_period_end: Optional[str] = None
    source_contractor_id: Optional[str] = None
    source_contract_id: Optional[str] = None
    # `kind` is intentionally absent — immutable after create.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scrub(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    out = {**doc}
    out.pop("_id", None)
    return out


def _user_dict(current_user: Any) -> dict:
    return current_user.model_dump() if hasattr(current_user, "model_dump") else current_user


def _mock_sync_id() -> str:
    return "SYNC-" + uuid.uuid4().hex[:8].upper()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/drafts",
    dependencies=[Depends(require_ai_permission("finance.draft.view"))],
)
async def list_drafts(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    kind: Optional[str] = Query(None),
    sync_status: Optional[str] = Query(None),
    source_origin: Optional[str] = Query(None),
    q: Optional[str] = Query(None, min_length=1),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.draft.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()
    query: Dict[str, Any] = {}
    if kind:
        query["kind"] = kind
    if sync_status:
        query["sync_status"] = sync_status
    if source_origin:
        query["source_origin"] = source_origin
    if q:
        query["counterparty"] = {"$regex": q, "$options": "i"}
    total = await db["finance_drafts"].count_documents(query)
    items = (
        await db["finance_drafts"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"rows": items, "total": total}


@router.get(
    "/sync-queue",
    dependencies=[Depends(require_ai_permission("finance.draft.view"))],
)
async def list_sync_queue(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    kind: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Legacy alias: returns drafts with sync_status in {Draft, Sent}."""
    if not has_perm(_user_dict(current_user), "finance.draft.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()
    query: Dict[str, Any] = {"sync_status": {"$in": list(SYNC_QUEUE_STATUSES)}}
    if kind:
        query["kind"] = kind
    total = await db["finance_drafts"].count_documents(query)
    items = (
        await db["finance_drafts"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"rows": items, "total": total}


@router.get(
    "/drafts/{draft_id}",
    dependencies=[Depends(require_ai_permission("finance.draft.view"))],
)
async def get_draft(
    draft_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.draft.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()
    doc = await db["finance_drafts"].find_one({"draft_id": draft_id}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return doc


@router.post(
    "/drafts",
    status_code=201,
    dependencies=[Depends(require_ai_permission("finance.draft.create"))],
)
async def create_draft(
    payload: DraftCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.draft.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    if payload.kind not in KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {sorted(KINDS)} (got {payload.kind!r})",
        )
    if payload.sync_status not in SYNC_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"sync_status must be one of {sorted(SYNC_STATUSES)} (got {payload.sync_status!r})",
        )
    if payload.source_origin not in SOURCE_ORIGINS:
        raise HTTPException(
            status_code=422,
            detail=f"source_origin must be one of {sorted(SOURCE_ORIGINS)} (got {payload.source_origin!r})",
        )
    db = get_db()

    now_iso = _utc_now_iso()
    draft_id = gen_id("fcc_draft_")
    doc: Dict[str, Any] = {
        "draft_id": draft_id,
        "kind": payload.kind,
        "counterparty": payload.counterparty,
        "counterparty_id": payload.counterparty_id,
        "amount": payload.amount,
        "memo": payload.memo,
        "line_items": list(payload.line_items),
        "sync_status": payload.sync_status,
        "due_date": payload.due_date.isoformat() if payload.due_date else None,
        "qbo_id": None,
        "error": None,
        "source_origin": payload.source_origin,
        "source_period_start": payload.source_period_start,
        "source_period_end": payload.source_period_end,
        "source_contractor_id": payload.source_contractor_id,
        "source_contract_id": payload.source_contract_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="finance",
        entity="draft",
        action="created",
        resource_id=draft_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["finance_drafts"].insert_one(doc)
        ctx.set_after(_scrub(doc))

    return _scrub(doc)


@router.patch(
    "/drafts/{draft_id}",
    dependencies=[Depends(require_ai_permission("finance.draft.edit"))],
)
async def update_draft(
    draft_id: str,
    payload: DraftUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.draft.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    before = await db["finance_drafts"].find_one({"draft_id": draft_id}, {"_id": 0})
    if before is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    updates = payload.model_dump(exclude_unset=True)

    if "sync_status" in updates and updates["sync_status"] not in SYNC_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"sync_status must be one of {sorted(SYNC_STATUSES)} (got {updates['sync_status']!r})",
        )

    updates["updated_at"] = _utc_now_iso()
    updated = {**before, **updates}

    async with crud_envelope(
        module="finance",
        entity="draft",
        action="updated",
        resource_id=draft_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["finance_drafts"].replace_one({"draft_id": draft_id}, updated)
        ctx.set_after(_scrub(updated))

    # Detect sync_status transition and emit a second envelope.
    if before.get("sync_status") != updated.get("sync_status"):
        async with crud_envelope(
            module="finance",
            entity="draft",
            action="status_changed",
            resource_id=draft_id,
            current_user=current_user,
            db=db,
            before=before,
            request=request,
        ) as ctx2:
            ctx2.set_after(_scrub(updated))

    return _scrub(updated)


@router.delete(
    "/drafts/{draft_id}",
    status_code=204,
    dependencies=[Depends(require_ai_permission("finance.draft.delete"))],
)
async def delete_draft(
    draft_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.draft.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    before = await db["finance_drafts"].find_one({"draft_id": draft_id}, {"_id": 0})
    if before is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    async with crud_envelope(
        module="finance",
        entity="draft",
        action="deleted",
        resource_id=draft_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["finance_drafts"].delete_one({"draft_id": draft_id})
        ctx.set_after(None)


@router.post(
    "/drafts/{draft_id}/sync",
    dependencies=[Depends(require_ai_permission("finance.draft.sync"))],
)
async def sync_draft(
    draft_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Mocked sync. Sets sync_status=Synced and stamps a mock SYNC-XXXXXXXX id.

    Returns 409 if the draft is already Synced. A real external-accounting push
    is deferred to a later phase (the direct integration was removed).
    """
    if not has_perm(_user_dict(current_user), "finance.draft.sync"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    before = await db["finance_drafts"].find_one({"draft_id": draft_id}, {"_id": 0})
    if before is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    if before.get("sync_status") == "Synced":
        raise HTTPException(status_code=409, detail="Draft is already synced")

    updated = {
        **before,
        "sync_status": "Synced",
        "qbo_id": _mock_sync_id(),
        "error": None,
        "updated_at": _utc_now_iso(),
    }

    async with crud_envelope(
        module="finance",
        entity="draft",
        action="synced",
        resource_id=draft_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["finance_drafts"].replace_one({"draft_id": draft_id}, updated)
        ctx.set_after(_scrub(updated))

    # Sync is always a status change — emit the secondary envelope.
    async with crud_envelope(
        module="finance",
        entity="draft",
        action="status_changed",
        resource_id=draft_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx2:
        ctx2.set_after(_scrub(updated))

    return _scrub(updated)
