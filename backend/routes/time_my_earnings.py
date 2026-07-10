"""Time module — contractor self-service earnings view.

Lifted from backend/routes/finance.py::get_contractor_payments (lines 631-740).
Route prefix: /api/time  →  full path: GET /api/time/my-earnings

Permission gate: payments.view.own (or * for admins).
Read-only — no state changes, no crud_envelope.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/my-earnings",
    dependencies=[Depends(require_ai_permission("payments.view.own"))],
)
async def get_my_earnings(
    current_user=Depends(get_current_user),
):
    """
    Get payment summary for the logged-in contractor.
    Shows total earned, payment status, and time entries broken out by status.

    Accessible to: any user with payments.view.own, or * (admin override).
    """
    if not (
        has_perm(current_user, "payments.view.own")
        or has_perm(current_user, "*")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    user_id = current_user.user_id

    # Fetch all time entries logged by this contractor, most-recent first.
    time_entries = (
        await db.time_entries.find(
            {"user_id": user_id},
            {"_id": 0},
        )
        .sort("date", -1)
        .to_list(10000)
    )

    # Look up every contract referenced by those entries (for cost rates).
    contract_ids = list(
        set(t.get("contract_id") for t in time_entries if t.get("contract_id"))
    )
    contracts: dict = {}
    if contract_ids:
        contract_docs = await db.contracts.find(
            {"contract_id": {"$in": contract_ids}},
            {"_id": 0},
        ).to_list(1000)
        contracts = {c["contract_id"]: c for c in contract_docs}

    # Look up every project referenced by those entries (for display names).
    project_ids = list(
        set(t.get("project_id") for t in time_entries if t.get("project_id"))
    )
    projects: dict = {}
    if project_ids:
        project_docs = await db.projects.find(
            {"project_id": {"$in": project_ids}},
            {"_id": 0},
        ).to_list(1000)
        projects = {p["project_id"]: p for p in project_docs}

    # Compute earnings per entry.
    total_hours: float = 0.0
    total_earned_approved: float = 0.0
    total_earned_pending: float = 0.0

    entries_by_status: dict = {
        "pending": [],
        "approved": [],
        "rejected": [],
    }

    for entry in time_entries:
        contract = contracts.get(entry.get("contract_id"))
        if not contract:
            # Entry has no linked contract — skip; cannot compute cost rate.
            continue

        # Find the per-resource cost rate for this contractor.
        cost_rate: float = 0.0
        for resource in contract.get("resources", []):
            if resource.get("user_id") == user_id:
                cost_rate = float(resource.get("cost_rate", 0))
                break

        # Source stores duration in minutes.
        duration_hours: float = float(entry.get("duration", 0)) / 60.0
        earned: float = duration_hours * cost_rate

        entry_detail = {
            "time_entry_id": entry.get("time_entry_id"),
            "date": entry.get("date"),
            "project_id": entry.get("project_id"),
            "project_name": projects.get(entry.get("project_id"), {}).get(
                "name", "Unknown Project"
            ),
            "contract_id": entry.get("contract_id"),
            "contract_name": contract.get("name", "Unknown Contract"),
            "duration_hours": duration_hours,
            "cost_rate": cost_rate,
            "earned": earned,
            "status": entry.get("status", "pending"),
            "notes": entry.get("notes", ""),
        }

        status = entry.get("status", "pending")
        if status in entries_by_status:
            entries_by_status[status].append(entry_detail)

        total_hours += duration_hours
        if status == "approved":
            total_earned_approved += earned
        elif status == "pending":
            total_earned_pending += earned

    # Build the list of contracts the contractor is assigned to
    # (carried from the source; note: this is built above but returned here).
    assigned_contracts: list = []
    for contract in contracts.values():
        for resource in contract.get("resources", []):
            if resource.get("user_id") == user_id:
                assigned_contracts.append(
                    {
                        "contract_id": contract["contract_id"],
                        "contract_name": contract.get("name"),
                        "contract_type": contract.get("contract_type"),
                        "account_name": contract.get("account_name"),
                        "cost_rate": resource.get("cost_rate"),
                        "role": resource.get("role", ""),
                    }
                )
                break

    return {
        "user_id": user_id,
        "user_name": current_user.name,
        "summary": {
            "total_hours_logged": total_hours,
            "total_earned_approved": total_earned_approved,
            "total_earned_pending": total_earned_pending,
            "total_earned": total_earned_approved + total_earned_pending,
        },
        "entries_by_status": entries_by_status,
        "assigned_contracts": assigned_contracts,
    }
