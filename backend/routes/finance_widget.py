"""Financial Command Center — Workspace Widget routes.

Adapted from Ryan-Finance-App `backend/server.py` (WidgetCreate / WidgetUpdate section).
Differences from source:
  * UUID ids replaced with AEGISAXIOM gen_id("fcc_widget_") prefix.
  * Persistence uses `db["finance_widgets"]` per the finance.json mapping file.
  * State changes routed through crud_envelope for audit/policy/events.
  * `size` enum validated: small | medium | large | full.
  * Reorder endpoint accepts {order: [widget_id, ...]} and rewrites positions
    atomically via bulk_write.
  * FK validation: dashboard must exist before creating a widget under it.
  * Permission model: finance.widget.{view,create,edit,delete}.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo import UpdateOne
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
SIZE_OPTIONS = [
    {"value": "small", "label": "Small"},
    {"value": "medium", "label": "Medium"},
    {"value": "large", "label": "Large"},
    {"value": "full", "label": "Full"},
]

register_module_config("finance", "finance_widgets", "widget_id", "Finance")
register_module_events("finance", "widget", ["created", "updated", "deleted", "reordered"])
register_fields(
    "finance",
    "widget",
    [
        {
            "name": "widget_id",
            "type": "string",
            "label": "Widget ID",
            "description": "Primary key — fcc_widget_ prefix.",
        },
        {
            "name": "dashboard_id",
            "type": "string",
            "label": "Dashboard ID",
            "description": "FK to finance_dashboards.dashboard_id.",
        },
        {
            "name": "widget_type",
            "type": "string",
            "label": "Widget Type",
            "description": "Matches an entry in WIDGET_CATALOG (frontend/src/lib/financeWidgets.jsx).",
        },
        {
            "name": "size",
            "type": "string",
            "label": "Size",
            "options": SIZE_OPTIONS,
            "description": "Display size: small | medium | large | full.",
        },
        {
            "name": "position",
            "type": "integer",
            "label": "Position",
            "description": "Display order within the dashboard.",
        },
        {
            "name": "config",
            "type": "object",
            "label": "Config",
            "description": "Per-widget settings (customer_id filter, date range, chart options, etc.).",
        },
        {"name": "created_at", "type": "datetime", "label": "Created At"},
        {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    ],
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SIZES = {"small", "medium", "large", "full"}


# ---------------------------------------------------------------------------
# Pydantic DTOs
# ---------------------------------------------------------------------------
class WidgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    widget_type: str = Field(min_length=1)
    size: str = "small"
    position: int = 0
    config: Dict[str, Any] = Field(default_factory=dict)


class WidgetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: Optional[str] = None
    position: Optional[int] = None
    config: Optional[Dict[str, Any]] = None


class WidgetReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: List[str] = Field(description="Ordered list of widget_ids for this dashboard.")


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


def _alias(doc: Optional[dict]) -> Optional[dict]:
    """Add an `id` alias to a widget doc for the frontend UI contract."""
    if doc is None:
        return None
    out = _scrub(doc) or {}
    out["id"] = out.get("widget_id")
    return out


def _user_dict(current_user: Any) -> dict:
    return current_user.model_dump() if hasattr(current_user, "model_dump") else current_user


async def _get_dashboard(db: Any, dashboard_id: str) -> Optional[dict]:
    return await db["finance_dashboards"].find_one(
        {"dashboard_id": dashboard_id}, {"_id": 0}
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/workspace/dashboards/{dashboard_id}/widgets",
    status_code=201,
    dependencies=[Depends(require_ai_permission("finance.widget.create"))],
)
async def create_widget(
    dashboard_id: str,
    payload: WidgetCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.widget.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    if payload.size not in SIZES:
        raise HTTPException(
            status_code=422,
            detail=f"size must be one of {sorted(SIZES)} (got {payload.size!r})",
        )
    db = get_db()

    dashboard = await _get_dashboard(db, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if dashboard.get("owner_user_id") != current_user.user_id and not has_perm(
        _user_dict(current_user), "admin.manage"
    ):
        raise HTTPException(status_code=403, detail="You can only modify widgets on your own dashboards")

    now_iso = _utc_now_iso()
    widget_id = gen_id("fcc_widget_")
    doc: Dict[str, Any] = {
        "widget_id": widget_id,
        "dashboard_id": dashboard_id,
        "widget_type": payload.widget_type,
        "size": payload.size,
        "position": payload.position,
        "config": dict(payload.config),
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    async with crud_envelope(
        module="finance",
        entity="widget",
        action="created",
        resource_id=widget_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["finance_widgets"].insert_one(doc)
        ctx.set_after(_scrub(doc))

    return _alias(doc)


@router.patch(
    "/workspace/dashboards/{dashboard_id}/widgets/{widget_id}",
    dependencies=[Depends(require_ai_permission("finance.widget.edit"))],
)
async def update_widget(
    dashboard_id: str,
    widget_id: str,
    payload: WidgetUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.widget.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    dashboard = await _get_dashboard(db, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if dashboard.get("owner_user_id") != current_user.user_id and not has_perm(
        _user_dict(current_user), "admin.manage"
    ):
        raise HTTPException(status_code=403, detail="You can only modify widgets on your own dashboards")

    before = await db["finance_widgets"].find_one(
        {"widget_id": widget_id, "dashboard_id": dashboard_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Widget not found")

    updates = payload.model_dump(exclude_unset=True)
    if "size" in updates and updates["size"] not in SIZES:
        raise HTTPException(
            status_code=422,
            detail=f"size must be one of {sorted(SIZES)} (got {updates['size']!r})",
        )

    updates["updated_at"] = _utc_now_iso()
    updated = {**before, **updates}

    async with crud_envelope(
        module="finance",
        entity="widget",
        action="updated",
        resource_id=widget_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["finance_widgets"].replace_one(
            {"widget_id": widget_id, "dashboard_id": dashboard_id}, updated
        )
        ctx.set_after(_scrub(updated))

    return _alias(updated)


@router.delete(
    "/workspace/dashboards/{dashboard_id}/widgets/{widget_id}",
    status_code=204,
    dependencies=[Depends(require_ai_permission("finance.widget.delete"))],
)
async def delete_widget(
    dashboard_id: str,
    widget_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.widget.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    dashboard = await _get_dashboard(db, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if dashboard.get("owner_user_id") != current_user.user_id and not has_perm(
        _user_dict(current_user), "admin.manage"
    ):
        raise HTTPException(status_code=403, detail="You can only modify widgets on your own dashboards")

    before = await db["finance_widgets"].find_one(
        {"widget_id": widget_id, "dashboard_id": dashboard_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Widget not found")

    async with crud_envelope(
        module="finance",
        entity="widget",
        action="deleted",
        resource_id=widget_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["finance_widgets"].delete_one(
            {"widget_id": widget_id, "dashboard_id": dashboard_id}
        )
        ctx.set_after(None)


@router.post(
    "/workspace/dashboards/{dashboard_id}/widgets/reorder",
    dependencies=[Depends(require_ai_permission("finance.widget.edit"))],
)
async def reorder_widgets(
    dashboard_id: str,
    payload: WidgetReorder,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Rewrite widget positions for a dashboard.

    Accepts `{order: [widget_id, ...]}` — each widget_id gets its `position`
    set to its index in the list. Only widgets that belong to `dashboard_id`
    are affected. Unknown widget_ids are silently skipped.
    """
    if not has_perm(_user_dict(current_user), "finance.widget.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    dashboard = await _get_dashboard(db, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if dashboard.get("owner_user_id") != current_user.user_id and not has_perm(
        _user_dict(current_user), "admin.manage"
    ):
        raise HTTPException(status_code=403, detail="You can only modify widgets on your own dashboards")

    if not payload.order:
        return {"reordered": 0}

    now_iso = _utc_now_iso()
    ops = [
        UpdateOne(
            {"widget_id": wid, "dashboard_id": dashboard_id},
            {"$set": {"position": idx, "updated_at": now_iso}},
        )
        for idx, wid in enumerate(payload.order)
    ]

    result = await db["finance_widgets"].bulk_write(ops, ordered=False)

    # Emit a single reorder event carrying the new order as the after-state.
    async with crud_envelope(
        module="finance",
        entity="widget",
        action="reordered",
        resource_id=dashboard_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        ctx.set_after({"dashboard_id": dashboard_id, "order": payload.order})

    return {"reordered": result.modified_count}
