"""Read-only import-plan generator for Zoho Contracts → AEGISAXIOM Contracts.

Third slice of the Zoho historical migration. Joins the local Zoho
Contract export with the Accounts and Deals/Opportunities exports and
produces a JSON plan of what would land in the AEGISAXIOM ``contracts``
collection — without writing anything to a database, calling any
Zoho API, or mutating any input file.

Run:
    python backend/scripts/zoho_contracts_import_plan.py \\
        --contracts backend/imports/zoho/raw/Contract.csv \\
        --accounts  backend/imports/zoho/raw/Accounts.csv \\
        --deals     backend/imports/zoho/raw/Deals.csv \\
        --out       backend/imports/zoho/reports/contracts_import_plan.json

Pass ``--allow-orphan-deals`` to count contracts whose Deals.id has no
matching row in the Deals export toward the planned-import total. The
warning is still emitted, but the contract lands in AEGISAXIOM with
``external_ids.zoho_deal_id`` stamped and ``deal_ids=[]``.

Exit codes:
    0 — All three CSVs parsed, plan written. Per-row blockers do NOT
        flip the exit code; the report's ``ready_for_import`` flag and
        the ``blocked[]`` list carry that signal.
    1 — Input file missing, malformed CSV, or missing structural
        headers.

The companion mapping doc is
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
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Header normalisation — kept consistent with the dry-run validators.
# ---------------------------------------------------------------------------

_APOSTROPHE_CHARS = ("'", "’", "ʼ")


def _normalize_header(raw: str) -> str:
    if raw is None:
        return ""
    s = raw.lstrip("﻿").strip()
    for apo in _APOSTROPHE_CHARS:
        s = s.replace(apo, "'")
    return s


_CONTRACT_HEADER_VARIANTS: dict[str, tuple[str, ...]] = {
    "record_id":         ("Record Id",),
    "contract_number":   ("Contract Number",),
    "contract_name":     ("Contract Name",),
    "account_name":      ("Account",),
    "account_id":        ("Account.id",),
    "deal_name":         ("Deals",),
    "deal_id":           ("Deals.id",),
    "contract_type":     ("Contract Type",),
    "invoice_type":      ("Invoice Type",),
    "contract_status":   ("Contract Status",),
    "total_hours_apos":  ("Total Contract Hour's",),
    "total_hours_plain": ("Total Contract Hours",),
    "remaining_hours":   ("Remaining Hour's", "Remaining Hours"),
    "consumed_hours":    ("Total Consumed Hour's", "Total Consumed Hours"),
    "consumed_pct":      ("Consumed Hour's %", "Consumed Hours %"),
    "contract_owner":    ("Contract Owner",),
    "created_time":      ("Created Time",),
    "modified_time":     ("Modified Time", "Last Activity Time"),
}

_ACCOUNT_HEADER_VARIANTS: dict[str, tuple[str, ...]] = {
    "record_id":   ("Record Id", "Account Record Id", "Account Record ID"),
    "account_name": ("Account Name", "Account", "Name"),
}

_DEAL_HEADER_VARIANTS: dict[str, tuple[str, ...]] = {
    "record_id":   (
        "Record Id", "Deal Record Id", "Deal Record ID",
        "Opportunity Record Id", "Opportunity Record ID",
    ),
    "deal_name":   (
        "Deal Name", "Opportunity Name", "Deal", "Name",
    ),
}

_CONTRACT_REQUIRED_HEADERS: tuple[str, ...] = (
    "Record Id", "Contract Number", "Contract Name",
    "Account.id", "Deals.id",
    "Contract Type", "Invoice Type", "Contract Status",
)
_ACCOUNT_REQUIRED_HEADER_GROUP: tuple[tuple[str, ...], ...] = (
    _ACCOUNT_HEADER_VARIANTS["record_id"],
)
_DEAL_REQUIRED_HEADER_GROUP: tuple[tuple[str, ...], ...] = (
    _DEAL_HEADER_VARIANTS["record_id"],
)

# Match the dry-run validator's tolerance so warnings agree.
_HOUR_TOLERANCE = 0.05
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


def _coerce_float(raw: Any) -> tuple[float | None, bool]:
    """Return ``(value, was_empty)``. ``value`` is None on parse failure."""
    if raw is None:
        return None, True
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw), False
    s = str(raw).strip()
    if not s:
        return None, True
    s2 = s.replace(",", "").rstrip("%").strip()
    try:
        return float(s2), False
    except ValueError:
        return None, False


# ---------------------------------------------------------------------------
# CSV reader (self-contained — same semantics as the dry-run validators)
# ---------------------------------------------------------------------------


def _read_csv(
    path: str,
    *,
    required_headers: tuple[str, ...] = (),
    required_header_groups: tuple[tuple[str, ...], ...] = (),
) -> tuple[list[dict[str, str]], list[str]]:
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

    missing_flat = [h for h in required_headers if h not in headers]
    missing_groups: list[tuple[str, ...]] = [
        group for group in required_header_groups
        if not any(variant in headers for variant in group)
    ]
    if missing_flat or missing_groups:
        gap = list(missing_flat) + [
            " | ".join(group) for group in missing_groups
        ]
        raise RuntimeError(
            f"{path!r}: missing required structural headers: {gap}. "
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
# Side-export lookups
# ---------------------------------------------------------------------------


def _index_by_id(
    rows: list[dict[str, str]],
    id_variants: tuple[str, ...],
    name_variants: tuple[str, ...],
) -> tuple[dict[str, str], Counter[str]]:
    """Return ``(id → display_name, duplicate_counter)`` for one side
    export. ``duplicate_counter`` contains only ids that appeared more
    than once.
    """
    by_id: dict[str, str] = {}
    seen: Counter[str] = Counter()
    for row in rows:
        rid = _resolve_field(row, id_variants)
        if not rid:
            continue
        seen[rid] += 1
        # First occurrence wins for the display-name pick. Subsequent
        # duplicates are surfaced separately via the counter and do
        # not overwrite a value that already resolved cleanly.
        if rid not in by_id:
            by_id[rid] = _resolve_field(row, name_variants)
    duplicates = Counter({k: v for k, v in seen.items() if v > 1})
    return by_id, duplicates


# ---------------------------------------------------------------------------
# Per-contract plan row
# ---------------------------------------------------------------------------


def _build_aegisaxiom_contract_candidate(
    *,
    contract_row: dict[str, str],
    account_id: str,
    account_name: str,
    deal_id: str,
    deal_name: str,
    contract_record_id: str,
    contract_number: str,
    contract_name: str,
    contract_type_raw: str,
    invoice_type_raw: str,
    status_raw: str,
) -> dict[str, Any]:
    """Build the additive shape the future importer will hand to the
    AEGISAXIOM Contracts collection. ``raw_source`` is the entire input row
    so a follow-up reconciliation job can re-derive any field without
    re-reading the CSV.
    """
    total_apos, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["total_hours_apos"])
    )
    total_plain, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["total_hours_plain"])
    )
    remaining, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["remaining_hours"])
    )
    consumed, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["consumed_hours"])
    )
    pct, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["consumed_pct"])
    )
    # Mapping decision baked in: apostrophe variant of "Total Contract
    # Hour's" is the source of truth; "Total Contract Hours" is the
    # secondary snapshot. Documented in
    # docs/zoho-migration/contracts_field_mapping.md.
    total = total_apos if total_apos is not None else total_plain

    return {
        "external_ids": {
            "zoho_contract_id": contract_record_id,
            "zoho_account_id": account_id or None,
            "zoho_deal_id": deal_id or None,
        },
        "contract_number": contract_number or None,
        "name": contract_name or None,
        "account_name": account_name or None,
        "opportunity_name": deal_name or None,
        "contract_type_raw": contract_type_raw or None,
        "invoice_type_raw": invoice_type_raw or None,
        "status_raw": status_raw or None,
        "total_hours_snapshot": total,
        "remaining_hours_snapshot": remaining,
        "consumed_hours_snapshot": consumed,
        "consumed_percent_snapshot": pct,
        "source_created_at": _resolve_field(
            contract_row, _CONTRACT_HEADER_VARIANTS["created_time"]
        ) or None,
        "source_modified_at": _resolve_field(
            contract_row, _CONTRACT_HEADER_VARIANTS["modified_time"]
        ) or None,
        "raw_source": dict(contract_row),
    }


def _collect_hour_anomalies(
    contract_row: dict[str, str],
    contract_record_id: str,
) -> list[dict[str, Any]]:
    """Per-contract hour anomalies — mirrors the dry-run validator's
    semantics so the plan's anomaly counts line up with the dry-run.
    """
    out: list[dict[str, Any]] = []
    total_apos, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["total_hours_apos"])
    )
    total_plain, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["total_hours_plain"])
    )
    remaining, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["remaining_hours"])
    )
    consumed, _ = _coerce_float(
        _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS["consumed_hours"])
    )

    if remaining is not None and remaining < 0:
        out.append({
            "kind": "negative_remaining",
            "zoho_contract_id": contract_record_id,
            "remaining": remaining,
        })
    if (
        total_apos is not None and total_plain is not None
        and abs(total_apos - total_plain) > _HOUR_TOLERANCE
    ):
        out.append({
            "kind": "total_hours_disagreement",
            "zoho_contract_id": contract_record_id,
            "total_apostrophe": total_apos,
            "total_plain": total_plain,
            "delta": round(total_apos - total_plain, 4),
        })
    ref_total = total_apos if total_apos is not None else total_plain
    if (
        ref_total is not None and consumed is not None and remaining is not None
        and abs((consumed + remaining) - ref_total) > _HOUR_TOLERANCE
    ):
        out.append({
            "kind": "consumed_plus_remaining_mismatch",
            "zoho_contract_id": contract_record_id,
            "consumed": consumed,
            "remaining": remaining,
            "total": ref_total,
            "delta": round((consumed + remaining) - ref_total, 4),
        })
    return out


def _collect_invalid_numeric(
    contract_row: dict[str, str],
    contract_record_id: str,
) -> list[dict[str, Any]]:
    """Per-contract invalid-numeric blockers. Same semantics as the
    contract dry-run; bumped to a *blocker* here because the plan
    cannot decide what the AEGISAXIOM-side numeric snapshot should hold.
    """
    out: list[dict[str, Any]] = []
    for field_key, header_label in (
        ("total_hours_apos", "Total Contract Hour's"),
        ("total_hours_plain", "Total Contract Hours"),
        ("remaining_hours", "Remaining Hour's"),
        ("consumed_hours", "Total Consumed Hour's"),
        ("consumed_pct", "Consumed Hour's %"),
    ):
        raw = _resolve_field(contract_row, _CONTRACT_HEADER_VARIANTS[field_key])
        value, was_empty = _coerce_float(raw)
        if value is None and not was_empty:
            out.append({
                "kind": "invalid_numeric",
                "zoho_contract_id": contract_record_id,
                "field": header_label,
                "raw_value": raw,
            })
    return out


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


def build_import_plan(
    *,
    contract_rows: list[dict[str, str]],
    account_rows: list[dict[str, str]],
    deal_rows: list[dict[str, str]],
    contracts_file: str,
    accounts_file: str,
    deals_file: str,
    allow_orphan_deals: bool = False,
) -> dict[str, Any]:
    """Pure plan builder. Returns the full JSON report shape.

    Builders are isolated from I/O so tests can call this with
    in-memory rows; the CLI's ``run()`` is the only function that
    touches the filesystem.
    """
    accounts_by_id, account_dupes = _index_by_id(
        account_rows,
        _ACCOUNT_HEADER_VARIANTS["record_id"],
        _ACCOUNT_HEADER_VARIANTS["account_name"],
    )
    deals_by_id, deal_dupes = _index_by_id(
        deal_rows,
        _DEAL_HEADER_VARIANTS["record_id"],
        _DEAL_HEADER_VARIANTS["deal_name"],
    )

    planned: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    contract_types: Counter[str] = Counter()
    invoice_types: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    hour_anomalies_by_kind: Counter[str] = Counter()
    contract_record_ids: Counter[str] = Counter()

    # First pass: count contract record ids so duplicate detection
    # can flip those rows to blocked without changing the join logic.
    for row in contract_rows:
        rid = _resolve_field(row, _CONTRACT_HEADER_VARIANTS["record_id"])
        if rid:
            contract_record_ids[rid] += 1
    duplicate_contract_ids = {
        k for k, n in contract_record_ids.items() if n > 1
    }

    for idx, row in enumerate(contract_rows):
        row_no = idx + 1
        record_id = _resolve_field(row, _CONTRACT_HEADER_VARIANTS["record_id"])
        contract_number = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["contract_number"]
        )
        contract_name = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["contract_name"]
        )
        account_id = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["account_id"]
        )
        deal_id = _resolve_field(row, _CONTRACT_HEADER_VARIANTS["deal_id"])
        contract_type_raw = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["contract_type"]
        )
        invoice_type_raw = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["invoice_type"]
        )
        status_raw = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["contract_status"]
        )
        contract_account_name_fallback = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["account_name"]
        )
        contract_deal_name_fallback = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["deal_name"]
        )

        if contract_type_raw:
            contract_types[contract_type_raw] += 1
        if invoice_type_raw:
            invoice_types[invoice_type_raw] += 1
        if status_raw:
            statuses[status_raw] += 1

        # ---- Per-row blockers (default policy) -------------------
        row_blockers: list[dict[str, Any]] = []
        row_warnings: list[dict[str, Any]] = []

        if not record_id:
            row_blockers.append({
                "kind": "missing_record_id", "row": row_no,
            })
        elif record_id in duplicate_contract_ids:
            row_blockers.append({
                "kind": "duplicate_contract_record_id",
                "zoho_contract_id": record_id,
                "row": row_no,
            })
        if not contract_number:
            row_warnings.append({"kind": "missing_contract_number"})
        if not contract_type_raw:
            row_blockers.append({"kind": "missing_contract_type"})
        if not status_raw:
            row_blockers.append({"kind": "missing_contract_status"})
        if not invoice_type_raw:
            row_warnings.append({"kind": "missing_invoice_type"})

        # ---- Hour validity ---------------------------------------
        invalid_numeric = _collect_invalid_numeric(row, record_id)
        if invalid_numeric:
            row_blockers.extend(invalid_numeric)

        anomalies = _collect_hour_anomalies(row, record_id)
        if anomalies:
            for a in anomalies:
                hour_anomalies_by_kind[a["kind"]] += 1
            row_warnings.extend(anomalies)

        # ---- Side joins ------------------------------------------
        account_resolved = bool(account_id) and account_id in accounts_by_id
        deal_resolved = bool(deal_id) and deal_id in deals_by_id
        if account_id and not account_resolved:
            row_blockers.append({
                "kind": "orphan_zoho_account_id",
                "zoho_account_id": account_id,
            })
        if deal_id and not deal_resolved:
            # Orphan deal: warning by default; only counts toward
            # ``ready_for_import`` when --allow-orphan-deals is set.
            row_warnings.append({
                "kind": "orphan_zoho_deal_id",
                "zoho_deal_id": deal_id,
            })

        account_name_resolved = (
            accounts_by_id.get(account_id, "") if account_resolved else ""
        )
        deal_name_resolved = (
            deals_by_id.get(deal_id, "") if deal_resolved else ""
        )

        # ---- AegisAxiom candidate --------------------------------------
        candidate = _build_aegisaxiom_contract_candidate(
            contract_row=row,
            account_id=account_id,
            account_name=account_name_resolved or contract_account_name_fallback,
            deal_id=deal_id,
            deal_name=deal_name_resolved or contract_deal_name_fallback,
            contract_record_id=record_id,
            contract_number=contract_number,
            contract_name=contract_name,
            contract_type_raw=contract_type_raw,
            invoice_type_raw=invoice_type_raw,
            status_raw=status_raw,
        )

        plan_row = {
            "source": "zoho",
            "zoho_contract_id": record_id or None,
            "contract_number": contract_number or None,
            "name": contract_name or None,
            "account": {
                "zoho_account_id": account_id or None,
                "name": account_name_resolved or contract_account_name_fallback or None,
                "resolved": bool(account_resolved),
            },
            "deal": {
                "zoho_deal_id": deal_id or None,
                "name": deal_name_resolved or contract_deal_name_fallback or None,
                "resolved": bool(deal_resolved),
            },
            "aegisaxiom_contract_candidate": candidate,
            "warnings": row_warnings,
        }

        if row_blockers:
            blocked.append({**plan_row, "blockers": row_blockers})
        else:
            # Orphan-deal policy: in default mode the orphan keeps the
            # row OUT of planned[] (and ready_for_import false). With
            # --allow-orphan-deals the row stays in planned[] and the
            # warning is the only signal.
            has_orphan_deal = any(
                w["kind"] == "orphan_zoho_deal_id" for w in row_warnings
            )
            if has_orphan_deal and not allow_orphan_deals:
                blocked.append({
                    **plan_row,
                    "blockers": [
                        {
                            "kind": "orphan_zoho_deal_id_default_policy",
                            "zoho_deal_id": deal_id,
                            "hint": (
                                "Re-run with --allow-orphan-deals to "
                                "include this row in the planned "
                                "import; the contract would land with "
                                "external_ids.zoho_deal_id stamped and "
                                "deal_ids=[]."
                            ),
                        },
                    ],
                })
            else:
                planned.append(plan_row)

        for w in row_warnings:
            warnings.append({
                **w,
                "zoho_contract_id": record_id or None,
                "row": row_no,
            })

    # ---- Side-export duplicates ---------------------------------
    if account_dupes:
        warnings.append({
            "kind": "duplicate_account_record_id_in_accounts_export",
            "count": len(account_dupes),
            "samples": [
                {"value": v, "count": n}
                for v, n in account_dupes.most_common(_SAMPLE_LIMIT)
            ],
        })
    if deal_dupes:
        warnings.append({
            "kind": "duplicate_deal_record_id_in_deals_export",
            "count": len(deal_dupes),
            "samples": [
                {"value": v, "count": n}
                for v, n in deal_dupes.most_common(_SAMPLE_LIMIT)
            ],
        })

    ready_for_import = not blocked

    return {
        "source_files": {
            "contracts": contracts_file,
            "accounts": accounts_file,
            "deals": deals_file,
        },
        "options": {"allow_orphan_deals": allow_orphan_deals},
        "total_contracts": len(contract_rows),
        "planned_import_count": len(planned),
        "blocked_count": len(blocked),
        "warning_count": len(warnings),
        "ready_for_import": ready_for_import,
        "contracts": planned,
        "blocked": blocked,
        "warnings": warnings,
        "enum_summary": {
            "contract_types": dict(contract_types),
            "invoice_types": dict(invoice_types),
            "statuses": dict(statuses),
        },
        "hour_anomaly_summary": dict(hour_anomalies_by_kind),
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Zoho Contracts Import Plan")
    lines.append("=" * 60)
    sf = report["source_files"]
    lines.append(f"Contracts file:         {sf['contracts']}")
    lines.append(f"Accounts file:          {sf['accounts']}")
    lines.append(f"Deals file:             {sf['deals']}")
    lines.append(
        f"Allow orphan deals:     "
        f"{'YES' if report['options']['allow_orphan_deals'] else 'no'}"
    )
    lines.append("")
    lines.append(f"Total contracts:        {report['total_contracts']}")
    lines.append(f"Planned import count:   {report['planned_import_count']}")
    lines.append(f"Blocked count:          {report['blocked_count']}")
    lines.append(f"Warning count:          {report['warning_count']}")
    lines.append("")
    lines.append(
        f"Ready for import:       "
        f"{'YES' if report['ready_for_import'] else 'NO'}"
    )

    enums = report["enum_summary"]
    if enums["contract_types"]:
        lines.append("")
        lines.append("Contract Types:")
        for k, v in sorted(enums["contract_types"].items()):
            lines.append(f"  {k}: {v}")
    if enums["invoice_types"]:
        lines.append("Invoice Types:")
        for k, v in sorted(enums["invoice_types"].items()):
            lines.append(f"  {k}: {v}")
    if enums["statuses"]:
        lines.append("Statuses:")
        for k, v in sorted(enums["statuses"].items()):
            lines.append(f"  {k}: {v}")

    if report["hour_anomaly_summary"]:
        lines.append("")
        lines.append("Hour anomalies:")
        for k, v in sorted(report["hour_anomaly_summary"].items()):
            lines.append(f"  {k}: {v}")

    if report["blocked"]:
        # Roll up blocker kinds without printing customer rows.
        kinds: Counter[str] = Counter()
        for b in report["blocked"]:
            for blk in b.get("blockers", []):
                kinds[blk["kind"]] += 1
        lines.append("")
        lines.append("Blocker categories:")
        for k, v in kinds.most_common():
            lines.append(f"  {k}: {v}")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(
    *,
    contracts_path: str,
    accounts_path: str,
    deals_path: str,
    out_path: str,
    allow_orphan_deals: bool = False,
) -> dict[str, Any]:
    contract_rows, _ = _read_csv(
        contracts_path,
        required_headers=_CONTRACT_REQUIRED_HEADERS,
    )
    account_rows, _ = _read_csv(
        accounts_path,
        required_header_groups=_ACCOUNT_REQUIRED_HEADER_GROUP,
    )
    deal_rows, _ = _read_csv(
        deals_path,
        required_header_groups=_DEAL_REQUIRED_HEADER_GROUP,
    )
    report = build_import_plan(
        contract_rows=contract_rows,
        account_rows=account_rows,
        deal_rows=deal_rows,
        contracts_file=contracts_path,
        accounts_file=accounts_path,
        deals_file=deals_path,
        allow_orphan_deals=allow_orphan_deals,
    )
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    return report


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only import-plan generator for Zoho Contracts → "
            "AEGISAXIOM Contracts."
        ),
    )
    parser.add_argument("--contracts", required=True,
                        help="Path to the Zoho Contract CSV export.")
    parser.add_argument("--accounts", required=True,
                        help="Path to the Zoho Accounts CSV export.")
    parser.add_argument("--deals", required=True,
                        help="Path to the Zoho Deals/Opportunities CSV export.")
    parser.add_argument("--out", required=True,
                        help="Destination path for the JSON plan.")
    parser.add_argument(
        "--allow-orphan-deals",
        action="store_true",
        help=(
            "Include contracts whose Deals.id has no matching row in "
            "the Deals export in the planned-import total. The warning "
            "is still emitted; the row would land with "
            "external_ids.zoho_deal_id stamped and deal_ids=[]."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = run(
            contracts_path=args.contracts,
            accounts_path=args.accounts,
            deals_path=args.deals,
            out_path=args.out,
            allow_orphan_deals=args.allow_orphan_deals,
        )
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:  # pragma: no cover — _read_csv pre-checks
        print(f"error: input file not found: {e}", file=sys.stderr)
        return 1
    except csv.Error as e:  # pragma: no cover — defensive
        print(f"error: malformed CSV: {e}", file=sys.stderr)
        return 1

    print(format_console_summary(report))
    print(f"\nReport written to: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
