"""Pure parser for a single Zoho Contract.csv row.

Shared by the audit, plan, and apply scripts so the field tables and
the contract-type mapping can never drift between them. The parser
is intentionally side-effect free: it reads a normalised dict-row
and returns a structured ``ParsedContractRow``. All three scripts
(audit/plan/apply) call ``parse_contract_csv_row`` for a single row
plus ``iter_contract_csv_rows`` / ``read_contract_csv`` for the
on-disk file.

May 22, 2026 review surfaced that the importer + audit/plan scripts
each redeclared header tables, with minor drift between them. This
module is the single source of truth.

Stays read-only:

  * No DB writes.
  * No Zoho API calls.
  * No file writes (the parser does not write the report file —
    that's the caller's job).
"""
from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Header normalisation
# ---------------------------------------------------------------------------

# Apostrophe characters Zoho exports use interchangeably. Normalise to
# ASCII so curly apostrophes don't double-count or hide a column.
_APOSTROPHE_CHARS = ("'", "’", "ʼ")


def normalize_header(raw: str | None) -> str:
    """Strip BOM, trim whitespace, fold every apostrophe variant to
    ASCII. Two headers that differ only in their apostrophe glyph
    are treated as the same column after this pass."""
    if raw is None:
        return ""
    s = raw.lstrip("﻿").strip()
    for apo in _APOSTROPHE_CHARS:
        s = s.replace(apo, "'")
    return s


# Each canonical key maps to a tuple of accepted Zoho header spellings.
# Order matters: the resolver picks the first non-empty cell in the
# row. ``total_hours_apostrophe`` is preferred over the plain variant
# because the May 22 export uses the apostrophe form.
ZOHO_CONTRACT_HEADERS: dict[str, tuple[str, ...]] = {
    # Identity
    "record_id":             ("Record Id",),
    "contract_number":       ("Contract Number",),
    "contract_name":         ("Contract Name",),
    # Owner / creator
    "contract_owner":        ("Contract Owner",),
    "contract_owner_id":     ("Contract Owner.id",),
    "created_by":            ("Created By",),
    "created_by_id":         ("Created By.id",),
    "created_time":          ("Created Time",),
    "last_activity_time":    ("Last Activity Time",),
    # Account / deal
    "account_name":          ("Account",),
    "account_id":            ("Account.id",),
    "deal_name":             ("Deals", "Deal"),
    "deal_id":               ("Deals.id", "Deal.id"),
    # Contract type / invoice / AR/AP
    "contract_type":         ("Contract Type",),
    "invoice_type":          ("Invoice Type",),
    "number_of_invoices":    ("Number of Invoices",),
    "bill_on_actual_hours":  ("Bill On Actual Hours", "Bill on Actual Hours"),
    "hours_to_be_paid":      ("Hours To Be Paid", "Hours to be Paid"),
    "paid_hours":            ("Paid Hours",),
    "ap_issues":             ("AP issues", "AP Issues"),
    # Hours — apostrophe is the preferred form; plain is a fallback.
    "total_hours_apos":      ("Total Contract Hour's",),
    "total_hours_plain":     ("Total Contract Hours",),
    "remaining_hours":       ("Remaining Hour's", "Remaining Hours"),
    "consumed_hours":        ("Total Consumed Hour's", "Total Consumed Hours"),
    "consumed_pct":          ("Consumed Hour's %", "Consumed Hours %"),
    # Status
    "contract_status":       ("Contract Status",),
    "status_flag":           ("Status",),
    # Zoho metadata
    "tag":                   ("Tag",),
    "unsubscribed_mode":     ("Unsubscribed Mode",),
    "unsubscribed_time":     ("Unsubscribed Time",),
    "locked":                ("Locked",),
    "connected_to_module":   ("Connected To.module", "Connected To.Module"),
    "connected_to_id":       ("Connected To.id",),
}


# Structural headers — the parser refuses to process a CSV that's
# missing any of these because every downstream rule reads them.
REQUIRED_STRUCTURAL_HEADERS: tuple[str, ...] = (
    "Record Id",
    "Contract Number",
    "Contract Name",
)


# ---------------------------------------------------------------------------
# Contract-type mapping (single source of truth)
# ---------------------------------------------------------------------------

# Zoho raw value -> AEGISAXIOM canonical contract_type enum. Anything not
# in this table lands as "other" with a warning emitted to the
# parser's warnings list. Values copy-pasted verbatim from the May 22
# parity review.
ZOHO_TO_AEGISAXIOM_CONTRACT_TYPE: dict[str, str] = {
    "BOH":                      "boh",
    "BOH and Bill on Actuals":  "boh_with_boa",
    "Bill on Actuals":          "boa",
    "Fixed Fee":                "fixed_fee",
    "Monthly":                  "monthly",
    "Wholesale MRR":            "wholesale_mrr",
    "SimpliSecure":             "simplisecure",
    "SimpliComply":             "simplicomply",
    "TBD 1":                    "other",
}


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def coerce_float(raw: Any) -> tuple[Optional[float], bool]:
    """Parse a Zoho numeric cell. Returns ``(value, was_blank)``.

    ``value`` is None when the cell holds a non-numeric token;
    ``was_blank`` is True when the cell was missing / blank so the
    caller can distinguish "no value supplied" from a real zero.
    """
    if raw is None:
        return None, True
    if isinstance(raw, (int, float)):
        return float(raw), False
    s = str(raw).strip()
    if not s:
        return None, True
    s2 = s.replace(",", "").rstrip("%").strip()
    try:
        return float(s2), False
    except ValueError:
        return None, False


def coerce_bool(raw: Any) -> Optional[bool]:
    """Parse a Zoho boolean cell. Returns None for blank / unknown."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in ("true", "yes", "y", "1"):
        return True
    if s in ("false", "no", "n", "0"):
        return False
    return None


def resolve_field(row: dict[str, str], variants: tuple[str, ...]) -> str:
    """First non-empty cell from any of the accepted header spellings.
    Empty string when none of the variants exist or all are blank."""
    for v in variants:
        if v in row:
            val = row[v]
            if val is None:
                continue
            s = val.strip() if isinstance(val, str) else val
            if s != "":
                return s
    return ""


# ---------------------------------------------------------------------------
# Parsed-row dataclass
# ---------------------------------------------------------------------------


@dataclass
class ParsedContractRow:
    """The structured result of parsing one Zoho Contract.csv row.

    Each field below maps to a AEGISAXIOM-side target field. Hour values use
    Python None for "blank in CSV" and a literal 0.0 for "Zoho
    explicitly wrote 0" — callers must preserve this distinction so
    the apply step can decide whether to hydrate a missing AEGISAXIOM field.
    """

    # Identity
    record_id: str
    contract_number: str
    name: str

    # Owner / creator
    contract_owner_name_raw: str
    contract_owner_id: str
    source_created_by: str
    created_by_id: str
    source_created_at: str
    source_last_activity_at: str

    # Account / deal
    account_name: str
    account_id: str
    deal_name: str
    deal_id: str

    # Contract type
    contract_type_raw: str
    contract_type: str  # mapped AEGISAXIOM enum value

    # Invoice / AR/AP
    invoice_type_raw: str
    number_of_invoices: Optional[float]
    bill_on_actual_hours: Optional[float]
    hours_to_be_paid: Optional[float]
    paid_hours: Optional[float]
    ap_issues_raw: str

    # Hours
    imported_total_hours: Optional[float]
    imported_total_hours_alt: Optional[float]
    imported_remaining_hours: Optional[float]
    imported_consumed_hours: Optional[float]
    imported_consumed_percent: Optional[float]

    # Status
    contract_status_raw: str
    zoho_status_raw: str

    # Metadata
    tags_raw: str
    zoho_unsubscribed_mode: str
    zoho_unsubscribed_at: str
    zoho_locked: Optional[bool]
    connected_to_module_raw: str
    connected_to_id: str

    # Raw snapshot of the entire input row — every original CSV column,
    # apostrophe-normalised, preserved verbatim so callers can re-derive
    # any field later without re-reading the file.
    raw_source: dict[str, Any] = field(default_factory=dict)

    # Per-row warnings the audit/apply scripts surface to the operator.
    warnings: list[dict[str, Any]] = field(default_factory=list)


def _resolve_total_hours(row: dict[str, str], warnings: list) -> tuple[Optional[float], Optional[float]]:
    """Total Contract Hour's is preferred; Total Contract Hours is the
    fallback. Returns (preferred, alt) so callers can stash the
    alternate column separately for audit."""
    apos_raw = resolve_field(row, ZOHO_CONTRACT_HEADERS["total_hours_apos"])
    plain_raw = resolve_field(row, ZOHO_CONTRACT_HEADERS["total_hours_plain"])
    apos_val, apos_blank = coerce_float(apos_raw)
    plain_val, plain_blank = coerce_float(plain_raw)
    # Prefer the apostrophe-form column. Both blank -> None / None.
    preferred = apos_val if not apos_blank else plain_val
    alt = plain_val
    # Warn when both columns are blank — caller may decide that's a
    # missing-data row vs an intentional zero contract.
    if apos_blank and plain_blank:
        warnings.append({
            "kind": "total_hours_blank_both_columns",
            "detail": "Both 'Total Contract Hour\\'s' and 'Total Contract Hours' were blank.",
        })
    elif (not apos_blank and not plain_blank and apos_val is not None and plain_val is not None
          and abs(apos_val - plain_val) > 0.005):
        warnings.append({
            "kind": "total_hours_conflict_between_columns",
            "apos": apos_val,
            "plain": plain_val,
        })
    return preferred, alt


def parse_contract_csv_row(row: dict[str, str]) -> ParsedContractRow:
    """Parse one Zoho Contract.csv row into a ParsedContractRow.

    The input ``row`` must have apostrophe-normalised header keys
    (use ``normalize_header`` upstream). The function is pure: no
    file or DB access, no exceptions for malformed cells — missing /
    unparseable values land as None / "" and may be surfaced as
    warnings.
    """
    warnings: list[dict[str, Any]] = []

    # Identity
    record_id = resolve_field(row, ZOHO_CONTRACT_HEADERS["record_id"])
    contract_number = resolve_field(row, ZOHO_CONTRACT_HEADERS["contract_number"])
    name = resolve_field(row, ZOHO_CONTRACT_HEADERS["contract_name"])
    if not record_id:
        warnings.append({"kind": "missing_record_id"})
    if not contract_number:
        warnings.append({"kind": "missing_contract_number"})

    # Owner / creator
    contract_owner = resolve_field(row, ZOHO_CONTRACT_HEADERS["contract_owner"])
    contract_owner_id = resolve_field(row, ZOHO_CONTRACT_HEADERS["contract_owner_id"])
    created_by = resolve_field(row, ZOHO_CONTRACT_HEADERS["created_by"])
    created_by_id = resolve_field(row, ZOHO_CONTRACT_HEADERS["created_by_id"])
    created_time = resolve_field(row, ZOHO_CONTRACT_HEADERS["created_time"])
    last_activity = resolve_field(row, ZOHO_CONTRACT_HEADERS["last_activity_time"])

    # Account / deal
    account_name = resolve_field(row, ZOHO_CONTRACT_HEADERS["account_name"])
    account_id = resolve_field(row, ZOHO_CONTRACT_HEADERS["account_id"])
    deal_name = resolve_field(row, ZOHO_CONTRACT_HEADERS["deal_name"])
    deal_id = resolve_field(row, ZOHO_CONTRACT_HEADERS["deal_id"])

    # Contract type
    ctype_raw = resolve_field(row, ZOHO_CONTRACT_HEADERS["contract_type"])
    ctype = ZOHO_TO_AEGISAXIOM_CONTRACT_TYPE.get(ctype_raw, "other")
    if ctype_raw and ctype_raw not in ZOHO_TO_AEGISAXIOM_CONTRACT_TYPE:
        warnings.append({
            "kind": "unknown_contract_type",
            "zoho_raw": ctype_raw,
            "mapped_to": "other",
        })

    # Invoice / AR/AP
    invoice_type_raw = resolve_field(row, ZOHO_CONTRACT_HEADERS["invoice_type"])
    number_of_invoices, _ = coerce_float(
        resolve_field(row, ZOHO_CONTRACT_HEADERS["number_of_invoices"]),
    )
    bill_on_actual_hours, _ = coerce_float(
        resolve_field(row, ZOHO_CONTRACT_HEADERS["bill_on_actual_hours"]),
    )
    hours_to_be_paid, _ = coerce_float(
        resolve_field(row, ZOHO_CONTRACT_HEADERS["hours_to_be_paid"]),
    )
    paid_hours, _ = coerce_float(
        resolve_field(row, ZOHO_CONTRACT_HEADERS["paid_hours"]),
    )
    ap_issues_raw = resolve_field(row, ZOHO_CONTRACT_HEADERS["ap_issues"])

    # Hours — apostrophe-preferred + alt
    imported_total_hours, imported_total_hours_alt = _resolve_total_hours(row, warnings)
    imported_remaining_hours, _ = coerce_float(
        resolve_field(row, ZOHO_CONTRACT_HEADERS["remaining_hours"]),
    )
    imported_consumed_hours, _ = coerce_float(
        resolve_field(row, ZOHO_CONTRACT_HEADERS["consumed_hours"]),
    )
    imported_consumed_percent, _ = coerce_float(
        resolve_field(row, ZOHO_CONTRACT_HEADERS["consumed_pct"]),
    )

    # Status
    contract_status_raw = resolve_field(row, ZOHO_CONTRACT_HEADERS["contract_status"])
    zoho_status_raw = resolve_field(row, ZOHO_CONTRACT_HEADERS["status_flag"])

    # Metadata
    tags_raw = resolve_field(row, ZOHO_CONTRACT_HEADERS["tag"])
    unsubscribed_mode = resolve_field(row, ZOHO_CONTRACT_HEADERS["unsubscribed_mode"])
    unsubscribed_time = resolve_field(row, ZOHO_CONTRACT_HEADERS["unsubscribed_time"])
    zoho_locked = coerce_bool(resolve_field(row, ZOHO_CONTRACT_HEADERS["locked"]))
    connected_to_module = resolve_field(row, ZOHO_CONTRACT_HEADERS["connected_to_module"])
    connected_to_id = resolve_field(row, ZOHO_CONTRACT_HEADERS["connected_to_id"])

    # Raw snapshot — preserve every original column. Apostrophe glyphs
    # already normalised upstream; we copy by key so the snapshot is a
    # plain dict[str, str] safe to JSON-serialise.
    raw_snapshot = {k: (v if v is not None else "") for k, v in row.items() if k}

    return ParsedContractRow(
        record_id=record_id,
        contract_number=contract_number,
        name=name,
        contract_owner_name_raw=contract_owner,
        contract_owner_id=contract_owner_id,
        source_created_by=created_by,
        created_by_id=created_by_id,
        source_created_at=created_time,
        source_last_activity_at=last_activity,
        account_name=account_name,
        account_id=account_id,
        deal_name=deal_name,
        deal_id=deal_id,
        contract_type_raw=ctype_raw,
        contract_type=ctype,
        invoice_type_raw=invoice_type_raw,
        number_of_invoices=number_of_invoices,
        bill_on_actual_hours=bill_on_actual_hours,
        hours_to_be_paid=hours_to_be_paid,
        paid_hours=paid_hours,
        ap_issues_raw=ap_issues_raw,
        imported_total_hours=imported_total_hours,
        imported_total_hours_alt=imported_total_hours_alt,
        imported_remaining_hours=imported_remaining_hours,
        imported_consumed_hours=imported_consumed_hours,
        imported_consumed_percent=imported_consumed_percent,
        contract_status_raw=contract_status_raw,
        zoho_status_raw=zoho_status_raw,
        tags_raw=tags_raw,
        zoho_unsubscribed_mode=unsubscribed_mode,
        zoho_unsubscribed_at=unsubscribed_time,
        zoho_locked=zoho_locked,
        connected_to_module_raw=connected_to_module,
        connected_to_id=connected_to_id,
        raw_source=raw_snapshot,
        warnings=warnings,
    )


def read_contract_csv(path: str) -> list[dict[str, str]]:
    """Open the CSV with UTF-8+BOM handling, normalise headers, and
    return apostrophe-normalised row dicts. Raises RuntimeError on
    structural issues so the caller can exit non-zero."""
    if not os.path.isfile(path):
        raise RuntimeError(f"Contract CSV not found: {path!r}")
    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise RuntimeError(f"Contract CSV has no header row: {path!r}")
    normalized_headers = [normalize_header(h) for h in reader.fieldnames]
    missing = [
        h for h in REQUIRED_STRUCTURAL_HEADERS if h not in normalized_headers
    ]
    if missing:
        raise RuntimeError(
            f"Contract CSV {path!r} missing required structural headers: "
            f"{missing}. Found: {normalized_headers}"
        )
    rows: list[dict[str, str]] = []
    for raw in reader:
        norm: dict[str, str] = {}
        for k, v in raw.items():
            if k is None:
                continue
            norm[normalize_header(k)] = v if v is not None else ""
        rows.append(norm)
    return rows


def iter_contract_csv_rows(path: str) -> Iterable[ParsedContractRow]:
    """Streaming convenience wrapper: yields one ParsedContractRow per
    CSV row in file order."""
    for r in read_contract_csv(path):
        yield parse_contract_csv_row(r)
