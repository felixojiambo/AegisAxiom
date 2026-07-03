from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import datetime, date, timezone
import logging
from database import get_db
from auth_helper import get_current_user, has_perm
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()
logger = logging.getLogger(__name__)

register_module_config("agent_dashboard", "users", "user_id", "Admin")
register_module_events("agent_dashboard", "agent_metric", [])
register_fields("agent_dashboard", "agent_metric", [
    {"name": "total_commissions", "type": "number", "label": "Total Commissions", "description": "Cumulative commission earnings"},
    {"name": "active_deals_count", "type": "number", "label": "Active Deals", "description": "Number of currently active deals"},
    {"name": "total_revenue", "type": "number", "label": "Total Revenue", "description": "Total revenue across all deals"},
])

# ----- helpers -----

def _build_commission_filter(role: str, email: str, account_id: Optional[str], zoho_account_id: Optional[str], status: Optional[str] = None) -> dict:
    """Build the correct MongoDB filter for zoho_commissions based on role."""
    if role == "agent":
        if zoho_account_id:
            filt = {"agent_of_records_id": zoho_account_id}
        elif account_id:
            filt = {"agent_of_records_id": account_id}
        else:
            return {"_id": {"$exists": False}}
    elif role in ("sales_rep", "director"):
        filt = {"sales_rep_email": email.lower()}
    elif role == "admin":
        filt = {}
    else:
        return {"_id": {"$exists": False}}

    if status and status != "all":
        if role == "agent":
            filt["agent_commission_status"] = status
        elif role in ("sales_rep", "director"):
            filt["sales_rep_commission_status"] = status

    return filt


def _build_deals_filter(role: str, email: str, account_id: Optional[str], zoho_account_id: Optional[str]) -> dict:
    if role == "agent":
        if zoho_account_id:
            return {"agent_of_record_id": zoho_account_id}
        elif account_id:
            return {"agent_of_record_id": account_id}
        return {"_id": {"$exists": False}}
    elif role in ("sales_rep", "director"):
        return {"deal_owner_email": email.lower()}
    elif role == "admin":
        return {}
    return {"_id": {"$exists": False}}


def _summarise(comms: list, role: str) -> dict:
    status_field = "agent_commission_status" if role == "agent" else "sales_rep_commission_status"
    amount_field  = "agent_commission"        if role == "agent" else "sales_rep_commission"

    owed      = [c for c in comms if c.get(status_field) == "Owed"]
    paid      = [c for c in comms if c.get(status_field) == "Paid"]
    potential = [c for c in comms if c.get(status_field) == "Potential"]

    return {
        "owed_count":           len(owed),
        "paid_count":           len(paid),
        "potential_count":      len(potential),
        "commission_owed":      sum(c.get(amount_field, 0) for c in owed),
        "commission_paid":      sum(c.get(amount_field, 0) for c in paid),
        "commission_potential": sum(c.get(amount_field, 0) for c in potential),
    }

def _safe_date_only(value, field_name: str, record_id: str) -> Optional[str]:
    """Return YYYY-MM-DD for datetime/date values, normalize ISO strings when possible."""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, str):
        s = value.strip()

        # Already date-only
        if len(s) == 10:
            try:
                return date.fromisoformat(s).isoformat()
            except ValueError:
                pass

        # ISO datetime string
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            logger.warning(
                f"_safe_date_only: {field_name} for {record_id} is a non-ISO string "
                f"({s[:30]!r}); passing through as-is."
            )
            return s

    logger.warning(
        f"_safe_date_only: {field_name} for {record_id} has unexpected type "
        f"{type(value).__name__}; returning None."
    )
    return None


async def _enrich_with_invoice_dates(db, commissions: list) -> None:
    """Attach invoice_issued_date and invoice_due_date to each commission
    by looking up local invoices via invoice_no -> invoice_number.

    NOTE on `issued_date`: sourced from the `invoices.issued_date` field, which
    is the business date the invoice was formally issued. Defaults to the
    creation date if not explicitly set by finance. See the finance invoice
    form and the backfill script (scripts/backfill_invoice_issued_date.py) for
    how historical invoices were migrated from `created_at`.

    NOTE on date serialization: invoice writers store tz-aware UTC datetimes.
    We use _safe_date_only() to drop the time component before sending to the
    frontend. The frontend's fmtDate() helper parses YYYY-MM-DD strings via
    the local-time Date constructor (not new Date(string), which parses as
    UTC midnight) — that's the other half of the fix. Both halves are
    required: backend stripping alone is not enough, because the browser
    parses date-only ISO strings as UTC, causing a one-day shift for users
    west of UTC.

    NOTE on matching: Zoho's `invoice_no` and AEGISAXIOM's `invoice_number` may use
    different numbering conventions. Matches depend on data alignment between
    systems. The diagnostic log below reports match rate per request so we can
    catch silent failures post-deploy.
    """
    invoice_nos = list({c.get("invoice_no") for c in commissions if c.get("invoice_no")})
    invoice_map = {}
    if invoice_nos:
        invoice_docs = await db.invoices.find(
            {"invoice_number": {"$in": invoice_nos}},
            {"_id": 0, "invoice_number": 1, "issued_date": 1, "due_date": 1}
        ).to_list(len(invoice_nos))
        for inv in invoice_docs:
            invoice_map[inv["invoice_number"]] = {
                "issued_date": _safe_date_only(inv.get("issued_date"), "invoices.issued_date", inv["invoice_number"]),
                "due_date": _safe_date_only(inv.get("due_date"), "invoices.due_date", inv["invoice_number"]),
            }

        matched = len(invoice_map)
        total = len(invoice_nos)
        if matched == 0:
            logger.warning(
                f"_enrich_with_invoice_dates: 0/{total} commission invoice_nos matched local invoices "
                f"(sample: {invoice_nos[:3]}). Check Zoho invoice_no vs AEGISAXIOM invoice_number format."
            )
        else:
            logger.info(
                f"_enrich_with_invoice_dates: {matched}/{total} commission invoice_nos matched local invoices"
            )

    for c in commissions:
        inv = invoice_map.get(c.get("invoice_no"))
        c["invoice_issued_date"] = inv["issued_date"] if inv else None
        c["invoice_due_date"] = inv["due_date"] if inv else None

# ----- endpoints -----

@router.get("/agent-dashboard")
async def get_agent_dashboard(
    view_user_id: Optional[str] = Query(None),      # admin: view a specific user
    view_account_id: Optional[str] = Query(None),    # admin: view by account (agent context)
    status: Optional[str] = Query(None),             # Paid | Owed | Potential | all
    payment_date: Optional[str] = Query(None),       # legacy single date (kept for backwards compat)
    date_from: Optional[str] = Query(None),          # ISO date string YYYY-MM-DD
    date_to: Optional[str] = Query(None),            # ISO date string YYYY-MM-DD
    current_user=Depends(get_current_user),
):
    db = get_db()

    # Admin-only overrides
    if view_user_id or view_account_id:
        if not has_perm(current_user, "users.view"):
            raise HTTPException(status_code=403, detail="Insufficient permissions to view other dashboards")

    # --- Determine whose dashboard to render ---
    if view_user_id:
        doc = await db.users.find_one({"user_id": view_user_id}, {"_id": 0, "password_hash": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="User not found")
        from models import User
        doc.setdefault("permissions", [])
        target = User(**doc)
        target_role  = target.role
        target_email = target.email
        target_account_id = target.account_id
        acc = await db.accounts.find_one({"account_id": target.account_id}, {"_id": 0, "zoho_account_id": 1, "name": 1}) if target.account_id else None
        target_zoho_id = acc.get("zoho_account_id") if acc else None
        view_label = f"{target.name} ({target.role})"

    elif view_account_id:
        # Admin viewing by Account — show agent-context data for that account
        acc = await db.accounts.find_one({"account_id": view_account_id}, {"_id": 0, "zoho_account_id": 1, "name": 1})
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found")
        target_role   = "agent"
        target_email  = ""
        target_account_id   = view_account_id
        target_zoho_id      = acc.get("zoho_account_id")
        view_label = acc.get("name", view_account_id)

    else:
        # Current user's own dashboard
        target_role   = current_user.role
        target_email  = current_user.email
        target_account_id   = current_user.account_id
        acc = await db.accounts.find_one({"account_id": current_user.account_id}, {"_id": 0, "zoho_account_id": 1}) if current_user.account_id else None
        target_zoho_id = acc.get("zoho_account_id") if acc else None
        view_label = None

    allowed_roles = {"agent", "sales_rep", "director", "admin", "finance"}
    if target_role not in allowed_roles:
        raise HTTPException(status_code=400, detail=f"Dashboard not available for role '{target_role}'")

    comm_filt = _build_commission_filter(target_role, target_email, target_account_id, target_zoho_id, status)
    deal_filt = _build_deals_filter(target_role, target_email, target_account_id, target_zoho_id)

    # Date filtering — prefer date_from/date_to range, fall back to legacy payment_date
    if date_from or date_to:
        date_range = {}
        if date_from:
            date_range["$gte"] = date_from
        if date_to:
            date_range["$lte"] = date_to
        comm_filt["payment_date"] = date_range
        deal_filt["close_date"] = date_range
    elif payment_date:
        comm_filt["payment_date"] = payment_date

    commissions = await db.zoho_commissions.find(comm_filt, {"_id": 0}).sort("payment_date", -1).to_list(1000)

    # Enrich commissions with invoice dates (issued + due) from invoices collection
    await _enrich_with_invoice_dates(db, commissions)

    deals = await db.zoho_deals.find(deal_filt, {"_id": 0}).sort("synced_at", -1).to_list(500)

    last_sync_doc = await db.zoho_commissions.find_one({}, {"_id": 0, "synced_at": 1}, sort=[("synced_at", -1)])
    last_synced = last_sync_doc.get("synced_at") if last_sync_doc else None

    # Summary using full unfiltered data (no status/date filter)
    base_filt = {k: v for k, v in comm_filt.items() if k not in ["agent_commission_status", "sales_rep_commission_status", "payment_date"]}
    summary_comms = await db.zoho_commissions.find(base_filt, {"_id": 0}).to_list(1000)

    return {
        "summary": _summarise(summary_comms, target_role),
        "commissions": commissions,
        "deals": deals,
        "last_synced": last_synced.isoformat() if last_synced else None,
        "target_role": target_role,
        "target_email": target_email,
        "view_label": view_label,
        "zoho_connected": bool(await db.oauth_tokens.find_one({"provider": "zoho"}, {"_id": 0})),
        "total_zoho_commission_records": await db.zoho_commissions.count_documents({}),
    }


@router.get("/agent-dashboard/accounts-list")
async def get_dashboard_accounts(current_user=Depends(get_current_user)):
    """Admin/Director: list of accounts (for the 'View As Account' filter dropdown)."""
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    accounts = await db.accounts.find(
        {},
        {"_id": 0, "account_id": 1, "name": 1, "account_type": 1}
    ).sort("name", 1).to_list(2000)
    return accounts


@router.get("/agent-dashboard/users-list")
async def get_dashboard_users(role: str = None, current_user=Depends(get_current_user)):
    """Admin/Director: list of agents and/or sales reps (for the 'View As' filter)."""
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    filt = {"is_active": True}
    if role:
        filt["role"] = role
    else:
        filt["role"] = {"$in": ["agent", "sales_rep"]}
    users = await db.users.find(
        filt,
        {"_id": 0, "user_id": 1, "name": 1, "email": 1, "role": 1}
    ).sort("name", 1).to_list(200)
    return users