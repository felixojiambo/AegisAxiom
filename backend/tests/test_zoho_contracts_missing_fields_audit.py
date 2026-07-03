"""Tests for the read-only contracts missing-fields audit + plan.

The audit script enumerates the May 22, 2026 Zoho parity fields that
the importer left blank when Zoho carries a value. The planner emits
per-contract candidate $set patches without ever offering to execute.

Both must stay strictly read-only — pinning tests below assert no
``--execute`` flag and no Mongo writers.
"""
from __future__ import annotations

import asyncio
import io
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
import scripts.zoho_contracts_missing_fields_audit as audit_mod  # noqa: E402
import scripts.zoho_contracts_missing_fields_plan as plan_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _zoho_row(
    *,
    record_id: str,
    contract_number: str = "CN-IMP-001",
    contract_type: str = "BOH",
    invoice_type: str = "Invoice at signing, due net30",
    contract_status: str = "Approved",
    total_hours: str = "120",
    remaining_hours: str = "60",
    consumed_hours: str = "60",
    consumed_pct: str = "50",
    bill_on_actual: str = "0",
    hours_to_be_paid: str = "0",
    paid_hours: str = "0",
    number_of_invoices: str = "1",
    ap_issues: str = "",
    status: str = "Active",
    contract_owner: str = "Synthetic Owner",
    created_by: str = "Synthetic Creator",
    created_time: str = "2024-04-10 21:47:10",
) -> dict[str, str]:
    return {
        "Record Id": record_id,
        "Contract Number": contract_number,
        "Contract Name": f"Synthetic {record_id}",
        "Account": "Synthetic Account",
        "Account.id": "ZA_SYN",
        "Deals": "Synthetic Deal",
        "Deals.id": "ZD_SYN",
        "Contract Type": contract_type,
        "Invoice Type": invoice_type,
        "Contract Status": contract_status,
        "Total Contract Hour's": total_hours,
        "Remaining Hour's": remaining_hours,
        "Total Consumed Hour's": consumed_hours,
        "Consumed Hour's %": consumed_pct,
        "Bill On Actual Hours": bill_on_actual,
        "Hours To Be Paid": hours_to_be_paid,
        "Paid Hours": paid_hours,
        "Number of Invoices": number_of_invoices,
        "AP Issues": ap_issues,
        "Status": status,
        "Contract Owner": contract_owner,
        "Created By": created_by,
        "Created Time": created_time,
    }


def _aegisaxiom_imported(
    *,
    contract_id: str,
    zoho_contract_id: str,
    contract_type: str = "boh",
    hourly_rate: Any = None,
    resources: list | None = None,
    imported_total_hours: Any = None,
    imported_consumed_hours: Any = None,
    contract_status_raw: Any = None,
    ap_issues_raw: Any = None,
    invoice_type_raw: Any = None,
    total_hours: Any = None,
    total_hours_purchased: Any = 0,
) -> dict[str, Any]:
    return {
        "contract_id": contract_id,
        "external_contract_id": zoho_contract_id,
        "external_ids": {
            "zoho_contract_id": zoho_contract_id,
            "zoho_account_id": "ZA_SYN",
            "zoho_deal_id": "ZD_SYN",
        },
        "contract_number": None,
        "contract_type": contract_type,
        "contract_type_raw": None,
        "status": "draft",
        "status_raw": None,
        "hourly_rate": hourly_rate,
        "resource_types": [],
        "resources": resources or [],
        "total_hours": total_hours,
        "total_hours_purchased": total_hours_purchased,
        "hours_consumed": 0,
        "imported_total_hours": imported_total_hours,
        "imported_remaining_hours": None,
        "imported_consumed_hours": imported_consumed_hours,
        "imported_consumed_percent": None,
        "invoice_type_raw": invoice_type_raw,
        "contract_status_raw": contract_status_raw,
        "zoho_status_raw": None,
        "ap_issues_raw": ap_issues_raw,
        "number_of_invoices": None,
        "bill_on_actual_hours": None,
        "hours_to_be_paid": None,
        "paid_hours": None,
        "source_created_by": None,
        "source_created_at": None,
        "import_source": "zoho",
    }


# ---------------------------------------------------------------------------
# Audit: actionable gap counters
# ---------------------------------------------------------------------------


def test_audit_counts_aegisaxiom_fields_empty_with_zoho_value():
    aegisaxiom = [_aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A")]
    zoho = [_zoho_row(record_id="ZC_A")]
    audit = audit_mod.build_audit(
        aegisaxiom_contracts=aegisaxiom,
        zoho_contracts_csv=zoho,
        zoho_transactions_csv=[],
        zoho_projects_csv=[],
        source_label="test",
    )
    gaps = audit["mapping_gaps"]["aegisaxiom_fields_empty_with_zoho_value"]
    # Every parity field except the ones we leave blank in Zoho should
    # be flagged because the AEGISAXIOM-side import left them empty.
    assert gaps["imported_total_hours"] == 1
    assert gaps["imported_remaining_hours"] == 1
    assert gaps["imported_consumed_hours"] == 1
    assert gaps["imported_consumed_percent"] == 1
    assert gaps["contract_status_raw"] == 1
    assert gaps["ap_issues_raw"] == 0  # Zoho row has empty AP Issues
    assert gaps["invoice_type_raw"] == 1
    # Zoho carries a literal "0" string for these — non-empty by the
    # audit's `_is_empty` check, so they DO count as actionable gaps
    # (AEGISAXIOM-empty + Zoho-has-a-value). Operators may decide later
    # that copying "0" forward is a no-op; the audit just reports.
    assert gaps["bill_on_actual_hours"] == 1
    assert gaps["source_created_by"] == 1


def test_audit_detects_hour_drift():
    aegisaxiom = [_aegisaxiom_imported(
        contract_id="c_a", zoho_contract_id="ZC_A",
        imported_total_hours=100,  # disagrees with Zoho 120
    )]
    zoho = [_zoho_row(record_id="ZC_A", total_hours="120")]
    audit = audit_mod.build_audit(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho,
        zoho_transactions_csv=[], zoho_projects_csv=[],
        source_label="test",
    )
    drifts = [d for d in audit["hour_mismatches"] if d["field"] == "imported_total_hours"]
    assert len(drifts) == 1
    assert drifts[0]["aegisaxiom"] == 100
    assert drifts[0]["zoho"] == 120
    assert drifts[0]["kind"] == "value_drift"


def test_audit_detects_contract_type_mismatch():
    aegisaxiom = [_aegisaxiom_imported(
        contract_id="c_a", zoho_contract_id="ZC_A",
        contract_type="boa",  # Zoho says BOH -> expected aegisaxiom boh
    )]
    zoho = [_zoho_row(record_id="ZC_A", contract_type="BOH")]
    audit = audit_mod.build_audit(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho,
        zoho_transactions_csv=[], zoho_projects_csv=[],
        source_label="test",
    )
    assert len(audit["contract_type_mismatches"]) == 1
    m = audit["contract_type_mismatches"][0]
    assert m["expected_aegisaxiom"] == "boh"
    assert m["aegisaxiom_actual"] == "boa"


def test_audit_lists_unmapped_zoho_columns():
    """Any header in the CSV that's not in the mapped variants must
    appear in zoho_columns_not_mapped_to_aegisaxiom so operators can audit
    drift."""
    zoho = [{
        **_zoho_row(record_id="ZC_A"),
        "Some New Zoho Column": "novel value",
    }]
    aegisaxiom = [_aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A")]
    audit = audit_mod.build_audit(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho,
        zoho_transactions_csv=[], zoho_projects_csv=[],
        source_label="test",
    )
    assert "Some New Zoho Column" in audit["mapping_gaps"]["zoho_columns_not_mapped_to_aegisaxiom"]


def test_audit_tracks_only_one_side_present():
    """AEGISAXIOM-only and Zoho-only buckets."""
    aegisaxiom = [_aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A")]
    zoho = [_zoho_row(record_id="ZC_B")]  # different id
    audit = audit_mod.build_audit(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho,
        zoho_transactions_csv=[], zoho_projects_csv=[],
        source_label="test",
    )
    assert audit["aegisaxiom_only_zoho_contract_ids"] == ["ZC_A"]
    assert audit["zoho_only_zoho_contract_ids"] == ["ZC_B"]


def test_audit_related_counts_from_transactions_and_projects():
    aegisaxiom = [_aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A")]
    zoho_contracts = [_zoho_row(record_id="ZC_A")]
    txns = [
        {"Contract.id": "ZC_A", "Transaction Type": "Consumed"},
        {"Contract.id": "ZC_A", "Transaction Type": "Purchased"},
        {"Contract.id": "ZC_OTHER", "Transaction Type": "Consumed"},
    ]
    projects = [
        {"Contract.id": "ZC_A"},
        {"Contract.id": "ZC_A"},
        {"Contract.id": "ZC_OTHER"},
    ]
    audit = audit_mod.build_audit(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho_contracts,
        zoho_transactions_csv=txns, zoho_projects_csv=projects,
        source_label="test",
    )
    rc = audit["related_counts"]
    assert rc["transactions_per_contract"]["ZC_A"] == 2
    assert rc["projects_per_contract"]["ZC_A"] == 2


def test_audit_handles_no_zoho_csv():
    """With AEGISAXIOM-only input, the audit still works and emits empty
    cross-source comparisons."""
    aegisaxiom = [_aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A")]
    audit = audit_mod.build_audit(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=[],
        zoho_transactions_csv=[], zoho_projects_csv=[],
        source_label="test",
    )
    assert audit["aegisaxiom_imported_count"] == 1
    assert audit["zoho_contract_count"] == 0
    assert audit["hour_mismatches"] == []
    assert audit["zoho_only_zoho_contract_ids"] == []


# ---------------------------------------------------------------------------
# Planner: candidate $set patches
# ---------------------------------------------------------------------------


def test_plan_emits_candidate_set_for_empty_aegisaxiom_fields():
    aegisaxiom = [_aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A")]
    zoho = [_zoho_row(
        record_id="ZC_A",
        invoice_type="net30",
        contract_status="Approved",
        total_hours="120",
        consumed_hours="60",
        remaining_hours="60",
        consumed_pct="50",
        contract_owner="Owner X",
        created_by="Creator X",
        created_time="2024-01-01 00:00:00",
    )]
    plan = plan_mod.build_plan(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho, source_label="test",
    )
    row = plan["contracts"][0]
    cs = row["candidate_set"]
    # Hour fields
    assert cs["imported_total_hours"] == 120.0
    assert cs["imported_remaining_hours"] == 60.0
    assert cs["imported_consumed_hours"] == 60.0
    assert cs["imported_consumed_percent"] == 50.0
    # String fields
    assert cs["invoice_type_raw"] == "net30"
    assert cs["contract_status_raw"] == "Approved"
    assert cs["source_created_by"] == "Creator X"
    assert cs["source_created_at"] == "2024-01-01 00:00:00"
    # Contract number propagated when blank on AEGISAXIOM.
    assert cs["contract_number"] == "CN-IMP-001"


def test_plan_proposes_total_hours_from_snapshot_with_review_flag():
    """For boh / boh_with_boa contracts whose canonical total_hours is
    zero/missing, the plan should offer the imported snapshot as a
    CANDIDATE but mark it as needing Finance review."""
    aegisaxiom = [_aegisaxiom_imported(
        contract_id="c_a", zoho_contract_id="ZC_A",
        contract_type="boh",
        imported_total_hours=120.0,
        total_hours=None,
        total_hours_purchased=0,
    )]
    zoho = [_zoho_row(record_id="ZC_A", total_hours="120")]
    plan = plan_mod.build_plan(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho, source_label="test",
    )
    row = plan["contracts"][0]
    cs = row["candidate_set"]
    assert cs["total_hours"] == 120.0
    assert cs["total_hours_purchased"] == 120.0
    assert "total_hours" in row["needs_finance_review"]


def test_plan_does_not_propose_total_hours_when_already_set():
    """Operator already set a positive total_hours — never overwrite."""
    aegisaxiom = [_aegisaxiom_imported(
        contract_id="c_a", zoho_contract_id="ZC_A",
        contract_type="boh",
        total_hours=200.0,  # already set
        total_hours_purchased=200.0,
    )]
    zoho = [_zoho_row(record_id="ZC_A", total_hours="120")]
    plan = plan_mod.build_plan(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho, source_label="test",
    )
    row = plan["contracts"][0]
    cs = row["candidate_set"]
    assert "total_hours" not in cs
    assert "total_hours_purchased" not in cs


def test_plan_skips_fields_already_present_on_aegisaxiom():
    """When AEGISAXIOM already has a value, the rule must not propose
    overwriting it."""
    aegisaxiom = [_aegisaxiom_imported(
        contract_id="c_a", zoho_contract_id="ZC_A",
        imported_total_hours=120.0,  # already populated
        contract_status_raw="Approved",  # already populated
    )]
    zoho = [_zoho_row(record_id="ZC_A")]
    plan = plan_mod.build_plan(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho, source_label="test",
    )
    cs = plan["contracts"][0]["candidate_set"]
    assert "imported_total_hours" not in cs
    assert "contract_status_raw" not in cs


def test_plan_no_zoho_row_warns_explicitly():
    aegisaxiom = [_aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_ORPHAN")]
    plan = plan_mod.build_plan(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=[], source_label="test",
    )
    row = plan["contracts"][0]
    assert row["candidate_set"] == {}
    assert "no_zoho_row_for_contract_id" in row["warnings"]


def test_plan_surfaces_contract_type_mismatch_as_warning():
    aegisaxiom = [_aegisaxiom_imported(
        contract_id="c_a", zoho_contract_id="ZC_A",
        contract_type="boa",
    )]
    zoho = [_zoho_row(record_id="ZC_A", contract_type="BOH")]
    plan = plan_mod.build_plan(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho, source_label="test",
    )
    row = plan["contracts"][0]
    assert any("contract_type mismatch" in w for w in row["warnings"])
    # Plan must NOT propose changing canonical contract_type.
    assert "contract_type" not in row["candidate_set"]


def test_plan_summary_rolls_up_counts():
    aegisaxiom = [
        _aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A"),
        _aegisaxiom_imported(contract_id="c_b", zoho_contract_id="ZC_B"),
        _aegisaxiom_imported(contract_id="c_c", zoho_contract_id="ZC_C",
                       imported_total_hours=100.0,
                       contract_status_raw="Approved",
                       invoice_type_raw="net30"),
    ]
    zoho = [_zoho_row(record_id="ZC_A"), _zoho_row(record_id="ZC_B")]
    plan = plan_mod.build_plan(
        aegisaxiom_contracts=aegisaxiom, zoho_contracts_csv=zoho, source_label="test",
    )
    s = plan["summary"]
    assert s["total_imported"] == 3
    # c_a + c_b get patches (multiple fields each). c_c has no zoho row
    # match -> no patch.
    assert s["with_patch"] == 2
    # boh + total_hours_purchased=0 + imported_total_hours from zoho
    # -> the review-flagged total_hours rule fires for both.
    assert s["needs_finance_review_count"] == 2


# ---------------------------------------------------------------------------
# Source-level safety
# ---------------------------------------------------------------------------


def test_audit_has_no_execute_flag(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        audit_mod.main(["--execute", "--out", str(tmp_path / "x.json")])
    assert excinfo.value.code == 2


def test_plan_has_no_execute_flag(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        plan_mod.main(["--execute", "--out", str(tmp_path / "x.json")])
    assert excinfo.value.code == 2


def test_no_mongo_writers_in_audit_source():
    src = Path(audit_mod.__file__).read_text(encoding="utf-8")
    for tok in (".update_one(", ".insert_one(", ".delete_one(", ".bulk_write(",
                ".replace_one(", ".update_many(", ".insert_many(", ".delete_many("):
        assert tok not in src, f"Audit script must not call {tok!r}"


def test_no_mongo_writers_in_plan_source():
    src = Path(plan_mod.__file__).read_text(encoding="utf-8")
    for tok in (".update_one(", ".insert_one(", ".delete_one(", ".bulk_write(",
                ".replace_one(", ".update_many(", ".insert_many(", ".delete_many("):
        assert tok not in src, f"Planner must not call {tok!r}"


def test_no_http_clients_imported():
    for mod in (audit_mod, plan_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for tok in ("import httpx", "import requests", "zoho_api"):
            assert tok not in src, f"{mod.__name__} must not import {tok!r}"


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_audit_cli_runs_with_no_inputs(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    out = tmp_path / "audit.json"
    code = audit_mod.main(["--out", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["aegisaxiom_imported_count"] == 0
    assert payload["zoho_contract_count"] == 0


def test_audit_cli_with_manifest_and_csv(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)

    aegisaxiom_path = tmp_path / "aegisaxiom.json"
    aegisaxiom_path.write_text(
        json.dumps([_aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A")]),
        encoding="utf-8",
    )

    csv_path = tmp_path / "contracts.csv"
    # Build a one-row CSV.
    z = _zoho_row(record_id="ZC_A")
    cols = list(z.keys())
    csv_path.write_text(
        ",".join(cols) + "\n" + ",".join(z[c] for c in cols) + "\n",
        encoding="utf-8",
    )

    out = tmp_path / "audit.json"
    code = audit_mod.main([
        "--aegisaxiom-manifest", str(aegisaxiom_path),
        "--contracts-csv", str(csv_path),
        "--out", str(out),
    ])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["aegisaxiom_imported_count"] == 1
    assert payload["zoho_contract_count"] == 1


def test_plan_cli_with_manifest_and_csv(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)

    aegisaxiom_path = tmp_path / "aegisaxiom.json"
    aegisaxiom_path.write_text(
        json.dumps({"contracts": [
            _aegisaxiom_imported(contract_id="c_a", zoho_contract_id="ZC_A"),
        ]}),
        encoding="utf-8",
    )
    csv_path = tmp_path / "contracts.csv"
    z = _zoho_row(record_id="ZC_A")
    cols = list(z.keys())
    csv_path.write_text(
        ",".join(cols) + "\n" + ",".join(z[c] for c in cols) + "\n",
        encoding="utf-8",
    )

    out = tmp_path / "plan.json"
    code = plan_mod.main([
        "--aegisaxiom-manifest", str(aegisaxiom_path),
        "--contracts-csv", str(csv_path),
        "--out", str(out),
    ])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["total_imported"] == 1
    assert payload["summary"]["with_patch"] >= 1
