"""Dry-run validator for a Zoho Projects CSV export.

Fifth slice of the Zoho historical migration. Reads a local Zoho
Projects CSV, validates structural fields, and (optionally) resolves
each row's ``Contract.id`` against the AEGISAXIOM contracts that the prior
slice already imported. NO database writes. NO Zoho API. NO mutation
of any input file.

Run:
    python backend/scripts/zoho_projects_dry_run.py \\
        --csv imports/zoho/raw/Projects.csv \\
        --out imports/zoho/reports/projects_dry_run.json

Optional relationship resolution against imported AEGISAXIOM contracts:

  * Default — when ``MONGO_URL`` / ``DB_NAME`` are in the environment,
    the validator runs a single read-only ``find`` against the
    ``contracts`` collection (filtered to ``import_source: "zoho"``)
    to build a Zoho contract id → imported AEGISAXIOM contract identity
    map.
  * ``--contracts-manifest <file>`` — alternative source for the
    same map. Useful in CI (no Mongo) and tests. The file must be a
    JSON list of objects with ``external_contract_id``,
    ``external_ids`` (with ``zoho_account_id`` / ``zoho_deal_id``),
    and (optional) ``contract_id``.
  * When neither is available the validator runs in CSV-only mode
    and the report's ``relationship_resolution.skipped`` flag is
    ``true``.

Exit codes:
    0 — CSV parsed; report written. Migration warnings/errors in the
        report do NOT flip the exit code.
    1 — malformed CSV or missing required structural headers.

The companion mapping doc is
``docs/zoho-migration/projects_field_mapping.md``.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from typing import Any, Iterable, Optional


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger("zoho_projects_dry_run")


# ---------------------------------------------------------------------------
# Header normalisation — kept consistent with the contracts validators.
# ---------------------------------------------------------------------------

_APOSTROPHE_CHARS = ("'", "’", "ʼ")


def _normalize_header(raw: str) -> str:
    if raw is None:
        return ""
    s = raw.lstrip("﻿").strip()
    for apo in _APOSTROPHE_CHARS:
        s = s.replace(apo, "'")
    return s


# Per-column accepted spellings. Zoho's Projects module ships fields
# under a few different names depending on the view chosen at export
# time; the validator tolerates the common variants without guessing.
_PROJECT_HEADER_VARIANTS: dict[str, tuple[str, ...]] = {
    "record_id":      ("Record Id",),
    "project_name":   ("Project Name", "Name"),
    # ``Project Id`` is Zoho's external project-number field on the
    # real Projects export. ``Project Number`` is documented but
    # absent in practice. Accept both; the project-number-equivalent
    # value lives at whichever column is populated.
    "project_number": ("Project Number", "Project Id"),
    "account_name":   ("Account",),
    "account_id":     ("Account.id",),
    "deal_name":      ("Deal", "Deals", "Opportunity"),
    "deal_id":        ("Deal.id", "Deals.id", "Opportunity.id"),
    "contract_name":  ("Contract",),
    "contract_id":    ("Contract.id",),
    "contract_number": ("Contract Number",),
    "status":         ("Project Status", "Status"),
    "project_type":   ("Project Type", "Type"),
    "owner":          ("Project Owner", "Owner"),
    "start_date":     ("Start Date",),
    "end_date":       ("End Date", "Target End Date"),
    "created_time":   ("Created Time",),
    "modified_time":  ("Modified Time", "Last Activity Time"),
    # Consumed-hours snapshot (apostrophe variants tolerated). AEGISAXIOM
    # recomputes its own hour counters from time entries post-import;
    # this snapshot is for variance reporting against Zoho.
    "consumed_hours": ("Total Project Consumed Hour's", "Total Project Consumed Hours"),
}

_REQUIRED_STRUCTURAL_HEADER_GROUPS: tuple[tuple[str, ...], ...] = (
    _PROJECT_HEADER_VARIANTS["record_id"],
    _PROJECT_HEADER_VARIANTS["project_name"],
    _PROJECT_HEADER_VARIANTS["contract_id"],
)

# How many sample rows to list per disposition bucket in the JSON
# report — same convention as the contracts validators.
_SAMPLE_LIMIT = 50


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------


def _resolve_field(row: dict[str, str], variants: tuple[str, ...]) -> str:
    for v in variants:
        if v in row:
            val = row[v]
            if val is None:
                continue
            return val.strip() if isinstance(val, str) else val
    return ""


# Zoho date columns can land as ``YYYY-MM-DD``, ``YYYY-MM-DD HH:MM:SS``,
# ``MM/DD/YYYY``, or an empty string. Empty is allowed (warned but not
# blocked); anything else that fails to parse becomes a date-warning.
_ACCEPTED_DATE_RE = re.compile(
    r"^("
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|"
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r")$"
)


def _parseable_date(value: str) -> bool:
    """True when the cell is empty OR matches one of the accepted shapes.

    Empty cells are tolerated; the validator surfaces them through the
    ``missing_*`` buckets, not as a parse warning. Unparseable non-empty
    values become a warning so the importer can decide whether to drop
    or coerce.
    """
    if not value:
        return True
    if not _ACCEPTED_DATE_RE.match(value):
        return False
    try:
        # Try ISO first.
        if "/" in value:
            month, day, year = value.split("/")
            if len(year) == 2:
                year = "20" + year
            datetime(int(year), int(month), int(day))
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# CSV reader
# ---------------------------------------------------------------------------


def _read_csv(path: str) -> tuple[list[dict[str, str]], list[str]]:
    if not os.path.isfile(path):
        raise RuntimeError(f"Input file not found: {path!r}")

    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise RuntimeError(
            f"CSV has no header row (empty or malformed): {path!r}"
        )

    headers = [_normalize_header(h) for h in reader.fieldnames]

    missing_groups = [
        group for group in _REQUIRED_STRUCTURAL_HEADER_GROUPS
        if not any(variant in headers for variant in group)
    ]
    if missing_groups:
        raise RuntimeError(
            f"{path!r}: missing required structural headers: "
            f"{[' | '.join(g) for g in missing_groups]}. "
            f"Found: {headers}"
        )

    rows: list[dict[str, str]] = []
    for raw_row in reader:
        norm: dict[str, str] = {}
        for raw_key, val in raw_row.items():
            key = _normalize_header(raw_key) if raw_key is not None else ""
            if key:
                norm[key] = val if val is not None else ""
        rows.append(norm)
    return rows, headers


# ---------------------------------------------------------------------------
# Contracts identity map
# ---------------------------------------------------------------------------


def _load_contracts_manifest(path: str) -> dict[str, dict[str, Any]]:
    """Read a JSON file of imported AEGISAXIOM contracts → identity map.

    Expected shape: list of objects, each carrying at minimum
    ``external_contract_id`` (= Zoho Record Id of the contract). The
    file is what the manifest-mode flag consumes; the live-DB path
    builds the same shape inside ``_load_contracts_from_db``.
    """
    if not os.path.isfile(path):
        raise RuntimeError(f"Manifest file not found: {path!r}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            entries = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Manifest file {path!r} is not valid JSON: {e}",
            ) from e
    if not isinstance(entries, list):
        raise RuntimeError(
            f"Manifest file {path!r} must be a JSON list of objects."
        )
    return _index_contracts(entries)


async def _load_contracts_from_db() -> dict[str, dict[str, Any]] | None:
    """Pull the imported AEGISAXIOM contracts → identity map from Mongo.

    Returns None when ``MONGO_URL`` or ``DB_NAME`` is missing — the
    caller then falls back to CSV-only mode. Read-only: a single
    ``find`` against the ``contracts`` collection filtered to
    ``import_source: "zoho"``.
    """
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        return None
    import database  # type: ignore[import-not-found]

    database.init(mongo_url, db_name)
    db = database.get_db()
    cursor = db.contracts.find(
        {"import_source": "zoho"},
        {
            "_id": 0,
            "contract_id": 1,
            "external_contract_id": 1,
            "external_ids": 1,
            "contract_number": 1,
            "name": 1,
        },
    )
    entries = [doc async for doc in cursor]
    return _index_contracts(entries)


def _index_contracts(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{ext_id: identity}``. Skips entries with no external_contract_id."""
    out: dict[str, dict[str, Any]] = {}
    for e in entries:
        ext_id = e.get("external_contract_id") or (
            (e.get("external_ids") or {}).get("zoho_contract_id")
        )
        if not ext_id:
            continue
        ext = e.get("external_ids") or {}
        out[ext_id] = {
            "contract_id": e.get("contract_id"),
            "external_contract_id": ext_id,
            "zoho_account_id": ext.get("zoho_account_id"),
            "zoho_deal_id": ext.get("zoho_deal_id"),
            "contract_number": e.get("contract_number"),
            "name": e.get("name"),
        }
    return out


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_rows(
    rows: list[dict[str, str]],
    headers_present: list[str],
    source_file: str,
    contracts_by_zoho_id: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Run the dry-run checks against parsed rows + optional contract
    identity map. Returns the full JSON report shape."""
    total = len(rows)
    headers_set = set(headers_present)

    record_ids: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    project_types: Counter[str] = Counter()

    # Consumed-hours snapshot stats. The validator just summarises
    # (count populated / negative count) so the operator can spot
    # whether Zoho's counter is consistent with what AEGISAXIOM will
    # rebuild from time entries post-import. Negative consumed
    # hours are surfaced — same shape as the contracts validator's
    # negative_remaining warning.
    consumed_hours_populated = 0
    consumed_hours_negative: list[dict[str, Any]] = []

    missing_record_id: list[int] = []
    missing_project_name: list[int] = []
    missing_contract_id: list[dict[str, Any]] = []
    missing_deal_id: list[dict[str, Any]] = []
    missing_account_id: list[dict[str, Any]] = []
    missing_owner: list[dict[str, Any]] = []
    missing_created: list[dict[str, Any]] = []
    missing_modified: list[dict[str, Any]] = []

    unresolved_contracts: list[dict[str, Any]] = []
    account_mismatches: list[dict[str, Any]] = []
    deal_mismatches: list[dict[str, Any]] = []

    date_warnings: list[dict[str, Any]] = []
    invalid_consumed_hours: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    relationship_skipped = contracts_by_zoho_id is None
    contracts_resolved_count = 0
    contracts_unresolved_count = 0

    for idx, row in enumerate(rows):
        row_no = idx + 1
        rid = _resolve_field(row, _PROJECT_HEADER_VARIANTS["record_id"])
        pname = _resolve_field(row, _PROJECT_HEADER_VARIANTS["project_name"])
        contract_id = _resolve_field(row, _PROJECT_HEADER_VARIANTS["contract_id"])
        deal_id = _resolve_field(row, _PROJECT_HEADER_VARIANTS["deal_id"])
        account_id = _resolve_field(row, _PROJECT_HEADER_VARIANTS["account_id"])
        status = _resolve_field(row, _PROJECT_HEADER_VARIANTS["status"])
        ptype = _resolve_field(row, _PROJECT_HEADER_VARIANTS["project_type"])
        owner = _resolve_field(row, _PROJECT_HEADER_VARIANTS["owner"])
        created_time = _resolve_field(row, _PROJECT_HEADER_VARIANTS["created_time"])
        modified_time = _resolve_field(row, _PROJECT_HEADER_VARIANTS["modified_time"])
        start_date_raw = _resolve_field(row, _PROJECT_HEADER_VARIANTS["start_date"])
        end_date_raw = _resolve_field(row, _PROJECT_HEADER_VARIANTS["end_date"])

        if not rid:
            missing_record_id.append(row_no)
        else:
            record_ids[rid] += 1

        if not pname:
            missing_project_name.append(row_no)

        if not contract_id:
            missing_contract_id.append({
                "row": row_no,
                "record_id": rid or None,
                "project_name": pname or None,
            })
        if not deal_id:
            missing_deal_id.append({
                "row": row_no, "record_id": rid or None,
            })
        if not account_id:
            missing_account_id.append({
                "row": row_no, "record_id": rid or None,
            })
        if not owner:
            missing_owner.append({"row": row_no, "record_id": rid or None})
        if not created_time:
            missing_created.append({"row": row_no, "record_id": rid or None})
        if not modified_time:
            missing_modified.append({"row": row_no, "record_id": rid or None})

        if status:
            statuses[status] += 1
        if ptype:
            project_types[ptype] += 1

        # Consumed-hours snapshot.
        consumed_raw = _resolve_field(
            row, _PROJECT_HEADER_VARIANTS["consumed_hours"],
        )
        if consumed_raw:
            try:
                consumed_val = float(consumed_raw.replace(",", "").rstrip("%"))
                consumed_hours_populated += 1
                if consumed_val < 0:
                    consumed_hours_negative.append({
                        "row": row_no,
                        "record_id": rid or None,
                        "consumed": consumed_val,
                    })
            except (TypeError, ValueError):
                invalid_consumed_hours.append({
                    "row": row_no,
                    "record_id": rid or None,
                    "raw_value": consumed_raw,
                })

        # Date parse warnings — empty cells are tolerated (handled by
        # the missing_* buckets); only unparseable non-empty values
        # surface here.
        for label, value in (
            ("Start Date", start_date_raw),
            ("End Date", end_date_raw),
            ("Created Time", created_time),
            ("Modified Time", modified_time),
        ):
            if value and not _parseable_date(value):
                date_warnings.append({
                    "row": row_no,
                    "record_id": rid or None,
                    "field": label,
                    "raw_value": value,
                })

        # Relationship resolution (skip when no contract map available).
        if contracts_by_zoho_id is not None and contract_id:
            identity = contracts_by_zoho_id.get(contract_id)
            if identity is None:
                contracts_unresolved_count += 1
                unresolved_contracts.append({
                    "row": row_no,
                    "record_id": rid or None,
                    "project_name": pname or None,
                    "contract_id": contract_id,
                })
            else:
                contracts_resolved_count += 1
                # Cross-check Deal and Account ids against the
                # imported contract's stamped external_ids. Mismatch
                # is a warning — it likely means the project was
                # linked to a different deal/account in Zoho than the
                # contract was.
                expected_deal = identity.get("zoho_deal_id")
                if deal_id and expected_deal and deal_id != expected_deal:
                    deal_mismatches.append({
                        "row": row_no,
                        "record_id": rid or None,
                        "contract_id": contract_id,
                        "project_deal_id": deal_id,
                        "contract_deal_id": expected_deal,
                    })
                expected_account = identity.get("zoho_account_id")
                if account_id and expected_account and account_id != expected_account:
                    account_mismatches.append({
                        "row": row_no,
                        "record_id": rid or None,
                        "contract_id": contract_id,
                        "project_account_id": account_id,
                        "contract_account_id": expected_account,
                    })

    # Duplicates ---------------------------------------------------
    duplicate_record_ids = [
        {"value": v, "count": n}
        for v, n in record_ids.items() if n > 1
    ]

    # Warnings + errors classification
    if missing_record_id:
        errors.append({
            "kind": "missing_record_id",
            "count": len(missing_record_id),
            "rows": missing_record_id[:_SAMPLE_LIMIT],
        })
    if missing_project_name:
        errors.append({
            "kind": "missing_project_name",
            "count": len(missing_project_name),
            "rows": missing_project_name[:_SAMPLE_LIMIT],
        })
    if missing_contract_id:
        errors.append({
            "kind": "missing_contract_id",
            "count": len(missing_contract_id),
            "samples": missing_contract_id[:_SAMPLE_LIMIT],
        })
    if duplicate_record_ids:
        errors.append({
            "kind": "duplicate_record_id",
            "count": len(duplicate_record_ids),
            "samples": duplicate_record_ids[:_SAMPLE_LIMIT],
        })
    if unresolved_contracts:
        errors.append({
            "kind": "unresolved_contract_id",
            "count": len(unresolved_contracts),
            "samples": unresolved_contracts[:_SAMPLE_LIMIT],
        })

    if missing_deal_id:
        warnings.append({
            "kind": "missing_deal_id",
            "count": len(missing_deal_id),
            "samples": missing_deal_id[:_SAMPLE_LIMIT],
        })
    if missing_account_id:
        warnings.append({
            "kind": "missing_account_id",
            "count": len(missing_account_id),
            "samples": missing_account_id[:_SAMPLE_LIMIT],
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
    if account_mismatches:
        warnings.append({
            "kind": "account_id_mismatch_with_contract",
            "count": len(account_mismatches),
            "samples": account_mismatches[:_SAMPLE_LIMIT],
        })
    if deal_mismatches:
        warnings.append({
            "kind": "deal_id_mismatch_with_contract",
            "count": len(deal_mismatches),
            "samples": deal_mismatches[:_SAMPLE_LIMIT],
        })
    if date_warnings:
        warnings.append({
            "kind": "date_parse_warning",
            "count": len(date_warnings),
            "samples": date_warnings[:_SAMPLE_LIMIT],
        })
    if invalid_consumed_hours:
        warnings.append({
            "kind": "invalid_consumed_hours",
            "count": len(invalid_consumed_hours),
            "samples": invalid_consumed_hours[:_SAMPLE_LIMIT],
        })
    if consumed_hours_negative:
        warnings.append({
            "kind": "negative_consumed_hours",
            "count": len(consumed_hours_negative),
            "samples": consumed_hours_negative[:_SAMPLE_LIMIT],
        })

    ready_for_import_plan = not errors

    return {
        "source_file": source_file,
        "total_rows": total,
        "ready_for_import_plan": ready_for_import_plan,
        "summary": {
            "error_count": len(errors),
            "warning_count": len(warnings),
            "duplicate_record_id_count": len(duplicate_record_ids),
            "unresolved_contract_count": len(unresolved_contracts),
            "account_mismatch_count": len(account_mismatches),
            "deal_mismatch_count": len(deal_mismatches),
            "date_warning_count": len(date_warnings),
            "consumed_hours_populated_count": consumed_hours_populated,
            "negative_consumed_hours_count": len(consumed_hours_negative),
            "invalid_consumed_hours_count": len(invalid_consumed_hours),
        },
        "headers_present": headers_present,
        "duplicates": {
            "record_ids": duplicate_record_ids,
        },
        "missing_required": {
            "record_id": missing_record_id,
            "project_name": missing_project_name,
            "contract_id": missing_contract_id,
            "deal_id": missing_deal_id,
            "account_id": missing_account_id,
        },
        "enumerations": {
            "project_statuses": dict(statuses),
            "project_types": dict(project_types),
        },
        "relationship_resolution": {
            "skipped": relationship_skipped,
            "contracts_resolved": contracts_resolved_count,
            "contracts_unresolved": contracts_unresolved_count,
            "account_mismatches": len(account_mismatches),
            "deal_mismatches": len(deal_mismatches),
            "contracts_map_size": (
                0 if contracts_by_zoho_id is None
                else len(contracts_by_zoho_id)
            ),
        },
        "warnings": warnings,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines: list[str] = []
    lines.append("Zoho Projects Dry-Run Summary")
    lines.append("=" * 60)
    lines.append(f"Source file:                {report['source_file']}")
    lines.append(f"Total rows:                 {report['total_rows']}")
    lines.append(
        f"Ready for import plan:      "
        f"{'YES' if report['ready_for_import_plan'] else 'NO'}"
    )
    lines.append("")
    lines.append(f"Errors:                     {s['error_count']}")
    lines.append(f"Warnings:                   {s['warning_count']}")
    lines.append(f"Duplicate Record Ids:       {s['duplicate_record_id_count']}")
    lines.append(f"Unresolved Contract.id:     {s['unresolved_contract_count']}")
    lines.append(f"Account.id mismatches:      {s['account_mismatch_count']}")
    lines.append(f"Deal.id mismatches:         {s['deal_mismatch_count']}")
    lines.append(f"Date parse warnings:        {s['date_warning_count']}")
    rr = report["relationship_resolution"]
    lines.append("")
    lines.append(
        f"Relationship resolution:    "
        f"{'SKIPPED' if rr['skipped'] else 'ran'}"
    )
    if not rr["skipped"]:
        lines.append(f"  Contracts map size:       {rr['contracts_map_size']}")
        lines.append(f"  Contracts resolved:       {rr['contracts_resolved']}")
        lines.append(f"  Contracts unresolved:     {rr['contracts_unresolved']}")
    enums = report["enumerations"]
    if enums["project_statuses"]:
        lines.append("")
        lines.append("Project statuses:")
        for k, v in sorted(enums["project_statuses"].items()):
            lines.append(f"  {k}: {v}")
    if enums["project_types"]:
        lines.append("Project types:")
        for k, v in sorted(enums["project_types"].items()):
            lines.append(f"  {k}: {v}")
    if report["errors"]:
        lines.append("")
        lines.append("Errors:")
        for e in report["errors"]:
            lines.append(f"  - {e['kind']}: count={e.get('count', '?')}")
    if report["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in report["warnings"]:
            lines.append(f"  - {w['kind']}: count={w.get('count', '?')}")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(
    *,
    csv_path: str,
    out_path: str,
    manifest_path: str | None = None,
) -> dict[str, Any]:
    rows, headers = _read_csv(csv_path)

    contracts_map: dict[str, dict[str, Any]] | None
    if manifest_path:
        contracts_map = _load_contracts_manifest(manifest_path)
    else:
        contracts_map = await _load_contracts_from_db()

    report = validate_rows(
        rows, headers,
        source_file=csv_path,
        contracts_by_zoho_id=contracts_map,
    )

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    return report


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run validator for Zoho Projects CSV exports.",
    )
    parser.add_argument("--csv", required=True,
                        help="Path to the Zoho Projects CSV export.")
    parser.add_argument("--out", required=True,
                        help="Destination path for the JSON report.")
    parser.add_argument(
        "--contracts-manifest",
        default=None,
        help=(
            "Optional JSON file with imported AEGISAXIOM contract identities. "
            "When omitted, the validator pulls them from Mongo if "
            "MONGO_URL + DB_NAME are set; otherwise relationship "
            "resolution is skipped."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        report = asyncio.run(run(
            csv_path=args.csv,
            out_path=args.out,
            manifest_path=args.contracts_manifest,
        ))
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
