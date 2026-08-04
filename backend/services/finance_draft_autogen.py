"""Finance Draft Auto-Generation Service.

Shared logic for generating finance_drafts docs from approved time entries.
Called by:
  - POST /api/finance/drafts/auto-generate-ar  (route: finance_draft_autogen.py)
  - POST /api/finance/drafts/auto-generate-ap  (route: finance_draft_autogen.py)
  - Future: workflow triggers (time-entry approval events)

Porting notes vs legacy ar_invoices.py / ap_bills.py:
  - ar_invoices: status "pm_approved" + is_billable=True, grouped by contract_id,
    rate from contract.resource_types[].billable_rate
  - ap_bills: status "approved", grouped by contractor_business_id or user_id,
    rate from entry.cost_rate; hours from duration_hours or hours field
  - New schema: total -> amount, account_id/name -> counterparty_id/counterparty
  - ID: gen_id("fcc_draft_") replaces INV-YYYY-NNN / AP-YYYY-NNN
  - Deduplication: check existing finance_drafts by source_origin + period + group key
    (matches legacy check-before-insert behavior, now in the new collection)

Gotcha for future agents: legacy time entries use EITHER "hours" or "duration_hours"
for the numeric hours value (ap_bills used "duration_hours"; ar_invoices used "hours").
The service normalises this with _get_hours(entry). Similarly, "time_entry_id" vs
"entry_id" varies — ar_invoices used "entry_id", ap_bills used "time_entry_id".
_get_entry_id(entry) handles this transparently.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from models import gen_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load_user_map(user_ids: List[str]) -> Dict[str, Any]:
    """``{user_id: User}`` for every id referenced by a time-entry batch.

    Phase 2a completion: `users` is Postgres. These lookups used to sit inside
    the per-entry loops as `db["users"].find_one(...)`, so a batch of N entries
    issued N queries; one prefetch keeps a single session for the whole build
    and drops the N+1. Callers read only `contractor_business_id` and `name`,
    both of which ride on the `User` DTO.
    """
    from db.session import get_sessionmaker
    from repositories.users import UserRepository

    ids = sorted({uid for uid in user_ids if uid})
    if not ids:
        return {}
    sm = get_sessionmaker()
    async with sm() as pg:
        found = await UserRepository(pg).get_users_by_ids(ids)
    return {u.user_id: u for u in found}


def _get_hours(entry: dict) -> float:
    """Normalise the two hour-field names used across legacy time entries."""
    return float(entry.get("duration_hours") or entry.get("hours") or 0.0)


def _get_entry_id(entry: dict) -> str:
    """Normalise the two entry-id field names used across legacy time entries."""
    return entry.get("time_entry_id") or entry.get("entry_id") or ""


def _str_date(val: Any) -> Optional[str]:
    """Coerce a datetime or string to ISO date string, or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    return str(val)


# ---------------------------------------------------------------------------
# AR auto-generation
# ---------------------------------------------------------------------------

async def build_ar_drafts(
    db: Any,
    period_start: str,
    period_end: str,
    contract_id_filter: Optional[str],
    created_by: str,
    dry_run: bool,
) -> Tuple[List[dict], List[dict], List[str]]:
    """Generate (or preview) AR invoice drafts from pm_approved billable time entries.

    Returns (drafts_written, skipped, warnings).
    In dry_run mode, drafts_written contains the would-be docs but nothing is inserted.

    N2 note: the auto-generate route always calls this with dry_run=True and handles
    all writes inside a crud_envelope to ensure policy/audit runs before insert_one.
    The dry_run=False path is preserved for any future callers that need direct writes
    (e.g., scheduled batch jobs that manage their own audit trail).
    """
    # Query approved billable time entries for the period
    time_query: Dict[str, Any] = {
        "status": "pm_approved",
        "is_billable": True,
        "date": {"$gte": period_start, "$lte": period_end},
    }
    if contract_id_filter:
        time_query["contract_id"] = contract_id_filter

    time_entries = await db["time_entries"].find(time_query, {"_id": 0}).to_list(10000)

    if not time_entries:
        return [], [{"reason": "no_approved_billable_entries", "period": f"{period_start}/{period_end}"}], []

    # Group by contract_id
    by_contract: Dict[str, List[dict]] = {}
    for entry in time_entries:
        cid = entry.get("contract_id")
        if not cid:
            continue
        by_contract.setdefault(cid, []).append(entry)

    drafts_out: List[dict] = []
    skipped: List[dict] = []
    warnings: List[str] = []
    now_iso = _utc_now_iso()

    for cid, entries in by_contract.items():
        # Deduplication: skip if a draft for this contract+period already exists
        existing = await db["finance_drafts"].find_one(
            {
                "source_origin": "auto_time_entries",
                "kind": "ar_invoice",
                "source_contract_id": cid,
                "source_period_start": period_start,
                "source_period_end": period_end,
            }
        )
        if existing:
            skipped.append({
                "reason": "draft_already_exists",
                "contract_id": cid,
                "existing_draft_id": existing.get("draft_id"),
            })
            continue

        # Load contract for counterparty info and billable rates
        contract = await db["contracts"].find_one({"contract_id": cid}, {"_id": 0})
        if not contract:
            skipped.append({"reason": "contract_not_found", "contract_id": cid})
            continue

        counterparty_id = contract.get("account_id")
        counterparty = contract.get("account_name") or contract.get("name") or cid

        # Build line items; look up billable_rate from contract.resource_types
        line_items: List[dict] = []
        total_amount = 0.0

        for entry in sorted(entries, key=lambda e: e.get("date", "")):
            billable_rate = 0.0
            for rt in contract.get("resource_types", []):
                assigned_users = rt.get("assigned_users", [])
                if any(u.get("user_id") == entry.get("user_id") for u in assigned_users):
                    billable_rate = float(rt.get("billable_rate", 0))
                    break

            hours = _get_hours(entry)
            line_amount = hours * billable_rate
            total_amount += line_amount

            line_items.append({
                "description": f"{entry.get('user_name', 'Contractor')}: {entry.get('notes', 'Time entry')} ({entry.get('date', '')})",
                "quantity": hours,
                "rate": billable_rate,
                "amount": line_amount,
            })

        if not line_items:
            skipped.append({"reason": "no_billable_line_items", "contract_id": cid})
            continue

        draft_id = gen_id("fcc_draft_")
        draft: Dict[str, Any] = {
            "draft_id": draft_id,
            "kind": "ar_invoice",
            "counterparty": counterparty,
            "counterparty_id": counterparty_id,
            "amount": round(total_amount, 2),
            "memo": f"Auto-generated AR invoice for {period_start} to {period_end}",
            "line_items": line_items,
            "sync_status": "Draft",
            "qbo_id": None,
            "error": None,
            "source_origin": "auto_time_entries",
            "source_period_start": period_start,
            "source_period_end": period_end,
            "source_contractor_id": None,
            "source_contract_id": cid,
            "created_at": now_iso,
            "updated_at": now_iso,
            "created_by": created_by,
        }

        if not dry_run:
            await db["finance_drafts"].insert_one(draft)
            draft.pop("_id", None)

        drafts_out.append(draft)

    return drafts_out, skipped, warnings


# ---------------------------------------------------------------------------
# AP auto-generation
# ---------------------------------------------------------------------------

async def build_ap_drafts(
    db: Any,
    period_start: str,
    period_end: str,
    contractor_user_id_filter: Optional[str],
    created_by: str,
    dry_run: bool,
) -> Tuple[List[dict], List[dict], List[str]]:
    """Generate (or preview) AP bill drafts from approved time entries.

    Returns (drafts_written, skipped, warnings).
    """
    time_query: Dict[str, Any] = {
        "status": "approved",
        "date": {"$gte": period_start, "$lte": period_end},
    }
    if contractor_user_id_filter:
        time_query["user_id"] = contractor_user_id_filter

    time_entries = await db["time_entries"].find(time_query, {"_id": 0}).to_list(10000)

    if not time_entries:
        return [], [{"reason": "no_approved_entries", "period": f"{period_start}/{period_end}"}], []

    # One Postgres round-trip for every user this batch touches (see
    # _load_user_map), instead of one per entry inside the loops below.
    user_map = await _load_user_map([e.get("user_id") for e in time_entries])

    # Group by contractor_business_id (preferred) or user_id
    # Each group key maps to metadata + entries
    grouped: Dict[str, Dict[str, Any]] = {}

    for entry in time_entries:
        uid = entry.get("user_id")
        if not uid:
            continue

        user = user_map.get(uid)
        if not user:
            continue

        business_id = user.contractor_business_id
        group_key = business_id if business_id else uid

        if group_key not in grouped:
            grouped[group_key] = {
                "contractor_business_id": business_id,
                "contractor_user_id": uid if not business_id else None,
                "entries": [],
            }
        grouped[group_key]["entries"].append(entry)

    drafts_out: List[dict] = []
    skipped: List[dict] = []
    warnings: List[str] = []
    now_iso = _utc_now_iso()

    for group_key, group_data in grouped.items():
        business_id = group_data["contractor_business_id"]
        contractor_uid = group_data["contractor_user_id"]

        # Deduplication: check finance_drafts
        dedup_query: Dict[str, Any] = {
            "source_origin": "auto_time_entries",
            "kind": "ap_bill",
            "source_period_start": period_start,
            "source_period_end": period_end,
        }
        if business_id:
            dedup_query["source_contractor_id"] = business_id
        else:
            dedup_query["source_contractor_id"] = contractor_uid

        existing = await db["finance_drafts"].find_one(dedup_query)
        if existing:
            skipped.append({
                "reason": "draft_already_exists",
                "contractor_id": group_key,
                "existing_draft_id": existing.get("draft_id"),
            })
            continue

        # Resolve counterparty name
        counterparty = ""
        counterparty_id = group_key

        if business_id:
            business = await db["contractor_businesses"].find_one(
                {"business_id": business_id}, {"_id": 0}
            )
            if business:
                counterparty = business.get("business_name", business_id)
            else:
                counterparty = business_id
                warnings.append(f"contractor_business {business_id} not found; using id as name")
        else:
            user = user_map.get(contractor_uid)
            if user:
                counterparty = user.name or contractor_uid
            else:
                counterparty = contractor_uid
                warnings.append(f"user {contractor_uid} not found; using id as name")

        # Build line items grouped by user+project (rolled up, matching legacy behavior)
        line_items_dict: Dict[str, Dict[str, Any]] = {}

        for entry in group_data["entries"]:
            uid = entry.get("user_id", "unknown")
            project_id = entry.get("project_id", "none")
            line_key = f"{uid}_{project_id}"

            if line_key not in line_items_dict:
                entry_user = user_map.get(uid)
                project = None
                if entry.get("project_id"):
                    project = await db["projects"].find_one(
                        {"project_id": entry["project_id"]}, {"_id": 0}
                    )

                user_name = (entry_user.name or uid) if entry_user else uid
                project_name = project.get("name", "") if project else ""
                description = f"{user_name}"
                if project_name:
                    description += f" — {project_name}"

                # Wave 4 (contracts-time-mgmt): prefer the cost rate
                # snapshot frozen on the entry over the legacy
                # entry.cost_rate field. Wave 3 entries write
                # cost_rate_snapshot but leave cost_rate unset; reading
                # cost_rate alone would silently zero out the AP draft.
                _initial_rate = (
                    entry.get("cost_rate_snapshot")
                    if entry.get("cost_rate_snapshot") is not None
                    else entry.get("cost_rate", 0)
                )
                line_items_dict[line_key] = {
                    "description": description,
                    "quantity": 0.0,
                    "rate": float(_initial_rate or 0),
                    "amount": 0.0,
                }

            hours = _get_hours(entry)
            _rate = (
                entry.get("cost_rate_snapshot")
                if entry.get("cost_rate_snapshot") is not None
                else entry.get("cost_rate", 0)
            )
            rate = float(_rate or 0)
            line_items_dict[line_key]["quantity"] += hours
            line_items_dict[line_key]["amount"] += hours * rate

        if not line_items_dict:
            skipped.append({"reason": "no_billable_line_items", "contractor_id": group_key})
            continue

        line_items = list(line_items_dict.values())
        total_amount = sum(li["amount"] for li in line_items)

        draft_id = gen_id("fcc_draft_")
        # source_contractor_id = business_id if grouped by business, else user_id
        source_contractor_id = business_id if business_id else contractor_uid

        draft: Dict[str, Any] = {
            "draft_id": draft_id,
            "kind": "ap_bill",
            "counterparty": counterparty,
            "counterparty_id": counterparty_id,
            "amount": round(total_amount, 2),
            "memo": f"Auto-generated AP bill for {period_start} to {period_end}",
            "line_items": line_items,
            "sync_status": "Draft",
            "qbo_id": None,
            "error": None,
            "source_origin": "auto_time_entries",
            "source_period_start": period_start,
            "source_period_end": period_end,
            "source_contractor_id": source_contractor_id,
            "source_contract_id": None,
            "created_at": now_iso,
            "updated_at": now_iso,
            "created_by": created_by,
        }

        if not dry_run:
            await db["finance_drafts"].insert_one(draft)
            draft.pop("_id", None)

        drafts_out.append(draft)

    return drafts_out, skipped, warnings
