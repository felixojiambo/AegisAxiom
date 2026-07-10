"""Wave 3 (contracts-time-mgmt) — shared rate snapshot resolver.

Given a contract document, a time entry payload, the entry user_id and
optional resource_type_id, returns the rates that should be frozen on
the entry at create-time. Snapshotting means the entry remembers the
rate that was in effect when the work happened so that a later contract
rate change does not retroactively repair (or break) the financial
totals on already-approved time.

Resolution order — matches the canonical sources used by
``services/operations/contract_financials.calculate_ar_summary``:

  sell_rate_snapshot:
    1. contract.resource_types[]: match by ``id == resource_type_id``,
       read ``sell_rate``.
    2. fall back to contract.hourly_rate when no resource_type match
       exists (legacy BOH contracts and contracts without
       resource_types yet).

  cost_rate_snapshot:
    1. contract.resources[]: match by user_id AND
       resource_type_id when both are present.
    2. contract.resources[]: match by user_id only when no
       resource_type_id is supplied.
    3. None when neither match nor fallback resolves — the entry still
       saves, but the snapshot is absent. Callers that require a
       resolved cost rate (Wave 3 debit on a billable entry) raise
       409 explicitly; non_billable entries tolerate the absence.

Client-supplied snapshot values are intentionally ignored. The route
layer must pass the original payload to ``resolve_rate_snapshots`` and
overwrite whatever the client sent — otherwise an attacker (or an
overzealous frontend) could pin the entry to a rate that contradicts
the contract.

See ``backend/tests/test_time_entry_billing_snapshots.py`` for the
locked behavioral contract.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _coerce_float(value: Any) -> Optional[float]:
    """Return ``value`` as a non-negative float, or None when the
    value is missing, blank, or invalid. The negative-zero check
    prevents oddly-shaped legacy seed rows from producing a debit
    against a negative rate."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f < 0:
        return None
    return f


def _find_resource_type(
    contract: Dict[str, Any], resource_type_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Return the resource_type sub-document matching ``resource_type_id``
    on the contract, or None when no id is supplied / no match exists."""
    if not resource_type_id:
        return None
    for rt in contract.get("resource_types") or []:
        if isinstance(rt, dict) and rt.get("id") == resource_type_id:
            return rt
    return None


def _find_resource(
    contract: Dict[str, Any],
    user_id: Optional[str],
    resource_type_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Find the resource entry on the contract for this user. Prefer
    a (user_id, resource_type_id) match when both are present; fall
    back to a user_id-only match so legacy single-resource-type
    contracts keep working."""
    if not user_id:
        return None
    resources = contract.get("resources") or []
    if resource_type_id:
        for r in resources:
            if (
                isinstance(r, dict)
                and r.get("user_id") == user_id
                and r.get("resource_type_id") == resource_type_id
            ):
                return r
    for r in resources:
        if isinstance(r, dict) and r.get("user_id") == user_id:
            return r
    return None


def resolve_rate_snapshots(
    contract: Optional[Dict[str, Any]],
    *,
    user_id: Optional[str],
    resource_type_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the rate snapshot dict for the given contract / user /
    resource type triple.

    Output shape::

        {
            "cost_rate_snapshot": float | None,
            "sell_rate_snapshot": float | None,
            "rate_source": {
                "cost": "contract.resources" | "missing",
                "sell": "contract.resource_types" | "contract.hourly_rate" | "missing",
            },
        }

    The ``rate_source`` map is internal-only — routes log it for
    diagnostics but do not return it to clients. None values mean the
    rate could not be resolved from the contract; the caller decides
    whether that is recoverable based on the entry's billing_type.
    """
    out: Dict[str, Any] = {
        "cost_rate_snapshot": None,
        "sell_rate_snapshot": None,
        "rate_source": {"cost": "missing", "sell": "missing"},
    }
    if not contract:
        return out

    rt = _find_resource_type(contract, resource_type_id)
    if rt is not None:
        rate = _coerce_float(rt.get("sell_rate"))
        if rate is not None:
            out["sell_rate_snapshot"] = rate
            out["rate_source"]["sell"] = "contract.resource_types"
    if out["sell_rate_snapshot"] is None:
        fallback = _coerce_float(contract.get("hourly_rate"))
        if fallback is not None:
            out["sell_rate_snapshot"] = fallback
            out["rate_source"]["sell"] = "contract.hourly_rate"

    resource = _find_resource(contract, user_id, resource_type_id)
    if resource is not None:
        cost = _coerce_float(resource.get("cost_rate"))
        if cost is not None:
            out["cost_rate_snapshot"] = cost
            out["rate_source"]["cost"] = "contract.resources"

    return out


# ---------------------------------------------------------------------------
# Convenience helpers consumed by the route layer and weekly_summary.
# ---------------------------------------------------------------------------


def entry_effective_cost_rate(
    entry: Dict[str, Any], contract: Optional[Dict[str, Any]] = None
) -> Optional[float]:
    """Return the cost rate to use when summarizing this entry for AP.
    Prefer the snapshot frozen on the entry; fall back to the contract
    today for legacy rows that pre-date the snapshot fields. Both
    sources are best-effort — finance summaries already tolerate a
    missing rate by treating it as 0."""
    snap = _coerce_float(entry.get("cost_rate_snapshot"))
    if snap is not None:
        return snap
    # Backward compat — cost_rate lived directly on the entry pre-Wave 0.
    direct = _coerce_float(entry.get("cost_rate"))
    if direct is not None:
        return direct
    if contract is None:
        return None
    resource = _find_resource(contract, entry.get("user_id"), entry.get("resource_type_id"))
    if resource is not None:
        return _coerce_float(resource.get("cost_rate"))
    return None


def entry_effective_sell_rate(
    entry: Dict[str, Any], contract: Optional[Dict[str, Any]] = None
) -> Optional[float]:
    """Return the sell rate to use when summarizing this entry for AR.
    Prefer the snapshot; fall back to contract resource_type or
    contract.hourly_rate for legacy rows."""
    snap = _coerce_float(entry.get("sell_rate_snapshot"))
    if snap is not None:
        return snap
    direct = _coerce_float(entry.get("sell_rate"))
    if direct is not None:
        return direct
    if contract is None:
        return None
    rt = _find_resource_type(contract, entry.get("resource_type_id"))
    if rt is not None:
        rate = _coerce_float(rt.get("sell_rate"))
        if rate is not None:
            return rate
    return _coerce_float(contract.get("hourly_rate"))
