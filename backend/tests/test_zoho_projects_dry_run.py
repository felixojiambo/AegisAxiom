"""Tests for the Zoho Projects dry-run validator.

Verifies:
  * Synthetic fixture parses.
  * Duplicate Project Record Id is detected.
  * Missing Contract.id is reported as an error.
  * Unresolved Contract.id (no matching imported AEGISAXIOM contract) is
    reported as an error.
  * Account/Deal id mismatches against the contract's stamped ids
    are reported as warnings.
  * Enumeration counts (project_status / project_type) are produced.
  * JSON report carries ``ready_for_import_plan``.
  * Script imports nothing that touches the Zoho API.
  * Tests don't require ``backend/imports/zoho/raw``.
  * Fixture contains no real customer data.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
import scripts.zoho_projects_dry_run as zpdr  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "zoho_projects_sample.csv"


# ---------------------------------------------------------------------------
# Synthetic contracts manifest (what the contracts importer would have
# stamped onto db.contracts for the fixture's referenced contracts).
# ---------------------------------------------------------------------------


def _contracts_manifest(tmp_path):
    """The imported AEGISAXIOM contracts identity map keyed by Zoho id.

    Fixture references:
      ZC0000000001 — Synthetic Contract Alpha
      ZC0000000002 — Synthetic Contract Beta
      ZC9999999999 — appears in fixture but NOT in the manifest
                    (so it surfaces as ``unresolved_contract_id``).

    Project rows can also reference a different Account/Deal than the
    contract was originally linked to. The manifest stamps the
    contract's Zoho account_id / deal_id so the validator can
    cross-check.
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


def _run(tmp_path):
    out = tmp_path / "report.json"
    report = asyncio.run(zpdr.run(
        csv_path=str(FIXTURE),
        out_path=str(out),
        manifest_path=str(_contracts_manifest(tmp_path)),
    ))
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == report
    return report


def _find_error(report, kind):
    return next((e for e in report["errors"] if e["kind"] == kind), None)


def _find_warning(report, kind):
    return next((w for w in report["warnings"] if w["kind"] == kind), None)


# ---------------------------------------------------------------------------
# Fixture hygiene
# ---------------------------------------------------------------------------


def test_fixture_exists_and_is_synthetic():
    text = FIXTURE.read_text(encoding="utf-8")
    assert "Synthetic" in text
    assert "@example.invalid" in text
    # ZP/ZC/ZD/ZA prefixes are not valid Zoho Record Id shapes.
    assert "ZP0000000001" in text
    # Real Zoho IDs are 19-digit decimals; fixture must not contain any.
    import re

    long_numeric = re.findall(r"\b\d{19}\b", text)
    assert not long_numeric, (
        f"Fixture must not contain real Zoho IDs (19-digit numbers); "
        f"found: {long_numeric}"
    )


def test_tests_do_not_require_real_imports_directory():
    real = _BACKEND_DIR / "imports" / "zoho" / "raw"
    if real.exists():
        assert real.is_dir()
    assert FIXTURE.is_file()


# ---------------------------------------------------------------------------
# Parsing + shape
# ---------------------------------------------------------------------------


def test_synthetic_fixture_parses(tmp_path):
    report = _run(tmp_path)
    assert report["total_rows"] == 9
    assert "ready_for_import_plan" in report
    assert "relationship_resolution" in report
    assert report["relationship_resolution"]["skipped"] is False


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def test_duplicate_record_id_is_detected(tmp_path):
    report = _run(tmp_path)
    dups = report["duplicates"]["record_ids"]
    assert any(d["value"] == "ZP0000000001" for d in dups)
    assert _find_error(report, "duplicate_record_id") is not None


# ---------------------------------------------------------------------------
# Missing required
# ---------------------------------------------------------------------------


def test_missing_contract_id_is_reported(tmp_path):
    report = _run(tmp_path)
    missing = report["missing_required"]["contract_id"]
    assert any(m["record_id"] == "ZP0000000002" for m in missing)
    assert _find_error(report, "missing_contract_id") is not None


def test_missing_owner_warning_reported(tmp_path):
    report = _run(tmp_path)
    w = _find_warning(report, "missing_owner")
    assert w is not None
    assert any(s["record_id"] == "ZP0000000007" for s in w["samples"])


# ---------------------------------------------------------------------------
# Relationship resolution against imported contracts
# ---------------------------------------------------------------------------


def test_unresolved_contract_id_is_reported(tmp_path):
    report = _run(tmp_path)
    err = _find_error(report, "unresolved_contract_id")
    assert err is not None
    # Phantom contract ZC9999999999 (row ZP0000000003) is not in
    # the manifest, so it surfaces here.
    assert any(s["contract_id"] == "ZC9999999999" for s in err["samples"])


def test_account_mismatch_is_reported(tmp_path):
    report = _run(tmp_path)
    w = _find_warning(report, "account_id_mismatch_with_contract")
    assert w is not None
    # Row ZP0000000004 references account ZA9999999999 but the
    # contract's stamped account is ZA0000000001.
    sample = next(s for s in w["samples"] if s["record_id"] == "ZP0000000004")
    assert sample["project_account_id"] == "ZA9999999999"
    assert sample["contract_account_id"] == "ZA0000000001"


def test_deal_mismatch_is_reported(tmp_path):
    report = _run(tmp_path)
    w = _find_warning(report, "deal_id_mismatch_with_contract")
    assert w is not None
    sample = next(s for s in w["samples"] if s["record_id"] == "ZP0000000005")
    assert sample["project_deal_id"] == "ZD9999999998"
    assert sample["contract_deal_id"] == "ZD0000000001"


def test_resolved_count_matches_clean_rows(tmp_path):
    report = _run(tmp_path)
    rr = report["relationship_resolution"]
    # 8 rows carry a Contract.id (everything except the missing-contract
    # row); 1 of those is unresolved (phantom). So 7 resolved.
    assert rr["contracts_resolved"] == 7
    assert rr["contracts_unresolved"] == 1


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


def test_project_status_and_type_enumerations(tmp_path):
    report = _run(tmp_path)
    enums = report["enumerations"]
    assert "Active" in enums["project_statuses"]
    assert "Completed" in enums["project_statuses"]
    assert "On Hold" in enums["project_statuses"]
    assert "Implementation" in enums["project_types"]
    assert "Support" in enums["project_types"]


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_date_parse_warning_for_bad_value(tmp_path):
    report = _run(tmp_path)
    w = _find_warning(report, "date_parse_warning")
    assert w is not None
    assert any(s["raw_value"] == "not-a-date" for s in w["samples"])


# ---------------------------------------------------------------------------
# Readiness gate
# ---------------------------------------------------------------------------


def test_ready_for_import_plan_is_false_with_blockers(tmp_path):
    report = _run(tmp_path)
    # The fixture seeds a duplicate, a missing contract, and an
    # unresolved contract — all errors → not ready.
    assert report["ready_for_import_plan"] is False


# ---------------------------------------------------------------------------
# CSV-only fallback (no manifest, no DB)
# ---------------------------------------------------------------------------


def test_csv_only_mode_skips_relationship_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    out = tmp_path / "report.json"
    report = asyncio.run(zpdr.run(
        csv_path=str(FIXTURE),
        out_path=str(out),
        manifest_path=None,  # no manifest, no DB
    ))
    rr = report["relationship_resolution"]
    assert rr["skipped"] is True
    assert rr["contracts_resolved"] == 0
    assert rr["contracts_unresolved"] == 0
    # The duplicate / missing_contract_id errors are independent of
    # relationship resolution and still fire.
    assert _find_error(report, "duplicate_record_id") is not None
    assert _find_error(report, "missing_contract_id") is not None
    # The unresolved-contract error must NOT fire in CSV-only mode
    # because we have no manifest to compare against.
    assert _find_error(report, "unresolved_contract_id") is None


# ---------------------------------------------------------------------------
# Manifest input shape
# ---------------------------------------------------------------------------


def test_manifest_must_be_a_json_list(tmp_path):
    bad = tmp_path / "bad_manifest.json"
    bad.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    out = tmp_path / "report.json"
    with pytest.raises(RuntimeError):
        asyncio.run(zpdr.run(
            csv_path=str(FIXTURE),
            out_path=str(out),
            manifest_path=str(bad),
        ))


def test_manifest_entries_without_external_contract_id_are_skipped(tmp_path):
    manifest = [
        # No external_contract_id → ignored.
        {"contract_id": "contract_no_ext", "external_ids": {}},
        {
            "contract_id": "contract_alpha_aegisaxiom",
            "external_contract_id": "ZC0000000001",
            "external_ids": {
                "zoho_account_id": "ZA0000000001",
                "zoho_deal_id": "ZD0000000001",
            },
        },
        {
            "contract_id": "contract_beta_aegisaxiom",
            "external_contract_id": "ZC0000000002",
            "external_ids": {
                "zoho_account_id": "ZA0000000002",
                "zoho_deal_id": "ZD0000000002",
            },
        },
    ]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "report.json"
    report = asyncio.run(zpdr.run(
        csv_path=str(FIXTURE),
        out_path=str(out),
        manifest_path=str(path),
    ))
    # The "no external_contract_id" entry was skipped; the map size
    # equals the count of valid entries.
    assert report["relationship_resolution"]["contracts_map_size"] == 2


# ---------------------------------------------------------------------------
# Structural-header guard
# ---------------------------------------------------------------------------


def test_missing_structural_header_fails_loudly(tmp_path):
    src = tmp_path / "broken.csv"
    src.write_text(
        "Record Id,Project Name\nZP0000000099,Synthetic\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.json"
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(zpdr.run(
            csv_path=str(src),
            out_path=str(out),
            manifest_path=None,
        ))
    assert "missing required structural headers" in str(exc.value)


def test_main_returns_non_zero_on_missing_file(tmp_path):
    code = zpdr.main([
        "--csv", str(tmp_path / "does_not_exist.csv"),
        "--out", str(tmp_path / "report.json"),
    ])
    assert code == 1


def test_main_runs_against_synthetic_fixture(tmp_path, capsys):
    out = tmp_path / "report.json"
    manifest = _contracts_manifest(tmp_path)
    code = zpdr.main([
        "--csv", str(FIXTURE),
        "--out", str(out),
        "--contracts-manifest", str(manifest),
    ])
    assert code == 0
    stdout = capsys.readouterr().out
    assert "Zoho Projects Dry-Run Summary" in stdout
    assert "Ready for import plan:" in stdout


# ---------------------------------------------------------------------------
# Safety: no Zoho API, no DB writes
# ---------------------------------------------------------------------------


def test_script_does_not_import_zoho_api():
    src = Path(zpdr.__file__).read_text(encoding="utf-8")
    forbidden = (
        "import httpx",
        "import requests",
        "zoho_api",
    )
    for token in forbidden:
        assert token not in src, (
            f"Projects dry-run must not import {token!r}; this slice is "
            "filesystem + read-only Mongo only."
        )


def test_project_id_alias_is_accepted(tmp_path):
    """The real Zoho Projects export ships ``Project Id`` (not
    ``Project Number``). The validator must accept it without
    requiring a header rewrite.
    """
    csv_text = (
        "Record Id,Project Name,Project Id,Account,Account.id,"
        "Deal,Deal.id,Contract,Contract.id,Status\n"
        "ZP0000000099,Synthetic With ProjectId,PID-0001,"
        "Synthetic Account Alpha,ZA0000000001,"
        "Synthetic Deal Alpha,ZD0000000001,"
        "Synthetic Contract Alpha,ZC0000000001,Active\n"
    )
    src = tmp_path / "with_project_id.csv"
    src.write_text(csv_text, encoding="utf-8")
    out = tmp_path / "report.json"
    report = asyncio.run(zpdr.run(
        csv_path=str(src),
        out_path=str(out),
        manifest_path=str(_contracts_manifest(tmp_path)),
    ))
    # Headers parsed OK and the row resolved against the manifest.
    assert report["total_rows"] == 1
    assert "Project Id" in report["headers_present"]
    assert report["relationship_resolution"]["contracts_resolved"] == 1


def test_total_consumed_hours_apostrophe_variant_accepted(tmp_path):
    """The real export ships ``Total Project Consumed Hour's`` with a
    curly or ASCII apostrophe. The validator must surface the
    populated count and detect negative values.
    """
    csv_text = (
        "Record Id,Project Name,Account,Account.id,Deal,Deal.id,"
        "Contract,Contract.id,Status,Total Project Consumed Hour's\n"
        "ZP0000000101,Synthetic Hours Positive,Synthetic Account Alpha,"
        "ZA0000000001,Synthetic Deal Alpha,ZD0000000001,"
        "Synthetic Contract Alpha,ZC0000000001,Active,42.5\n"
        "ZP0000000102,Synthetic Hours Negative,Synthetic Account Alpha,"
        "ZA0000000001,Synthetic Deal Alpha,ZD0000000001,"
        "Synthetic Contract Alpha,ZC0000000001,Active,-3\n"
        "ZP0000000103,Synthetic Hours Empty,Synthetic Account Alpha,"
        "ZA0000000001,Synthetic Deal Alpha,ZD0000000001,"
        "Synthetic Contract Alpha,ZC0000000001,Active,\n"
        "ZP0000000104,Synthetic Hours Invalid,Synthetic Account Alpha,"
        "ZA0000000001,Synthetic Deal Alpha,ZD0000000001,"
        "Synthetic Contract Alpha,ZC0000000001,Active,not-a-number\n"
    )
    src = tmp_path / "consumed.csv"
    src.write_text(csv_text, encoding="utf-8")
    out = tmp_path / "report.json"
    report = asyncio.run(zpdr.run(
        csv_path=str(src),
        out_path=str(out),
        manifest_path=str(_contracts_manifest(tmp_path)),
    ))
    s = report["summary"]
    # 42.5, -3, and "not-a-number" count as populated cells; the empty
    # row does not. Wait — invalid string never makes it into
    # populated_count because the float() throws before increment.
    # 42.5 + -3 = 2 populated; -3 negative = 1; invalid = 1.
    assert s["consumed_hours_populated_count"] == 2
    assert s["negative_consumed_hours_count"] == 1
    assert s["invalid_consumed_hours_count"] == 1
    assert _find_warning(report, "negative_consumed_hours") is not None
    assert _find_warning(report, "invalid_consumed_hours") is not None


def test_script_does_not_call_db_writers():
    src = Path(zpdr.__file__).read_text(encoding="utf-8")
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
            f"Projects dry-run must not call {token!r}; it is "
            "read-only."
        )
