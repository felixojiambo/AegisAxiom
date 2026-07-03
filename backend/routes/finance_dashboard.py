"""Financial Command Center — Workspace Dashboard routes.

Adapted from Ryan-Finance-App `backend/server.py` (DashboardCreate / DashboardUpdate section).
Differences from source:
  * UUID ids replaced with AEGISAXIOM gen_id("fcc_dash_") prefix.
  * Persistence uses `db["finance_dashboards"]` per the finance.json mapping file.
  * State changes routed through crud_envelope for audit/policy/events.
  * Dashboards are per-user: list filters by owner_user_id == current_user.user_id.
  * Permission model: finance.dashboard.{view,create,edit,delete}.
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
register_module_config("finance", "finance_dashboards", "dashboard_id", "Finance")
register_module_events("finance", "dashboard", ["created", "updated", "deleted"])
register_fields(
    "finance",
    "dashboard",
    [
        {
            "name": "dashboard_id",
            "type": "string",
            "label": "Dashboard ID",
            "description": "Primary key — fcc_dash_ prefix.",
        },
        {"name": "name", "type": "string", "label": "Name"},
        {
            "name": "order",
            "type": "integer",
            "label": "Order",
            "description": "Display order in the workspace tab list.",
        },
        {
            "name": "filters",
            "type": "object",
            "label": "Filters",
            "description": "Optional global filters (period, location, class) applied to all widgets.",
        },
        {
            "name": "owner_user_id",
            "type": "string",
            "label": "Owner User ID",
            "description": "Workspace dashboards are per-user.",
        },
        {"name": "created_at", "type": "datetime", "label": "Created At"},
        {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    ],
)


# ---------------------------------------------------------------------------
# Pydantic DTOs
# ---------------------------------------------------------------------------
class DashboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    order: int = 0
    filters: Dict[str, Any] = Field(default_factory=dict)


class DashboardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1)
    order: Optional[int] = None
    filters: Optional[Dict[str, Any]] = None


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


def _alias_widget(w: dict) -> dict:
    """Add an `id` alias to a widget doc for the frontend (which keys by `w.id`).

    Routes through ``_scrub`` so this stays in lockstep with the
    ``_alias`` helper in ``routes/finance_widget.py``. The two paths
    used to do the ``_id`` strip via parallel inline expressions; if
    ``_scrub`` ever grows extra sanitization (e.g. dropping internal
    bookkeeping fields), keeping both call sites on it means the
    sanitization applies to the dashboard list endpoint too without
    a separate fix.
    """
    out = _scrub(w) or {}
    out["id"] = out.get("widget_id")
    return out


def _alias_dashboard(d: dict, widgets: Optional[List[dict]] = None) -> dict:
    """Add an `id` alias and embed widgets so the frontend can render the grid.

    The frontend (frontend/src/components/finance/WorkspaceTab.js) reads
    `d.id` for keys/URLs and `d.widgets` for the grid; the canonical fields
    `dashboard_id` and the separate finance_widgets collection are kept,
    these are additive aliases for the UI contract.
    """
    # Reuse _scrub so the _id strip rule lives in one place; the
    # earlier hand-rolled comprehension was a redundant copy.
    out = _scrub(d) or {}
    out["id"] = out.get("dashboard_id")
    if widgets is not None:
        out["widgets"] = widgets
    return out


# Per-dashboard widget cap. The previous to_list(length=10000) silently
# truncated when a dashboard grew past 10k widgets — that's a hard
# product invariant violation (no dashboard should ever carry that many
# widgets) and the silent cut is what makes it dangerous. 500 per
# dashboard is well above the realistic upper bound and small enough
# that an obvious page-of-truncation log line surfaces the misuse
# instead of silently dropping rows.
_MAX_WIDGETS_PER_DASHBOARD = 500


async def _load_widgets_for(db: Any, dashboard_ids: List[str]) -> Dict[str, List[dict]]:
    """Fetch widgets for a set of dashboards in one query, grouped by dashboard_id."""
    if not dashboard_ids:
        return {}
    dashboard_ids_list = list(dashboard_ids)
    # The cap is per-dashboard, not per-batch. The previous
    # implementation pre-computed ``cap = 500 * n_dashboards`` and
    # warned only when the BATCH crossed that total — meaning a
    # 2-dashboard request with one dashboard at 600 widgets and the
    # other at 50 sat under the 1000 batch cap and never warned.
    #
    # Strategy: do a single bulk read under a generous batch ceiling
    # of ``(cap + 1) * n_dashboards`` so under-the-cap dashboards
    # always come back complete. If the ceiling itself is reached,
    # we may have undercounted SOME dashboards, so we re-fetch each
    # group that came back exactly at ``cap + 1`` to confirm whether
    # it actually overflows. Bulk-then-confirm keeps the common path
    # at one query while making the warning accurate per dashboard.
    batch_ceiling = (_MAX_WIDGETS_PER_DASHBOARD + 1) * max(
        len(dashboard_ids_list), 1
    )
    cursor = db["finance_widgets"].find(
        {"dashboard_id": {"$in": dashboard_ids_list}},
        {"_id": 0},
    ).sort("position", 1).limit(batch_ceiling)
    rows = await cursor.to_list(length=batch_ceiling)

    grouped: Dict[str, List[dict]] = {}
    for w in rows:
        grouped.setdefault(w.get("dashboard_id"), []).append(w)

    # Per-dashboard cap with a loud warning when any single dashboard
    # crosses the threshold.
    capped: Dict[str, List[dict]] = {}
    batch_was_truncated = len(rows) >= batch_ceiling
    for dashboard_id in dashboard_ids_list:
        widgets = grouped.get(dashboard_id, [])
        # If the batch hit its ceiling, an under-cap-looking group
        # might actually be an over-cap dashboard that just lost rows
        # to other dashboards in the bulk read. Re-fetch with a
        # per-dashboard limit of cap+1 to get a definitive count.
        if batch_was_truncated and len(widgets) <= _MAX_WIDGETS_PER_DASHBOARD:
            confirm_cursor = db["finance_widgets"].find(
                {"dashboard_id": dashboard_id},
                {"_id": 0},
            ).sort("position", 1).limit(_MAX_WIDGETS_PER_DASHBOARD + 1)
            widgets = await confirm_cursor.to_list(
                length=_MAX_WIDGETS_PER_DASHBOARD + 1
            )
        if len(widgets) > _MAX_WIDGETS_PER_DASHBOARD:
            logger.warning(
                "_load_widgets_for: dashboard %r holds %s+ widgets, "
                "exceeding the per-dashboard cap of %s. Truncating — "
                "investigate widget explosion on this dashboard.",
                dashboard_id,
                len(widgets),
                _MAX_WIDGETS_PER_DASHBOARD,
            )
            widgets = widgets[:_MAX_WIDGETS_PER_DASHBOARD]
        capped[dashboard_id] = [_alias_widget(w) for w in widgets]
    return capped


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/workspace/dashboards",
    dependencies=[Depends(require_ai_permission("finance.dashboard.view"))],
)
async def list_dashboards(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.dashboard.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()
    # Dashboards are per-user — only return the caller's own.
    query: Dict[str, Any] = {"owner_user_id": current_user.user_id}
    total = await db["finance_dashboards"].count_documents(query)
    items = (
        await db["finance_dashboards"]
        .find(query, {"_id": 0})
        .sort("order", 1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    # Embed widgets and an `id` alias so the workspace UI can render the grid
    # without an N+1 fetch — see _alias_dashboard / _load_widgets_for.
    widgets_by_dash = await _load_widgets_for(
        db, [d.get("dashboard_id") for d in items if d.get("dashboard_id")]
    )
    rows = [
        _alias_dashboard(d, widgets_by_dash.get(d.get("dashboard_id"), []))
        for d in items
    ]
    return {"rows": rows, "total": total}


@router.post(
    "/workspace/dashboards",
    status_code=201,
    dependencies=[Depends(require_ai_permission("finance.dashboard.create"))],
)
async def create_dashboard(
    payload: DashboardCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.dashboard.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    now_iso = _utc_now_iso()
    dashboard_id = gen_id("fcc_dash_")
    doc: Dict[str, Any] = {
        "dashboard_id": dashboard_id,
        "name": payload.name,
        "order": payload.order,
        "filters": dict(payload.filters),
        "owner_user_id": current_user.user_id,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    async with crud_envelope(
        module="finance",
        entity="dashboard",
        action="created",
        resource_id=dashboard_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["finance_dashboards"].insert_one(doc)
        ctx.set_after(_scrub(doc))

    # New dashboards have no widgets yet; the empty list keeps the UI happy.
    return _alias_dashboard(_scrub(doc), [])


@router.patch(
    "/workspace/dashboards/{dashboard_id}",
    dependencies=[Depends(require_ai_permission("finance.dashboard.edit"))],
)
async def update_dashboard(
    dashboard_id: str,
    payload: DashboardUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.dashboard.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    before = await db["finance_dashboards"].find_one(
        {"dashboard_id": dashboard_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Ownership check — users may only edit their own dashboards.
    if before.get("owner_user_id") != current_user.user_id and not has_perm(
        _user_dict(current_user), "admin.manage"
    ):
        raise HTTPException(status_code=403, detail="Cannot edit another user's dashboard")

    updates = payload.model_dump(exclude_unset=True)
    updates["updated_at"] = _utc_now_iso()
    updated = {**before, **updates}

    async with crud_envelope(
        module="finance",
        entity="dashboard",
        action="updated",
        resource_id=dashboard_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["finance_dashboards"].replace_one({"dashboard_id": dashboard_id}, updated)
        ctx.set_after(_scrub(updated))

    widgets_by_dash = await _load_widgets_for(db, [dashboard_id])
    return _alias_dashboard(_scrub(updated), widgets_by_dash.get(dashboard_id, []))


@router.delete(
    "/workspace/dashboards/{dashboard_id}",
    status_code=204,
    dependencies=[Depends(require_ai_permission("finance.dashboard.delete"))],
)
async def delete_dashboard(
    dashboard_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(_user_dict(current_user), "finance.dashboard.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    db = get_db()

    before = await db["finance_dashboards"].find_one(
        {"dashboard_id": dashboard_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    if before.get("owner_user_id") != current_user.user_id and not has_perm(
        _user_dict(current_user), "admin.manage"
    ):
        raise HTTPException(status_code=403, detail="Cannot delete another user's dashboard")

    async with crud_envelope(
        module="finance",
        entity="dashboard",
        action="deleted",
        resource_id=dashboard_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["finance_dashboards"].delete_one({"dashboard_id": dashboard_id})
        ctx.set_after(None)
