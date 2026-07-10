"""Tests for the Zoho Contract relationships dry-run validator.

Verifies:
  * Happy path resolves every Contract.Account.id → Accounts.Record Id
    and Contract.Deals.id → Deals.Record Id when the side exports
    cover the universe.
  * Missing account-export row is reported as an unresolved link.
  * Missing deal-export row is reported as an unresolved link.
  * Duplicate Account Record Id in Accounts.csv is reported as an error.
  * Duplicate Deal Record Id in Deals.csv is reported as an error.
  * Missing required structural headers fail loudly with a non-zero
    exit; the script never silently substitutes defaults.
  * The script imports nothing that touches the database or any Zoho
    API client — it is purely filesystem + stdlib.
  * Tests stand alone — they never depend on backend/imports/zoho/raw
    existing.
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
import scripts.zoho_contract_relationships_dry_run as zrel  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"
CONTRACTS_FX = FIXTURES / "zoho_contracts_sample.csv"
ACCOUNTS_FX = FIXTURES / "zoho_accounts_sample.csv"
DEALS_FX = FIXTURES / "zoho_deals_sample.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_with_fixtures(tmp_path):
    out = tmp_path / "report.json"
    report = zrel.run(
        contracts_path=str(CONTRACTS_FX),
        accounts_path=str(ACCOUNTS_FX),
        deals_path=str(DEALS_FX),
        out_path=str(out),
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
    """Build a tiny Contract CSV from (record_id, contract_number,
    account_id, deal_id) tuples. Other Zoho columns are stubbed so the
    structural-header guard passes."""
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


def test_synthetic_fixtures_exist_and_are_synthetic():
    for path in (CONTRACTS_FX, ACCOUNTS_FX, DEALS_FX):
        assert path.exists(), f"Missing fixture: {path}"
        text = path.read_text(encoding="utf-8")
        # ZC/ZA/ZD prefixes; real Zoho IDs are 19-digit numerics.
        assert "Synthetic" in text


def test_tests_do_not_require_real_imports_directory():
    """The validator's test suite must not depend on the real
    ``backend/imports/zoho/raw`` directory. If it exists, it's a
    directory; if it doesn't, the tests must still pass.
    """
    real = _BACKEND_DIR / "imports" / "zoho" / "raw"
    if real.exists():
        assert real.is_dir()
    # And the fixtures we DO depend on must all live under tests/.
    assert ACCOUNTS_FX.is_file()
    assert DEALS_FX.is_file()
    assert CONTRACTS_FX.is_file()


# ---------------------------------------------------------------------------
# Happy path against the in-repo synthetic fixtures
# ---------------------------------------------------------------------------


def test_happy_path_resolves_every_id_with_a_side_export_row(tmp_path):
    """The fixtures are constructed so that every Contract row whose
    Account.id / Deals.id is populated has a matching row in the
    Accounts / Deals exports. Rows whose IDs are intentionally blank
    in the contracts fixture (Gamma → Account.id, Delta → Deals.id)
    should NOT count as unresolved — they have nothing to resolve.
    """
    report = _run_with_fixtures(tmp_path)
    assert report["total_contracts"] == 11
    assert report["total_accounts"] == 9
    assert report["total_deals"] == 9
    # 11 contracts; 10 have an Account.id (Gamma blank) and 10 have a
    # Deals.id (Delta blank). Every one of those resolves.
    assert report["resolved"]["accounts"] == 10
    assert report["resolved"]["deals"] == 10
    # Both-resolved is 9 — every row that has BOTH an account and a
    # deal id (excludes Gamma's missing account and Delta's missing
    # deal).
    assert report["resolved"]["both"] == 9
    assert report["unresolved"]["accounts"] == []
    assert report["unresolved"]["deals"] == []
    assert report["duplicates"]["account_record_ids"] == {}
    assert report["duplicates"]["deal_record_ids"] == {}
    assert report["ready_for_import_plan"] is True


# ---------------------------------------------------------------------------
# Missing-side-export-row scenarios
# ---------------------------------------------------------------------------


def test_missing_account_row_is_reported(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
            ("ZC0000000002", "CN-002", "ZA0000000099", "ZD0000000002"),
            # account ZA0000000099 is missing from the accounts export
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    deals = _write(
        tmp_path / "deals.csv",
        _minimal_deals(["ZD0000000001", "ZD0000000002"]),
    )
    report = zrel.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "report.json"),
    )
    assert report["resolved"]["accounts"] == 1
    assert len(report["unresolved"]["accounts"]) == 1
    miss = report["unresolved"]["accounts"][0]
    assert miss["contract_record_id"] == "ZC0000000002"
    assert miss["account_id"] == "ZA0000000099"
    assert report["ready_for_import_plan"] is False
    # Warning bucket carries the same count.
    w = next(
        w for w in report["warnings"]
        if w["kind"] == "contract_account_id_not_in_accounts_export"
    )
    assert w["count"] == 1


def test_missing_deal_row_is_reported(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
            ("ZC0000000002", "CN-002", "ZA0000000001", "ZD0000000099"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    report = zrel.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "report.json"),
    )
    assert report["resolved"]["deals"] == 1
    assert len(report["unresolved"]["deals"]) == 1
    miss = report["unresolved"]["deals"][0]
    assert miss["deal_id"] == "ZD0000000099"
    assert report["ready_for_import_plan"] is False


def test_row_missing_both_account_and_deal_appears_in_both_buckets(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA9999999999", "ZD9999999999"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    report = zrel.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "report.json"),
    )
    assert len(report["unresolved"]["accounts"]) == 1
    assert len(report["unresolved"]["deals"]) == 1
    assert len(report["unresolved"]["both"]) == 1


# ---------------------------------------------------------------------------
# Duplicate-side-export Record Ids
# ---------------------------------------------------------------------------


def test_duplicate_account_record_id_is_detected(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv",
        "Record Id,Account Name\n"
        "ZA0000000001,Synthetic Account Alpha\n"
        "ZA0000000001,Synthetic Account Alpha Duplicate\n",
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    report = zrel.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "report.json"),
    )
    dup = report["duplicates"]["account_record_ids"]
    assert dup == {"ZA0000000001": 2}
    assert any(
        e["kind"] == "duplicate_account_record_id"
        for e in report["errors"]
    )
    assert report["ready_for_import_plan"] is False


def test_duplicate_deal_record_id_is_detected(tmp_path):
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
    report = zrel.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "report.json"),
    )
    dup = report["duplicates"]["deal_record_ids"]
    assert dup == {"ZD0000000001": 2}
    assert any(
        e["kind"] == "duplicate_deal_record_id"
        for e in report["errors"]
    )
    assert report["ready_for_import_plan"] is False


# ---------------------------------------------------------------------------
# Aliased Record Id columns
# ---------------------------------------------------------------------------


def test_account_export_accepts_account_record_id_alias(tmp_path):
    # Some Zoho views label the id column "Account Record Id" instead
    # of the bare "Record Id". The validator must accept the alias.
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv",
        "Account Record Id,Account Name\nZA0000000001,Synthetic\n",
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    report = zrel.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "report.json"),
    )
    assert report["resolved"]["accounts"] == 1


def test_deal_export_accepts_opportunity_record_id_alias(tmp_path):
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
        "Opportunity Record Id,Opportunity Name\n"
        "ZD0000000001,Synthetic Opportunity\n",
    )
    report = zrel.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts),
        deals_path=str(deals),
        out_path=str(tmp_path / "report.json"),
    )
    assert report["resolved"]["deals"] == 1


# ---------------------------------------------------------------------------
# Structural-header guards
# ---------------------------------------------------------------------------


def test_missing_contracts_header_fails_loudly(tmp_path):
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
        zrel.run(
            contracts_path=str(contracts),
            accounts_path=str(accounts),
            deals_path=str(deals),
            out_path=str(tmp_path / "report.json"),
        )
    assert "missing required structural headers" in str(exc.value)


def test_missing_accounts_record_id_header_fails_loudly(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv",
        "Wrong Header,Account Name\nZA0000000001,X\n",
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    with pytest.raises(RuntimeError):
        zrel.run(
            contracts_path=str(contracts),
            accounts_path=str(accounts),
            deals_path=str(deals),
            out_path=str(tmp_path / "report.json"),
        )


def test_missing_accounts_file_fails_loudly(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
        ]),
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    with pytest.raises(RuntimeError) as exc:
        zrel.run(
            contracts_path=str(contracts),
            accounts_path=str(tmp_path / "does_not_exist.csv"),
            deals_path=str(deals),
            out_path=str(tmp_path / "report.json"),
        )
    assert "Input file not found" in str(exc.value)


def test_missing_deals_file_fails_loudly(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
        ]),
    )
    accounts = _write(
        tmp_path / "accounts.csv", _minimal_accounts(["ZA0000000001"]),
    )
    with pytest.raises(RuntimeError) as exc:
        zrel.run(
            contracts_path=str(contracts),
            accounts_path=str(accounts),
            deals_path=str(tmp_path / "does_not_exist.csv"),
            out_path=str(tmp_path / "report.json"),
        )
    assert "Input file not found" in str(exc.value)


def test_main_exits_non_zero_on_missing_file(tmp_path, capsys):
    code = zrel.main([
        "--contracts", str(tmp_path / "nope_contracts.csv"),
        "--accounts", str(tmp_path / "nope_accounts.csv"),
        "--deals", str(tmp_path / "nope_deals.csv"),
        "--out", str(tmp_path / "report.json"),
    ])
    assert code == 1


# ---------------------------------------------------------------------------
# UTF-8 + apostrophe tolerance
# ---------------------------------------------------------------------------


def test_bom_in_accounts_export_is_handled(tmp_path):
    contracts = _write(
        tmp_path / "contracts.csv",
        _minimal_contracts([
            ("ZC0000000001", "CN-001", "ZA0000000001", "ZD0000000001"),
        ]),
    )
    accounts_path = tmp_path / "accounts.csv"
    accounts_path.write_bytes(
        b"\xef\xbb\xbf" + b"Record Id,Account Name\nZA0000000001,Synthetic\n"
    )
    deals = _write(
        tmp_path / "deals.csv", _minimal_deals(["ZD0000000001"]),
    )
    report = zrel.run(
        contracts_path=str(contracts),
        accounts_path=str(accounts_path),
        deals_path=str(deals),
        out_path=str(tmp_path / "report.json"),
    )
    assert report["resolved"]["accounts"] == 1


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def test_console_summary_is_produced(tmp_path, capsys):
    code = zrel.main([
        "--contracts", str(CONTRACTS_FX),
        "--accounts", str(ACCOUNTS_FX),
        "--deals", str(DEALS_FX),
        "--out", str(tmp_path / "report.json"),
    ])
    assert code == 0
    stdout = capsys.readouterr().out
    assert "Zoho Contract Relationships Dry-Run" in stdout
    assert "Resolved account links:" in stdout
    assert "Ready for import plan:" in stdout
    assert "Report written to:" in stdout


# ---------------------------------------------------------------------------
# Safety: no DB, no Zoho API
# ---------------------------------------------------------------------------


def test_script_does_not_import_database_or_zoho_api():
    src = Path(zrel.__file__).read_text(encoding="utf-8")
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
            f"Relationship validator must not import {token!r}; this "
            "slice is filesystem-only."
        )


def test_script_does_not_call_db_writers():
    src = Path(zrel.__file__).read_text(encoding="utf-8")
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
            f"Relationship validator must not call {token!r}; it is "
            "read-only."
        )
