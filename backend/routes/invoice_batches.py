"""Wave 5 (contracts-time-mgmt) — invoice batch CRUD + approve/reject.

Endpoints (mounted at the api_router root by server.py — full paths
become /api/invoice-batches/...):

  POST /invoice-batches/build
      Build bi-weekly batches for one period across every contract
      that has unbatched approved billable hours. Manual/scheduler
      entry point.

  GET  /invoice-batches
      List batches with status / contract / period filters. Tenant
      scoped and RBAC filtered.

  GET  /invoice-batches/{batch_id}
      Single batch detail.

  POST /invoice-batches/{batch_id}/approve
      Promote a pending batch into a finance_drafts AR invoice row
      and stamp linked_finance_draft_id on the batch. Idempotent on
      re-approve.

  POST /invoice-batches/{batch_id}/reject
      Move the batch to status=rejected. Source time entries are
      not touched.

Permission rules
----------------
Build  : caller needs finance.portal or contracts.approve or *.
List   : caller needs finance.portal or contracts.view.all or *.
Get    : same as list, plus tenant filter.
Approve: caller needs finance.portal or contracts.approve or *.
Reject : same as approve.

Do not send customer invoices. Do not push to QBO.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from services.audit_service import log_action
from services.event_bus import emit_event
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope
from services.invoice_batch_builder import build_invoice_batches
from services.invoice_batch_scheduler_security import (
    SCHEDULER_TOKEN_ENV_VAR,
    is_scheduler_configured,
    verify_scheduler_token,
)
from models import gen_id


logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Field / event / module registration. Mirrors the convention in
# contracts.py / time_entries.py so the policy engine, workflow engine,
# and Admin GUI can discover this module.
# ---------------------------------------------------------------------------
register_module_config(
    "invoice_batches", "invoice_batches", "batch_id", "Finance"
)
register_module_events(
    "invoice_batches",
    "invoice_batch",
    ["created", "approved", "rejected", "scheduled_build"],
)
register_fields(
    "invoice_batches",
    "invoice_batch",
    [
        {"name": "batch_id", "type": "string", "label": "Batch ID"},
        {"name": "contract_id", "type": "string", "label": "Contract ID"},
        {"name": "period_start", "type": "string", "label": "Period Start"},
        {"name": "period_end", "type": "string", "label": "Period End"},
        {"name": "status", "type": "string", "label": "Status",
         "options": ["draft", "pending_finance_approval", "approved",
                     "rejected", "sent_to_qbo"]},
        {"name": "total_amount", "type": "number", "label": "Total Amount"},
        {"name": "linked_finance_draft_id", "type": "string",
         "label": "Linked Finance Draft ID"},
        {"name": "decided_by", "type": "string", "label": "Decided By"},
        {"name": "decided_at", "type": "datetime", "label": "Decided At"},
        {"name": "decision_notes", "type": "string", "label": "Decision Notes"},
        {"name": "tenant_id", "type": "string", "label": "Tenant ID"},
    ],
)


VALID_BATCH_STATUSES = frozenset({
    "draft",
    "pending_finance_approval",
    "approved",
    "rejected",
    "sent_to_qbo",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _can_build(user) -> bool:
    # Wave 5 hardening: prefer the dedicated invoice_batches.build
    # key when granted; fall back to the original finance.portal /
    # contracts.approve / * gate so existing role grants keep
    # working without a data migration.
    return (
        has_perm(user, "invoice_batches.build")
        or has_perm(user, "finance.portal")
        or has_perm(user, "contracts.approve")
        or has_perm(user, "*")
    )


def _can_approve(user) -> bool:
    return (
        has_perm(user, "invoice_batches.approve")
        or has_perm(user, "finance.portal")
        or has_perm(user, "contracts.approve")
        or has_perm(user, "*")
    )


def _can_view(user) -> bool:
    return (
        has_perm(user, "invoice_batches.view")
        or has_perm(user, "invoice_batches.approve")
        or has_perm(user, "invoice_batches.build")
        or has_perm(user, "finance.portal")
        or has_perm(user, "contracts.view.all")
        or has_perm(user, "*")
    )


# Backward-compat alias for the original gate. Approve and build
# happen to share the same role set today; the wrapper preserves
# the old name for any callers (only tests should reference it).
def _can_approve_or_build(user) -> bool:
    return _can_build(user) or _can_approve(user)


async def _load_batch_for_user(db, batch_id: str, current_user):
    """Load a batch and apply the tenant guard. 404 (not 403) on
    cross-tenant access so existence is not leaked."""
    doc = await db.invoice_batches.find_one({"batch_id": batch_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice batch not found")
    if (
        getattr(current_user, "tenant_id", None)
        and doc.get("tenant_id")
        and current_user.tenant_id != doc["tenant_id"]
    ):
        raise HTTPException(status_code=404, detail="Invoice batch not found")
    return doc


# ---------------------------------------------------------------------------
# POST /invoice-batches/build
# ---------------------------------------------------------------------------


@router.post(
    "/invoice-batches/build",
    dependencies=[Depends(require_ai_permission("invoice_batches.build"))],
)
async def build_batches_route(
    data: dict,
    request: Request = None,
    current_user=Depends(get_current_user),
):
    if not _can_build(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    period_start = (data or {}).get("period_start")
    period_end = (data or {}).get("period_end")
    if not period_start or not period_end:
        raise HTTPException(
            status_code=400,
            detail="period_start and period_end are required (ISO YYYY-MM-DD)",
        )
    contract_id_filter = (data or {}).get("contract_id")
    dry_run = bool((data or {}).get("dry_run", False))

    resource_id = f"period:{period_start}/{period_end}"
    async with crud_envelope(
        module="invoice_batches",
        entity="invoice_batch",
        action="created",
        resource_id=resource_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        batches, skipped, warnings = await build_invoice_batches(
            db,
            period_start=period_start,
            period_end=period_end,
            contract_id_filter=contract_id_filter,
            actor_user_id=current_user.user_id,
            dry_run=dry_run,
        )
        ctx.set_after({
            "period_start": period_start,
            "period_end": period_end,
            "batches_count": len(batches),
            "skipped_count": len(skipped),
            "warnings_count": len(warnings),
            "dry_run": dry_run,
            "contract_id_filter": contract_id_filter,
        })

    try:
        await log_action(
            "invoice_batch.build",
            current_user.user_id,
            current_user.email,
            "invoice_batch",
            resource_id,
            {
                "batches_created": len(batches),
                "skipped": len(skipped),
                "warnings": len(warnings),
                "dry_run": dry_run,
                "contract_id_filter": contract_id_filter,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log_action failed for invoice_batch.build: %s", exc)
    return {
        "batches": batches,
        "skipped": skipped,
        "warnings": warnings,
        "dry_run": dry_run,
    }


# Wave 5 hardening: backward-compatible alias for the build route
# under a more descriptive name. Body and behavior are identical to
# /invoice-batches/build; both endpoints stay registered so a future
# rename never breaks existing schedulers.
@router.post(
    "/invoice-batches/build-biweekly",
    dependencies=[Depends(require_ai_permission("invoice_batches.build"))],
)
async def build_biweekly_alias(
    data: dict,
    request: Request = None,
    current_user=Depends(get_current_user),
):
    return await build_batches_route(
        data=data, request=request, current_user=current_user,
    )


# ---------------------------------------------------------------------------
# GET /invoice-batches  /  GET /invoice-batches/{batch_id}
# ---------------------------------------------------------------------------


@router.get(
    "/invoice-batches",
    dependencies=[Depends(require_ai_permission("invoice_batches.view"))],
)
async def list_invoice_batches(
    status: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    period_start: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    if not _can_view(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    query: Dict[str, Any] = {}
    if status:
        if status not in VALID_BATCH_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of: {', '.join(sorted(VALID_BATCH_STATUSES))}",
            )
        query["status"] = status
    if contract_id:
        query["contract_id"] = contract_id
    if period_start:
        query["period_start"] = period_start
    if getattr(current_user, "tenant_id", None):
        query["$or"] = [
            {"tenant_id": current_user.tenant_id},
            {"tenant_id": {"$exists": False}},
            {"tenant_id": None},
        ]
    # Total is computed against the full filter, not the to_list page —
    # len(items) capped at 500 even when there were thousands of matches,
    # which silently misled the operator's "how many batches qualified"
    # mental model.
    total = await db.invoice_batches.count_documents(query)
    items = await db.invoice_batches.find(query, {"_id": 0}).sort("period_start", -1).to_list(500)
    return {"items": items, "total": total}


@router.get(
    "/invoice-batches/{batch_id}",
    dependencies=[Depends(require_ai_permission("invoice_batches.view"))],
)
async def get_invoice_batch(batch_id: str, current_user=Depends(get_current_user)):
    if not _can_view(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    return await _load_batch_for_user(db, batch_id, current_user)


# ---------------------------------------------------------------------------
# POST /invoice-batches/{batch_id}/approve
# ---------------------------------------------------------------------------


def _promote_to_finance_draft(batch: Dict[str, Any], *, actor_user_id: str) -> Dict[str, Any]:
    """Compose the finance_drafts row that mirrors the existing
    auto-generated AR shape. ``source_origin`` flags the row as
    invoice-batch-derived so a future reconcile pass can tell it
    apart from the legacy auto_time_entries path."""
    draft_id = gen_id("fcc_draft_")
    now_iso = _now().isoformat()
    line_items = [
        {
            "description": li.get("description", ""),
            "quantity": float(li.get("hours") or 0),
            "rate": float(li.get("sell_rate_snapshot") or 0),
            "amount": float(li.get("amount") or 0),
        }
        for li in batch.get("line_items") or []
    ]
    draft = {
        "draft_id": draft_id,
        "kind": "ar_invoice",
        "counterparty": batch.get("account_name") or batch.get("contract_name") or batch.get("contract_id"),
        "counterparty_id": batch.get("account_id"),
        "amount": float(batch.get("total_amount") or 0),
        "memo": (
            f"Invoice batch {batch.get('batch_id')} for "
            f"{batch.get('period_start')} to {batch.get('period_end')}"
        ),
        "line_items": line_items,
        "sync_status": "Draft",
        "qbo_id": None,
        "error": None,
        "source_origin": "invoice_batch",
        "source_batch_id": batch.get("batch_id"),
        "source_contract_id": batch.get("contract_id"),
        "source_period_start": batch.get("period_start"),
        "source_period_end": batch.get("period_end"),
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": actor_user_id,
    }
    if batch.get("tenant_id"):
        draft["tenant_id"] = batch["tenant_id"]
    return draft


@router.post(
    "/invoice-batches/{batch_id}/approve",
    dependencies=[Depends(require_ai_permission("invoice_batches.approve"))],
)
async def approve_invoice_batch(
    batch_id: str,
    data: Optional[dict] = None,
    request: Request = None,
    current_user=Depends(get_current_user),
):
    if not _can_approve(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    batch = await _load_batch_for_user(db, batch_id, current_user)
    notes = ((data or {}).get("notes") or "").strip() or None

    current_status = batch.get("status")
    # Idempotent on re-approve: a batch that already has a linked
    # finance_draft just returns the existing draft. Re-applying the
    # approve handler must NOT create a duplicate draft.
    if current_status == "approved":
        existing_draft_id = batch.get("linked_finance_draft_id")
        existing_draft = None
        if existing_draft_id:
            existing_draft = await db.finance_drafts.find_one(
                {"draft_id": existing_draft_id}, {"_id": 0}
            )
        return {
            "batch": batch,
            "finance_draft": existing_draft,
            "already_approved": True,
        }

    if current_status not in {"pending_finance_approval", "draft"}:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot approve batch from status={current_status!r}; "
                "expected pending_finance_approval or draft."
            ),
        )

    draft = _promote_to_finance_draft(batch, actor_user_id=current_user.user_id)
    now = _now()
    async with crud_envelope(
        module="invoice_batches",
        entity="invoice_batch",
        action="status_changed",
        resource_id=batch_id,
        current_user=current_user,
        db=db,
        before=batch,
        request=request,
    ) as ctx:
        await db.finance_drafts.insert_one(draft)
        draft.pop("_id", None)

        await db.invoice_batches.update_one(
            {"batch_id": batch_id},
            {"$set": {
                "status": "approved",
                "linked_finance_draft_id": draft["draft_id"],
                "decided_by": current_user.user_id,
                "decided_at": now,
                "decision_notes": notes,
                "updated_at": now,
            }},
        )
        ctx.set_after({
            **batch,
            "status": "approved",
            "linked_finance_draft_id": draft["draft_id"],
        })
    try:
        await log_action(
            "invoice_batch.approved",
            current_user.user_id,
            current_user.email,
            "invoice_batch",
            batch_id,
            {
                "linked_finance_draft_id": draft["draft_id"],
                "contract_id": batch.get("contract_id"),
                "total_amount": batch.get("total_amount"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log_action failed for invoice_batch.approved: %s", exc)
    try:
        await emit_event(
            "invoice_batches",
            "approved",
            record={**batch, "linked_finance_draft_id": draft["draft_id"]},
            user_id=current_user.user_id,
            user_email=current_user.email,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_event failed for invoice_batch.approved: %s", exc)

    refreshed = await db.invoice_batches.find_one({"batch_id": batch_id}, {"_id": 0})
    return {
        "batch": refreshed,
        "finance_draft": draft,
        "already_approved": False,
    }


# ---------------------------------------------------------------------------
# POST /invoice-batches/{batch_id}/reject
# ---------------------------------------------------------------------------


@router.post(
    "/invoice-batches/{batch_id}/reject",
    dependencies=[Depends(require_ai_permission("invoice_batches.approve"))],
)
async def reject_invoice_batch(
    batch_id: str,
    data: Optional[dict] = None,
    request: Request = None,
    current_user=Depends(get_current_user),
):
    if not _can_approve(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    batch = await _load_batch_for_user(db, batch_id, current_user)
    notes = ((data or {}).get("notes") or "").strip()
    if not notes:
        raise HTTPException(status_code=400, detail="Rejection notes are required")

    current_status = batch.get("status")
    if current_status == "rejected":
        return {"batch": batch, "already_rejected": True}
    if current_status not in {"pending_finance_approval", "draft"}:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot reject batch from status={current_status!r}; "
                "expected pending_finance_approval or draft."
            ),
        )

    now = _now()
    async with crud_envelope(
        module="invoice_batches",
        entity="invoice_batch",
        action="status_changed",
        resource_id=batch_id,
        current_user=current_user,
        db=db,
        before=batch,
        request=request,
    ) as ctx:
        await db.invoice_batches.update_one(
            {"batch_id": batch_id},
            {"$set": {
                "status": "rejected",
                # Wave 5 hardening: clear batch_key on rejection so the
                # sparse unique index on (contract_id, period_start,
                # period_end) frees up for a rebuild. The rejected row
                # stays for audit but no longer holds the dedup slot.
                "batch_key": None,
                "decided_by": current_user.user_id,
                "decided_at": now,
                "decision_notes": notes,
                "updated_at": now,
            }},
        )
        ctx.set_after({**batch, "status": "rejected"})
    try:
        await log_action(
            "invoice_batch.rejected",
            current_user.user_id,
            current_user.email,
            "invoice_batch",
            batch_id,
            {"contract_id": batch.get("contract_id"), "reason": notes},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log_action failed for invoice_batch.rejected: %s", exc)
    try:
        await emit_event(
            "invoice_batches",
            "rejected",
            record=batch,
            user_id=current_user.user_id,
            user_email=current_user.email,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_event failed for invoice_batch.rejected: %s", exc)

    refreshed = await db.invoice_batches.find_one({"batch_id": batch_id}, {"_id": 0})
    return {"batch": refreshed, "already_rejected": False}


# ---------------------------------------------------------------------------
# POST /invoice-batches/build-biweekly/scheduled
#
# External-scheduler entry point. NOT user-authenticated — the
# caller is an external scheduler (Cloud Scheduler, GitHub Actions
# cron, n8n cron workflows, etc.) that authenticates via a shared
# token in the X-Scheduler-Token header. Fails closed when the env
# var is unset.
#
# Reuses build_invoice_batches verbatim — no logic duplication.
# Defaults period_start / period_end to the most recent complete
# 14-day window ending yesterday so a scheduler that calls this
# endpoint on every other Monday without any body gets the
# expected fortnight.
# ---------------------------------------------------------------------------


def _default_biweekly_period() -> tuple[str, str]:
    """Return (period_start, period_end) for the most recent
    complete 14-day window ending yesterday (UTC). The cadence
    target is bi-weekly; the scheduler that calls this endpoint
    should fire every other Monday after midnight UTC so this
    default lines up with the fortnight that just closed."""
    today = datetime.now(timezone.utc).date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=13)
    return (start.isoformat(), end.isoformat())


# -----------------------------------------------------------------------
# AI authorization carve-out for the token-only scheduler routes
# -----------------------------------------------------------------------
# This endpoint and `/invoice-batches/scheduler/health` are NOT wrapped
# in `Depends(require_ai_permission(...))` because they are
# token-authenticated, not user-authenticated. `require_ai_permission`
# resolves `current_user` from the JWT — it has no input here. Adding
# the dep would force the endpoint to require a AEGISAXIOM user session,
# which would break the external-scheduler contract (Cloud Scheduler /
# GitHub Actions cron / n8n cron have no session to present).
#
# AI employees that need to trigger a scheduled build go through the
# normal user-authenticated /invoice-batches/build route, which IS
# gated by `require_ai_permission("invoice_batches.build")`. The
# token-only routes here are reserved for infra-level callers.
#
# A source-level negative lock test
# (`test_scheduler_routes_documented_ai_authz_exemption`) verifies the
# deliberate exemption stays explicit so a future refactor can't
# silently strip it without surfacing the security implication.
@router.post("/invoice-batches/build-biweekly/scheduled")
async def build_biweekly_scheduled(
    data: Optional[dict] = None,
    x_scheduler_token: Optional[str] = Header(
        None, alias="X-Scheduler-Token", convert_underscores=False
    ),
):
    """Scheduler-callable bi-weekly invoice-batch build.

    Authentication
    --------------
    NO bearer / session auth. The caller authenticates by presenting
    the X-Scheduler-Token header value matching the env-configured
    INVOICE_BATCH_SCHEDULER_TOKEN. The token comparison runs in
    constant time via hmac.compare_digest.

    Configuration gating
    --------------------
    When INVOICE_BATCH_SCHEDULER_TOKEN is unset, the route returns
    503 with a structured envelope — an unconfigured deploy MUST NOT
    accept a scheduler call from any source.

    Body
    ----
    Optional JSON body with the same shape the manual /build route
    accepts:
        { period_start?, period_end?, contract_id?, dry_run? }

    When period_start / period_end are omitted, the route defaults
    to the most recent complete 14-day window ending yesterday
    (UTC) — see _default_biweekly_period above.

    Reuse
    -----
    Calls build_invoice_batches verbatim. No logic duplication;
    the same idempotency + skip + warning behavior the manual
    /build route exercises is preserved here.

    Audit
    -----
    Writes an invoice_batch.build_biweekly_scheduled audit row
    with the actor stamped as "system_scheduler" so the audit
    history distinguishes scheduler-driven sweeps from manual
    /build triggers (which stamp the human actor's user_id).
    """
    # Fail closed on unconfigured deploys.
    if not is_scheduler_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "scheduler_not_configured",
                "message": (
                    f"{SCHEDULER_TOKEN_ENV_VAR} is not set on this deploy. "
                    "External-scheduler invoice batch builds are disabled. "
                    "See docs/contracts-time-mgmt/scheduler.md."
                ),
            },
        )

    if not verify_scheduler_token(x_scheduler_token):
        # 401 rather than 403 because the caller is unauthenticated
        # (no user identity claim), not just unauthorized to act.
        # Match the pattern in the procurement webhook (401 invalid
        # signature) for consistency.
        raise HTTPException(status_code=401, detail="Invalid scheduler token")

    db = get_db()
    body = data or {}
    period_start = body.get("period_start")
    period_end = body.get("period_end")
    if not period_start or not period_end:
        period_start, period_end = _default_biweekly_period()
    contract_id_filter = body.get("contract_id")
    dry_run = bool(body.get("dry_run", False))

    # The actor user_id for the builder service is a synthetic
    # "system_scheduler" string. The builder service stamps this on
    # the batch's created_by field and the audit row uses it as the
    # actor id below; this keeps scheduler-built batches visually
    # distinct from human-built batches in admin views.
    actor_user_id = "system_scheduler"

    # Telemetry — write a "started" row before the builder runs so
    # the status endpoint can detect a hung / crashed sweep (the row
    # never moves to succeeded/failed). On success we patch the same
    # row with counts; on exception we patch with sanitized error.
    started_at = _now()
    run_id = f"schedrun_{uuid.uuid4().hex[:12]}"
    started_doc: Dict[str, Any] = {
        "run_id": run_id,
        "source": "external_scheduler",
        "status": "started",
        "started_at": started_at,
        "finished_at": None,
        "period_start": period_start,
        "period_end": period_end,
        "contract_id": contract_id_filter,
        "dry_run": dry_run,
        "batches_count": None,
        "skipped_count": None,
        "warnings_count": None,
        "error": None,
        "created_at": started_at,
        "updated_at": started_at,
    }
    try:
        await db.invoice_batch_scheduler_runs.insert_one(dict(started_doc))
    except Exception as exc:  # noqa: BLE001
        # Telemetry must not break the sweep. Log + carry on.
        logger.warning(
            "invoice_batch_scheduler_runs insert (started) failed: %s", exc
        )

    try:
        batches, skipped, warnings = await build_invoice_batches(
            db,
            period_start=period_start,
            period_end=period_end,
            contract_id_filter=contract_id_filter,
            actor_user_id=actor_user_id,
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        # Sanitize the error message — no stack traces, no internal
        # state. The status endpoint surfaces this string to
        # operators so it must be safe to display.
        sanitized = type(exc).__name__
        now = _now()
        try:
            await db.invoice_batch_scheduler_runs.update_one(
                {"run_id": run_id},
                {"$set": {
                    "status": "failed",
                    "finished_at": now,
                    "error": sanitized,
                    "updated_at": now,
                }},
            )
        except Exception as inner_exc:  # noqa: BLE001
            logger.warning(
                "invoice_batch_scheduler_runs update (failed) failed: %s",
                inner_exc,
            )
        try:
            await log_action(
                "invoice_batch.build_biweekly_scheduled.failed",
                actor_user_id,
                "scheduler@aegisaxiom.internal",
                "invoice_batch",
                f"period:{period_start}/{period_end}",
                {
                    "run_id": run_id,
                    "error": sanitized,
                    "source": "external_scheduler",
                },
            )
        except Exception as inner_exc:  # noqa: BLE001
            logger.warning(
                "audit log_action failed for "
                "invoice_batch.build_biweekly_scheduled.failed: %s",
                inner_exc,
            )
        # Re-raise so the scheduler caller sees the failure on the
        # wire. The sanitized status row is the operator-facing
        # surface; the raw exception still surfaces to whatever sits
        # in front of the deploy (Cloud Run logs, GHA logs).
        raise

    # Success path — patch the started row to "succeeded" with counts.
    finished_at = _now()
    try:
        await db.invoice_batch_scheduler_runs.update_one(
            {"run_id": run_id},
            {"$set": {
                "status": "succeeded",
                "finished_at": finished_at,
                "batches_count": len(batches),
                "skipped_count": len(skipped),
                "warnings_count": len(warnings),
                "updated_at": finished_at,
            }},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "invoice_batch_scheduler_runs update (succeeded) failed: %s", exc
        )

    try:
        await log_action(
            "invoice_batch.build_biweekly_scheduled",
            actor_user_id,
            "scheduler@aegisaxiom.internal",
            "invoice_batch",
            f"period:{period_start}/{period_end}",
            {
                "run_id": run_id,
                "batches_created": len(batches),
                "skipped": len(skipped),
                "warnings": len(warnings),
                "dry_run": dry_run,
                "contract_id_filter": contract_id_filter,
                "source": "external_scheduler",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "audit log_action failed for invoice_batch.build_biweekly_scheduled: %s",
            exc,
        )

    return {
        "period_start": period_start,
        "period_end": period_end,
        "batches": batches,
        "skipped": skipped,
        "warnings": warnings,
        "dry_run": dry_run,
        "source": "external_scheduler",
        "run_id": run_id,
    }


# ---------------------------------------------------------------------------
# GET /invoice-batches/scheduler/status   (user-authenticated)
# GET /invoice-batches/scheduler/health   (token-authenticated)
# ---------------------------------------------------------------------------


_DEFAULT_STALE_AFTER_HOURS = 384.0  # 14-day cadence + 2-day grace


def _resolve_stale_after_hours() -> float:
    """Read the operator-tunable stale threshold from env. Falls back
    to 384 hours (14-day cadence + 2-day grace) when unset or
    malformed."""
    raw = (os.environ.get("INVOICE_BATCH_SCHEDULER_STALE_AFTER_HOURS") or "").strip()
    if not raw:
        return _DEFAULT_STALE_AFTER_HOURS
    try:
        v = float(raw)
        if v <= 0:
            return _DEFAULT_STALE_AFTER_HOURS
        return v
    except (TypeError, ValueError):
        return _DEFAULT_STALE_AFTER_HOURS


def _isoformat(value):
    """Best-effort ISO formatter that tolerates naive datetimes and
    pre-formatted strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _strip_id(doc):
    if not isinstance(doc, dict):
        return doc
    out = dict(doc)
    out.pop("_id", None)
    return out


async def _latest_scheduler_run(db, *, status: Optional[str] = None):
    query: Dict[str, Any] = {"source": "external_scheduler"}
    if status:
        query["status"] = status
    cursor = db.invoice_batch_scheduler_runs.find(query, {"_id": 0}).sort(
        "started_at", -1
    )
    rows = await cursor.to_list(1)
    return rows[0] if rows else None


def _hours_since(when) -> Optional[float]:
    if when is None:
        return None
    try:
        if isinstance(when, str):
            ts = datetime.fromisoformat(when.replace("Z", "+00:00"))
        else:
            ts = when
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = _now() - ts
        return delta.total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        return None


def _classify_health(
    *, configured: bool, last_success_row, stale_after_hours: float
) -> tuple[str, Optional[float]]:
    """Compute the health verdict + last-success age in hours.

    Returns one of:
      not_configured  — env var unset
      no_success_yet  — configured but no succeeded run exists
      ok              — last success within stale threshold
      critical        — last success older than stale threshold
    """
    if not configured:
        return ("not_configured", None)
    if not last_success_row:
        return ("no_success_yet", None)
    age = _hours_since(last_success_row.get("finished_at"))
    if age is None:
        # We have a row but no parsable timestamp — treat as
        # critical so the operator looks at it.
        return ("critical", None)
    if age <= stale_after_hours:
        return ("ok", age)
    return ("critical", age)


@router.get(
    "/invoice-batches/scheduler/status",
    dependencies=[Depends(require_ai_permission("invoice_batches.view"))],
)
async def get_scheduler_status(current_user=Depends(get_current_user)):
    """Operator-facing scheduler status. Surfaces the last
    succeeded run, the last failure (if any), the health verdict,
    and the stale-threshold setting.

    Permission: any user who can view or build invoice batches.
    """
    if not (_can_view(current_user) or _can_build(current_user)):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    configured = is_scheduler_configured()
    stale_after = _resolve_stale_after_hours()

    last_run = await _latest_scheduler_run(db)
    last_success = await _latest_scheduler_run(db, status="succeeded")
    last_failure = await _latest_scheduler_run(db, status="failed")

    health, last_success_age = _classify_health(
        configured=configured,
        last_success_row=last_success,
        stale_after_hours=stale_after,
    )

    def _serialize(row):
        if not row:
            return None
        scrubbed = _strip_id(row)
        # Normalize datetimes to ISO strings so the JSON serializer
        # never trips on tz-aware datetimes.
        for k in ("started_at", "finished_at", "created_at", "updated_at"):
            if k in scrubbed:
                scrubbed[k] = _isoformat(scrubbed[k])
        return scrubbed

    return {
        "configured": configured,
        "health": health,
        "stale_after_hours": stale_after,
        "last_success_age_hours": last_success_age,
        "last_run": _serialize(last_run),
        "last_success": _serialize(last_success),
        "last_failure": _serialize(last_failure),
    }


@router.get("/invoice-batches/scheduler/health")
async def get_scheduler_health(
    response: Any = None,
    x_scheduler_token: Optional[str] = Header(
        None, alias="X-Scheduler-Token", convert_underscores=False
    ),
):
    """Monitor-safe health endpoint. Same auth as the scheduled
    POST. Returns a small JSON envelope plus an HTTP status code
    the monitor can alert on:

      503 scheduler_not_configured     env var unset
      401 invalid scheduler token       token missing/wrong
      503 stale                          configured but last
                                         success older than threshold
      200 ok / no_success_yet            healthy or pre-first-run

    No internal batch documents are returned — only a tiny
    envelope safe to forward to a third-party uptime monitor.
    """
    if not is_scheduler_configured():
        # Fail-closed mirror of the POST route. An unconfigured
        # deploy returns 503 to any caller.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "scheduler_not_configured",
                "message": (
                    f"{SCHEDULER_TOKEN_ENV_VAR} is not set on this deploy. "
                    "Scheduler health check is disabled."
                ),
            },
        )

    if not verify_scheduler_token(x_scheduler_token):
        raise HTTPException(status_code=401, detail="Invalid scheduler token")

    db = get_db()
    stale_after = _resolve_stale_after_hours()
    last_success = await _latest_scheduler_run(db, status="succeeded")
    health, age = _classify_health(
        configured=True,
        last_success_row=last_success,
        stale_after_hours=stale_after,
    )

    envelope = {
        "health": health,
        "stale_after_hours": stale_after,
        "last_success_age_hours": age,
        "last_success_at": _isoformat(
            (last_success or {}).get("finished_at")
        ) if last_success else None,
    }

    if health == "critical":
        # Monitor-callable 503 so an uptime check fires. Mirror the
        # detail-envelope shape from the unconfigured case so an
        # uptime tool gets consistent JSON.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "scheduler_stale",
                "message": (
                    f"Last successful scheduled run is older than "
                    f"{stale_after} hours."
                ),
                **envelope,
            },
        )

    return envelope
