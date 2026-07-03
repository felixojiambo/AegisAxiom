"""Wave 4 (contracts-time-mgmt) — contract adjustment CRUD.

Endpoints (mounted at root by server.py — full paths become
/api/contracts/{id}/adjustments/...):

  GET  /contracts/{contract_id}/adjustments
  POST /contracts/{contract_id}/adjustments

Adjustments capture credit/debit corrections that move a contract's
balance outside the normal time-entry-approval pathway:
  - hours_credit / hours_debit move ``hours_consumed`` and
    ``overage_hours`` (with the same BOH-with-BOA split math the
    Wave 3 debit service uses).
  - billing_credit / billing_debit move ``financial_commit``.

Every adjustment writes a row to ``contract_adjustments`` with a
before / after snapshot so the FCC audit can answer "why did this
contract's balance change outside an approved time entry?".

After the contract is mutated, the billing-trigger evaluator runs
for the affected contract. Its failure must NOT roll back the
adjustment — the adjustment is already committed and the trigger
sweep is a side effect.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_helper import get_contract_filter, get_current_user, has_perm
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
from services.contract_trigger_evaluator import (
    evaluate_contract_billing_triggers,
)


logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Field / event / module registration. Mirrors contracts.py / time_entries.py.
# ---------------------------------------------------------------------------
register_module_config(
    "contracts", "contract_adjustments", "adjustment_id", "Operations"
)
register_module_events(
    "contracts", "contract_adjustment", ["created"]
)
register_fields(
    "contracts",
    "contract_adjustment",
    [
        {"name": "adjustment_id", "type": "string", "label": "Adjustment ID"},
        {"name": "contract_id", "type": "string", "label": "Contract ID"},
        {"name": "kind", "type": "string", "label": "Kind",
         "options": ["hours_credit", "hours_debit",
                     "billing_credit", "billing_debit"]},
        {"name": "amount", "type": "number", "label": "Amount"},
        {"name": "reason_code", "type": "string", "label": "Reason Code"},
        {"name": "reason_note", "type": "string", "label": "Reason Note"},
        {"name": "actor_id", "type": "string", "label": "Actor ID"},
        {"name": "linked_time_entry_id", "type": "string",
         "label": "Linked Time Entry ID"},
        {"name": "before", "type": "object", "label": "Before Snapshot"},
        {"name": "after", "type": "object", "label": "After Snapshot"},
        {"name": "tenant_id", "type": "string", "label": "Tenant ID"},
    ],
)


VALID_ADJUSTMENT_KINDS = frozenset({
    "hours_credit",
    "hours_debit",
    "billing_credit",
    "billing_debit",
})

VALID_REASON_CODES = frozenset({
    "late_timesheet",
    "scope_change",
    "customer_credit",
    "correction",
    "goodwill",
    "manual_reconcile",
    "other",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve_total_block_hours(contract: Dict[str, Any]) -> float:
    direct = _coerce_float(contract.get("total_hours"))
    if direct > 0:
        return direct
    model = _coerce_float(contract.get("total_hours_purchased"))
    if model > 0:
        return model
    rts = contract.get("resource_types") or []
    return sum(
        _coerce_float(rt.get("hours_purchased"))
        for rt in rts
        if isinstance(rt, dict)
    )


def _snapshot(contract: Dict[str, Any]) -> Dict[str, Any]:
    """Return the balance-relevant subset of the contract for the
    before/after rows the audit cares about. Snapshot is small on
    purpose — the full contract doc would blow up the audit table."""
    return {
        "hours_consumed": _coerce_float(contract.get("hours_consumed")),
        "overage_hours": _coerce_float(contract.get("overage_hours")),
        "bill_and_actuals_active": bool(contract.get("bill_and_actuals_active")),
        "total_hours": _resolve_total_block_hours(contract),
        "financial_commit": (
            _coerce_float(contract.get("financial_commit"))
            if contract.get("financial_commit") is not None
            else None
        ),
    }


async def _load_contract_for_user(db, contract_id: str, current_user):
    rbac_filter = await get_contract_filter(current_user)
    query: Dict[str, Any] = {"contract_id": contract_id}
    if rbac_filter and rbac_filter != {}:
        query = {"$and": [{"contract_id": contract_id}, rbac_filter]}
    contract = await db.contracts.find_one(query, {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if (
        getattr(current_user, "tenant_id", None)
        and contract.get("tenant_id")
        and current_user.tenant_id != contract["tenant_id"]
    ):
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


def _validate_payload(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    kind = data.get("kind")
    if kind not in VALID_ADJUSTMENT_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"kind must be one of: {', '.join(sorted(VALID_ADJUSTMENT_KINDS))}",
        )
    reason_code = data.get("reason_code")
    if reason_code not in VALID_REASON_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"reason_code must be one of: {', '.join(sorted(VALID_REASON_CODES))}",
        )
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount must be a number")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be greater than zero")


def _apply_hours_credit(contract: Dict[str, Any], amount: float) -> Dict[str, Any]:
    """Return ($set, $inc) the update needs. Credit reduces overage
    first (since overage is the most recent debit, returning it first
    matches LIFO accounting), then dips into block hours_consumed. If
    overage drops to zero AND hours_consumed is below total, the
    bill_and_actuals_active flag flips back off."""
    overage = _coerce_float(contract.get("overage_hours"))
    consumed = _coerce_float(contract.get("hours_consumed"))
    total = _resolve_total_block_hours(contract)
    decrement_overage = min(amount, overage)
    remaining = amount - decrement_overage
    decrement_block = min(remaining, consumed)
    new_overage = max(overage - decrement_overage, 0.0)
    new_consumed = max(consumed - decrement_block, 0.0)
    set_doc: Dict[str, Any] = {
        "hours_consumed": new_consumed,
        "overage_hours": new_overage,
    }
    # Only flip the overflow flag back off when overage is zero AND
    # consumed dropped below the block size — a partial credit that
    # leaves us still in overage keeps the flag on.
    if (
        contract.get("bill_and_actuals_active")
        and new_overage == 0.0
        and (total <= 0 or new_consumed < total)
    ):
        set_doc["bill_and_actuals_active"] = False
    return {"$set": {**set_doc, "updated_at": _now()}}


def _apply_hours_debit(contract: Dict[str, Any], amount: float) -> Dict[str, Any]:
    """Manually consume hours using the same BOH-with-BOA split math
    the Wave 3 debit service applies to approved time entries."""
    ctype = (contract.get("contract_type") or "").strip()
    total = _resolve_total_block_hours(contract)
    consumed = _coerce_float(contract.get("hours_consumed"))
    overage_now = _coerce_float(contract.get("overage_hours"))
    set_doc: Dict[str, Any] = {"updated_at": _now()}
    if ctype == "boh_with_boa":
        remaining = max(total - consumed, 0.0)
        block_hours = min(amount, remaining)
        overflow = max(amount - block_hours, 0.0)
        set_doc["hours_consumed"] = consumed + block_hours
        set_doc["overage_hours"] = overage_now + overflow
        if overflow > 0 or (total > 0 and (consumed + block_hours) >= total):
            set_doc["bill_and_actuals_active"] = True
    else:
        # Legacy boh / boa / other — debit hours_consumed only.
        set_doc["hours_consumed"] = consumed + amount
    return {"$set": set_doc}


def _apply_billing_credit(contract: Dict[str, Any], amount: float) -> Dict[str, Any]:
    current = contract.get("financial_commit")
    if current is None:
        # Cannot credit against an unset financial_commit — the FCC
        # audit needs an established baseline. Raise 409 so the
        # caller knows to set financial_commit first via a debit.
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot apply billing_credit because contract.financial_commit "
                "is not set. Use billing_debit to establish the financial "
                "commit first."
            ),
        )
    new_value = max(_coerce_float(current) - amount, 0.0)
    return {"$set": {"financial_commit": new_value, "updated_at": _now()}}


def _apply_billing_debit(contract: Dict[str, Any], amount: float) -> Dict[str, Any]:
    current = _coerce_float(contract.get("financial_commit"))
    return {"$set": {"financial_commit": current + amount, "updated_at": _now()}}


def _can_view_adjustments(user) -> bool:
    """The financial adjustment ledger is more sensitive than the
    contract row itself — a hourly contractor with contracts.view.own
    can see the contract but must NOT see the adjustment ledger that
    moves billing balances. Restrict to the approver / edit-all set
    that already gates POST.
    """
    return (
        has_perm(user, "contracts.approve")
        or has_perm(user, "contracts.edit.all")
        or has_perm(user, "contracts.edit")
        or has_perm(user, "contracts.view.all")
        or has_perm(user, "*")
    )


@router.get(
    "/contracts/{contract_id}/adjustments",
    dependencies=[Depends(require_ai_permission("contracts.view.all"))],
)
async def list_contract_adjustments(
    contract_id: str, current_user=Depends(get_current_user),
):
    """List the financial-adjustment audit ledger for a contract.

    Permission gate is the contract-financial-view set, NOT the
    plain contracts-view set. A user with only ``contracts.view.own``
    can read the contract itself but is forbidden from reading the
    adjustment ledger that moves billing balances — that's an
    approver / edit-all / view-all surface."""
    if not _can_view_adjustments(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    await _load_contract_for_user(db, contract_id, current_user)
    items = await db.contract_adjustments.find(
        {"contract_id": contract_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    total = await db.contract_adjustments.count_documents(
        {"contract_id": contract_id}
    )
    return {"items": items, "total": total}


@router.post(
    "/contracts/{contract_id}/adjustments",
    dependencies=[Depends(require_ai_permission("contracts.approve"))],
)
async def create_contract_adjustment(
    contract_id: str,
    data: dict,
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Apply a credit / debit adjustment and write the audit row.
    Permission gate is the stricter of edit/approve so a Sales Ops
    user with only create cannot move a contract balance without an
    explicit approve grant."""
    if not (
        has_perm(current_user, "contracts.approve")
        or has_perm(current_user, "contracts.edit.all")
        or has_perm(current_user, "*")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    contract = await _load_contract_for_user(db, contract_id, current_user)

    _validate_payload(data)
    kind = data["kind"]
    amount = float(data["amount"])
    reason_code = data["reason_code"]
    reason_note = (data.get("reason_note") or "").strip()
    linked_entry_id = data.get("linked_time_entry_id")

    if linked_entry_id:
        entry = await db.time_entries.find_one(
            {"entry_id": linked_entry_id}, {"_id": 0, "entry_id": 1},
        )
        if not entry:
            raise HTTPException(
                status_code=404,
                detail=f"linked_time_entry_id {linked_entry_id} not found",
            )

    before = _snapshot(contract)

    if kind == "hours_credit":
        update = _apply_hours_credit(contract, amount)
    elif kind == "hours_debit":
        update = _apply_hours_debit(contract, amount)
    elif kind == "billing_credit":
        update = _apply_billing_credit(contract, amount)
    else:  # billing_debit
        update = _apply_billing_debit(contract, amount)

    adjustment_id = f"adj_{uuid.uuid4().hex[:12]}"
    async with crud_envelope(
        module="contracts",
        entity="contract_adjustment",
        action="created",
        resource_id=adjustment_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db.contracts.update_one({"contract_id": contract_id}, update)
        refreshed = await db.contracts.find_one({"contract_id": contract_id}, {"_id": 0})
        after = _snapshot(refreshed or contract)

        row: Dict[str, Any] = {
            "adjustment_id": adjustment_id,
            "contract_id": contract_id,
            "kind": kind,
            "amount": amount,
            "reason_code": reason_code,
            "reason_note": reason_note or None,
            "actor_id": current_user.user_id,
            "actor_email": getattr(current_user, "email", None),
            "linked_time_entry_id": linked_entry_id,
            "before": before,
            "after": after,
            "created_at": _now(),
        }
        if contract.get("tenant_id"):
            row["tenant_id"] = contract["tenant_id"]
        await db.contract_adjustments.insert_one(row)
        ctx.set_after(row)

    try:
        await log_action(
            f"contract.adjustment.{kind}",
            current_user.user_id,
            current_user.email,
            "contract",
            contract_id,
            {"adjustment_id": adjustment_id, "amount": amount, "reason_code": reason_code},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log_action failed for adjustment: %s", exc)
    try:
        await emit_event(
            "contracts",
            f"adjustment_{kind}",
            record=row,
            user_id=current_user.user_id,
            user_email=current_user.email,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_event failed for adjustment: %s", exc)

    # Sweep billing triggers AFTER the adjustment is committed. Its
    # failure must not roll back the adjustment.
    trigger_result: Optional[Dict[str, Any]] = None
    try:
        trigger_result = await evaluate_contract_billing_triggers(
            db, refreshed or contract, actor_user_id=current_user.user_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("trigger evaluator failed after adjustment %s: %s", adjustment_id, exc)

    row.pop("_id", None)
    response: Dict[str, Any] = {"adjustment": row}
    if trigger_result:
        response["triggers"] = trigger_result
    return response
