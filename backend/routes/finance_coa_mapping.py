"""Financial Command Center — Chart of Account Mapping routes.

Adapted from Ryan-Finance-App `backend/server.py` (CoAMapping section).
Differences from source:
  * UUID ids replaced with AEGISAXIOM gen_id("fcc_coa_") prefix.
  * Persistence uses `db["finance_coa_mappings"]` per the finance.json mapping file.
  * State changes routed through crud_envelope for audit/policy/events.
  * internal_kind enum validated at create time (income | expense).
  * Permission model: finance.coa_mapping.{view,create,edit,delete}.

Wave-1 carry-forward fix: entity name registered as "coa_mapping" (NOT "co_a_mapping").
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
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
INTERNAL_KIND_OPTIONS = [
    {"value": "income", "label": "Income"},
    {"value": "expense", "label": "Expense"},
]

register_module_config("finance", "finance_coa_mappings", "coa_mapping_id", "Finance")
register_module_events("finance", "coa_mapping", ["created", "updated", "deleted"])
register_fields(
    "finance",
    "coa_mapping",
    [
        {
            "name": "coa_mapping_id",
            "type": "string",
            "label": "COA Mapping ID",
            "description": "Primary key — fcc_coa_ prefix.",
        },
        {
            "name": "internal_item",
            "type": "string",
            "label": "Internal Item",
            "description": "AEGISAXIOM-side item key being mapped to a QBO account.",
        },
        {
            "name": "internal_kind",
            "type": "string",
            "label": "Internal Kind",
            "options": INTERNAL_KIND_OPTIONS,
            "description": "Whether this item maps to an income or expense account.",
        },
        {
            "name": "qbo_account_id",
            "type": "string",
            "label": "QBO Account ID",
            "description": "QBO Account ID (string) the item maps to.",
        },
        {
            "name": "qbo_account_name",
            "type": "string",
            "label": "QBO Account Name",
            "description": "Display name of the mapped QBO account.",
        },
        {
            "name": "auto_apply",
            "type": "boolean",
            "label": "Auto Apply",
            "description": "When true, this mapping auto-applies during draft creation.",
        },
        {"name": "created_at", "type": "datetime", "label": "Created At"},
        {"name": "updated_at", "type": "datetime", "label": "Updated At"},
        {"name": "created_by", "type": "string", "label": "Created By (user_id)"},
    ],
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
INTERNAL_KINDS = {"income", "expense"}


# ---------------------------------------------------------------------------
# Pydantic DTOs
# ---------------------------------------------------------------------------
class CoaMappingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal_item: str = Field(min_length=1)
    internal_kind: str
    qbo_account_id: str = Field(min_length=1)
    qbo_account_name: str = Field(min_length=1)
    auto_apply: bool = True


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/coa-mappings",
    dependencies=[Depends(require_ai_permission("finance.coa_mapping.view"))],
)
async def list_coa_mappings(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    internal_kind: Optional[str] = Query(None),
    q: Optional[str] = Query(None, min_length=1),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.coa_mapping.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()
    query: Dict[str, Any] = {}
    if internal_kind:
        query["internal_kind"] = internal_kind
    if q:
        query["$or"] = [
            {"internal_item": {"$regex": q, "$options": "i"}},
            {"qbo_account_name": {"$regex": q, "$options": "i"}},
        ]
    total = await db["finance_coa_mappings"].count_documents(query)
    items = (
        await db["finance_coa_mappings"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"rows": items, "total": total}


@router.post(
    "/coa-mappings",
    status_code=201,
    dependencies=[Depends(require_ai_permission("finance.coa_mapping.create"))],
)
async def create_coa_mapping(
    payload: CoaMappingCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.coa_mapping.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    if payload.internal_kind not in INTERNAL_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"internal_kind must be one of {sorted(INTERNAL_KINDS)} (got {payload.internal_kind!r})",
        )
    db = get_db()

    now_iso = _utc_now_iso()
    coa_mapping_id = gen_id("fcc_coa_")
    doc: Dict[str, Any] = {
        "coa_mapping_id": coa_mapping_id,
        "internal_item": payload.internal_item,
        "internal_kind": payload.internal_kind,
        "qbo_account_id": payload.qbo_account_id,
        "qbo_account_name": payload.qbo_account_name,
        "auto_apply": payload.auto_apply,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="finance",
        entity="coa_mapping",
        action="created",
        resource_id=coa_mapping_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["finance_coa_mappings"].insert_one(doc)
        ctx.set_after(_scrub(doc))

    return _scrub(doc)


@router.delete(
    "/coa-mappings/{coa_mapping_id}",
    status_code=204,
    dependencies=[Depends(require_ai_permission("finance.coa_mapping.delete"))],
)
async def delete_coa_mapping(
    coa_mapping_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.coa_mapping.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    before = await db["finance_coa_mappings"].find_one(
        {"coa_mapping_id": coa_mapping_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="COA mapping not found")

    async with crud_envelope(
        module="finance",
        entity="coa_mapping",
        action="deleted",
        resource_id=coa_mapping_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["finance_coa_mappings"].delete_one({"coa_mapping_id": coa_mapping_id})
        ctx.set_after(None)
