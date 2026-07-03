"""Financial Command Center — Referral MRR routes.

Lifted from legacy `backend/routes/referral_mrr.py` into the FCC namespace.

Differences from source:
  * Router prefix: /finance (mounted in server.py under /api/finance).
  * All paths prefixed with /referral-mrr/… (e.g. /referral-mrr/header-templates).
  * Permission model: finance.portal + granular finance.referral_mrr.*
    variants → finance.referral_mrr.view (reads) and finance.referral_mrr.edit
    (writes). finance.access is accepted as a parent-perm bypass on all endpoints.
  * State-changing endpoints (POST/DELETE) routed through crud_envelope for
    audit/policy/events.
  * parse-csv and commit-commissions use crud_envelope with action="created".
  * Field registry registered under module "finance" for three entities:
    referral_account_mapping, referral_header_template, referral_commission.
  * CSV parsing logic and commission bulk-insert logic are preserved exactly
    from the legacy source; no behavioural changes.
  * Note: parse-csv is a multipart FormData endpoint. AEGISAXIOM frontend uses raw
    fetch() (not safeXhr) for this route — the backend keeps the same contract.

Collections used (no renames — Thread 3A wiring handles all mounts):
  account_mappings, header_templates, referral_commissions,
  commissions, local_commissions.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict

from auth_helper import get_current_user, has_perm
from database import get_db
from models import gen_id
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
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

register_module_config("finance", "account_mappings", "account_number", "Finance")
register_module_events(
    "finance", "referral_account_mapping", ["created", "updated", "deleted"]
)
register_fields(
    "finance",
    "referral_account_mapping",
    [
        {
            "name": "account_number",
            "type": "string",
            "label": "Account Number",
            "description": "Partner's linking identifier (BAN, BAM, Account Number, etc.).",
        },
        {
            "name": "deal_id",
            "type": "string",
            "label": "Deal ID",
            "description": "AEGISAXIOM deal this account number maps to.",
        },
        {
            "name": "deal_name",
            "type": "string",
            "label": "Deal Name",
            "description": "Human-readable name of the linked deal.",
        },
        {
            "name": "partner_name",
            "type": "string",
            "label": "Partner Name",
            "description": "Name of the referral partner.",
        },
        {
            "name": "commission_plan",
            "type": "string",
            "label": "Commission Plan",
            "description": "Commission plan identifier for this partner.",
        },
        {
            "name": "linking_field_name",
            "type": "string",
            "label": "Linking Field Name",
            "description": "What this linking field is called in the partner's CSV.",
        },
        {"name": "created_at", "type": "datetime", "label": "Created At"},
        {"name": "updated_at", "type": "datetime", "label": "Updated At"},
        {"name": "created_by", "type": "string", "label": "Created By (user_id)"},
        {"name": "updated_by", "type": "string", "label": "Updated By (user_id)"},
    ],
)

register_module_config(
    "finance", "header_templates", "template_id", "Finance"
)
register_module_events(
    "finance", "referral_header_template", ["created", "deleted"]
)
register_fields(
    "finance",
    "referral_header_template",
    [
        {
            "name": "template_id",
            "type": "string",
            "label": "Template ID",
            "description": "Primary key — tmpl_ prefix.",
        },
        {
            "name": "template_name",
            "type": "string",
            "label": "Template Name",
            "description": "Human-readable name for this header mapping template.",
        },
        {
            "name": "mappings",
            "type": "object",
            "label": "Mappings",
            "description": "JSON dict mapping CSV header names to AEGISAXIOM field names.",
        },
        {
            "name": "linking_column",
            "type": "string",
            "label": "Linking Column",
            "description": "CSV column used to link rows to account mappings.",
        },
        {
            "name": "amount_column",
            "type": "string",
            "label": "Amount Column",
            "description": "CSV column containing the commission amount.",
        },
        {"name": "created_at", "type": "datetime", "label": "Created At"},
        {"name": "created_by", "type": "string", "label": "Created By (user_id)"},
    ],
)

register_module_config(
    "finance", "referral_commissions", "record_id", "Finance"
)
register_module_events(
    "finance", "referral_commission", ["created", "committed"]
)
register_fields(
    "finance",
    "referral_commission",
    [
        {
            "name": "record_id",
            "type": "string",
            "label": "Record ID",
            "description": "Primary key — refmrr_ prefix.",
        },
        {
            "name": "type",
            "type": "string",
            "label": "Type",
            "description": "Always 'referral_mrr' — identifies the commission source.",
        },
        {
            "name": "account_number",
            "type": "string",
            "label": "Account Number",
            "description": "Partner account number that originated this commission.",
        },
        {
            "name": "deal_id",
            "type": "string",
            "label": "Deal ID",
            "description": "AEGISAXIOM deal this referral commission is tied to.",
        },
        {
            "name": "deal_name",
            "type": "string",
            "label": "Deal Name",
            "description": "Human-readable name of the deal.",
        },
        {
            "name": "partner_name",
            "type": "string",
            "label": "Partner Name",
            "description": "Referral partner name.",
        },
        {
            "name": "amount",
            "type": "number",
            "label": "Amount",
            "description": "Raw MRR amount from the partner's CSV.",
        },
        {
            "name": "period",
            "type": "string",
            "label": "Period",
            "description": "Commission period in YYYY-MM format.",
        },
        {
            "name": "linking_field_name",
            "type": "string",
            "label": "Linking Field Name",
            "description": "CSV column name used for the account number lookup.",
        },
        {
            "name": "status",
            "type": "string",
            "label": "Status",
            "description": "Processing status (processed, committed).",
            "options": [
                {"value": "processed", "label": "Processed"},
                {"value": "committed", "label": "Committed"},
            ],
        },
        {
            "name": "raw_data",
            "type": "object",
            "label": "Raw Data",
            "description": "Original CSV row values stored for audit purposes.",
        },
        {"name": "created_at", "type": "datetime", "label": "Created At"},
        {"name": "created_by", "type": "string", "label": "Created By (user_id)"},
    ],
)


# ---------------------------------------------------------------------------
# Pydantic DTOs
# ---------------------------------------------------------------------------

class HeaderMappingTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_name: str
    mappings: Dict[str, Any]
    linking_column: str
    amount_column: str


class AccountMappingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_number: str
    deal_id: str
    deal_name: Optional[str] = None
    partner_name: Optional[str] = None
    commission_plan: Optional[str] = None
    linking_field_name: Optional[str] = None


class CommitCommissionsRequest(BaseModel):
    rows: List[dict]
    period: str


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


def _user_id(current_user: Any) -> str:
    if isinstance(current_user, dict):
        return current_user.get("user_id", "unknown")
    return getattr(current_user, "user_id", "unknown")


def _user_dict(current_user: Any) -> dict:
    return current_user.model_dump() if hasattr(current_user, "model_dump") else current_user


def _require_view(user: Any) -> None:
    u = _user_dict(user)
    if not (
        has_perm(u, "finance.referral_mrr.view")
        or has_perm(u, "finance.access")
    ):
        raise HTTPException(
            status_code=403,
            detail="Permission denied: finance.referral_mrr.view required",
        )


def _require_edit(user: Any) -> None:
    u = _user_dict(user)
    if not (
        has_perm(u, "finance.referral_mrr.edit")
        or has_perm(u, "finance.access")
    ):
        raise HTTPException(
            status_code=403,
            detail="Permission denied: finance.referral_mrr.edit required",
        )


# ---------------------------------------------------------------------------
# Header Template routes
# ---------------------------------------------------------------------------

@router.get(
    "/referral-mrr/header-templates",
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.view"))],
)
async def list_header_templates(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Return all saved header mapping templates."""
    _require_view(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    templates = (
        await db["header_templates"]
        .find({}, {"_id": 0})
        .to_list(100)
    )
    return {"templates": templates}


@router.post(
    "/referral-mrr/header-templates",
    status_code=201,
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.edit"))],
)
async def create_header_template(
    payload: HeaderMappingTemplateCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Save a new header mapping template."""
    _require_edit(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    template_id = gen_id("tmpl_")
    doc: Dict[str, Any] = {
        "template_id": template_id,
        "template_name": payload.template_name,
        "mappings": payload.mappings,
        "linking_column": payload.linking_column,
        "amount_column": payload.amount_column,
        "created_by": _user_id(current_user),
        "created_at": _utc_now_iso(),
    }

    async with crud_envelope(
        module="finance",
        entity="referral_header_template",
        action="created",
        resource_id=template_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["header_templates"].insert_one(doc)
        ctx.set_after(_scrub(doc))

    return {"template_id": template_id, "message": "Template saved"}


@router.delete(
    "/referral-mrr/header-templates/{template_id}",
    status_code=204,
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.edit"))],
)
async def delete_header_template(
    template_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Delete a header mapping template."""
    _require_edit(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    before = await db["header_templates"].find_one({"template_id": template_id}, {"_id": 0})
    if before is None:
        raise HTTPException(status_code=404, detail="Template not found")

    async with crud_envelope(
        module="finance",
        entity="referral_header_template",
        action="deleted",
        resource_id=template_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["header_templates"].delete_one({"template_id": template_id})
        ctx.set_after(None)


# ---------------------------------------------------------------------------
# Account Mapping routes
# ---------------------------------------------------------------------------

@router.get(
    "/referral-mrr/account-mappings",
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.view"))],
)
async def list_account_mappings(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Return all account-number-to-deal-ID mappings."""
    _require_view(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    mappings = await db["account_mappings"].find({}, {"_id": 0}).to_list(1000)
    return {"mappings": mappings}


@router.post(
    "/referral-mrr/account-mappings",
    status_code=201,
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.edit"))],
)
async def save_account_mapping(
    payload: AccountMappingCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Create or update an account-number-to-deal-ID mapping.

    Upserts on account_number: if the mapping already exists it is updated in-place;
    otherwise a new document is inserted.
    """
    _require_edit(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    existing = await db["account_mappings"].find_one(
        {"account_number": payload.account_number}, {"_id": 0}
    )

    if existing:
        update_fields: Dict[str, Any] = {
            "deal_id": payload.deal_id,
            "deal_name": payload.deal_name,
            "partner_name": payload.partner_name,
            "commission_plan": payload.commission_plan,
            "linking_field_name": payload.linking_field_name,
            "updated_at": _utc_now_iso(),
            "updated_by": _user_id(current_user),
        }
        async with crud_envelope(
            module="finance",
            entity="referral_account_mapping",
            action="updated",
            resource_id=payload.account_number,
            current_user=current_user,
            db=db,
            before=existing,
            request=request,
        ) as ctx:
            await db["account_mappings"].update_one(
                {"account_number": payload.account_number},
                {"$set": update_fields},
            )
            ctx.set_after({**existing, **update_fields})
        return {"message": "Account mapping updated"}

    else:
        doc: Dict[str, Any] = {
            "account_number": payload.account_number,
            "deal_id": payload.deal_id,
            "deal_name": payload.deal_name,
            "partner_name": payload.partner_name,
            "commission_plan": payload.commission_plan,
            "linking_field_name": payload.linking_field_name,
            "created_at": _utc_now_iso(),
            "created_by": _user_id(current_user),
        }
        async with crud_envelope(
            module="finance",
            entity="referral_account_mapping",
            action="created",
            resource_id=payload.account_number,
            current_user=current_user,
            db=db,
            request=request,
        ) as ctx:
            await db["account_mappings"].insert_one(doc)
            ctx.set_after(_scrub(doc))
        return {"message": "Account mapping created"}


@router.delete(
    "/referral-mrr/account-mappings/{account_number}",
    status_code=204,
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.edit"))],
)
async def delete_account_mapping(
    account_number: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Delete an account-number mapping."""
    _require_edit(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    before = await db["account_mappings"].find_one(
        {"account_number": account_number}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Account mapping not found")

    async with crud_envelope(
        module="finance",
        entity="referral_account_mapping",
        action="deleted",
        resource_id=account_number,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["account_mappings"].delete_one({"account_number": account_number})
        ctx.set_after(None)


# ---------------------------------------------------------------------------
# CSV parse endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/referral-mrr/parse-csv",
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.edit"))],
)
async def parse_csv(
    file: UploadFile = File(...),
    mappings: Optional[str] = Form(None),
    linking_column: Optional[str] = Form(None),
    amount_column: Optional[str] = Form(None),
    period: Optional[str] = Form(None),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Parse an uploaded CSV, match account numbers to deals, and auto-create commission records.

    This is a multipart FormData endpoint. AEGISAXIOM frontend uses raw fetch() (not safeXhr)
    to call this route — do not change the multipart contract.

    Period format: YYYY-MM (e.g. '2026-01' for January 2026).
    """
    _require_edit(current_user)

    if not period:
        raise HTTPException(status_code=400, detail="Commission period is required")

    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    caller_id = _user_id(current_user)

    try:
        contents = await file.read()
        decoded = contents.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(decoded))

        header_mappings: dict = json.loads(mappings) if mappings else {}

        logger.info(
            "parse-csv — linking=%s amount=%s mappings=%s",
            linking_column,
            amount_column,
            header_mappings,
        )

        # Load all account mappings once
        account_map_docs = await db["account_mappings"].find({}, {"_id": 0}).to_list(10000)
        account_lookup: Dict[str, dict] = {doc["account_number"]: doc for doc in account_map_docs}
        logger.info("parse-csv — loaded %d account mappings", len(account_lookup))

        matched_rows: list = []
        unmatched_rows: list = []
        skipped_rows = 0

        for idx, row in enumerate(csv_reader):
            account_num = None
            if linking_column and linking_column in row:
                account_num = row[linking_column].strip()

            if not account_num:
                skipped_rows += 1
                logger.warning("Row %d: no account number in column '%s'", idx, linking_column)
                continue

            # Parse amount
            amount = 0.0
            if amount_column and amount_column in row:
                amount_str = row[amount_column].replace("$", "").replace(",", "").strip()
                try:
                    amount = float(amount_str)
                except ValueError:
                    logger.warning("Row %d: could not parse amount '%s'", idx, amount_str)

            # Extract partner name via field mappings
            partner_name: Optional[str] = None
            for csv_field, aegisaxiom_field in header_mappings.items():
                if aegisaxiom_field == "partner_name" and csv_field in row:
                    partner_name = row[csv_field].strip()
                    break

            if account_num in account_lookup:
                mapping = account_lookup[account_num]
                matched_rows.append(
                    {
                        "account_number": account_num,
                        "deal_id": mapping["deal_id"],
                        "deal_name": mapping.get("deal_name", ""),
                        "partner_name": partner_name or mapping.get("partner_name", ""),
                        "amount": amount,
                        "commission_plan": mapping.get("commission_plan", ""),
                        "linking_field_name": mapping.get("linking_field_name")
                        or linking_column
                        or "Account Number",
                        "raw_data": row,
                    }
                )
                logger.info("Row %d: matched %s → deal %s", idx, account_num, mapping["deal_id"])
            else:
                unmatched_rows.append(
                    {
                        "account_number": account_num,
                        "partner_name": partner_name,
                        "amount": amount,
                        "linking_field_name": linking_column or "Account Number",
                        "raw_data": row,
                    }
                )
                logger.info("Row %d: unmatched %s amount=$%.2f", idx, account_num, amount)

        logger.info(
            "parse-csv complete: %d matched, %d unmatched, %d skipped",
            len(matched_rows),
            len(unmatched_rows),
            skipped_rows,
        )

        # Cache commission plans to avoid redundant DB queries
        commission_plans_cache: Dict[str, Any] = {}
        for matched_row in matched_rows:
            deal_id = matched_row["deal_id"]
            if deal_id not in commission_plans_cache:
                plan = await db["commissions"].find_one(
                    {"deal_id": deal_id, "tiers": {"$exists": True}}, {"_id": 0}
                )
                commission_plans_cache[deal_id] = plan

        commissions_created = 0
        commissions_skipped = 0
        deals_without_plans: list = []
        deals_without_plans_set: set = set()

        for matched_row in matched_rows:
            deal_id = matched_row["deal_id"]
            account_num = matched_row["account_number"]
            amount = matched_row["amount"]

            commission_plan = commission_plans_cache.get(deal_id)
            if not commission_plan or not commission_plan.get("tiers"):
                logger.warning("Deal %s has no commission plan", deal_id)
                if deal_id not in deals_without_plans_set:
                    deals_without_plans_set.add(deal_id)
                    deals_without_plans.append(
                        {
                            "deal_id": deal_id,
                            "deal_name": matched_row["deal_name"],
                            "account_number": account_num,
                            "amount": amount,
                        }
                    )
                continue

            ref_record_id = gen_id("refmrr_")
            ref_doc: Dict[str, Any] = {
                "record_id": ref_record_id,
                "type": "referral_mrr",
                "account_number": account_num,
                "deal_id": deal_id,
                "deal_name": matched_row["deal_name"],
                "partner_name": matched_row["partner_name"],
                "amount": amount,
                "period": period,
                "linking_field_name": matched_row["linking_field_name"],
                "status": "processed",
                "raw_data": matched_row["raw_data"],
                "created_at": _utc_now_iso(),
                "created_by": caller_id,
            }
            await db["referral_commissions"].insert_one(ref_doc)

            for tier in commission_plan["tiers"]:
                for user_split in tier.get("users", []):
                    user_split_id = user_split.get("user_id")
                    user_split_name = user_split.get("user_name")
                    split_pct = user_split.get("split_percentage", 0)

                    if not user_split_id or split_pct == 0:
                        continue

                    user_commission_amount = (
                        (amount * tier.get("percentage", 0) / 100) * (split_pct / 100)
                    )

                    # Duplicate check in commissions collection
                    existing_in_main = await db["commissions"].find_one(
                        {
                            "source": "referral_mrr",
                            "deal_id": deal_id,
                            "account_number": account_num,
                            "user_id": user_split_id,
                            "period": period,
                        }
                    )
                    if existing_in_main:
                        logger.info("Skipping duplicate commission for user %s", user_split_name)
                        commissions_skipped += 1
                        continue

                    commission_id = gen_id("comm_")
                    user_comm_doc: Dict[str, Any] = {
                        "commission_id": commission_id,
                        "source": "referral_mrr",
                        "source_record_id": ref_record_id,
                        "deal_id": deal_id,
                        "deal_name": matched_row["deal_name"],
                        "user_id": user_split_id,
                        "user_name": user_split_name,
                        "commission_type": "referral_mrr",
                        "tier_number": tier.get("tier_number"),
                        "tier_name": tier.get("tier_name"),
                        "commission_rate": split_pct,
                        "amount_owed": user_commission_amount,
                        "amount_paid": 0,
                        "potential_amount": user_commission_amount,
                        "status": "pending",
                        "period": period,
                        "partner_name": matched_row["partner_name"],
                        "account_number": account_num,
                        "created_at": _utc_now_iso(),
                        "created_by": caller_id,
                    }
                    await db["local_commissions"].insert_one(user_comm_doc)
                    await db["commissions"].insert_one(user_comm_doc)
                    commissions_created += 1

        logger.info(
            "parse-csv auto-created %d commissions, skipped %d, %d deals without plans",
            commissions_created,
            commissions_skipped,
            len(deals_without_plans),
        )

        return {
            "matched": matched_rows,
            "unmatched": unmatched_rows,
            "total_rows": len(matched_rows) + len(unmatched_rows),
            "headers": list(csv_reader.fieldnames) if hasattr(csv_reader, "fieldnames") else [],
            "linking_column": linking_column,
            "amount_column": amount_column,
            "period": period,
            "commissions_created": commissions_created,
            "commissions_skipped": commissions_skipped,
            "deals_without_plans": deals_without_plans,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("CSV parse error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to parse CSV: {exc}") from exc


# ---------------------------------------------------------------------------
# Commit commissions endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/referral-mrr/commit-commissions",
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.edit"))],
)
async def commit_commissions(
    request_body: CommitCommissionsRequest,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Commit matched commission rows to the database.

    For each row with a mapped deal:
      1. Save a referral source record to referral_commissions for tracking.
      2. Look up the deal's commission plan.
      3. Create individual user commission records based on plan tiers.

    Wraps the entire batch in a single crud_envelope with action="created".
    """
    _require_edit(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)
    caller_id = _user_id(current_user)

    try:
        committed_records: list = []
        user_commissions_created: list = []

        async with crud_envelope(
            module="finance",
            entity="referral_commission",
            action="created",
            resource_id=f"batch:{len(request_body.rows)}",
            current_user=current_user,
            db=db,
            request=request,
        ) as ctx:
            for row in request_body.rows:
                deal_id = row.get("deal_id")
                amount = row.get("amount", 0)

                ref_record_id = gen_id("refmrr_")
                ref_doc: Dict[str, Any] = {
                    "record_id": ref_record_id,
                    "type": "referral_mrr",
                    "account_number": row.get("account_number"),
                    "deal_id": deal_id,
                    "deal_name": row.get("deal_name"),
                    "partner_name": row.get("partner_name"),
                    "amount": amount,
                    "period": request_body.period,
                    "linking_field_name": row.get("linking_field_name", "Account Number"),
                    "status": "processed",
                    "raw_data": row.get("raw_data", {}),
                    "created_at": _utc_now_iso(),
                    "created_by": caller_id,
                }
                await db["referral_commissions"].insert_one(ref_doc)
                committed_records.append(ref_record_id)

                if deal_id:
                    commission_plan = await db["commissions"].find_one(
                        {"deal_id": deal_id}, {"_id": 0}
                    )
                    if commission_plan and commission_plan.get("tiers"):
                        for tier in commission_plan["tiers"]:
                            for user_split in tier.get("users", []):
                                user_split_id = user_split.get("user_id")
                                user_split_name = user_split.get("user_name")
                                split_pct = user_split.get("split_percentage", 0)

                                if not user_split_id or split_pct == 0:
                                    continue

                                user_commission_amount = (
                                    (amount * tier.get("percentage", 0) / 100)
                                    * (split_pct / 100)
                                )

                                commission_id = gen_id("comm_")
                                user_comm_doc: Dict[str, Any] = {
                                    "commission_id": commission_id,
                                    "source": "referral_mrr",
                                    "source_record_id": ref_record_id,
                                    "deal_id": deal_id,
                                    "deal_name": row.get("deal_name"),
                                    "user_id": user_split_id,
                                    "user_name": user_split_name,
                                    "type": "referral_mrr",
                                    "tier_number": tier.get("tier_number"),
                                    "tier_name": tier.get("tier_name"),
                                    "rate": split_pct,
                                    "owed": user_commission_amount,
                                    "paid": 0,
                                    "status": "pending",
                                    "period": request_body.period,
                                    "partner_name": row.get("partner_name"),
                                    "account_number": row.get("account_number"),
                                    "created_at": _utc_now_iso(),
                                    "created_by": caller_id,
                                }
                                await db["local_commissions"].insert_one(user_comm_doc)
                                user_commissions_created.append(commission_id)

            ctx.set_after(
                {
                    "committed_records": committed_records,
                    "user_commissions_created": user_commissions_created,
                }
            )

        return {
            "message": (
                f"Successfully committed {len(committed_records)} referral records "
                f"and created {len(user_commissions_created)} user commissions"
            ),
            "referral_records": committed_records,
            "user_commissions": user_commissions_created,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to commit commissions: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Referral commissions list endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/referral-mrr/commissions",
    dependencies=[Depends(require_ai_permission("finance.referral_mrr.view"))],
)
async def list_referral_commissions(
    period: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Return paginated referral MRR commission records from referral_commissions.

    Note: this is referral-specific — the /finance/commissions endpoint (Thread 2E)
    serves records from the main commissions collection. These two endpoints are
    intentionally distinct.
    """
    _require_view(current_user)
    db = get_db()
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    query: Dict[str, Any] = {}
    if period:
        query["period"] = period
    if status:
        query["status"] = status

    total = await db["referral_commissions"].count_documents(query)
    docs = (
        await db["referral_commissions"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {"commissions": docs, "total": total, "skip": skip, "limit": limit}
