"""Finance Draft Auto-Generation endpoints.

Ports the time-entry-driven invoice/bill creation logic from:
  - backend/routes/ar_invoices.py::auto_generate_ar_invoices
  - backend/routes/ap_bills.py::auto_generate_bills_for_period / auto_generate_ap_bills

into NEW endpoints that write to `finance_drafts` (kind=ar_invoice / ap_bill)
with source_origin="auto_time_entries".

Endpoints:
  POST /api/finance/drafts/auto-generate-ar
  POST /api/finance/drafts/auto-generate-ap

Both accept a `dry_run` flag (default false). When true, the service computes
the would-be drafts and returns them without writing anything.

Both use the crud_envelope pattern via a bulk "created" envelope per draft so
policy/audit runs for each generated document.

Permission: finance.draft.create (same permission as manual draft create).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.finance_draft_autogen import build_ar_drafts, build_ap_drafts
from services.ingestion.route_helpers import crud_envelope

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic DTOs
# ---------------------------------------------------------------------------

class AutoGenerateARRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: str = Field(..., description="YYYY-MM-DD period start")
    period_end: str = Field(..., description="YYYY-MM-DD period end")
    dry_run: bool = Field(False, description="If true, return what would be created without writing")
    contract_id: Optional[str] = Field(None, description="Optional: filter to one contract")


class AutoGenerateAPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: str = Field(..., description="YYYY-MM-DD period start")
    period_end: str = Field(..., description="YYYY-MM-DD period end")
    dry_run: bool = Field(False, description="If true, return what would be created without writing")
    contractor_user_id: Optional[str] = Field(None, description="Optional: filter to one contractor user")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_dict(current_user) -> dict:
    return current_user.model_dump() if hasattr(current_user, "model_dump") else current_user


def _draft_summary(draft: dict) -> dict:
    return {
        "draft_id": draft["draft_id"],
        "counterparty": draft["counterparty"],
        "amount": draft["amount"],
        "line_count": len(draft.get("line_items", [])),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/drafts/auto-generate-ar",
    status_code=200,
    dependencies=[Depends(require_ai_permission("finance.draft.create"))],
)
async def auto_generate_ar(
    payload: AutoGenerateARRequest,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Auto-generate AR invoice drafts from pm_approved billable time entries.

    Groups time entries by contract. One draft per contract per period.
    Idempotent: skips contracts that already have a draft for the period.

    Returns:
        drafts_created: count of new drafts written (0 in dry_run)
        drafts: list of {draft_id, counterparty, amount, line_count}
        skipped: list of {reason, contract_id, ...}
        warnings: soft messages (missing data, etc.)
    """
    user_dict = _user_dict(current_user)
    if not has_perm(user_dict, "finance.draft.create"):
        raise HTTPException(status_code=403, detail="Permission denied: finance.draft.create required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    if not payload.period_start or not payload.period_end:
        raise HTTPException(status_code=400, detail="period_start and period_end are required")

    if payload.period_start > payload.period_end:
        raise HTTPException(status_code=400, detail="period_start must be <= period_end")

    db = get_db()

    # N2 fix: always call the service in dry_run=True so it never writes directly.
    # The route owns all writes, each guarded by a crud_envelope so policy/audit
    # runs before the insert_one — not after it.
    drafts, skipped, warnings = await build_ar_drafts(
        db=db,
        period_start=payload.period_start,
        period_end=payload.period_end,
        contract_id_filter=payload.contract_id,
        created_by=current_user.user_id,
        dry_run=True,  # service always dry-runs; route decides whether to write
    )

    written: list = []
    if not payload.dry_run:
        for draft in drafts:
            async with crud_envelope(
                module="finance",
                entity="draft",
                action="created",
                resource_id=draft["draft_id"],
                current_user=current_user,
                db=db,
                request=request,
            ) as ctx:
                await db["finance_drafts"].insert_one(draft)
                draft.pop("_id", None)
                ctx.set_after({k: v for k, v in draft.items() if k != "_id"})
            written.append(draft)
    else:
        written = drafts

    return {
        "drafts_created": len(written) if not payload.dry_run else 0,
        "drafts": [_draft_summary(d) for d in written],
        "skipped": skipped,
        "warnings": warnings,
        "dry_run": payload.dry_run,
    }


@router.post(
    "/drafts/auto-generate-ap",
    status_code=200,
    dependencies=[Depends(require_ai_permission("finance.draft.create"))],
)
async def auto_generate_ap(
    payload: AutoGenerateAPRequest,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Auto-generate AP bill drafts from approved time entries.

    Groups by contractor_business_id (preferred) or user_id.
    One draft per group per period.
    Idempotent: skips groups that already have a draft for the period.

    Returns:
        drafts_created: count of new drafts written (0 in dry_run)
        drafts: list of {draft_id, counterparty, amount, line_count}
        skipped: list of {reason, contractor_id, ...}
        warnings: soft messages
    """
    user_dict = _user_dict(current_user)
    if not has_perm(user_dict, "finance.draft.create"):
        raise HTTPException(status_code=403, detail="Permission denied: finance.draft.create required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    if not payload.period_start or not payload.period_end:
        raise HTTPException(status_code=400, detail="period_start and period_end are required")

    if payload.period_start > payload.period_end:
        raise HTTPException(status_code=400, detail="period_start must be <= period_end")

    db = get_db()

    # N2 fix: always call the service in dry_run=True so it never writes directly.
    # The route owns all writes, each guarded by a crud_envelope so policy/audit
    # runs before the insert_one — not after it.
    drafts, skipped, warnings = await build_ap_drafts(
        db=db,
        period_start=payload.period_start,
        period_end=payload.period_end,
        contractor_user_id_filter=payload.contractor_user_id,
        created_by=current_user.user_id,
        dry_run=True,  # service always dry-runs; route decides whether to write
    )

    written: list = []
    if not payload.dry_run:
        for draft in drafts:
            async with crud_envelope(
                module="finance",
                entity="draft",
                action="created",
                resource_id=draft["draft_id"],
                current_user=current_user,
                db=db,
                request=request,
            ) as ctx:
                await db["finance_drafts"].insert_one(draft)
                draft.pop("_id", None)
                ctx.set_after({k: v for k, v in draft.items() if k != "_id"})
            written.append(draft)
    else:
        written = drafts

    return {
        "drafts_created": len(written) if not payload.dry_run else 0,
        "drafts": [_draft_summary(d) for d in written],
        "skipped": skipped,
        "warnings": warnings,
        "dry_run": payload.dry_run,
    }
