"""Enhanced Contract Management with BOH, BOA, Fixed Fee, Monthly types."""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import re
import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from db.session import get_session
from repositories.users import UserRepository
from auth_helper import get_current_user, get_contract_filter, has_perm, verify_ownership
from middleware.ai_authz import require_ai_permission
from services.audit_service import log_action
from services.operations.contract_financials import calculate_contract_financials
from services.policy_engine import evaluate_policies
from services.policy_helpers import enforce_policy_result
from services.field_registry import register_fields, register_module_events, register_module_config
from services.field_options import (
    CONTRACT_APPROVAL_STATUSES,
    CONTRACT_STATUSES,
    CONTRACT_TYPES,
)
from services.event_bus import emit_event

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_config("contracts", "contracts", "contract_id")
register_module_events("contracts", "contract", ["created", "updated", "deleted", "status_changed"])

# Register fields available for policy evaluation
register_fields("contracts", "contract", [
    {"name": "status", "type": "string", "label": "Contract Status", "options": CONTRACT_STATUSES, "description": "Current contract lifecycle status (draft, active, completed, etc.)"},
    {"name": "contract_type", "type": "string", "label": "Contract Type", "options": CONTRACT_TYPES, "description": "Billing model type (BOH, BOA, fixed_fee, monthly, boh_with_boa)"},
    # Computed fields
    {"name": "cost_rate_changed", "type": "boolean", "label": "Cost Rate Changed", "computed": True, "description": "Whether a resource cost rate was modified in this update"},
    {"name": "user_has_cost_rate_perm", "type": "boolean", "label": "User Has Cost Rate Permission", "computed": True, "description": "Whether the user has permission to modify cost rates"},
    # Wave 0 (contracts-time-mgmt) — field registry only; behavior wired in later waves.
    {"name": "approval_status", "type": "string", "label": "Approval Status", "options": CONTRACT_APPROVAL_STATUSES, "description": "Finance Command Center approval queue state (pending/approved/rejected/changes_requested)"},
    {"name": "approved_by", "type": "string", "label": "Approved By", "description": "user_id of the approver who decided the contract"},
    {"name": "approved_at", "type": "datetime", "label": "Approved At", "description": "Timestamp the contract was approved or rejected"},
    {"name": "approval_notes", "type": "string", "label": "Approval Notes", "description": "Free-text justification recorded with the approval decision"},
    {"name": "bill_and_actuals_active", "type": "boolean", "label": "Bill & Actuals Active", "description": "True once the contract's Block of Hours is exhausted and overflow billing is in effect"},
    {"name": "overage_hours", "type": "number", "label": "Overage Hours", "description": "Hours billed against the Bill on Actuals overflow rate after BOH exhaustion"},
    {"name": "overage_bill_rate", "type": "number", "label": "Overage Bill Rate", "description": "Customer-facing rate applied to overage hours"},
    {"name": "overage_cost_rate", "type": "number", "label": "Overage Cost Rate", "description": "Contractor cost rate applied to overage hours"},
    {"name": "signed_date", "type": "datetime", "label": "Signed Date", "description": "Date the customer signed the contract"},
    {"name": "financial_commit", "type": "number", "label": "Financial Commit", "description": "Committed contract value used for forecasting and revenue recognition"},
    {"name": "forecast", "type": "object", "label": "Forecast Snapshot", "description": "Forecasted hours/revenue snapshot copied from the SOW at approval time"},
    {"name": "source_sow_id", "type": "string", "label": "Source SOW ID", "description": "Identifier of the SOW the contract was created from"},
    {"name": "original_sow_snapshot", "type": "object", "label": "Original SOW Snapshot", "description": "Frozen scope captured at contract approval for audit/variance reporting"},
    {"name": "spirit_summary", "type": "string", "label": "Spirit Summary", "description": "Human-readable intent statement carried alongside the structured scope"},
    {"name": "tenant_id", "type": "string", "label": "Tenant ID", "description": "Tenant the contract belongs to, populated by the multi-tenancy filter"},
    {"name": "import_source", "type": "string", "label": "Import Source", "description": "Origin system of an imported contract (e.g. zoho, manual)"},
    {"name": "external_contract_id", "type": "string", "label": "External Contract ID", "description": "Identifier of the contract in its source system for reconciliation"},
    {"name": "external_source_synced_at", "type": "datetime", "label": "External Source Synced At", "description": "Last time the contract was reconciled against its source system"},
])


# ---- CONTRACTS ----

@router.get(
    "/contracts",
    dependencies=[Depends(require_ai_permission("contracts.view"))],
)
async def get_contracts(
    skip: int = 0,
    limit: int = 100,
    contract_type: Optional[str] = None,
    status: Optional[str] = None,
    account_id: Optional[str] = None,
    test_only: bool = False,
    q: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    db = get_db()
    filt = await get_contract_filter(current_user)
    if contract_type:
        filt = {"$and": [filt, {"contract_type": contract_type}]} if filt else {"contract_type": contract_type}
    if status:
        filt = {"$and": [filt, {"status": status}]} if filt else {"status": status}
    if account_id:
        filt = {"$and": [filt, {"account_id": account_id}]} if filt else {"account_id": account_id}
    # Test data filter: default shows real data only, toggle shows test data only
    test_filter = {"is_test_data": True} if test_only else {"is_test_data": {"$ne": True}}
    filt = {"$and": [filt, test_filter]} if filt else test_filter
    # Server-side free-text search. The previous Contracts.js search
    # filtered client-side over the current page, which made search
    # appear to "miss" contracts that were on a later page. Match the
    # imported-contracts review (May 22, 2026): search across
    # contract_number, name, account_name, external_contract_id,
    # external_ids.zoho_contract_id, contract_type, status.
    if q:
        pattern = {"$regex": re.escape(q.strip()), "$options": "i"}
        q_clause = {
            "$or": [
                {"contract_number": pattern},
                {"name": pattern},
                {"account_name": pattern},
                {"external_contract_id": pattern},
                {"external_ids.zoho_contract_id": pattern},
                {"contract_type": pattern},
                {"status": pattern},
            ]
        }
        filt = {"$and": [filt, q_clause]} if filt else q_clause
    capped_limit = min(limit, 500)
    docs = await db.contracts.find(filt, {"_id": 0}).sort("created_at", -1).skip(skip).limit(capped_limit).to_list(capped_limit)
    total = await db.contracts.count_documents(filt if filt else {})
    # Derived pagination fields. The May 22, 2026 stabilization review
    # asked for page/pages/has_next/has_prev so the frontend pagination
    # control can render "Page N of M" without computing it client-side.
    # We keep skip/limit unchanged for backward compatibility with the
    # existing PaginationControls consumer.
    effective_limit = capped_limit if capped_limit > 0 else limit
    page = (skip // effective_limit) + 1 if effective_limit > 0 else 1
    pages = max(1, -(-total // effective_limit)) if effective_limit > 0 else 1
    has_prev = skip > 0
    has_next = (skip + capped_limit) < total
    # Echo the EFFECTIVE limit (post-cap). Returning the raw requested
    # limit made the frontend pagination math wrong whenever a caller
    # asked for more than 500 — the cursor returned at most 500, but
    # the UI computed has_next against the requested value.
    return {
        "items": docs,
        "total": total,
        "skip": skip,
        "limit": capped_limit,
        "page": page,
        "pages": pages,
        "has_prev": has_prev,
        "has_next": has_next,
    }


@router.post(
    "/contracts",
    dependencies=[Depends(require_ai_permission("contracts.create"))],
)
async def create_contract(data: dict, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "contracts.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()

    contract_type = data.get("contract_type", "fixed_fee")
    # boh_with_boa is the Wave 0 schema-foundation addition. Behavior
    # (BOH-to-BOA overflow debit) lands in Wave 2; Wave 1 only accepts
    # the type at the create boundary so the field surfaces in the UI.
    valid_types = [
        "boh", "boa", "fixed_fee", "monthly", "wholesale_mrr",
        "simplisecure", "simplicomply", "other", "boh_with_boa",
    ]
    # referral_mrr removed - referral deals bypass contract module
    if contract_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid contract type. Must be one of: {', '.join(valid_types)}")

    account = await db.accounts.find_one({"account_id": data.get("account_id")}, {"_id": 0, "name": 1})
    contract_id = f"contract_{uuid.uuid4().hex[:12]}"

    # Process products BEFORE creating doc (for Wholesale MRR)
    products = data.get("products", [])
    for p in products:
        p["cost_price"] = float(p.get("cost_price", 0) or 0)
        p["sell_price"] = float(p.get("sell_price", 0) or 0)
        p["quantity"] = int(p.get("quantity", 1) or 1)
        p.setdefault("pricing_tier", "monthly")  # monthly, quarterly, annual
        p.setdefault("product_id", "")
        p.setdefault("product_name", "")
        p.setdefault("sku", "")
        # Calculate line total
        p["line_total"] = p["sell_price"] * p["quantity"]
        p["line_margin"] = (p["sell_price"] - p["cost_price"]) * p["quantity"]

    doc = {
        "contract_id": contract_id,
        "name": data.get("name", ""),
        "account_id": data.get("account_id", ""),
        "account_name": account["name"] if account else data.get("account_name"),
        "contract_type": contract_type,
        "service_category": data.get("service_category", ""),  # field_services, professional_services, wholesale_mrr
        "status": "active",
        "deal_ids": data.get("deal_ids", []),
        "project_ids": [],
        "assigned_sales_rep_id": data.get("assigned_sales_rep_id"),
        "assigned_agent_id": data.get("assigned_agent_id"),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "notes": data.get("notes", ""),
        "created_by": current_user.user_id,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "products": products,  # Wholesale MRR products
    }

    # Type-specific fields
    # NEW: Resource Types with hours tracking (BOH, BOA, Monthly)
    resource_types = data.get("resource_types", [])
    for rt in resource_types:
        if not rt.get("id"):
            rt["id"] = f"rt_{uuid.uuid4().hex[:8]}"
        rt["sell_rate"] = float(rt.get("sell_rate", 0) or 0)
        rt["hours_purchased"] = float(rt.get("hours_purchased", 0) or 0)
        rt["hours_used"] = float(rt.get("hours_used", 0) or 0)
        rt["hours_remaining"] = rt["hours_purchased"] - rt["hours_used"]
        rt.setdefault("name", "Default")
        rt.setdefault("description", "")
    
    # Resources: workers assigned to resource types
    resources = data.get("resources", [])
    for r in resources:
        r["cost_rate"] = float(r.get("cost_rate", 0) or 0)
        r.setdefault("resource_type_id", "")  # Link to resource_type
        # Backward compatibility: if sell_rate exists, keep it (will be deprecated)
        if "sell_rate" in r:
            r["sell_rate"] = float(r.get("sell_rate", 0) or 0)

    if contract_type == "boh":
        # BOH contracts expire after 6 months of no usage — no fixed end date
        use_by_date = (datetime.now(timezone.utc) + timedelta(days=183)).isoformat()
        
        # Calculate total hours from resource types
        total_hours = sum(rt["hours_purchased"] for rt in resource_types)
        
        doc.update({
            "resource_types": resource_types,
            "resources": resources,
            "total_hours": total_hours,
            "hours_consumed": 0.0,
            "hourly_rate": float(data.get("hourly_rate") or 0),  # deprecated, use resource_types
            "last_usage_date": None,
            "use_by_date": use_by_date,  # 6 months from creation; resets on usage
            "usage_window_months": 6,
            "expiration_warning_sent": False,
            "low_hours_warning_sent": False,
            "hour_adjustments": [],  # [{deal_id, adjustment, date, note}]
        })
        doc["end_date"] = None  # BOH has no fixed end date
    elif contract_type == "boa":
        doc.update({
            "resource_types": resource_types,
            "resources": resources,
            "hourly_rate": float(data.get("hourly_rate") or 0),  # deprecated, use resource_types
            "billing_period": data.get("billing_period", "monthly"),  # weekly, biweekly, monthly
            "invoice_terms": data.get("invoice_terms", "net_30"),  # net_14, net_30, net_45
            "total_hours_logged": 0.0,
            "total_billed": 0.0,
            "estimated_boa_cost": float(data.get("estimated_boa_cost") or 0),  # Field Services estimated cost
        })
    elif contract_type == "fixed_fee":
        doc.update({
            "total_amount": float(data.get("total_amount", 0)),
            "amount_invoiced": 0.0,
            "milestones": data.get("milestones", []),
        })
        # Ensure milestones have IDs
        for i, m in enumerate(doc["milestones"]):
            if not m.get("id"):
                m["id"] = f"ms_{uuid.uuid4().hex[:8]}"
            m.setdefault("status", "pending")
            m["percentage"] = float(m.get("percentage", 0) or 0)
            m.setdefault("amount", doc["total_amount"] * (m["percentage"] / 100))
    elif contract_type == "monthly":
        doc.update({
            "monthly_rate": float(data.get("monthly_rate", 0)),
            "resource_types": resource_types,
            "resources": resources,
            "billing_day": int(data.get("billing_day", 1)),
            "total_invoiced": 0.0,
        })
    elif contract_type == "wholesale_mrr" or data.get("service_category") == "wholesale_mrr":
        # Wholesale MRR: Selling fixed products with locked cost and editable sell price
        total_cost = sum(p["cost_price"] * p["quantity"] for p in products)
        total_sell = sum(p["sell_price"] * p["quantity"] for p in products)
        total_margin = total_sell - total_cost
        
        doc.update({
            "products": products,
            "total_products": len(products),
            "total_cost_monthly": total_cost,
            "total_sell_monthly": total_sell,
            "total_margin_monthly": total_margin,
            "margin_percent": (total_margin / total_sell * 100) if total_sell > 0 else 0,
            "billing_period": data.get("billing_period", "monthly"),
            "invoice_terms": data.get("invoice_terms", "net_30"),
            "total_invoiced": 0.0,
        })

    await db.contracts.insert_one(doc)
    doc.pop("_id", None)
    
    # Calculate financial summaries (Phase 1)
    try:
        financials = calculate_contract_financials(doc)
        await db.contracts.update_one(
            {"contract_id": contract_id},
            {"$set": financials}
        )
        doc.update(financials)
    except Exception as e:
        logger.error(f"Failed to calculate financials for contract {contract_id}: {e}")
        # Continue without financials - not a critical failure
    
    await log_action("contract.create", current_user.user_id, current_user.email, "contract", contract_id, {"name": doc["name"], "type": contract_type})
    await emit_event("contracts", "created", record=doc, user_id=current_user.user_id, user_email=current_user.email)
    return doc


@router.get(
    "/contracts/warnings",
    dependencies=[Depends(require_ai_permission("contracts.view.all"))],
)
async def get_contract_warnings(current_user=Depends(get_current_user)):
    """Get all contracts with active warnings (BOH: 6-month inactivity or <10% remaining)."""
    if not has_perm(current_user, "contracts.view.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    warnings = []
    now = datetime.now(timezone.utc)
    six_months_ago = now - timedelta(days=180)

    # BOH contracts
    boh_contracts = await db.contracts.find(
        {"contract_type": "boh", "status": "active"}, {"_id": 0}
    ).to_list(1000)

    for c in boh_contracts:
        hours_remaining = c.get("total_hours", 0) - c.get("hours_consumed", 0)
        total = c.get("total_hours", 0)
        pct_remaining = (hours_remaining / total * 100) if total > 0 else 0

        # 6-month usage window expiration — BOH must be used within 6 months
        use_by = c.get("use_by_date")
        if use_by:
            if isinstance(use_by, str):
                use_by_dt = datetime.fromisoformat(use_by)
            else:
                use_by_dt = use_by
            if use_by_dt.tzinfo is None:
                use_by_dt = use_by_dt.replace(tzinfo=timezone.utc)

            days_left = (use_by_dt - now).days
            if days_left <= 0:
                # Expired — contract violation
                warnings.append({
                    "contract_id": c["contract_id"],
                    "contract_name": c["name"],
                    "account_name": c.get("account_name"),
                    "warning_type": "usage_window_expired",
                    "message": f"6-month usage window expired on {use_by_dt.strftime('%Y-%m-%d')}. Re-engage or close contract.",
                    "severity": "critical",
                    "use_by_date": use_by_dt.isoformat(),
                    "assigned_sales_rep_id": c.get("assigned_sales_rep_id"),
                    "assigned_agent_id": c.get("assigned_agent_id"),
                })
            elif days_left <= 30:
                # Expiring soon — 30 days warning
                warnings.append({
                    "contract_id": c["contract_id"],
                    "contract_name": c["name"],
                    "account_name": c.get("account_name"),
                    "warning_type": "usage_window_expiring",
                    "message": f"Usage window expires in {days_left} days ({use_by_dt.strftime('%Y-%m-%d')}). Contact deal owner and lead source.",
                    "severity": "high",
                    "use_by_date": use_by_dt.isoformat(),
                    "assigned_sales_rep_id": c.get("assigned_sales_rep_id"),
                    "assigned_agent_id": c.get("assigned_agent_id"),
                })
        else:
            # Fallback: check last_usage_date or created_at
            last_usage = c.get("last_usage_date")
            ref_date = None
            if last_usage:
                ref_date = last_usage if isinstance(last_usage, datetime) else datetime.fromisoformat(str(last_usage))
            elif c.get("created_at"):
                ref_date = c["created_at"] if isinstance(c["created_at"], datetime) else datetime.fromisoformat(str(c["created_at"]))
            if ref_date:
                if ref_date.tzinfo is None:
                    ref_date = ref_date.replace(tzinfo=timezone.utc)
                if ref_date < six_months_ago:
                    warnings.append({
                        "contract_id": c["contract_id"],
                        "contract_name": c["name"],
                        "account_name": c.get("account_name"),
                        "warning_type": "usage_window_expired",
                        "message": f"No usage in 6+ months (since {ref_date.strftime('%Y-%m-%d')}). Re-engage or close.",
                        "severity": "critical",
                        "assigned_sales_rep_id": c.get("assigned_sales_rep_id"),
                        "assigned_agent_id": c.get("assigned_agent_id"),
                    })

        # <10% remaining warning
        if total > 0 and pct_remaining < 10 and pct_remaining >= 0:
            warnings.append({
                "contract_id": c["contract_id"],
                "contract_name": c["name"],
                "account_name": c.get("account_name"),
                "warning_type": "low_hours",
                "message": f"Only {hours_remaining:.1f}h remaining ({pct_remaining:.0f}% of {total:.0f}h)",
                "severity": "medium",
                "assigned_sales_rep_id": c.get("assigned_sales_rep_id"),
                "assigned_agent_id": c.get("assigned_agent_id"),
            })

    return {"warnings": warnings, "count": len(warnings)}



@router.get(
    "/contracts/closed-won-deals",
    dependencies=[Depends(require_ai_permission("contracts.view"))],
)
async def get_closed_won_deals(
    unattached_only: bool = True,
    current_user=Depends(get_current_user),
):
    """Get Closed Won deals from Zoho, optionally only those not yet attached to any contract."""
    db = get_db()
    filt = {"stage": "Closed Won"}
    deals = await db.deals.find(filt, {"_id": 0}).sort("created_at", -1).to_list(1000)

    if unattached_only:
        # Find all deal_ids already attached to contracts (scoped by user's contract access)
        contract_filt = await get_contract_filter(current_user)
        contracts = await db.contracts.find(contract_filt, {"_id": 0, "deal_ids": 1}).to_list(5000)
        attached_ids = set()
        for c in contracts:
            for did in (c.get("deal_ids") or []):
                attached_ids.add(did)
        deals = [d for d in deals if d.get("deal_id") not in attached_ids and d.get("zoho_deal_id") not in attached_ids]

    # Enrich with account info
    for d in deals:
        if d.get("account_id"):
            acc = await db.accounts.find_one({"account_id": d["account_id"]}, {"_id": 0, "name": 1})
            d["account_name"] = acc["name"] if acc else d.get("account_name")

    return {"items": deals, "total": len(deals)}


@router.get(
    "/contracts/deal-pipeline",
    dependencies=[Depends(require_ai_permission("contracts.view"))],
)
async def get_deal_pipeline(test_only: bool = None, current_user=Depends(get_current_user)):
    """Get Closed Won deals split into unassigned and active-contract groups."""
    db = get_db()

    # Build deal filter
    deal_filter = {"stage": "Closed Won"}
    if test_only is True:
        deal_filter["is_test_data"] = True
    elif test_only is False:
        deal_filter["is_test_data"] = {"$ne": True}
    # If test_only is None, no filter (all data)

    # Get all Closed Won deals
    closed_won = await db.deals.find(deal_filter, {"_id": 0}).sort("created_at", -1).to_list(5000)

    # Build map of deal_id -> contract(s)
    contract_filter = {"status": "active"}
    if test_only is True:
        contract_filter["is_test_data"] = True
    elif test_only is False:
        contract_filter["is_test_data"] = {"$ne": True}

    contracts = await db.contracts.find(
        contract_filter,
        {"_id": 0, "contract_id": 1, "name": 1, "contract_type": 1, "deal_ids": 1, "account_name": 1, "status": 1,
         "total_hours": 1, "hours_consumed": 1, "hourly_rate": 1, "billing_period": 1, "invoice_terms": 1,
         "total_amount": 1, "monthly_rate": 1}
    ).to_list(5000)

    deal_to_contracts = {}  # deal_id -> list of contracts
    for c in contracts:
        for did in (c.get("deal_ids") or []):
            deal_to_contracts.setdefault(did, []).append(c)

    unassigned = []
    assigned = []

    for d in closed_won:
        did = d.get("deal_id", "")
        zdid = d.get("zoho_deal_id", "")
        matched = deal_to_contracts.get(did, []) or deal_to_contracts.get(zdid, [])
        if matched:
            d["contracts"] = matched
            assigned.append(d)
        else:
            unassigned.append(d)

    return {
        "unassigned": {"items": unassigned, "total": len(unassigned)},
        "assigned": {"items": assigned, "total": len(assigned)},
        "total_closed_won": len(closed_won),
    }




@router.post(
    "/contracts/import-deal",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def import_deal_to_contract(data: dict, current_user=Depends(get_current_user)):
    """Accept a Closed Won deal and attach it to an existing or new contract."""
    if not has_perm(current_user, "contracts.edit") and not has_perm(current_user, "contracts.edit.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()

    deal_id = data.get("deal_id")
    contract_id = data.get("contract_id")  # Existing contract, or None to create new
    if not deal_id:
        raise HTTPException(status_code=400, detail="deal_id is required")

    # Fetch the deal
    deal = await db.deals.find_one(
        {"$or": [{"deal_id": deal_id}, {"zoho_deal_id": deal_id}]},
        {"_id": 0}
    )
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    # If creating a new contract for this deal
    if not contract_id and data.get("create_contract"):
        cc = data["create_contract"]
        contract_type = cc.get("contract_type", "boh")
        new_contract_id = f"contract_{uuid.uuid4().hex[:12]}"

        account_name = deal.get("account_name", "")
        if deal.get("account_id"):
            acc = await db.accounts.find_one({"account_id": deal["account_id"]}, {"_id": 0, "name": 1})
            if acc:
                account_name = acc["name"]

        new_contract = {
            "contract_id": new_contract_id,
            "name": cc.get("name", deal.get("name", "Untitled Contract")),
            "account_id": deal.get("account_id", ""),
            "account_name": account_name,
            "contract_type": contract_type,
            "status": "active",
            "deal_ids": [deal.get("deal_id") or deal.get("zoho_deal_id")],
            "project_ids": [],
            "assigned_sales_rep_id": deal.get("assigned_sales_rep_id") or deal.get("deal_owner_id"),
            "assigned_agent_id": deal.get("assigned_agent_id"),
            "start_date": cc.get("start_date"),
            "end_date": cc.get("end_date"),
            "notes": cc.get("notes", f"Created from Closed Won deal: {deal.get('name', '')}"),
            "created_by": current_user.user_id,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        # Type-specific fields
        # Process resource_types and resources from create_contract data
        resource_types = cc.get("resource_types", [])
        for rt in resource_types:
            if not rt.get("id"):
                rt["id"] = f"rt_{uuid.uuid4().hex[:8]}"
            rt["sell_rate"] = float(rt.get("sell_rate", 0) or 0)
            rt["hours_purchased"] = float(rt.get("hours_purchased", 0) or 0)
            rt["hours_used"] = float(rt.get("hours_used", 0) or 0)
            rt["hours_remaining"] = rt["hours_purchased"] - rt["hours_used"]
            rt.setdefault("name", "Default")
            rt.setdefault("description", "")
        
        resources = cc.get("resources", [])
        for r in resources:
            r["cost_rate"] = float(r.get("cost_rate", 0) or 0)
            r.setdefault("resource_type_id", "")

        if contract_type == "boh":
            total_hours = sum(rt["hours_purchased"] for rt in resource_types) if resource_types else float(cc.get("total_hours", 0))
            use_by_date = (datetime.now(timezone.utc) + timedelta(days=183)).isoformat()
            new_contract.update({
                "resource_types": resource_types,
                "resources": resources,
                "total_hours": total_hours,
                "hours_consumed": 0.0,
                "hourly_rate": float(cc.get("hourly_rate", 0)),
                "last_usage_date": None,
                "use_by_date": use_by_date,
                "usage_window_months": 6,
                "expiration_warning_sent": False,
                "low_hours_warning_sent": False,
                "hour_adjustments": [],
            })
            new_contract["end_date"] = None
        elif contract_type == "boa":
            new_contract.update({
                "resource_types": resource_types,
                "resources": resources,
                "hourly_rate": float(cc.get("hourly_rate", 0)),
                "billing_period": cc.get("billing_period", "monthly"),
                "invoice_terms": cc.get("invoice_terms", "net_30"),
                "total_hours_logged": 0.0,
                "total_billed": 0.0,
            })
        elif contract_type == "fixed_fee":
            milestones = cc.get("milestones", [])
            for i, m in enumerate(milestones):
                if not m.get("id"):
                    m["id"] = f"ms_{uuid.uuid4().hex[:8]}"
                m.setdefault("status", "pending")
                m["percentage"] = float(m.get("percentage", 0) or 0)
            new_contract.update({
                "total_amount": float(cc.get("total_amount", deal.get("amount", 0))),
                "amount_invoiced": 0.0,
                "milestones": milestones,
            })
        elif contract_type == "monthly":
            new_contract.update({
                "resource_types": resource_types,
                "resources": resources,
                "monthly_rate": float(cc.get("monthly_rate", 0)),
                "billing_day": int(cc.get("billing_day", 1)),
                "total_invoiced": 0.0,
            })

        await db.contracts.insert_one(new_contract)
        new_contract.pop("_id", None)
        await log_action("contract.create_from_deal", current_user.user_id, current_user.email, "contract", new_contract_id, {"deal_id": deal_id})
        return {"message": "Contract created and deal attached", "contract": new_contract}

    # Attach to existing contract
    if not contract_id:
        raise HTTPException(status_code=400, detail="Either contract_id or create_contract is required")

    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    effective_deal_id = deal.get("deal_id") or deal.get("zoho_deal_id")
    hour_adjustment = float(data.get("hour_adjustment", 0))
    update_ops = {
        "$addToSet": {"deal_ids": effective_deal_id},
        "$set": {"updated_at": datetime.now(timezone.utc)},
    }

    if contract.get("contract_type") == "boh" and hour_adjustment != 0:
        update_ops["$inc"] = {"total_hours": hour_adjustment}
        update_ops["$push"] = {"hour_adjustments": {
            "deal_id": effective_deal_id,
            "adjustment": hour_adjustment,
            "date": datetime.now(timezone.utc).isoformat(),
            "note": data.get("note", f"Added from deal: {deal.get('name', '')}"),
            "adjusted_by": current_user.user_id,
        }}

    await db.contracts.update_one({"contract_id": contract_id}, update_ops)
    updated = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    await log_action("contract.import_deal", current_user.user_id, current_user.email, "contract", contract_id, {"deal_id": deal_id})
    return {"message": "Deal attached to contract", "contract": updated}


# ============================================================================
# Wave 1 (contracts-time-mgmt) — Approval workflow, project linking,
# Professional Services auto-create.
#
# Approval flow:
#   draft / changes_requested -> submit-for-approval -> pending
#   pending                  -> approve              -> approved (status=active)
#   pending                  -> reject               -> rejected (status=draft)
#   pending                  -> request-changes      -> changes_requested (status=draft)
#
# Every decision writes a contract_approvals row, an audit row, and an
# event. Approving a Professional Services contract with no linked
# project auto-creates one project (deterministic name) so the PMO
# surface has something to work with. Field Services contracts never
# auto-create here.
# ============================================================================


# Mapping of contract.service_category / contract.line_of_business
# values that count as Professional Services for the auto-create branch.
_PS_MARKERS: tuple = ("professional_services", "ps")

# Allowed approval action -> (target approval_status, target contract.status)
_APPROVAL_TRANSITIONS = {
    "submit": ("pending", "draft"),
    "approve": ("approved", "active"),
    "reject": ("rejected", "draft"),
    "request_changes": ("changes_requested", "draft"),
}

# Approval status values from which a given action is legal. The keys
# match _APPROVAL_TRANSITIONS; missing/None approval_status counts as a
# pre-submit state and is treated like "draft" for the submit action.
_LEGAL_PRIOR_STATES = {
    "submit": {None, "", "draft", "rejected", "changes_requested"},
    "approve": {"pending"},
    "reject": {"pending"},
    "request_changes": {"pending"},
}


def _is_professional_services_contract(contract: dict) -> bool:
    """True when the contract's service category or line_of_business
    designates Professional Services. PS is the wave that gets a
    PMO project auto-created on approve; FS dispatch lives elsewhere."""
    sc = (contract.get("service_category") or "").strip().lower()
    lob = (contract.get("line_of_business") or "").strip().lower()
    return sc in _PS_MARKERS or lob in _PS_MARKERS


def _build_ps_project_doc(
    contract: dict,
    *,
    project_defaults: dict,
    created_by: str,
) -> tuple[dict, list[str]]:
    """Compose the project document that the PS auto-create path
    inserts. Returns (doc, missing_required_fields). When the missing
    list is non-empty the route must NOT insert and instead surface a
    409 with the actionable message — see _PROJECT_REQUIRED_FIELDS."""
    defaults = dict(project_defaults or {})
    project_type = defaults.get("project_type") or contract.get("project_type")
    service_line = defaults.get("service_line") or contract.get("service_line")
    name_seed = (
        defaults.get("name")
        or contract.get("name")
        or contract.get("account_name")
        or "Professional Services Project"
    )
    name = f"{name_seed} — Project"

    missing: list[str] = []
    if not project_type:
        missing.append("project_type")
    if not service_line:
        missing.append("service_line")
    if missing:
        return ({}, missing)

    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        "project_id": f"proj_{uuid.uuid4().hex[:12]}",
        "name": name,
        "description": defaults.get("description") or (
            f"Auto-created from contract {contract.get('contract_id')}"
        ),
        "project_type": project_type,
        "service_line": service_line,
        "status": defaults.get("status") or "planning",
        "health": defaults.get("health") or "green",
        "pm_id": defaults.get("pm_id"),
        "assigned_user_ids": list(defaults.get("assigned_user_ids") or []),
        "start_date": defaults.get("start_date") or contract.get("start_date"),
        "target_end_date": defaults.get("target_end_date") or contract.get("end_date"),
        "actual_end_date": None,
        "template_id": defaults.get("template_id"),
        "methodology_config": defaults.get("methodology_config") or {},
        "contract_id": contract.get("contract_id"),
        "account_id": contract.get("account_id"),
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": created_by,
        "auto_created_from_contract": True,
    }
    if contract.get("tenant_id"):
        doc["tenant_id"] = contract["tenant_id"]
    return (doc, [])


async def _write_approval_row(
    db,
    *,
    contract_id: str,
    action: str,
    prior_approval_status: str | None,
    next_approval_status: str,
    user_id: str,
    user_email: str,
    notes: str | None,
) -> None:
    """Insert a contract_approvals row capturing one approval decision.
    The shape matches the Wave 0 migration's contract_approvals index
    plan (contract_id ascending, decided_at descending)."""
    now = datetime.now(timezone.utc)
    await db.contract_approvals.insert_one({
        "approval_id": f"appr_{uuid.uuid4().hex[:12]}",
        "contract_id": contract_id,
        "action": action,
        "prior_approval_status": prior_approval_status,
        "next_approval_status": next_approval_status,
        "decided_by": user_id,
        "decided_by_email": user_email,
        "decided_at": now,
        "notes": notes or "",
    })


def _check_invalid_transition(action: str, current: str | None) -> str | None:
    """Return None when the transition is legal; otherwise a human
    explanation suitable for a 409 detail string."""
    legal = _LEGAL_PRIOR_STATES.get(action, set())
    if current in legal:
        return None
    return (
        f"Cannot {action.replace('_', ' ')} contract from approval_status="
        f"{current!r}; expected one of {sorted(s for s in legal if s)}."
    )


@router.get(
    "/contracts/approval-queue",
    dependencies=[Depends(require_ai_permission("contracts.approve"))],
)
async def list_contract_approval_queue(current_user=Depends(get_current_user)):
    """Finance Command Center approval queue. Returns pending
    contracts scoped to what the caller can actually decide:

    * Global ``contracts.approve``: existing behavior — every
      pending contract the RBAC contract-filter admits.
    * Override-only (no global approve, but at least one active
      row in ``contract_approver_overrides``): only the pending
      contracts whose ``contract_id`` matches one of those rows.
    * Neither: empty list (200, not 403). The frontend page can be
      reached by users with broader contract-view perms; returning
      empty rather than 403 lets the page render its empty state
      without splitting auth on multiple keys.
    """
    db = get_db()
    has_global = has_perm(current_user, "contracts.approve")
    overridden_ids: list[str] = []
    if not has_global:
        from services.contract_per_contract_overrides import (
            list_overridden_contract_ids,
        )
        overridden_ids = await list_overridden_contract_ids(
            db,
            tenant_id=getattr(current_user, "tenant_id", None),
            user_id=getattr(current_user, "user_id", None),
        )
        if not overridden_ids:
            # No global approve AND no active overrides — preserve
            # the original 403 contract that pre-Wave-6 callers
            # depended on. Override-only users with at least one
            # active row drop into the scoped branch below; this
            # branch is the no-authority case.
            raise HTTPException(status_code=403, detail="Insufficient permissions")

    rbac_filter = await get_contract_filter(current_user)
    filt: dict = {"approval_status": "pending"}
    if rbac_filter and rbac_filter != {}:
        filt = {"$and": [filt, rbac_filter]}
    if not has_global and overridden_ids:
        # Restrict to the contracts where the user has an active
        # per-contract override. Composed BEFORE the tenant clause
        # so the $and chain stays well-formed.
        override_clause = {"contract_id": {"$in": overridden_ids}}
        if "$and" in filt:
            filt["$and"].append(override_clause)
        else:
            filt = {"$and": [filt, override_clause]}
    if getattr(current_user, "tenant_id", None):
        tenant_clause = {"tenant_id": current_user.tenant_id}
        if "$and" in filt:
            filt["$and"].append(tenant_clause)
        else:
            filt = {"$and": [filt, tenant_clause]}
    items = await db.contracts.find(filt, {"_id": 0}).sort("updated_at", -1).to_list(500)
    return {"items": items, "total": len(items)}


async def _apply_approval_action(
    contract_id: str,
    action: str,
    data: dict,
    current_user,
    *,
    required_perm: str,
):
    """Shared body for the four approval action routes. Validates the
    transition, persists the new approval_status / status, writes the
    approval row, audits, and (for the approve action) handles PS
    project auto-create. Returns the updated contract document.

    Per-contract approver overrides (Wave 6) are honored for the
    three DECISION actions only — approve / reject / request_changes.
    The "submit" action's required_perm is contracts.edit (the
    submitter is the contract author, not an approver), so override
    authority does not unlock submission. For decision actions, a
    user with an active row in contract_approver_overrides for
    THIS contract_id is treated as having contracts.approve for
    this one contract — they cannot approve a different contract
    without a separate override row.
    """
    # The submit action does not consult overrides — its gate is
    # the contract editor permission, not approver authority.
    db = get_db()
    if action == "submit":
        if not has_perm(current_user, required_perm):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    else:
        # Decision action — global perm OR active per-contract override.
        if not has_perm(current_user, required_perm):
            from services.contract_per_contract_overrides import (
                has_active_override,
            )
            tenant_id = getattr(current_user, "tenant_id", None)
            user_id = getattr(current_user, "user_id", None)
            permitted = False
            if user_id:
                permitted = await has_active_override(
                    db,
                    tenant_id=tenant_id,
                    contract_id=contract_id,
                    user_id=user_id,
                )
            if not permitted:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if getattr(current_user, "tenant_id", None) and contract.get("tenant_id"):
        if current_user.tenant_id != contract["tenant_id"]:
            raise HTTPException(status_code=404, detail="Contract not found")

    current_status = contract.get("approval_status")
    err = _check_invalid_transition(action, current_status)
    if err:
        raise HTTPException(status_code=409, detail=err)

    next_approval, next_contract_status = _APPROVAL_TRANSITIONS[action]
    notes = (data or {}).get("approval_notes") or (data or {}).get("notes") or None

    now = datetime.now(timezone.utc)
    set_fields: dict = {
        "approval_status": next_approval,
        "approval_notes": notes,
        "status": next_contract_status,
        "updated_at": now,
    }
    if action == "approve":
        set_fields["approved_by"] = current_user.user_id
        set_fields["approved_at"] = now
    elif action in ("reject", "request_changes", "submit"):
        # approve is the only action that stamps approved_by/at. The
        # other actions leave the prior approval metadata in place so a
        # rejection -> resubmit -> approval cycle records each decision
        # via contract_approvals rows rather than overwriting fields.
        pass

    project_auto_created_id: str | None = None
    if action == "approve" and _is_professional_services_contract(contract):
        existing_project_ids = list(contract.get("project_ids") or [])
        if not existing_project_ids:
            project_defaults = (data or {}).get("project_defaults") or {}
            doc, missing = _build_ps_project_doc(
                contract,
                project_defaults=project_defaults,
                created_by=current_user.user_id,
            )
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot approve Professional Services contract because "
                        f"project creation requires: {', '.join(missing)}"
                    ),
                )
            await db.projects.insert_one(doc)
            project_auto_created_id = doc["project_id"]
            set_fields["project_ids"] = existing_project_ids + [project_auto_created_id]

    # Race-safe state transition: atomic find_one_and_update gated on
    # the prior approval_status still being one of the legal pending
    # states the validator just admitted. Two concurrent decision
    # requests will both pass the validator above (they each read a
    # state of "pending"), but only the FIRST conditional update
    # matches — the second one's filter fails because the row's
    # approval_status has already advanced. The loser sees the same
    # 409 invalid-transition response that a stale stand-alone
    # request would have produced.
    legal_priors = list(_LEGAL_PRIOR_STATES.get(action, set()))
    # Normalize the prior-state filter so missing/None matches the
    # validator's tolerance (the "submit" action treats absent
    # approval_status as a pre-submit state).
    has_null = None in legal_priors or "" in legal_priors
    non_null = [s for s in legal_priors if s not in (None, "")]
    prior_clauses: list[dict] = []
    if non_null:
        prior_clauses.append({"approval_status": {"$in": non_null}})
    if has_null:
        prior_clauses.append(
            {"$or": [
                {"approval_status": {"$exists": False}},
                {"approval_status": None},
                {"approval_status": ""},
            ]}
        )
    if len(prior_clauses) == 1:
        prior_filter = prior_clauses[0]
    else:
        prior_filter = {"$or": prior_clauses}

    updated_row = await db.contracts.find_one_and_update(
        {"contract_id": contract_id, **prior_filter},
        {"$set": set_fields},
        return_document=True,
    )
    if updated_row is None:
        # Either the row vanished or another caller raced us to a
        # post-pending state. Re-read so we can surface the precise
        # 409 detail string the validator builds, matching the stale
        # stand-alone request flow.
        latest = await db.contracts.find_one(
            {"contract_id": contract_id}, {"_id": 0, "approval_status": 1}
        )
        if latest is None:
            raise HTTPException(status_code=404, detail="Contract not found")
        # Roll back the auto-created project if we inserted one — the
        # decision did not commit so the PS project would be orphaned.
        if project_auto_created_id:
            try:
                await db.projects.delete_one(
                    {"project_id": project_auto_created_id}
                )
            except Exception as cleanup_exc:  # noqa: BLE001
                logger.warning(
                    "Race rollback: failed to delete orphan project %s: %s",
                    project_auto_created_id,
                    cleanup_exc,
                )
        raise HTTPException(
            status_code=409,
            detail=(
                _check_invalid_transition(action, latest.get("approval_status"))
                or "Approval state changed under us; refresh and retry."
            ),
        )
    await _write_approval_row(
        db,
        contract_id=contract_id,
        action=action,
        prior_approval_status=current_status,
        next_approval_status=next_approval,
        user_id=current_user.user_id,
        user_email=current_user.email,
        notes=notes,
    )
    await log_action(
        f"contract.{action}",
        current_user.user_id,
        current_user.email,
        "contract",
        contract_id,
        {
            "prior_approval_status": current_status,
            "next_approval_status": next_approval,
            "project_auto_created_id": project_auto_created_id,
        },
    )
    try:
        await emit_event(
            "contracts",
            f"approval_{action}",
            record={**contract, **set_fields, "contract_id": contract_id},
            user_id=current_user.user_id,
            user_email=current_user.email,
        )
    except Exception as exc:  # noqa: BLE001 — event bus must not break the decision
        logger.warning("emit_event contracts.approval_%s failed: %s", action, exc)

    updated = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if project_auto_created_id:
        updated = updated or {}
        updated["_auto_created_project_id"] = project_auto_created_id
    return updated


@router.post(
    "/contracts/{contract_id}/submit-for-approval",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def submit_contract_for_approval(
    contract_id: str,
    data: dict | None = None,
    current_user=Depends(get_current_user),
):
    return await _apply_approval_action(
        contract_id,
        "submit",
        data or {},
        current_user,
        required_perm="contracts.edit",
    )


@router.post(
    "/contracts/{contract_id}/approve",
    dependencies=[Depends(require_ai_permission("contracts.approve"))],
)
async def approve_contract(
    contract_id: str,
    data: dict | None = None,
    current_user=Depends(get_current_user),
):
    return await _apply_approval_action(
        contract_id,
        "approve",
        data or {},
        current_user,
        required_perm="contracts.approve",
    )


@router.post(
    "/contracts/{contract_id}/reject",
    dependencies=[Depends(require_ai_permission("contracts.approve"))],
)
async def reject_contract(
    contract_id: str,
    data: dict | None = None,
    current_user=Depends(get_current_user),
):
    return await _apply_approval_action(
        contract_id,
        "reject",
        data or {},
        current_user,
        required_perm="contracts.approve",
    )


@router.post(
    "/contracts/{contract_id}/request-changes",
    dependencies=[Depends(require_ai_permission("contracts.approve"))],
)
async def request_changes_on_contract(
    contract_id: str,
    data: dict | None = None,
    current_user=Depends(get_current_user),
):
    return await _apply_approval_action(
        contract_id,
        "request_changes",
        data or {},
        current_user,
        required_perm="contracts.approve",
    )


# ---- Project linking (Wave 1) -----------------------------------------------


@router.post(
    "/contracts/{contract_id}/projects",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def attach_projects_to_contract(
    contract_id: str,
    data: dict,
    current_user=Depends(get_current_user),
):
    """Attach one or more existing projects to a contract. Body shape:
    ``{"project_ids": ["proj_a", "proj_b"]}``. Idempotent — duplicate
    ids in the body and ids already linked are deduped. Returns the
    updated contract."""
    has_edit_all = has_perm(current_user, "contracts.edit.all") or has_perm(current_user, "contracts.edit")
    has_edit_own = has_perm(current_user, "contracts.edit.own")
    if not (has_edit_all or has_edit_own):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()

    project_ids = list((data or {}).get("project_ids") or [])
    if not project_ids:
        raise HTTPException(status_code=400, detail="project_ids is required and must be non-empty")
    # Dedupe while preserving order.
    seen: set = set()
    deduped: list[str] = []
    for pid in project_ids:
        if isinstance(pid, str) and pid and pid not in seen:
            seen.add(pid)
            deduped.append(pid)
    if not deduped:
        raise HTTPException(status_code=400, detail="project_ids must contain at least one non-empty string")

    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if getattr(current_user, "tenant_id", None) and contract.get("tenant_id"):
        if current_user.tenant_id != contract["tenant_id"]:
            raise HTTPException(status_code=404, detail="Contract not found")

    # Resolve each project. A missing project surfaces as 404 because
    # the link target does not exist; a cross-tenant project surfaces
    # as 404 to avoid leaking that the row exists in another tenant.
    projects = await db.projects.find({"project_id": {"$in": deduped}}, {"_id": 0}).to_list(len(deduped))
    found = {p["project_id"]: p for p in projects if p.get("project_id")}
    missing = [pid for pid in deduped if pid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Projects not found: {', '.join(missing)}",
        )
    if getattr(current_user, "tenant_id", None):
        cross_tenant = [
            pid for pid, p in found.items()
            if p.get("tenant_id") and p["tenant_id"] != current_user.tenant_id
        ]
        if cross_tenant:
            raise HTTPException(
                status_code=404,
                detail=f"Projects not found: {', '.join(cross_tenant)}",
            )
    if contract.get("tenant_id"):
        cross_tenant_contract = [
            pid for pid, p in found.items()
            if p.get("tenant_id") and p["tenant_id"] != contract["tenant_id"]
        ]
        if cross_tenant_contract:
            raise HTTPException(
                status_code=404,
                detail=f"Projects not found: {', '.join(cross_tenant_contract)}",
            )

    existing_ids = list(contract.get("project_ids") or [])
    merged: list[str] = list(existing_ids)
    newly_linked: list[str] = []
    for pid in deduped:
        if pid not in merged:
            merged.append(pid)
            newly_linked.append(pid)

    now = datetime.now(timezone.utc)
    await db.contracts.update_one(
        {"contract_id": contract_id},
        {"$set": {"project_ids": merged, "updated_at": now}},
    )
    # Each newly-linked project gets contract_id set so the back-edge
    # is consistent. Already-linked projects with the same contract_id
    # are left alone.
    if newly_linked:
        await db.projects.update_many(
            {"project_id": {"$in": newly_linked}},
            {"$set": {"contract_id": contract_id, "updated_at": now}},
        )
    for pid in newly_linked:
        await log_action(
            "contract.project_attached",
            current_user.user_id,
            current_user.email,
            "contract",
            contract_id,
            {"project_id": pid},
        )

    updated = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    return updated


@router.delete(
    "/contracts/{contract_id}/projects/{project_id}",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def detach_project_from_contract(
    contract_id: str,
    project_id: str,
    current_user=Depends(get_current_user),
):
    """Detach one project from a contract. Idempotent — detaching a
    project that was never linked returns 200 with the unchanged
    contract document."""
    has_edit_all = has_perm(current_user, "contracts.edit.all") or has_perm(current_user, "contracts.edit")
    has_edit_own = has_perm(current_user, "contracts.edit.own")
    if not (has_edit_all or has_edit_own):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if getattr(current_user, "tenant_id", None) and contract.get("tenant_id"):
        if current_user.tenant_id != contract["tenant_id"]:
            raise HTTPException(status_code=404, detail="Contract not found")

    now = datetime.now(timezone.utc)
    was_linked = project_id in (contract.get("project_ids") or [])
    await db.contracts.update_one(
        {"contract_id": contract_id},
        {"$pull": {"project_ids": project_id}, "$set": {"updated_at": now}},
    )
    if was_linked:
        # Clear the back-edge only when this project was actually
        # pointing at this contract. A project may have been moved to
        # a different contract; do not clobber that linkage.
        await db.projects.update_one(
            {"project_id": project_id, "contract_id": contract_id},
            {"$set": {"contract_id": None, "updated_at": now}},
        )
        await log_action(
            "contract.project_detached",
            current_user.user_id,
            current_user.email,
            "contract",
            contract_id,
            {"project_id": project_id},
        )

    return await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})


@router.get(
    "/contracts/{contract_id}",
    dependencies=[Depends(require_ai_permission("contracts.view"))],
)
async def get_contract(contract_id: str, current_user=Depends(get_current_user)):
    db = get_db()
    rbac_filter = await get_contract_filter(current_user)
    query = {"contract_id": contract_id}
    if rbac_filter and rbac_filter != {}:
        query = {"$and": [{"contract_id": contract_id}, rbac_filter]}
    doc = await db.contracts.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Contract not found")

    # Enrich with project and deal info.
    #
    # Linked projects come from two sources:
    #   (1) doc["project_ids"]  — the forward list the native Wave-1
    #       contract→projects linker maintains (POST/DELETE
    #       /api/contracts/{id}/projects).
    #   (2) projects whose project.contract_id == this contract_id —
    #       the back-edge. The Zoho importer set this on all 101
    #       imported projects but never populated the forward
    #       project_ids[] on the parent contracts, so without this
    #       merge the detail view shows "no projects linked" for
    #       every imported contract.
    #
    # Merge dedupes by project_id so a project on both sides shows
    # up once. We do NOT mutate the contract's project_ids[] here —
    # this is purely a read enrichment; promotion to the canonical
    # list is a separate operator decision.
    forward_ids = list(doc.get("project_ids") or [])
    projects_by_id: dict[str, dict] = {}
    if forward_ids:
        for p in await db.projects.find(
            {"project_id": {"$in": forward_ids}}, {"_id": 0},
        ).to_list(500):
            pid = p.get("project_id")
            if pid:
                projects_by_id[pid] = p
    # Back-edge fan-in.
    async for p in db.projects.find(
        {"contract_id": contract_id}, {"_id": 0},
    ):
        pid = p.get("project_id")
        if pid and pid not in projects_by_id:
            projects_by_id[pid] = p
    if projects_by_id:
        doc["projects"] = list(projects_by_id.values())

    if doc.get("deal_ids"):
        deals = await db.deals.find(
            {"deal_id": {"$in": doc["deal_ids"]}}, {"_id": 0}
        ).to_list(100)
        # Also check zoho_deal_id matches
        zoho_deals = await db.deals.find(
            {"zoho_deal_id": {"$in": doc["deal_ids"]}}, {"_id": 0}
        ).to_list(100)
        all_deals = {d.get("deal_id") or d.get("zoho_deal_id"): d for d in deals + zoho_deals}
        doc["deals"] = list(all_deals.values())

    return doc


@router.put(
    "/contracts/{contract_id}",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def update_contract(contract_id: str, data: dict, current_user=Depends(get_current_user)):
    has_edit_all = has_perm(current_user, "contracts.edit.all") or has_perm(current_user, "contracts.edit")
    has_edit_own = has_perm(current_user, "contracts.edit.own")
    if not has_edit_all and not has_edit_own:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()

    existing = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Contract not found")

    # Ownership verification for .edit.own users
    if has_edit_own and not has_edit_all:
        if not verify_ownership(existing, current_user, "contract"):
            raise HTTPException(status_code=403, detail="You can only edit contracts assigned to you")

    # Remove fields that shouldn't be updated directly
    safe_fields = [
        "name", "status", "deal_ids", "start_date", "end_date", "notes",
        "assigned_sales_rep_id", "assigned_agent_id",
        "total_hours", "hourly_rate", "billing_period", "invoice_terms",
        "total_amount", "milestones", "monthly_rate", "billing_day",
        "account_id", "account_name", "resources", "resource_types",
        "products", "service_category",  # Wholesale MRR products
    ]
    # Fields that can be explicitly set to None/null to clear them
    clearable_fields = ["end_date", "assigned_sales_rep_id", "assigned_agent_id", "notes"]
    update_data = {}
    for k, v in data.items():
        if k not in safe_fields:
            continue
        if v is not None:
            update_data[k] = v
        elif k in clearable_fields:
            update_data[k] = None
    update_data["updated_at"] = datetime.now(timezone.utc)

    # BUSINESS RULE: Prevent sell rate changes on active contracts
    if "resource_types" in update_data and existing.get("status") == "active":
        existing_types = {rt["id"]: rt for rt in existing.get("resource_types", [])}
        for rt in update_data["resource_types"]:
            rt_id = rt.get("id")
            if rt_id and rt_id in existing_types:
                # Check if sell_rate changed
                old_sell_rate = float(existing_types[rt_id].get("sell_rate", 0))
                new_sell_rate = float(rt.get("sell_rate", 0))
                if abs(old_sell_rate - new_sell_rate) > 0.01:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot update sell rate on active contract. Sell rate changes require creating a new contract. Resource type: {rt.get('name')}"
                    )

    # BUSINESS RULE: Cost rate updates require special permission on active contracts
    if "resources" in update_data and existing.get("status") == "active":
        # Check if cost rates changed
        existing_resources = existing.get("resources", [])
        has_cost_rate_changes = False
        
        for new_res in update_data["resources"]:
            # Find matching existing resource
            matching = [
                r for r in existing_resources 
                if (r.get("user_id") == new_res.get("user_id") and 
                    r.get("resource_type_id") == new_res.get("resource_type_id"))
            ]
            if matching:
                old_cost = float(matching[0].get("cost_rate", 0))
                new_cost = float(new_res.get("cost_rate", 0))
                if abs(old_cost - new_cost) > 0.01:
                    has_cost_rate_changes = True
                    break
        
        # Policy engine: check cost rate update restriction
        if has_cost_rate_changes:
            policy_payload = {
                "cost_rate_changed": True,
                "user_has_cost_rate_perm": has_perm(current_user, "contracts.update_cost_rates"),
                "status": existing.get("status"),
            }
            cost_result = await evaluate_policies(db, "contracts", "contract", "on_update", policy_payload, user_role=current_user.role)
            enforce_policy_result(cost_result)

    # Ensure resource_types have proper structure
    if "resource_types" in update_data:
        for rt in update_data["resource_types"]:
            if not rt.get("id"):
                rt["id"] = f"rt_{uuid.uuid4().hex[:8]}"
            rt["sell_rate"] = float(rt.get("sell_rate", 0) or 0)
            rt["hours_purchased"] = float(rt.get("hours_purchased", 0) or 0)
            rt["hours_used"] = float(rt.get("hours_used", 0) or 0)
            rt["hours_remaining"] = rt["hours_purchased"] - rt["hours_used"]
            rt.setdefault("name", "Default")
            rt.setdefault("description", "")
        
        # Update total_hours for BOH contracts
        if existing.get("contract_type") == "boh":
            update_data["total_hours"] = sum(rt["hours_purchased"] for rt in update_data["resource_types"])

    # Ensure resource rates are floats
    if "resources" in update_data:
        for r in update_data["resources"]:
            r["cost_rate"] = float(r.get("cost_rate", 0) or 0)
            r.setdefault("resource_type_id", "")
            # Backward compatibility
            if "sell_rate" in r:
                r["sell_rate"] = float(r.get("sell_rate", 0) or 0)

    # Ensure product rates are floats and calculate totals
    if "products" in update_data:
        for p in update_data["products"]:
            p["cost_price"] = float(p.get("cost_price", 0) or 0)
            p["sell_price"] = float(p.get("sell_price", 0) or 0)
            p["quantity"] = int(p.get("quantity", 1) or 1)
            p.setdefault("pricing_tier", "monthly")
            p.setdefault("product_id", "")
            p.setdefault("product_name", "")
            p.setdefault("sku", "")
            # Calculate line totals
            p["line_total"] = p["sell_price"] * p["quantity"]
            p["line_margin"] = (p["sell_price"] - p["cost_price"]) * p["quantity"]
        
        # Recalculate contract totals for wholesale MRR
        if existing.get("service_category") == "wholesale_mrr" or existing.get("contract_type") == "wholesale_mrr":
            total_cost = sum(p["cost_price"] * p["quantity"] for p in update_data["products"])
            total_sell = sum(p["sell_price"] * p["quantity"] for p in update_data["products"])
            total_margin = total_sell - total_cost
            
            update_data.update({
                "total_products": len(update_data["products"]),
                "total_cost_monthly": total_cost,
                "total_sell_monthly": total_sell,
                "total_margin_monthly": total_margin,
                "margin_percent": (total_margin / total_sell * 100) if total_sell > 0 else 0,
            })

    # Ensure milestone IDs and float conversion
    if "milestones" in update_data:
        for m in update_data["milestones"]:
            if not m.get("id"):
                m["id"] = f"ms_{uuid.uuid4().hex[:8]}"
            m.setdefault("status", "pending")
            m["percentage"] = float(m.get("percentage", 0) or 0)
            # Recalculate amount if total_amount is present
            if "total_amount" in update_data:
                m.setdefault("amount", update_data["total_amount"] * (m["percentage"] / 100))

    await db.contracts.update_one({"contract_id": contract_id}, {"$set": update_data})
    updated = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    
    # Recalculate financials after update (Phase 1)
    try:
        financials = calculate_contract_financials(updated)
        await db.contracts.update_one(
            {"contract_id": contract_id},
            {"$set": financials}
        )
        updated.update(financials)
    except Exception as e:
        logger.error(f"Failed to recalculate financials for contract {contract_id}: {e}")
        # Continue without financials - not a critical failure
    
    await log_action("contract.update", current_user.user_id, current_user.email, "contract", contract_id)
    await emit_event("contracts", "updated", record=updated, old_record=existing, user_id=current_user.user_id, user_email=current_user.email)
    return updated


@router.delete(
    "/contracts/{contract_id}",
    dependencies=[Depends(require_ai_permission("contracts.delete"))],
)
async def delete_contract(contract_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "contracts.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()

    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    if not has_perm(current_user, "contracts.edit.all") and not has_perm(current_user, "*"):
        if not verify_ownership(contract, current_user, "contract"):
            raise HTTPException(status_code=403, detail="You can only delete contracts assigned to you")

    # Cascade: clean up related projects, time entries, field jobs, invoices, FCC drafts
    # Note: legacy ap_bills / ar_invoices cascades replaced with finance_drafts (Wave 3A).
    # The legacy collections may still hold historical data migrated by the one-shot
    # migration script; those rows are intentionally left in place (migration is non-destructive).
    await db.projects.delete_many({"contract_id": contract_id})
    await db.time_entries.delete_many({"contract_id": contract_id})
    await db.field_jobs.delete_many({"contract_id": contract_id})
    await db.invoices.delete_many({"contract_id": contract_id})
    await db["finance_drafts"].delete_many({
        "source_contract_id": contract_id,
        "kind": {"$in": ["ar_invoice", "ap_bill"]},
    })
    await db.contracts.delete_one({"contract_id": contract_id})
    await log_action("contract.delete", current_user.user_id, current_user.email, "contract", contract_id)
    await emit_event("contracts", "deleted", record={"contract_id": contract_id}, user_id=current_user.user_id, user_email=current_user.email)
    return {"message": "Contract and related data deleted"}


# ---- Deal linking ----

@router.post(
    "/contracts/{contract_id}/deals",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def add_deal_to_contract(contract_id: str, data: dict, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "contracts.assign_to_deal"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    deal_id = data.get("deal_id")
    if not deal_id:
        raise HTTPException(status_code=400, detail="deal_id required")

    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    # For BOH: optionally adjust hours
    hour_adjustment = float(data.get("hour_adjustment", 0))
    update_ops = {"$addToSet": {"deal_ids": deal_id}, "$set": {"updated_at": datetime.now(timezone.utc)}}

    if contract.get("contract_type") == "boh" and hour_adjustment != 0:
        update_ops["$inc"] = {"total_hours": hour_adjustment}
        # Track adjustment
        adjustment_record = {
            "deal_id": deal_id,
            "adjustment": hour_adjustment,
            "date": datetime.now(timezone.utc).isoformat(),
            "note": data.get("note", ""),
        }
        update_ops["$push"] = {"hour_adjustments": adjustment_record}

    await db.contracts.update_one({"contract_id": contract_id}, update_ops)
    return await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})


@router.delete(
    "/contracts/{contract_id}/deals/{deal_id}",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def remove_deal_from_contract(contract_id: str, deal_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "contracts.assign_to_deal"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    await db.contracts.update_one(
        {"contract_id": contract_id},
        {"$pull": {"deal_ids": deal_id}, "$set": {"updated_at": datetime.now(timezone.utc)}}
    )
    return await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})


# ---- Milestone management (Fixed Fee) ----

@router.post(
    "/contracts/{contract_id}/milestones/{milestone_id}/complete",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def complete_milestone(contract_id: str, milestone_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "contracts.milestones.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract or contract.get("contract_type") != "fixed_fee":
        raise HTTPException(status_code=404, detail="Fixed fee contract not found")

    milestones = contract.get("milestones", [])
    found = False
    for m in milestones:
        if m.get("id") == milestone_id:
            m["status"] = "completed"
            m["completed_date"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Milestone not found")

    amount_invoiced = sum(m.get("amount", 0) for m in milestones if m.get("status") == "completed")
    await db.contracts.update_one(
        {"contract_id": contract_id},
        {"$set": {"milestones": milestones, "amount_invoiced": amount_invoiced, "updated_at": datetime.now(timezone.utc)}}
    )
    return await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})


# ---- BOH Hour Adjustment ----

@router.post(
    "/contracts/{contract_id}/adjust-hours",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def adjust_boh_hours(contract_id: str, data: dict, current_user=Depends(get_current_user)):
    """Adjust hours on a BOH contract (e.g., when a new deal adds/removes hours)."""
    if not has_perm(current_user, "contracts.edit") and not has_perm(current_user, "contracts.edit.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract or contract.get("contract_type") != "boh":
        raise HTTPException(status_code=404, detail="BOH contract not found")

    adjustment = float(data.get("adjustment", 0))
    if adjustment == 0:
        raise HTTPException(status_code=400, detail="adjustment must be non-zero")

    record = {
        "deal_id": data.get("deal_id"),
        "adjustment": adjustment,
        "date": datetime.now(timezone.utc).isoformat(),
        "note": data.get("note", ""),
        "adjusted_by": current_user.user_id,
    }
    await db.contracts.update_one(
        {"contract_id": contract_id},
        {
            "$inc": {"total_hours": adjustment},
            "$push": {"hour_adjustments": record},
            "$set": {"updated_at": datetime.now(timezone.utc), "low_hours_warning_sent": False},
        }
    )
    await log_action("contract.adjust_hours", current_user.user_id, current_user.email, "contract", contract_id, {"adjustment": adjustment})
    return {"success": True, "new_total_hours": contract["total_hours"] + adjustment}


# ---- FINANCIAL CALCULATIONS (Phase 1) ----

@router.post(
    "/contracts/{contract_id}/recalculate-financials",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def recalculate_contract_financials(contract_id: str, current_user=Depends(get_current_user)):
    """Recalculate AR, AP, and Margin summaries for a contract."""
    if not has_perm(current_user, "contracts.edit") and not has_perm(current_user, "contracts.edit.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    try:
        financials = calculate_contract_financials(contract)
        await db.contracts.update_one(
            {"contract_id": contract_id},
            {
                "$set": {
                    **financials,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        await log_action("contract.recalculate_financials", current_user.user_id, current_user.email, "contract", contract_id, {})
        
        return {
            "success": True,
            "contract_id": contract_id,
            "financials": financials
        }
    except Exception as e:
        logger.error(f"Failed to recalculate financials for contract {contract_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to recalculate financials: {str(e)}")


# Bulk-write batch size for recalculate-all. 200 keeps each batch's
# wire payload well under MongoDB's 16MB BSON limit even when each
# contract carries a populated margin_summary, and amortises the
# round-trip cost without retaining the full contract set in memory.
_RECALC_ALL_BATCH_SIZE = 200


@router.post(
    "/contracts/recalculate-all-financials",
    dependencies=[Depends(require_ai_permission("contracts.recalculate_all"))],
)
async def recalculate_all_contract_financials(current_user=Depends(get_current_user)):
    """Recalculate financial summaries for every contract VISIBLE TO
    THE CALLER.

    Admin-only. The previous gate accepted plain ``contracts.edit``,
    which let any contract editor (including a sales rep with edit-
    own) trigger a fan-out rewrite. The new gate is:

      * wildcard ``*`` (root admin), OR
      * the dedicated ``contracts.recalculate_all`` permission
        (granted to admin role only).

    Visibility-scoped. The previous query was ``find({})`` and
    rewrote every contract in the database; that both leaks knowledge
    of cross-tenant data and lets a single admin in tenant A
    overwrite contracts in tenant B. Now we compose the same
    visibility helper /api/contracts uses (``get_contract_filter``)
    so the recalc set is exactly the set the caller could see via
    GET /api/contracts.

    Execution model: server-streamed cursor + ``bulk_write`` batches
    of ``_RECALC_ALL_BATCH_SIZE``. Replaces the prior
    ``to_list(10000) + per-row update_one`` loop, which (a) capped
    silently at 10k contracts, (b) issued one network round-trip per
    row, and (c) kept the full contract set in Python memory before
    doing any work.
    """
    if not (
        has_perm(current_user, "*")
        or has_perm(current_user, "contracts.recalculate_all")
    ):
        raise HTTPException(
            status_code=403,
            detail="Admin permission required: contracts.recalculate_all",
        )

    db = get_db()

    # Same visibility helper /api/contracts uses. For admins this
    # returns {} (no constraint); for scoped roles it limits the
    # recalc set to what the caller would see in the list view.
    filt = await get_contract_filter(current_user)
    query = filt if filt and filt != {} else {}

    from pymongo import UpdateOne

    now = datetime.now(timezone.utc)
    total = 0
    updated_count = 0
    failed_count = 0
    errors: list[dict] = []
    pending_ops: list = []

    async def _flush(ops: list) -> int:
        if not ops:
            return 0
        try:
            result = await db.contracts.bulk_write(ops, ordered=False)
            return result.modified_count or 0
        except Exception as exc:  # noqa: BLE001
            logger.error("bulk_write batch failed in recalculate-all: %s", exc)
            return 0

    async for contract in db.contracts.find(query, {"_id": 0}):
        total += 1
        try:
            financials = calculate_contract_financials(contract)
        except Exception as exc:  # noqa: BLE001
            failed_count += 1
            errors.append({
                "contract_id": contract.get("contract_id"),
                "name": contract.get("name"),
                "error": str(exc),
            })
            logger.error(
                "Failed to recalculate financials for contract %s: %s",
                contract.get("contract_id"),
                exc,
            )
            continue

        pending_ops.append(UpdateOne(
            {"contract_id": contract["contract_id"]},
            {"$set": {**financials, "updated_at": now}},
        ))
        if len(pending_ops) >= _RECALC_ALL_BATCH_SIZE:
            updated_count += await _flush(pending_ops)
            pending_ops = []

    updated_count += await _flush(pending_ops)

    await log_action(
        "contract.recalculate_all_financials",
        current_user.user_id,
        current_user.email,
        "system",
        "bulk",
        {
            "total_contracts": total,
            "updated": updated_count,
            "failed": failed_count,
        },
    )

    return {
        "success": True,
        "total_contracts": total,
        "updated": updated_count,
        "failed": failed_count,
        "errors": errors[:10],  # Return first 10 errors only
    }


# ---- Per-contract approver overrides (Wave 6) ----------------------------
#
# Distinct from the GLOBAL contract-approver override panel at
# /admin/contract-approvers, which mutates user.extra_permissions.
# These per-contract overrides live on the contract_approver_overrides
# collection (see services/contract_per_contract_overrides.py +
# migrations/2026_contracts_per_contract_overrides_indexes.py) and
# grant approval authority for one contract only.
#
# Management gate mirrors the global page: users.edit + admin role.
# Normal contract editors cannot grant approval authority casually.


def _can_manage_per_contract_overrides(current_user) -> bool:
    """Mirror routes/admin.py:288 — extra_permissions-style mutations
    are admin-only. The same trust boundary applies to granting
    approval authority for a single contract."""
    if has_perm(current_user, "*"):
        return True
    if not has_perm(current_user, "users.edit"):
        return False
    return getattr(current_user, "role", None) == "admin"


@router.get(
    "/contracts/{contract_id}/approver-overrides",
    dependencies=[Depends(require_ai_permission("contracts.approve"))],
)
async def list_contract_approver_overrides(
    contract_id: str, current_user=Depends(get_current_user)
):
    """List override rows attached to a contract, newest first.
    Active + revoked rows are both returned so the management UI
    can render the full grant/revoke history.

    Visible to anyone who can either decide on the contract
    (global contracts.approve) or manage the overrides themselves
    (admin). Other callers receive 403 — the override set leaks
    who is currently empowered to approve.
    """
    if not (
        has_perm(current_user, "contracts.approve")
        or _can_manage_per_contract_overrides(current_user)
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if getattr(current_user, "tenant_id", None) and contract.get("tenant_id"):
        if current_user.tenant_id != contract["tenant_id"]:
            raise HTTPException(status_code=404, detail="Contract not found")

    from services.contract_per_contract_overrides import (
        list_overrides_for_contract,
    )
    items = await list_overrides_for_contract(
        db,
        tenant_id=getattr(current_user, "tenant_id", None),
        contract_id=contract_id,
        include_revoked=True,
    )
    return {"items": items, "total": len(items)}


@router.post(
    "/contracts/{contract_id}/approver-overrides",
    dependencies=[Depends(require_ai_permission("users.edit"))],
)
async def grant_contract_approver_override(
    contract_id: str,
    data: dict,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Grant or reactivate a per-contract approver override.
    Body: { "user_id": str, "note"?: str }.

    Idempotent: a repeat grant for the same (tenant, contract,
    user) tuple updates note + granted_by + granted_at on the
    existing row rather than inserting a duplicate. A previously
    revoked row is reactivated in place to preserve the audit
    chain.
    """
    if not _can_manage_per_contract_overrides(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if getattr(current_user, "tenant_id", None) and contract.get("tenant_id"):
        if current_user.tenant_id != contract["tenant_id"]:
            raise HTTPException(status_code=404, detail="Contract not found")

    user_id = (data or {}).get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")
    user_id = user_id.strip()

    # Cross-tenant override prevention: target user must belong to
    # the same tenant as the contract.
    target_user = await UserRepository(pg).get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    if getattr(current_user, "tenant_id", None):
        target_tenant = target_user.tenant_id
        if target_tenant and target_tenant != current_user.tenant_id:
            # 404 not 403 — tenant existence is not leaked.
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    note = (data or {}).get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=400, detail="note must be a string")

    from services.contract_per_contract_overrides import grant_override

    row = await grant_override(
        db,
        tenant_id=getattr(current_user, "tenant_id", None),
        contract_id=contract_id,
        user_id=user_id,
        granted_by=getattr(current_user, "user_id", None) or "system",
        note=note,
    )
    try:
        await log_action(
            "contract.approver_override.granted",
            getattr(current_user, "user_id", None),
            getattr(current_user, "email", "unknown"),
            "contract",
            contract_id,
            {
                "override_id": row.get("override_id"),
                "user_id": user_id,
                "has_note": bool((note or "").strip()),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "audit log_action failed for approver_override.granted: %s", exc
        )

    return row


@router.delete(
    "/contracts/{contract_id}/approver-overrides/{user_id}",
    dependencies=[Depends(require_ai_permission("users.edit"))],
)
async def revoke_contract_approver_override(
    contract_id: str,
    user_id: str,
    data: Optional[dict] = None,
    current_user=Depends(get_current_user),
):
    """Soft-revoke the active override for (contract, user).
    Idempotent: revoking when no active row exists returns the
    most recent row in its current state without raising 404 if
    a prior (revoked) row exists.

    Body (optional): { "revoke_note"?: str } or { "note"?: str }.
    """
    if not _can_manage_per_contract_overrides(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    contract = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if getattr(current_user, "tenant_id", None) and contract.get("tenant_id"):
        if current_user.tenant_id != contract["tenant_id"]:
            raise HTTPException(status_code=404, detail="Contract not found")

    note = (data or {}).get("revoke_note") or (data or {}).get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=400, detail="revoke_note must be a string")

    from services.contract_per_contract_overrides import (
        find_any_override_row,
        revoke_override,
    )

    row = await revoke_override(
        db,
        tenant_id=getattr(current_user, "tenant_id", None),
        contract_id=contract_id,
        user_id=user_id,
        revoked_by=getattr(current_user, "user_id", None) or "system",
        revoke_note=note,
    )
    if row is None:
        # Idempotent path — there is no active row to revoke. Return
        # the latest existing row (likely already-revoked) so the
        # caller sees the current state; 404 when no row has ever
        # existed for this (contract, user) tuple.
        existing = await find_any_override_row(
            db,
            tenant_id=getattr(current_user, "tenant_id", None),
            contract_id=contract_id,
            user_id=user_id,
        )
        if existing is None:
            raise HTTPException(
                status_code=404, detail="No override found for this user/contract"
            )
        return existing

    try:
        await log_action(
            "contract.approver_override.revoked",
            getattr(current_user, "user_id", None),
            getattr(current_user, "email", "unknown"),
            "contract",
            contract_id,
            {
                "override_id": row.get("override_id"),
                "user_id": user_id,
                "has_note": bool((note or "").strip()),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "audit log_action failed for approver_override.revoked: %s", exc
        )

    return row
