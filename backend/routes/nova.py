"""Nova direct-API routes.

Two surfaces live here:

  1. Legacy direct-API (`/project`, `/runs/...`) — kept for tests, scheduled
     jobs, and the chat agent's read path. Gated on ``nova.use``.

  2. AEGISAXIOM-owned cash-flow API (`/projections/cashflow`,
     `/projections/{id}`, `/projections/{id}/export`) — the finance portal
     contract for Ryan's app. Tenant context is resolved from the
     authenticated session, never from the request body. Gated on
     ``finance.cashflow.*`` / ``finance.nova.run_projection`` so the
     finance role can be granted access without unlocking the chat agent.

The chat agent path goes through services/ai/conductor.py +
services/ai/agents/nova.py.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import ValidationError

from auth_helper import get_current_user, has_perm, require_perm
from middleware.ai_authz import require_ai_permission
from services.ai.agents.tools.nova_tools import (
    MRR_MAPPING_UNAVAILABLE_MESSAGE as _MRR_MAPPING_UNAVAILABLE_MESSAGE,
    _is_valid_projection_month,
)
from services.audit_service import log_action
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
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
from services.nova_engine.data_loader import build_projection_inputs_from_globals
from services.nova_engine.engine import project_cash_flow
from services.nova_engine.models import (
    InsufficientHistoryError,
    LINE_TIMING_DEFAULTS,
    ManualCostAssumption,
    Override,
    RecurringRevenueAssumption,
    WeeklyDistribution,
)
from services.nova_engine.persistence import (
    get_latest_run,
    get_run,
    list_runs,
    save_run,
)
from services.permission_registry import register_module_permissions


logger = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------------------------------------------------------
# Permission registration
# ---------------------------------------------------------------------------
# Four AEGISAXIOM-owned permissions gate the cash-flow API. They sit alongside
# the existing ``nova.use`` permission (declared in routes/roles.py) which
# gates the chat agent. Ryan's finance app should grant these to whichever
# role hosts the cash-flow tab; ``nova.use`` only matters for the chat UX.
#
# Implication edges:
#   finance.nova.run_projection      => finance.cashflow.view
#                                        finance.cashflow.edit_assumptions
#   finance.cashflow.edit_assumptions => finance.cashflow.view
#   finance.cashflow.export           => finance.cashflow.view
#
# Rationale: anyone who can run the engine implicitly needs to view its
# output and to author the assumptions that drove it; anyone who can edit
# assumptions or export needs to see the report. ``nova.use`` and
# ``finance.cashflow.view`` are intentionally not linked — they govern
# different surfaces (chat agent vs. finance portal tab) and finance ops
# may want one without the other.

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
# Field / event / module registration
# ---------------------------------------------------------------------------
# Surface projection_runs to the Policy Admin GUI and the Workflow Builder.
# ``module_config.collection`` matches services/nova_engine/persistence.py's
# ``_COLLECTION`` constant. Events: ``created`` fires every time a run is
# persisted; ``exported`` is emitted by the export stub below. No
# status_changed — runs are immutable artifacts (a re-run produces a new
# document with a new projection_run_id).
register_module_config(
    "nova", "projection_runs", "projection_run_id", "Finance"
)
register_module_events("nova", "projection_run", ["created", "exported"])
register_fields(
    "nova",
    "projection_run",
    [
        {
            "name": "projection_run_id",
            "type": "string",
            "label": "Projection Run ID",
            "description": "Unique id of the persisted projection run.",
        },
        {
            "name": "user_id",
            "type": "string",
            "label": "Owner",
            "description": "AEGISAXIOM user that initiated the projection.",
        },
        {
            "name": "starting_cash",
            "type": "number",
            "label": "Starting Cash",
            "description": "Cash on hand at projection start (USD).",
        },
        {
            "name": "projection_start_month",
            "type": "string",
            "label": "Projection Start Month",
            "description": "First forecast month, YYYY-MM.",
        },
        {
            "name": "horizon_weeks",
            "type": "number",
            "label": "Horizon (Weeks)",
            "description": "How many weeks the projection covers (default 13).",
        },
        {
            "name": "ending_cash",
            "type": "number",
            "label": "Ending Cash",
            "description": "Cash on hand at horizon, after applying assumptions and overrides.",
        },
        {
            "name": "minimum_cash",
            "type": "number",
            "label": "Minimum Cash",
            "description": "Lowest weekly ending cash across the horizon.",
        },
        {
            "name": "minimum_cash_week",
            "type": "number",
            "label": "Minimum Cash Week",
            "description": "1-based week number where minimum cash occurs.",
        },
        {
            "name": "inputs_hash",
            "type": "string",
            "label": "Inputs Hash",
            "description": "SHA-256 of the canonicalized inputs; same inputs => same hash.",
        },
    ],
)


# ---------------------------------------------------------------------------
# Tenant context resolution
# ---------------------------------------------------------------------------
# AEGISAXIOM is single-tenant in practice today — there is no ``tenant_id`` on
# the user model. Until that lands, the resolved tenant key is whatever
# the session-bound user was assigned to (``current_user.account_id``)
# falling back to the literal ``"platform"`` for users who have no
# customer-account association (admins, internal staff). Two invariants
# that callers MUST NOT violate:
#
#   1. The tenant key is derived from the session user, not the request
#      body. ``finance.cashflow.*`` callers cannot pass ``tenant_id`` /
#      ``account_id`` / ``company_id`` to read another tenant's data.
#   2. Nova persistence (services/nova_engine/persistence.py) always
#      scopes reads + writes by ``user_id``. The tenant key here lives
#      only in the audit row and the response envelope; it does NOT yet
#      partition Mongo collections.
#
# When real multi-tenancy lands, replace this body with the real lookup
# (likely ``current_user.tenant_id`` plus a per-tenant DB resolver) and
# update the audit details schema to include both keys.
_BODY_KEYS_THAT_LEAK_TENANT = ("tenant_id", "account_id", "company_id")


def _resolve_tenant_context(current_user: Any) -> dict[str, Any]:
    """Return the session-derived tenant envelope for a Nova call.

    Never read tenant from the request body — see module docstring.
    """
    account_id = getattr(current_user, "account_id", None)
    return {
        "tenant_id": account_id or "platform",
        "tenant_source": "session.account_id" if account_id else "session.platform_default",
    }


def _reject_body_tenant_overrides(payload: dict) -> None:
    """Hard-fail any client that tries to spoof tenant from the body.

    A 400 here is intentional: silent ignore would let a frontend bug
    accidentally surface another tenant's data to the user once true
    multi-tenancy lands. Fail loud now, fix the caller.
    """
    leaked = [k for k in _BODY_KEYS_THAT_LEAK_TENANT if k in payload]
    if leaked:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tenant context is resolved server-side; remove "
                f"{leaked!r} from the request body."
            ),
        )


# ---------------------------------------------------------------------------
# Assumption / override coercion
# ---------------------------------------------------------------------------
# Used by /projections/cashflow when the caller supplies scenario state.
# Pydantic models do their own validation; we just shape the failure into
# an HTTP 400 with which row was bad, so Ryan's frontend can highlight it.

def _coerce_overrides(raw: list[dict] | None) -> list[Override]:
    if not raw:
        return []
    out: list[Override] = []
    for i, row in enumerate(raw):
        try:
            out.append(Override(**row))
        except (ValidationError, ValueError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"overrides[{i}] invalid: {e}",
            )
    return out


def _coerce_manual_costs(raw: list[dict] | None) -> list[ManualCostAssumption]:
    if not raw:
        return []
    out: list[ManualCostAssumption] = []
    for i, row in enumerate(raw):
        try:
            out.append(ManualCostAssumption(**row))
        except (ValidationError, ValueError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"manual_cost_assumptions[{i}] invalid: {e}",
            )
    return out


def _coerce_manual_revenue(raw: list[dict] | None) -> list[RecurringRevenueAssumption]:
    if not raw:
        return []
    out: list[RecurringRevenueAssumption] = []
    for i, row in enumerate(raw):
        try:
            out.append(RecurringRevenueAssumption(**row))
        except (ValidationError, ValueError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"manual_recurring_revenue[{i}] invalid: {e}",
            )
    return out


def _coerce_weekly_timing_overrides(
    raw: dict[str, dict[str, Any]] | None,
) -> dict[str, WeeklyDistribution]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400,
            detail="weekly_timing_overrides must be an object keyed by line type.",
        )

    valid_line_types = set(LINE_TIMING_DEFAULTS)
    out: dict[str, WeeklyDistribution] = {}
    for line_type, row in raw.items():
        if line_type not in valid_line_types:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"weekly_timing_overrides[{line_type!r}] invalid: "
                    f"line type must be one of {sorted(valid_line_types)!r}"
                ),
            )
        try:
            out[line_type] = WeeklyDistribution(**row)
        except (ValidationError, ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"weekly_timing_overrides[{line_type!r}] invalid: {e}",
            )
    return out


def _insufficient_history_detail(e: InsufficientHistoryError) -> dict[str, Any]:
    affected_line = getattr(e, "line", None)
    is_mrr = bool(affected_line and "mrr" in str(affected_line).lower())
    code = "mrr_mapping_unavailable" if is_mrr else "insufficient_history"
    message = _MRR_MAPPING_UNAVAILABLE_MESSAGE if is_mrr else str(e)
    return {
        "error": code,
        "message": message,
        "affected_line": affected_line,
        "available_months": getattr(e, "available_months", None),
        "required_months": getattr(e, "required_months", None),
    }


def _raise_insufficient_history_http(e: InsufficientHistoryError) -> NoReturn:
    raise HTTPException(status_code=422, detail=_insufficient_history_detail(e))


# ---------------------------------------------------------------------------
# UI-friendly response envelope
# ---------------------------------------------------------------------------
# ``/projections/cashflow`` returns a thin envelope around the engine
# output rather than the raw ProjectionOutput. The shape stays stable as
# the engine evolves: ``summary`` for headline KPI cards, ``weeks`` for
# the table, ``meta`` for the audit/correlation hooks, ``warnings`` for
# the freshness banner. Frontend code consumes the envelope; engine
# internals stay private.

def _build_response_envelope(
    *,
    run: dict,
    output: Any,
    tenant: dict[str, Any],
) -> dict[str, Any]:
    """Build the POST envelope from a live ``ProjectionOutput``.

    Funnels through ``_build_response_envelope_from_doc`` so the POST and
    GET responses for the same projection_run_id are byte-equivalent.
    """
    output_payload = output.model_dump(mode="json")
    # model_dump already turns weeks into dicts; keep the explicit dump for
    # robustness in case a custom serializer omits them.
    output_payload["weeks"] = [w.model_dump(mode="json") for w in output.weeks]
    warnings_payload = [
        w.model_dump(mode="json") if hasattr(w, "model_dump") else w
        for w in (getattr(output, "warnings", None) or [])
    ]
    return _build_response_envelope_from_doc(
        run=run,
        output_doc=output_payload,
        tenant=tenant,
        warnings=warnings_payload,
    )


def _build_response_envelope_from_doc(
    *,
    run: dict,
    output_doc: dict[str, Any],
    tenant: dict[str, Any],
    warnings: list | None = None,
) -> dict[str, Any]:
    """Build the response envelope from a persisted run doc.

    POST and GET both reach this function so their response shapes match.
    The persisted doc has already been ``model_dump``'d at save_run time
    (see services/nova_engine/persistence.py), so every value here is
    JSON-native and we don't need to handle Pydantic models.
    """
    weeks = output_doc.get("weeks") or []
    inputs_hash = output_doc.get("inputs_hash") or run.get("inputs_hash")
    if warnings is None:
        # GET path: prefer freshness_warnings stored on the run, but fall
        # back to whatever the engine stuffed into output.warnings so we
        # don't silently drop data the engine emitted.
        warnings = list(
            run.get("freshness_warnings")
            or output_doc.get("warnings")
            or []
        )

    return {
        "projection_run_id": run.get("projection_run_id"),
        "status": "complete",
        "inputs_hash": inputs_hash,
        "output": output_doc,
        "report": _build_cashflow_report(output_doc, weeks),
        "summary": {
            "starting_cash": output_doc.get("starting_cash"),
            "ending_cash": output_doc.get("ending_cash"),
            "minimum_cash": output_doc.get("minimum_cash"),
            "minimum_cash_week": output_doc.get("minimum_cash_week"),
            "total_revenue": output_doc.get("total_revenue"),
            "total_costs": output_doc.get("total_costs"),
            "net_change": output_doc.get("net_change"),
        },
        "weeks": weeks,
        "warnings": warnings,
        "meta": {
            "inputs_hash": inputs_hash,
            "computed_at": output_doc.get("computed_at"),
            "engine_version": (
                output_doc.get("formulas_version")
                or run.get("engine_version")
            ),
            "tenant_id": tenant["tenant_id"],
            "tenant_source": tenant["tenant_source"],
        },
    }


def _build_cashflow_report(output_doc: dict, weeks: list[dict]) -> dict[str, Any]:
    """Build the report block from a persisted output doc + weeks list.

    Operates on dicts so POST (live ``ProjectionOutput`` model_dump'd) and
    GET (already-persisted dict) produce identical reports.
    """
    column_names = [f"W{w.get('week_number')}" for w in weeks]

    def values_for(field_name: str) -> list[float]:
        return [float(w.get(field_name, 0.0) or 0.0) for w in weeks]

    starting_cash_raw = output_doc.get("starting_cash")
    starting_cash = float(starting_cash_raw or 0.0)
    opening_cash: list[float] = []
    running = starting_cash
    for week in weeks:
        opening_cash.append(running)
        running = float(week.get("ending_cash", running) or running)

    line_defs = [
        ("ps_revenue", "PS revenue", values_for("ps_revenue")),
        ("fs_revenue", "FS revenue", values_for("fs_revenue")),
        ("mrr_revenue", "MRR revenue", values_for("mrr_revenue")),
        ("wholesale_revenue", "Wholesale revenue", values_for("wholesale_revenue")),
        ("other_revenue", "Other revenue", values_for("other_revenue")),
        ("ps_cogs", "PS COGS", values_for("ps_cogs")),
        ("fs_cogs", "FS COGS", values_for("fs_cogs")),
        ("payroll", "Payroll", values_for("payroll")),
        (
            "administrative_expenses",
            "Administrative expenses",
            values_for("administrative_expenses"),
        ),
        ("other_costs", "Other costs", values_for("other_costs")),
        ("total_revenue", "Total revenue", values_for("total_revenue")),
        ("total_costs", "Total costs", values_for("total_costs")),
        ("net_cash_flow", "Net cash flow", values_for("net_cash_flow")),
        ("opening_cash", "Opening cash", opening_cash),
        ("ending_cash", "Ending cash", values_for("ending_cash")),
    ]

    return {
        "column_names": column_names,
        "line_items": [
            {"key": key, "label": label, "values": values}
            for key, label, values in line_defs
        ],
        "kpis": {
            "starting_cash": output_doc.get("starting_cash"),
            "ending_cash": output_doc.get("ending_cash"),
            "minimum_cash": output_doc.get("minimum_cash"),
            "minimum_cash_week": output_doc.get("minimum_cash_week"),
            "total_revenue": output_doc.get("total_revenue"),
            "total_costs": output_doc.get("total_costs"),
            "net_change": output_doc.get("net_change"),
        },
    }


async def _emit_audit(
    *,
    action: str,
    current_user: Any,
    resource_id: str | None,
    details: dict[str, Any],
) -> None:
    """Best-effort audit write. A failed audit must never break the call."""
    try:
        await log_action(
            action=action,
            actor_id=current_user.user_id,
            actor_email=getattr(current_user, "email", "") or "",
            resource_type="projection_run",
            resource_id=resource_id,
            details=details,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to write audit log %s: %s", action, e)


# ---------------------------------------------------------------------------
# LEGACY: POST /api/nova/project  (gated on nova.use, kept for direct
# engine tests, chat read path, and scheduled jobs). Ryan's finance portal
# contract is POST /api/nova/projections/cashflow.
# ---------------------------------------------------------------------------

@router.post("/project")
async def run_projection_route(
    payload: dict,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Trigger a projection run. Body: starting_cash, projection_start_month, horizon_weeks (optional)."""
    await require_perm(current_user, "nova.use", resource="nova", action="run_projection")

    try:
        starting_cash = float(payload["starting_cash"])
        horizon_weeks = int(payload.get("horizon_weeks", 13))
        projection_start_month = payload["projection_start_month"]
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or missing field: {e}",
        )

    if not _is_valid_projection_month(projection_start_month):
        raise HTTPException(
            status_code=400,
            detail="projection_start_month must be YYYY-MM (e.g., '2026-05')",
        )

    try:
        inputs = await build_projection_inputs_from_globals(
            starting_cash=starting_cash,
            projection_start_month=projection_start_month,
            horizon_weeks=horizon_weeks,
        )
    except InsufficientHistoryError as e:
        _raise_insufficient_history_http(e)

    try:
        output = project_cash_flow(inputs)
    except InsufficientHistoryError as e:
        _raise_insufficient_history_http(e)
    run = await save_run(
        user_id=current_user.user_id,
        inputs=inputs,
        output=output,
    )

    await _emit_audit(
        action="nova.projection_run",
        current_user=current_user,
        resource_id=run["projection_run_id"],
        details={
            "starting_cash": starting_cash,
            "projection_start_month": projection_start_month,
            "horizon_weeks": horizon_weeks,
            "inputs_hash": output.inputs_hash,
        },
    )

    return {
        "projection_run_id": run["projection_run_id"],
        "inputs_hash": output.inputs_hash,
        "output": output.model_dump(mode="json"),
    }


@router.get("/runs/{projection_run_id}")
async def get_run_route(
    projection_run_id: str,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await require_perm(current_user, "nova.use", resource="projection_run",
                       resource_id=projection_run_id, action="get_run")
    run = await get_run(projection_run_id, current_user.user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Projection run not found")
    return run


@router.get("/runs")
async def list_runs_route(
    latest: bool = Query(False, description="Return only the most recent run."),
    limit: int = Query(20, ge=1, le=100),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await require_perm(current_user, "nova.use", resource="nova", action="list_runs")
    if latest:
        run = await get_latest_run(current_user.user_id)
        runs = [run] if run else []
        return {"runs": runs, "returned_count": len(runs)}
    runs = await list_runs(current_user.user_id, limit=limit)
    return {"runs": runs, "returned_count": len(runs)}


# ---------------------------------------------------------------------------
# AEGISAXIOM-owned cash-flow API for Ryan's finance portal
# ---------------------------------------------------------------------------
# These endpoints are the integration seam. Contract:
#
#   POST /api/nova/projections/cashflow
#     Body: {starting_cash, projection_start_month,
#            horizon_weeks?, manual_cost_assumptions?,
#            manual_recurring_revenue?, overrides?,
#            weekly_timing_overrides?, sales_modifier?, cost_modifier?}
#     Auth: requires finance.nova.run_projection.
#           Supplying any scenario-state key ALSO requires
#           finance.cashflow.edit_assumptions (so a view-only role can
#           still trigger a baseline projection but cannot author
#           scenario state).
#     Returns: stable response envelope (see _build_response_envelope).
#     Audit:   nova.projection.generated (always)
#              nova.assumptions.updated  (when scenario state is supplied)
#
#   GET /api/nova/projections/{projection_run_id}
#     Auth: finance.cashflow.view. Scoped by user_id at the persistence
#           layer (cross-user reads return 404).
#
#   POST /api/nova/projections/{projection_run_id}/export
#     Stub. Auth: finance.cashflow.export. Emits nova.projection.exported
#     audit row and returns a 200 marker. The actual file generation
#     (CSV/PDF) is a v2 follow-up; see TODO inside the handler.

def _user_supplied_scenario_state(payload: dict) -> bool:
    """True iff the caller passed any non-baseline scenario-state key.

    Presence matters, not truthiness: sales_modifier=0 and cost_modifier=0
    are explicit scenario inputs.
    """
    keys = (
        "manual_cost_assumptions",
        "manual_recurring_revenue",
        "overrides",
        "weekly_timing_overrides",
        "sales_modifier",
        "cost_modifier",
    )
    return any(k in payload for k in keys)


@router.post("/projections/cashflow")
async def post_cashflow_projection(
    payload: dict = Body(default_factory=dict),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Run the 90-day cash-flow projection and persist it.

    This is the AEGISAXIOM-owned endpoint Ryan's finance portal should call.
    See the section docstring above for the full contract.
    """
    # --- Authz ---------------------------------------------------------
    await require_perm(
        current_user,
        "finance.nova.run_projection",
        resource="nova.projection_run",
        action="run_projection",
    )

    has_scenario_state = _user_supplied_scenario_state(payload)
    if has_scenario_state and not has_perm(
        current_user, "finance.cashflow.edit_assumptions"
    ):
        # Use require_perm to keep the audit trail consistent with other
        # 403s on this endpoint.
        await require_perm(
            current_user,
            "finance.cashflow.edit_assumptions",
            resource="nova.projection_run",
            action="edit_assumptions",
        )

    # --- Tenant context (server-resolved, never from body) ------------
    _reject_body_tenant_overrides(payload)
    tenant = _resolve_tenant_context(current_user)

    # --- Required body fields -----------------------------------------
    try:
        starting_cash = float(payload["starting_cash"])
        projection_start_month = payload["projection_start_month"]
    except KeyError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required field: {e.args[0]}",
        )
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="starting_cash must be a number.",
        )

    if not _is_valid_projection_month(projection_start_month):
        raise HTTPException(
            status_code=400,
            detail="projection_start_month must be YYYY-MM (e.g., '2026-05').",
        )

    try:
        horizon_weeks = int(payload.get("horizon_weeks", 13))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="horizon_weeks must be an integer.",
        )

    # --- Coerce caller-supplied scenario state -----------------------
    overrides = _coerce_overrides(payload.get("overrides"))
    manual_costs = _coerce_manual_costs(payload.get("manual_cost_assumptions"))
    manual_revenue = _coerce_manual_revenue(payload.get("manual_recurring_revenue"))
    weekly_timing_overrides = _coerce_weekly_timing_overrides(
        payload.get("weekly_timing_overrides")
    )

    # sales_modifier / cost_modifier flow through the engine via
    # ProjectionInputs defaults if not supplied. When supplied here, we
    # validate and forward — the engine applies them in its line-totals
    # step (see services/nova_engine/engine.py:235).
    extra_inputs: dict[str, Any] = {}
    for knob in ("sales_modifier", "cost_modifier"):
        if knob in payload and payload[knob] is not None:
            try:
                extra_inputs[knob] = float(payload[knob])
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"{knob} must be a number.",
                )

    # --- Build inputs + run engine ------------------------------------
    try:
        base_inputs = await build_projection_inputs_from_globals(
            starting_cash=starting_cash,
            projection_start_month=projection_start_month,
            horizon_weeks=horizon_weeks,
            overrides=overrides,
            manual_cost_assumptions=manual_costs,
            manual_recurring_revenue=manual_revenue,
            weekly_timing_overrides=weekly_timing_overrides,
        )
    except InsufficientHistoryError as e:
        _raise_insufficient_history_http(e)

    inputs = base_inputs.model_copy(update=extra_inputs) if extra_inputs else base_inputs
    try:
        output = project_cash_flow(inputs)
    except InsufficientHistoryError as e:
        _raise_insufficient_history_http(e)

    run = await save_run(
        user_id=current_user.user_id,
        inputs=inputs,
        output=output,
    )

    # --- Audit -------------------------------------------------------
    manual_mrr_supplied = any(a.line_type == "MRR" for a in manual_revenue)
    audit_details = {
        "user_id": current_user.user_id,
        "projection_run_id": run["projection_run_id"],
        "starting_cash": starting_cash,
        "projection_start_month": projection_start_month,
        "horizon_weeks": horizon_weeks,
        "inputs_hash": output.inputs_hash,
        "tenant_id": tenant["tenant_id"],
        "tenant_source": tenant["tenant_source"],
        "manual_assumptions_supplied": bool(manual_costs or manual_revenue),
        "manual_mrr_supplied": manual_mrr_supplied,
        "scenario_state": {
            "overrides": len(overrides),
            "manual_cost_assumptions": len(manual_costs),
            "manual_recurring_revenue": len(manual_revenue),
            "weekly_timing_overrides": len(weekly_timing_overrides),
            "sales_modifier_set": "sales_modifier" in extra_inputs,
            "cost_modifier_set": "cost_modifier" in extra_inputs,
        },
    }

    await _emit_audit(
        action="nova.projection.generated",
        current_user=current_user,
        resource_id=run["projection_run_id"],
        details=audit_details,
    )

    if has_scenario_state:
        await _emit_audit(
            action="nova.assumptions.updated",
            current_user=current_user,
            resource_id=run["projection_run_id"],
            details={
                "user_id": current_user.user_id,
                "projection_run_id": run["projection_run_id"],
                "projection_start_month": projection_start_month,
                "horizon_weeks": horizon_weeks,
                "inputs_hash": output.inputs_hash,
                "tenant_id": tenant["tenant_id"],
                "overrides": len(overrides),
                "manual_cost_assumptions": len(manual_costs),
                "manual_recurring_revenue": len(manual_revenue),
                "weekly_timing_overrides": len(weekly_timing_overrides),
                "manual_mrr_supplied": manual_mrr_supplied,
                "sales_modifier_set": "sales_modifier" in extra_inputs,
                "cost_modifier_set": "cost_modifier" in extra_inputs,
            },
        )

    return _build_response_envelope(run=run, output=output, tenant=tenant)


@router.get("/projections/{projection_run_id}")
async def get_cashflow_projection(
    projection_run_id: str,
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Read a previously generated projection (UI-friendly envelope).

    Scoped by ``user_id`` at the persistence layer; cross-user reads
    return 404 (not 403) so we don't leak existence.
    """
    await require_perm(
        current_user,
        "finance.cashflow.view",
        resource="nova.projection_run",
        resource_id=projection_run_id,
        action="get_projection",
    )

    run = await get_run(projection_run_id, current_user.user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Projection run not found")

    tenant = _resolve_tenant_context(current_user)
    output_doc = run.get("output") or {}

    # Funnel through the shared envelope builder so GET and POST produce
    # the same shape (status, output, report, summary, weeks, warnings,
    # meta) for the same projection_run_id. Earlier versions of GET
    # silently dropped status/output/report despite a comment claiming
    # parity; the refactor closes that gap.
    return _build_response_envelope_from_doc(
        run=run, output_doc=output_doc, tenant=tenant,
    )


@router.post("/projections/{projection_run_id}/export")
async def export_cashflow_projection(
    projection_run_id: str,
    payload: dict = Body(default_factory=dict),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark a projection as exported and emit an audit event.

    Stub: this v1 endpoint exists so Ryan's finance portal can wire
    up the "Export" button immediately and so admins see export
    activity in the audit log. Actual CSV/PDF generation is a v2
    follow-up — see TODO below.
    """
    await require_perm(
        current_user,
        "finance.cashflow.export",
        resource="nova.projection_run",
        resource_id=projection_run_id,
        action="export_projection",
    )

    _reject_body_tenant_overrides(payload)

    # Confirm the projection exists and belongs to this user before logging.
    run = await get_run(projection_run_id, current_user.user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Projection run not found")

    tenant = _resolve_tenant_context(current_user)
    export_format = (payload.get("format") or "csv").lower()
    if export_format not in ("csv", "pdf", "json"):
        raise HTTPException(
            status_code=400,
            detail="format must be one of: csv, pdf, json",
        )

    await _emit_audit(
        action="nova.projection.exported",
        current_user=current_user,
        resource_id=projection_run_id,
        details={
            "format": export_format,
            "tenant_id": tenant["tenant_id"],
            "tenant_source": tenant["tenant_source"],
            "inputs_hash": run.get("inputs_hash"),
        },
    )

    # TODO(v2): generate the actual file. Options under discussion:
    #   - CSV via csv.DictWriter on output.weeks (cheap, ships first)
    #   - PDF via the same renderer Orion/SOW uses
    #   - JSON: just return the persisted run doc
    # For v1 the audit row + 200 lets the frontend surface "Exported"
    # without misleading the user that a file was actually generated.
    return {
        "projection_run_id": projection_run_id,
        "format": export_format,
        "status": "audit_logged",
        "message": (
            "Export recorded in audit log. File generation lands in v2."
        ),
        "meta": {
            "tenant_id": tenant["tenant_id"],
            "tenant_source": tenant["tenant_source"],
        },
    }


# ---------------------------------------------------------------------------
# Cashflow Planner — live finance-data forecast
# ---------------------------------------------------------------------------
# Contract:
#
#   GET /api/nova/cashflow-planner
#     Auth: finance.cashflow.view (same permission as the projection
#           portal — viewing the planner does NOT imply being able to
#           run a fresh engine projection).
#     Returns: as_of / active_week_ending / horizon_weeks /
#              items_count / rolled_forward_count / forecast[] /
#              rolled_forward_items[] / warnings[].
#
# Behavior:
#   1. Load A/R + A/P rows from finance_drafts via the adapter.
#   2. Normalise dates to Friday week-endings.
#   3. Roll unpaid past-due items onto the active Friday.
#   4. Write audit rows (idempotent — repeated calls collapse on
#      audit_key).
#   5. Bucket items into per-Friday expected_ar / expected_ap /
#      net_cash_flow.
#
# Source records are NOT mutated; the roll-forward exists only in
# the planner output and the audit collection.


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
