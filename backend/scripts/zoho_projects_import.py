"""Idempotent Zoho Projects importer.

Seventh slice of the Zoho historical migration. Consumes the JSON
plan emitted by ``scripts/zoho_projects_import_plan.py`` and upserts
each planned row into the AEGISAXIOM ``projects`` collection. The upsert
key is the sparse-unique ``external_project_id`` index added in
``migrations/2026_projects_zoho_import_indexes.py``; a repeat run
against the same plan re-targets the same row and does NOT create
duplicates.

Safety posture:

  * **Default mode is dry-run.** Writes happen only when ``--execute``
    is explicitly passed. Dry-run without ``--execute`` reports what
    *would* happen and never calls a Mongo writer.
  * **Blocked rows are refused.** The plan's ``blocked[]`` carries
    rows the dry-run / plan generator already ruled out (missing
    ``Contract.id`` or ``Contract.id`` that does not resolve to an
    imported AEGISAXIOM contract). The importer refuses to write any
    blocked row, period — there is no ``--force`` toggle. The
    operator must resolve the upstream blocker (typically by
    re-importing the parent contract or fixing the Zoho data) and
    regenerate the plan.
  * **Partial imports require an explicit flag.** When the plan
    contains any blocked rows, the importer refuses to proceed
    unless ``--allow-partial`` is passed. With the flag, only the
    planned ``projects[]`` rows are imported; the blocked rows are
    preserved in the result report under ``blocked_excluded`` for
    auditability but never written.
  * **Source records are NOT mutated.** Accounts, Deals, Contracts,
    Time Entries, and Finance collections are never written to. The
    importer only writes to ``projects``.
  * **Raw Zoho payload is preserved.** The full source row from
    Projects.csv lives under ``raw_source`` on the AEGISAXIOM document so
    a reconciliation job can re-derive any field without re-reading
    the CSV.
  * **Conservative AEGISAXIOM-side defaults.** AEGISAXIOM ``project_type`` is
    immutable after create and Zoho's export contains no
    ``Project Type`` column for the resolved subset; every imported
    project lands with ``project_type = "waterfall"`` (the most
    conservative methodology for historical, non-agile work) and the
    raw value preserved under ``project_type_raw``. AEGISAXIOM
    ``service_line`` has no Zoho equivalent at all — every imported
    project lands with ``service_line = "other"``. AEGISAXIOM ``status``
    starts as ``"planning"`` for every imported project regardless
    of the Zoho status — flipping a historical project to ``active``
    has billing-side cascades (time approvals route against it) and
    that sign-off has not been given. A separate
    "promote-imported" pass can flip status once the team approves.

Run:
    python backend/scripts/zoho_projects_import.py \\
        --plan backend/imports/zoho/reports/projects_import_plan.json \\
        --dry-run \\
        --allow-partial

    # Write to Mongo (irreversible — confirm the dry-run summary first):
    python backend/scripts/zoho_projects_import.py \\
        --plan backend/imports/zoho/reports/projects_import_plan.json \\
        --execute --allow-partial

Exit codes:
    0 — plan read; report written. Skipped rows do NOT flip the exit
        code; the JSON report's ``skipped`` / ``blocked_excluded``
        counts carry that signal.
    1 — plan file missing/malformed, ``--execute`` requested without
        Mongo connection details, or the plan contains blocked rows
        without ``--allow-partial`` set.

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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# Imported lazily inside _connect() so a `--dry-run` without Mongo
# credentials does not crash on import.

logger = logging.getLogger("zoho_projects_import")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMPORT_SOURCE = "zoho"

# Every imported project lands in AEGISAXIOM "planning" status. Flipping
# to "active" is a separate sign-off step — see module docstring.
DEFAULT_IMPORTED_STATUS = "planning"

# AEGISAXIOM project_type is immutable after create. Zoho's resolved subset
# has no Project Type column, so we pick the most conservative
# methodology and stamp the raw value (always null today) under
# ``project_type_raw`` for the eventual mapping pass.
DEFAULT_IMPORTED_PROJECT_TYPE = "waterfall"

# AEGISAXIOM service_line has no Zoho equivalent in Projects. "other" is
# the documented "no clean fit" bucket already accepted by the
# existing Projects model enum (see projects_project.py::SERVICE_LINES).
DEFAULT_IMPORTED_SERVICE_LINE = "other"

# AEGISAXIOM health default — matches ProjectCreate.health default. Future
# reconciliation can derive this from contract state if needed.
DEFAULT_IMPORTED_HEALTH = "green"


# ---------------------------------------------------------------------------
# DB connect (optional in dry-run)
# ---------------------------------------------------------------------------


async def _connect() -> Any | None:
    """Initialise the AEGISAXIOM Mongo connection from env vars.

    Returns the motor database handle, or ``None`` when MONGO_URL or
    DB_NAME is missing. ``--execute`` paths must check for the None
    return and refuse to proceed; ``--dry-run`` paths can degrade to
    plan-only reporting.
    """
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        return None
    import database  # type: ignore[import-not-found]

    database.init(mongo_url, db_name)
    return database.get_db()


def _sanitized_mongo_host(mongo_url: str | None) -> str:
    """Return a credential-free representation of the Mongo URL.

    Only used for the optional environment-confirmation print line.
    Never logs the password.
    """
    if not mongo_url:
        return "<unset>"
    try:
        scheme, _, rest = mongo_url.partition("://")
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        host = rest.split("/", 1)[0]
        return f"{scheme}://{host}" if scheme else host
    except Exception:
        return "<unparseable>"


# ---------------------------------------------------------------------------
# Per-row helpers
# ---------------------------------------------------------------------------


def _build_upsert_payload(
    *,
    plan_row: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``($set, $setOnInsert)`` payloads for the upsert.

    The split is deliberate: ``$set`` captures every field the
    importer wants to keep fresh on every run (timestamps, snapshots,
    raw_source, mapped enums); ``$setOnInsert`` carries the AEGISAXIOM-side
    primary key plus ``created_at`` so a repeated run never
    regenerates ``project_id`` and never rewrites the original
    creation timestamp.
    """
    candidate = plan_row.get("aegisaxiom_project_candidate") or {}
    external_ids = candidate.get("external_ids") or {}
    account = plan_row.get("account") or {}
    deal = plan_row.get("deal") or {}

    # aegisaxiom_contract_id comes from the plan's relationship resolution.
    # The importer trusts the plan: blocked rows (which would have
    # aegisaxiom_contract_id=None) are refused above, so by the time we
    # reach this helper the value is the resolved AEGISAXIOM contract id.
    aegisaxiom_contract_id = (
        plan_row.get("aegisaxiom_contract_id")
        or candidate.get("aegisaxiom_contract_id")
    )

    set_doc: dict[str, Any] = {
        # Mutable AEGISAXIOM-side fields the importer owns.
        "name": candidate.get("name") or plan_row.get("project_name") or "",
        "description": None,
        # AEGISAXIOM project_type is immutable; we cannot move historical
        # rows between methodologies on re-run. $setOnInsert handles
        # the initial pick (see below); $set keeps the raw value
        # tracking fresh.
        "service_line": DEFAULT_IMPORTED_SERVICE_LINE,
        "status": DEFAULT_IMPORTED_STATUS,
        "health": DEFAULT_IMPORTED_HEALTH,
        # pm_id stays None until the Users export resolves Zoho
        # Project Owner -> AEGISAXIOM user_id. Imported projects can be
        # patched later.
        "pm_id": None,
        "assigned_user_ids": [],
        "start_date": None,
        "target_end_date": None,
        "actual_end_date": None,
        "template_id": None,
        "methodology_config": {},
        # Bridge to the resolved AEGISAXIOM contract — set on every run.
        "contract_id": aegisaxiom_contract_id,
        # account_id stays empty until the Accounts importer lands;
        # account_name carries the fallback display so the row is
        # at least browsable in AEGISAXIOM until then.
        "account_id": None,
        "account_name": account.get("name") or candidate.get("account_name"),
        "deal_name": deal.get("name") or candidate.get("deal_name"),
        "updated_at": now,
        "imported_at": now,
        "external_source_synced_at": now,
        "import_source": IMPORT_SOURCE,
        # Additive Zoho-only fields. Existing AEGISAXIOM callers ignore
        # unknown fields on read, so nothing breaks.
        "external_ids": {
            "zoho_project_id": external_ids.get("zoho_project_id"),
            "zoho_contract_id": external_ids.get("zoho_contract_id"),
            "zoho_account_id": external_ids.get("zoho_account_id"),
            "zoho_deal_id": external_ids.get("zoho_deal_id"),
        },
        # Preserve raw enums so a later sign-off pass can map them.
        "status_raw": candidate.get("status_raw"),
        "project_type_raw": candidate.get("project_type_raw"),
        # Snapshot fields — useful for ops review.
        "project_number": candidate.get("project_number"),
        "contract_number": candidate.get("contract_number"),
        "owner_raw": candidate.get("owner_raw"),
        "imported_consumed_hours": candidate.get("imported_consumed_hours"),
        "source_created_at": candidate.get("source_created_at"),
        "source_modified_at": candidate.get("source_modified_at"),
        "raw_source": candidate.get("raw_source") or {},
        # Plan-time warnings echoed onto the imported document so a
        # AEGISAXIOM operator can see why a project was imported with
        # missing-owner / missing-type / mismatch-account-id, etc.
        "import_warnings": list(plan_row.get("warnings") or []),
    }

    set_on_insert: dict[str, Any] = {
        "project_id": f"proj_{uuid.uuid4().hex[:12]}",
        "created_at": now,
        # external_project_id is the upsert key — set it once on
        # insert and never touch it again.
        "external_project_id": external_ids.get("zoho_project_id"),
        # project_type is immutable; lock it on first insert.
        "project_type": DEFAULT_IMPORTED_PROJECT_TYPE,
    }
    return set_doc, set_on_insert


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    dry_run: bool
    allow_partial: bool
    plan_path: str
    total_plan_rows: int
    considered: int
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    blocked_excluded: int = 0
    warning_count: int = 0
    db_connected: bool = False
    per_row: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_doc(self) -> dict[str, Any]:
        return {
            "plan_file": self.plan_path,
            "mode": "dry_run" if self.dry_run else "execute",
            "allow_partial": self.allow_partial,
            "total_plan_rows": self.total_plan_rows,
            "considered": self.considered,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "blocked_excluded": self.blocked_excluded,
            "warning_count": self.warning_count,
            "db_connected": self.db_connected,
            "per_row": self.per_row,
            "warnings": self.warnings,
            "errors": self.errors,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }


async def import_from_plan(
    plan: dict[str, Any],
    *,
    db: Any,
    execute: bool,
    allow_partial: bool,
    plan_path: str,
    now: datetime | None = None,
) -> ImportResult:
    """Run the importer against an already-loaded plan document.

    Tests call this directly with a fake Mongo handle; the CLI's
    ``run()`` is what reads the JSON from disk.
    """
    eff_now = now if now is not None else datetime.now(timezone.utc)
    projects = list(plan.get("projects") or [])
    blocked = list(plan.get("blocked") or [])
    result = ImportResult(
        dry_run=not execute,
        allow_partial=allow_partial,
        plan_path=plan_path,
        total_plan_rows=len(projects) + len(blocked),
        considered=0,
        db_connected=db is not None,
    )

    # Refuse blocked plans without --allow-partial. This is the
    # primary safety gate that prevents an unaware operator from
    # silently dropping rows on the floor.
    if blocked and not allow_partial:
        result.errors.append(
            f"Plan contains {len(blocked)} blocked row(s); refusing to "
            "proceed without --allow-partial. Re-run with --allow-partial "
            "to import only the resolved projects[] and preserve the "
            "blocked rows in the result report."
        )
        result.blocked_excluded = len(blocked)
        return result

    if execute and db is None:
        result.errors.append(
            "--execute requires MONGO_URL + DB_NAME in the environment; "
            "refusing to write."
        )
        return result

    # Defence in depth: never consider blocked rows even if a future
    # caller forgets the gate above. The blocked count is reported
    # for auditability so the operator can confirm at the console.
    result.blocked_excluded = len(blocked)

    rows_to_consider: list[dict[str, Any]] = list(projects)
    result.considered = len(rows_to_consider)

    for row in rows_to_consider:
        candidate = row.get("aegisaxiom_project_candidate") or {}
        external_ids = candidate.get("external_ids") or {}
        ext_id = external_ids.get("zoho_project_id")

        # Defence in depth — the plan generator guarantees this is
        # set for every projects[] row, but skip rather than crash
        # if a hand-edited plan ever omits it.
        if not ext_id:
            result.skipped += 1
            result.per_row.append({
                "zoho_project_id": None,
                "project_name": row.get("project_name"),
                "action": "skipped",
                "reason": "missing_external_project_id",
            })
            continue

        # Defence in depth — refuse rows the plan resolved without
        # a AEGISAXIOM contract id. The plan generator should never put
        # such rows in projects[] (they belong in blocked[]), but
        # checking again here means a hand-edited plan cannot
        # bypass the rule.
        aegisaxiom_contract_id = (
            row.get("aegisaxiom_contract_id")
            or candidate.get("aegisaxiom_contract_id")
        )
        if not aegisaxiom_contract_id:
            result.skipped += 1
            result.per_row.append({
                "zoho_project_id": ext_id,
                "project_name": row.get("project_name"),
                "action": "skipped",
                "reason": "missing_aegisaxiom_contract_id",
            })
            continue

        set_doc, set_on_insert = _build_upsert_payload(
            plan_row=row, now=eff_now,
        )

        if execute:
            res = await db.projects.update_one(
                {"external_project_id": ext_id},
                {"$set": set_doc, "$setOnInsert": set_on_insert},
                upsert=True,
            )
            if res.upserted_id is not None:
                result.inserted += 1
                action = "inserted"
            else:
                result.updated += 1
                action = "updated"
        else:
            existing = None
            if db is not None:
                existing = await db.projects.find_one(
                    {"external_project_id": ext_id},
                    {"_id": 0, "project_id": 1},
                )
            if existing:
                result.updated += 1
                action = "would_update"
            else:
                result.inserted += 1
                action = "would_insert"

        per_row_warnings = list(row.get("warnings") or [])
        result.warning_count += len(per_row_warnings)
        result.per_row.append({
            "zoho_project_id": ext_id,
            "project_name": row.get("project_name"),
            "aegisaxiom_contract_id": aegisaxiom_contract_id,
            "action": action,
            "warning_kinds": sorted({w.get("kind", "") for w in per_row_warnings}),
        })

    return result


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(result: ImportResult) -> str:
    lines: list[str] = []
    lines.append("Zoho Projects Importer")
    lines.append("=" * 60)
    lines.append(f"Plan file:           {result.plan_path}")
    lines.append(
        f"Mode:                "
        f"{'DRY-RUN (no writes)' if result.dry_run else 'EXECUTE (writes)'}"
    )
    lines.append(
        f"Allow partial:       "
        f"{'YES' if result.allow_partial else 'no'}"
    )
    lines.append(
        f"DB connected:        "
        f"{'YES' if result.db_connected else 'no (plan-only preview)'}"
    )
    lines.append("")
    lines.append(f"Total plan rows:     {result.total_plan_rows}")
    lines.append(f"Considered:          {result.considered}")
    if result.dry_run:
        lines.append(f"Would insert:        {result.inserted}")
        lines.append(f"Would update:        {result.updated}")
    else:
        lines.append(f"Inserted:            {result.inserted}")
        lines.append(f"Updated:             {result.updated}")
    lines.append(f"Skipped:             {result.skipped}")
    lines.append(f"Blocked excluded:    {result.blocked_excluded}")
    lines.append(f"Per-row warnings:    {result.warning_count}")
    if result.errors:
        lines.append("")
        lines.append("Errors:")
        for e in result.errors:
            lines.append(f"  - {e}")
    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in result.warnings:
            lines.append(f"  - {w}")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _load_plan(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise RuntimeError(f"Plan file not found: {path!r}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Plan file {path!r} is not valid JSON: {e}") from e


async def run(
    *,
    plan_path: str,
    out_path: Optional[str],
    execute: bool,
    allow_partial: bool,
) -> ImportResult:
    plan = _load_plan(plan_path)
    db = await _connect()
    result = await import_from_plan(
        plan,
        db=db,
        execute=execute,
        allow_partial=allow_partial,
        plan_path=plan_path,
    )
    if out_path:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result.to_doc(), f, indent=2, sort_keys=True)
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Idempotent Zoho Projects importer.",
    )
    parser.add_argument(
        "--plan",
        default="imports/zoho/reports/projects_import_plan.json",
        help=(
            "Path to the JSON import plan emitted by "
            "scripts/zoho_projects_import_plan.py."
        ),
    )
    parser.add_argument(
        "--out",
        default="imports/zoho/reports/projects_import_result.json",
        help="Destination path for the JSON result report.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Report what would happen. Default mode (no writes).",
    )
    mode.add_argument(
        "--execute", action="store_true",
        help=(
            "Write to Mongo. Requires MONGO_URL + DB_NAME in env. "
            "Refused without this flag."
        ),
    )
    parser.add_argument(
        "--allow-partial", action="store_true",
        help=(
            "Permit running the importer when the plan contains "
            "blocked rows. Blocked rows are NEVER imported — this "
            "flag only allows the resolved projects[] subset to "
            "land while blocked rows stay excluded for auditability."
        ),
    )
    parser.add_argument(
        "--confirm-env", action="store_true",
        help=(
            "Print the sanitized Mongo host and DB_NAME before "
            "running (no credentials). Useful when --execute is set."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    execute = args.execute  # dry-run is the default when neither flag is set

    if args.confirm_env:
        print(
            f"Mongo host: {_sanitized_mongo_host(os.environ.get('MONGO_URL'))} | "
            f"DB_NAME: {os.environ.get('DB_NAME') or '<unset>'}"
        )

    try:
        result = asyncio.run(run(
            plan_path=args.plan,
            out_path=args.out,
            execute=execute,
            allow_partial=args.allow_partial,
        ))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(format_console_summary(result))
    if args.out:
        print(f"\nReport written to: {args.out}")
    if result.errors:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
