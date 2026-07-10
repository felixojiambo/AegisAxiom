"""Tests for the Zoho Contracts → AEGISAXIOM import-plan generator.

Verifies:
  * Synthetic fixtures produce a plan with the documented shape.
  * Account / deal joins populate names and `resolved` flags.
  * Orphan deals → `orphan_zoho_deal_id` warning; default policy
    blocks the row from `contracts[]`; --allow-orphan-deals reverses
    that.
  * Duplicate source Contract Record Id blocks readiness.
  * Missing structural headers fail loudly (non-zero exit).
  * Numeric hour fields convert cleanly; invalid values produce
    blockers consistent with the contract dry-run validator.
  * Raw source row is preserved under `raw_source`.
  * Script imports nothing that touches the database or Zoho API.
  * Tests don't require `backend/imports/zoho/raw`.
  * Fixtures contain no real customer data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
import scripts.zoho_contracts_import_plan as plan  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"
CONTRACTS_FX = FIXTURES / "zoho_contracts_sample.csv"
ACCOUNTS_FX = FIXTURES / "zoho_accounts_sample.csv"
DEALS_FX = FIXTURES / "zoho_deals_sample.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_with_fixtures(tmp_path, *, allow_orphan_deals=False):
    out = tmp_path / "report.json"
    report = plan.run(
        contracts_path=str(CONTRACTS_FX),
        accounts_path=str(ACCOUNTS_FX),
        deals_path=str(DEALS_FX),
        out_path=str(out),
        allow_orphan_deals=allow_orphan_deals,
    )
    assert out.exists()
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == report
    return report


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _minimal_contracts(rows: list[tuple[str, str, str, str]]) -> str:
    """Build a minimal Contract CSV from (record_id, contract_number,
    account_id, deal_id) tuples."""
    lines = [
        "Record Id,Contract Number,Contract Name,Account,Account.id,Deals,"
        "Deals.id,Contract Type,Invoice Type,Contract Status",
    ]
    for rid, cn, aid, did in rows:
        lines.append(
            f"{rid},{cn},Synthetic Contract {rid},Synthetic Account {aid},"
            f"{aid},Synthetic Deal {did},{did},Block of Hours,Monthly,Active"
        )
    return "\n".join(lines) + "\n"


def _minimal_accounts(ids: list[str]) -> str:
    lines = ["Record Id,Account Name"]
    for aid in ids:
        lines.append(f"{aid},Synthetic Account {aid}")
    return "\n".join(lines) + "\n"


def _minimal_deals(ids: list[str]) -> str:
    lines = ["Record Id,Deal Name"]
    for did in ids:
        lines.append(f"{did},Synthetic Deal {did}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Fixture hygiene
# ---------------------------------------------------------------------------


def test_fixtures_exist_and_are_synthetic():
    for path in (CONTRACTS_FX, ACCOUNTS_FX, DEALS_FX):
        assert path.exists(), f"Missing fixture: {path}"
        text = path.read_text(encoding="utf-8")
        # ZC/ZA/ZD prefixes are not valid Zoho Record Id shapes.
        assert "Synthetic" in text


def test_tests_do_not_require_real_imports_directory():
    """Real CSVs live untracked under backend/imports/zoho/raw; the
    test suite must not depend on them existing."""
    real = _BACKEND_DIR / "imports" / "zoho" / "raw"
    if real.exists():
        assert real.is_dir()
    assert CONTRACTS_FX.is_file()
    assert ACCOUNTS_FX.is_file()
    assert DEALS_FX.is_file()


# ---------------------------------------------------------------------------
# Plan shape
# ---------------------------------------------------------------------------


def test_synthetic_fixture_produces_plan_with_documented_shape(tmp_path):
    report = _run_with_fixtures(tmp_path)
    expected_top_keys = {
        "source_files", "options", "total_contracts",
        "planned_import_count", "blocked_count", "warning_count",
        "ready_for_import", "contracts", "blocked", "warnings",
        "enum_summary", "hour_anomaly_summary",
    }
    assert expected_top_keys <= set(report)
    assert report["total_contracts"] == 11
    assert report["source_files"]["contracts"].endswith(
        "zoho_contracts_sample.csv"
    )


def test_planned_row_carries_documented_fields(tmp_path):
    # Iota (ZC0000000009) is the cleanest row in the shared fixture —
    # no blockers, no anomalies — so the planned-row contract is
    # exercised end-to-end without colliding with the negative /
    # mismatched edge cases on adjacent rows.
    report = _run_with_fixtures(tmp_path)
    sample = next(
        c for c in report["contracts"]
        if c["zoho_contract_id"] == "ZC0000000009"
    )
    assert sample["source"] == "zoho"
    assert sample["account"]["zoho_account_id"] == "ZA0000000009"
    assert sample["account"]["resolved"] is True
    assert sample["deal"]["resolved"] is True
    candidate = sample["aegisaxiom_contract_candidate"]
    assert candidate["external_ids"]["zoho_contract_id"] == "ZC0000000009"
    assert candidate["external_ids"]["zoho_account_id"] == "ZA0000000009"
    assert candidate["external_ids"]["zoho_deal_id"] == "ZD0000000009"
    assert candidate["contract_number"] == "CN-FIX-009"
    assert candidate["raw_source"]["Record Id"] == "ZC0000000009"


def test_account_and_deal_links_resolve(tmp_path):
    report = _run_with_fixtures(tmp_path)
    # Account / deal display names round-trip from the side exports.
    iota = next(
        c for c in report["contracts"]
        if c["zoho_contract_id"] == "ZC0000000009"
    )
    assert iota["account"]["name"] == "Synthetic Account Iota"
    assert iota["deal"]["name"] == "Synthetic Deal Iota"


# ---------------------------------------------------------------------------
# Enum / hour-anomaly summaries
# ---------------------------------------------------------------------------


def test_enum_summary_reports_observed_values(tmp_path):
    report = _run_with_fixtures(tmp_path)
    enums = report["enum_summary"]
    assert "Block of Hours" in enums["contract_types"]
    assert "Net 30" in enums["invoice_types"]
    assert "Active" in enums["statuses"]


def test_hour_anomaly_summary_counts_by_kind(tmp_path):
    report = _run_with_fixtures(tmp_path)
    h = report["hour_anomaly_summary"]
    assert h.get("negative_remaining", 0) >= 1
    assert h.get("total_hours_disagreement", 0) >= 1


# ---------------------------------------------------------------------------
# Orphan-deal policy
# ---------------------------------------------------------------------------


def test_orphan_deal_warning_default_policy_blocks_row(tmp_path):
    # Contract row references a deal that doesn't exist in deals.csv.
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
            ("ZC0000000002", "CN-002", "ZA0000000001", "ZD9999999999"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    report = plan.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "plan.json"),
    )
    assert report["planned_import_count"] == 1
    orphans = [
        b for b in report["blocked"]
        if any(
            blk["kind"] == "orphan_zoho_deal_id_default_policy"
            for blk in b["blockers"]
        )
    ]
    assert len(orphans) == 1
    # Warning carries the orphan_zoho_deal_id signal so the operator
    # can find it without scanning blockers.
    assert any(
        w.get("kind") == "orphan_zoho_deal_id"
        for w in report["warnings"]
    )
    assert report["ready_for_import"] is False


def test_allow_orphan_deals_flips_planned_count(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
            ("ZC0000000002", "CN-002", "ZA0000000001", "ZD9999999999"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    report = plan.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "plan.json"),
        allow_orphan_deals=True,
    )
    assert report["planned_import_count"] == 2
    # Warning still present even when allowed.
    assert any(
        w.get("kind") == "orphan_zoho_deal_id"
        for w in report["warnings"]
    )
    assert report["ready_for_import"] is True
    orphan_row = next(
        c for c in report["contracts"]
        if c["zoho_contract_id"] == "ZC0000000002"
    )
    assert orphan_row["deal"]["resolved"] is False
    # external_ids.zoho_deal_id preserved so the importer can record
    # the original Zoho reference for the orphan.
    assert (
        orphan_row["aegisaxiom_contract_candidate"]["external_ids"]["zoho_deal_id"]
        == "ZD9999999999"
    )


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def test_duplicate_contract_record_id_blocks_readiness(tmp_path):
    report = _run_with_fixtures(tmp_path)
    # The shared synthetic contracts fixture has ZC0000000001 twice.
    dups = [
        b for b in report["blocked"]
        if any(
            blk.get("kind") == "duplicate_contract_record_id"
            for blk in b.get("blockers", [])
        )
    ]
    assert len(dups) == 2
    assert report["ready_for_import"] is False


# ---------------------------------------------------------------------------
# Invalid-numeric blockers
# ---------------------------------------------------------------------------


def test_invalid_numeric_blocks_the_row(tmp_path):
    # ZC0000000007 (Eta) in the shared fixture has total_hours=`not-a-number`.
    report = _run_with_fixtures(tmp_path)
    eta_blocks = [
        b for b in report["blocked"]
        if b["zoho_contract_id"] == "ZC0000000007"
    ]
    assert eta_blocks
    assert any(
        blk["kind"] == "invalid_numeric"
        for blk in eta_blocks[0]["blockers"]
    )


def test_numeric_hour_fields_convert_safely(tmp_path):
    report = _run_with_fixtures(tmp_path)
    iota = next(
        c for c in report["contracts"]
        if c["zoho_contract_id"] == "ZC0000000009"
    )
    cand = iota["aegisaxiom_contract_candidate"]
    # The Iota row carries 75 / 30 / 45 / 60.
    assert cand["total_hours_snapshot"] == 75.0
    assert cand["remaining_hours_snapshot"] == 30.0
    assert cand["consumed_hours_snapshot"] == 45.0
    assert cand["consumed_percent_snapshot"] == 60.0


def test_total_hours_apostrophe_variant_is_primary(tmp_path):
    # Build a row where the apostrophe and plain columns disagree —
    # the candidate should report the apostrophe value.
    contracts_csv = (
        "Record Id,Contract Number,Contract Name,Account,Account.id,"
        "Deals,Deals.id,Contract Type,Invoice Type,Contract Status,"
        "Total Contract Hour's,Total Contract Hours\n"
        "ZC0000000001,CN-001,Synthetic,Synthetic Acct,"
        "ZA0000000001,Synthetic Deal,ZD0000000001,"
        "Block of Hours,Monthly,Active,100,120\n"
    )
    contracts = _write(tmp_path / "contracts.csv", contracts_csv)
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    report = plan.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "plan.json"),
    )
    cand = report["contracts"][0]["aegisaxiom_contract_candidate"]
    assert cand["total_hours_snapshot"] == 100.0


# ---------------------------------------------------------------------------
# Structural-header guard
# ---------------------------------------------------------------------------


def test_missing_structural_header_fails_loudly(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        "Record Id,Contract Name\nZC0000000001,Synthetic\n",
    )
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    with pytest.raises(RuntimeError) as exc:
        plan.run(
            contracts_path=str(contracts),
            accounts_path=str(accounts),
            deals_path=str(deals),
            out_path=str(tmp_path / "plan.json"),
        )
    assert "missing required structural headers" in str(exc.value)


def test_main_exits_non_zero_on_missing_file(tmp_path, capsys):
    code = plan.main([
        "--contracts", str(tmp_path / "nope1.csv"),
        "--accounts", str(tmp_path / "nope2.csv"),
        "--deals", str(tmp_path / "nope3.csv"),
        "--out", str(tmp_path / "plan.json"),
    ])
    assert code == 1


def test_main_prints_console_summary(tmp_path, capsys):
    code = plan.main([
        "--contracts", str(CONTRACTS_FX),
        "--accounts", str(ACCOUNTS_FX),
        "--deals", str(DEALS_FX),
        "--out", str(tmp_path / "plan.json"),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "Zoho Contracts Import Plan" in out
    assert "Planned import count:" in out
    assert "Ready for import:" in out


# ---------------------------------------------------------------------------
# Raw-source preservation
# ---------------------------------------------------------------------------


def test_raw_source_preserves_every_column(tmp_path):
    report = _run_with_fixtures(tmp_path)
    candidate = report["contracts"][0]["aegisaxiom_contract_candidate"]
    raw = candidate["raw_source"]
    # Every header from the fixture should round-trip into raw_source
    # so the future importer's reconciliation job can rebuild any
    # field without re-reading the CSV.
    expected_columns = {
        "Record Id", "Contract Number", "Contract Name",
        "Account", "Account.id", "Deals", "Deals.id",
        "Contract Type", "Invoice Type", "Contract Status",
        "Total Contract Hour's", "Total Contract Hours",
        "Remaining Hour's", "Total Consumed Hour's",
        "Consumed Hour's %", "Contract Owner", "Created Time",
        "Modified Time",
    }
    assert expected_columns <= set(raw)


# ---------------------------------------------------------------------------
# Safety: no DB, no Zoho API
# ---------------------------------------------------------------------------


def test_script_does_not_import_database_or_zoho_api():
    src = Path(plan.__file__).read_text(encoding="utf-8")
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
            f"Import plan must not import {token!r}; this slice is "
            "filesystem-only."
        )


def test_script_does_not_call_db_writers():
    src = Path(plan.__file__).read_text(encoding="utf-8")
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
            f"Import plan must not call {token!r}; it is read-only."
        )


# ---------------------------------------------------------------------------
# Deal-export duplicate detection (side warning)
# ---------------------------------------------------------------------------


def test_duplicate_deal_record_id_surfaces_warning(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    deals = _write(
        tmp_path / "deals.csv",
        "Record Id,Deal Name\n"
        "ZD0000000001,Synthetic Deal Alpha\n"
        "ZD0000000001,Synthetic Deal Alpha Duplicate\n",
    )
    report = plan.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "plan.json"),
    )
    assert any(
        w.get("kind") == "duplicate_deal_record_id_in_deals_export"
        for w in report["warnings"]
    )
