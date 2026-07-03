"""Read-only audit of fields the Zoho Projects importer did not hydrate.

Tenth slice of the Zoho historical migration. Sibling to
``zoho_contracts_missing_fields_audit.py``. The May 22, 2026 review
showed Zoho Projects carry Project Id, ZOHO Project URL, Project
Owner, Modified Time, deleted, related tasks, and consumed-hours
snapshots that AEGISAXIOM does not yet fully reflect on imported rows.

Strictly read-only:

  * No DB writes.
  * No Zoho API calls.
  * No ``--execute`` flag.

Inputs:
  --aegisaxiom-from-mongo            read import_source='zoho' projects via
                                MONGO_URL + DB_NAME.
  --aegisaxiom-manifest PATH         optional JSON manifest of AEGISAXIOM projects.
  --projects-csv PATH          optional Zoho Projects export.
  --tasks-csv PATH             optional Zoho Tasks export (relates by
                                Project.id / Project Id).
  --transactions-csv PATH      optional Zoho Transactions export (for
                                consumed-hour roll-ups by project).
  --out PATH                   destination JSON.

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
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# Reuse normalization helpers from the contracts audit so the two
# audits agree on header / value handling.
from scripts.zoho_contracts_missing_fields_audit import (  # noqa: E402
    _coerce_float,
    _is_empty,
    _get_path,
    _normalize_header,
    _read_csv,
    _resolve_field,
)


logger = logging.getLogger("zoho_projects_missing_fields_audit")


# ---------------------------------------------------------------------------
# Field tables (May 22, 2026 Zoho parity)
# ---------------------------------------------------------------------------


_ZOHO_PROJECT_FIELDS: dict[str, tuple[str, ...]] = {
    "record_id":         ("Record Id",),
    "project_name":      ("Project Name",),
    "project_id":        ("Project Id", "Project ID"),
    "zoho_project_url":  ("ZOHO Project URL", "Zoho Project URL", "Project URL"),
    "account_name":      ("Account",),
    "account_id":        ("Account.id",),
    "deal_name":         ("Deal", "Deals"),
    "deal_id":           ("Deal.id", "Deals.id"),
    "contract_name":     ("Contract",),
    "contract_id":       ("Contract.id",),
    "project_owner":     ("Project Owner",),
    "project_owner_id":  ("Project Owner.id",),
    "consumed_hours":    (
        "Total Project Consumed Hour's",
        "Total Project Consumed Hours",
    ),
    "status":            ("Status",),
    "deleted_field":     ("deleted field", "Deleted Field"),
    "created_by":        ("Created By",),
    "created_time":      ("Created Time",),
    "modified_time":     ("Modified Time", "Last Activity Time"),
    "project_type":      ("Project Type",),
}


_AEGISAXIOM_PROJECT_FIELDS: tuple[str, ...] = (
    "project_id",
    "name",
    "project_type",
    "service_line",
    "status",
    "health",
    "pm_id",
    "assigned_user_ids",
    "start_date",
    "target_end_date",
    "actual_end_date",
    "contract_id",
    "account_id",
    "account_name",
    "external_project_id",
    "external_ids.zoho_project_id",
    "external_ids.zoho_contract_id",
    "external_ids.zoho_account_id",
    "external_ids.zoho_deal_id",
    "project_number",
    "owner_raw",
    "status_raw",
    "project_type_raw",
    "imported_consumed_hours",
    "source_created_at",
    "source_modified_at",
    "raw_source",
    "import_source",
)


_PARITY_FIELDS_OBSERVED_MAY_22 = (
    "external_ids.zoho_project_id",
    "external_ids.zoho_contract_id",
    "zoho_project_url",
    "project_number",
    "owner_raw",
    "status_raw",
    "project_type_raw",
    "imported_consumed_hours",
    "source_modified_at",
    "deleted_raw",
    "account_name",
    "deal_name",
    "contract_number",
)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


async def _load_aegisaxiom_projects_from_db() -> list[dict[str, Any]]:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        return []
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        return await db.projects.find(
            {"import_source": "zoho"}, {"_id": 0},
        ).to_list(length=None)
    finally:
        client.close()


def _load_aegisaxiom_manifest(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        raise RuntimeError(f"AEGISAXIOM manifest not found: {path!r}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Manifest {path!r} is not valid JSON: {e}") from e
    if isinstance(data, dict) and "projects" in data:
        data = data["projects"]
    if not isinstance(data, list):
        raise RuntimeError(f"Manifest {path!r} must be a list of project docs.")
    return [d for d in data if isinstance(d, dict)]


# ---------------------------------------------------------------------------
# Index + comparison helpers
# ---------------------------------------------------------------------------


def _index_zoho_projects(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        rid = _resolve_field(r, _ZOHO_PROJECT_FIELDS["record_id"])
        if rid:
            out[rid] = r
    return out


def _population(docs: list[dict[str, Any]], paths: Iterable[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for p in paths:
        present = empty = 0
        for d in docs:
            v = _get_path(d, p)
            if _is_empty(v):
                empty += 1
            else:
                present += 1
        out[p] = {"present": present, "empty": empty}
    return out


def _csv_population(rows: list[dict[str, str]], fields: dict[str, tuple[str, ...]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for key, variants in fields.items():
        present = empty = 0
        for r in rows:
            v = _resolve_field(r, variants)
            if _is_empty(v):
                empty += 1
            else:
                present += 1
        out[key] = {"present": present, "empty": empty}
    return out


def _compare_hours(aegisaxiom: dict[str, Any], zoho: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    aegisaxiom_val, _ = _coerce_float(_get_path(aegisaxiom, "imported_consumed_hours"))
    zoho_raw = _resolve_field(zoho, _ZOHO_PROJECT_FIELDS["consumed_hours"])
    zoho_val, _ = _coerce_float(zoho_raw)
    if aegisaxiom_val is None and zoho_val is None:
        return out
    if aegisaxiom_val is None and zoho_val is not None:
        out.append({"field": "imported_consumed_hours", "aegisaxiom": None, "zoho": zoho_val, "kind": "aegisaxiom_missing"})
    elif zoho_val is None and aegisaxiom_val is not None:
        out.append({"field": "imported_consumed_hours", "aegisaxiom": aegisaxiom_val, "zoho": None, "kind": "zoho_missing"})
    elif abs((aegisaxiom_val or 0) - (zoho_val or 0)) > 0.05:
        out.append({"field": "imported_consumed_hours", "aegisaxiom": aegisaxiom_val, "zoho": zoho_val, "kind": "value_drift"})
    return out


# ---------------------------------------------------------------------------
# Audit builder
# ---------------------------------------------------------------------------


def build_audit(
    *,
    aegisaxiom_projects: list[dict[str, Any]],
    zoho_projects_csv: list[dict[str, str]],
    zoho_tasks_csv: list[dict[str, str]],
    zoho_transactions_csv: list[dict[str, str]],
    source_label: str,
) -> dict[str, Any]:
    zoho_index = _index_zoho_projects(zoho_projects_csv)

    aegisaxiom_by_zoho_id: dict[str, dict[str, Any]] = {}
    for p in aegisaxiom_projects:
        zid = (p.get("external_ids") or {}).get("zoho_project_id") or p.get("external_project_id")
        if zid:
            aegisaxiom_by_zoho_id[zid] = p

    aegisaxiom_only = sorted(set(aegisaxiom_by_zoho_id) - set(zoho_index))
    zoho_only = sorted(set(zoho_index) - set(aegisaxiom_by_zoho_id))

    # Field populations
    aegisaxiom_pop = _population(aegisaxiom_projects, _AEGISAXIOM_PROJECT_FIELDS)
    zoho_pop = _csv_population(zoho_projects_csv, _ZOHO_PROJECT_FIELDS) if zoho_projects_csv else {}

    # Actionable hydration gaps
    aegisaxiom_fields_empty_with_zoho_value: dict[str, int] = {}
    for field in _PARITY_FIELDS_OBSERVED_MAY_22:
        count = 0
        for zid, aegisaxiom in aegisaxiom_by_zoho_id.items():
            zrow = zoho_index.get(zid)
            if not zrow:
                continue
            aegisaxiom_val = _get_path(aegisaxiom, field)
            if not _is_empty(aegisaxiom_val):
                continue
            zoho_check = {
                "external_ids.zoho_project_id":  _ZOHO_PROJECT_FIELDS["record_id"],
                "external_ids.zoho_contract_id": _ZOHO_PROJECT_FIELDS["contract_id"],
                "zoho_project_url":              _ZOHO_PROJECT_FIELDS["zoho_project_url"],
                "project_number":                _ZOHO_PROJECT_FIELDS["project_id"],
                "owner_raw":                     _ZOHO_PROJECT_FIELDS["project_owner"],
                "status_raw":                    _ZOHO_PROJECT_FIELDS["status"],
                "project_type_raw":              _ZOHO_PROJECT_FIELDS["project_type"],
                "imported_consumed_hours":       _ZOHO_PROJECT_FIELDS["consumed_hours"],
                "source_modified_at":            _ZOHO_PROJECT_FIELDS["modified_time"],
                "deleted_raw":                   _ZOHO_PROJECT_FIELDS["deleted_field"],
                "account_name":                  _ZOHO_PROJECT_FIELDS["account_name"],
                "deal_name":                     _ZOHO_PROJECT_FIELDS["deal_name"],
                "contract_number":               _ZOHO_PROJECT_FIELDS["contract_name"],
            }.get(field)
            if not zoho_check:
                continue
            zval = _resolve_field(zrow, zoho_check)
            if not _is_empty(zval):
                count += 1
        aegisaxiom_fields_empty_with_zoho_value[field] = count

    # Unmapped Zoho columns
    mapped_variants: set[str] = set()
    for variants in _ZOHO_PROJECT_FIELDS.values():
        mapped_variants.update(variants)
    csv_columns_seen: set[str] = set()
    for r in zoho_projects_csv:
        csv_columns_seen.update(r.keys())
    unmapped_zoho_columns = sorted(c for c in csv_columns_seen if c not in mapped_variants)

    # Per-project drift
    per_project: list[dict[str, Any]] = []
    hour_mismatches: list[dict[str, Any]] = []
    status_mismatches: list[dict[str, Any]] = []
    owner_gaps: list[dict[str, Any]] = []

    for zid, aegisaxiom in aegisaxiom_by_zoho_id.items():
        zrow = zoho_index.get(zid)
        per: dict[str, Any] = {
            "zoho_project_id": zid,
            "aegisaxiom_project_id": aegisaxiom.get("project_id"),
            "name": aegisaxiom.get("name"),
            "aegisaxiom_only": zrow is None,
        }
        if zrow is None:
            per_project.append(per)
            continue
        h_drift = _compare_hours(aegisaxiom, zrow)
        if h_drift:
            per["hour_drift"] = h_drift
            for d in h_drift:
                hour_mismatches.append({"zoho_project_id": zid, **d})

        # status raw mismatch
        zoho_status = _resolve_field(zrow, _ZOHO_PROJECT_FIELDS["status"])
        aegisaxiom_status_raw = aegisaxiom.get("status_raw")
        if zoho_status and aegisaxiom_status_raw and zoho_status != aegisaxiom_status_raw:
            status_mismatches.append({
                "zoho_project_id": zid,
                "zoho_raw": zoho_status,
                "aegisaxiom_raw": aegisaxiom_status_raw,
            })

        # owner mapping gap
        zoho_owner = _resolve_field(zrow, _ZOHO_PROJECT_FIELDS["project_owner"])
        if zoho_owner:
            owner_gaps.append({
                "zoho_project_id": zid,
                "zoho_owner": zoho_owner,
                "aegisaxiom_pm_id": aegisaxiom.get("pm_id"),
            })

        per_project.append(per)

    # Task counts per project (if tasks CSV present)
    tasks_per_project: dict[str, int] = defaultdict(int)
    if zoho_tasks_csv:
        for r in zoho_tasks_csv:
            pid = (
                _resolve_field(r, ("Project Id", "Project ID"))
                or _resolve_field(r, ("Project.id",))
            )
            if pid:
                tasks_per_project[pid] += 1

    # Consumed-hour roll-ups from transactions (per project)
    consumed_per_project_from_txn: dict[str, float] = defaultdict(float)
    if zoho_transactions_csv:
        for r in zoho_transactions_csv:
            pid = (
                _resolve_field(r, ("Project Name",))  # legacy
                or _resolve_field(r, ("Project Id", "Project ID"))
            )
            hours_raw = _resolve_field(r, ("Hour's Consumed", "Hours Consumed"))
            val, _ = _coerce_float(hours_raw)
            if pid and val is not None:
                consumed_per_project_from_txn[pid] += val

    return {
        "source": source_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aegisaxiom_imported_count": len(aegisaxiom_projects),
        "zoho_project_count": len(zoho_projects_csv),
        "aegisaxiom_only_zoho_project_ids": aegisaxiom_only,
        "zoho_only_zoho_project_ids": zoho_only,
        "field_population": {"aegisaxiom": aegisaxiom_pop, "zoho": zoho_pop},
        "mapping_gaps": {
            "aegisaxiom_fields_empty_with_zoho_value": aegisaxiom_fields_empty_with_zoho_value,
            "zoho_columns_not_mapped_to_aegisaxiom": unmapped_zoho_columns,
        },
        "hour_mismatches": hour_mismatches,
        "status_mismatches": status_mismatches,
        "owner_mapping_gaps": owner_gaps,
        "related_counts": {
            "tasks_per_project": dict(tasks_per_project),
            "consumed_per_project_from_txn": dict(consumed_per_project_from_txn),
        },
        "per_project": per_project,
        "notes": [
            (
                "Read-only audit. No DB writes, no Zoho API calls, no "
                "--execute flag. Companion planner: "
                "zoho_projects_missing_fields_plan.py."
            ),
            (
                "Tasks are NOT imported in this slice. The task counter "
                "is informational only and feeds the future task "
                "importer's sizing estimate."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(audit: dict[str, Any]) -> str:
    lines = []
    lines.append("Zoho Projects Missing-Fields Audit")
    lines.append("=" * 64)
    lines.append(f"Source:                {audit['source']}")
    lines.append(f"AEGISAXIOM imported count:   {audit['aegisaxiom_imported_count']}")
    lines.append(f"Zoho project count:    {audit['zoho_project_count']}")
    lines.append(f"AEGISAXIOM-only:             {len(audit['aegisaxiom_only_zoho_project_ids'])}")
    lines.append(f"Zoho-only:             {len(audit['zoho_only_zoho_project_ids'])}")
    lines.append("")
    lines.append("AegisAxiom-side empty with Zoho value (actionable hydration):")
    gaps = audit["mapping_gaps"]["aegisaxiom_fields_empty_with_zoho_value"]
    for f, n in sorted(gaps.items(), key=lambda kv: -kv[1]):
        if n > 0:
            lines.append(f"  - {f:32s}: {n}")
    lines.append("")
    lines.append(f"Hour mismatches:       {len(audit['hour_mismatches'])}")
    lines.append(f"Status mismatches:     {len(audit['status_mismatches'])}")
    lines.append(f"Owner mapping gaps:    {len(audit['owner_mapping_gaps'])}")
    lines.append(f"Unmapped Zoho columns: {len(audit['mapping_gaps']['zoho_columns_not_mapped_to_aegisaxiom'])}")
    rc = audit["related_counts"]
    if rc["tasks_per_project"]:
        lines.append(f"Tasks/project rolled up: {sum(rc['tasks_per_project'].values())} across {len(rc['tasks_per_project'])} projects")
    if rc["consumed_per_project_from_txn"]:
        lines.append(f"Consumed hours from txns: {sum(rc['consumed_per_project_from_txn'].values()):.1f} across {len(rc['consumed_per_project_from_txn'])} projects")
    lines.append("=" * 64)
    lines.append(
        "Mode: READ-ONLY. No DB writes. The companion planner emits "
        "per-project candidate $set patches — Finance / PM review "
        "required before any hydration step is applied."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def run(
    *,
    aegisaxiom_from_mongo: bool,
    aegisaxiom_manifest: Optional[str],
    projects_csv: Optional[str],
    tasks_csv: Optional[str],
    transactions_csv: Optional[str],
    out_path: Optional[str],
) -> dict[str, Any]:
    sources: list[str] = []
    aegisaxiom_projects: list[dict[str, Any]] = []
    if aegisaxiom_manifest:
        aegisaxiom_projects = _load_aegisaxiom_manifest(aegisaxiom_manifest)
        sources.append(f"aegisaxiom_manifest={aegisaxiom_manifest}")
    elif aegisaxiom_from_mongo:
        aegisaxiom_projects = await _load_aegisaxiom_projects_from_db()
        sources.append("aegisaxiom=mongo" if aegisaxiom_projects else "aegisaxiom=mongo (empty / no credentials)")
    else:
        sources.append("aegisaxiom=none")

    zoho_csv: list[dict[str, str]] = []
    if projects_csv:
        zoho_csv = _read_csv(projects_csv)
        sources.append(f"projects_csv={projects_csv}")
    tasks: list[dict[str, str]] = []
    if tasks_csv:
        tasks = _read_csv(tasks_csv)
        sources.append(f"tasks_csv={tasks_csv}")
    txns: list[dict[str, str]] = []
    if transactions_csv:
        txns = _read_csv(transactions_csv)
        sources.append(f"transactions_csv={transactions_csv}")

    audit = build_audit(
        aegisaxiom_projects=aegisaxiom_projects,
        zoho_projects_csv=zoho_csv,
        zoho_tasks_csv=tasks,
        zoho_transactions_csv=txns,
        source_label="; ".join(sources) or "none",
    )

    if out_path:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=2, sort_keys=True)
    return audit


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of imported Zoho project fields against "
            "the Zoho CSV exports. No DB writes; no --execute flag."
        ),
    )
    parser.add_argument("--aegisaxiom-from-mongo", action="store_true")
    parser.add_argument("--aegisaxiom-manifest", default=None)
    parser.add_argument("--projects-csv", default=None)
    parser.add_argument("--tasks-csv", default=None)
    parser.add_argument("--transactions-csv", default=None)
    parser.add_argument(
        "--out", default="imports/zoho/reports/projects_missing_fields_audit.json",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    try:
        audit = asyncio.run(run(
            aegisaxiom_from_mongo=args.aegisaxiom_from_mongo,
            aegisaxiom_manifest=args.aegisaxiom_manifest,
            projects_csv=args.projects_csv,
            tasks_csv=args.tasks_csv,
            transactions_csv=args.transactions_csv,
            out_path=args.out,
        ))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(format_console_summary(audit))
    if args.out:
        print(f"\nReport written to: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
