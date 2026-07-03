from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from datetime import datetime, timezone
import uuid
import logging

logger = logging.getLogger(__name__)

from database import get_db
from models import Account, AccountCreate, AccountUpdate, Deal, DealCreate, DealUpdate, PARTNERSHIP_STATUSES
from auth_helper import get_current_user, get_account_filter, get_deal_filter, has_perm, verify_ownership
from services.audit_service import log_action
from services.event_bus import emit_event
from services.field_registry import register_fields, register_module_events, register_module_config
from services.response_filter import filter_response_fields

router = APIRouter()

register_module_config("accounts", "accounts", "account_id", "CRM & Sales")
register_module_config("deals", "deals", "deal_id", "CRM & Sales")
register_module_events("accounts", "account", ["created", "updated", "deleted"])
register_module_events("deals", "deal", ["created", "updated", "deleted"])

register_fields("accounts", "account", [
    {"name": "name", "type": "string", "label": "Account Name", "description": "Company or organization name"},
    {"name": "account_type", "type": "string", "label": "Account Type", "description": "Type of account (company, partner, prospect)"},
    {"name": "industry", "type": "string", "label": "Industry", "description": "Industry classification"},
    {"name": "email", "type": "string", "label": "Email", "description": "Primary account email address"},
    {"name": "phone", "type": "string", "label": "Phone", "description": "Primary contact phone number"},
    {"name": "website", "type": "string", "label": "Website", "description": "Company website URL"},
    {"name": "address", "type": "string", "label": "Address", "description": "Physical address"},
    {"name": "assigned_sales_rep_name", "type": "string", "label": "Sales Rep", "description": "Assigned sales representative"},
    {"name": "assigned_agent_name", "type": "string", "label": "Agent", "description": "Assigned partner or agent"},
    {"name": "is_partner", "type": "boolean", "label": "Is Partner", "description": "Whether this account is a referral partner"},
    {"name": "partner_tier", "type": "string", "label": "Partner Tier", "description": "Partner tier level (gold, silver, bronze)"},
    {"name": "partner_status", "type": "string", "label": "Partner Status", "description": "Partner relationship status (active, inactive, suspended)"},
    {"name": "partner_commission_rate", "type": "number", "label": "Partner Commission Rate", "description": "Commission percentage for partner referrals"},
    {"name": "source", "type": "string", "label": "Source", "description": "Data source (local, zoho)"},
    {"name": "is_test_data", "type": "boolean", "label": "Is Test Data", "description": "Flag indicating test/demo data"},
])

def _validate_partnership_fields(status, rate):
    """Shared validation for the director-tracked partnership fields.

    Called from both create_account and update_account — keep the rules in
    one place so POST and PUT can never drift. Does not check presence;
    either field may legitimately be None/absent.
    """
    if status is not None and status not in PARTNERSHIP_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid partnership_status. Must be one of: {', '.join(PARTNERSHIP_STATUSES)}",
        )
    if rate is not None and not (0 <= rate <= 100):
        raise HTTPException(
            status_code=400,
            detail="commission_rate must be between 0 and 100 (percentage).",
        )


register_fields("deals", "deal", [
    {"name": "name", "type": "string", "label": "Deal Name", "description": "Name of the closed deal"},
    {"name": "account_name", "type": "string", "label": "Account Name", "description": "Company the deal is with"},
    {"name": "stage", "type": "string", "label": "Stage", "description": "Deal stage (Prospecting, Proposal, Negotiation, Closed Won, Closed Lost)"},
    {"name": "amount", "type": "number", "label": "Amount", "description": "Deal revenue value"},
    {"name": "close_date", "type": "date", "label": "Close Date", "description": "Expected or actual deal close date"},
    {"name": "deal_owner_name", "type": "string", "label": "Deal Owner", "description": "User who owns this deal"},
    {"name": "assigned_sales_rep_name", "type": "string", "label": "Sales Rep", "description": "Sales representative assigned to the deal"},
    {"name": "probability", "type": "number", "label": "Probability", "description": "Win probability percentage (0-100)"},
    {"name": "description", "type": "string", "label": "Description", "description": "Deal description and notes"},
    {"name": "source", "type": "string", "label": "Source", "description": "Data source (local, zoho)"},
    {"name": "is_test_data", "type": "boolean", "label": "Is Test Data", "description": "Flag indicating test/demo data"},
])

# ---- ACCOUNTS ----

@router.get("/accounts")
async def get_accounts(
    skip: int = 0, 
    limit: int = 5000, 
    source: str = None,  # Filter by source: "local" or "zoho"
    test_only: Optional[bool] = None,
    current_user=Depends(get_current_user)
):
    db = get_db()
    base_filter = await get_account_filter(current_user)

    # Add source filter if specified (admin-only feature for test accounts)
    if source:
        if base_filter:
            base_filter = {"$and": [base_filter, {"source": source}]}
        else:
            base_filter = {"source": source}

    # Test data filter: None=all, True=test only, False=real only
    if test_only is True:
        test_filter = {"is_test_data": True}
    elif test_only is False:
        test_filter = {"is_test_data": {"$ne": True}}
    else:
        test_filter = None
    if test_filter:
        if base_filter:
            base_filter = {"$and": [base_filter, test_filter]}
        else:
            base_filter = test_filter
    
    capped = min(limit, 5000)
    docs = await db.accounts.find(base_filter, {"_id": 0}).sort("name", 1).skip(skip).limit(capped).to_list(capped)
    total = await db.accounts.count_documents(base_filter)

    # Enrich accounts with sales rep / agent names if missing
    rep_ids = set()
    for doc in docs:
        if doc.get("assigned_sales_rep_id") and not doc.get("assigned_sales_rep_name"):
            rep_ids.add(doc["assigned_sales_rep_id"])
        if doc.get("assigned_agent_id") and not doc.get("assigned_agent_name"):
            rep_ids.add(doc["assigned_agent_id"])
    if rep_ids:
        users = await db.users.find(
            {"user_id": {"$in": list(rep_ids)}},
            {"_id": 0, "user_id": 1, "name": 1}
        ).to_list(len(rep_ids))
        name_map = {u["user_id"]: u["name"] for u in users}
        for doc in docs:
            if doc.get("assigned_sales_rep_id") and not doc.get("assigned_sales_rep_name"):
                doc["assigned_sales_rep_name"] = name_map.get(doc["assigned_sales_rep_id"])
            if doc.get("assigned_agent_id") and not doc.get("assigned_agent_name"):
                doc["assigned_agent_name"] = name_map.get(doc["assigned_agent_id"])

    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.post("/accounts")
async def create_account(data: AccountCreate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "accounts.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    _validate_partnership_fields(data.partnership_status, data.commission_rate)
    db = get_db()
    account_id = f"acc_{uuid.uuid4().hex[:12]}"
    doc = {
        "account_id": account_id,
        **data.model_dump(),
        "zoho_account_id": None,
        "qbo_customer_id": None,
        "source": "local",  # Mark as locally created (not from Zoho)
        "created_by": current_user.user_id,
        "created_at": datetime.now(timezone.utc),
    }
    # Auto-assign sales rep if creator is a sales_rep and none was set
    if not doc.get("assigned_sales_rep_id") and current_user.role == "sales_rep":
        doc["assigned_sales_rep_id"] = current_user.user_id
        doc["assigned_sales_rep_name"] = current_user.name
    await db.accounts.insert_one(doc)
    doc.pop("_id", None)
    await emit_event("accounts", "created", record=doc, user_id=current_user.user_id, user_email=current_user.email)
    return Account(**doc)


@router.get("/accounts/{account_id}")
async def get_account(account_id: str, current_user=Depends(get_current_user)):
    db = get_db()
    rbac = await get_account_filter(current_user)
    q = {"account_id": account_id}
    if rbac:
        q = {"$and": [q, rbac]}
    doc = await db.accounts.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Account not found")

    # Enrich with sales rep / agent name if missing
    if doc.get("assigned_sales_rep_id") and not doc.get("assigned_sales_rep_name"):
        rep = await db.users.find_one({"user_id": doc["assigned_sales_rep_id"]}, {"_id": 0, "name": 1})
        if rep:
            doc["assigned_sales_rep_name"] = rep["name"]
    if doc.get("assigned_agent_id") and not doc.get("assigned_agent_name"):
        agent = await db.users.find_one({"user_id": doc["assigned_agent_id"]}, {"_id": 0, "name": 1})
        if agent:
            doc["assigned_agent_name"] = agent["name"]

    return doc


@router.put("/accounts/{account_id}")
async def update_account(account_id: str, data: AccountUpdate, current_user=Depends(get_current_user)):
    # SECURITY FIX C-9: Verify ownership when using edit.own permission
    db = get_db()
    
    # Check if user has edit.all permission (can edit anything)
    has_edit_all = has_perm(current_user, "accounts.edit.all")
    has_edit_own = has_perm(current_user, "accounts.edit.own")
    
    if not has_edit_all and not has_edit_own:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # If only has edit.own, verify actual ownership
    if has_edit_own and not has_edit_all:
        account = await db.accounts.find_one({"account_id": account_id}, {"_id": 0})
        if not verify_ownership(account, current_user, "account"):
            raise HTTPException(status_code=403, detail="You can only edit accounts assigned to you")

    # Director-tracked signed-partnership fields — same rules as create_account.
    _validate_partnership_fields(data.partnership_status, data.commission_rate)

    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    old_account = await db.accounts.find_one({"account_id": account_id}, {"_id": 0})
    await db.accounts.update_one({"account_id": account_id}, {"$set": update_data})
    updated_account = await db.accounts.find_one({"account_id": account_id}, {"_id": 0})
    await emit_event("accounts", "updated", record=updated_account, old_record=old_account, user_id=current_user.user_id, user_email=current_user.email)
    return updated_account


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "accounts.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Cascade: clean up all related data
    deal_ids = [d["deal_id"] for d in await db.deals.find({"account_id": account_id}, {"deal_id": 1, "_id": 0}).to_list(1000)]
    if deal_ids:
        await db.commissions.delete_many({"deal_id": {"$in": deal_ids}})
    await db.deals.delete_many({"account_id": account_id})
    await db.invoices.delete_many({"account_id": account_id})
    await db.contracts.delete_many({"account_id": account_id})
    await db.field_jobs.delete_many({"account_id": account_id})
    await db.accounts.delete_one({"account_id": account_id})
    await log_action("account_delete", current_user.user_id, current_user.email, "account", account_id)
    await emit_event("accounts", "deleted", record={"account_id": account_id}, user_id=current_user.user_id, user_email=current_user.email)
    return {"message": "Account and related data deleted"}


# ---- DEALS ----

@router.get("/deals")
async def get_deals(skip: int = 0, limit: int = 5000, stage: str = None, current_user=Depends(get_current_user)):
    db = get_db()
    filt = await get_deal_filter(current_user)
    if stage:
        filt = {**filt, "stage": stage} if filt else {"stage": stage}
    capped = min(limit, 5000)
    docs = await db.deals.find(filt, {"_id": 0}).sort("created_at", -1).skip(skip).limit(capped).to_list(capped)
    total = await db.deals.count_documents(filt)
    docs = filter_response_fields(docs, current_user, "deals")
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.post("/deals")
async def create_deal(data: DealCreate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "deals.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    account = await db.accounts.find_one({"account_id": data.account_id}, {"_id": 0, "name": 1})
    rep_name = None
    if data.assigned_sales_rep_id:
        rep = await db.users.find_one({"user_id": data.assigned_sales_rep_id}, {"_id": 0, "name": 1})
        rep_name = rep["name"] if rep else None

    deal_id = f"deal_{uuid.uuid4().hex[:12]}"
    doc = {
        "deal_id": deal_id,
        **data.model_dump(),
        "account_name": account["name"] if account else None,
        "deal_owner_id": current_user.user_id,
        "deal_owner_name": current_user.name,
        "assigned_sales_rep_name": rep_name,
        "zoho_deal_id": None,
        "source": "local",  # Mark as locally created (not from Zoho)
        "created_at": datetime.now(timezone.utc),
    }
    await db.deals.insert_one(doc)
    doc.pop("_id", None)
    await emit_event("deals", "created", record=doc, user_id=current_user.user_id, user_email=current_user.email)
    return Deal(**doc)


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: str, current_user=Depends(get_current_user)):
    db = get_db()
    rbac = await get_deal_filter(current_user)
    q = {"deal_id": deal_id}
    if rbac:
        q = {"$and": [q, rbac]}
    doc = await db.deals.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Deal not found")
    return doc


@router.put("/deals/{deal_id}")
async def update_deal(deal_id: str, data: DealUpdate, current_user=Depends(get_current_user)):
    # SECURITY FIX C-9: Verify ownership when using edit.own permission
    db = get_db()
    
    # Check if user has edit.all permission (can edit anything)
    has_edit_all = has_perm(current_user, "deals.edit.all")
    has_edit_own = has_perm(current_user, "deals.edit.own")
    
    if not has_edit_all and not has_edit_own:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # If only has edit.own, verify actual ownership
    if has_edit_own and not has_edit_all:
        deal = await db.deals.find_one({"deal_id": deal_id}, {"_id": 0})
        if not verify_ownership(deal, current_user, "deal"):
            raise HTTPException(status_code=403, detail="You can only edit deals assigned to you")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}

    # Field-level permission checks for deal lifecycle actions
    if "stage" in update_data:
        if not has_perm(current_user, "deals.stage.change") and not has_perm(current_user, "deals.edit.all"):
            raise HTTPException(status_code=403, detail="Insufficient permissions to change deal stage")
        # Check for close specifically
        if update_data.get("stage") in ("Closed Won", "Closed Lost"):
            if not has_perm(current_user, "deals.close") and not has_perm(current_user, "deals.edit.all"):
                raise HTTPException(status_code=403, detail="Insufficient permissions to close deals")
    if "deal_owner_id" in update_data or "assigned_sales_rep_id" in update_data:
        if not has_perm(current_user, "deals.assign") and not has_perm(current_user, "deals.edit.all"):
            raise HTTPException(status_code=403, detail="Insufficient permissions to assign deals")

    await db.deals.update_one({"deal_id": deal_id}, {"$set": update_data})

    # Write-back to Zoho if this deal is linked
    deal = await db.deals.find_one({"deal_id": deal_id}, {"_id": 0})
    if deal and deal.get("zoho_deal_id"):
        try:
            from services.zoho_service import ZohoService
            zoho = ZohoService()
            zoho_updates = {}
            if "name" in update_data:
                zoho_updates["deal_name"] = update_data["name"]
            for f in ["stage", "amount", "close_date"]:
                if f in update_data:
                    zoho_updates[f] = update_data[f]
            if zoho_updates:
                await zoho.update_deal_in_zoho(db, deal["zoho_deal_id"], zoho_updates)
        except Exception as e:
            logger.warning(f"Zoho write-back failed for deal {deal_id}: {e}")

    await emit_event("deals", "updated", record=deal, user_id=current_user.user_id, user_email=current_user.email)
    return deal


@router.delete("/deals/{deal_id}")
async def delete_deal(deal_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "deals.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Cascade: clean up related commissions and invoices
    await db.commissions.delete_many({"deal_id": deal_id})
    await db.invoices.delete_many({"deal_id": deal_id})
    await db.deals.delete_one({"deal_id": deal_id})
    await log_action("deal_delete", current_user.user_id, current_user.email, "deal", deal_id)
    await emit_event("deals", "deleted", record={"deal_id": deal_id}, user_id=current_user.user_id, user_email=current_user.email)
    return {"message": "Deal and related data deleted"}
