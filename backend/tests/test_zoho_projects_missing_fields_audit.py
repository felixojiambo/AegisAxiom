"""Tests for the read-only projects missing-fields audit + plan."""
from __future__ import annotations

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

import scripts.zoho_projects_missing_fields_audit as audit_mod  # noqa: E402
import scripts.zoho_projects_missing_fields_plan as plan_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _zoho_project_row(
    *,
    record_id: str,
    project_name: str = "Synthetic Project",
    project_id: str = "1764876000007270075",
    project_url: str = "https://projects.zoho.com/portal/x#todomilestones/1",
    account: str = "Synthetic Account",
    account_id: str = "ZA_SYN",
    deal: str = "Synthetic Deal",
    deal_id: str = "ZD_SYN",
    contract: str = "Synthetic Contract",
    contract_id: str = "ZC_SYN",
    project_owner: str = "Synthetic Owner",
    consumed_hours: str = "12",
    status: str = "Active",
    deleted: str = "A",
    modified_time: str = "2025-04-29 21:21:51",
) -> dict[str, str]:
    return {
        "Record Id": record_id,
        "Project Name": project_name,
        "Project Id": project_id,
        "ZOHO Project URL": project_url,
        "Account": account,
        "Account.id": account_id,
        "Deal": deal,
        "Deal.id": deal_id,
        "Contract": contract,
        "Contract.id": contract_id,
        "Project Owner": project_owner,
        "Total Project Consumed Hour's": consumed_hours,
        "Status": status,
        "deleted field": deleted,
        "Modified Time": modified_time,
    }


def _aegisaxiom_project(
    *,
    project_id: str,
    zoho_project_id: str,
    zoho_contract_id: Any = None,
    pm_id: Any = None,
    imported_consumed_hours: Any = None,
    zoho_project_url: Any = None,
    owner_raw: Any = None,
    status_raw: Any = None,
    project_number: Any = None,
    source_modified_at: Any = None,
    account_name: Any = None,
    deal_name: Any = None,
    contract_number: Any = None,
    deleted_raw: Any = None,
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "external_project_id": zoho_project_id,
        "name": "Synthetic",
        "project_type": "waterfall",
        "service_line": "other",
        "status": "planning",
        "health": "green",
        "pm_id": pm_id,
        "assigned_user_ids": [],
        "start_date": None,
        "target_end_date": None,
        "actual_end_date": None,
        "external_ids": {
            "zoho_project_id": zoho_project_id,
            "zoho_contract_id": zoho_contract_id,
            "zoho_account_id": None,
            "zoho_deal_id": None,
        },
        "zoho_project_url": zoho_project_url,
        "owner_raw": owner_raw,
        "status_raw": status_raw,
        "project_number": project_number,
        "source_modified_at": source_modified_at,
        "account_name": account_name,
        "deal_name": deal_name,
        "contract_number": contract_number,
        "deleted_raw": deleted_raw,
        "imported_consumed_hours": imported_consumed_hours,
        "import_source": "zoho",
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_audit_actionable_gap_counts():
    aegisaxiom = [_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A")]
    zoho = [_zoho_project_row(record_id="ZP_A")]
    audit = audit_mod.build_audit(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        zoho_tasks_csv=[], zoho_transactions_csv=[],
        source_label="test",
    )
    gaps = audit["mapping_gaps"]["aegisaxiom_fields_empty_with_zoho_value"]
    assert gaps["zoho_project_url"] == 1
    assert gaps["project_number"] == 1
    assert gaps["owner_raw"] == 1
    assert gaps["status_raw"] == 1
    assert gaps["imported_consumed_hours"] == 1
    assert gaps["source_modified_at"] == 1
    assert gaps["external_ids.zoho_contract_id"] == 1
    assert gaps["deleted_raw"] == 1


def test_audit_hour_drift_detected():
    aegisaxiom = [_aegisaxiom_project(
        project_id="p_a", zoho_project_id="ZP_A",
        imported_consumed_hours=10,
    )]
    zoho = [_zoho_project_row(record_id="ZP_A", consumed_hours="20")]
    audit = audit_mod.build_audit(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        zoho_tasks_csv=[], zoho_transactions_csv=[],
        source_label="test",
    )
    drifts = audit["hour_mismatches"]
    assert len(drifts) == 1
    assert drifts[0]["aegisaxiom"] == 10
    assert drifts[0]["zoho"] == 20


def test_audit_orphan_buckets():
    aegisaxiom = [_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A")]
    zoho = [_zoho_project_row(record_id="ZP_B")]
    audit = audit_mod.build_audit(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        zoho_tasks_csv=[], zoho_transactions_csv=[],
        source_label="test",
    )
    assert audit["aegisaxiom_only_zoho_project_ids"] == ["ZP_A"]
    assert audit["zoho_only_zoho_project_ids"] == ["ZP_B"]


def test_audit_lists_unmapped_zoho_columns():
    zoho = [{
        **_zoho_project_row(record_id="ZP_A"),
        "Some Novel Column": "value",
    }]
    aegisaxiom = [_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A")]
    audit = audit_mod.build_audit(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        zoho_tasks_csv=[], zoho_transactions_csv=[],
        source_label="test",
    )
    assert "Some Novel Column" in audit["mapping_gaps"]["zoho_columns_not_mapped_to_aegisaxiom"]


def test_audit_tasks_per_project_counter():
    zoho = [_zoho_project_row(record_id="ZP_A")]
    aegisaxiom = [_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A")]
    tasks = [
        {"Project Id": "ZP_A"},
        {"Project Id": "ZP_A"},
        {"Project Id": "ZP_B"},
    ]
    audit = audit_mod.build_audit(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        zoho_tasks_csv=tasks, zoho_transactions_csv=[],
        source_label="test",
    )
    tpp = audit["related_counts"]["tasks_per_project"]
    assert tpp["ZP_A"] == 2
    assert tpp["ZP_B"] == 1


def test_audit_owner_gap_flagged():
    aegisaxiom = [_aegisaxiom_project(
        project_id="p_a", zoho_project_id="ZP_A", pm_id=None,
    )]
    zoho = [_zoho_project_row(record_id="ZP_A", project_owner="Some PM")]
    audit = audit_mod.build_audit(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        zoho_tasks_csv=[], zoho_transactions_csv=[],
        source_label="test",
    )
    assert len(audit["owner_mapping_gaps"]) == 1
    assert audit["owner_mapping_gaps"][0]["zoho_owner"] == "Some PM"


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def test_plan_emits_candidate_set_for_empty_fields():
    aegisaxiom = [_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A")]
    zoho = [_zoho_project_row(
        record_id="ZP_A",
        project_url="https://projects.zoho.com/portal/x#todomilestones/1",
        project_owner="Owner X",
        consumed_hours="42",
        project_id="ZPI-99",
    )]
    plan = plan_mod.build_plan(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        source_label="test",
    )
    row = plan["projects"][0]
    cs = row["candidate_set"]
    assert cs["zoho_project_url"].startswith("https://")
    assert cs["owner_raw"] == "Owner X"
    assert cs["imported_consumed_hours"] == 42.0
    assert cs["project_number"] == "ZPI-99"
    assert cs["external_ids.zoho_contract_id"] == "ZC_SYN"


def test_plan_does_not_overwrite_existing_fields():
    aegisaxiom = [_aegisaxiom_project(
        project_id="p_a", zoho_project_id="ZP_A",
        zoho_project_url="https://existing/",
        imported_consumed_hours=10.0,
    )]
    zoho = [_zoho_project_row(record_id="ZP_A", consumed_hours="20")]
    plan = plan_mod.build_plan(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        source_label="test",
    )
    cs = plan["projects"][0]["candidate_set"]
    assert "zoho_project_url" not in cs
    assert "imported_consumed_hours" not in cs


def test_plan_flags_no_zoho_row():
    aegisaxiom = [_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_ORPHAN")]
    plan = plan_mod.build_plan(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=[],
        source_label="test",
    )
    row = plan["projects"][0]
    assert row["candidate_set"] == {}
    assert "no_zoho_row_for_project_id" in row["warnings"]


def test_plan_surfaces_task_count_in_warnings_only():
    """Task counts must be informational only — never enter the
    candidate_set, since this slice does not import tasks."""
    aegisaxiom = [_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A")]
    zoho = [_zoho_project_row(record_id="ZP_A")]
    tasks = [{"Project Id": "ZP_A"}, {"Project Id": "ZP_A"}]
    plan = plan_mod.build_plan(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho,
        zoho_tasks_csv=tasks, source_label="test",
    )
    row = plan["projects"][0]
    assert any("tasks_observed_in_zoho_export_count=2" in w for w in row["warnings"])
    assert "tasks" not in row["candidate_set"]


def test_plan_flags_pm_id_for_users_export_review():
    """Every imported project with pm_id=None must surface a
    needs_finance_review entry pointing at the Users export
    follow-up."""
    aegisaxiom = [_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A", pm_id=None)]
    zoho = [_zoho_project_row(record_id="ZP_A")]
    plan = plan_mod.build_plan(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho, source_label="test",
    )
    row = plan["projects"][0]
    assert any("pm_id" in tag for tag in row["needs_finance_review"])


def test_plan_summary_counts():
    aegisaxiom = [
        _aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A"),
        _aegisaxiom_project(project_id="p_b", zoho_project_id="ZP_B"),
        _aegisaxiom_project(project_id="p_c", zoho_project_id="ZP_NOMATCH"),
    ]
    zoho = [_zoho_project_row(record_id="ZP_A"), _zoho_project_row(record_id="ZP_B")]
    plan = plan_mod.build_plan(
        aegisaxiom_projects=aegisaxiom, zoho_projects_csv=zoho, source_label="test",
    )
    s = plan["summary"]
    assert s["total_imported"] == 3
    assert s["with_patch"] == 2


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
        assert tok not in src


def test_no_mongo_writers_in_plan_source():
    src = Path(plan_mod.__file__).read_text(encoding="utf-8")
    for tok in (".update_one(", ".insert_one(", ".delete_one(", ".bulk_write(",
                ".replace_one(", ".update_many(", ".insert_many(", ".delete_many("):
        assert tok not in src


def test_no_http_clients_imported():
    for mod in (audit_mod, plan_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for tok in ("import httpx", "import requests", "zoho_api"):
            assert tok not in src


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_audit_cli_handles_no_inputs(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    out = tmp_path / "audit.json"
    code = audit_mod.main(["--out", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["aegisaxiom_imported_count"] == 0


def test_plan_cli_with_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)

    aegisaxiom_path = tmp_path / "aegisaxiom.json"
    aegisaxiom_path.write_text(
        json.dumps([_aegisaxiom_project(project_id="p_a", zoho_project_id="ZP_A")]),
        encoding="utf-8",
    )

    csv_path = tmp_path / "projects.csv"
    z = _zoho_project_row(record_id="ZP_A")
    cols = list(z.keys())
    csv_path.write_text(
        ",".join(cols) + "\n" + ",".join(z[c] for c in cols) + "\n",
        encoding="utf-8",
    )

    out = tmp_path / "plan.json"
    code = plan_mod.main([
        "--aegisaxiom-manifest", str(aegisaxiom_path),
        "--projects-csv", str(csv_path),
        "--out", str(out),
    ])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["with_patch"] == 1
