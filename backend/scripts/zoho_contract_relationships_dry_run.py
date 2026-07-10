"""Read-only relationship validator for Zoho Contracts → Accounts/Deals.

Second slice of the Zoho historical migration. Confirms — before any
importer code is written — that every Contract row in the export can
resolve its parent Account (``Account.id``) and Deal (``Deals.id``)
against the corresponding Zoho Accounts and Deals/Opportunities CSV
exports.

Run:
    python backend/scripts/zoho_contract_relationships_dry_run.py \\
        --contracts imports/zoho/raw/Contract.csv \\
        --accounts  imports/zoho/raw/Accounts.csv \\
        --deals     imports/zoho/raw/Deals.csv \\
        --out       imports/zoho/reports/contracts_relationships_dry_run.json

Exit codes:
    0 — All three CSVs parsed; report written. Relationship gaps in
        the report are NOT a non-zero exit (operators read the JSON
        to decide next steps).
    1 — One or more input files missing, malformed CSV, or missing
        required structural headers. The script must fail loudly when
        it cannot even line up the inputs.

Behaviour:
    * No DB connection, no Zoho API, no writes back to any source.
    * Reads three local CSVs, builds dictionaries keyed by Record Id,
      and produces resolution counts.
    * Tolerates UTF-8 BOM and the three apostrophe variants Zoho uses
      in column names ('``'``', U+2019, U+02BC).
    * Headers are matched case-sensitively after whitespace + BOM
      normalisation. The Zoho field names below are the canonical
      defaults; common aliases (``Deal Record Id``, ``Account Record
      ID``) are accepted as fallbacks.

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
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Header normalisation — shared with zoho_contracts_dry_run.py semantics
# ---------------------------------------------------------------------------

_APOSTROPHE_CHARS = ("'", "’", "ʼ")


def _normalize_header(raw: str) -> str:
    if raw is None:
        return ""
    s = raw.lstrip("﻿").strip()
    for apo in _APOSTROPHE_CHARS:
        s = s.replace(apo, "'")
    return s


# Per-export header variants. The relationship check only needs
# Record Id from each side; display-name columns are documented so
# the future importer's joined view has a label even when AEGISAXIOM can't
# yet resolve the account/deal in its own data.
_CONTRACT_HEADER_VARIANTS: dict[str, tuple[str, ...]] = {
    "record_id":   ("Record Id",),
    "account_id":  ("Account.id",),
    "account":     ("Account",),
    "deal_id":     ("Deals.id",),
    "deal":        ("Deals",),
}

_ACCOUNT_HEADER_VARIANTS: dict[str, tuple[str, ...]] = {
    "record_id":   (
        "Record Id", "Account Record Id", "Account Record ID",
    ),
    "account_name": (
        "Account Name", "Account", "Name",
    ),
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

# Structural-header guards — the validator refuses to proceed if any
# of these is missing because the entire relationship match depends
# on them.
_CONTRACT_REQUIRED_HEADERS: tuple[str, ...] = (
    "Record Id", "Account.id", "Deals.id",
)
_ACCOUNT_REQUIRED_HEADER_GROUP: tuple[tuple[str, ...], ...] = (
    _ACCOUNT_HEADER_VARIANTS["record_id"],
)
_DEAL_REQUIRED_HEADER_GROUP: tuple[tuple[str, ...], ...] = (
    _DEAL_HEADER_VARIANTS["record_id"],
)

_SAMPLE_LIMIT = 50


# ---------------------------------------------------------------------------
# CSV reader
# ---------------------------------------------------------------------------


def _resolve_field(row: dict[str, str], variants: tuple[str, ...]) -> str:
    for v in variants:
        if v in row:
            val = row[v]
            if val is None:
                continue
            return val.strip() if isinstance(val, str) else val
    return ""


def _read_csv(
    path: str,
    *,
    required_headers: tuple[str, ...] = (),
    required_header_groups: tuple[tuple[str, ...], ...] = (),
) -> tuple[list[dict[str, str]], list[str]]:
    """Read a Zoho CSV with UTF-8 + BOM handling and normalised headers.

    ``required_headers`` is a flat tuple where every entry MUST appear
    in the file's headers (after normalisation). ``required_header_groups``
    is a tuple-of-tuples where each inner tuple supplies the accepted
    variants for one column; at least one variant per group must be
    present.
    """
    if not os.path.isfile(path):
        raise RuntimeError(f"Input file not found: {path!r}")

    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise RuntimeError(f"CSV has no header row (empty or malformed): {path!r}")

    headers = [_normalize_header(h) for h in reader.fieldnames]

    missing_flat = [h for h in required_headers if h not in headers]
    missing_groups: list[tuple[str, ...]] = []
    for group in required_header_groups:
        if not any(variant in headers for variant in group):
            missing_groups.append(group)

    if missing_flat or missing_groups:
        gap_detail: list[str] = list(missing_flat)
        for group in missing_groups:
            gap_detail.append(" | ".join(group))
        raise RuntimeError(
            f"{path!r}: missing required structural headers: {gap_detail}. "
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
# Relationship resolution
# ---------------------------------------------------------------------------


def _build_id_index(
    rows: list[dict[str, str]],
    id_variants: tuple[str, ...],
) -> tuple[set[str], Counter[str]]:
    """Return ``(unique_ids, duplicate_counter)`` for a side export.

    ``duplicate_counter`` only contains ids that appear more than once
    — keeping the report concise even when most ids are unique.
    """
    counter: Counter[str] = Counter()
    for row in rows:
        rid = _resolve_field(row, id_variants)
        if rid:
            counter[rid] += 1
    unique_ids = set(counter)
    duplicates = Counter({k: v for k, v in counter.items() if v > 1})
    return unique_ids, duplicates


def resolve_relationships(
    *,
    contracts: list[dict[str, str]],
    accounts: list[dict[str, str]],
    deals: list[dict[str, str]],
    contracts_file: str,
    accounts_file: str,
    deals_file: str,
) -> dict[str, Any]:
    """Pure analysis. Returns the full JSON report shape."""
    account_ids, account_dupes = _build_id_index(
        accounts, _ACCOUNT_HEADER_VARIANTS["record_id"],
    )
    deal_ids, deal_dupes = _build_id_index(
        deals, _DEAL_HEADER_VARIANTS["record_id"],
    )

    resolved_accounts = 0
    resolved_deals = 0
    resolved_both = 0

    unresolved_accounts: list[dict[str, Any]] = []
    unresolved_deals: list[dict[str, Any]] = []
    unresolved_both: list[dict[str, Any]] = []

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, row in enumerate(contracts):
        contract_record = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["record_id"]
        )
        account_id = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["account_id"]
        )
        deal_id = _resolve_field(
            row, _CONTRACT_HEADER_VARIANTS["deal_id"]
        )

        # Display labels are reported back so a reviewer can scan the
        # unresolved list without cross-referencing Zoho — never the
        # full row, just the IDs and the labels Zoho already showed
        # in the source export.
        contract_label = _resolve_field(
            row, ("Contract Number", "Contract Name"),
        ) or None

        acct_present_in_export = bool(account_id) and account_id in account_ids
        deal_present_in_export = bool(deal_id) and deal_id in deal_ids

        if acct_present_in_export:
            resolved_accounts += 1
        if deal_present_in_export:
            resolved_deals += 1
        if acct_present_in_export and deal_present_in_export:
            resolved_both += 1

        if account_id and not acct_present_in_export:
            unresolved_accounts.append({
                "row": idx + 1,
                "contract_record_id": contract_record or None,
                "contract_label": contract_label,
                "account_id": account_id,
            })
        if deal_id and not deal_present_in_export:
            unresolved_deals.append({
                "row": idx + 1,
                "contract_record_id": contract_record or None,
                "contract_label": contract_label,
                "deal_id": deal_id,
            })
        if (
            account_id and deal_id
            and not acct_present_in_export
            and not deal_present_in_export
        ):
            unresolved_both.append({
                "row": idx + 1,
                "contract_record_id": contract_record or None,
                "contract_label": contract_label,
                "account_id": account_id,
                "deal_id": deal_id,
            })

    # Surface bucketed warnings/errors so the console summary stays
    # short and the JSON report stays self-describing.
    if account_dupes:
        errors.append({
            "kind": "duplicate_account_record_id",
            "count": len(account_dupes),
            "samples": [
                {"value": v, "count": n}
                for v, n in list(account_dupes.most_common(_SAMPLE_LIMIT))
            ],
        })
    if deal_dupes:
        errors.append({
            "kind": "duplicate_deal_record_id",
            "count": len(deal_dupes),
            "samples": [
                {"value": v, "count": n}
                for v, n in list(deal_dupes.most_common(_SAMPLE_LIMIT))
            ],
        })
    if unresolved_accounts:
        warnings.append({
            "kind": "contract_account_id_not_in_accounts_export",
            "count": len(unresolved_accounts),
            "samples": unresolved_accounts[:_SAMPLE_LIMIT],
        })
    if unresolved_deals:
        warnings.append({
            "kind": "contract_deal_id_not_in_deals_export",
            "count": len(unresolved_deals),
            "samples": unresolved_deals[:_SAMPLE_LIMIT],
        })

    ready_for_import_plan = (
        not unresolved_accounts
        and not unresolved_deals
        and not account_dupes
        and not deal_dupes
    )

    return {
        "contracts_file": contracts_file,
        "accounts_file": accounts_file,
        "deals_file": deals_file,
        "total_contracts": len(contracts),
        "total_accounts": len(accounts),
        "total_deals": len(deals),
        "resolved": {
            "accounts": resolved_accounts,
            "deals": resolved_deals,
            "both": resolved_both,
        },
        "unresolved": {
            "accounts": unresolved_accounts,
            "deals": unresolved_deals,
            "both": unresolved_both,
        },
        "duplicates": {
            "account_record_ids": dict(account_dupes),
            "deal_record_ids": dict(deal_dupes),
        },
        "warnings": warnings,
        "errors": errors,
        "ready_for_import_plan": ready_for_import_plan,
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Zoho Contract Relationships Dry-Run")
    lines.append("=" * 60)
    lines.append(f"Contracts file:         {report['contracts_file']}")
    lines.append(f"Accounts file:          {report['accounts_file']}")
    lines.append(f"Deals file:             {report['deals_file']}")
    lines.append("")
    lines.append(f"Total contracts:        {report['total_contracts']}")
    lines.append(f"Total accounts:         {report['total_accounts']}")
    lines.append(f"Total deals:            {report['total_deals']}")
    res = report["resolved"]
    lines.append("")
    lines.append(f"Resolved account links: {res['accounts']}")
    lines.append(f"Resolved deal links:    {res['deals']}")
    lines.append(f"Resolved both:          {res['both']}")
    unr = report["unresolved"]
    lines.append("")
    lines.append(f"Unresolved accounts:    {len(unr['accounts'])}")
    lines.append(f"Unresolved deals:       {len(unr['deals'])}")
    lines.append(f"Unresolved (both):      {len(unr['both'])}")
    dups = report["duplicates"]
    lines.append("")
    lines.append(
        f"Duplicate Account Record Ids: {len(dups['account_record_ids'])}"
    )
    lines.append(
        f"Duplicate Deal Record Ids:    {len(dups['deal_record_ids'])}"
    )
    lines.append("")
    ready = "YES" if report["ready_for_import_plan"] else "NO"
    lines.append(f"Ready for import plan:  {ready}")
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


def run(
    *,
    contracts_path: str,
    accounts_path: str,
    deals_path: str,
    out_path: str,
) -> dict[str, Any]:
    """Read inputs, resolve relationships, write the JSON report,
    return the in-memory report."""
    contracts, _ = _read_csv(
        contracts_path,
        required_headers=_CONTRACT_REQUIRED_HEADERS,
    )
    accounts, _ = _read_csv(
        accounts_path,
        required_header_groups=_ACCOUNT_REQUIRED_HEADER_GROUP,
    )
    deals, _ = _read_csv(
        deals_path,
        required_header_groups=_DEAL_REQUIRED_HEADER_GROUP,
    )

    report = resolve_relationships(
        contracts=contracts,
        accounts=accounts,
        deals=deals,
        contracts_file=contracts_path,
        accounts_file=accounts_path,
        deals_file=deals_path,
    )

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only relationship validator for Zoho Contracts → "
            "Accounts/Deals."
        ),
    )
    parser.add_argument("--contracts", required=True,
                        help="Path to the Zoho Contract CSV export.")
    parser.add_argument("--accounts", required=True,
                        help="Path to the Zoho Accounts CSV export.")
    parser.add_argument("--deals", required=True,
                        help="Path to the Zoho Deals/Opportunities CSV export.")
    parser.add_argument("--out", required=True,
                        help="Destination path for the JSON report.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = run(
            contracts_path=args.contracts,
            accounts_path=args.accounts,
            deals_path=args.deals,
            out_path=args.out,
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
