"""Wave 5 (contracts-time-mgmt) — bi-weekly invoice batch builder.

Groups approved billable time entries by contract and a 14-day
period into ``invoice_batches`` rows that finance can approve before
a customer invoice is generated.

Contract surface
----------------
The Wave 4 trigger evaluator emits an ``invoice_batch_eligible``
marker when a ``queue_invoice_batch`` action fires; Wave 5 consumes
that marker by writing actual rows. Two entry points:

  - Scheduler / admin sweep: call ``build_invoice_batches`` for a
    fixed period across every contract that has unbatched approved
    hours in that window.
  - Per-contract build: pass ``contract_id_filter`` so a manual
    rebuild only touches one contract.

Idempotency
-----------
Each (contract_id, period_start, period_end) tuple maps to at most
one batch in a non-rejected state. A second build call for the same
window returns the existing batch_id in ``skipped`` instead of
double-creating. The Wave 0 migration's
``(contract_id ascending, period_start descending)`` index keeps the
lookup cheap.

Rate math
---------
Line-item amounts use ``entry_effective_sell_rate(entry, contract)``
from Wave 3's rate helpers, so frozen sell_rate_snapshots win over
live contract rates. Pre-Wave-0 entries without snapshots fall back
to the live contract's resource_type sell_rate or hourly_rate.

Not included in Wave 5
----------------------
- Customer invoice send.
- QBO sync.
- Frontend.
- Bi-weekly schedule selection (caller passes period_start /
  period_end explicitly; the scheduler that picks the cadence lives
  outside this service).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.time_entry_rates import entry_effective_sell_rate


logger = logging.getLogger(__name__)


# An entry counts toward AR batching when it is both approved and
# billable. The Wave 3 billing_type derivation flips is_billable to
# False on non_billable entries, so the is_billable check is the
# canonical filter; billing_type is checked too so a future schema
# tweak that decouples them does not silently re-include
# non_billable hours.
#
# Wave 5 hardening: accept every status value that the repo treats
# as "approved enough to bill". The Wave 1 approve route writes the
# bare literal "approved"; the pre-Wave-1 two-stage approval flow
# wrote pm_approved / admin_approved. Both forms count.
_BATCHABLE_STATUSES = frozenset({"approved", "pm_approved", "admin_approved"})


def _is_batchable(entry: Dict[str, Any]) -> bool:
    if entry.get("status") not in _BATCHABLE_STATUSES:
        return False
    if entry.get("is_billable") is False:
        return False
    billing_type = entry.get("billing_type")
    if billing_type and billing_type != "billable":
        return False
    return True


# Wave 5 hardening: contract types whose hours are billed by separate
# rule from the entry's raw hours.
#
#   boh: prepaid block. Customer paid the full block up front; the
#        builder must NOT re-invoice block hours every cycle.
#   boh_with_boa: prepaid block PLUS overage. Block hours are prepaid;
#                  only the per-entry overage_hours portion (from the
#                  contract_time_applications ledger) is invoiceable.
#   boa: pure bill-on-actuals. Every approved billable hour is
#        invoiceable.
#   fixed_fee / monthly / other: hours are not the billing basis;
#        the builder skips with a documented reason.
_BOH_INVOICEABLE_KINDS = frozenset({"boh"})  # skip entire entry
_BOH_BOA_KINDS = frozenset({"boh_with_boa"})  # invoice ledger overage_hours only
_BOA_KINDS = frozenset({"boa"})  # invoice full entry.hours
_TIME_BILLABLE_KINDS = _BOH_INVOICEABLE_KINDS | _BOH_BOA_KINDS | _BOA_KINDS


def compute_batch_key(contract_id: str, period_start: str, period_end: str) -> str:
    """Deterministic dedup key. Format is stable so a sparse unique
    index on invoice_batches.batch_key can enforce one
    non-rejected batch per (contract, period) at the DB layer.
    Rejected batches carry the same key — the unique constraint
    lives on the active row; rejected rows can be present without
    blocking a rebuild because the builder clears the key on
    rejection (handled in the route's reject path)."""
    return f"{contract_id}:{period_start}:{period_end}"


def gen_batch_id() -> str:
    return f"ibatch_{uuid.uuid4().hex[:12]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def biweekly_period_for(reference: date, *, anchor: Optional[date] = None) -> Tuple[str, str]:
    """Return the (period_start, period_end) ISO strings for the
    14-day window containing ``reference``. ``anchor`` is the
    Monday on which the bi-weekly cadence starts; default is the
    Monday on or before reference. Both bounds are inclusive.

    The scheduler in a later wave will pass an explicit anchor so
    the bi-weekly windows do not drift; this helper is the unit
    test surface for the period math."""
    if anchor is None:
        anchor = reference - timedelta(days=reference.weekday())
    delta = (reference - anchor).days
    cycles = delta // 14
    start = anchor + timedelta(days=14 * cycles)
    end = start + timedelta(days=13)
    return (start.isoformat(), end.isoformat())


def _invoiceable_hours_for(
    entry: Dict[str, Any],
    contract: Dict[str, Any],
    application: Optional[Dict[str, Any]],
) -> float:
    """Return the hours that should appear on the customer invoice
    for this entry on this contract. Wave 5 hardening rule set:

      - boh_with_boa: use the Wave 3 ledger row's overage_hours
        (the boundary-crossing math has already been done by the
        contract_debit service). Falls back to max(entry.hours -
        application.block_hours, 0) if overage_hours is missing
        from the row. If no ledger row exists, conservative default
        is 0 (do not invoice an entry we cannot trace).
      - boa: every approved billable hour is invoiceable.
      - boh: caller filters before reaching this function; defensive
        return of 0 here.
      - anything else: 0 (caller already skipped).
    """
    ctype = (contract.get("contract_type") or "").strip()
    if ctype in _BOA_KINDS:
        return _coerce_float(entry.get("hours"))
    if ctype in _BOH_BOA_KINDS:
        if application is None:
            return 0.0
        overage = _coerce_float(application.get("overage_hours"))
        if overage > 0:
            return overage
        # Fallback: if overage_hours wasn't recorded, derive from
        # block_hours so a partially-broken ledger row still produces
        # the correct invoiceable portion.
        entry_hours = _coerce_float(entry.get("hours"))
        block = _coerce_float(application.get("block_hours"))
        return max(entry_hours - block, 0.0)
    return 0.0


def _line_item_for_entry(
    entry: Dict[str, Any],
    contract: Dict[str, Any],
    *,
    invoiceable_hours: Optional[float] = None,
) -> Dict[str, Any]:
    """One AR line item per invoiceable entry. The sell rate prefers
    the entry's frozen snapshot over the live contract rate so a
    post-approval rate edit does not retroactively rewrite the batch
    total.

    ``invoiceable_hours`` is the BOH-with-BOA boundary math result
    from ``_invoiceable_hours_for``. When omitted (e.g. BOA contract
    or test fixtures that pre-date Wave 5 hardening), it falls back
    to ``entry.hours`` so existing call sites still work."""
    sell = entry_effective_sell_rate(entry, contract) or 0.0
    raw_hours = _coerce_float(entry.get("hours"))
    hours = float(invoiceable_hours) if invoiceable_hours is not None else raw_hours
    amount = round(hours * float(sell), 2)
    desc_parts: List[str] = []
    if entry.get("user_name"):
        desc_parts.append(entry["user_name"])
    if entry.get("notes"):
        desc_parts.append(entry["notes"])
    if entry.get("date"):
        desc_parts.append(f"({entry['date']})")
    description = " ".join(desc_parts) or f"Time entry {entry.get('entry_id', '')}"
    return {
        "entry_id": entry.get("entry_id"),
        "user_id": entry.get("user_id"),
        "user_name": entry.get("user_name"),
        "project_id": entry.get("project_id"),
        "date": entry.get("date"),
        "hours": hours,
        "entry_hours": raw_hours,
        "sell_rate_snapshot": float(sell),
        "amount": amount,
        "description": description,
    }


async def build_invoice_batches(
    db,
    *,
    period_start: str,
    period_end: str,
    contract_id_filter: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    dry_run: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Build invoice batches for one bi-weekly period.

    Returns ``(batches, skipped, warnings)`` where:
      - batches  — every newly-written or dry-run-preview batch.
      - skipped  — per-contract reasons the builder did not produce
        a batch (already exists, no entries, contract missing).
      - warnings — soft issues that did not block batch creation
        (e.g. resolved entries with zero sell rate).

    Caller is responsible for the period_start / period_end window
    (the scheduler that picks the bi-weekly cadence is out of scope
    for Wave 5).
    """
    time_query: Dict[str, Any] = {
        # Wave 5 hardening: include every "approved" form the repo
        # has written historically. The bare literal "approved" is
        # what Wave 1 writes today; pm_approved / admin_approved are
        # the pre-Wave-1 two-stage values still present on some
        # legacy rows.
        "status": {"$in": list(_BATCHABLE_STATUSES)},
        "is_billable": True,
        "date": {"$gte": period_start, "$lte": period_end},
        "contract_id": {"$ne": None},
    }
    if contract_id_filter:
        time_query["contract_id"] = contract_id_filter

    # to_list(20000) is the intentional per-period scale ceiling for
    # a single bi-weekly build. The realistic upper bound is on the
    # order of a few hundred billable entries per fortnight across
    # every contract; 20k leaves two orders of magnitude of headroom
    # over the current production footprint. If a single period ever
    # actually exceeds 20k entries, the build silently truncates —
    # raise this ceiling alongside a paged-build strategy at that
    # point rather than removing the cap (the old un-capped to_list
    # ran the risk of pulling a multi-million-row scan into memory).
    entries: List[Dict[str, Any]] = await db.time_entries.find(
        time_query, {"_id": 0}
    ).to_list(20000)

    # The query already filters status / is_billable, but a defensive
    # _is_batchable pass guards against legacy rows whose
    # billing_type contradicts is_billable.
    entries = [e for e in entries if _is_batchable(e)]

    if not entries:
        return [], [{"reason": "no_billable_entries", "period_start": period_start, "period_end": period_end}], []

    by_contract: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        cid = entry.get("contract_id")
        if cid:
            by_contract.setdefault(cid, []).append(entry)

    batches_out: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    warnings: List[str] = []
    now = _utc_now()

    for cid, contract_entries in by_contract.items():
        # Dedup: any non-rejected batch for this (contract, period)
        # blocks a re-build. Rejected batches are excluded so a
        # rebuild after a rejection is legal.
        existing = await db.invoice_batches.find_one(
            {
                "contract_id": cid,
                "period_start": period_start,
                "period_end": period_end,
                "status": {"$ne": "rejected"},
            },
            {"_id": 0},
        )
        if existing:
            skipped.append({
                "reason": "batch_already_exists",
                "contract_id": cid,
                "existing_batch_id": existing.get("batch_id"),
                "status": existing.get("status"),
            })
            continue

        contract = await db.contracts.find_one({"contract_id": cid}, {"_id": 0})
        if not contract:
            skipped.append({"reason": "contract_not_found", "contract_id": cid})
            continue

        ctype = (contract.get("contract_type") or "").strip()
        if ctype not in _TIME_BILLABLE_KINDS:
            # Wave 5 hardening: fixed_fee / monthly / wholesale_mrr /
            # other types are NOT invoiced by hour. Skip the contract
            # for AR batching with a documented reason so the caller
            # can see exactly why this contract produced no batch.
            skipped.append({
                "reason": "contract_type_not_time_billable",
                "contract_id": cid,
                "contract_type": ctype or None,
            })
            continue

        if ctype in _BOH_INVOICEABLE_KINDS:
            # Wave 5 hardening: plain BOH is prepaid. Every entry's
            # hours were paid at contract signing. AR billing for the
            # cycle is zero. Surface this as a documented skip rather
            # than a silent no-op so finance can confirm the contract
            # is being treated correctly.
            skipped.append({
                "reason": "boh_prepaid_no_overage",
                "contract_id": cid,
                "candidate_entry_count": len(contract_entries),
            })
            continue

        # Wave 5 hardening: BOH-with-BOA boundary math. For each
        # entry, look up the Wave 3 ledger row (one per entry_id) and
        # use its overage_hours as the invoiceable quantity. Plain
        # entry.hours would include the prepaid block portion that the
        # customer already paid for.
        invoiceable_entry_ids = [e.get("entry_id") for e in contract_entries if e.get("entry_id")]
        applications: List[Dict[str, Any]] = []
        if invoiceable_entry_ids and ctype in _BOH_BOA_KINDS:
            applications = await db.contract_time_applications.find(
                {"entry_id": {"$in": invoiceable_entry_ids}, "contract_id": cid},
                {"_id": 0},
            ).to_list(len(invoiceable_entry_ids))
        applications_by_entry: Dict[str, Dict[str, Any]] = {
            a.get("entry_id"): a for a in applications if a.get("entry_id")
        }

        line_items: List[Dict[str, Any]] = []
        total_hours = 0.0
        total_amount = 0.0
        entry_ids: List[str] = []
        zero_rate_count = 0
        for entry in sorted(contract_entries, key=lambda e: e.get("date", "")):
            invoiceable_hours = _invoiceable_hours_for(
                entry, contract, applications_by_entry.get(entry.get("entry_id"))
            )
            if invoiceable_hours <= 0:
                # Pure block hours on a boh_with_boa contract are
                # prepaid; the entry contributed zero overage so it
                # is intentionally NOT a line item. Keep it out of
                # entry_ids too — the batch only lists entries it
                # actually billed for.
                continue
            line = _line_item_for_entry(entry, contract, invoiceable_hours=invoiceable_hours)
            line_items.append(line)
            total_hours += line["hours"]
            total_amount += line["amount"]
            if line["sell_rate_snapshot"] == 0:
                zero_rate_count += 1
            if entry.get("entry_id"):
                entry_ids.append(entry["entry_id"])

        if not line_items:
            skipped.append({
                "reason": "no_overage_hours_to_invoice"
                if ctype in _BOH_BOA_KINDS else "no_line_items_after_filter",
                "contract_id": cid,
            })
            continue

        if zero_rate_count:
            warnings.append(
                f"contract {cid} has {zero_rate_count} line item(s) with zero sell rate; "
                "review entries with missing sell_rate_snapshot before approving the batch"
            )

        batch_id = gen_batch_id()
        batch: Dict[str, Any] = {
            "batch_id": batch_id,
            # Wave 5 hardening: deterministic dedup key so the sparse
            # unique index added in migration 2026_invoice_batches_wave5_indexes
            # rejects a duplicate active batch for the same
            # (contract, period) at the DB layer. Rejected batches
            # have batch_key cleared so a rebuild can claim it.
            "batch_key": compute_batch_key(cid, period_start, period_end),
            "contract_id": cid,
            "account_id": contract.get("account_id"),
            "account_name": contract.get("account_name"),
            "contract_name": contract.get("name"),
            "contract_type": ctype,
            "period_start": period_start,
            "period_end": period_end,
            "status": "pending_finance_approval",
            "total_hours": round(total_hours, 4),
            "total_amount": round(total_amount, 2),
            "line_items": line_items,
            "entry_ids": entry_ids,
            "linked_finance_draft_id": None,
            "decided_by": None,
            "decided_at": None,
            "decision_notes": None,
            "created_by": actor_user_id,
            "created_at": now,
            "updated_at": now,
        }
        if contract.get("tenant_id"):
            batch["tenant_id"] = contract["tenant_id"]

        if not dry_run:
            await db.invoice_batches.insert_one(batch)
            batch.pop("_id", None)
        batches_out.append(batch)

    return batches_out, skipped, warnings
