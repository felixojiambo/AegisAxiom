"""Tests for the read-only Zoho contracts/projects hydration plan.

Pins the five readiness dimensions the plan reports against the
actual code paths they shadow:

  billable_draft_entry_ready    ``routes/time_entries.py`` 409 sell-rate
                                gate + the billing_type derivation at
                                line 367 (fixed_fee shortcircuit).
  non_billable_draft_entry_ready
                                Same route's 409 gate when
                                billing_type=='non_billable'.
  resource_type_entry_ready     The membership check at lines 325-337.
  approval_debit_ready          ``services/contract_debit`` contract-
                                balance updates by contract_type.
  hydration_required            Aggregate gate.

The classifier mirrors three helpers in the AEGISAXIOM code:

  * ``services/time_entry_rates.resolve_rate_snapshots``
  * ``services/contract_debit._contract_total_block_hours``
  * ``routes/time_entries.create_time_entry`` lines 367 / 407.

If any of those drift, the corresponding test below is the
early-warning that the classifier needs to follow.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
import scripts.zoho_contracts_hydration_plan as planmod  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic factories
# ---------------------------------------------------------------------------


def _imported_contract(
    *,
    contract_id: str = "contract_imp_001",
    contract_type: str = "boh",
    hourly_rate: Any = None,
    resource_types: list[dict[str, Any]] | None = None,
    resources: list[dict[str, Any]] | None = None,
    total_hours: Any = None,
    total_hours_purchased: Any = 0.0,
    imported_total_hours: Any = None,
    imported_consumed_hours: Any = 0.0,
    hours_consumed: Any = 0.0,
    status: str = "draft",
) -> dict[str, Any]:
    return {
        "contract_id": contract_id,
        "contract_number": "CN-IMP-001",
        "external_contract_id": "ZC0000000001",
        "contract_type": contract_type,
        "status": status,
        "hourly_rate": hourly_rate,
        "resource_types": resource_types or [],
        "resources": resources or [],
        "total_hours": total_hours,
        "total_hours_purchased": total_hours_purchased,
        "imported_total_hours": imported_total_hours,
        "imported_consumed_hours": imported_consumed_hours,
        "hours_consumed": hours_consumed,
        "import_source": "zoho",
    }


def _imported_project(
    *,
    project_id: str = "proj_imp_001",
    contract_id: Any = "contract_imp_001",
    name: str = "Synthetic Project",
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "external_project_id": "ZP0000000001",
        "name": name,
        "status": "planning",
        "contract_id": contract_id,
        "import_source": "zoho",
    }


# ---------------------------------------------------------------------------
# Per-dimension readiness — billable draft entry
# ---------------------------------------------------------------------------


def test_boh_without_sell_rate_is_not_billable_draft_ready():
    c = _imported_contract(contract_type="boh")
    row = planmod._classify_contract(c)
    assert row["readiness"]["billable_draft_entry_ready"] is False
    assert "needs_sell_rate_for_billable_draft" in row["gaps"]


def test_fixed_fee_without_sell_rate_is_billable_draft_ready():
    """``routes/time_entries.py`` line 367 sets
    billing_type='fixed_fee' for fixed_fee contracts BEFORE the 409
    sell-rate gate runs (line 407 only fires when
    billing_type=='billable'). The plan must reflect that bypass —
    fixed_fee contracts can accept a default billable POST today even
    with no sell rate."""
    c = _imported_contract(contract_type="fixed_fee")
    row = planmod._classify_contract(c)
    assert row["readiness"]["billable_draft_entry_ready"] is True
    assert "needs_sell_rate_for_billable_draft" not in row["gaps"]


def test_contract_with_hourly_rate_is_billable_draft_ready():
    c = _imported_contract(contract_type="boh", hourly_rate=175.0)
    row = planmod._classify_contract(c)
    assert row["readiness"]["billable_draft_entry_ready"] is True


def test_contract_with_resource_type_sell_rate_is_billable_draft_ready():
    c = _imported_contract(
        contract_type="boh",
        resource_types=[{"id": "rt_1", "sell_rate": 150.0}],
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["billable_draft_entry_ready"] is True


def test_zero_or_negative_rate_is_treated_as_missing():
    c = _imported_contract(contract_type="boh", hourly_rate=0)
    row = planmod._classify_contract(c)
    assert row["readiness"]["billable_draft_entry_ready"] is False


def test_classifier_billable_matches_rate_resolver_for_unhydrated_contract():
    """Lock the alignment between the classifier and the actual
    resolver — drift here means the plan and the route disagree."""
    from services.time_entry_rates import resolve_rate_snapshots

    c = _imported_contract(contract_type="boh")
    snap = resolve_rate_snapshots(c, user_id="u_x", resource_type_id=None)
    assert snap["sell_rate_snapshot"] is None
    row = planmod._classify_contract(c)
    assert row["readiness"]["billable_draft_entry_ready"] is False


def test_classifier_billable_matches_rate_resolver_for_hourly_rate():
    from services.time_entry_rates import resolve_rate_snapshots

    c = _imported_contract(contract_type="boh", hourly_rate=175.0)
    snap = resolve_rate_snapshots(c, user_id="u_x", resource_type_id=None)
    assert snap["sell_rate_snapshot"] == 175.0
    row = planmod._classify_contract(c)
    assert row["readiness"]["billable_draft_entry_ready"] is True


# ---------------------------------------------------------------------------
# Per-dimension readiness — non-billable draft entry
# ---------------------------------------------------------------------------


def test_unhydrated_contract_is_non_billable_draft_ready():
    """The 409 gate at routes/time_entries.py:407 only fires for
    billing_type=='billable'. A POST with is_billable=false maps to
    billing_type=='non_billable' (line 370), which bypasses the gate.
    Today the create endpoint imposes no further contract-shape
    requirement on non-billable entries."""
    c = _imported_contract()
    row = planmod._classify_contract(c)
    assert row["readiness"]["non_billable_draft_entry_ready"] is True


def test_fixed_fee_is_non_billable_draft_ready_too():
    c = _imported_contract(contract_type="fixed_fee")
    row = planmod._classify_contract(c)
    assert row["readiness"]["non_billable_draft_entry_ready"] is True


# ---------------------------------------------------------------------------
# Per-dimension readiness — resource_type-validated entry
# ---------------------------------------------------------------------------


def test_empty_resources_blocks_resource_type_entry():
    c = _imported_contract(resources=[])
    row = planmod._classify_contract(c)
    assert row["readiness"]["resource_type_entry_ready"] is False
    assert "needs_resources_for_resource_type_entry" in row["gaps"]


def test_resources_populated_unlocks_resource_type_entry():
    c = _imported_contract(
        resources=[{"user_id": "u_a", "resource_type_id": "rt_1", "cost_rate": 80.0}],
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["resource_type_entry_ready"] is True


# ---------------------------------------------------------------------------
# Per-dimension readiness — approval / debit math
# ---------------------------------------------------------------------------


def test_boh_without_total_hours_is_not_approval_debit_ready():
    """``contract_debit.apply_time_entry_to_contract`` increments
    contract.hours_consumed unconditionally for boh; without
    total_hours the counter has no meaningful denominator."""
    c = _imported_contract(contract_type="boh")
    row = planmod._classify_contract(c)
    assert row["readiness"]["approval_debit_ready"] is False
    assert "needs_total_hours_for_debit" in row["gaps"]


def test_boh_with_total_hours_and_no_consumed_snapshot_is_approval_debit_ready():
    """Default test fixture has imported_consumed_hours=0 — no
    historical consumption to baseline. So total_hours alone is
    enough."""
    c = _imported_contract(contract_type="boh", total_hours=120.0)
    row = planmod._classify_contract(c)
    assert row["readiness"]["approval_debit_ready"] is True


def test_boh_with_total_hours_purchased_is_approval_debit_ready():
    c = _imported_contract(contract_type="boh", total_hours_purchased=120.0)
    row = planmod._classify_contract(c)
    assert row["readiness"]["approval_debit_ready"] is True


def test_boh_with_resource_type_hours_purchased_is_approval_debit_ready():
    c = _imported_contract(
        contract_type="boh",
        resource_types=[{"id": "rt_1", "hours_purchased": 40.0}],
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["approval_debit_ready"] is True


def test_boh_with_boa_without_total_hours_is_not_approval_debit_ready():
    c = _imported_contract(contract_type="boh_with_boa")
    row = planmod._classify_contract(c)
    assert row["readiness"]["approval_debit_ready"] is False


def test_boh_with_total_hours_but_missing_consumed_baseline_is_not_approval_debit_ready():
    """Total_hours alone is INSUFFICIENT when the Zoho snapshot says
    the contract has historical consumption — without baselining,
    new approvals would understate consumption."""
    c = _imported_contract(
        contract_type="boh",
        total_hours=200.0,
        imported_consumed_hours=110.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["consumed_hours_baseline_ready"] is False
    assert row["readiness"]["approval_debit_ready"] is False
    assert "needs_consumed_hours_baseline_for_debit" in row["gaps"]


def test_fixed_fee_is_always_approval_debit_ready():
    """fixed_fee never moves a contract balance; the debit service
    short-circuits at billing_type=='fixed_fee'."""
    c = _imported_contract(contract_type="fixed_fee")
    row = planmod._classify_contract(c)
    assert row["readiness"]["approval_debit_ready"] is True


def test_boa_without_total_hours_is_approval_debit_ready():
    """The boa branch only increments total_hours_logged when that
    field already exists on the contract; an unhydrated import has
    no such field, so the branch is a no-op."""
    c = _imported_contract(contract_type="boa")
    row = planmod._classify_contract(c)
    assert row["readiness"]["approval_debit_ready"] is True


def test_other_contract_type_is_approval_debit_ready():
    c = _imported_contract(contract_type="other")
    row = planmod._classify_contract(c)
    assert row["readiness"]["approval_debit_ready"] is True


# ---------------------------------------------------------------------------
# operator_candidate_patch + finance review
# ---------------------------------------------------------------------------


def test_operator_candidate_patch_offers_imported_total_hours_when_present():
    """The snapshot stays a CANDIDATE — the doc/notes flag it for
    Finance review. We just assert the candidate IS surfaced, not
    that it is safe to apply blindly."""
    c = _imported_contract(contract_type="boh", imported_total_hours=136.0)
    row = planmod._classify_contract(c)
    assert row["operator_candidate_patch"]["total_hours"] == 136.0
    assert row["operator_candidate_patch"]["total_hours_purchased"] == 136.0
    review = row["needs_finance_review"]
    # Verify the operator is explicitly told to verify the snapshot.
    assert any("total_hours" in tag for tag in review)


def test_operator_candidate_patch_empty_when_no_snapshot():
    c = _imported_contract(contract_type="boh", imported_total_hours=None)
    row = planmod._classify_contract(c)
    assert "total_hours" not in row["operator_candidate_patch"]


def test_fixed_fee_does_not_emit_total_hours_in_candidate_patch():
    """fixed_fee does not run the BOH block debit, so the plan must
    not suggest copying imported_total_hours onto it."""
    c = _imported_contract(contract_type="fixed_fee", imported_total_hours=80.0)
    row = planmod._classify_contract(c)
    assert "total_hours" not in row["operator_candidate_patch"]


def test_operator_candidate_patch_offers_hours_consumed_when_warranted():
    """For a boh contract with a positive imported_consumed_hours and
    a zero canonical hours_consumed, the plan must surface
    imported_consumed_hours as a CANDIDATE patch and flag it for
    Finance / historical-replay verification."""
    c = _imported_contract(
        contract_type="boh",
        total_hours=200.0,
        imported_consumed_hours=110.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert row["operator_candidate_patch"]["hours_consumed"] == 110.0
    review = row["needs_finance_review"]
    # The verification note must reference time replay / Zoho snapshot.
    assert any(
        "hours_consumed" in tag and ("replay" in tag or "snapshot" in tag)
        for tag in review
    )


def test_operator_candidate_patch_omits_hours_consumed_when_snapshot_zero():
    """No historical consumption to baseline -> no candidate emitted."""
    c = _imported_contract(
        contract_type="boh",
        total_hours=200.0,
        imported_consumed_hours=0.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert "hours_consumed" not in row["operator_candidate_patch"]


def test_fixed_fee_does_not_emit_hours_consumed_in_candidate_patch():
    """fixed_fee never runs the BOH block debit; no consumed-hours
    baseline is needed even if imported_consumed_hours is positive."""
    c = _imported_contract(
        contract_type="fixed_fee",
        imported_consumed_hours=50.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert "hours_consumed" not in row["operator_candidate_patch"]


def test_hydration_required_aggregates_correctly():
    unhydrated = _imported_contract(contract_type="boh", imported_total_hours=80.0)
    row_u = planmod._classify_contract(unhydrated)
    assert row_u["readiness"]["hydration_required"] is True

    hydrated = _imported_contract(
        contract_type="boh",
        hourly_rate=175.0,
        total_hours=80.0,
        # No historical consumption snapshot -> baseline trivially ok.
        imported_consumed_hours=0.0,
        hours_consumed=0.0,
        resources=[{"user_id": "u_a", "cost_rate": 80.0}],
    )
    row_h = planmod._classify_contract(hydrated)
    assert row_h["readiness"]["hydration_required"] is False


# ---------------------------------------------------------------------------
# Per-dimension readiness — consumed-hours baseline
# ---------------------------------------------------------------------------


def test_boh_with_zero_imported_consumed_hours_does_not_need_baseline():
    """No historical consumption snapshot -> baseline is trivially ok."""
    c = _imported_contract(
        contract_type="boh",
        total_hours=100.0,
        imported_consumed_hours=0.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["consumed_hours_baseline_ready"] is True
    assert "needs_consumed_hours_baseline_for_debit" not in row["gaps"]


def test_boh_with_canonical_hours_consumed_set_does_not_need_baseline():
    """Someone — or a Timesheets replay pass — has already baselined
    the counter. The plan must not flag it as needing baseline."""
    c = _imported_contract(
        contract_type="boh",
        total_hours=100.0,
        imported_consumed_hours=80.0,
        hours_consumed=80.0,
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["consumed_hours_baseline_ready"] is True
    assert "needs_consumed_hours_baseline_for_debit" not in row["gaps"]


def test_boh_with_positive_imported_consumed_and_zero_canonical_needs_baseline():
    c = _imported_contract(
        contract_type="boh",
        total_hours=100.0,
        imported_consumed_hours=42.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["consumed_hours_baseline_ready"] is False
    assert "needs_consumed_hours_baseline_for_debit" in row["gaps"]


def test_boh_with_boa_with_positive_imported_consumed_needs_baseline():
    c = _imported_contract(
        contract_type="boh_with_boa",
        total_hours=300.0,
        imported_consumed_hours=120.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["consumed_hours_baseline_ready"] is False


def test_fixed_fee_with_positive_imported_consumed_does_not_need_baseline():
    """fixed_fee never debits a block, so a non-zero
    imported_consumed_hours is informational only — not a gap."""
    c = _imported_contract(
        contract_type="fixed_fee",
        imported_consumed_hours=50.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["consumed_hours_baseline_ready"] is True
    assert "needs_consumed_hours_baseline_for_debit" not in row["gaps"]


def test_boa_with_positive_imported_consumed_does_not_need_baseline():
    """boa increments total_hours_logged only when that field already
    exists. The unhydrated import has no such field, so no consumed-
    hours baseline is required."""
    c = _imported_contract(
        contract_type="boa",
        imported_consumed_hours=50.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["consumed_hours_baseline_ready"] is True
    assert row["readiness"]["approval_debit_ready"] is True


def test_other_contract_type_does_not_need_baseline():
    c = _imported_contract(
        contract_type="other",
        imported_consumed_hours=50.0,
        hours_consumed=0.0,
    )
    row = planmod._classify_contract(c)
    assert row["readiness"]["consumed_hours_baseline_ready"] is True
    assert row["readiness"]["approval_debit_ready"] is True


# ---------------------------------------------------------------------------
# Project readiness inheritance
# ---------------------------------------------------------------------------


def test_project_inherits_parent_contract_readiness():
    contracts = [
        _imported_contract(contract_id="c_boh", contract_type="boh"),
        _imported_contract(
            contract_id="c_ff",
            contract_type="fixed_fee",
        ),
        _imported_contract(
            contract_id="c_hydrated",
            contract_type="boh",
            hourly_rate=175.0,
            total_hours=120.0,
            resources=[{"user_id": "u_a", "cost_rate": 80.0}],
        ),
    ]
    projects = [
        _imported_project(project_id="p_boh", contract_id="c_boh"),
        _imported_project(project_id="p_ff", contract_id="c_ff"),
        _imported_project(project_id="p_hyd", contract_id="c_hydrated"),
    ]
    plan = planmod.build_hydration_plan(contracts, projects, source_label="test")
    by_id = {p["project_id"]: p for p in plan["projects"]}

    # p_boh inherits the unhydrated boh contract's flags.
    assert by_id["p_boh"]["readiness"]["billable_draft_entry_ready"] is False
    assert by_id["p_boh"]["readiness"]["approval_debit_ready"] is False
    assert by_id["p_boh"]["readiness"]["resource_type_entry_ready"] is False

    # p_ff inherits fixed_fee: billable draft and debit ready, but
    # resource_type entry still needs resources.
    assert by_id["p_ff"]["readiness"]["billable_draft_entry_ready"] is True
    assert by_id["p_ff"]["readiness"]["approval_debit_ready"] is True
    assert by_id["p_ff"]["readiness"]["resource_type_entry_ready"] is False

    # p_hyd inherits the fully-hydrated boh.
    assert by_id["p_hyd"]["readiness"]["billable_draft_entry_ready"] is True
    assert by_id["p_hyd"]["readiness"]["approval_debit_ready"] is True
    assert by_id["p_hyd"]["readiness"]["resource_type_entry_ready"] is True


def test_project_with_unresolved_parent_contract_flags_correctly():
    """Project carries contract_id but that id is not in the imported
    contract set -> parent_resolution='unresolved'."""
    contracts = [_imported_contract(contract_id="c_a", contract_type="boh")]
    projects = [
        _imported_project(project_id="p_unresolved", contract_id="c_missing"),
    ]
    plan = planmod.build_hydration_plan(contracts, projects, source_label="test")
    by_id = {p["project_id"]: p for p in plan["projects"]}
    p_un = by_id["p_unresolved"]
    assert p_un["parent_resolution"] == "unresolved"
    assert "unresolved_parent_contract" in p_un["gaps"]
    rd = p_un["readiness"]
    assert rd["billable_draft_entry_ready"] is False
    assert rd["non_billable_draft_entry_ready"] is False
    assert rd["approval_debit_ready"] is False
    assert rd["resource_type_entry_ready"] is False
    assert rd["consumed_hours_baseline_ready"] is False


def test_project_with_missing_contract_id_flags_correctly():
    """Project carries no contract_id at all -> parent_resolution='missing'."""
    contracts = [_imported_contract(contract_id="c_a", contract_type="boh")]
    projects = [
        _imported_project(project_id="p_no_cid", contract_id=None),
    ]
    plan = planmod.build_hydration_plan(contracts, projects, source_label="test")
    by_id = {p["project_id"]: p for p in plan["projects"]}
    p_no = by_id["p_no_cid"]
    assert p_no["parent_resolution"] == "missing"
    assert "missing_contract_id" in p_no["gaps"]
    rd = p_no["readiness"]
    assert rd["billable_draft_entry_ready"] is False
    assert rd["non_billable_draft_entry_ready"] is False
    assert rd["approval_debit_ready"] is False


def test_resolved_project_carries_inherited_parent_gaps():
    """The classifier mirrors the parent contract's gap list onto the
    project as ``inherited_parent_gaps`` so the project_impact
    inverse roll-ups can attribute gaps to specific contract problems
    without re-classifying the parent."""
    contracts = [_imported_contract(contract_id="c_a", contract_type="boh")]
    projects = [_imported_project(project_id="p_a", contract_id="c_a")]
    plan = planmod.build_hydration_plan(contracts, projects, source_label="test")
    by_id = {p["project_id"]: p for p in plan["projects"]}
    assert by_id["p_a"]["parent_resolution"] == "resolved"
    parent_gaps = set(by_id["p_a"]["inherited_parent_gaps"])
    assert "needs_sell_rate_for_billable_draft" in parent_gaps
    assert "needs_total_hours_for_debit" in parent_gaps
    assert "needs_resources_for_resource_type_entry" in parent_gaps


# ---------------------------------------------------------------------------
# Summary roll-up — contracts + project_impact
# ---------------------------------------------------------------------------


def test_summary_roll_up_with_mixed_contract_types():
    contracts = [
        _imported_contract(contract_id="c_boh", contract_type="boh"),
        _imported_contract(contract_id="c_ff", contract_type="fixed_fee"),
        _imported_contract(contract_id="c_boa", contract_type="boa"),
        _imported_contract(contract_id="c_oth", contract_type="other"),
        _imported_contract(
            contract_id="c_full",
            contract_type="boh",
            hourly_rate=175.0,
            total_hours=120.0,
            resources=[{"user_id": "u_a", "cost_rate": 80.0}],
        ),
    ]
    plan = planmod.build_hydration_plan(contracts, [], source_label="test")
    s = plan["summary"]
    # billable_draft_ready: fixed_fee (1) + boh hydrated (1) = 2.
    # boa and other still need a sell rate to pass the billable gate.
    assert s["billable_draft_entry_ready"] == 2
    # non-billable always works.
    assert s["non_billable_draft_entry_ready"] == 5
    # resource_type_entry: only c_full has resources.
    assert s["resource_type_entry_ready"] == 1
    # consumed_hours_baseline_ready: every contract has
    # imported_consumed_hours=0 (factory default), so baseline is
    # trivially ok for all 5.
    assert s["consumed_hours_baseline_ready"] == 5
    # approval_debit_ready: fixed_fee + boa + other + hydrated boh = 4.
    # c_boh (unhydrated) lacks total_hours.
    assert s["approval_debit_ready"] == 4
    # hydration_required: every contract except c_full is missing
    # something (resources at minimum).
    assert s["hydration_required"] == 4

    assert s["needs_sell_rate_for_billable_draft"] == 3  # c_boh, c_boa, c_oth
    assert s["needs_total_hours_for_debit"] == 1  # c_boh only
    assert s["needs_consumed_hours_baseline_for_debit"] == 0  # no positive snapshot
    assert s["needs_resources_for_resource_type_entry"] == 4  # all but c_full

    # by_contract_type roll-up
    by_t = s["by_contract_type"]
    assert by_t == {"boh": 2, "fixed_fee": 1, "boa": 1, "other": 1}


def test_summary_roll_up_with_consumed_hours_baseline_gap():
    contracts = [
        # c_a: boh, total_hours hydrated, but historical consumption
        # snapshot is positive and canonical counter still zero ->
        # baseline gap.
        _imported_contract(
            contract_id="c_a",
            contract_type="boh",
            hourly_rate=175.0,
            total_hours=200.0,
            imported_consumed_hours=110.0,
            hours_consumed=0.0,
        ),
        # c_b: boh, fully baselined (canonical positive).
        _imported_contract(
            contract_id="c_b",
            contract_type="boh",
            hourly_rate=175.0,
            total_hours=200.0,
            imported_consumed_hours=110.0,
            hours_consumed=110.0,
        ),
    ]
    plan = planmod.build_hydration_plan(contracts, [], source_label="test")
    s = plan["summary"]
    assert s["consumed_hours_baseline_ready"] == 1  # c_b only
    assert s["needs_consumed_hours_baseline_for_debit"] == 1
    # c_a has total_hours but no baseline -> approval_debit_ready=False.
    # c_b has both -> True.
    assert s["approval_debit_ready"] == 1


def test_project_impact_summary():
    contracts = [
        _imported_contract(contract_id="c_boh", contract_type="boh"),
        _imported_contract(contract_id="c_ff", contract_type="fixed_fee"),
    ]
    projects = [
        _imported_project(project_id="p1", contract_id="c_boh"),
        _imported_project(project_id="p2", contract_id="c_boh"),
        _imported_project(project_id="p3", contract_id="c_ff"),
        # Carries a non-empty contract_id but it does not resolve.
        _imported_project(project_id="p_unresolved", contract_id="c_missing"),
        # Carries no contract_id at all.
        _imported_project(project_id="p_no_cid", contract_id=None),
    ]
    plan = planmod.build_hydration_plan(contracts, projects, source_label="test")
    p = plan["project_impact"]
    assert p["total_imported_projects"] == 5
    # projects_with_contract_id counts any non-empty contract_id,
    # including the unresolved orphan.
    assert p["projects_with_contract_id"] == 4
    # projects_linked_to_imported_contracts counts ONLY projects
    # whose contract_id resolves in the imported set — the orphan
    # is NOT included.
    assert p["projects_linked_to_imported_contracts"] == 3
    assert p["projects_unresolved_parent_contract"] == 1
    assert p["projects_missing_contract_id"] == 1

    # Readiness counts use parent_resolution authoritatively.
    # billable safe = ff (1) only.
    assert p["projects_safe_for_billable_draft_entry"] == 1
    # non-billable: only resolved projects qualify (non-billable
    # requires the contract to exist in the imported set).
    assert p["projects_safe_for_non_billable_draft_entry"] == 3
    # resource_type safe = 0 (neither contract has resources).
    assert p["projects_safe_for_resource_type_entry"] == 0
    # approval_debit safe = ff (1).
    assert p["projects_safe_for_approval_debit"] == 1
    # consumed-hours baseline: all parents have imported_consumed_hours=0
    # (factory default) so resolved projects are baseline-ok.
    assert p["projects_safe_for_consumed_hours_baseline"] == 3

    # Inverse roll-ups count ONLY resolved projects. p1+p2 link to
    # c_boh which lacks sell rate/total_hours/resources; p3 links to
    # c_ff which lacks resources only.
    assert p["projects_linked_to_contracts_missing_sell_rate"] == 2
    assert p["projects_linked_to_contracts_missing_total_hours"] == 2
    assert p["projects_linked_to_contracts_missing_resources"] == 3
    # No positive imported_consumed_hours on any parent in this fixture.
    assert p["projects_linked_to_contracts_missing_consumed_hours_baseline"] == 0


def test_project_impact_counts_consumed_hours_baseline_gap():
    contracts = [
        _imported_contract(
            contract_id="c_baseline_gap",
            contract_type="boh",
            hourly_rate=175.0,
            total_hours=200.0,
            imported_consumed_hours=110.0,
            hours_consumed=0.0,
        ),
    ]
    projects = [
        _imported_project(project_id="p_a", contract_id="c_baseline_gap"),
        _imported_project(project_id="p_b", contract_id="c_baseline_gap"),
    ]
    plan = planmod.build_hydration_plan(contracts, projects, source_label="test")
    p = plan["project_impact"]
    assert p["projects_linked_to_contracts_missing_consumed_hours_baseline"] == 2
    assert p["projects_safe_for_consumed_hours_baseline"] == 0
    # Approval debit unsafe for both (parent has total_hours but no
    # consumed baseline).
    assert p["projects_safe_for_approval_debit"] == 0


# ---------------------------------------------------------------------------
# Plan envelope
# ---------------------------------------------------------------------------


def test_plan_includes_readiness_dimension_descriptions():
    """The JSON must document what each readiness flag means so an
    operator reading the report does not have to read this source
    file."""
    plan = planmod.build_hydration_plan([], [], source_label="test")
    rd = plan["readiness_dimensions"]
    for key in (
        "billable_draft_entry_ready",
        "non_billable_draft_entry_ready",
        "resource_type_entry_ready",
        "consumed_hours_baseline_ready",
        "approval_debit_ready",
        "hydration_required",
    ):
        assert key in rd
        assert isinstance(rd[key], str) and len(rd[key]) > 30


def test_plan_notes_warn_about_total_hours_only_hydration():
    """The notes must warn that setting total_hours alone is
    INSUFFICIENT for historically consumed BOH contracts, and must
    mention the Timesheets replay alternative."""
    plan = planmod.build_hydration_plan([], [], source_label="test")
    text = " ".join(plan.get("notes", [])).lower()
    assert "insufficient" in text
    assert "timesheets" in text or "replay" in text
    assert "hours_consumed" in text or "consumed-hours" in text or "consumed hours" in text


def test_plan_includes_finance_review_notes():
    plan = planmod.build_hydration_plan([], [], source_label="test")
    text = " ".join(plan.get("notes", []))
    # The plan must say candidate patch is not safe to apply blind.
    assert "candidate" in text.lower()
    assert "execute" in text.lower() or "finance" in text.lower()


def test_plan_includes_generated_at_iso():
    from datetime import datetime as _dt

    plan = planmod.build_hydration_plan([], [], source_label="test")
    assert "generated_at" in plan
    _dt.fromisoformat(plan["generated_at"].replace("Z", "+00:00"))


def test_plan_handles_empty_input():
    plan = planmod.build_hydration_plan([], [], source_label="test")
    assert plan["summary"]["total_imported"] == 0
    assert plan["project_impact"]["total_imported_projects"] == 0


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def test_cli_reads_contracts_and_projects_manifests(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    cm = tmp_path / "contracts.json"
    cm.write_text(
        json.dumps([
            _imported_contract(contract_id="c_a", contract_type="fixed_fee"),
            _imported_contract(contract_id="c_b", contract_type="boh"),
        ]),
        encoding="utf-8",
    )
    pm = tmp_path / "projects.json"
    pm.write_text(
        json.dumps([_imported_project(project_id="p_a", contract_id="c_a")]),
        encoding="utf-8",
    )
    out_path = tmp_path / "plan.json"
    code = planmod.main([
        "--contracts-manifest", str(cm),
        "--projects-manifest", str(pm),
        "--out", str(out_path),
    ])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["source"] == "manifest"
    assert payload["summary"]["total_imported"] == 2
    assert payload["project_impact"]["total_imported_projects"] == 1
    # Project p_a inherits fixed_fee parent: billable draft + debit ready.
    assert payload["project_impact"]["projects_safe_for_billable_draft_entry"] == 1


def test_cli_accepts_envelope_shaped_manifests(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    cm = tmp_path / "contracts.json"
    cm.write_text(
        json.dumps({"contracts": [_imported_contract()]}),
        encoding="utf-8",
    )
    pm = tmp_path / "projects.json"
    pm.write_text(
        json.dumps({"projects": [_imported_project()]}),
        encoding="utf-8",
    )
    out_path = tmp_path / "plan.json"
    code = planmod.main([
        "--contracts-manifest", str(cm),
        "--projects-manifest", str(pm),
        "--out", str(out_path),
    ])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["summary"]["total_imported"] == 1
    assert payload["project_impact"]["total_imported_projects"] == 1


def test_cli_rejects_missing_manifest(tmp_path):
    code = planmod.main([
        "--contracts-manifest", str(tmp_path / "no.json"),
        "--out", str(tmp_path / "plan.json"),
    ])
    assert code == 1


def test_cli_rejects_malformed_manifest(tmp_path):
    bad = tmp_path / "contracts.json"
    bad.write_text("{not json", encoding="utf-8")
    code = planmod.main([
        "--contracts-manifest", str(bad),
        "--out", str(tmp_path / "plan.json"),
    ])
    assert code == 1


def test_cli_runs_with_no_mongo_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    out_path = tmp_path / "plan.json"
    code = planmod.main(["--out", str(out_path)])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["summary"]["total_imported"] == 0
    assert payload["project_impact"]["total_imported_projects"] == 0


# ---------------------------------------------------------------------------
# Source-level safety
# ---------------------------------------------------------------------------


def test_script_has_no_execute_flag(tmp_path):
    """The hydration script must never gain an ``--execute`` flag —
    this test fails the day someone adds one, forcing a code-review
    conversation about Finance sign-off first."""
    with pytest.raises(SystemExit) as excinfo:
        planmod.main([
            "--execute",
            "--out", str(tmp_path / "plan.json"),
        ])
    assert excinfo.value.code == 2


def test_script_does_not_call_mongo_writers():
    src = Path(planmod.__file__).read_text(encoding="utf-8")
    forbidden = (
        ".update_one(",
        ".update_many(",
        ".insert_one(",
        ".insert_many(",
        ".delete_one(",
        ".delete_many(",
        ".bulk_write(",
        ".replace_one(",
    )
    for token in forbidden:
        assert token not in src, (
            f"Hydration plan must not call {token!r}. Read-only by design."
        )


def test_script_does_not_import_zoho_api_or_http_clients():
    src = Path(planmod.__file__).read_text(encoding="utf-8")
    forbidden = (
        "import httpx",
        "import requests",
        "zoho_api",
    )
    for token in forbidden:
        assert token not in src, (
            f"Hydration plan must not import {token!r}; consumes only "
            "the local DB / manifest."
        )


def test_tests_use_no_real_customer_data():
    sample = json.dumps([
        _imported_contract(),
        _imported_project(),
    ])
    assert "@simplimeta.com" not in sample
    for forbidden in ("Reading Bakery", "Acme"):
        assert forbidden not in sample
