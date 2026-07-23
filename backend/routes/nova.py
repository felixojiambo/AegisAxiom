"""Cashflow Planner route (finance survivor).

The externally-fed legacy Nova projection engine (`/project`, `/runs/...`,
`/projections/cashflow`, `/projections/{id}`, `/projections/{id}/export`)
was removed in Phase 1 (accounting-integration removal + Nova/FCC park). What
remains here is the internal-billing-fed **Cashflow Planner**:

    GET /api/nova/cashflow-planner

It reads A/R + A/P rows from ``finance_drafts`` (internal only), rolls unpaid
past-due items onto the active Friday, and buckets them into a weekly
forecast. Source rows are never mutated. Gated on ``finance.cashflow.view``.

This surface migrates to Postgres in the Finance-survivors phase (Phase 10).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, require_perm
from middleware.ai_authz import require_ai_permission
from database import get_db
from services.nova_engine.cashflow_planner import (
    active_week_ending,
    bucket_items_into_weeks,
    roll_unpaid_past_due_items,
    write_cashflow_audit_records,
)
from services.nova_engine.cashflow_planner_adapter import (
    load_cashflow_items_from_finance_sources,
    summarise_warnings,
)
from services.permission_registry import register_module_permissions


logger = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------------------------------------------------------
# Permission registration
# ---------------------------------------------------------------------------
# ``finance.cashflow.view`` gates the surviving Cashflow Planner surface. The
# remaining cash-flow permissions are retained here for RBAC/DAG stability
# (roles may already reference them); their former projection endpoints were
# removed with the Nova engine. Permission-surface cleanup is owned by the
# Platform-core phase (Phase 2), not this deletion pass.

@register_module_permissions("nova")
def _nova_permissions():
    return [
        {
            "key": "finance.cashflow.view",
            "group": "Finance - Cash Flow",
            "label": "View Cash Flow Projections",
        },
        {
            "key": "finance.cashflow.edit_assumptions",
            "group": "Finance - Cash Flow",
            "label": "Edit Cash Flow Assumptions",
            "implies": ["finance.cashflow.view"],
        },
        {
            "key": "finance.cashflow.export",
            "group": "Finance - Cash Flow",
            "label": "Export Cash Flow Reports",
            "implies": ["finance.cashflow.view"],
        },
        {
            "key": "finance.nova.run_projection",
            "group": "Finance - Cash Flow",
            "label": "Run Nova Cash Flow Projection",
            "implies": [
                "finance.cashflow.view",
                "finance.cashflow.edit_assumptions",
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Tenant context resolution
# ---------------------------------------------------------------------------
# AEGISAXIOM is single-tenant in practice today — there is no ``tenant_id`` on
# the user model. The resolved tenant key is whatever the session-bound user
# was assigned to (``current_user.account_id``) falling back to the literal
# ``"platform"`` for users who have no customer-account association. The tenant
# key is always derived from the session user, never from the request body.


def _resolve_tenant_context(current_user: Any) -> dict[str, Any]:
    """Return the session-derived tenant envelope for a planner call."""
    account_id = getattr(current_user, "account_id", None)
    return {
        "tenant_id": account_id or "platform",
        "tenant_source": "session.account_id" if account_id else "session.platform_default",
    }


# ---------------------------------------------------------------------------
# Cashflow Planner — live finance-data forecast
# ---------------------------------------------------------------------------
# Contract:
#
#   GET /api/nova/cashflow-planner
#     Auth: finance.cashflow.view.
#     Returns: as_of / active_week_ending / horizon_weeks /
#              items_count / rolled_forward_count / forecast[] /
#              rolled_forward_items[] / warnings[].
#
# Behavior:
#   1. Load A/R + A/P rows from finance_drafts via the adapter.
#   2. Normalise dates to Friday week-endings.
#   3. Roll unpaid past-due items onto the active Friday.
#   4. Write audit rows (idempotent — repeated calls collapse on audit_key).
#   5. Bucket items into per-Friday expected_ar / expected_ap / net_cash_flow.
#
# Source records are NOT mutated; the roll-forward exists only in the planner
# output and the audit collection.


def _parse_as_of(raw: str | None) -> date:
    """Coerce the ``as_of`` query string to a date.

    Defaults to UTC today when omitted. Invalid input → HTTP 400 so a
    bad query string doesn't silently substitute today.
    """
    if not raw:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="as_of must be YYYY-MM-DD (e.g., '2026-05-15').",
        )


@router.get(
    "/cashflow-planner",
    dependencies=[Depends(require_ai_permission("finance.cashflow.view"))],
)
async def get_cashflow_planner_forecast(
    as_of: Optional[str] = Query(
        None,
        description=(
            "YYYY-MM-DD. Overrides today as the anchor date used "
            "to compute the active Friday week-ending. Defaults to "
            "today (UTC)."
        ),
    ),
    horizon_weeks: int = Query(
        13, ge=1, le=52,
        description="Forecast horizon in weeks (1-52). Default 13.",
    ),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Live Cashflow Planner forecast from finance_drafts.

    Read-only: source rows are never mutated, and the forecast only
    rolls past-due unpaid items forward in the response + audit log.
    See the section docstring above for the full contract.
    """
    # --- Authz ---------------------------------------------------------
    await require_perm(
        current_user,
        "finance.cashflow.view",
        resource="nova.cashflow_planner",
        action="view_forecast",
    )

    tenant = _resolve_tenant_context(current_user)
    today = _parse_as_of(as_of)

    db = get_db()

    # --- Load ----------------------------------------------------------
    load_result = await load_cashflow_items_from_finance_sources(
        db,
        tenant_id=tenant["tenant_id"],
        as_of=today,
        horizon_weeks=horizon_weeks,
    )

    # --- Roll forward --------------------------------------------------
    roll_result = roll_unpaid_past_due_items(load_result.items, today)

    # --- Audit (idempotent on audit_key) ------------------------------
    audit_written = await write_cashflow_audit_records(
        roll_result.audit_records, db=db,
    )

    # --- Bucket -------------------------------------------------------
    buckets = bucket_items_into_weeks(roll_result.items)
    forecast_payload = [
        {
            "week_ending": b.week_ending.isoformat(),
            "expected_ar": b.expected_ar,
            "expected_ap": b.expected_ap,
            "net_cash_flow": b.net_cash_flow,
        }
        for b in buckets
    ]

    rolled_forward_payload = [
        {
            "item_id": rec.item_id,
            "item_type": rec.item_type,
            "old_week_ending_date": rec.old_week_ending_date.isoformat(),
            "new_week_ending_date": rec.new_week_ending_date.isoformat(),
            "reason": rec.reason,
            "status_at_change": rec.status_at_change,
            "amount": rec.amount,
            "source_system": rec.source_system,
        }
        for rec in roll_result.audit_records
    ]

    return {
        "as_of": today.isoformat(),
        "active_week_ending": active_week_ending(today).isoformat(),
        "horizon_weeks": horizon_weeks,
        "items_count": len(roll_result.items),
        "rolled_forward_count": len(roll_result.audit_records),
        "audit_rows_written": audit_written,
        "forecast": forecast_payload,
        "rolled_forward_items": rolled_forward_payload,
        "source_counts": load_result.source_counts,
        "warnings": summarise_warnings(load_result.warnings),
        "meta": {
            "tenant_id": tenant["tenant_id"],
            "tenant_source": tenant["tenant_source"],
        },
    }