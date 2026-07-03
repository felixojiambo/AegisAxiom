"""Tests for the pure Zoho Contract.csv parser.

The May 22, 2026 review surfaced contract fields missing from imports
because the importer + audit/plan + apply scripts each redeclared
their own header tables. ``services/zoho_contract_csv`` is the
single source of truth. These tests pin every field that the
PART 4 apply script depends on.

Synthetic fixtures only — no real customer data.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
from services.zoho_contract_csv import (  # noqa: E402
    ParsedContractRow,
    ZOHO_CONTRACT_HEADERS,
    ZOHO_TO_AEGISAXIOM_CONTRACT_TYPE,
    coerce_bool,
    coerce_float,
    iter_contract_csv_rows,
    normalize_header,
    parse_contract_csv_row,
    read_contract_csv,
    resolve_field,
)


# ---------------------------------------------------------------------------
# Helpers — build a synthetic Zoho row with all the May 22 headers
# ---------------------------------------------------------------------------


def _zoho_row(
    *,
    record_id: str = "zcrm_001",
    contract_number: str = "CN-100008",
    name: str = "Reading Bakery PSA",
    contract_owner: str = "Owner Name",
    contract_owner_id: str = "zcrm_owner_1",
    created_by: str = "Creator Name",
    created_by_id: str = "zcrm_creator_1",
    created_time: str = "2024-04-10 21:47:10",
    last_activity_time: str = "2025-04-29 21:21:51",
    account: str = "Reading Bakery",
    account_id: str = "zcrm_acct_1",
    deals: str = "Reading Bakery Deal",
    deals_id: str = "zcrm_deal_1",
    contract_type: str = "BOH",
    invoice_type: str = "Invoice at signing, due net30",
    number_of_invoices: str = "1",
    bill_on_actual_hours: str = "0",
    hours_to_be_paid: str = "0",
    paid_hours: str = "0",
    ap_issues: str = "",
    total_hours_apos: str = "136",
    total_hours_plain: str = "",
    remaining_hours: str = "1",
    consumed_hours: str = "135",
    consumed_pct: str = "99.26",
    contract_status: str = "Approved",
    status_flag: str = "Active",
    tag: str = "",
    unsubscribed_mode: str = "",
    unsubscribed_time: str = "",
    locked: str = "false",
    connected_to_module: str = "",
    connected_to_id: str = "",
) -> dict[str, str]:
    """All May 22 headers — apostrophe-normalised by caller."""
    return {
        "Record Id": record_id,
        "Contract Name": name,
        "Contract Owner.id": contract_owner_id,
        "Contract Owner": contract_owner,
        "Created By.id": created_by_id,
        "Created By": created_by,
        "Created Time": created_time,
        "Last Activity Time": last_activity_time,
        "Tag": tag,
        "Unsubscribed Mode": unsubscribed_mode,
        "Unsubscribed Time": unsubscribed_time,
        "Locked": locked,
        "Account.id": account_id,
        "Account": account,
        "Total Contract Hours": total_hours_plain,
        "Deals.id": deals_id,
        "Deals": deals,
        "Contract Type": contract_type,
        "Invoice Type": invoice_type,
        "Remaining Hour's": remaining_hours,
        "Contract Number": contract_number,
        "Consumed Hour's %": consumed_pct,
        "Contract Status": contract_status,
        "Total Consumed Hour's": consumed_hours,
        "Number of Invoices": number_of_invoices,
        "Bill On Actual Hours": bill_on_actual_hours,
        "Hours To Be Paid": hours_to_be_paid,
        "Paid Hours": paid_hours,
        "AP issues": ap_issues,
        "Total Contract Hour's": total_hours_apos,
        "Status": status_flag,
        "Connected To.module": connected_to_module,
        "Connected To.id": connected_to_id,
    }


# ---------------------------------------------------------------------------
# Header normalisation + coercion helpers
# ---------------------------------------------------------------------------


def test_apostrophe_variants_normalise_to_ascii():
    assert normalize_header("Total Contract Hour's") == "Total Contract Hour's"
    assert normalize_header("Total Contract Hour’s") == "Total Contract Hour's"
    assert normalize_header("Total Contract Hourʼs") == "Total Contract Hour's"


def test_bom_stripped_from_first_header():
    assert normalize_header("﻿Record Id") == "Record Id"


def test_coerce_float_handles_zoho_quirks():
    assert coerce_float("99.26") == (99.26, False)
    assert coerce_float("99.26%") == (99.26, False)
    assert coerce_float("1,200.5") == (1200.5, False)
    assert coerce_float("") == (None, True)
    assert coerce_float(None) == (None, True)
    # Non-numeric token -> (None, False) so caller can distinguish.
    assert coerce_float("abc") == (None, False)
    # Explicit zero is a value, not blank.
    assert coerce_float("0") == (0.0, False)
    assert coerce_float(0) == (0.0, False)


def test_coerce_bool_handles_zoho_strings():
    assert coerce_bool("true") is True
    assert coerce_bool("True") is True
    assert coerce_bool("yes") is True
    assert coerce_bool("false") is False
    assert coerce_bool("0") is False
    assert coerce_bool("") is None
    assert coerce_bool(None) is None


def test_resolve_field_picks_first_non_empty_variant():
    row = {"Remaining Hours": "", "Remaining Hour's": "5"}
    assert resolve_field(row, ZOHO_CONTRACT_HEADERS["remaining_hours"]) == "5"


# ---------------------------------------------------------------------------
# Identity + headline fields
# ---------------------------------------------------------------------------


def test_parser_maps_record_id_and_contract_number():
    p = parse_contract_csv_row(_zoho_row())
    assert p.record_id == "zcrm_001"
    assert p.contract_number == "CN-100008"
    assert p.name == "Reading Bakery PSA"


def test_parser_warns_on_missing_record_id():
    p = parse_contract_csv_row(_zoho_row(record_id=""))
    assert any(w.get("kind") == "missing_record_id" for w in p.warnings)


def test_parser_warns_on_missing_contract_number():
    p = parse_contract_csv_row(_zoho_row(contract_number=""))
    assert any(w.get("kind") == "missing_contract_number" for w in p.warnings)


# ---------------------------------------------------------------------------
# Owner / creator
# ---------------------------------------------------------------------------


def test_parser_maps_owner_and_creator_fields():
    p = parse_contract_csv_row(_zoho_row())
    assert p.contract_owner_name_raw == "Owner Name"
    assert p.contract_owner_id == "zcrm_owner_1"
    assert p.source_created_by == "Creator Name"
    assert p.created_by_id == "zcrm_creator_1"
    assert p.source_created_at == "2024-04-10 21:47:10"
    assert p.source_last_activity_at == "2025-04-29 21:21:51"


# ---------------------------------------------------------------------------
# Contract type matrix (the documented mapping table)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("zoho,aegisaxiom", [
    ("BOH", "boh"),
    ("BOH and Bill on Actuals", "boh_with_boa"),
    ("Bill on Actuals", "boa"),
    ("Fixed Fee", "fixed_fee"),
    ("Monthly", "monthly"),
    ("Wholesale MRR", "wholesale_mrr"),
    ("SimpliSecure", "simplisecure"),
    ("SimpliComply", "simplicomply"),
    ("TBD 1", "other"),
])
def test_contract_type_mapping(zoho, aegisaxiom):
    p = parse_contract_csv_row(_zoho_row(contract_type=zoho))
    assert p.contract_type == aegisaxiom
    assert p.contract_type_raw == zoho


def test_unknown_contract_type_lands_as_other_with_warning():
    p = parse_contract_csv_row(_zoho_row(contract_type="Novel Untracked Type"))
    assert p.contract_type == "other"
    assert any(
        w.get("kind") == "unknown_contract_type" and w.get("zoho_raw") == "Novel Untracked Type"
        for w in p.warnings
    )


def test_blank_contract_type_lands_as_other_silently():
    """A blank contract type is not flagged as 'unknown' — it's just
    a missing field. The downstream apply script's overwrite rule
    decides whether to push the 'other' value into a AEGISAXIOM contract."""
    p = parse_contract_csv_row(_zoho_row(contract_type=""))
    assert p.contract_type == "other"
    assert not any(w.get("kind") == "unknown_contract_type" for w in p.warnings)


# ---------------------------------------------------------------------------
# Hours — both columns + fallback + negative remaining
# ---------------------------------------------------------------------------


def test_total_hours_apostrophe_preferred_over_plain():
    p = parse_contract_csv_row(_zoho_row(
        total_hours_apos="136", total_hours_plain="200",
    ))
    assert p.imported_total_hours == 136.0
    assert p.imported_total_hours_alt == 200.0


def test_total_hours_falls_back_to_plain_when_apos_blank():
    p = parse_contract_csv_row(_zoho_row(
        total_hours_apos="", total_hours_plain="80",
    ))
    assert p.imported_total_hours == 80.0
    assert p.imported_total_hours_alt == 80.0


def test_both_total_hour_columns_blank_emits_warning():
    p = parse_contract_csv_row(_zoho_row(
        total_hours_apos="", total_hours_plain="",
    ))
    assert p.imported_total_hours is None
    assert p.imported_total_hours_alt is None
    assert any(
        w.get("kind") == "total_hours_blank_both_columns" for w in p.warnings
    )


def test_total_hours_conflict_between_columns_emits_warning():
    p = parse_contract_csv_row(_zoho_row(
        total_hours_apos="100", total_hours_plain="200",
    ))
    assert any(
        w.get("kind") == "total_hours_conflict_between_columns" for w in p.warnings
    )


def test_remaining_hours_supports_negative_values():
    """Zoho uses negative Remaining Hour's for over-consumed contracts.
    The parser must not clamp to zero."""
    p = parse_contract_csv_row(_zoho_row(remaining_hours="-5.5"))
    assert p.imported_remaining_hours == -5.5


def test_explicit_zero_consumed_hours_is_a_value_not_blank():
    """A literal '0' is a valid Zoho value (brand-new contract).
    The parser must preserve it as 0.0, not None."""
    p = parse_contract_csv_row(_zoho_row(consumed_hours="0"))
    assert p.imported_consumed_hours == 0.0


def test_consumed_percent_strips_trailing_percent_sign():
    p = parse_contract_csv_row(_zoho_row(consumed_pct="99.26%"))
    assert p.imported_consumed_percent == 99.26


# ---------------------------------------------------------------------------
# Status preservation
# ---------------------------------------------------------------------------


def test_parser_preserves_contract_status_raw_and_zoho_status_raw():
    p = parse_contract_csv_row(_zoho_row(
        contract_status="Approved", status_flag="Active",
    ))
    assert p.contract_status_raw == "Approved"
    assert p.zoho_status_raw == "Active"


# ---------------------------------------------------------------------------
# Invoice / AR/AP
# ---------------------------------------------------------------------------


def test_invoice_and_ap_fields_carried_verbatim():
    p = parse_contract_csv_row(_zoho_row(
        invoice_type="net30",
        number_of_invoices="2",
        bill_on_actual_hours="3.5",
        hours_to_be_paid="10",
        paid_hours="7.5",
        ap_issues="Late invoice flagged",
    ))
    assert p.invoice_type_raw == "net30"
    assert p.number_of_invoices == 2.0
    assert p.bill_on_actual_hours == 3.5
    assert p.hours_to_be_paid == 10.0
    assert p.paid_hours == 7.5
    assert p.ap_issues_raw == "Late invoice flagged"


# ---------------------------------------------------------------------------
# Zoho metadata
# ---------------------------------------------------------------------------


def test_locked_field_parsed_as_bool():
    assert parse_contract_csv_row(_zoho_row(locked="true")).zoho_locked is True
    assert parse_contract_csv_row(_zoho_row(locked="false")).zoho_locked is False
    assert parse_contract_csv_row(_zoho_row(locked="")).zoho_locked is None


def test_connected_to_fields_carried_verbatim():
    p = parse_contract_csv_row(_zoho_row(
        connected_to_module="Deal", connected_to_id="zcrm_deal_99",
    ))
    assert p.connected_to_module_raw == "Deal"
    assert p.connected_to_id == "zcrm_deal_99"


# ---------------------------------------------------------------------------
# Raw snapshot — every original column preserved
# ---------------------------------------------------------------------------


def test_raw_snapshot_includes_every_original_header():
    """The full row goes under raw_source so a future audit can re-
    derive any field from the CSV without re-reading the file."""
    p = parse_contract_csv_row(_zoho_row())
    # Spot-check a column from each major section.
    for header in (
        "Record Id", "Contract Owner", "Created By.id", "Account.id",
        "Deals", "Contract Type", "Invoice Type",
        "Total Contract Hour's", "Total Consumed Hour's",
        "Remaining Hour's", "Consumed Hour's %",
        "Contract Status", "Status", "AP issues",
        "Bill On Actual Hours", "Hours To Be Paid", "Paid Hours",
        "Number of Invoices", "Last Activity Time", "Locked",
        "Connected To.module", "Connected To.id",
    ):
        assert header in p.raw_source, f"raw_source missing {header!r}"


def test_raw_snapshot_preserves_apostrophe_normalised_keys():
    """The parser receives apostrophe-normalised keys upstream. The
    snapshot uses those exact keys so re-running the audit yields the
    same shape regardless of the source apostrophe glyph."""
    p = parse_contract_csv_row(_zoho_row(total_hours_apos="136"))
    # ASCII apostrophe is the canonical form post-normalize_header.
    assert "Total Contract Hour's" in p.raw_source
    assert p.raw_source["Total Contract Hour's"] == "136"


# ---------------------------------------------------------------------------
# File-level reader
# ---------------------------------------------------------------------------


def _write_csv(tmp_path, rows: list[dict[str, str]]) -> str:
    """Write a CSV with the same header set as the May 22 export."""
    if not rows:
        rows = [_zoho_row()]
    headers = list(_zoho_row().keys())
    path = tmp_path / "Contract.csv"
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(_csv_cell(r.get(h, "")) for h in headers))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _csv_cell(value: str) -> str:
    """Quote-escape commas / quotes."""
    s = str(value)
    if "," in s or '"' in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def test_read_contract_csv_round_trip(tmp_path):
    rows = [
        _zoho_row(record_id="zcrm_1", contract_number="CN-A"),
        _zoho_row(record_id="zcrm_2", contract_number="CN-B"),
    ]
    path = _write_csv(tmp_path, rows)
    parsed = list(iter_contract_csv_rows(path))
    assert len(parsed) == 2
    assert {p.record_id for p in parsed} == {"zcrm_1", "zcrm_2"}


def test_read_contract_csv_raises_on_missing_structural_header(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text(
        "Some Header,Other Header\nvalue,value\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="missing required structural"):
        read_contract_csv(str(path))


def test_read_contract_csv_raises_on_missing_file():
    with pytest.raises(RuntimeError, match="not found"):
        read_contract_csv("/no/such/file/Contract.csv")
