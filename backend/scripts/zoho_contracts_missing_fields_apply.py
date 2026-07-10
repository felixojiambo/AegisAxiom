"""Apply hydration patches from Zoho Contract.csv to existing AEGISAXIOM
contracts. Default mode is dry-run; ``--execute`` is the only path
that writes to Mongo.

Hard guarantees:

  * Writes only to the ``contracts`` collection.
  * Never modifies any of:
      contract_id, project_ids, projects, resources, resource_types,
      hourly_rate, approval configuration, local audit/created_at
      fields, tenant_id, is_test_data.
  * Never creates a new contract unless ``--allow-create`` is passed.
  * Never overwrites a non-empty AEGISAXIOM canonical field unless
    ``--overwrite-existing`` is passed.
  * Never promotes ``status`` from AEGISAXIOM-side ``draft`` unless
    ``--promote-status`` is passed AND the Zoho ``Contract Status``
    maps cleanly to a known AEGISAXIOM status enum.
  * Always preserves a raw snapshot under
    ``import_raw.zoho_contract_csv`` so a future audit can re-derive
    any field without re-reading the CSV.

Run::

    # Dry-run (default — produces a JSON report, no DB writes):
    python backend/scripts/zoho_contracts_missing_fields_apply.py \\
        --contracts-csv backend/imports/zoho/raw/Contract.csv \\
        --out backend/imports/zoho/reports/contracts_missing_fields_apply_report.json

    # Execute (irreversible — confirm dry-run summary first):
    python backend/scripts/zoho_contracts_missing_fields_apply.py \\
        --contracts-csv backend/imports/zoho/raw/Contract.csv \\
        --out backend/imports/zoho/reports/contracts_missing_fields_apply_report.json \\
        --execute

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
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from services.zoho_contract_csv import (  # noqa: E402
    ParsedContractRow,
    read_contract_csv,
    parse_contract_csv_row,
    ZOHO_TO_AEGISAXIOM_CONTRACT_TYPE,
)


logger = logging.getLogger("zoho_contracts_missing_fields_apply")


# ---------------------------------------------------------------------------
# Status promotion mapping (only used when --promote-status is set)
# ---------------------------------------------------------------------------

# Zoho Contract Status -> AEGISAXIOM status. Only values that map cleanly
# to the existing AEGISAXIOM allowed enum land here. Anything else stays as
# `*_raw` and is NEVER promoted automatically. The actual AEGISAXIOM allowed
# statuses are validated by the contracts route at create/update; we
# keep this mapping conservative to avoid drift.
ZOHO_TO_AEGISAXIOM_STATUS_PROMOTION: dict[str, str] = {
    "Approved": "active",
    "Active":   "active",
    "New":      "draft",
    "Closed":   "closed",
}


# ---------------------------------------------------------------------------
# Patch planner
# ---------------------------------------------------------------------------


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _is_zero_or_empty(value: Any) -> bool:
    """Either explicitly zero or empty/missing. Used by the canonical-
    field overwrite rule: AEGISAXIOM-side 0 is treated as "missing" so the
    apply step can hydrate from the Zoho snapshot without
    --overwrite-existing."""
    if _is_empty(value):
        return True
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _zoho_value_present(value: Any) -> bool:
    """A Zoho cell counts as "present" only when the parser produced
    a non-None value. The parser deliberately preserves 0.0 as a
    valid value (Zoho can explicitly write 0), so we accept that
    here too — only None / blank string is "not present"."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def build_candidate_patch(
    parsed: ParsedContractRow,
    aegisaxiom: dict[str, Any] | None,
    *,
    overwrite_existing: bool,
    promote_status: bool,
) -> dict[str, Any]:
    """Return a structured patch description for one CSV row.

    Output shape::

        {
            "set": {<field>: <value>, ...},  # the $set payload
            "conflicts": [...],              # canonical fields where
                                              # AEGISAXIOM has a non-empty
                                              # value that disagrees
                                              # with Zoho; not applied
                                              # without --overwrite-existing
            "warnings": [...],
            "needs_finance_review": [...],
        }

    Does NOT include any field that touches resources, resource_types,
    hourly_rate, project_ids, projects, approval config, or audit
    fields. The caller can safely apply ``set`` via update_one $set
    without worrying about those areas.
    """
    aegisaxiom = aegisaxiom or {}
    set_doc: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = list(parsed.warnings)
    needs_review: list[str] = []

    # Helper: assign a Zoho-derived value to a AEGISAXIOM field when safe.
    def _maybe_set(field: str, zoho_val: Any, *, canonical: bool):
        """Common rule. For *raw / *_raw / additive fields we set when
        AEGISAXIOM is empty. For canonical fields we set only when AEGISAXIOM is
        empty/zero, OR --overwrite-existing is on (in which case we
        also record a conflict for audit)."""
        if not _zoho_value_present(zoho_val):
            return
        aegisaxiom_val = aegisaxiom.get(field)
        if canonical:
            if _is_zero_or_empty(aegisaxiom_val):
                set_doc[field] = zoho_val
                return
            # AEGISAXIOM has a non-empty / non-zero value already.
            try:
                same = float(aegisaxiom_val) == float(zoho_val)
            except (TypeError, ValueError):
                same = aegisaxiom_val == zoho_val
            if same:
                return
            if overwrite_existing:
                set_doc[field] = zoho_val
                conflicts.append({
                    "field": field,
                    "aegisaxiom": aegisaxiom_val,
                    "zoho": zoho_val,
                    "action": "overwritten",
                })
            else:
                conflicts.append({
                    "field": field,
                    "aegisaxiom": aegisaxiom_val,
                    "zoho": zoho_val,
                    "action": "preserved_aegisaxiom",
                })
        else:
            # Additive / *_raw / metadata field — set only when AEGISAXIOM
            # is missing/blank, never overwrite. *_raw fields by
            # definition record the Zoho source, so once we have one
            # we keep it.
            if _is_empty(aegisaxiom_val):
                set_doc[field] = zoho_val

    # ---- Identity (never overwritten; only set when missing).
    if not aegisaxiom.get("external_contract_id") and parsed.record_id:
        set_doc["external_contract_id"] = parsed.record_id
    # external_ids is a nested dict; we patch sub-keys when empty.
    ext_ids = dict(aegisaxiom.get("external_ids") or {})
    changed_ext = False
    for sub_key, val in (
        ("zoho_contract_id", parsed.record_id),
        ("zoho_contract_owner_id", parsed.contract_owner_id),
        ("zoho_created_by_id", parsed.created_by_id),
        ("zoho_account_id", parsed.account_id),
        ("zoho_deal_id", parsed.deal_id),
        ("zoho_connected_to_id", parsed.connected_to_id),
    ):
        if _zoho_value_present(val) and _is_empty(ext_ids.get(sub_key)):
            ext_ids[sub_key] = val
            changed_ext = True
    if changed_ext:
        set_doc["external_ids"] = ext_ids

    # ---- Owner / creator (additive raw fields only).
    _maybe_set("contract_owner_name_raw", parsed.contract_owner_name_raw, canonical=False)
    _maybe_set("source_created_by", parsed.source_created_by, canonical=False)
    _maybe_set("source_created_at", parsed.source_created_at, canonical=False)
    _maybe_set("source_last_activity_at", parsed.source_last_activity_at, canonical=False)

    # ---- Account / deal (display fallbacks).
    _maybe_set("account_name", parsed.account_name, canonical=False)
    _maybe_set("deal_name", parsed.deal_name, canonical=False)
    if not aegisaxiom.get("contract_number") and parsed.contract_number:
        set_doc["contract_number"] = parsed.contract_number
    if not aegisaxiom.get("name") and parsed.name:
        set_doc["name"] = parsed.name

    # ---- Contract type. contract_type_raw is always copied when
    # blank. The canonical contract_type promotion is gated behind
    # --overwrite-existing because AEGISAXIOM's value may have been
    # intentionally set by an admin.
    _maybe_set("contract_type_raw", parsed.contract_type_raw, canonical=False)
    if parsed.contract_type and parsed.contract_type_raw:
        if _is_empty(aegisaxiom.get("contract_type")) or aegisaxiom.get("contract_type") == "other":
            set_doc["contract_type"] = parsed.contract_type
        elif aegisaxiom.get("contract_type") != parsed.contract_type:
            if overwrite_existing:
                set_doc["contract_type"] = parsed.contract_type
                conflicts.append({
                    "field": "contract_type", "aegisaxiom": aegisaxiom.get("contract_type"),
                    "zoho": parsed.contract_type, "action": "overwritten",
                })
            else:
                conflicts.append({
                    "field": "contract_type", "aegisaxiom": aegisaxiom.get("contract_type"),
                    "zoho": parsed.contract_type, "action": "preserved_aegisaxiom",
                })

    # ---- Invoice / AR/AP (all additive).
    _maybe_set("invoice_type_raw", parsed.invoice_type_raw, canonical=False)
    _maybe_set("number_of_invoices", parsed.number_of_invoices, canonical=False)
    _maybe_set("bill_on_actual_hours", parsed.bill_on_actual_hours, canonical=False)
    _maybe_set("hours_to_be_paid", parsed.hours_to_be_paid, canonical=False)
    _maybe_set("paid_hours", parsed.paid_hours, canonical=False)
    _maybe_set("ap_issues_raw", parsed.ap_issues_raw, canonical=False)

    # ---- Hours — snapshot fields (additive) + canonical (gated).
    _maybe_set("imported_total_hours", parsed.imported_total_hours, canonical=False)
    _maybe_set("imported_total_hours_alt", parsed.imported_total_hours_alt, canonical=False)
    _maybe_set("imported_remaining_hours", parsed.imported_remaining_hours, canonical=False)
    _maybe_set("imported_consumed_hours", parsed.imported_consumed_hours, canonical=False)
    _maybe_set("imported_consumed_percent", parsed.imported_consumed_percent, canonical=False)

    # Canonical total_hours / total_hours_purchased — Zoho snapshot is
    # offered when AEGISAXIOM-side is zero/empty (the documented hydration
    # case). For block-debit types only; Finance review required.
    if parsed.contract_type in {"boh", "boh_with_boa"}:
        if parsed.imported_total_hours is not None and parsed.imported_total_hours > 0:
            _maybe_set("total_hours", parsed.imported_total_hours, canonical=True)
            _maybe_set("total_hours_purchased", parsed.imported_total_hours, canonical=True)
            if "total_hours" in set_doc or "total_hours_purchased" in set_doc:
                needs_review.append("total_hours (verify against contract record)")

    # Canonical remaining_hours / consumed_hours_percent (no business
    # math reads remaining_hours today, but we hydrate when blank so
    # the UI can show the Zoho snapshot directly).
    _maybe_set("remaining_hours", parsed.imported_remaining_hours, canonical=True)
    _maybe_set("consumed_hours_percent", parsed.imported_consumed_percent, canonical=True)

    # hours_consumed — block-debit types only; needs Finance review
    # because of the consumed-hours baseline concern documented in
    # docs/zoho-migration/contracts_field_mapping.md.
    if parsed.contract_type in {"boh", "boh_with_boa"}:
        if parsed.imported_consumed_hours is not None and parsed.imported_consumed_hours > 0:
            _maybe_set("hours_consumed", parsed.imported_consumed_hours, canonical=True)
            if "hours_consumed" in set_doc:
                needs_review.append(
                    "hours_consumed (verify against historical time replay / Zoho snapshot)"
                )

    # ---- Status. Always preserve raw values; only promote canonical
    # status under --promote-status AND when AEGISAXIOM-side status is the
    # post-import default ("draft" or blank).
    _maybe_set("contract_status_raw", parsed.contract_status_raw, canonical=False)
    _maybe_set("zoho_status_raw", parsed.zoho_status_raw, canonical=False)
    if promote_status and parsed.contract_status_raw:
        target = ZOHO_TO_AEGISAXIOM_STATUS_PROMOTION.get(parsed.contract_status_raw)
        if target:
            current = aegisaxiom.get("status")
            if _is_empty(current) or current == "draft":
                set_doc["status"] = target
            elif current != target:
                conflicts.append({
                    "field": "status", "aegisaxiom": current, "zoho": target,
                    "action": "preserved_aegisaxiom_non_draft",
                })
        else:
            warnings.append({
                "kind": "unmapped_status_promotion",
                "zoho_raw": parsed.contract_status_raw,
            })

    # ---- Zoho metadata (all additive).
    _maybe_set("tags_raw", parsed.tags_raw, canonical=False)
    _maybe_set("zoho_unsubscribed_mode", parsed.zoho_unsubscribed_mode, canonical=False)
    _maybe_set("zoho_unsubscribed_at", parsed.zoho_unsubscribed_at, canonical=False)
    if parsed.zoho_locked is not None and "zoho_locked" not in (aegisaxiom or {}):
        set_doc["zoho_locked"] = parsed.zoho_locked
    _maybe_set("connected_to_module_raw", parsed.connected_to_module_raw, canonical=False)

    # ---- Raw CSV snapshot. We replace the snapshot whenever it
    # differs from what AEGISAXIOM already has, so a future re-run picks up
    # newly-added Zoho columns. The replacement is scoped under
    # import_raw.zoho_contract_csv — does not touch any canonical field.
    import_raw = dict(aegisaxiom.get("import_raw") or {})
    existing_snapshot = import_raw.get("zoho_contract_csv")
    if existing_snapshot != parsed.raw_source:
        import_raw["zoho_contract_csv"] = parsed.raw_source
        set_doc["import_raw"] = import_raw

    return {
        "set": set_doc,
        "conflicts": conflicts,
        "warnings": warnings,
        "needs_finance_review": sorted(set(needs_review)),
    }


# ---------------------------------------------------------------------------
# Mongo loader (read-only when --execute is off)
# ---------------------------------------------------------------------------


async def _connect_db():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        return None
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

    client = AsyncIOMotorClient(mongo_url)
    return client[db_name]


async def _load_aegisaxiom_contracts_by_zoho_id(db) -> dict[str, dict[str, Any]]:
    """Return ``{zoho_contract_id: aegisaxiom_doc}`` for every AEGISAXIOM contract
    that already carries an external_contract_id. The matcher uses
    this map first, then falls back to contract_number for rows
    whose external id is missing."""
    out: dict[str, dict[str, Any]] = {}
    async for c in db.contracts.find(
        {"external_contract_id": {"$ne": None}}, {"_id": 0},
    ):
        zid = c.get("external_contract_id") or (c.get("external_ids") or {}).get("zoho_contract_id")
        if zid:
            out[zid] = c
    return out


async def _load_aegisaxiom_contracts_by_number(db) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    async for c in db.contracts.find(
        {"contract_number": {"$ne": None}}, {"_id": 0},
    ):
        num = c.get("contract_number")
        if num:
            out[num] = c
    return out


# ---------------------------------------------------------------------------
# Apply runner
# ---------------------------------------------------------------------------


async def run_apply(
    *,
    csv_path: str,
    out_path: Optional[str],
    execute: bool,
    overwrite_existing: bool,
    promote_status: bool,
    allow_create: bool,
) -> dict[str, Any]:
    """Top-level coordinator. Read-only by default; ``execute`` is the
    only switch that touches Mongo (and even then only ``db.contracts``)."""
    rows = read_contract_csv(csv_path)
    parsed_rows = [parse_contract_csv_row(r) for r in rows]

    # Surface duplicate Record Id / Contract Number in the CSV before
    # we hit any DB — they'll cause unstable apply behaviour otherwise.
    seen_record_ids = Counter(p.record_id for p in parsed_rows if p.record_id)
    seen_numbers = Counter(p.contract_number for p in parsed_rows if p.contract_number)
    csv_warnings: list[dict[str, Any]] = []
    for rid, n in seen_record_ids.items():
        if n > 1:
            csv_warnings.append({"kind": "duplicate_record_id_in_csv", "record_id": rid, "count": n})
    for num, n in seen_numbers.items():
        if n > 1:
            csv_warnings.append({"kind": "duplicate_contract_number_in_csv", "contract_number": num, "count": n})

    db = await _connect_db()
    if execute and db is None:
        return {
            "mode": "execute",
            "errors": [
                "--execute requires MONGO_URL + DB_NAME in the environment; "
                "refusing to write."
            ],
            "csv_rows_seen": len(parsed_rows),
        }

    by_zid: dict[str, dict[str, Any]] = {}
    by_num: dict[str, dict[str, Any]] = {}
    if db is not None:
        by_zid = await _load_aegisaxiom_contracts_by_zoho_id(db)
        by_num = await _load_aegisaxiom_contracts_by_number(db)

    per_row: list[dict[str, Any]] = []
    field_patch_counts: Counter[str] = Counter()
    matched = 0
    unmatched = 0
    would_update = 0
    would_create = 0
    actual_updates = 0
    actual_creates = 0
    skipped = 0
    conflicts_total = 0
    warning_kinds: Counter[str] = Counter()

    now = datetime.now(timezone.utc)

    for parsed in parsed_rows:
        # Match preferentially by external id; fall back to contract_number.
        aegisaxiom = by_zid.get(parsed.record_id) if parsed.record_id else None
        match_source = "zoho_contract_id" if aegisaxiom else None
        if aegisaxiom is None and parsed.contract_number:
            aegisaxiom = by_num.get(parsed.contract_number)
            if aegisaxiom:
                match_source = "contract_number"
        if aegisaxiom is None:
            unmatched += 1
            row_entry = {
                "record_id": parsed.record_id,
                "contract_number": parsed.contract_number,
                "matched": False,
                "match_source": None,
                "action": "would_create" if allow_create else "skipped",
                "warnings": parsed.warnings,
            }
            if allow_create:
                would_create += 1
                if execute and db is not None:
                    # Minimal creation path — we never insert without
                    # a record_id and contract_number, and we set
                    # only the additive fields. NOTE: this branch is
                    # opt-in via --allow-create and is intentionally
                    # conservative; the prior Zoho importer remains
                    # the canonical creator.
                    if parsed.record_id and parsed.contract_number:
                        new_doc = {
                            "contract_id": f"contract_{parsed.record_id[:12]}",
                            "external_contract_id": parsed.record_id,
                            "contract_number": parsed.contract_number,
                            "name": parsed.name,
                            "contract_type": parsed.contract_type,
                            "import_source": "zoho",
                            "imported_at": now,
                            "created_at": now,
                            "updated_at": now,
                            "is_test_data": False,
                            "external_ids": {
                                "zoho_contract_id": parsed.record_id,
                                "zoho_account_id": parsed.account_id or None,
                                "zoho_deal_id": parsed.deal_id or None,
                            },
                            "project_ids": [],
                            "resources": [],
                            "resource_types": [],
                            "status": "draft",
                            "import_raw": {"zoho_contract_csv": parsed.raw_source},
                        }
                        await db.contracts.insert_one(new_doc)
                        actual_creates += 1
                        row_entry["action"] = "created"
                    else:
                        skipped += 1
                        row_entry["action"] = "skipped_missing_identity"
            else:
                skipped += 1
            for w in parsed.warnings:
                warning_kinds[w.get("kind", "")] += 1
            per_row.append(row_entry)
            continue

        matched += 1
        plan = build_candidate_patch(
            parsed, aegisaxiom,
            overwrite_existing=overwrite_existing,
            promote_status=promote_status,
        )
        set_doc = plan["set"]
        if not set_doc:
            skipped += 1
            per_row.append({
                "record_id": parsed.record_id,
                "contract_number": parsed.contract_number,
                "matched": True,
                "match_source": match_source,
                "action": "noop",
                "warnings": plan["warnings"],
                "conflicts": plan["conflicts"],
            })
            for c in plan["conflicts"]:
                conflicts_total += 1
            for w in plan["warnings"]:
                warning_kinds[w.get("kind", "")] += 1
            continue

        would_update += 1
        for f in set_doc:
            field_patch_counts[f] += 1
        for c in plan["conflicts"]:
            conflicts_total += 1
        for w in plan["warnings"]:
            warning_kinds[w.get("kind", "")] += 1
        if execute and db is not None:
            # Idempotency-friendly: include updated_at + imported_at.
            set_doc_with_stamps = {
                **set_doc,
                "imported_at": now,
                "updated_at": now,
                "import_source": "zoho",
            }
            await db.contracts.update_one(
                {"contract_id": aegisaxiom["contract_id"]},
                {"$set": set_doc_with_stamps},
            )
            actual_updates += 1
            action = "updated"
        else:
            action = "would_update"
        per_row.append({
            "contract_id": aegisaxiom["contract_id"],
            "record_id": parsed.record_id,
            "contract_number": parsed.contract_number,
            "matched": True,
            "match_source": match_source,
            "action": action,
            "fields_patched": sorted(set_doc.keys()),
            "needs_finance_review": plan["needs_finance_review"],
            "conflicts": plan["conflicts"],
            "warnings": plan["warnings"],
        })

    return {
        "mode": "execute" if execute else "dry_run",
        "csv_path": csv_path,
        "csv_rows_seen": len(parsed_rows),
        "csv_warnings": csv_warnings,
        "options": {
            "overwrite_existing": overwrite_existing,
            "promote_status": promote_status,
            "allow_create": allow_create,
        },
        "match_counts": {
            "matched": matched,
            "unmatched": unmatched,
        },
        "action_counts": {
            "would_update": would_update,
            "would_create": would_create,
            "actual_updates": actual_updates,
            "actual_creates": actual_creates,
            "skipped": skipped,
        },
        "field_patch_counts": dict(field_patch_counts),
        "conflicts_total": conflicts_total,
        "warning_kinds": dict(warning_kinds),
        "per_row": per_row,
        "finished_at": now.isoformat(),
        "notes": [
            (
                "Default mode is dry-run. Writes happen only when "
                "--execute is explicitly passed."
            ),
            (
                "Never overwrites resources, resource_types, hourly_rate, "
                "project_ids, projects, approval configuration, or local "
                "AEGISAXIOM audit fields. Use git history to verify."
            ),
            (
                "Canonical fields (total_hours, hours_consumed, status) "
                "are gated behind --overwrite-existing / --promote-status. "
                "Finance review required where flagged."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(report: dict[str, Any]) -> str:
    a = report.get("action_counts") or {}
    m = report.get("match_counts") or {}
    lines = []
    lines.append("Zoho Contracts Missing-Fields Apply")
    lines.append("=" * 64)
    lines.append(f"Mode:                  {report.get('mode')}")
    lines.append(f"CSV file:              {report.get('csv_path')}")
    lines.append(f"CSV rows seen:         {report.get('csv_rows_seen')}")
    lines.append("")
    lines.append("MATCHING (against existing AEGISAXIOM contracts)")
    lines.append(f"  matched:             {m.get('matched', 0)}")
    lines.append(f"  unmatched:           {m.get('unmatched', 0)}")
    lines.append("")
    lines.append("ACTIONS")
    lines.append(f"  would_update:        {a.get('would_update', 0)}")
    lines.append(f"  would_create:        {a.get('would_create', 0)}")
    lines.append(f"  actual_updates:      {a.get('actual_updates', 0)}")
    lines.append(f"  actual_creates:      {a.get('actual_creates', 0)}")
    lines.append(f"  skipped:             {a.get('skipped', 0)}")
    lines.append(f"  conflicts_total:     {report.get('conflicts_total', 0)}")
    lines.append("")
    fp = report.get("field_patch_counts") or {}
    if fp:
        lines.append("FIELD PATCH COUNTS (top 15)")
        for f, n in sorted(fp.items(), key=lambda kv: -kv[1])[:15]:
            lines.append(f"  {f:42s}: {n}")
        lines.append("")
    wk = report.get("warning_kinds") or {}
    if wk:
        lines.append("WARNING KINDS")
        for k, n in sorted(wk.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {k:42s}: {n}")
    lines.append("=" * 64)
    if report.get("mode") == "dry_run":
        lines.append(
            "Mode: DRY-RUN. No DB writes performed. Review the JSON "
            "report and re-run with --execute when approved."
        )
    else:
        lines.append(
            "Mode: EXECUTE. Writes were applied to db.contracts only. "
            "resources / resource_types / hourly_rate / project_ids / "
            "approval config were NOT touched."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply hydration patches from Zoho Contract.csv to existing "
            "AEGISAXIOM contracts. Default mode is dry-run; --execute is the "
            "only path that writes to Mongo."
        ),
    )
    parser.add_argument(
        "--contracts-csv", required=True,
        help="Path to the Zoho Contract.csv export.",
    )
    parser.add_argument(
        "--out",
        default="imports/zoho/reports/contracts_missing_fields_apply_report.json",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Write to db.contracts. Requires MONGO_URL + DB_NAME.",
    )
    parser.add_argument(
        "--overwrite-existing", action="store_true",
        help="Permit overwriting non-empty AEGISAXIOM canonical fields when "
             "Zoho disagrees. Off by default — conflicts are reported "
             "but not applied without this flag.",
    )
    parser.add_argument(
        "--promote-status", action="store_true",
        help="Permit promoting AEGISAXIOM status from 'draft' to a mapped "
             "Zoho Contract Status (Approved -> active, etc.). Raw "
             "status_raw is preserved regardless of this flag.",
    )
    parser.add_argument(
        "--allow-create", action="store_true",
        help="Insert a new contract for any CSV row that does not "
             "match an existing AEGISAXIOM contract. Off by default.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        report = asyncio.run(run_apply(
            csv_path=args.contracts_csv,
            out_path=args.out,
            execute=args.execute,
            overwrite_existing=args.overwrite_existing,
            promote_status=args.promote_status,
            allow_create=args.allow_create,
        ))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True, default=str)
    print(format_console_summary(report))
    if args.out:
        print(f"\nReport written to: {args.out}")
    if report.get("errors"):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
