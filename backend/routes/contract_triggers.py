"""Wave 4 (contracts-time-mgmt) — billing trigger CRUD + evaluate.

Endpoints (mounted by server.py without a prefix; the full paths
become /api/contracts/{id}/billing-triggers/...):

  GET    /contracts/{contract_id}/billing-triggers
  POST   /contracts/{contract_id}/billing-triggers
  DELETE /contracts/{contract_id}/billing-triggers/{trigger_id}
  POST   /contracts/{contract_id}/billing-triggers/evaluate

The evaluate route is a manual hook for tests and the future
scheduler. It does NOT create invoice batches; see
services/contract_trigger_evaluator.py for the side-effect contract.
"""

from __future__ import annotations

import logging
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
    gen_trigger_id,
    validate_trigger_payload,
)


logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Field / event / module registration. Mirrors contracts.py / time_entries.py.
# ---------------------------------------------------------------------------
register_module_config(
    "contracts", "contract_billing_triggers", "trigger_id", "Operations"
)
register_module_events(
    "contracts",
    "contract_billing_trigger",
    ["created", "status_changed"],
)
register_fields(
    "contracts",
    "contract_billing_trigger",
    [
        {"name": "trigger_id", "type": "string", "label": "Trigger ID"},
        {"name": "contract_id", "type": "string", "label": "Contract ID"},
        {"name": "account_id", "type": "string", "label": "Account ID"},
        {"name": "trigger_type", "type": "string", "label": "Trigger Type"},
        {"name": "threshold", "type": "object", "label": "Threshold"},
        {"name": "action", "type": "string", "label": "Action"},
        {"name": "state", "type": "string", "label": "State",
         "options": ["armed", "fired", "disabled"]},
        {"name": "label", "type": "string", "label": "Label"},
        {"name": "last_fired_at", "type": "datetime", "label": "Last Fired At"},
        {"name": "tenant_id", "type": "string", "label": "Tenant ID"},
    ],
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_contract_for_user(
    db, contract_id: str, current_user, *, projection: Optional[Dict[str, int]] = None
):
    """Load a contract scoped to the caller's RBAC + tenant filter.
    Returns 404 (not 403) when the caller cannot see it so callers
    cannot infer existence across tenants."""
    rbac_filter = await get_contract_filter(current_user)
    query: Dict[str, Any] = {"contract_id": contract_id}
    if rbac_filter and rbac_filter != {}:
        query = {"$and": [{"contract_id": contract_id}, rbac_filter]}
    contract = await db.contracts.find_one(query, projection or {"_id": 0})
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if (
        getattr(current_user, "tenant_id", None)
        and contract.get("tenant_id")
        and current_user.tenant_id != contract["tenant_id"]
    ):
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


@router.get(
    "/contracts/{contract_id}/billing-triggers",
    dependencies=[Depends(require_ai_permission("contracts.view.all"))],
)
async def list_billing_triggers(contract_id: str, current_user=Depends(get_current_user)):
    """List every trigger attached to the contract regardless of
    state. The frontend filters by state in the UI; the route returns
    armed / fired / disabled together so audit trails are visible."""
    db = get_db()
    await _load_contract_for_user(db, contract_id, current_user)
    items = await db.contract_billing_triggers.find(
        {"contract_id": contract_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    total = await db.contract_billing_triggers.count_documents(
        {"contract_id": contract_id}
    )
    return {"items": items, "total": total}


@router.post(
    "/contracts/{contract_id}/billing-triggers",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def create_billing_trigger(
    contract_id: str,
    data: dict,
    request: Request = None,
    current_user=Depends(get_current_user),
):
    if not (
        has_perm(current_user, "contracts.edit")
        or has_perm(current_user, "contracts.edit.all")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    contract = await _load_contract_for_user(db, contract_id, current_user)

    errors = validate_trigger_payload(data or {})
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    trigger_id = gen_trigger_id()
    now = _now()
    doc = {
        "trigger_id": trigger_id,
        "contract_id": contract_id,
        "account_id": contract.get("account_id"),
        "trigger_type": data["trigger_type"],
        "threshold": data.get("threshold"),
        "action": data["action"],
        "state": data.get("state", "armed"),
        "label": (data.get("label") or "").strip() or None,
        "last_fired_at": None,
        "last_fire_reason": None,
        "last_action_result": None,
        "created_by": current_user.user_id,
        "created_at": now,
        "updated_at": now,
    }
    if contract.get("tenant_id"):
        doc["tenant_id"] = contract["tenant_id"]

    async with crud_envelope(
        module="contracts",
        entity="contract_billing_trigger",
        action="created",
        resource_id=trigger_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db.contract_billing_triggers.insert_one(doc)
        ctx.set_after(doc)

    try:
        await log_action(
            "contract.billing_trigger.created",
            current_user.user_id,
            current_user.email,
            "contract",
            contract_id,
            {"trigger_id": trigger_id, "trigger_type": doc["trigger_type"], "action": doc["action"]},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log_action failed for trigger create: %s", exc)
    try:
        await emit_event(
            "contracts",
            "billing_trigger_created",
            record=doc,
            user_id=current_user.user_id,
            user_email=current_user.email,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_event failed for trigger create: %s", exc)

    doc.pop("_id", None)
    return doc


@router.delete(
    "/contracts/{contract_id}/billing-triggers/{trigger_id}",
    dependencies=[Depends(require_ai_permission("contracts.edit"))],
)
async def disable_billing_trigger(
    contract_id: str,
    trigger_id: str,
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Soft-disable. The trigger row is retained for audit; only its
    state flips to 'disabled' so a subsequent evaluator pass skips
    it. Hard delete would break the audit chain that finance relies
    on; the trigger admin UI can hide disabled rows by default."""
    if not (
        has_perm(current_user, "contracts.edit")
        or has_perm(current_user, "contracts.edit.all")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    await _load_contract_for_user(db, contract_id, current_user)
    existing = await db.contract_billing_triggers.find_one(
        {"trigger_id": trigger_id, "contract_id": contract_id}, {"_id": 0}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Trigger not found")
    now = _now()
    async with crud_envelope(
        module="contracts",
        entity="contract_billing_trigger",
        action="status_changed",
        resource_id=trigger_id,
        current_user=current_user,
        db=db,
        before=existing,
        request=request,
    ) as ctx:
        await db.contract_billing_triggers.update_one(
            {"trigger_id": trigger_id, "contract_id": contract_id},
            {"$set": {"state": "disabled", "updated_at": now}},
        )
        ctx.set_after({**existing, "state": "disabled"})
    try:
        await log_action(
            "contract.billing_trigger.disabled",
            current_user.user_id,
            current_user.email,
            "contract",
            contract_id,
            {"trigger_id": trigger_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log_action failed for trigger disable: %s", exc)
    return {"trigger_id": trigger_id, "state": "disabled"}


@router.post(
    "/contracts/{contract_id}/billing-triggers/evaluate",
    dependencies=[Depends(require_ai_permission("contracts.approve"))],
)
async def evaluate_contract_triggers_route(
    contract_id: str, current_user=Depends(get_current_user),
):
    """Manual evaluation hook. Restricted to roles that can approve
    contracts or view finance — same set that will own the scheduler
    in Wave 5.

    Note: this route does NOT wrap in crud_envelope because the
    evaluator may or may not mutate state (depending on which
    triggers fire). The evaluator itself is responsible for emitting
    per-trigger fire events through its own service-internal audit
    path; treating the route call as one CRUD op would either
    double-audit or skip the per-trigger detail."""
    if not (
        has_perm(current_user, "contracts.approve")
        or has_perm(current_user, "finance.portal")
        or has_perm(current_user, "*")
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    contract = await _load_contract_for_user(db, contract_id, current_user)
    result = await evaluate_contract_billing_triggers(
        db, contract, actor_user_id=current_user.user_id,
    )
    return result
