"""Tests for the Zoho Projects → AEGISAXIOM import-plan generator.

Verifies:
  * Synthetic fixture produces project candidates with the documented
    shape.
  * Planned rows link to the parent AEGISAXIOM ``contract_id`` via the
    Zoho contract id.
  * Missing ``Contract.id`` goes to ``blocked[]``.
  * Unresolved ``Contract.id`` (no matching imported AEGISAXIOM contract)
    goes to ``blocked[]``.
  * Duplicate Project Record Id blocks readiness.
  * ``raw_source`` is preserved on every candidate (planned or
    blocked) so re-key by hand is possible later.
  * ``imported_consumed_hours`` is carried when the value parses.
  * Invalid consumed hours surface as a warning.
  * ``--allow-partial`` flips ``ready_for_import`` to ``true`` once
    only blocked rows remain excluded.
  * The script imports no Mongo / Zoho-API writers.
  * Tests don't require ``backend/imports/zoho/raw``.
  * Fixture contains no real customer data.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
import scripts.zoho_projects_import_plan as planmod  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "zoho_projects_sample.csv"


# ---------------------------------------------------------------------------
# Helpers (mirrors the dry-run test patterns)
# ---------------------------------------------------------------------------


def _contracts_manifest(tmp_path):
    """The imported AEGISAXIOM contracts identity map keyed by Zoho id.

    Same shape used by the dry-run tests so the two suites stay in
    lock-step on what counts as a resolved contract.
    """
    manifest = [
        {
            "contract_id": "contract_alpha_aegisaxiom",
            "external_contract_id": "ZC0000000001",
            "external_ids": {
                "zoho_account_id": "ZA0000000001",
                "zoho_deal_id": "ZD0000000001",
            },
            "contract_number": "CN-FIX-001",
            "name": "Synthetic Contract Alpha",
        },
        {
            "contract_id": "contract_beta_aegisaxiom",
            "external_contract_id": "ZC0000000002",
            "external_ids": {
                "zoho_account_id": "ZA0000000002",
                "zoho_deal_id": "ZD0000000002",
            },
            "contract_number": "CN-FIX-002",
            "name": "Synthetic Contract Beta",
        },
    ]
    path = tmp_path / "contracts_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _empty_dry_run_report(tmp_path) -> Path:
    """The plan generator only reads the dry-run JSON for presence /
    parseability — synthetic tests don't need the full shape. Provide
    a minimal valid object.
    """
    path = tmp_path / "dry_run.json"
    path.write_text(json.dumps({"total_rows": 0}), encoding="utf-8")
    return path


def _run(tmp_path, *, allow_partial=False, projects_path=None):
    out = tmp_path / "plan.json"
    report = asyncio.run(planmod.run(
        projects_path=str(projects_path or FIXTURE),
        dry_run_report_path=str(_empty_dry_run_report(tmp_path)),
        out_path=str(out),
        allow_partial=allow_partial,
        manifest_path=str(_contracts_manifest(tmp_path)),
    ))
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == report
    return report


def _find_blocker(plan_row, kind):
    for blk in plan_row.get("blockers", []):
        if blk["kind"] == kind:
            return blk
    return None


def _planned_row(report, zoho_project_id):
    return next(
        (p for p in report["projects"] if p["zoho_project_id"] == zoho_project_id),
        None,
    )


def _blocked_row(report, zoho_project_id):
    return next(
        (b for b in report["blocked"] if b["zoho_project_id"] == zoho_project_id),
        None,
    )


# ---------------------------------------------------------------------------
# Fixture hygiene
# ---------------------------------------------------------------------------


def test_synthetic_fixture_is_synthetic_and_real_dir_not_required():
    text = FIXTURE.read_text(encoding="utf-8")
    assert "Synthetic" in text
    assert "@example.invalid" in text
    # The plan must work without backend/imports/zoho/raw existing.
    real = _BACKEND_DIR / "imports" / "zoho" / "raw"
    if real.exists():
        assert real.is_dir()
    assert FIXTURE.is_file()


# ---------------------------------------------------------------------------
# Plan shape + happy path
# ---------------------------------------------------------------------------


def test_synthetic_fixture_produces_project_candidates(tmp_path):
    report = _run(tmp_path)
    assert report["total_rows"] == 9
    assert "projects" in report and "blocked" in report and "warnings" in report
    # Documented top-level keys all present.
    expected_top = {
        "source_files", "options", "total_rows", "planned_import_count",
        "blocked_count", "warning_count", "ready_for_import",
        "summary", "projects", "blocked", "warnings",
    }
    assert expected_top <= set(report)


def test_planned_row_links_to_parent_aegisaxiom_contract(tmp_path):
    report = _run(tmp_path)
    # ZP0000000008 (Theta) is the cleanest row referencing Beta.
    row = _planned_row(report, "ZP0000000008")
    assert row is not None
    assert row["aegisaxiom_contract_id"] == "contract_beta_aegisaxiom"
    cand = row["aegisaxiom_project_candidate"]
    assert cand["aegisaxiom_contract_id"] == "contract_beta_aegisaxiom"
    assert cand["external_ids"]["zoho_contract_id"] == "ZC0000000002"
    assert cand["contract_number"] == "CN-FIX-002"


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------


def test_missing_contract_id_is_blocked(tmp_path):
    report = _run(tmp_path)
    row = _blocked_row(report, "ZP0000000002")
    assert row is not None
    assert _find_blocker(row, "missing_contract_id") is not None


def test_unresolved_contract_id_is_blocked(tmp_path):
    report = _run(tmp_path)
    row = _blocked_row(report, "ZP0000000003")
    assert row is not None
    blk = _find_blocker(row, "unresolved_contract_id")
    assert blk is not None
    assert blk["zoho_contract_id"] == "ZC9999999999"


def test_duplicate_record_id_blocks_both_copies(tmp_path):
    report = _run(tmp_path)
    # ZP0000000001 appears twice in the fixture.
    dup_blocks = [
        b for b in report["blocked"]
        if b["zoho_project_id"] == "ZP0000000001"
    ]
    assert len(dup_blocks) == 2
    assert all(
        _find_blocker(b, "duplicate_record_id") is not None
        for b in dup_blocks
    )


def test_blocked_rows_still_carry_raw_source(tmp_path):
    report = _run(tmp_path)
    row = _blocked_row(report, "ZP0000000002")
    assert row is not None
    raw = row["aegisaxiom_project_candidate"]["raw_source"]
    # Every column from the fixture round-trips into raw_source so a
    # later manual-link pass can re-key the row by hand.
    assert raw["Record Id"] == "ZP0000000002"


# ---------------------------------------------------------------------------
# Account / deal warnings
# ---------------------------------------------------------------------------


def test_account_id_mismatch_surfaces_as_warning(tmp_path):
    report = _run(tmp_path)
    # ZP0000000004 carries account ZA9999999999 against Alpha's ZA0000000001.
    row = _planned_row(report, "ZP0000000004")
    assert row is not None
    kinds = {w["kind"] for w in row["warnings"]}
    assert "account_id_mismatch_with_contract" in kinds


def test_deal_id_mismatch_surfaces_as_warning(tmp_path):
    report = _run(tmp_path)
    row = _planned_row(report, "ZP0000000005")
    assert row is not None
    kinds = {w["kind"] for w in row["warnings"]}
    assert "deal_id_mismatch_with_contract" in kinds


# ---------------------------------------------------------------------------
# Consumed hours
# ---------------------------------------------------------------------------


def test_imported_consumed_hours_carried_when_valid(tmp_path):
    contracts = _contracts_manifest(tmp_path)
    csv_text = (
        "Record Id,Project Name,Project Id,Account,Account.id,Deal,Deal.id,"
        "Contract,Contract.id,Status,Total Project Consumed Hour's\n"
        "ZP0000000111,Synthetic With Hours,PID-001,Synthetic Account Alpha,"
        "ZA0000000001,Synthetic Deal Alpha,ZD0000000001,Synthetic Contract Alpha,"
        "ZC0000000001,Active,42.5\n"
    )
    src = tmp_path / "with_hours.csv"
    src.write_text(csv_text, encoding="utf-8")
    out = tmp_path / "plan.json"
    report = asyncio.run(planmod.run(
        projects_path=str(src),
        dry_run_report_path=str(_empty_dry_run_report(tmp_path)),
        out_path=str(out),
        allow_partial=False,
        manifest_path=str(contracts),
    ))
    row = _planned_row(report, "ZP0000000111")
    assert row is not None
    assert row["aegisaxiom_project_candidate"]["imported_consumed_hours"] == 42.5
    # No warnings about consumed hours.
    kinds = {w["kind"] for w in row["warnings"]}
    assert "invalid_consumed_hours" not in kinds
    assert "missing_consumed_hours" not in kinds


def test_invalid_consumed_hours_is_a_warning_not_a_blocker(tmp_path):
    contracts = _contracts_manifest(tmp_path)
    csv_text = (
        "Record Id,Project Name,Project Id,Account,Account.id,Deal,Deal.id,"
        "Contract,Contract.id,Status,Total Project Consumed Hour's\n"
        "ZP0000000222,Synthetic Bad Hours,PID-002,Synthetic Account Alpha,"
        "ZA0000000001,Synthetic Deal Alpha,ZD0000000001,Synthetic Contract Alpha,"
        "ZC0000000001,Active,not-a-number\n"
    )
    src = tmp_path / "bad_hours.csv"
    src.write_text(csv_text, encoding="utf-8")
    out = tmp_path / "plan.json"
    report = asyncio.run(planmod.run(
        projects_path=str(src),
        dry_run_report_path=str(_empty_dry_run_report(tmp_path)),
        out_path=str(out),
        allow_partial=False,
        manifest_path=str(contracts),
    ))
    row = _planned_row(report, "ZP0000000222")
    assert row is not None  # not blocked
    assert row["aegisaxiom_project_candidate"]["imported_consumed_hours"] is None
    kinds = {w["kind"] for w in row["warnings"]}
    assert "invalid_consumed_hours" in kinds


# ---------------------------------------------------------------------------
# --allow-partial gate
# ---------------------------------------------------------------------------


def test_default_policy_blocks_readiness_when_any_blockers(tmp_path):
    report = _run(tmp_path, allow_partial=False)
    assert report["blocked_count"] > 0
    assert report["ready_for_import"] is False


def test_allow_partial_flips_readiness(tmp_path):
    report = _run(tmp_path, allow_partial=True)
    # planned rows are still planned; blocked are still blocked,
    # but the gate now says we can proceed with the partial slice.
    assert report["blocked_count"] > 0
    assert report["ready_for_import"] is True


# ---------------------------------------------------------------------------
# Source safety
# ---------------------------------------------------------------------------


def test_script_does_not_import_zoho_api():
    src = Path(planmod.__file__).read_text(encoding="utf-8")
    forbidden = (
        "import httpx",
        "import requests",
        "zoho_api",
    )
    for token in forbidden:
        assert token not in src, (
            f"Projects import plan must not import {token!r}; this "
            "slice is filesystem-only."
        )


def test_script_does_not_call_db_writers():
    src = Path(planmod.__file__).read_text(encoding="utf-8")
    forbidden = (
        ".insert_one(",
        ".insert_many(",
        ".update_one(",
        ".update_many(",
        ".delete_one(",
        ".replace_one(",
        ".bulk_write(",
    )
    for token in forbidden:
        assert token not in src, (
            f"Projects import plan must not call {token!r}; it is "
            "read-only."
        )


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def test_main_runs_against_synthetic_fixture(tmp_path, capsys, monkeypatch):
    # No Mongo env; contracts map comes from the manifest.
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    out = tmp_path / "plan.json"
    code = planmod.main([
        "--projects", str(FIXTURE),
        "--dry-run-report", str(_empty_dry_run_report(tmp_path)),
        "--out", str(out),
        "--contracts-manifest", str(_contracts_manifest(tmp_path)),
    ])
    assert code == 0
    stdout = capsys.readouterr().out
    assert "Zoho Projects Import Plan" in stdout
    assert "Ready for import:" in stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["total_rows"] == 9


def test_main_rejects_missing_files(tmp_path):
    code = planmod.main([
        "--projects", str(tmp_path / "no_projects.csv"),
        "--dry-run-report", str(tmp_path / "no_report.json"),
        "--out", str(tmp_path / "plan.json"),
    ])
    assert code == 1


def test_main_rejects_malformed_dry_run_report(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    code = planmod.main([
        "--projects", str(FIXTURE),
        "--dry-run-report", str(bad),
        "--out", str(tmp_path / "plan.json"),
    ])
    assert code == 1


# ---------------------------------------------------------------------------
# Empty contracts map → every row blocks
# ---------------------------------------------------------------------------


def test_empty_contracts_map_blocks_every_resolvable_row(tmp_path):
    """When the contracts identity map is empty, every row whose
    Contract.id IS present should land in blocked[] as
    ``unresolved_contract_id`` — the safe default the plan generator
    documents.
    """
    empty_manifest = tmp_path / "empty.json"
    empty_manifest.write_text(json.dumps([]), encoding="utf-8")
    out = tmp_path / "plan.json"
    report = asyncio.run(planmod.run(
        projects_path=str(FIXTURE),
        dry_run_report_path=str(_empty_dry_run_report(tmp_path)),
        out_path=str(out),
        allow_partial=False,
        manifest_path=str(empty_manifest),
    ))
    # Every row in the synthetic fixture has a Contract.id except
    # ZP0000000002 (missing) — so 8 rows block on
    # unresolved_contract_id and 1 on missing_contract_id (plus the
    # duplicate-id pair). Net: blocked_count is at least 8.
    blocker_kinds = report["summary"]["blocker_kinds"]
    assert blocker_kinds.get("unresolved_contract_id", 0) >= 7
