"""API endpoints for editing Zoho-synced records with write-back and conflict detection."""

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone

from auth_helper import get_current_user, has_perm
from database import get_db
from models import ZohoDealUpdate, ZohoCommissionUpdate
from services.zoho_service import ZohoService
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter(prefix="/zoho-sync", tags=["zoho-sync"])
zoho = ZohoService()

register_module_config("zoho_sync", "zoho_deals", "zoho_id", "Integrations")
register_module_events("zoho_sync", "zoho_deal", ["updated"])
register_fields("zoho_sync", "zoho_deal", [
    {"name": "deal_name", "type": "string", "label": "Deal Name", "description": "Name of the deal synced from Zoho CRM"},
    {"name": "stage", "type": "string", "label": "Stage", "description": "Deal stage in Zoho (Closed Won, Closed Lost, Proposal Sent, etc.)"},
    {"name": "amount", "type": "number", "label": "Amount", "description": "Deal dollar value"},
    {"name": "account_name", "type": "string", "label": "Account", "description": "Company/account name from Zoho"},
    {"name": "deal_owner_name", "type": "string", "label": "Deal Owner", "description": "Deal owner's full name in Zoho"},
    {"name": "close_date", "type": "date", "label": "Close Date", "description": "When the deal closed or is expected to close"},
])


def _parse_synced_at(doc: dict) -> datetime:
    sa = doc.get("synced_at")
    if isinstance(sa, datetime):
        return sa if sa.tzinfo else sa.replace(tzinfo=timezone.utc)
    if isinstance(sa, str):
        dt = datetime.fromisoformat(sa)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


# ──────────────────────────────────────────────
#  ZOHO DEALS — Edit + Write-back
# ──────────────────────────────────────────────

@router.put("/deals/{zoho_id}")
async def update_zoho_deal(zoho_id: str, data: ZohoDealUpdate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "deals.edit.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    local = await db.zoho_deals.find_one({"zoho_id": zoho_id}, {"_id": 0})
    if not local:
        raise HTTPException(status_code=404, detail="Zoho deal not found")

    updates = {k: v for k, v in data.model_dump().items() if v is not None and k != "force"}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Conflict detection (skip if force=True)
    if not data.force:
        conflict_remote = await zoho.detect_conflict(db, "Deals", zoho_id, _parse_synced_at(local))
        if conflict_remote:
            return {
                "conflict": True,
                "message": "This record was modified in Zoho since the last sync.",
                "local": {k: local.get(k) for k in ["deal_name", "stage", "amount", "close_date", "synced_at"]},
                "remote": {
                    "deal_name": conflict_remote.get("Deal_Name"),
                    "stage": conflict_remote.get("Stage"),
                    "amount": conflict_remote.get("Amount"),
                    "close_date": str(conflict_remote.get("Close_Date", "")),
                    "modified_time": conflict_remote.get("Modified_Time"),
                },
            }

    # Push to Zoho CRM
    result = await zoho.update_deal_in_zoho(db, zoho_id, updates)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=f"Zoho update failed: {result['error']}")

    # Update local DB
    updates["synced_at"] = datetime.now(timezone.utc)
    await db.zoho_deals.update_one({"zoho_id": zoho_id}, {"$set": updates})

    # Also update the local deals collection if there's a linked record
    linked = await db.deals.find_one({"zoho_deal_id": zoho_id}, {"_id": 0})
    if linked:
        deal_updates = {}
        if "deal_name" in updates:
            deal_updates["name"] = updates["deal_name"]
        for f in ["stage", "amount", "close_date"]:
            if f in updates:
                deal_updates[f] = updates[f]
        if deal_updates:
            await db.deals.update_one({"zoho_deal_id": zoho_id}, {"$set": deal_updates})

    doc = await db.zoho_deals.find_one({"zoho_id": zoho_id}, {"_id": 0})
    return {"success": True, "record": doc}


# ──────────────────────────────────────────────
#  ZOHO COMMISSIONS — Edit + Write-back
# ──────────────────────────────────────────────

@router.put("/commissions/{zoho_id}")
async def update_zoho_commission(zoho_id: str, data: ZohoCommissionUpdate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "commissions.edit.all") and not has_perm(current_user, "commissions.mark_paid"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    local = await db.zoho_commissions.find_one({"zoho_id": zoho_id}, {"_id": 0})
    if not local:
        raise HTTPException(status_code=404, detail="Zoho commission not found")

    updates = {k: v for k, v in data.model_dump().items() if v is not None and k != "force"}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Conflict detection
    if not data.force:
        conflict_remote = await zoho.detect_conflict(db, "Commissons_Data", zoho_id, _parse_synced_at(local))
        if conflict_remote:
            return {
                "conflict": True,
                "message": "This record was modified in Zoho since the last sync.",
                "local": {k: local.get(k) for k in [
                    "deal_name", "sales_rep_commission", "sales_rep_commission_status",
                    "agent_commission", "agent_commission_status", "payment_date", "synced_at",
                ]},
                "remote": {
                    "deal_name": (conflict_remote.get("Deal_Name") or {}).get("name") if isinstance(conflict_remote.get("Deal_Name"), dict) else conflict_remote.get("Deal_Name"),
                    "sales_rep_commission": conflict_remote.get("Sales_Rep1"),
                    "sales_rep_commission_status": conflict_remote.get("Sales_Rep_Commission_Status"),
                    "agent_commission": conflict_remote.get("Agent_Rep_Commission"),
                    "agent_commission_status": conflict_remote.get("Agen_Commission_Status"),
                    "payment_date": str(conflict_remote.get("Agent_Commission_Payment_Date", "")),
                    "modified_time": conflict_remote.get("Modified_Time"),
                },
            }

    # Push to Zoho CRM
    result = await zoho.update_commission_in_zoho(db, zoho_id, updates)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=f"Zoho update failed: {result['error']}")

    # Update local DB
    updates["synced_at"] = datetime.now(timezone.utc)
    await db.zoho_commissions.update_one({"zoho_id": zoho_id}, {"$set": updates})

    doc = await db.zoho_commissions.find_one({"zoho_id": zoho_id}, {"_id": 0})
    return {"success": True, "record": doc}
