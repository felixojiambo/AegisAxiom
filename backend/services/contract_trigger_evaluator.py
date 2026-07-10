"""Wave 4 (contracts-time-mgmt) — contract billing trigger evaluator.

Evaluates the ``contract_billing_triggers`` rows attached to one
contract against the contract's current balance / dates and updates
each trigger's state. Side-effect actions (notify_finance,
create_task, queue_invoice_batch) are deliberately conservative in
Wave 4:

  - ``notify_finance`` / ``create_task`` short-circuit with
    ``action_result="..._not_configured"`` when the underlying
    helper does not exist in this deployment. We do NOT invent
    fan-out behavior — Wave 5 will wire real action handlers once
    the notification / task subsystems are ready.
  - ``queue_invoice_batch`` deliberately does NOT create an
    invoice_batches row. Invoice batch building is owned by Wave 5
    so the evaluator only records eligibility via the
    ``invoice_batch_eligible`` marker on the fired result. Tests
    lock this rule.

Idempotency
-----------
One-shot triggers (date / pct_consumed / remaining_threshold /
block_exhausted) flip to ``state="fired"`` on the first match and
are skipped on subsequent evaluations. Only ``biweekly_cycle``
stays armed across firings — the evaluator updates ``last_fired_at``
but leaves the state alone so the next cycle can fire again.

Failure isolation
-----------------
Callers in the approval / adjustment flow invoke
``evaluate_contract_billing_triggers`` AFTER the financial mutation
is committed. The evaluator's failure must never roll back a
successful debit or adjustment, so route callers wrap this in
try/except and log at most a warning.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


VALID_TRIGGER_TYPES = frozenset({
    "date",
    "pct_consumed",
    "remaining_threshold",
    "block_exhausted",
    "biweekly_cycle",
})

VALID_TRIGGER_STATES = frozenset({"armed", "fired", "disabled"})

VALID_TRIGGER_ACTIONS = frozenset({
    "notify_finance",
    "create_task",
    "queue_invoice_batch",
})

# One-shot triggers move to state="fired" on the first match.
# biweekly_cycle is recurring; the evaluator records last_fired_at
# but leaves state="armed" so it can fire on the next cycle.
_ONESHOT_TRIGGER_TYPES = frozenset({
    "date",
    "pct_consumed",
    "remaining_threshold",
    "block_exhausted",
})


def gen_trigger_id() -> str:
    return f"trig_{uuid.uuid4().hex[:12]}"


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
    """Same fallback ladder the contract_debit service uses so
    pct_consumed / remaining_threshold are computed against the same
    denominator the debit math uses."""
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


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    """Accept ISO date or datetime strings and return a UTC-aware
    datetime. Bare YYYY-MM-DD is treated as midnight UTC of that day."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate_trigger_payload(payload: Dict[str, Any]) -> List[str]:
    """Return a list of validation errors for a trigger create
    payload. Empty list means the payload is acceptable. Callers
    raise 400 with the joined messages so the wire response is
    actionable."""
    errors: List[str] = []
    ttype = payload.get("trigger_type")
    if ttype not in VALID_TRIGGER_TYPES:
        errors.append(
            f"trigger_type must be one of: {', '.join(sorted(VALID_TRIGGER_TYPES))}"
        )
    action = payload.get("action")
    if action not in VALID_TRIGGER_ACTIONS:
        errors.append(
            f"action must be one of: {', '.join(sorted(VALID_TRIGGER_ACTIONS))}"
        )
    state = payload.get("state", "armed")
    if state not in {"armed", "disabled"}:
        # "fired" is set by the evaluator; not allowed on create.
        errors.append("state must be 'armed' or 'disabled' on create")
    threshold = payload.get("threshold")
    if ttype == "date":
        if _parse_iso_datetime(threshold) is None:
            errors.append("threshold must be an ISO date or datetime string for date triggers")
    elif ttype == "pct_consumed":
        try:
            pct = float(threshold)
        except (TypeError, ValueError):
            pct = None
        if pct is None or not (0 <= pct <= 100):
            errors.append("threshold must be a number from 0 to 100 for pct_consumed triggers")
    elif ttype == "remaining_threshold":
        try:
            rem = float(threshold)
        except (TypeError, ValueError):
            rem = None
        if rem is None or rem < 0:
            errors.append("threshold must be a non-negative number for remaining_threshold triggers")
    elif ttype == "block_exhausted":
        # threshold is ignored / optional for block_exhausted.
        pass
    elif ttype == "biweekly_cycle":
        # threshold optional; if supplied, must be an ISO date anchor.
        if threshold is not None and _parse_iso_datetime(threshold) is None:
            errors.append("threshold must be an ISO date string for biweekly_cycle triggers (optional)")
    return errors


# ---------------------------------------------------------------------------
# Per-trigger evaluators. Each returns either:
#   ("fire", reason_string)  -> trigger should fire now
#   ("skip", reason_string)  -> trigger does not match yet
# ---------------------------------------------------------------------------


def _evaluate_one(trigger: Dict[str, Any], contract: Dict[str, Any]) -> tuple[str, str]:
    ttype = trigger.get("trigger_type")
    threshold = trigger.get("threshold")
    if ttype == "date":
        dt = _parse_iso_datetime(threshold)
        if dt is None:
            return ("skip", "invalid_threshold")
        if _now() >= dt:
            return ("fire", f"date_due:{dt.isoformat()}")
        return ("skip", "date_not_due")
    if ttype == "pct_consumed":
        try:
            target = float(threshold)
        except (TypeError, ValueError):
            return ("skip", "invalid_threshold")
        total = _resolve_total_block_hours(contract)
        if total <= 0:
            return ("skip", "missing_total_hours")
        consumed = _coerce_float(contract.get("hours_consumed"))
        pct = (consumed / total) * 100.0 if total > 0 else 0.0
        if pct >= target:
            return ("fire", f"pct_consumed:{pct:.2f}>={target:.2f}")
        return ("skip", f"pct_consumed:{pct:.2f}<{target:.2f}")
    if ttype == "remaining_threshold":
        try:
            target = float(threshold)
        except (TypeError, ValueError):
            return ("skip", "invalid_threshold")
        total = _resolve_total_block_hours(contract)
        consumed = _coerce_float(contract.get("hours_consumed"))
        remaining = max(total - consumed, 0.0)
        if total <= 0:
            return ("skip", "missing_total_hours")
        if remaining <= target:
            return ("fire", f"remaining:{remaining:.2f}<={target:.2f}")
        return ("skip", f"remaining:{remaining:.2f}>{target:.2f}")
    if ttype == "block_exhausted":
        if contract.get("bill_and_actuals_active"):
            return ("fire", "bill_and_actuals_active")
        total = _resolve_total_block_hours(contract)
        consumed = _coerce_float(contract.get("hours_consumed"))
        if total > 0 and consumed >= total:
            return ("fire", f"hours_consumed:{consumed:.2f}>=total:{total:.2f}")
        return ("skip", "block_not_exhausted")
    if ttype == "biweekly_cycle":
        # Marker-only. The evaluator reports eligibility but does not
        # create invoice batches; Wave 5 owns batch building.
        return ("fire", "biweekly_cycle_eligible")
    return ("skip", "unknown_trigger_type")


# ---------------------------------------------------------------------------
# Action handlers — conservative by design. Real fan-out lands in Wave 5.
# ---------------------------------------------------------------------------


async def _run_action(
    db,
    *,
    trigger: Dict[str, Any],
    contract: Dict[str, Any],
    actor_user_id: Optional[str],
) -> str:
    """Run a trigger action. Returns a short result string for the
    fired record so audits show what (if anything) happened."""
    action = trigger.get("action")
    if action == "notify_finance":
        # Notification fan-out is owned by a service that this Wave 4
        # commit does NOT introduce. If a notify_users helper exists
        # on services/notifications.py in a later wave the evaluator
        # can be updated to call it; today we record the fired state
        # and surface the not-configured marker so operators can
        # tell why no email was sent.
        return "notification_not_configured"
    if action == "create_task":
        return "task_creation_not_configured"
    if action == "queue_invoice_batch":
        # Invoice batch building is Wave 5. The fired record carries
        # the eligibility marker; no invoice_batches row is written.
        return "invoice_batch_eligible"
    return "unknown_action"


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


async def evaluate_contract_billing_triggers(
    db,
    contract: Dict[str, Any],
    *,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate every armed trigger attached to this contract.

    Returns::

        {
            "contract_id": "...",
            "fired":   [<fired trigger summary>...],
            "skipped": [<skipped trigger summary>...],
        }

    Side effects:
      - One-shot triggers that fire flip to state="fired" and stamp
        last_fired_at / last_fire_reason.
      - biweekly_cycle triggers that fire stay state="armed"; only
        last_fired_at / last_fire_reason are updated.
      - No invoice_batches rows are written.
      - No customer emails are sent.
    """
    contract_id = contract.get("contract_id")
    if not contract_id:
        return {"contract_id": None, "fired": [], "skipped": []}

    cursor = db.contract_billing_triggers.find(
        {"contract_id": contract_id, "state": "armed"}, {"_id": 0}
    )
    armed_triggers: List[Dict[str, Any]] = await cursor.to_list(500)

    fired: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    now = _now()

    for trigger in armed_triggers:
        decision, reason = _evaluate_one(trigger, contract)
        if decision == "fire":
            action_result = await _run_action(
                db,
                trigger=trigger,
                contract=contract,
                actor_user_id=actor_user_id,
            )
            ttype = trigger.get("trigger_type")
            next_state = "fired" if ttype in _ONESHOT_TRIGGER_TYPES else "armed"
            set_doc: Dict[str, Any] = {
                "state": next_state,
                "last_fired_at": now,
                "last_fire_reason": reason,
                "last_action_result": action_result,
                "updated_at": now,
            }
            await db.contract_billing_triggers.update_one(
                {"trigger_id": trigger.get("trigger_id")},
                {"$set": set_doc},
            )
            fired.append({
                "trigger_id": trigger.get("trigger_id"),
                "trigger_type": ttype,
                "action": trigger.get("action"),
                "reason": reason,
                "action_result": action_result,
                "state": next_state,
            })
        else:
            skipped.append({
                "trigger_id": trigger.get("trigger_id"),
                "trigger_type": trigger.get("trigger_type"),
                "reason": reason,
            })

    return {"contract_id": contract_id, "fired": fired, "skipped": skipped}
