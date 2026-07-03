"""Dry-run validator for a Zoho Contract CSV export.

Reads a CSV from the local filesystem (e.g.
``imports/zoho/raw/Contract.csv``) and emits a JSON migration-readiness
report plus a human-readable console summary. NO database connection,
NO Zoho API call, NO database writes — this is purely a read-only
audit that tells us whether the export is import-ready.

Run:
    python backend/scripts/zoho_contracts_dry_run.py \\
        --csv imports/zoho/raw/Contract.csv \\
        --out imports/zoho/reports/contracts_dry_run.json

Exit codes:
    0 — CSV parsed; report written. Migration warnings/errors in the
        report are NOT a non-zero exit (operators read the JSON to
        decide next steps).
    1 — malformed CSV or missing required structural headers. The
        script must fail loudly when it cannot even parse what
        landed.

Designed to run before the actual importer exists. The questions it
answers:

  * What columns are present?
  * Which fields map cleanly to AEGISAXIOM Contracts?
  * Which rows have missing IDs or broken relationships?
  * Which rows have hour/accounting anomalies?
  * What enums (Contract Type / Invoice Type / Status) appear?
  * Are we ready to build the actual importer next?

This is the first slice of the Zoho historical migration. The
companion mapping doc lives at
``docs/zoho-migration/contracts_field_mapping.md``.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Header configuration
# ---------------------------------------------------------------------------
#
# Zoho exports use a curly apostrophe in some installations ("Hour's")
# and a plain one in others ("Hours"). The reader normalises both
# variants to a stable internal key BEFORE the checks run so the
# downstream code never has to think about Unicode.

# Apostrophe characters that may appear in Zoho headers. ASCII single
# quote ('), right single quotation mark (U+2019), and modifier letter
# apostrophe (U+02BC) cover what we have seen in real exports.
_APOSTROPHE_CHARS = ("'", "’", "ʼ")


def _normalize_header(raw: str) -> str:
    """Strip BOM, trim whitespace, fold every apostrophe variant to
    ASCII so two headers that differ only in their apostrophe glyph
    are treated as the same column.
    """
    if raw is None:
        return ""
    s = raw.lstrip("﻿").strip()
    for apo in _APOSTROPHE_CHARS:
        s = s.replace(apo, "'")
    return s


# Each canonical key in the report maps to one or more accepted source
# header spellings. The reader picks the first present variant per row.
_HEADER_VARIANTS: dict[str, tuple[str, ...]] = {
    "record_id":            ("Record Id",),
    "contract_number":      ("Contract Number",),
    "contract_name":        ("Contract Name",),
    "account_name":         ("Account",),
    "account_id":           ("Account.id",),
    "deal_name":            ("Deals",),
    "deal_id":              ("Deals.id",),
    "contract_type":        ("Contract Type",),
    "invoice_type":         ("Invoice Type",),
    "contract_status":      ("Contract Status",),
    "total_hours_apos":     ("Total Contract Hour's",),
    "total_hours_plain":    ("Total Contract Hours",),
    "remaining_hours":      ("Remaining Hour's", "Remaining Hours"),
    "consumed_hours":       ("Total Consumed Hour's", "Total Consumed Hours"),
    "consumed_pct":         ("Consumed Hour's %", "Consumed Hours %"),
    "contract_owner":       ("Contract Owner",),
    "created_time":         ("Created Time",),
    "modified_time":        ("Modified Time",),
}

# Structural headers — the validator refuses to proceed if even one is
# missing because every downstream check assumes the column exists. A
# real Zoho export of the Contract module includes all of these by
# default; if one is absent, the export was either filtered or comes
# from a different module entirely.
_REQUIRED_STRUCTURAL_HEADERS: tuple[str, ...] = (
    "Record Id",
    "Contract Number",
    "Contract Name",
    "Account.id",
    "Deals.id",
    "Contract Type",
    "Invoice Type",
    "Contract Status",
)

# Tolerance for the hours-arithmetic check: |consumed + remaining -
# total| <= 0.05 hours is considered "approximately equal". Anything
# beyond that gets flagged as an accounting anomaly. Five hundredths
# of an hour is three minutes — small enough to absorb Zoho's
# rounding without papering over real drift.
_HOUR_TOLERANCE = 0.05

# How many sample rows to list per disposition bucket in the JSON
# report. Keeps the report small for spot checks; the script still
# counts every offender so totals are accurate.
_SAMPLE_LIMIT = 50


# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------


def _coerce_float(raw: Any) -> tuple[float | None, bool]:
    """Parse a Zoho numeric cell.

    Returns ``(value, was_empty)``. ``value`` is ``None`` when the cell
    holds a non-numeric token (e.g. ``"abc"``); ``was_empty`` is True
    when the cell is blank / missing so the caller can treat absence
    differently from a parse failure.
    """
    if raw is None:
        return None, True
    if isinstance(raw, (int, float)):
        return float(raw), False
    s = str(raw).strip()
    if not s:
        return None, True
    # Zoho percent fields land as "12.34%" or "12.34"; strip a trailing
    # percent sign defensively. Thousands separators ("1,200.5") are
    # rare but tolerated.
    s2 = s.replace(",", "").rstrip("%").strip()
    try:
        return float(s2), False
    except ValueError:
        return None, False


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def _resolve_field(row: dict[str, str], variants: tuple[str, ...]) -> str:
    """Return the first non-empty value from any of the accepted
    header spellings. Empty string when none of the variants exist or
    all are blank.
    """
    for v in variants:
        if v in row:
            val = row[v]
            if val is None:
                continue
            return val.strip() if isinstance(val, str) else val
    return ""


def _read_rows(csv_path: str) -> tuple[list[dict[str, str]], list[str]]:
    """Open the CSV with UTF-8 + BOM handling, normalise headers, and
    return ``(rows, normalized_headers)``.

    Raises ``RuntimeError`` when the CSV cannot be parsed or when any
    structural header is missing. The script's main() converts that
    into a non-zero exit.
    """
    with open(csv_path, "rb") as f:
        raw_bytes = f.read()
    # utf-8-sig strips a BOM if present; pass-through otherwise.
    text = raw_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise RuntimeError(
            "Zoho CSV has no header row — the file is empty or "
            "malformed."
        )

    raw_headers = list(reader.fieldnames)
    normalized = [_normalize_header(h) for h in raw_headers]

    missing_structural = [
        h for h in _REQUIRED_STRUCTURAL_HEADERS if h not in normalized
    ]
    if missing_structural:
        raise RuntimeError(
            f"Zoho CSV is missing required structural headers: "
            f"{missing_structural}. Found: {normalized}"
        )

    rows: list[dict[str, str]] = []
    for raw_row in reader:
        # Re-key each row by the normalised header so lookups elsewhere
        # in this module use the canonical spelling regardless of which
        # apostrophe glyph the export used.
        normalised_row: dict[str, str] = {}
        for raw_key, val in raw_row.items():
            key = _normalize_header(raw_key) if raw_key is not None else ""
            if key:
                normalised_row[key] = val if val is not None else ""
        rows.append(normalised_row)
    return rows, normalized


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _build_field_coverage(
    rows: list[dict[str, str]],
    headers_present: set[str],
) -> dict[str, dict[str, Any]]:
    """For every canonical-key → variant block, count how many rows
    carry a non-empty value via any of its accepted spellings.
    """
    coverage: dict[str, dict[str, Any]] = {}
    total = len(rows)
    for canonical, variants in _HEADER_VARIANTS.items():
        present = any(v in headers_present for v in variants)
        non_empty = 0
        if present:
            for row in rows:
                if _resolve_field(row, variants):
                    non_empty += 1
        empty = total - non_empty if present else 0
        coverage_pct = round((non_empty / total * 100), 2) if total else 0.0
        # Report under the human header (first variant) so operators
        # can match the JSON keys back to what they see in Zoho.
        coverage[variants[0]] = {
            "present": present,
            "non_empty": non_empty,
            "empty": empty,
            "coverage_pct": coverage_pct,
            "variants_accepted": list(variants),
        }
    return coverage


def validate_rows(
    rows: list[dict[str, str]],
    headers_present: list[str],
    source_file: str,
) -> dict[str, Any]:
    """Run every dry-run check against the parsed rows and return the
    full JSON report shape.

    No I/O — pure analysis. The script's main() does the file open and
    the file write; this function is what the tests call directly.
    """
    headers_set = set(headers_present)
    total = len(rows)

    record_ids: Counter[str] = Counter()
    contract_numbers: Counter[str] = Counter()
    contract_types: Counter[str] = Counter()
    invoice_types: Counter[str] = Counter()
    statuses: Counter[str] = Counter()

    missing_record_id: list[int] = []
    missing_contract_number: list[int] = []
    missing_account_id: list[dict[str, Any]] = []
    missing_deal_id: list[dict[str, Any]] = []
    missing_contract_type: list[dict[str, Any]] = []
    missing_invoice_type: list[dict[str, Any]] = []
    missing_contract_status: list[dict[str, Any]] = []
    missing_owner: list[dict[str, Any]] = []
    missing_created: list[dict[str, Any]] = []
    missing_modified: list[dict[str, Any]] = []

    hour_anomalies: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    unmappable_rows: list[dict[str, Any]] = []

    for idx, row in enumerate(rows):
        # 1-based row index for human consumption; data rows start at
        # CSV line 2 (line 1 is the header). Carrying both makes it
        # easy to jump straight to the offending row in a spreadsheet.
        row_no = idx + 1
        line_no = idx + 2

        rid = _resolve_field(row, _HEADER_VARIANTS["record_id"])
        cn = _resolve_field(row, _HEADER_VARIANTS["contract_number"])
        acc_id = _resolve_field(row, _HEADER_VARIANTS["account_id"])
        deal_id = _resolve_field(row, _HEADER_VARIANTS["deal_id"])
        ctype = _resolve_field(row, _HEADER_VARIANTS["contract_type"])
        itype = _resolve_field(row, _HEADER_VARIANTS["invoice_type"])
        cstatus = _resolve_field(row, _HEADER_VARIANTS["contract_status"])
        owner = _resolve_field(row, _HEADER_VARIANTS["contract_owner"])
        created = _resolve_field(row, _HEADER_VARIANTS["created_time"])
        modified = _resolve_field(row, _HEADER_VARIANTS["modified_time"])

        # Required fields ---------------------------------------------
        if not rid:
            missing_record_id.append(row_no)
        else:
            record_ids[rid] += 1

        if not cn:
            missing_contract_number.append(row_no)
        else:
            contract_numbers[cn] += 1

        if not acc_id:
            missing_account_id.append({
                "row": row_no,
                "line": line_no,
                "record_id": rid or None,
                "contract_number": cn or None,
            })
        if not deal_id:
            missing_deal_id.append({
                "row": row_no,
                "line": line_no,
                "record_id": rid or None,
                "contract_number": cn or None,
            })
        if not ctype:
            missing_contract_type.append({
                "row": row_no, "record_id": rid or None,
            })
        else:
            contract_types[ctype] += 1
        if not itype:
            missing_invoice_type.append({
                "row": row_no, "record_id": rid or None,
            })
        else:
            invoice_types[itype] += 1
        if not cstatus:
            missing_contract_status.append({
                "row": row_no, "record_id": rid or None,
            })
        else:
            statuses[cstatus] += 1

        if not owner:
            missing_owner.append({
                "row": row_no, "record_id": rid or None,
            })
        if not created:
            missing_created.append({
                "row": row_no, "record_id": rid or None,
            })
        if not modified:
            missing_modified.append({
                "row": row_no, "record_id": rid or None,
            })

        # Hour fields -------------------------------------------------
        # Both apostrophe-variant total columns participate: we track
        # them separately because the team has not yet decided which
        # is the source of truth.
        total_apos, total_apos_empty = _coerce_float(
            _resolve_field(row, _HEADER_VARIANTS["total_hours_apos"])
        )
        total_plain, total_plain_empty = _coerce_float(
            _resolve_field(row, _HEADER_VARIANTS["total_hours_plain"])
        )
        remaining, remaining_empty = _coerce_float(
            _resolve_field(row, _HEADER_VARIANTS["remaining_hours"])
        )
        consumed, consumed_empty = _coerce_float(
            _resolve_field(row, _HEADER_VARIANTS["consumed_hours"])
        )
        pct, _pct_empty = _coerce_float(
            _resolve_field(row, _HEADER_VARIANTS["consumed_pct"])
        )

        if total_apos is None and not total_apos_empty:
            errors.append({
                "row": row_no, "record_id": rid or None,
                "kind": "invalid_numeric",
                "field": "Total Contract Hour's",
            })
        if total_plain is None and not total_plain_empty:
            errors.append({
                "row": row_no, "record_id": rid or None,
                "kind": "invalid_numeric",
                "field": "Total Contract Hours",
            })
        if remaining is None and not remaining_empty:
            errors.append({
                "row": row_no, "record_id": rid or None,
                "kind": "invalid_numeric",
                "field": "Remaining Hour's",
            })
        if consumed is None and not consumed_empty:
            errors.append({
                "row": row_no, "record_id": rid or None,
                "kind": "invalid_numeric",
                "field": "Total Consumed Hour's",
            })
        if pct is None and not _pct_empty:
            errors.append({
                "row": row_no, "record_id": rid or None,
                "kind": "invalid_numeric",
                "field": "Consumed Hour's %",
            })

        # Negative remaining hours — Zoho should never emit this; it
        # indicates either drift between Zoho's counters and the
        # source-of-truth time entries, or a manual adjustment that
        # pushed the contract past its block. Either way the import
        # needs to decide how to handle it before we land it in AEGISAXIOM.
        if remaining is not None and remaining < 0:
            hour_anomalies.append({
                "row": row_no, "record_id": rid or None,
                "kind": "negative_remaining",
                "remaining": remaining,
            })

        # Total apostrophe vs plain disagreement. Both columns may
        # appear in the same export; the team has not yet decided
        # which to trust, so any disagreement is surfaced.
        if (
            total_apos is not None
            and total_plain is not None
            and abs(total_apos - total_plain) > _HOUR_TOLERANCE
        ):
            hour_anomalies.append({
                "row": row_no, "record_id": rid or None,
                "kind": "total_hours_disagreement",
                "total_apostrophe": total_apos,
                "total_plain": total_plain,
                "delta": round(total_apos - total_plain, 4),
            })

        # consumed + remaining ≈ total — uses whichever total field
        # is populated. When both are present the apostrophe variant
        # wins per the mapping doc; this is documented and revisable.
        ref_total = (
            total_apos if total_apos is not None else total_plain
        )
        if (
            ref_total is not None
            and consumed is not None
            and remaining is not None
        ):
            delta = (consumed + remaining) - ref_total
            if abs(delta) > _HOUR_TOLERANCE:
                hour_anomalies.append({
                    "row": row_no, "record_id": rid or None,
                    "kind": "consumed_plus_remaining_mismatch",
                    "consumed": consumed,
                    "remaining": remaining,
                    "total": ref_total,
                    "delta": round(delta, 4),
                })

        # Mappability — a row cannot be confidently mapped into AEGISAXIOM
        # Contracts without at least: a Record Id (so we can stamp
        # external_contract_id), a Contract Type (so we know which
        # AEGISAXIOM contract_type bucket it lands in), and a Contract
        # Status (so we know whether it's draft / active / completed
        # / cancelled). Missing Account.id is a warning, not a
        # mappability blocker, because the importer can fall back to
        # the Account name field — but no record-id at all means we
        # cannot even deduplicate on re-import.
        if not rid or not ctype or not cstatus:
            unmappable_rows.append({
                "row": row_no,
                "record_id": rid or None,
                "contract_number": cn or None,
                "missing": [
                    name for name, val in (
                        ("Record Id", rid),
                        ("Contract Type", ctype),
                        ("Contract Status", cstatus),
                    ) if not val
                ],
            })

    # Duplicates --------------------------------------------------
    duplicate_record_ids = [
        {"value": v, "count": n}
        for v, n in record_ids.items() if n > 1
    ]
    duplicate_contract_numbers = [
        {"value": v, "count": n}
        for v, n in contract_numbers.items() if n > 1
    ]

    # Relationship warnings — Account.id and Deals.id absences are
    # warnings in this first slice, not errors, because the importer
    # we ship next will have its own resolution strategy (name
    # fallback, manual link queue).
    if missing_account_id:
        warnings.append({
            "kind": "missing_account_id",
            "count": len(missing_account_id),
            "samples": missing_account_id[:_SAMPLE_LIMIT],
        })
    if missing_deal_id:
        warnings.append({
            "kind": "missing_deal_id",
            "count": len(missing_deal_id),
            "samples": missing_deal_id[:_SAMPLE_LIMIT],
        })
    if missing_owner:
        warnings.append({
            "kind": "missing_owner",
            "count": len(missing_owner),
            "samples": missing_owner[:_SAMPLE_LIMIT],
        })
    if missing_created:
        warnings.append({
            "kind": "missing_created_time",
            "count": len(missing_created),
            "samples": missing_created[:_SAMPLE_LIMIT],
        })
    if missing_modified:
        warnings.append({
            "kind": "missing_modified_time",
            "count": len(missing_modified),
            "samples": missing_modified[:_SAMPLE_LIMIT],
        })

    # Errors --------------------------------------------------------
    # Missing Record Id is fatal-by-row: we cannot import a row we
    # can't dedupe. Same for duplicate Record Id within a single
    # export — the importer would have to choose one and discard the
    # rest, which loses information.
    if missing_record_id:
        errors.append({
            "kind": "missing_record_id",
            "count": len(missing_record_id),
            "rows": missing_record_id[:_SAMPLE_LIMIT],
        })
    if missing_contract_number:
        warnings.append({
            "kind": "missing_contract_number",
            "count": len(missing_contract_number),
            "rows": missing_contract_number[:_SAMPLE_LIMIT],
        })
    if missing_contract_type:
        errors.append({
            "kind": "missing_contract_type",
            "count": len(missing_contract_type),
            "samples": missing_contract_type[:_SAMPLE_LIMIT],
        })
    if missing_invoice_type:
        warnings.append({
            "kind": "missing_invoice_type",
            "count": len(missing_invoice_type),
            "samples": missing_invoice_type[:_SAMPLE_LIMIT],
        })
    if missing_contract_status:
        errors.append({
            "kind": "missing_contract_status",
            "count": len(missing_contract_status),
            "samples": missing_contract_status[:_SAMPLE_LIMIT],
        })
    if duplicate_record_ids:
        errors.append({
            "kind": "duplicate_record_id",
            "count": len(duplicate_record_ids),
            "samples": duplicate_record_ids[:_SAMPLE_LIMIT],
        })
    if duplicate_contract_numbers:
        errors.append({
            "kind": "duplicate_contract_number",
            "count": len(duplicate_contract_numbers),
            "samples": duplicate_contract_numbers[:_SAMPLE_LIMIT],
        })

    field_coverage = _build_field_coverage(rows, headers_set)

    error_count = len(errors)
    warning_count = len(warnings)
    ready_for_import = error_count == 0 and not unmappable_rows

    return {
        "source_file": source_file,
        "total_rows": total,
        "ready_for_import": ready_for_import,
        "summary": {
            "error_count": error_count,
            "warning_count": warning_count,
            "duplicate_record_id_count": len(duplicate_record_ids),
            "duplicate_contract_number_count": len(duplicate_contract_numbers),
            "hour_anomaly_count": len(hour_anomalies),
            "unmappable_row_count": len(unmappable_rows),
        },
        "headers_present": headers_present,
        "field_coverage": field_coverage,
        "duplicates": {
            "record_ids": duplicate_record_ids,
            "contract_numbers": duplicate_contract_numbers,
        },
        "missing_required": {
            "record_id": missing_record_id,
            "contract_number": missing_contract_number,
            "account_id": missing_account_id,
            "deal_id": missing_deal_id,
            "contract_type": missing_contract_type,
            "invoice_type": missing_invoice_type,
            "contract_status": missing_contract_status,
        },
        "enumerations": {
            "contract_types": dict(contract_types),
            "invoice_types": dict(invoice_types),
            "statuses": dict(statuses),
        },
        "hour_anomalies": hour_anomalies,
        "unmappable_rows": unmappable_rows,
        "warnings": warnings,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(report: dict[str, Any]) -> str:
    """Render a human-readable, copy-pasteable console block from the
    JSON report. Kept separate so the test suite can call it without
    having to capture stdout.
    """
    s = report["summary"]
    lines: list[str] = []
    lines.append("Zoho Contracts Dry-Run Summary")
    lines.append("=" * 60)
    lines.append(f"Source file:           {report['source_file']}")
    lines.append(f"Total rows:            {report['total_rows']}")
    lines.append(
        f"Ready for import:      "
        f"{'YES' if report['ready_for_import'] else 'NO'}"
    )
    lines.append("")
    lines.append(f"Errors:                {s['error_count']}")
    lines.append(f"Warnings:              {s['warning_count']}")
    lines.append(f"Duplicate Record Ids:  {s['duplicate_record_id_count']}")
    lines.append(
        f"Duplicate Contract Numbers: "
        f"{s['duplicate_contract_number_count']}"
    )
    lines.append(f"Hour anomalies:        {s['hour_anomaly_count']}")
    lines.append(f"Unmappable rows:       {s['unmappable_row_count']}")
    lines.append("")
    enums = report["enumerations"]
    if enums["contract_types"]:
        lines.append("Contract Types found:")
        for k, v in sorted(enums["contract_types"].items()):
            lines.append(f"  {k}: {v}")
    if enums["invoice_types"]:
        lines.append("Invoice Types found:")
        for k, v in sorted(enums["invoice_types"].items()):
            lines.append(f"  {k}: {v}")
    if enums["statuses"]:
        lines.append("Contract Statuses found:")
        for k, v in sorted(enums["statuses"].items()):
            lines.append(f"  {k}: {v}")
    if report["errors"]:
        lines.append("")
        lines.append("Errors:")
        for e in report["errors"]:
            lines.append(
                f"  - {e['kind']}: count={e.get('count', '?')}"
            )
    if report["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in report["warnings"]:
            lines.append(
                f"  - {w['kind']}: count={w.get('count', '?')}"
            )
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(csv_path: str, out_path: str) -> dict[str, Any]:
    """Read ``csv_path``, validate, write the JSON report to
    ``out_path``, and return the in-memory report.

    Raises ``RuntimeError`` for malformed CSV or missing structural
    headers; main() turns that into exit code 1.
    """
    rows, headers = _read_rows(csv_path)
    report = validate_rows(rows, headers, source_file=csv_path)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run validator for Zoho Contract CSV exports.",
    )
    parser.add_argument(
        "--csv", required=True,
        help="Path to the Zoho Contract CSV export.",
    )
    parser.add_argument(
        "--out", required=True,
        help="Destination path for the JSON report.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = run(args.csv, args.out)
    except FileNotFoundError as e:
        print(f"error: input CSV not found: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except csv.Error as e:  # pragma: no cover — defensive
        print(f"error: malformed CSV: {e}", file=sys.stderr)
        return 1

    print(format_console_summary(report))
    print(f"\nReport written to: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
