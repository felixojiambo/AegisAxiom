"""Read-only hydration plan for imported Zoho contracts and projects.

Eighth slice (corrected) of the Zoho historical migration. Post-import
verification surfaced that the AEGISAXIOM time-entry workflow has several
distinct gates on a contract before time can flow against it, and the
earlier version of this script collapsed them all into a single
``ready_for_time_entries`` flag. That hid two real signals: (1) that
``fixed_fee`` contracts bypass the create-time 409 sell-rate gate
entirely, and (2) that some readiness dimensions (resource-type
validation, approval/debit math) are independent of others.

This rewrite classifies each imported contract along six dimensions:

  billable_draft_entry_ready
    Would a *default* POST /api/time-entries (``is_billable=true``,
    no explicit ``billing_type``) save? The 409 gate at
    ``routes/time_entries.py`` line 407 only fires for
    ``billing_type == "billable"``. The route at line 367 maps
    ``contract_type == "fixed_fee"`` to ``billing_type = "fixed_fee"``
    before that check, so fixed_fee contracts pass without a sell rate.
    For every other contract type, a positive ``hourly_rate`` or a
    positive ``resource_types[].sell_rate`` is required.

  non_billable_draft_entry_ready
    Would a POST with ``is_billable=false`` (or
    ``billing_type="non_billable"``) save? The 409 gate is bypassed
    entirely; today the create endpoint imposes no further
    contract-shape requirement on non-billable entries, so this is
    True for every imported contract that resolves.

  resource_type_entry_ready
    Would a POST that supplies ``resource_type_id`` pass the
    membership gate at ``routes/time_entries.py`` lines 325-337?
    Requires at least one entry in ``contract.resources[]`` so that
    a future user/role pairing can match. Empty resources means no
    user can submit a resource-type-validated entry today.

  consumed_hours_baseline_ready
    boh / boh_with_boa only. The importer preserves the Zoho
    consumption snapshot under ``imported_consumed_hours`` but leaves
    the canonical ``hours_consumed`` counter at zero. Newly approved
    entries against an unbaselined contract would push
    ``hours_consumed`` from zero, under-counting the historical block
    consumption. True when EITHER ``imported_consumed_hours`` is
    zero / missing (no historical consumption to baseline) OR
    canonical ``hours_consumed`` is already positive (someone — or a
    Timesheets replay pass — has already taken the snapshot
    decision).

  approval_debit_ready
    Would the contract balance updates inside
    ``services/contract_debit.apply_time_entry_to_contract`` be
    defensible? Depends on the contract type:

      fixed_fee / non_billable -> never touches contract balances
                                  -> always True.
      boa                      -> only increments
                                  ``total_hours_logged`` when that
                                  field exists, never debits a block
                                  -> True (the safety hinges on AR,
                                  which the create-time sell-rate
                                  gate already handles).
      boh / boh_with_boa       -> ``contract.hours_consumed`` is
                                  incremented unconditionally on
                                  approval. Requires BOTH a positive
                                  ``total_hours`` (block size) AND a
                                  consumed-hours baseline so the
                                  counter has a meaningful
                                  denominator AND starts from the
                                  right historical value.
      other / unknown          -> writes a ledger row, no contract
                                  balance moves -> True.

  hydration_required
    A bool aggregating "this contract needs at least one operator/
    Finance step before it is fully usable in AEGISAXIOM." True when any
    of the five readiness flags above is False AND that dimension is
    relevant for the contract type.

The plan also reports the **project impact**: every imported
``import_source=zoho`` project carries a ``contract_id`` pointing at
an imported contract. Each project inherits its parent contract's
readiness flags, so an operator looking at the project list can tell
which projects can accept time today.

What this script will NOT do:

  * Write to Mongo. There is no ``--execute`` flag and a pinning test
    fails the day anyone tries to add one.
  * Bulk-promote contracts or projects.
  * Auto-derive a sell rate. The raw Zoho export has no rate column,
    so ``hourly_rate`` always lands in ``needs_finance_review``.
  * Auto-copy ``imported_total_hours`` into ``total_hours`` /
    ``total_hours_purchased`` silently, or auto-copy
    ``imported_consumed_hours`` into ``hours_consumed`` silently.
    Both are offered under ``operator_candidate_patch`` and the doc
    explicitly flags them as requiring Finance / operator sign-off.
    For consumed hours specifically, the *preferred* path may be
    replaying historical Zoho Timesheets / time logs against the
    imported project once that export lands — that writes real
    ``contract_time_applications`` rows and is auditable. Directly
    patching ``hours_consumed`` is the candidate fallback.

Run::

    # Live Mongo (read-only):
    python backend/scripts/zoho_contracts_hydration_plan.py \\
        --out backend/imports/zoho/reports/contracts_hydration_plan.json

    # CI / offline: consume JSON manifests instead of Mongo.
    python backend/scripts/zoho_contracts_hydration_plan.py \\
        --contracts-manifest path/to/contracts.json \\
        --projects-manifest path/to/projects.json \\
        --out path/to/plan.json

The companion mapping doc is
``docs/zoho-migration/contracts_field_mapping.md``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)


logger = logging.getLogger("zoho_contracts_hydration_plan")


# ---------------------------------------------------------------------------
# Gap classifiers
# ---------------------------------------------------------------------------


# Contract types that run the BOH-style block debit on approval. For
# these, ``total_hours`` (or its equivalents) must be positive or the
# contract balance counter is left meaningless.
_BLOCK_DEBIT_TYPES: frozenset[str] = frozenset({"boh", "boh_with_boa"})

# Contract types where the create-time 409 sell-rate gate is bypassed
# by the route's billing_type derivation (see
# ``routes/time_entries.py`` line 367). For these, a billable default
# POST succeeds even without a sell rate.
_BILLING_TYPE_SHORTCIRCUIT_TYPES: frozenset[str] = frozenset({"fixed_fee"})


def _coerce_positive_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    return f


def _has_sell_rate(contract: dict[str, Any]) -> bool:
    """Mirror ``services/time_entry_rates.resolve_rate_snapshots``:
    a contract has a usable sell rate when either ``hourly_rate`` is
    a positive number, OR any ``resource_types[]`` element carries
    a positive ``sell_rate``."""
    if _coerce_positive_float(contract.get("hourly_rate")) is not None:
        return True
    for rt in contract.get("resource_types") or []:
        if not isinstance(rt, dict):
            continue
        if _coerce_positive_float(rt.get("sell_rate")) is not None:
            return True
    return False


def _has_total_hours(contract: dict[str, Any]) -> bool:
    """Mirror ``services/contract_debit._contract_total_block_hours``:
    ``total_hours`` first, then ``total_hours_purchased``, then the
    sum of ``resource_types[].hours_purchased``."""
    for key in ("total_hours", "total_hours_purchased"):
        if _coerce_positive_float(contract.get(key)) is not None:
            return True
    for rt in contract.get("resource_types") or []:
        if not isinstance(rt, dict):
            continue
        if _coerce_positive_float(rt.get("hours_purchased")) is not None:
            return True
    return False


def _has_resources(contract: dict[str, Any]) -> bool:
    """``contract.resources`` is the list of (user_id, resource_type_id,
    cost_rate) assignments. Empty means the time-entry route's
    resource-type validation gate (lines 325-337) cannot pass for any
    user, and the cost-rate snapshot resolver returns None."""
    res = contract.get("resources")
    return bool(res) and isinstance(res, list) and any(isinstance(r, dict) for r in res)


def _consumed_baseline_ok(contract: dict[str, Any]) -> bool:
    """A boh / boh_with_boa contract's debit math is only defensible
    once the canonical ``hours_consumed`` counter reflects the
    historical consumption snapshot from Zoho.

    Returns True when EITHER:
      * ``imported_consumed_hours`` is missing / zero / non-positive
        (no historical consumption to baseline), OR
      * canonical ``hours_consumed`` is already a positive number
        (someone — or a Timesheets replay pass — has already taken
        the snapshot decision).

    Returns False when the contract carries a positive
    ``imported_consumed_hours`` but the canonical counter is still
    None / 0. In that state, every approved entry would under-count
    consumption relative to the historical block.
    """
    imported = _coerce_positive_float(contract.get("imported_consumed_hours"))
    if imported is None:
        return True
    canonical = _coerce_positive_float(contract.get("hours_consumed"))
    return canonical is not None


def _classify_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Classify one imported contract along the six readiness
    dimensions described in the module docstring. Pure function — no
    DB access, no I/O."""
    ctype = (contract.get("contract_type") or "").strip()
    has_sell = _has_sell_rate(contract)
    has_total = _has_total_hours(contract)
    has_res = _has_resources(contract)
    is_block_debit = ctype in _BLOCK_DEBIT_TYPES
    baseline_ok = _consumed_baseline_ok(contract) if is_block_debit else True

    # ---- Dimension 1: billable default POST.
    if ctype in _BILLING_TYPE_SHORTCIRCUIT_TYPES:
        # routes/time_entries.py:367 sets billing_type='fixed_fee'
        # before the 409 gate, so the create endpoint never raises
        # on the rate even when one is absent.
        billable_draft_entry_ready = True
    else:
        billable_draft_entry_ready = has_sell

    # ---- Dimension 2: explicit non-billable POST.
    # The 409 gate only fires for billing_type=='billable', and the
    # create endpoint imposes no other contract-shape requirement on
    # non-billable entries. An imported contract that exists at all
    # can therefore accept a non-billable draft today.
    non_billable_draft_entry_ready = True

    # ---- Dimension 3: resource-type-validated POST.
    # Needs at least one resources[] entry; otherwise the membership
    # check at lines 325-337 rejects every (user, resource_type_id)
    # pair.
    resource_type_entry_ready = has_res

    # ---- Dimension 4: consumed-hours baseline (boh / boh_with_boa
    # only). Even when total_hours is set, an unbaselined canonical
    # hours_consumed under-counts historical consumption — newly
    # approved entries would push the counter from zero instead of
    # from the Zoho snapshot.
    consumed_hours_baseline_ready = baseline_ok

    # ---- Dimension 5: approval debit. For block-debiting contract
    # types it now requires BOTH total_hours AND a consumed-hours
    # baseline; for everyone else (fixed_fee, boa, other, unknown)
    # the contract balance either does not move or only moves when
    # an explicit logging field exists, so an unhydrated import is
    # debit-safe by default.
    if is_block_debit:
        approval_debit_ready = has_total and baseline_ok
    else:
        approval_debit_ready = True

    # ---- Gaps + operator candidate patch.
    gaps: list[str] = []
    operator_patch: dict[str, Any] = {}
    needs_review: list[str] = []

    if not billable_draft_entry_ready and not has_sell:
        gaps.append("needs_sell_rate_for_billable_draft")
        needs_review.append("hourly_rate")
    if is_block_debit and not has_total:
        gaps.append("needs_total_hours_for_debit")
        # imported_total_hours is offered as a CANDIDATE only — the
        # Zoho snapshot can disagree with the contract's actual block
        # size, so the operator/Finance must confirm before applying.
        imported_total = _coerce_positive_float(contract.get("imported_total_hours"))
        if imported_total is not None:
            operator_patch["total_hours"] = imported_total
            operator_patch["total_hours_purchased"] = imported_total
            needs_review.append("total_hours (verify against contract record)")
        else:
            needs_review.append("total_hours")
    if is_block_debit and not baseline_ok:
        gaps.append("needs_consumed_hours_baseline_for_debit")
        # imported_consumed_hours is the Zoho snapshot. We surface it
        # only as a candidate because the *preferred* path may be
        # replaying historical Zoho Timesheets/time logs rather than
        # patching hours_consumed directly — that decision belongs to
        # Finance / the contract operator.
        imported_consumed = _coerce_positive_float(contract.get("imported_consumed_hours"))
        if imported_consumed is not None:
            operator_patch["hours_consumed"] = imported_consumed
            needs_review.append(
                "hours_consumed (verify against historical time replay / Zoho snapshot)"
            )
        else:
            needs_review.append("hours_consumed")
    if not resource_type_entry_ready:
        gaps.append("needs_resources_for_resource_type_entry")
        needs_review.append("resources")

    hydration_required = bool(gaps)

    return {
        "contract_id": contract.get("contract_id"),
        "contract_number": contract.get("contract_number"),
        "external_contract_id": contract.get("external_contract_id"),
        "contract_type": ctype or None,
        "status": contract.get("status"),
        "readiness": {
            "billable_draft_entry_ready": billable_draft_entry_ready,
            "non_billable_draft_entry_ready": non_billable_draft_entry_ready,
            "resource_type_entry_ready": resource_type_entry_ready,
            "consumed_hours_baseline_ready": consumed_hours_baseline_ready,
            "approval_debit_ready": approval_debit_ready,
            "hydration_required": hydration_required,
        },
        "gaps": gaps,
        "imported_total_hours": contract.get("imported_total_hours"),
        "imported_consumed_hours": contract.get("imported_consumed_hours"),
        "canonical_hours_consumed": contract.get("hours_consumed"),
        "operator_candidate_patch": operator_patch,
        "needs_finance_review": sorted(set(needs_review)),
    }


def _classify_project(
    project: dict[str, Any],
    contract_readiness_by_id: dict[str, dict[str, Any]],
    contract_gaps_by_id: dict[str, list[str]],
) -> dict[str, Any]:
    """A project inherits its parent contract's readiness flags.

    Three parent-resolution outcomes:
      * ``"resolved"``    contract_id present and matches an imported
                          contract.
      * ``"missing"``     contract_id is empty / null.
      * ``"unresolved"``  contract_id present but no imported contract
                          matches.

    Missing and unresolved both drop every readiness flag to False so
    the project is not counted as safe for any workflow. The two
    cases are reported separately so an operator can tell whether
    the upstream Zoho data was incomplete (``missing``) or the parent
    import was skipped / blocked (``unresolved``).
    """
    contract_id = project.get("contract_id") or None
    if not contract_id:
        parent_resolution = "missing"
        parent_readiness = None
        parent_gaps: list[str] = []
    elif contract_id in contract_readiness_by_id:
        parent_resolution = "resolved"
        parent_readiness = contract_readiness_by_id[contract_id]
        parent_gaps = list(contract_gaps_by_id.get(contract_id, []))
    else:
        parent_resolution = "unresolved"
        parent_readiness = None
        parent_gaps = []

    if parent_readiness is None:
        readiness = {
            "billable_draft_entry_ready": False,
            "non_billable_draft_entry_ready": False,
            "resource_type_entry_ready": False,
            "consumed_hours_baseline_ready": False,
            "approval_debit_ready": False,
            "hydration_required": True,
        }
        gaps = (
            ["missing_contract_id"] if parent_resolution == "missing"
            else ["unresolved_parent_contract"]
        )
    else:
        readiness = dict(parent_readiness)
        gaps = []

    return {
        "project_id": project.get("project_id"),
        "external_project_id": project.get("external_project_id"),
        "name": project.get("name"),
        "status": project.get("status"),
        "contract_id": contract_id,
        "parent_resolution": parent_resolution,
        "inherited_parent_gaps": parent_gaps,
        "readiness": readiness,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------


def _load_manifest(path: str, kind: str, envelope_key: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        raise RuntimeError(f"{kind} manifest not found: {path!r}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"{kind} manifest {path!r} is not valid JSON: {e}",
            ) from e
    if isinstance(data, dict) and envelope_key in data:
        data = data[envelope_key]
    if not isinstance(data, list):
        raise RuntimeError(
            f"{kind} manifest {path!r} must be a list of {kind} objects.",
        )
    return [d for d in data if isinstance(d, dict)]


def _load_contracts_manifest(path: str) -> list[dict[str, Any]]:
    return _load_manifest(path, kind="contracts", envelope_key="contracts")


def _load_projects_manifest(path: str) -> list[dict[str, Any]]:
    return _load_manifest(path, kind="projects", envelope_key="projects")


async def _load_from_db() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Read-only fetch of imported contracts + projects from Mongo.
    Returns ``([], [], "no_credentials")`` when MONGO_URL / DB_NAME
    are absent so a dry-run with no credentials degrades to "no input"
    rather than crashing."""
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        return [], [], "no_credentials"
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        contracts = await db.contracts.find(
            {"import_source": "zoho"}, {"_id": 0},
        ).to_list(length=None)
        projects = await db.projects.find(
            {"import_source": "zoho"}, {"_id": 0},
        ).to_list(length=None)
        return contracts, projects, "mongo"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


def _summarize_contracts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: Counter[str] = Counter()
    billable_ready = 0
    non_billable_ready = 0
    resource_type_ready = 0
    consumed_baseline_ready = 0
    approval_debit_ready = 0
    hydration_required = 0
    needs_sell_rate = 0
    needs_total_hours = 0
    needs_consumed_baseline = 0
    needs_resources = 0
    for r in rows:
        ct = r["contract_type"] or "<unknown>"
        by_type[ct] += 1
        rd = r["readiness"]
        if rd["billable_draft_entry_ready"]:
            billable_ready += 1
        if rd["non_billable_draft_entry_ready"]:
            non_billable_ready += 1
        if rd["resource_type_entry_ready"]:
            resource_type_ready += 1
        if rd["consumed_hours_baseline_ready"]:
            consumed_baseline_ready += 1
        if rd["approval_debit_ready"]:
            approval_debit_ready += 1
        if rd["hydration_required"]:
            hydration_required += 1
        gaps = set(r["gaps"])
        if "needs_sell_rate_for_billable_draft" in gaps:
            needs_sell_rate += 1
        if "needs_total_hours_for_debit" in gaps:
            needs_total_hours += 1
        if "needs_consumed_hours_baseline_for_debit" in gaps:
            needs_consumed_baseline += 1
        if "needs_resources_for_resource_type_entry" in gaps:
            needs_resources += 1
    return {
        "total_imported": len(rows),
        "by_contract_type": dict(by_type),
        "billable_draft_entry_ready": billable_ready,
        "non_billable_draft_entry_ready": non_billable_ready,
        "resource_type_entry_ready": resource_type_ready,
        "consumed_hours_baseline_ready": consumed_baseline_ready,
        "approval_debit_ready": approval_debit_ready,
        "hydration_required": hydration_required,
        "needs_sell_rate_for_billable_draft": needs_sell_rate,
        "needs_total_hours_for_debit": needs_total_hours,
        "needs_consumed_hours_baseline_for_debit": needs_consumed_baseline,
        "needs_resources_for_resource_type_entry": needs_resources,
    }


def _summarize_projects(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_contract_id = 0
    linked_to_imported = 0
    missing_contract_id = 0
    unresolved_parent = 0
    billable_ready = 0
    non_billable_ready = 0
    resource_type_ready = 0
    consumed_baseline_ready = 0
    approval_debit_ready = 0
    parent_needs_sell_rate = 0
    parent_needs_total_hours = 0
    parent_needs_consumed_baseline = 0
    parent_needs_resources = 0
    for r in rows:
        # Parent-resolution counters use the explicit ``parent_resolution``
        # field set by the project classifier so the rules cannot drift
        # between "has a string in contract_id" and "actually links to
        # an imported contract."
        if r["contract_id"]:
            with_contract_id += 1
        pr = r.get("parent_resolution")
        if pr == "resolved":
            linked_to_imported += 1
        elif pr == "missing":
            missing_contract_id += 1
        elif pr == "unresolved":
            unresolved_parent += 1

        rd = r["readiness"]
        if rd["billable_draft_entry_ready"]:
            billable_ready += 1
        if rd["non_billable_draft_entry_ready"]:
            non_billable_ready += 1
        if rd["resource_type_entry_ready"]:
            resource_type_ready += 1
        if rd["consumed_hours_baseline_ready"]:
            consumed_baseline_ready += 1
        if rd["approval_debit_ready"]:
            approval_debit_ready += 1

        # Inverse roll-ups must only consider resolved projects so an
        # orphan never gets counted as "linked to a contract missing
        # X." The classifier emits inherited_parent_gaps for resolved
        # rows only; we read that authoritatively here.
        parent_gaps = set(r.get("inherited_parent_gaps") or [])
        if pr == "resolved":
            if "needs_sell_rate_for_billable_draft" in parent_gaps:
                parent_needs_sell_rate += 1
            if "needs_total_hours_for_debit" in parent_gaps:
                parent_needs_total_hours += 1
            if "needs_consumed_hours_baseline_for_debit" in parent_gaps:
                parent_needs_consumed_baseline += 1
            if "needs_resources_for_resource_type_entry" in parent_gaps:
                parent_needs_resources += 1
    return {
        "total_imported_projects": len(rows),
        "projects_with_contract_id": with_contract_id,
        "projects_linked_to_imported_contracts": linked_to_imported,
        "projects_missing_contract_id": missing_contract_id,
        "projects_unresolved_parent_contract": unresolved_parent,
        "projects_linked_to_contracts_missing_sell_rate": parent_needs_sell_rate,
        "projects_linked_to_contracts_missing_total_hours": parent_needs_total_hours,
        "projects_linked_to_contracts_missing_consumed_hours_baseline": (
            parent_needs_consumed_baseline
        ),
        "projects_linked_to_contracts_missing_resources": parent_needs_resources,
        "projects_safe_for_billable_draft_entry": billable_ready,
        "projects_safe_for_non_billable_draft_entry": non_billable_ready,
        "projects_safe_for_resource_type_entry": resource_type_ready,
        "projects_safe_for_consumed_hours_baseline": consumed_baseline_ready,
        "projects_safe_for_approval_debit": approval_debit_ready,
    }


def build_hydration_plan(
    contracts: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    *,
    source_label: str,
) -> dict[str, Any]:
    classified_contracts = [_classify_contract(c) for c in contracts]
    contract_readiness_by_id: dict[str, dict[str, Any]] = {
        row["contract_id"]: row["readiness"]
        for row in classified_contracts
        if row.get("contract_id")
    }
    contract_gaps_by_id: dict[str, list[str]] = {
        row["contract_id"]: list(row.get("gaps") or [])
        for row in classified_contracts
        if row.get("contract_id")
    }
    classified_projects = [
        _classify_project(p, contract_readiness_by_id, contract_gaps_by_id)
        for p in projects
    ]
    return {
        "source": source_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_dimensions": {
            "billable_draft_entry_ready": (
                "Default POST /api/time-entries (is_billable=true) would save. "
                "fixed_fee bypasses the 409 sell-rate gate; all others need a "
                "positive contract.hourly_rate or resource_types[].sell_rate."
            ),
            "non_billable_draft_entry_ready": (
                "POST with is_billable=false (or billing_type=non_billable) "
                "would save. True for every contract that resolves."
            ),
            "resource_type_entry_ready": (
                "POST that supplies resource_type_id would pass the membership "
                "check. Requires contract.resources[] to contain at least one "
                "(user_id, resource_type_id, cost_rate) triple."
            ),
            "consumed_hours_baseline_ready": (
                "boh / boh_with_boa only. True when imported_consumed_hours is "
                "zero/missing OR when canonical contract.hours_consumed is "
                "already positive. False when the Zoho snapshot says the "
                "contract has historical consumption but the canonical "
                "counter is still zero — newly approved entries would then "
                "under-count consumption relative to the historical block."
            ),
            "approval_debit_ready": (
                "Approval would produce defensible contract balance updates. "
                "fixed_fee / boa / other never debit a block; boh and "
                "boh_with_boa need BOTH total_hours AND a consumed-hours "
                "baseline so contract.hours_consumed has a meaningful "
                "denominator AND starts from the right historical value."
            ),
            "hydration_required": (
                "Aggregate flag. True when any of the five readiness "
                "dimensions is False AND the corresponding gap is relevant "
                "for the contract type."
            ),
        },
        "summary": _summarize_contracts(classified_contracts),
        "project_impact": _summarize_projects(classified_projects),
        "contracts": classified_contracts,
        "projects": classified_projects,
        "notes": [
            (
                "operator_candidate_patch is a CANDIDATE only — Finance / "
                "operator must verify imported_total_hours against the "
                "contract record before applying it. The Zoho snapshot can "
                "drift from the contract's stated block size."
            ),
            (
                "Setting total_hours alone is INSUFFICIENT for historically "
                "consumed boh / boh_with_boa contracts. Without a "
                "consumed-hours baseline, every newly approved entry would "
                "push contract.hours_consumed from zero, under-counting "
                "historical consumption."
            ),
            (
                "Preferred path for the consumed-hours baseline may be "
                "replaying historical Zoho Timesheets/time logs against the "
                "imported project once that export is available — that "
                "writes real contract_time_applications rows and is "
                "auditable. Directly patching contract.hours_consumed from "
                "imported_consumed_hours is the candidate fallback; it "
                "requires Finance / operator sign-off because the Zoho "
                "snapshot can drift from the ledger of approved entries."
            ),
            (
                "Sell rates are never auto-derived; the Zoho export has no "
                "rate column. needs_finance_review='hourly_rate' marks the "
                "contracts where Finance must supply a number."
            ),
            (
                "There is no --execute flag. The hydration executor is a "
                "separate, reviewed slice."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(plan: dict[str, Any]) -> str:
    s = plan["summary"]
    p = plan["project_impact"]
    lines: list[str] = []
    lines.append("Zoho Contracts Hydration Plan")
    lines.append("=" * 72)
    lines.append(f"Source:                  {plan['source']}")
    lines.append("")
    lines.append("CONTRACTS (read-only count)")
    lines.append(f"  total imported:                                {s['total_imported']}")
    lines.append("")
    lines.append("  --- create-time gates (draft entry creation) ---")
    lines.append(f"  billable draft entry ready (default POST):     {s['billable_draft_entry_ready']}")
    lines.append(f"  non-billable draft entry ready:                {s['non_billable_draft_entry_ready']}")
    lines.append("")
    lines.append("  --- resource_type membership gate ---")
    lines.append(f"  resource_type-validated entry ready:           {s['resource_type_entry_ready']}")
    lines.append("")
    lines.append("  --- approval / debit math correctness ---")
    lines.append(f"  consumed-hours baseline ready:                 {s['consumed_hours_baseline_ready']}")
    lines.append(f"  approval / debit math safe:                    {s['approval_debit_ready']}")
    lines.append("")
    lines.append(f"  hydration required (any dimension):            {s['hydration_required']}")
    lines.append("")
    lines.append("  gap counters:")
    lines.append("    needs_sell_rate_for_billable_draft:          "
                 f"{s['needs_sell_rate_for_billable_draft']}")
    lines.append("    needs_total_hours_for_debit:                 "
                 f"{s['needs_total_hours_for_debit']}")
    lines.append("    needs_consumed_hours_baseline_for_debit:     "
                 f"{s['needs_consumed_hours_baseline_for_debit']}")
    lines.append("    needs_resources_for_resource_type_entry:     "
                 f"{s['needs_resources_for_resource_type_entry']}")
    if s["by_contract_type"]:
        lines.append("")
        lines.append("  By contract_type:")
        for k, n in sorted(s["by_contract_type"].items()):
            lines.append(f"    - {k or '<unknown>':16s}: {n}")

    lines.append("")
    lines.append("PROJECTS (read-only count, inherited from parent contract)")
    lines.append(f"  total imported projects:                       {p['total_imported_projects']}")
    lines.append(f"  with any contract_id:                          {p['projects_with_contract_id']}")
    lines.append(f"  linked to imported contracts (resolved):       {p['projects_linked_to_imported_contracts']}")
    lines.append(f"  missing contract_id:                           {p['projects_missing_contract_id']}")
    lines.append(f"  unresolved parent contract:                    {p['projects_unresolved_parent_contract']}")
    lines.append("")
    lines.append(f"  safe for billable draft entry:                 {p['projects_safe_for_billable_draft_entry']}")
    lines.append(f"  safe for non-billable draft entry:             {p['projects_safe_for_non_billable_draft_entry']}")
    lines.append(f"  safe for resource_type-validated entry:        {p['projects_safe_for_resource_type_entry']}")
    lines.append(f"  safe for consumed-hours baseline:              {p['projects_safe_for_consumed_hours_baseline']}")
    lines.append(f"  safe for approval / debit math:                {p['projects_safe_for_approval_debit']}")
    lines.append("")
    lines.append("  inverse roll-ups (resolved projects only):")
    lines.append(f"    linked to contracts missing sell rate:       {p['projects_linked_to_contracts_missing_sell_rate']}")
    lines.append(f"    linked to contracts missing total_hours:     {p['projects_linked_to_contracts_missing_total_hours']}")
    lines.append("    linked to contracts missing consumed_hours")
    lines.append(f"      baseline:                                  {p['projects_linked_to_contracts_missing_consumed_hours_baseline']}")
    lines.append(f"    linked to contracts missing resources:       {p['projects_linked_to_contracts_missing_resources']}")

    lines.append("=" * 72)
    lines.append("Blocker categories (read alongside the counters above):")
    lines.append("  * Draft creation blockers     -> needs_sell_rate_for_billable_draft")
    lines.append("                                  (non-billable lane is always open)")
    lines.append("  * resource_type_id blockers   -> needs_resources_for_resource_type_entry")
    lines.append("  * Approval/debit correctness  -> needs_total_hours_for_debit AND")
    lines.append("                                   needs_consumed_hours_baseline_for_debit")
    lines.append("  * Historical baseline blocker -> needs_consumed_hours_baseline_for_debit")
    lines.append("                                  (preferred fix: replay Zoho Timesheets;")
    lines.append("                                   direct patch is a Finance-reviewed fallback)")
    lines.append("")
    lines.append(
        "Mode: READ-ONLY. No DB writes. No --execute flag. "
        "operator_candidate_patch is a candidate only — Finance / "
        "operator must confirm before any hydration step is applied."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def run(
    *,
    contracts_manifest: Optional[str],
    projects_manifest: Optional[str],
    out_path: Optional[str],
) -> dict[str, Any]:
    if contracts_manifest or projects_manifest:
        contracts = (
            _load_contracts_manifest(contracts_manifest) if contracts_manifest else []
        )
        projects = (
            _load_projects_manifest(projects_manifest) if projects_manifest else []
        )
        source = "manifest"
    else:
        contracts, projects, source_kind = await _load_from_db()
        if source_kind == "no_credentials":
            source = "mongo (empty / no credentials)"
        else:
            source = "mongo"

    plan = build_hydration_plan(contracts, projects, source_label=source)
    if out_path:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, sort_keys=True)
    return plan


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only hydration plan for imported Zoho contracts and "
            "projects. Never writes to Mongo. No execute flag exists by "
            "design — the actual hydration is a separate, "
            "Finance-reviewed step."
        ),
    )
    parser.add_argument(
        "--contracts-manifest", default=None,
        help=(
            "Optional JSON file containing imported contract documents "
            "(list or {'contracts': [...]}). When omitted, reads "
            "import_source='zoho' rows from Mongo via MONGO_URL + DB_NAME."
        ),
    )
    parser.add_argument(
        "--projects-manifest", default=None,
        help=(
            "Optional JSON file containing imported project documents "
            "(list or {'projects': [...]}). When omitted, reads "
            "import_source='zoho' rows from Mongo."
        ),
    )
    parser.add_argument(
        "--out",
        default="imports/zoho/reports/contracts_hydration_plan.json",
        help="Destination path for the JSON hydration plan.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        plan = asyncio.run(run(
            contracts_manifest=args.contracts_manifest,
            projects_manifest=args.projects_manifest,
            out_path=args.out,
        ))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(format_console_summary(plan))
    if args.out:
        print(f"\nReport written to: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
