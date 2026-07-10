"""Tests for the Zoho Contract CSV dry-run validator.

Verifies:
  * The synthetic fixture parses with the apostrophe variants Zoho
    actually emits.
  * Every required dry-run check fires on the seeded edge cases:
    duplicate Record Id, duplicate Contract Number, missing
    Account.id, missing Deals.id, negative remaining hours,
    total-hours disagreement, invalid numeric values, consumed +
    remaining mismatch, missing owner, missing created time.
  * The JSON report carries ``ready_for_import`` plus an
    operator-readable console summary.
  * The script imports nothing that touches the database — no Mongo
    client, no Zoho API, no writes anywhere except the report path.
  * Tests do not require ``imports/zoho/raw`` or any real export.
  * The fixture contains no real customer data.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest


# Importing the script from tests requires the backend dir on sys.path
# so ``scripts.zoho_contracts_dry_run`` resolves. The pytest config
# already includes the backend dir; this matches the pattern used by
# the existing migration-script tests.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
import scripts.zoho_contracts_dry_run as zdr  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "zoho_contracts_sample.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_report(tmp_path) -> dict:
    out = tmp_path / "report.json"
    report = zdr.run(str(FIXTURE), str(out))
    assert out.exists(), "JSON report file should be written"
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == report, "in-memory report must match on-disk file"
    return report


def _find(report_section, kind: str):
    for entry in report_section:
        if entry.get("kind") == kind:
            return entry
    return None


# ---------------------------------------------------------------------------
# Fixture sanity
# ---------------------------------------------------------------------------


def test_fixture_csv_exists_and_is_synthetic():
    assert FIXTURE.exists(), f"Synthetic CSV fixture missing: {FIXTURE}"
    text = FIXTURE.read_text(encoding="utf-8")
    # Belt-and-braces: tests should fail if anyone ever drops a real
    # customer name into this fixture. The synthetic dataset uses
    # "example.invalid" for owner emails and "Synthetic" for every
    # account / deal / contract name.
    assert "example.invalid" in text
    assert "Synthetic" in text
    # No real-world Zoho IDs (Zoho IDs are 19-digit numeric strings
    # like 4623120000023456789). The fixture uses ZC/ZA/ZD prefixes
    # which cannot appear in a real export.
    assert "ZC0000000001" in text


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_script_parses_synthetic_fixture(tmp_path):
    report = _run_report(tmp_path)
    assert report["source_file"].endswith("zoho_contracts_sample.csv")
    assert report["total_rows"] == 11
    # Both apostrophe variants should resolve to coverage entries.
    cov = report["field_coverage"]
    assert "Total Contract Hour's" in cov
    assert "Total Contract Hours" in cov
    assert "Remaining Hour's" in cov
    assert "Consumed Hour's %" in cov


def test_field_coverage_counts_non_empty_cells(tmp_path):
    report = _run_report(tmp_path)
    cov = report["field_coverage"]
    # 11 rows; every row in the fixture supplies Record Id except none
    # — the fixture intentionally has no missing-Record-Id row because
    # the existing edge cases already cover the most painful drift.
    assert cov["Record Id"]["non_empty"] == 11
    # Account.id has one explicitly blank row (Gamma).
    assert cov["Account.id"]["empty"] == 1
    # Deals.id has one explicitly blank row (Delta).
    assert cov["Deals.id"]["empty"] == 1
    # Owner has one blank (Iota); Created Time has one blank (Kappa).
    assert cov["Contract Owner"]["empty"] == 1
    assert cov["Created Time"]["empty"] == 1


# ---------------------------------------------------------------------------
# Required-dry-run checks
# ---------------------------------------------------------------------------


def test_duplicate_record_id_is_detected(tmp_path):
    report = _run_report(tmp_path)
    dups = report["duplicates"]["record_ids"]
    assert any(d["value"] == "ZC0000000001" for d in dups)
    assert report["summary"]["duplicate_record_id_count"] >= 1
    assert _find(report["errors"], "duplicate_record_id") is not None


def test_duplicate_contract_number_is_detected(tmp_path):
    report = _run_report(tmp_path)
    dups = report["duplicates"]["contract_numbers"]
    assert any(d["value"] == "CN-FIX-001" for d in dups)
    assert report["summary"]["duplicate_contract_number_count"] >= 1
    assert _find(report["errors"], "duplicate_contract_number") is not None


def test_missing_account_id_is_reported(tmp_path):
    report = _run_report(tmp_path)
    missing = report["missing_required"]["account_id"]
    assert len(missing) == 1
    assert missing[0]["record_id"] == "ZC0000000003"
    assert _find(report["warnings"], "missing_account_id") is not None


def test_missing_deal_id_is_reported(tmp_path):
    report = _run_report(tmp_path)
    missing = report["missing_required"]["deal_id"]
    assert len(missing) == 1
    assert missing[0]["record_id"] == "ZC0000000004"
    assert _find(report["warnings"], "missing_deal_id") is not None


def test_negative_remaining_hours_reported(tmp_path):
    report = _run_report(tmp_path)
    anomalies = [
        a for a in report["hour_anomalies"]
        if a["kind"] == "negative_remaining"
    ]
    assert len(anomalies) == 1
    assert anomalies[0]["record_id"] == "ZC0000000005"
    assert anomalies[0]["remaining"] == -10.0


def test_total_hours_disagreement_reported(tmp_path):
    report = _run_report(tmp_path)
    anomalies = [
        a for a in report["hour_anomalies"]
        if a["kind"] == "total_hours_disagreement"
    ]
    assert len(anomalies) == 1
    assert anomalies[0]["record_id"] == "ZC0000000006"
    assert anomalies[0]["total_apostrophe"] == 100
    assert anomalies[0]["total_plain"] == 120


def test_invalid_numeric_value_reported(tmp_path):
    report = _run_report(tmp_path)
    invalid = [
        e for e in report["errors"]
        if e.get("kind") == "invalid_numeric"
    ]
    assert any(
        e["record_id"] == "ZC0000000007"
        and e["field"] == "Total Contract Hour's"
        for e in invalid
    )


def test_consumed_plus_remaining_mismatch_reported(tmp_path):
    report = _run_report(tmp_path)
    anomalies = [
        a for a in report["hour_anomalies"]
        if a["kind"] == "consumed_plus_remaining_mismatch"
    ]
    assert any(a["record_id"] == "ZC0000000008" for a in anomalies)
    theta = next(
        a for a in anomalies if a["record_id"] == "ZC0000000008"
    )
    # 30 remaining + 50 consumed = 80, total = 100, delta = -20.
    assert theta["delta"] == -20.0


def test_missing_owner_warning_reported(tmp_path):
    report = _run_report(tmp_path)
    w = _find(report["warnings"], "missing_owner")
    assert w is not None
    assert w["count"] == 1
    assert w["samples"][0]["record_id"] == "ZC0000000009"


def test_missing_created_time_warning_reported(tmp_path):
    report = _run_report(tmp_path)
    w = _find(report["warnings"], "missing_created_time")
    assert w is not None
    assert w["count"] == 1


# ---------------------------------------------------------------------------
# Enumeration extraction
# ---------------------------------------------------------------------------


def test_contract_type_invoice_type_status_enumerations(tmp_path):
    report = _run_report(tmp_path)
    enums = report["enumerations"]
    # The fixture seeds three contract types, three invoice types,
    # two statuses — counts are derived from the rows.
    assert "Block of Hours" in enums["contract_types"]
    assert "Fixed Fee" in enums["contract_types"]
    assert "Burn on Actual" in enums["contract_types"]
    assert "Monthly" in enums["invoice_types"]
    assert "Quarterly" in enums["invoice_types"]
    assert "Net 30" in enums["invoice_types"]
    assert "Active" in enums["statuses"]
    assert "Completed" in enums["statuses"]


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


def test_report_contains_ready_for_import(tmp_path):
    report = _run_report(tmp_path)
    assert "ready_for_import" in report
    # The fixture has duplicate Record Id and duplicate Contract
    # Number, both of which are fatal errors → not ready.
    assert report["ready_for_import"] is False


def test_summary_counts_align_with_lists(tmp_path):
    report = _run_report(tmp_path)
    s = report["summary"]
    assert s["error_count"] == len(report["errors"])
    assert s["warning_count"] == len(report["warnings"])
    assert s["duplicate_record_id_count"] == \
        len(report["duplicates"]["record_ids"])
    assert s["duplicate_contract_number_count"] == \
        len(report["duplicates"]["contract_numbers"])
    assert s["hour_anomaly_count"] == len(report["hour_anomalies"])


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def test_console_summary_is_produced(tmp_path):
    report = _run_report(tmp_path)
    summary = zdr.format_console_summary(report)
    assert "Zoho Contracts Dry-Run Summary" in summary
    assert "Total rows:" in summary
    assert "Ready for import:" in summary
    assert "Contract Types found:" in summary
    assert "Block of Hours" in summary


def test_main_prints_summary_and_returns_zero(tmp_path, capsys):
    out = tmp_path / "report.json"
    exit_code = zdr.main([
        "--csv", str(FIXTURE),
        "--out", str(out),
    ])
    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Zoho Contracts Dry-Run Summary" in stdout
    assert "Report written to:" in stdout
    assert out.exists()


# ---------------------------------------------------------------------------
# Apostrophe / BOM tolerance
# ---------------------------------------------------------------------------


def test_curly_apostrophe_header_normalises(tmp_path):
    """Real Zoho exports sometimes use U+2019 in the column name. The
    reader must fold that to ASCII so the row data still resolves.
    """
    csv_text = (
        "Record Id,Contract Number,Contract Name,Account,Account.id,"
        "Deals,Deals.id,Contract Type,Invoice Type,Contract Status,"
        "Total Contract Hour’s,Total Contract Hours,"
        "Remaining Hour’s,Total Consumed Hour’s,"
        "Consumed Hour’s %,Contract Owner,Created Time,"
        "Modified Time\n"
        "ZC0000000099,CN-FIX-099,Synthetic Curly Apostrophe Row,"
        "Synthetic Account Curly,ZA0000000099,Synthetic Deal Curly,"
        "ZD0000000099,Block of Hours,Monthly,Active,42,42,12,30,"
        "71.4,fixture.owner@example.invalid,"
        "2024-01-01 00:00:00,2024-01-02 00:00:00\n"
    )
    src = tmp_path / "curly.csv"
    src.write_text(csv_text, encoding="utf-8")
    out = tmp_path / "report.json"
    report = zdr.run(str(src), str(out))
    cov = report["field_coverage"]
    # The reader should still surface coverage under the ASCII key.
    assert cov["Total Contract Hour's"]["present"]
    assert cov["Total Contract Hour's"]["non_empty"] == 1


def test_bom_is_stripped(tmp_path):
    csv_text = (
        "Record Id,Contract Number,Contract Name,Account,Account.id,"
        "Deals,Deals.id,Contract Type,Invoice Type,Contract Status\n"
        "ZC0000000099,CN-FIX-099,Synthetic BOM,Synthetic BOM Account,"
        "ZA0000000099,Synthetic BOM Deal,ZD0000000099,Block of Hours,"
        "Monthly,Active\n"
    )
    src = tmp_path / "bom.csv"
    # Write UTF-8 with BOM explicitly.
    src.write_bytes(b"\xef\xbb\xbf" + csv_text.encode("utf-8"))
    out = tmp_path / "report.json"
    report = zdr.run(str(src), str(out))
    assert report["total_rows"] == 1
    assert report["field_coverage"]["Record Id"]["non_empty"] == 1


# ---------------------------------------------------------------------------
# Structural-header guard
# ---------------------------------------------------------------------------


def test_missing_structural_header_fails_loudly(tmp_path):
    csv_text = (
        "Record Id,Contract Name\n"
        "ZC0000000099,Synthetic Missing Structure\n"
    )
    src = tmp_path / "broken.csv"
    src.write_text(csv_text, encoding="utf-8")
    with pytest.raises(RuntimeError) as exc_info:
        zdr.run(str(src), str(tmp_path / "report.json"))
    assert "missing required structural headers" in str(exc_info.value)


def test_main_returns_non_zero_on_missing_structural_header(tmp_path, capsys):
    src = tmp_path / "broken.csv"
    src.write_text("Record Id\nZC0000000099\n", encoding="utf-8")
    out = tmp_path / "report.json"
    exit_code = zdr.main([
        "--csv", str(src),
        "--out", str(out),
    ])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "missing required structural headers" in err


def test_main_returns_non_zero_when_csv_missing(tmp_path, capsys):
    exit_code = zdr.main([
        "--csv", str(tmp_path / "does_not_exist.csv"),
        "--out", str(tmp_path / "report.json"),
    ])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Safety: no DB writes
# ---------------------------------------------------------------------------


def test_dry_run_script_does_not_import_database_or_zoho_api():
    src = Path(zdr.__file__).read_text(encoding="utf-8")
    # No Mongo client, no Motor, no requests/httpx for Zoho — the
    # dry-run is supposed to be filesystem-only.
    forbidden = (
        "import database",
        "from database",
        "motor.",
        "AsyncIOMotorClient",
        "pymongo",
        "import httpx",
        "import requests",
        "zoho_api",
    )
    for token in forbidden:
        assert token not in src, (
            f"Dry-run script must not import {token!r}; this slice is "
            "filesystem-only."
        )


def test_dry_run_script_does_not_call_db_writers():
    src = Path(zdr.__file__).read_text(encoding="utf-8")
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
            f"Dry-run script must not call {token!r}; it is read-only."
        )


def test_unmappable_rows_only_include_rows_missing_mapping_essentials(tmp_path):
    """A row is `unmappable` only when Record Id / Contract Type /
    Contract Status are absent. The synthetic fixture has none of
    those so this list should be empty — keeps the check honest.
    """
    report = _run_report(tmp_path)
    assert report["summary"]["unmappable_row_count"] == 0
    assert report["unmappable_rows"] == []


# ---------------------------------------------------------------------------
# Repo hygiene
# ---------------------------------------------------------------------------


def test_imports_zoho_raw_directory_is_not_required():
    """The tests must not depend on any external directory existing
    under ``imports/``. Run the validator entirely against the
    fixture; if this test passes we're free to keep the real
    ``imports/zoho/raw/`` untracked.
    """
    repo_root = _BACKEND_DIR.parent
    imports_dir = repo_root / "imports" / "zoho" / "raw"
    assert not imports_dir.exists() or imports_dir.is_dir(), (
        "If imports/zoho/raw exists at all it must be a directory."
    )
    # The test suite must work whether or not the dir exists.
    assert FIXTURE.exists()


def test_fixture_contains_no_real_customer_data():
    """Sanity check the fixture doesn't accidentally pick up real
    customer indicators. If a developer copies real Zoho output into
    this file we want CI to scream.
    """
    text = FIXTURE.read_text(encoding="utf-8")
    # Real Zoho IDs are 19-digit decimal numbers. The fixture uses a
    # ZC/ZA/ZD prefix so any pure 19-digit run would be a leak.
    import re

    long_numeric = re.findall(r"\b\d{19}\b", text)
    assert not long_numeric, (
        f"Fixture must not contain real Zoho IDs (19-digit numbers); "
        f"found: {long_numeric}"
    )
    # Email domains in real exports won't be ``.invalid``. RFC 6761
    # reserves the suffix specifically so tests can never resolve.
    assert "@example.invalid" in text or "@example.com" in text or \
        "@invalid" in text, (
        "Fixture must use a reserved/test email domain for owners."
    )
