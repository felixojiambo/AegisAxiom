"""Read-only Projects import-plan generator for Zoho → AEGISAXIOM.

Sixth slice of the Zoho historical migration. Consumes the local
``Projects.csv`` together with the JSON report emitted by
``scripts/zoho_projects_dry_run.py`` and produces a plan of the AEGISAXIOM
project documents that would be upserted by the (still-to-come)
Projects importer. No DB writes. No Zoho API. No mutation of inputs.

Run:
    python backend/scripts/zoho_projects_import_plan.py \\
        --projects backend/imports/zoho/raw/Projects.csv \\
        --dry-run-report backend/imports/zoho/reports/projects_dry_run.json \\
        --out backend/imports/zoho/reports/projects_import_plan.json

The dry-run JSON supplies the relationship-resolution result the
generator needs to decide which rows are planned vs. blocked. The
contract identity comes from the dry-run's
``relationship_resolution`` block plus, optionally, the same Mongo
``contracts`` collection the dry-run already consulted — passed
via ``--contracts-manifest <file>`` for the CI / no-Mongo path.

``--allow-partial`` flips the gate: with the flag, the report's
``ready_for_import`` is True when every planned row is valid even if
blocked rows remain excluded. Without the flag, any blocked row
keeps ``ready_for_import`` False.

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
import sys
from collections import Counter
from typing import Any, Iterable, Optional


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# Reuse the header-variant tables + CSV reader from the dry-run
# validator so the two scripts agree on every alias / required-header
# decision. The dry-run module imports nothing Mongo-side until
# ``_load_contracts_from_db`` is called, so this import stays
# filesystem-only.
from scripts.zoho_projects_dry_run import (  # type: ignore[import-not-found]
    _PROJECT_HEADER_VARIANTS,
    _REQUIRED_STRUCTURAL_HEADER_GROUPS,
    _index_contracts,
    _load_contracts_from_db,
    _load_contracts_manifest,
    _normalize_header,
    _parseable_date,
    _resolve_field,
)


logger = logging.getLogger("zoho_projects_import_plan")


# How many sample rows per disposition bucket — same convention as
# the other migration scripts.
_SAMPLE_LIMIT = 50


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _read_projects_csv(path: str) -> tuple[list[dict[str, str]], list[str]]:
    """Read Projects.csv with the same shape the dry-run produces."""
    if not os.path.isfile(path):
        raise RuntimeError(f"Projects CSV not found: {path!r}")
    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise RuntimeError(
            f"Projects CSV has no header row (empty or malformed): {path!r}"
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


def _load_dry_run_report(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise RuntimeError(f"Dry-run report not found: {path!r}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            doc = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Dry-run report {path!r} is not valid JSON: {e}",
            ) from e
    if not isinstance(doc, dict):
        raise RuntimeError(
            f"Dry-run report {path!r} must be a JSON object."
        )
    return doc


# ---------------------------------------------------------------------------
# Per-row helpers
# ---------------------------------------------------------------------------


def _coerce_consumed_hours(raw: Any) -> tuple[float | None, str | None]:
    """Return ``(value, warning_kind)``.

    ``warning_kind`` is None on success, ``"missing_consumed_hours"``
    when the cell is empty, or ``"invalid_consumed_hours"`` when the
    cell holds a non-numeric token. The two warnings are kept distinct
    so the operator can tell stale snapshot from corrupt input.
    """
    if raw is None:
        return None, "missing_consumed_hours"
    s = str(raw).strip()
    if not s:
        return None, "missing_consumed_hours"
    try:
        return float(s.replace(",", "").rstrip("%")), None
    except ValueError:
        return None, "invalid_consumed_hours"


def _build_aegisaxiom_project_candidate(
    *,
    row: dict[str, str],
    record_id: str,
    project_name: str,
    contract_id_zoho: str,
    deal_id: str,
    account_id: str,
    status_raw: str,
    project_type_raw: str,
    project_number: str,
    contract_identity: dict[str, Any] | None,
    consumed_hours: float | None,
) -> dict[str, Any]:
    """Shape the future AEGISAXIOM project document.

    Mirrors the Contracts import plan's ``aegisaxiom_contract_candidate``
    pattern. The future importer will hand this directly to the
    upsert layer once the additive AEGISAXIOM project fields land.
    """
    return {
        "source": "zoho",
        "import_source": "zoho",
        "external_project_id": record_id,
        "external_ids": {
            "zoho_project_id": record_id,
            "zoho_contract_id": contract_id_zoho or None,
            "zoho_account_id": account_id or None,
            "zoho_deal_id": deal_id or None,
        },
        # Bridge to the imported AEGISAXIOM contract — only present when
        # the relationship resolved.
        "aegisaxiom_contract_id": (
            (contract_identity or {}).get("contract_id")
            if contract_identity else None
        ),
        # User-facing fields the importer will populate verbatim.
        "name": project_name or None,
        "project_number": project_number or None,
        "status_raw": status_raw or None,
        "project_type_raw": project_type_raw or None,
        "account_name": _resolve_field(
            row, _PROJECT_HEADER_VARIANTS["account_name"]
        ) or None,
        "deal_name": _resolve_field(
            row, _PROJECT_HEADER_VARIANTS["deal_name"]
        ) or None,
        "contract_number": (
            (contract_identity or {}).get("contract_number")
            if contract_identity else
            _resolve_field(
                row, _PROJECT_HEADER_VARIANTS["contract_number"]
            ) or None
        ),
        "owner_raw": _resolve_field(
            row, _PROJECT_HEADER_VARIANTS["owner"]
        ) or None,
        "imported_consumed_hours": consumed_hours,
        "source_created_at": _resolve_field(
            row, _PROJECT_HEADER_VARIANTS["created_time"]
        ) or None,
        "source_modified_at": _resolve_field(
            row, _PROJECT_HEADER_VARIANTS["modified_time"]
        ) or None,
        "raw_source": dict(row),
    }


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


def build_import_plan(
    *,
    project_rows: list[dict[str, str]],
    contracts_by_zoho_id: dict[str, dict[str, Any]],
    projects_file: str,
    dry_run_report_path: str,
    allow_partial: bool,
) -> dict[str, Any]:
    """Pure plan builder. Returns the full JSON report shape."""
    planned: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    statuses: Counter[str] = Counter()
    project_types: Counter[str] = Counter()
    record_ids: Counter[str] = Counter()

    # First pass: count record_ids so duplicates can flip to blocked
    # without changing the per-row loop's structure.
    for row in project_rows:
        rid = _resolve_field(row, _PROJECT_HEADER_VARIANTS["record_id"])
        if rid:
            record_ids[rid] += 1
    duplicate_record_ids = {k for k, n in record_ids.items() if n > 1}

    for idx, row in enumerate(project_rows):
        row_no = idx + 1
        record_id = _resolve_field(row, _PROJECT_HEADER_VARIANTS["record_id"])
        project_name = _resolve_field(row, _PROJECT_HEADER_VARIANTS["project_name"])
        contract_id_zoho = _resolve_field(row, _PROJECT_HEADER_VARIANTS["contract_id"])
        deal_id = _resolve_field(row, _PROJECT_HEADER_VARIANTS["deal_id"])
        account_id = _resolve_field(row, _PROJECT_HEADER_VARIANTS["account_id"])
        status_raw = _resolve_field(row, _PROJECT_HEADER_VARIANTS["status"])
        project_type_raw = _resolve_field(row, _PROJECT_HEADER_VARIANTS["project_type"])
        project_number = _resolve_field(row, _PROJECT_HEADER_VARIANTS["project_number"])
        owner = _resolve_field(row, _PROJECT_HEADER_VARIANTS["owner"])
        created_time = _resolve_field(row, _PROJECT_HEADER_VARIANTS["created_time"])
        modified_time = _resolve_field(row, _PROJECT_HEADER_VARIANTS["modified_time"])
        start_date_raw = _resolve_field(row, _PROJECT_HEADER_VARIANTS["start_date"])
        end_date_raw = _resolve_field(row, _PROJECT_HEADER_VARIANTS["end_date"])

        if status_raw:
            statuses[status_raw] += 1
        if project_type_raw:
            project_types[project_type_raw] += 1

        contract_identity = (
            contracts_by_zoho_id.get(contract_id_zoho)
            if contract_id_zoho else None
        )

        # ---- Per-row blockers ---------------------------------------
        row_blockers: list[dict[str, Any]] = []
        row_warnings: list[dict[str, Any]] = []

        if not record_id:
            row_blockers.append({
                "kind": "missing_record_id", "row": row_no,
            })
        elif record_id in duplicate_record_ids:
            row_blockers.append({
                "kind": "duplicate_record_id",
                "zoho_project_id": record_id,
                "row": row_no,
            })
        if not project_name:
            row_blockers.append({"kind": "missing_project_name"})
        if not contract_id_zoho:
            row_blockers.append({"kind": "missing_contract_id"})
        elif contract_identity is None:
            row_blockers.append({
                "kind": "unresolved_contract_id",
                "zoho_contract_id": contract_id_zoho,
            })

        # ---- Per-row warnings ---------------------------------------
        if not deal_id:
            row_warnings.append({"kind": "missing_deal_id"})
        if not account_id:
            row_warnings.append({"kind": "missing_account_id"})
        if not owner:
            row_warnings.append({"kind": "missing_owner"})
        if not created_time:
            row_warnings.append({"kind": "missing_created_time"})
        if not modified_time:
            row_warnings.append({"kind": "missing_modified_time"})
        if not status_raw:
            row_warnings.append({"kind": "missing_status"})
        elif not project_type_raw:
            # Status present but type missing — useful for the
            # importer's type-mapping decision.
            row_warnings.append({"kind": "missing_project_type"})

        # Account.id / Deal.id mismatch with contract — only meaningful
        # when we actually resolved the contract.
        if contract_identity is not None:
            expected_account = contract_identity.get("zoho_account_id")
            if account_id and expected_account and account_id != expected_account:
                row_warnings.append({
                    "kind": "account_id_mismatch_with_contract",
                    "project_account_id": account_id,
                    "contract_account_id": expected_account,
                })
            expected_deal = contract_identity.get("zoho_deal_id")
            if deal_id and expected_deal and deal_id != expected_deal:
                row_warnings.append({
                    "kind": "deal_id_mismatch_with_contract",
                    "project_deal_id": deal_id,
                    "contract_deal_id": expected_deal,
                })

        # Date warnings.
        for label, value in (
            ("Start Date", start_date_raw),
            ("End Date", end_date_raw),
            ("Created Time", created_time),
            ("Modified Time", modified_time),
        ):
            if value and not _parseable_date(value):
                row_warnings.append({
                    "kind": "date_parse_warning",
                    "field": label,
                    "raw_value": value,
                })

        # Consumed-hours snapshot.
        consumed_raw = _resolve_field(
            row, _PROJECT_HEADER_VARIANTS["consumed_hours"],
        )
        consumed_value, consumed_warning = _coerce_consumed_hours(consumed_raw)
        if consumed_warning:
            row_warnings.append({"kind": consumed_warning})

        # ---- Build the candidate (always — even blocked rows keep
        # raw_source so the operator can re-key by hand later).
        candidate = _build_aegisaxiom_project_candidate(
            row=row,
            record_id=record_id,
            project_name=project_name,
            contract_id_zoho=contract_id_zoho,
            deal_id=deal_id,
            account_id=account_id,
            status_raw=status_raw,
            project_type_raw=project_type_raw,
            project_number=project_number,
            contract_identity=contract_identity,
            consumed_hours=consumed_value,
        )

        plan_row: dict[str, Any] = {
            "source": "zoho",
            "zoho_project_id": record_id or None,
            "project_name": project_name or None,
            "contract_id_zoho": contract_id_zoho or None,
            "aegisaxiom_contract_id": candidate["aegisaxiom_contract_id"],
            "account": {
                "zoho_account_id": account_id or None,
                "name": candidate["account_name"],
            },
            "deal": {
                "zoho_deal_id": deal_id or None,
                "name": candidate["deal_name"],
            },
            "aegisaxiom_project_candidate": candidate,
            "warnings": row_warnings,
        }

        if row_blockers:
            blocked.append({**plan_row, "blockers": row_blockers})
        else:
            planned.append(plan_row)

        # Roll per-row warnings into the top-level warnings list with
        # source-row coordinates so the operator can find them later.
        for w in row_warnings:
            warnings.append({
                **w,
                "zoho_project_id": record_id or None,
                "row": row_no,
            })

    # ---- Readiness gate --------------------------------------------
    ready_for_import = (
        (not blocked) if not allow_partial
        else True  # planned rows are all already valid by construction
    )

    # Roll up blocker kinds for the console summary.
    blocker_kinds: Counter[str] = Counter()
    for b in blocked:
        for blk in b.get("blockers", []):
            blocker_kinds[blk["kind"]] += 1

    return {
        "source_files": {
            "projects": projects_file,
            "dry_run_report": dry_run_report_path,
        },
        "options": {"allow_partial": allow_partial},
        "total_rows": len(project_rows),
        "planned_import_count": len(planned),
        "blocked_count": len(blocked),
        "warning_count": len(warnings),
        "ready_for_import": ready_for_import,
        "summary": {
            "planned": len(planned),
            "blocked": len(blocked),
            "warnings": len(warnings),
            "blocker_kinds": dict(blocker_kinds),
            "status_enum": dict(statuses),
            "project_type_enum": dict(project_types),
            "contracts_map_size": len(contracts_by_zoho_id),
        },
        "projects": planned,
        "blocked": blocked,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines: list[str] = []
    lines.append("Zoho Projects Import Plan")
    lines.append("=" * 60)
    sf = report["source_files"]
    lines.append(f"Projects file:        {sf['projects']}")
    lines.append(f"Dry-run report:       {sf['dry_run_report']}")
    lines.append(
        f"Allow partial:        "
        f"{'YES' if report['options']['allow_partial'] else 'no'}"
    )
    lines.append("")
    lines.append(f"Total rows:           {report['total_rows']}")
    lines.append(f"Planned import count: {report['planned_import_count']}")
    lines.append(f"Blocked count:        {report['blocked_count']}")
    lines.append(f"Warning count:        {report['warning_count']}")
    lines.append("")
    lines.append(
        f"Ready for import:     "
        f"{'YES' if report['ready_for_import'] else 'NO'}"
    )
    if s["blocker_kinds"]:
        lines.append("")
        lines.append("Blocker categories:")
        for k, v in sorted(s["blocker_kinds"].items()):
            lines.append(f"  {k}: {v}")
    if s["status_enum"]:
        lines.append("")
        lines.append("Status values:")
        for k, v in sorted(s["status_enum"].items()):
            lines.append(f"  {k}: {v}")
    if s["project_type_enum"]:
        lines.append("Project type values:")
        for k, v in sorted(s["project_type_enum"].items()):
            lines.append(f"  {k}: {v}")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _resolve_contracts_map(
    manifest_path: str | None,
) -> dict[str, dict[str, Any]]:
    """Build the Zoho-contract-id → AEGISAXIOM-contract identity map.

    Precedence:
      1. ``--contracts-manifest`` (CI / no-Mongo path),
      2. live Mongo if ``MONGO_URL`` + ``DB_NAME`` are present,
      3. empty map (every row will block on ``unresolved_contract_id``,
         which is the safe default).
    """
    if manifest_path:
        return _load_contracts_manifest(manifest_path)
    db_map = await _load_contracts_from_db()
    return db_map or {}


async def run(
    *,
    projects_path: str,
    dry_run_report_path: str,
    out_path: str,
    allow_partial: bool,
    manifest_path: str | None = None,
) -> dict[str, Any]:
    rows, _ = _read_projects_csv(projects_path)
    _ = _load_dry_run_report(dry_run_report_path)  # fail-fast presence check
    contracts_map = await _resolve_contracts_map(manifest_path)

    report = build_import_plan(
        project_rows=rows,
        contracts_by_zoho_id=contracts_map,
        projects_file=projects_path,
        dry_run_report_path=dry_run_report_path,
        allow_partial=allow_partial,
    )

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    return report


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Projects import-plan generator (Zoho → AEGISAXIOM).",
    )
    parser.add_argument("--projects", required=True,
                        help="Path to the Zoho Projects CSV export.")
    parser.add_argument(
        "--dry-run-report", required=True,
        help=(
            "Path to the JSON dry-run report produced by "
            "scripts/zoho_projects_dry_run.py."
        ),
    )
    parser.add_argument(
        "--out", required=True,
        help="Destination path for the JSON plan.",
    )
    parser.add_argument(
        "--allow-partial", action="store_true",
        help=(
            "When set, ``ready_for_import`` is True if every planned "
            "row is valid even if blocked rows remain excluded."
        ),
    )
    parser.add_argument(
        "--contracts-manifest", default=None,
        help=(
            "Optional JSON file with imported AEGISAXIOM contract identities "
            "(same shape the dry-run validator accepts). When omitted, "
            "the plan generator falls back to Mongo if MONGO_URL + "
            "DB_NAME are set; otherwise the map is empty and every row "
            "blocks on unresolved_contract_id."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        report = asyncio.run(run(
            projects_path=args.projects,
            dry_run_report_path=args.dry_run_report,
            out_path=args.out,
            allow_partial=args.allow_partial,
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
