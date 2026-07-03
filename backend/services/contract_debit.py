"""Wave 3 (contracts-time-mgmt) — shared contract debit service.

Single source of truth for how an approved time entry mutates a
contract's hour balance and writes the contract_time_applications
ledger row. Both the single-approve and bulk-approve routes call this
function so the debit math, idempotency guard, and effective billing
mode classification cannot drift between them.

Idempotency
-----------
``contract_time_applications`` carries a unique index on ``entry_id``
(Wave 0 migration). If a row already exists for ``time_entry.entry_id``,
this service returns the existing row's summary and does NOT touch the
contract again. That makes ``apply_time_entry_to_contract`` safe to
call from a retry path or a bulk approve loop that processes the same
entry twice.

Billing modes
-------------
The contract type plus the entry's billing_type produce an
``effective_billing_mode`` that the ledger row records. Modes:

  non_billable    billing_type=non_billable; zero debit, zero revenue.
  fixed_fee       billing_type=fixed_fee OR contract_type=fixed_fee;
                  cost tracked but no BOH debit.
  block           billable hours that fit inside a BOH or boh_with_boa
                  block remainder.
  overflow        billable hours that exceeded a boh_with_boa block;
                  bills against overage rates.
  bill_and_actuals billable hours against a contract_type=boa; no block
                  debit, hours tracked for AR.
  unhandled       billable hours against a contract type this service
                  does not yet have explicit behavior for. Ledger row
                  is written for traceability; contract balance is
                  left alone.

See ``backend/tests/test_contract_boh_with_boa_debit.py`` for the
locked behavioral contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


VALID_BILLING_TYPES = frozenset({"billable", "non_billable", "fixed_fee"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_float(value: Any) -> float:
    """Permissive float coercion that returns 0.0 for None / blank /
    invalid so the ledger math never blows up on missing data. Cost /
    revenue rows that show zero are easier to triage than rows that
    raised mid-debit."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _contract_total_block_hours(contract: Dict[str, Any]) -> float:
    """Block-size of a BOH-style contract. Prefers the canonical
    ``total_hours`` field that ``routes/contracts.create_contract``
    writes; falls back to ``total_hours_purchased`` (model-level
    name) and finally to the sum of ``resource_types[].hours_purchased``
    so older or partially-migrated rows still resolve."""
    direct = _coerce_float(contract.get("total_hours"))
    if direct > 0:
        return direct
    direct_model = _coerce_float(contract.get("total_hours_purchased"))
    if direct_model > 0:
        return direct_model
    rts = contract.get("resource_types") or []
    return sum(_coerce_float(rt.get("hours_purchased")) for rt in rts if isinstance(rt, dict))


def _classify_effective_mode(
    contract: Dict[str, Any],
    billing_type: str,
    *,
    block_hours: float,
    overage_hours: float,
) -> str:
    """Pick the ledger row's effective_billing_mode string from the
    billing_type + contract_type + computed split. Order matters:
    non_billable and fixed_fee short-circuit before any block math."""
    if billing_type == "non_billable":
        return "non_billable"
    if billing_type == "fixed_fee":
        return "fixed_fee"
    ctype = (contract.get("contract_type") or "").strip()
    if ctype == "fixed_fee":
        return "fixed_fee"
    if ctype == "boh_with_boa":
        if block_hours > 0 and overage_hours == 0:
            return "block"
        if block_hours == 0 and overage_hours > 0:
            return "overflow"
        if block_hours > 0 and overage_hours > 0:
            return "block_and_overflow"
        return "overflow"  # zero-hour edge case, treat as overflow
    if ctype == "boh":
        return "block"
    if ctype == "boa":
        return "bill_and_actuals"
    return "unhandled"


def _build_ledger_doc(
    *,
    time_entry: Dict[str, Any],
    contract: Dict[str, Any],
    block_hours: float,
    overage_hours: float,
    cost_rate: float,
    sell_rate: float,
    overage_cost_rate: float,
    overage_sell_rate: float,
    effective_billing_mode: str,
    billing_type: str,
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    """Compose the contract_time_applications row. Fields mirror the
    Wave 0 migration's index plan + the Wave 3 spec list."""
    block_cost = round(block_hours * cost_rate, 4)
    block_revenue = round(block_hours * sell_rate, 4)
    overage_cost = round(overage_hours * overage_cost_rate, 4)
    overage_revenue = round(overage_hours * overage_sell_rate, 4)

    doc: Dict[str, Any] = {
        "application_id": f"appl_{uuid.uuid4().hex[:12]}",
        "entry_id": time_entry["entry_id"],
        "contract_id": contract.get("contract_id"),
        "applied_at": _now(),
        "applied_by": actor_user_id,
        "billing_type": billing_type,
        "effective_billing_mode": effective_billing_mode,
        "block_hours": round(block_hours, 4),
        "overage_hours": round(overage_hours, 4),
        "cost_rate_snapshot": cost_rate,
        "sell_rate_snapshot": sell_rate,
        "overage_cost_rate_snapshot": overage_cost_rate,
        "overage_sell_rate_snapshot": overage_sell_rate,
        "block_cost": block_cost,
        "block_revenue": block_revenue,
        "overage_cost": overage_cost,
        "overage_revenue": overage_revenue,
        "user_id": time_entry.get("user_id"),
        "project_id": time_entry.get("project_id"),
        "resource_type_id": time_entry.get("resource_type_id"),
        "entry_hours": _coerce_float(time_entry.get("hours")),
        "entry_date": time_entry.get("date"),
    }
    if contract.get("tenant_id"):
        doc["tenant_id"] = contract["tenant_id"]
    return doc


async def apply_time_entry_to_contract(
    db,
    *,
    time_entry: Dict[str, Any],
    contract: Optional[Dict[str, Any]],
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply one approved time entry's hours to its contract.

    Returns a result dict::

        {
            "applied": bool,            # False when idempotent short-circuit hit
            "already_applied": bool,    # True when the entry's ledger row exists
            "application": <ledger row>,
            "block_hours": float,
            "overage_hours": float,
            "effective_billing_mode": str,
            "contract_updates": dict,   # the $set payload pushed to db.contracts
        }

    The route layer treats a return as success regardless of
    ``applied`` — that signal is for tests / audit so the route can
    distinguish a fresh debit from a re-approval no-op.
    """
    entry_id = time_entry.get("entry_id")
    if not entry_id:
        raise ValueError("apply_time_entry_to_contract requires time_entry.entry_id")

    # ---- Idempotency: existing ledger row short-circuits.
    existing = await db.contract_time_applications.find_one({"entry_id": entry_id}, {"_id": 0})
    if existing:
        return {
            "applied": False,
            "already_applied": True,
            "application": existing,
            "block_hours": _coerce_float(existing.get("block_hours")),
            "overage_hours": _coerce_float(existing.get("overage_hours")),
            "effective_billing_mode": existing.get("effective_billing_mode"),
            "contract_updates": {},
        }

    # ---- Resolve billing classification.
    billing_type = (time_entry.get("billing_type") or "").strip()
    if billing_type not in VALID_BILLING_TYPES:
        # Fall back via is_billable when billing_type was not stamped
        # at create time (legacy entries). Wave 3 ensures all NEW
        # entries carry billing_type explicitly; this branch keeps
        # the debit service safe for older rows that get approved
        # after the Wave 3 deploy.
        billing_type = "billable" if time_entry.get("is_billable", True) else "non_billable"

    cost_rate = _coerce_float(
        time_entry.get("cost_rate_snapshot")
        if time_entry.get("cost_rate_snapshot") is not None
        else time_entry.get("cost_rate")
    )
    sell_rate = _coerce_float(
        time_entry.get("sell_rate_snapshot")
        if time_entry.get("sell_rate_snapshot") is not None
        else time_entry.get("sell_rate")
    )
    hours = _coerce_float(time_entry.get("hours"))

    contract = contract or {}
    ctype = (contract.get("contract_type") or "").strip()

    # ---- Compute split by billing type / contract type.
    block_hours = 0.0
    overage_hours = 0.0
    overage_cost_rate = _coerce_float(contract.get("overage_cost_rate")) or cost_rate
    overage_sell_rate = _coerce_float(contract.get("overage_bill_rate")) or sell_rate

    contract_updates: Dict[str, Any] = {}
    contract_increments: Dict[str, float] = {}
    contract_set: Dict[str, Any] = {}

    if billing_type == "non_billable":
        # Zero debit, zero revenue. Ledger row written for project /
        # time-reporting traceability (and for idempotency).
        pass
    elif billing_type == "fixed_fee" or ctype == "fixed_fee":
        # Cost is real (we still pay the contractor) but no BOH debit
        # and no AR contribution. The ledger row captures cost for the
        # margin summary; overage_* stay zero.
        pass
    elif ctype == "boh_with_boa":
        total_block = _contract_total_block_hours(contract)
        consumed = _coerce_float(contract.get("hours_consumed"))
        remaining = max(total_block - consumed, 0.0)
        block_hours = min(hours, remaining)
        overage_hours = max(hours - block_hours, 0.0)
        if block_hours > 0:
            contract_increments["hours_consumed"] = block_hours
        if overage_hours > 0:
            contract_increments["overage_hours"] = overage_hours
        # Flip the overflow flag once the block is exhausted OR any
        # overage has been recorded. Use computed total instead of
        # re-reading contract.total_hours so partially-migrated rows
        # without total_hours but with resource_types still flip.
        if overage_hours > 0 or (consumed + block_hours) >= total_block > 0:
            contract_set["bill_and_actuals_active"] = True
        contract_set["last_usage_date"] = _now()
    elif ctype == "boh":
        block_hours = hours
        contract_increments["hours_consumed"] = block_hours
        contract_set["last_usage_date"] = _now()
    elif ctype == "boa":
        # Bill-and-actuals contracts do not debit a block. Hours flow
        # to AR through weekly_summary today; the ledger row preserves
        # the snapshot pair for that summary path.
        if "total_hours_logged" in contract:
            contract_increments["total_hours_logged"] = hours
    else:
        # Unknown contract type — write the ledger but do not touch
        # contract balances. Surfaces in tests via the "unhandled"
        # effective_billing_mode.
        pass

    effective_mode = _classify_effective_mode(
        contract,
        billing_type,
        block_hours=block_hours,
        overage_hours=overage_hours,
    )

    # ---- Ledger-first ordering for race safety.
    #
    # Mongo does not give us a multi-collection transaction in this
    # deployment (the repo's existing patterns don't use sessions), so
    # the unique index on contract_time_applications.entry_id is the
    # only atomic claim we have. Inserting the ledger row FIRST makes
    # the entry_id claim happen before any contract balance moves. If
    # two approval requests race, exactly one succeeds at the insert
    # and the other gets DuplicateKeyError and short-circuits — the
    # contract balance is moved at most once.
    #
    # Trade-off: if the contract update fails after the ledger insert,
    # we have an orphan ledger row recording an intent that did not
    # land on the contract. That is far less bad than a double debit
    # because (a) the ledger row records the snapshot pair so a
    # reconciliation job can re-apply, and (b) finance summaries
    # already prefer the ledger row over live contract balances for
    # historical periods.
    ledger_doc = _build_ledger_doc(
        time_entry=time_entry,
        contract=contract,
        block_hours=block_hours,
        overage_hours=overage_hours,
        cost_rate=cost_rate,
        sell_rate=sell_rate,
        overage_cost_rate=overage_cost_rate,
        overage_sell_rate=overage_sell_rate,
        effective_billing_mode=effective_mode,
        billing_type=billing_type,
        actor_user_id=actor_user_id,
    )
    try:
        await db.contract_time_applications.insert_one(ledger_doc)
    except Exception as exc:  # noqa: BLE001 — DuplicateKeyError under a
        # parallel-approval race. Whoever lost the race short-circuits
        # to the existing row; the contract is not touched again.
        msg = str(exc).lower()
        if "duplicate" not in msg and "e11000" not in msg:
            raise
        existing = await db.contract_time_applications.find_one(
            {"entry_id": entry_id}, {"_id": 0}
        )
        return {
            "applied": False,
            "already_applied": True,
            "application": existing,
            "block_hours": _coerce_float((existing or {}).get("block_hours")),
            "overage_hours": _coerce_float((existing or {}).get("overage_hours")),
            "effective_billing_mode": (existing or {}).get("effective_billing_mode"),
            "contract_updates": {},
        }
    ledger_doc.pop("_id", None)

    # ---- Persist contract updates (when any). The ledger claim above
    # already secured idempotency, so this $inc is guaranteed to fire
    # at most once per entry_id.
    if contract.get("contract_id") and (contract_increments or contract_set):
        update_doc: Dict[str, Any] = {}
        if contract_increments:
            update_doc["$inc"] = contract_increments
        contract_set["updated_at"] = _now()
        update_doc["$set"] = contract_set
        await db.contracts.update_one(
            {"contract_id": contract["contract_id"]}, update_doc
        )
        contract_updates = {**contract_set, **{f"+{k}": v for k, v in contract_increments.items()}}

    return {
        "applied": True,
        "already_applied": False,
        "application": ledger_doc,
        "block_hours": block_hours,
        "overage_hours": overage_hours,
        "effective_billing_mode": effective_mode,
        "contract_updates": contract_updates,
    }
