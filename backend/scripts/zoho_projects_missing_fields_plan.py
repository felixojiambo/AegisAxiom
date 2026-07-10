"""Read-only candidate-patch planner for missing Zoho project fields.

Sibling to ``zoho_contracts_missing_fields_plan.py``. Consumes the
same inputs the audit ingested and emits per-project candidate
``$set`` patches. The actual hydration is a separate, reviewed
slice — this script never writes to Mongo.

Strict guarantees:

  * No DB writes.
  * No Zoho API calls.
  * No ``--execute`` flag (pinning test in
    ``test_zoho_projects_missing_fields_plan.py``).
  * Tasks are NOT proposed for import here — the audit may flag
    task counts but task import is a separate slice.

The companion mapping doc is
``docs/zoho-migration/projects_field_mapping.md``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# Reuse field tables / loaders from the audit so the two scripts
# cannot disagree on which Zoho columns map to which AEGISAXIOM fields.
from scripts.zoho_projects_missing_fields_audit import (  # noqa: E402
    _ZOHO_PROJECT_FIELDS,
    _coerce_float,
    _is_empty,
    _get_path,
    _index_zoho_projects,
    _read_csv,
    _resolve_field,
    _load_aegisaxiom_projects_from_db,
    _load_aegisaxiom_manifest,
)


logger = logging.getLogger("zoho_projects_missing_fields_plan")


# ---------------------------------------------------------------------------
# Rule helpers
# ---------------------------------------------------------------------------


def _string_rule(aegisaxiom_field: str, zoho_variants: tuple[str, ...]) -> Any:
    def rule(aegisaxiom: dict[str, Any], zoho: dict[str, str]) -> dict[str, Any] | None:
        if not _is_empty(_get_path(aegisaxiom, aegisaxiom_field)):
            return None
        raw = _resolve_field(zoho, zoho_variants)
        if _is_empty(raw):
            return None
        return {"rule": f"fill_{aegisaxiom_field}", "field": aegisaxiom_field, "candidate": raw}

    return rule


def _hour_rule(aegisaxiom_field: str, zoho_variants: tuple[str, ...]) -> Any:
    def rule(aegisaxiom: dict[str, Any], zoho: dict[str, str]) -> dict[str, Any] | None:
        if not _is_empty(_get_path(aegisaxiom, aegisaxiom_field)):
            return None
        raw = _resolve_field(zoho, zoho_variants)
        val, _ = _coerce_float(raw)
        if val is None:
            return None
        return {"rule": f"fill_{aegisaxiom_field}", "field": aegisaxiom_field, "candidate": val}

    return rule


def _external_ids_rule(
    aegisaxiom_subfield: str, zoho_variants: tuple[str, ...],
) -> Any:
    """Fill external_ids.<subfield> when the value is missing.
    Returns a candidate with a synthetic path the executor can
    interpret (e.g. ``external_ids.zoho_contract_id``)."""

    def rule(aegisaxiom: dict[str, Any], zoho: dict[str, str]) -> dict[str, Any] | None:
        path = f"external_ids.{aegisaxiom_subfield}"
        if not _is_empty(_get_path(aegisaxiom, path)):
            return None
        raw = _resolve_field(zoho, zoho_variants)
        if _is_empty(raw):
            return None
        return {"rule": f"fill_{path}", "field": path, "candidate": raw}

    return rule


_RULES = [
    _string_rule("zoho_project_url",       _ZOHO_PROJECT_FIELDS["zoho_project_url"]),
    _string_rule("project_number",         _ZOHO_PROJECT_FIELDS["project_id"]),
    _string_rule("owner_raw",              _ZOHO_PROJECT_FIELDS["project_owner"]),
    _string_rule("status_raw",             _ZOHO_PROJECT_FIELDS["status"]),
    _string_rule("project_type_raw",       _ZOHO_PROJECT_FIELDS["project_type"]),
    _string_rule("deleted_raw",            _ZOHO_PROJECT_FIELDS["deleted_field"]),
    _string_rule("source_modified_at",     _ZOHO_PROJECT_FIELDS["modified_time"]),
    _string_rule("account_name",           _ZOHO_PROJECT_FIELDS["account_name"]),
    _string_rule("deal_name",              _ZOHO_PROJECT_FIELDS["deal_name"]),
    _string_rule("contract_number",        _ZOHO_PROJECT_FIELDS["contract_name"]),
    _hour_rule("imported_consumed_hours",  _ZOHO_PROJECT_FIELDS["consumed_hours"]),
    _external_ids_rule("zoho_project_id",  _ZOHO_PROJECT_FIELDS["record_id"]),
    _external_ids_rule("zoho_contract_id", _ZOHO_PROJECT_FIELDS["contract_id"]),
    _external_ids_rule("zoho_account_id",  _ZOHO_PROJECT_FIELDS["account_id"]),
    _external_ids_rule("zoho_deal_id",     _ZOHO_PROJECT_FIELDS["deal_id"]),
]


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


def build_plan(
    *,
    aegisaxiom_projects: list[dict[str, Any]],
    zoho_projects_csv: list[dict[str, str]],
    zoho_tasks_csv: list[dict[str, str]] | None = None,
    source_label: str,
) -> dict[str, Any]:
    zoho_index = _index_zoho_projects(zoho_projects_csv)

    rule_counts: Counter[str] = Counter()
    rows_with_patch = 0
    out_rows: list[dict[str, Any]] = []

    # Task counts by zoho project id, surfaced as informational warnings.
    tasks_by_pid: dict[str, int] = {}
    if zoho_tasks_csv:
        for r in zoho_tasks_csv:
            pid = (
                _resolve_field(r, ("Project Id", "Project ID"))
                or _resolve_field(r, ("Project.id",))
            )
            if pid:
                tasks_by_pid[pid] = tasks_by_pid.get(pid, 0) + 1

    for aegisaxiom in aegisaxiom_projects:
        external_ids = aegisaxiom.get("external_ids") or {}
        zid = external_ids.get("zoho_project_id") or aegisaxiom.get("external_project_id")
        zoho = zoho_index.get(zid or "") or {}

        candidate_set: dict[str, Any] = {}
        warnings: list[str] = []
        needs_review: list[str] = []

        if zoho:
            for rule in _RULES:
                result = rule(aegisaxiom, zoho)
                if not result:
                    continue
                rule_counts[result["rule"]] += 1
                candidate_set[result["field"]] = result["candidate"]
        else:
            warnings.append("no_zoho_row_for_project_id")

        # Task-count informational note (does NOT enter candidate_set).
        if zid and zid in tasks_by_pid:
            warnings.append(
                f"tasks_observed_in_zoho_export_count={tasks_by_pid[zid]}"
                " (task import is a separate slice)"
            )

        # PM mapping is a separate concern (Users export); flag every
        # imported project with pm_id=None as needing the Users pass.
        if _is_empty(aegisaxiom.get("pm_id")):
            needs_review.append("pm_id (resolved via Users export)")

        if candidate_set:
            rows_with_patch += 1

        out_rows.append({
            "project_id": aegisaxiom.get("project_id"),
            "external_project_id": aegisaxiom.get("external_project_id"),
            "zoho_project_id": zid,
            "name": aegisaxiom.get("name"),
            "candidate_set": candidate_set,
            "warnings": warnings,
            "needs_finance_review": sorted(set(needs_review)),
        })

    return {
        "source": source_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_imported": len(aegisaxiom_projects),
            "with_patch": rows_with_patch,
            "rule_invocations": dict(rule_counts),
            "projects_with_observed_tasks": sum(1 for r in out_rows if any("tasks_observed_in_zoho_export_count" in w for w in r["warnings"])),
        },
        "projects": out_rows,
        "notes": [
            (
                "candidate_set is a CANDIDATE only. Apply via a separate, "
                "reviewed executor — never blind-write these values."
            ),
            (
                "Task records are NOT included in candidate_set. The "
                "task counter is informational; a separate, reviewed "
                "task importer is required before tasks can land."
            ),
            (
                "pm_id resolution is gated on the Users export landing. "
                "needs_finance_review='pm_id (resolved via Users export)'"
                " flags every row that currently has no PM assigned."
            ),
            "No --execute flag exists. Read-only by design.",
        ],
    }


def format_console_summary(plan: dict[str, Any]) -> str:
    s = plan["summary"]
    lines = []
    lines.append("Zoho Projects Missing-Fields Plan")
    lines.append("=" * 64)
    lines.append(f"Source:                  {plan['source']}")
    lines.append(f"Total imported:          {s['total_imported']}")
    lines.append(f"Rows with candidate set: {s['with_patch']}")
    lines.append(f"Projects w/observed tasks (info): {s['projects_with_observed_tasks']}")
    lines.append("")
    if s["rule_invocations"]:
        lines.append("Rule invocations:")
        for k, n in sorted(s["rule_invocations"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {k:48s}: {n}")
    lines.append("=" * 64)
    lines.append(
        "Mode: READ-ONLY. No DB writes. candidate_set is a CANDIDATE — "
        "PM / Finance must confirm before any hydration step."
    )
    return "\n".join(lines)


async def run(
    *,
    aegisaxiom_from_mongo: bool,
    aegisaxiom_manifest: Optional[str],
    projects_csv: Optional[str],
    tasks_csv: Optional[str],
    out_path: Optional[str],
) -> dict[str, Any]:
    sources: list[str] = []
    if aegisaxiom_manifest:
        aegisaxiom_projects = _load_aegisaxiom_manifest(aegisaxiom_manifest)
        sources.append(f"aegisaxiom_manifest={aegisaxiom_manifest}")
    elif aegisaxiom_from_mongo:
        aegisaxiom_projects = await _load_aegisaxiom_projects_from_db()
        sources.append("aegisaxiom=mongo" if aegisaxiom_projects else "aegisaxiom=mongo (empty / no credentials)")
    else:
        aegisaxiom_projects = []
        sources.append("aegisaxiom=none")

    zoho_csv: list[dict[str, str]] = []
    if projects_csv:
        zoho_csv = _read_csv(projects_csv)
        sources.append(f"projects_csv={projects_csv}")
    tasks_csv_rows: list[dict[str, str]] = []
    if tasks_csv:
        tasks_csv_rows = _read_csv(tasks_csv)
        sources.append(f"tasks_csv={tasks_csv}")

    plan = build_plan(
        aegisaxiom_projects=aegisaxiom_projects,
        zoho_projects_csv=zoho_csv,
        zoho_tasks_csv=tasks_csv_rows,
        source_label="; ".join(sources) or "none",
    )

    if out_path:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, sort_keys=True)
    return plan


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only candidate-patch planner for missing Zoho project "
            "fields. No DB writes; no --execute flag."
        ),
    )
    parser.add_argument("--aegisaxiom-from-mongo", action="store_true")
    parser.add_argument("--aegisaxiom-manifest", default=None)
    parser.add_argument("--projects-csv", default=None)
    parser.add_argument("--tasks-csv", default=None)
    parser.add_argument(
        "--out", default="imports/zoho/reports/projects_missing_fields_plan.json",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    try:
        plan = asyncio.run(run(
            aegisaxiom_from_mongo=args.aegisaxiom_from_mongo,
            aegisaxiom_manifest=args.aegisaxiom_manifest,
            projects_csv=args.projects_csv,
            tasks_csv=args.tasks_csv,
            out_path=args.out,
        ))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(format_console_summary(plan))
    if args.out:
        print(f"\nReport written to: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
