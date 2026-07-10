"""Read-only candidate-patch planner for missing Zoho contract fields.

Consumes the audit emitted by ``zoho_contracts_missing_fields_audit.py``
(or the same inputs the audit ingested) and produces a per-contract
plan listing the AEGISAXIOM-side fields the importer left blank where Zoho
carries a value. Each row carries a CANDIDATE ``$set`` patch — the
operator / Finance still owns the decision to apply it.

Strict guarantees:

  * No DB writes.
  * No Zoho API calls.
  * No ``--execute`` flag (pinning test in
    ``test_zoho_contracts_missing_fields_plan.py``).
  * Candidate values copied from Zoho are flagged in
    ``needs_finance_review`` so a downstream executor never confuses
    a candidate with an approved patch.

Output shape::

    {
      "source": "...",
      "generated_at": "...",
      "summary": {
        "total_imported": int,
        "with_patch": int,
        "needs_finance_review_count": int,
        "rule_invocations": {<rule>: int},
      },
      "contracts": [
        {
          "contract_id": "...",
          "external_contract_id": "...",
          "zoho_contract_id": "...",
          "candidate_set": {<field>: <value>, ...},
          "warnings": [...],
          "needs_finance_review": [<field>, ...]
        },
        ...
      ],
      "notes": [...]
    }

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

# Reuse the field tables and loaders from the audit so the two scripts
# can never disagree on which AEGISAXIOM fields map to which Zoho columns.
from scripts.zoho_contracts_missing_fields_audit import (  # noqa: E402
    _ZOHO_CONTRACT_FIELDS,
    _CONTRACT_TYPE_TO_AEGISAXIOM,
    _coerce_float,
    _is_empty,
    _get_path,
    _index_zoho_by_id,
    _read_csv,
    _resolve_field,
    _load_aegisaxiom_contracts_from_db,
    _load_aegisaxiom_manifest,
)


logger = logging.getLogger("zoho_contracts_missing_fields_plan")


# ---------------------------------------------------------------------------
# Patch rules
# ---------------------------------------------------------------------------


# Each rule reads AEGISAXIOM-side state + Zoho row and proposes ONE candidate
# field. Returning ``None`` means the rule does not apply. ``warning``
# is an optional string when the value is set but suspect (e.g., the
# Zoho snapshot says positive hours but AEGISAXIOM already has different
# positive hours).
#
# The list ordering does not matter — every rule runs against every
# contract.


def _hour_field_rule(
    aegisaxiom_field: str, zoho_variants: tuple[str, ...],
) -> Any:
    """Build a rule closure that copies a numeric Zoho field into a
    AEGISAXIOM-side imported_* field when the AEGISAXIOM field is empty."""

    def rule(aegisaxiom: dict[str, Any], zoho: dict[str, str]) -> dict[str, Any] | None:
        aegisaxiom_val = _get_path(aegisaxiom, aegisaxiom_field)
        if not _is_empty(aegisaxiom_val):
            return None
        raw = _resolve_field(zoho, zoho_variants)
        val, _ = _coerce_float(raw)
        if val is None:
            return None
        return {
            "rule": f"fill_{aegisaxiom_field}",
            "field": aegisaxiom_field,
            "candidate": val,
            "review": False,
        }

    return rule


def _string_field_rule(
    aegisaxiom_field: str, zoho_variants: tuple[str, ...], review: bool = False,
) -> Any:
    """Build a rule that copies a string Zoho cell into a AEGISAXIOM-side
    *_raw field when the AEGISAXIOM field is empty."""

    def rule(aegisaxiom: dict[str, Any], zoho: dict[str, str]) -> dict[str, Any] | None:
        aegisaxiom_val = _get_path(aegisaxiom, aegisaxiom_field)
        if not _is_empty(aegisaxiom_val):
            return None
        raw = _resolve_field(zoho, zoho_variants)
        if _is_empty(raw):
            return None
        return {
            "rule": f"fill_{aegisaxiom_field}",
            "field": aegisaxiom_field,
            "candidate": raw,
            "review": review,
        }

    return rule


def _total_hours_review_rule(
    aegisaxiom: dict[str, Any], zoho: dict[str, str],
) -> dict[str, Any] | None:
    """For boh / boh_with_boa contracts, propose copying
    ``imported_total_hours`` into ``total_hours`` / ``total_hours_purchased``
    as a CANDIDATE that explicitly requires Finance review. The
    snapshot can drift from the contract's actual block size."""
    ctype = (aegisaxiom.get("contract_type") or "").strip()
    if ctype not in {"boh", "boh_with_boa"}:
        return None
    # Skip when AEGISAXIOM already has a positive total — operator already
    # made a decision we should not overwrite.
    for already_set in (aegisaxiom.get("total_hours"), aegisaxiom.get("total_hours_purchased")):
        v, _ = _coerce_float(already_set)
        if v and v > 0:
            return None
    snapshot_val, _ = _coerce_float(
        aegisaxiom.get("imported_total_hours")
        or _resolve_field(zoho, _ZOHO_CONTRACT_FIELDS["total_hours"])
    )
    if not snapshot_val or snapshot_val <= 0:
        return None
    return {
        "rule": "propose_total_hours_from_snapshot",
        "field": "total_hours",
        "candidate": snapshot_val,
        "review": True,
        "additional_set": {
            "total_hours_purchased": snapshot_val,
        },
    }


_RULES = [
    _hour_field_rule("imported_total_hours",       _ZOHO_CONTRACT_FIELDS["total_hours"]),
    _hour_field_rule("imported_remaining_hours",   _ZOHO_CONTRACT_FIELDS["remaining_hours"]),
    _hour_field_rule("imported_consumed_hours",    _ZOHO_CONTRACT_FIELDS["consumed_hours"]),
    _hour_field_rule("imported_consumed_percent",  _ZOHO_CONTRACT_FIELDS["consumed_pct"]),
    _hour_field_rule("bill_on_actual_hours",       _ZOHO_CONTRACT_FIELDS["bill_on_actual_hours"]),
    _hour_field_rule("hours_to_be_paid",           _ZOHO_CONTRACT_FIELDS["hours_to_be_paid"]),
    _hour_field_rule("paid_hours",                 _ZOHO_CONTRACT_FIELDS["paid_hours"]),
    _hour_field_rule("number_of_invoices",         _ZOHO_CONTRACT_FIELDS["number_of_invoices"]),
    _string_field_rule("contract_number",          ("Contract Number",)),
    _string_field_rule("invoice_type_raw",         _ZOHO_CONTRACT_FIELDS["invoice_type"]),
    _string_field_rule("contract_status_raw",      _ZOHO_CONTRACT_FIELDS["contract_status"]),
    _string_field_rule("zoho_status_raw",          _ZOHO_CONTRACT_FIELDS["status"]),
    _string_field_rule("ap_issues_raw",            _ZOHO_CONTRACT_FIELDS["ap_issues"]),
    _string_field_rule("source_created_by",        _ZOHO_CONTRACT_FIELDS["created_by"]),
    _string_field_rule("source_created_at",        _ZOHO_CONTRACT_FIELDS["created_time"]),
    _total_hours_review_rule,
]


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


def _classify_contract_type_mismatch(
    aegisaxiom: dict[str, Any], zoho: dict[str, str],
) -> str | None:
    """Surface contract_type mismatches as a warning. The plan does
    NOT propose changing the canonical contract_type — that's a
    business decision."""
    zoho_ctype = _resolve_field(zoho, _ZOHO_CONTRACT_FIELDS["contract_type"])
    if not zoho_ctype:
        return None
    expected_aegisaxiom = _CONTRACT_TYPE_TO_AEGISAXIOM.get(zoho_ctype, "other")
    aegisaxiom_ctype = (aegisaxiom.get("contract_type") or "").strip()
    if aegisaxiom_ctype and aegisaxiom_ctype != expected_aegisaxiom:
        return (
            f"contract_type mismatch: zoho={zoho_ctype!r} "
            f"(maps to {expected_aegisaxiom!r}); aegisaxiom={aegisaxiom_ctype!r}"
        )
    return None


def build_plan(
    *,
    aegisaxiom_contracts: list[dict[str, Any]],
    zoho_contracts_csv: list[dict[str, str]],
    source_label: str,
) -> dict[str, Any]:
    zoho_index = _index_zoho_by_id(zoho_contracts_csv)
    rule_counts: Counter[str] = Counter()
    needs_finance_review_total = 0
    rows_with_patch = 0
    out_rows: list[dict[str, Any]] = []

    for aegisaxiom in aegisaxiom_contracts:
        external_ids = aegisaxiom.get("external_ids") or {}
        zid = (
            external_ids.get("zoho_contract_id")
            or aegisaxiom.get("external_contract_id")
        )
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
                if result.get("additional_set"):
                    for k, v in result["additional_set"].items():
                        candidate_set[k] = v
                if result.get("review"):
                    needs_review.append(result["field"])
            ct_mismatch = _classify_contract_type_mismatch(aegisaxiom, zoho)
            if ct_mismatch:
                warnings.append(ct_mismatch)
        else:
            warnings.append("no_zoho_row_for_contract_id")

        if candidate_set:
            rows_with_patch += 1
        if needs_review:
            needs_finance_review_total += 1

        out_rows.append({
            "contract_id": aegisaxiom.get("contract_id"),
            "external_contract_id": aegisaxiom.get("external_contract_id"),
            "zoho_contract_id": zid,
            "contract_number": aegisaxiom.get("contract_number"),
            "contract_type": aegisaxiom.get("contract_type"),
            "candidate_set": candidate_set,
            "warnings": warnings,
            "needs_finance_review": sorted(set(needs_review)),
        })

    return {
        "source": source_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_imported": len(aegisaxiom_contracts),
            "with_patch": rows_with_patch,
            "needs_finance_review_count": needs_finance_review_total,
            "rule_invocations": dict(rule_counts),
        },
        "contracts": out_rows,
        "notes": [
            (
                "candidate_set is a CANDIDATE only. Apply via a separate, "
                "reviewed executor — never blind-write these values."
            ),
            (
                "needs_finance_review fields require explicit Finance / "
                "operator sign-off. The canonical total_hours candidate "
                "is the canonical example: the Zoho snapshot can drift "
                "from the contract's stated block size."
            ),
            (
                "Hour fields land in AEGISAXIOM additive *_raw / imported_* "
                "fields by default — never the canonical hours_consumed "
                "/ total_hours_purchased — unless the rule explicitly "
                "marks the candidate for review."
            ),
            "No --execute flag exists. Read-only by design.",
        ],
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(plan: dict[str, Any]) -> str:
    s = plan["summary"]
    lines = []
    lines.append("Zoho Contracts Missing-Fields Plan")
    lines.append("=" * 64)
    lines.append(f"Source:                  {plan['source']}")
    lines.append(f"Total imported:          {s['total_imported']}")
    lines.append(f"Rows with candidate set: {s['with_patch']}")
    lines.append(f"Rows needing Finance review: {s['needs_finance_review_count']}")
    lines.append("")
    if s["rule_invocations"]:
        lines.append("Rule invocations:")
        for k, n in sorted(s["rule_invocations"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {k:48s}: {n}")
    lines.append("=" * 64)
    lines.append(
        "Mode: READ-ONLY. No DB writes. candidate_set is a CANDIDATE — "
        "Finance / operator must confirm before any hydration step."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def run(
    *,
    aegisaxiom_from_mongo: bool,
    aegisaxiom_manifest: Optional[str],
    contracts_csv: Optional[str],
    out_path: Optional[str],
) -> dict[str, Any]:
    sources: list[str] = []
    if aegisaxiom_manifest:
        aegisaxiom_contracts = _load_aegisaxiom_manifest(aegisaxiom_manifest)
        sources.append(f"aegisaxiom_manifest={aegisaxiom_manifest}")
    elif aegisaxiom_from_mongo:
        aegisaxiom_contracts = await _load_aegisaxiom_contracts_from_db()
        sources.append("aegisaxiom=mongo" if aegisaxiom_contracts else "aegisaxiom=mongo (empty / no credentials)")
    else:
        aegisaxiom_contracts = []
        sources.append("aegisaxiom=none")

    zoho_csv: list[dict[str, str]] = []
    if contracts_csv:
        zoho_csv = _read_csv(contracts_csv)
        sources.append(f"contracts_csv={contracts_csv}")

    plan = build_plan(
        aegisaxiom_contracts=aegisaxiom_contracts,
        zoho_contracts_csv=zoho_csv,
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
            "Read-only candidate-patch planner for missing Zoho contract "
            "fields. No DB writes; no --execute flag."
        ),
    )
    parser.add_argument(
        "--aegisaxiom-from-mongo", action="store_true",
        help="Read import_source='zoho' contracts via MONGO_URL + DB_NAME.",
    )
    parser.add_argument(
        "--aegisaxiom-manifest", default=None,
        help="Optional JSON manifest of AEGISAXIOM contracts.",
    )
    parser.add_argument("--contracts-csv", default=None)
    parser.add_argument(
        "--out", default="imports/zoho/reports/contracts_missing_fields_plan.json",
        help="Destination path for the JSON plan.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    try:
        plan = asyncio.run(run(
            aegisaxiom_from_mongo=args.aegisaxiom_from_mongo,
            aegisaxiom_manifest=args.aegisaxiom_manifest,
            contracts_csv=args.contracts_csv,
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
