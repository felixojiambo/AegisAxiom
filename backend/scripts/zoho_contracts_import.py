"""Idempotent Zoho Contracts importer.

Fourth slice of the Zoho historical migration. Consumes the JSON plan
emitted by ``scripts/zoho_contracts_import_plan.py`` and upserts each
planned row into the AEGISAXIOM ``contracts`` collection. The upsert key is
the sparse-unique ``external_contract_id`` index added in
``migrations/2026_contracts_time_mgmt_schema.py``; a repeat run against
the same plan re-targets the same row and does NOT create duplicates.

Safety posture:

  * **Default mode is dry-run.** Writes happen only when ``--execute``
    is explicitly passed. Dry-run without ``--execute`` reports what
    *would* happen and never calls a Mongo writer.
  * **Orphan deal rows are refused** unless ``--allow-orphan-deals``
    is passed. The plan's default policy already keeps them out of
    ``contracts[]`` — this importer mirrors that policy and also
    re-checks at write time so the gate cannot be bypassed by
    passing a hand-edited plan.
  * **Enum-mapping decisions stay conservative.** The Zoho status
    string is preserved under ``status_raw`` but ``status`` is set to
    ``draft`` for every imported row regardless — flipping a
    historical Zoho contract to ``status="active"`` in AEGISAXIOM has
    billing-side cascades (time approvals route against it) and that
    sign-off has not been given. A separate "promote-imported" pass
    can flip status once the team approves.
  * **Source records are NOT mutated.** Accounts, Deals, Projects,
    Time Entries, and Finance collections are never written to.
  * **Raw Zoho payload is preserved.** The full source row from
    Contract.csv lives under ``raw_source`` on the AEGISAXIOM document so
    a reconciliation job can re-derive any field without re-reading
    the CSV.

Run:
    python backend/scripts/zoho_contracts_import.py \\
        --plan backend/imports/zoho/reports/contracts_import_plan.json \\
        --dry-run

    # Write to Mongo (irreversible — confirm orphan-deal policy first):
    python backend/scripts/zoho_contracts_import.py \\
        --plan backend/imports/zoho/reports/contracts_import_plan.json \\
        --execute --allow-orphan-deals

Exit codes:
    0 — plan read; report written. Skipped rows do NOT flip the exit
        code; the JSON report's ``skipped`` / ``blocked_excluded``
        counts carry that signal.
    1 — plan file missing/malformed, or ``--execute`` requested
        without Mongo connection details, or any blocker the
        importer refuses to bypass.

The companion mapping doc is
``docs/zoho-migration/contracts_field_mapping.md``.
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

logger = logging.getLogger("zoho_contracts_import")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMPORT_SOURCE = "zoho"

# Statuses we will not promote to "active" automatically. Every imported
# row lands as "draft" until a separate sign-off step flips the value.
DEFAULT_IMPORTED_STATUS = "draft"

# Conservative contract_type mapping. Anything not in this table lands as
# ``"other"`` — the AEGISAXIOM enum's documented "no clean fit" bucket — and the
# raw value stays available under ``contract_type_raw``. The mapping doc
# tracks the Zoho-side cleanup needed for the unmapped values.
_CONTRACT_TYPE_MAP: dict[str, str] = {
    "BOH": "boh",
    "BOH and Bill on Actuals": "boh_with_boa",
    "Bill on Actuals": "boa",
    "Fixed Fee": "fixed_fee",
}
_DEFAULT_CONTRACT_TYPE = "other"


# Blockers the plan emits that the importer treats as policy-only
# (i.e., the row is structurally importable, just refused by the
# default policy). Orphan-deal is the only entry today; if a future
# plan version introduces another policy gate, list its blocker
# kind here so this importer can flip it via a CLI flag.
_POLICY_BLOCKER_KINDS: frozenset[str] = frozenset({
    "orphan_zoho_deal_id_default_policy",
})


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


# ---------------------------------------------------------------------------
# Per-row helpers
# ---------------------------------------------------------------------------


def _map_contract_type(raw: str | None) -> str:
    if not raw:
        return _DEFAULT_CONTRACT_TYPE
    return _CONTRACT_TYPE_MAP.get(raw.strip(), _DEFAULT_CONTRACT_TYPE)


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
    regenerates ``contract_id`` and never rewrites the original
    creation timestamp.
    """
    candidate = plan_row["aegisaxiom_contract_candidate"]
    external_ids = candidate.get("external_ids") or {}
    account = plan_row.get("account") or {}
    deal = plan_row.get("deal") or {}

    set_doc: dict[str, Any] = {
        # Mutable AEGISAXIOM-side fields the importer owns.
        "name": candidate.get("name") or plan_row.get("name") or "",
        # account_id stays empty until the Accounts importer lands;
        # account_name carries the fallback display so the row is
        # at least browsable in AEGISAXIOM until then.
        "account_id": "",
        "account_name": account.get("name") or candidate.get("account_name"),
        "contract_type": _map_contract_type(candidate.get("contract_type_raw")),
        # Every imported contract starts in "draft". Flipping to
        # "active" is a separate sign-off step — see module docstring.
        "status": DEFAULT_IMPORTED_STATUS,
        # Conservative AEGISAXIOM-side counters — the snapshot fields below
        # are the historical Zoho figures; AEGISAXIOM recomputes its own
        # counters from time entries once the Time Entries import
        # lands. Keeping these at zero prevents the imported contract
        # from looking "exhausted" or "burned" in AEGISAXIOM UI.
        "total_hours_purchased": 0.0,
        "hours_consumed": 0.0,
        "updated_at": now,
        "imported_at": now,
        "external_source_synced_at": now,
        "import_source": IMPORT_SOURCE,
        # Additive Zoho-only fields. Pydantic ignores unknown fields
        # on read, so existing AEGISAXIOM callers see no change.
        "external_ids": {
            "zoho_contract_id": external_ids.get("zoho_contract_id"),
            "zoho_account_id": external_ids.get("zoho_account_id"),
            "zoho_deal_id": external_ids.get("zoho_deal_id"),
        },
        "contract_type_raw": candidate.get("contract_type_raw"),
        "invoice_type_raw": candidate.get("invoice_type_raw"),
        "status_raw": candidate.get("status_raw"),
        "opportunity_name": candidate.get("opportunity_name"),
        "contract_number": candidate.get("contract_number"),
        "imported_total_hours": candidate.get("total_hours_snapshot"),
        "imported_remaining_hours": candidate.get("remaining_hours_snapshot"),
        "imported_consumed_hours": candidate.get("consumed_hours_snapshot"),
        "imported_consumed_percent": candidate.get("consumed_percent_snapshot"),
        "source_created_at": candidate.get("source_created_at"),
        "source_modified_at": candidate.get("source_modified_at"),
        "raw_source": candidate.get("raw_source") or {},
        # Plan-time warnings echoed onto the imported document so a
        # AEGISAXIOM operator can see why a contract was imported with
        # placeholder enums or a snapshot anomaly.
        "import_warnings": list(plan_row.get("warnings") or []),
        "import_deal_resolved": bool(deal.get("resolved")),
    }

    set_on_insert: dict[str, Any] = {
        "contract_id": f"contract_{uuid.uuid4().hex[:12]}",
        "created_at": now,
        # external_contract_id is the upsert key — set it once on
        # insert and never touch it again. Putting it in $setOnInsert
        # (instead of $set) means a manual edit of the field in AEGISAXIOM
        # would survive a re-import; today no one edits it, but the
        # invariant is cheap to preserve.
        "external_contract_id": external_ids.get("zoho_contract_id"),
    }
    return set_doc, set_on_insert


def _is_orphan_deal_row(row: dict[str, Any]) -> bool:
    """A row's deal join failed to resolve. True for both planned-and-
    accepted orphans (``--allow-orphan-deals``) and blocked-orphan
    rows in the plan."""
    deal = row.get("deal") or {}
    return bool(deal.get("zoho_deal_id")) and not deal.get("resolved")


def _row_is_policy_orphan_blocked(row: dict[str, Any]) -> bool:
    """The plan's default policy moved this row to ``blocked[]`` for
    the orphan-deal reason ONLY. These can be re-included by the
    ``--allow-orphan-deals`` flag without bypassing any structural
    blocker.
    """
    blockers = row.get("blockers") or []
    if not blockers:
        return False
    return all(blk.get("kind") in _POLICY_BLOCKER_KINDS for blk in blockers)


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    dry_run: bool
    allow_orphan_deals: bool
    plan_path: str
    total_plan_rows: int
    considered: int
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    blocked_excluded: int = 0
    orphan_deal_count: int = 0
    warning_count: int = 0
    db_connected: bool = False
    per_row: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_doc(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "allow_orphan_deals": self.allow_orphan_deals,
            "plan_path": self.plan_path,
            "total_plan_rows": self.total_plan_rows,
            "considered": self.considered,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "blocked_excluded": self.blocked_excluded,
            "orphan_deal_count": self.orphan_deal_count,
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
    allow_orphan_deals: bool,
    plan_path: str,
    now: datetime | None = None,
) -> ImportResult:
    """Run the importer against an already-loaded plan document.

    Tests call this directly with a fake Mongo handle; the CLI's
    ``run()`` is what reads the JSON from disk.
    """
    eff_now = now if now is not None else datetime.now(timezone.utc)
    contracts = list(plan.get("contracts") or [])
    blocked = list(plan.get("blocked") or [])
    result = ImportResult(
        dry_run=not execute,
        allow_orphan_deals=allow_orphan_deals,
        plan_path=plan_path,
        total_plan_rows=len(contracts) + len(blocked),
        considered=0,
        db_connected=db is not None,
    )

    if execute and db is None:
        result.errors.append(
            "--execute requires MONGO_URL + DB_NAME in the environment; "
            "refusing to write."
        )
        return result

    rows_to_consider: list[dict[str, Any]] = list(contracts)

    # Promote policy-only blocked rows back into consideration when
    # --allow-orphan-deals is set. Structural blockers (missing
    # record id, invalid_numeric, etc.) stay refused.
    for b in blocked:
        if _row_is_policy_orphan_blocked(b):
            if allow_orphan_deals:
                rows_to_consider.append(b)
            else:
                result.blocked_excluded += 1
        else:
            result.blocked_excluded += 1

    result.considered = len(rows_to_consider)

    for row in rows_to_consider:
        candidate = row.get("aegisaxiom_contract_candidate") or {}
        external_ids = candidate.get("external_ids") or {}
        ext_id = external_ids.get("zoho_contract_id")
        if not ext_id:
            result.skipped += 1
            result.per_row.append({
                "zoho_contract_id": None,
                "contract_number": row.get("contract_number"),
                "action": "skipped",
                "reason": "missing_external_contract_id",
            })
            continue

        is_orphan = _is_orphan_deal_row(row)
        if is_orphan:
            result.orphan_deal_count += 1
            if not allow_orphan_deals:
                # Defence in depth — even if a hand-edited plan
                # leaked an orphan into contracts[] without the flag,
                # refuse it here.
                result.skipped += 1
                result.per_row.append({
                    "zoho_contract_id": ext_id,
                    "contract_number": row.get("contract_number"),
                    "action": "skipped",
                    "reason": "orphan_deal_without_allow_flag",
                })
                continue

        set_doc, set_on_insert = _build_upsert_payload(
            plan_row=row, now=eff_now,
        )

        if execute:
            res = await db.contracts.update_one(
                {"external_contract_id": ext_id},
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
                existing = await db.contracts.find_one(
                    {"external_contract_id": ext_id},
                    {"_id": 0, "contract_id": 1},
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
            "zoho_contract_id": ext_id,
            "contract_number": row.get("contract_number"),
            "action": action,
            "orphan_deal": is_orphan,
            "warning_kinds": sorted({w.get("kind", "") for w in per_row_warnings}),
        })

    return result


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(result: ImportResult) -> str:
    lines: list[str] = []
    lines.append("Zoho Contracts Importer")
    lines.append("=" * 60)
    lines.append(f"Plan file:           {result.plan_path}")
    lines.append(
        f"Mode:                "
        f"{'DRY-RUN (no writes)' if result.dry_run else 'EXECUTE (writes)'}"
    )
    lines.append(
        f"Allow orphan deals:  "
        f"{'YES' if result.allow_orphan_deals else 'no'}"
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
    lines.append(f"Orphan deal count:   {result.orphan_deal_count}")
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
    allow_orphan_deals: bool,
) -> ImportResult:
    plan = _load_plan(plan_path)
    db = await _connect()
    result = await import_from_plan(
        plan,
        db=db,
        execute=execute,
        allow_orphan_deals=allow_orphan_deals,
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
        description="Idempotent Zoho Contracts importer.",
    )
    parser.add_argument(
        "--plan",
        default="imports/zoho/reports/contracts_import_plan.json",
        help=(
            "Path to the JSON import plan emitted by "
            "scripts/zoho_contracts_import_plan.py."
        ),
    )
    parser.add_argument(
        "--out",
        default="imports/zoho/reports/contracts_import_result.json",
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
        "--allow-orphan-deals", action="store_true",
        help=(
            "Permit rows whose Zoho Deals.id has no matching row in "
            "the Deals export. The contract lands with "
            "external_ids.zoho_deal_id stamped and deal_ids=[]. "
            "Refused without this flag — even if the plan's "
            "contracts[] already contains such a row."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    execute = args.execute  # dry-run is the default when neither flag is set

    try:
        result = asyncio.run(run(
            plan_path=args.plan,
            out_path=args.out,
            execute=execute,
            allow_orphan_deals=args.allow_orphan_deals,
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
