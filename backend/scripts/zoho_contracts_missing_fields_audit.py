"""Read-only audit of fields the Zoho Contracts importer did not hydrate.

Ninth slice of the Zoho historical migration. The May 22, 2026 review
against the live Zoho UI surfaced a list of contract fields visible
in Zoho that AEGISAXIOM-side imports left blank or set to zero. The earlier
``zoho_contracts_hydration_plan.py`` quantified the readiness gaps
along five debit/draft dimensions; this script is the **field-level**
audit — what scalar values exist in Zoho that AEGISAXIOM does not yet
carry, plus what AEGISAXIOM carries that disagrees with Zoho when both
sides are available.

The audit is **strictly read-only**:

  * No DB writes.
  * No Zoho API calls.
  * No ``--execute`` flag.
  * No mutation of the input CSVs.

It produces a single JSON report
(``imports/zoho/reports/contracts_missing_fields_audit.json``) plus
a human console summary. The companion planner
``zoho_contracts_missing_fields_plan.py`` consumes the audit data to
emit per-contract candidate ``$set`` patches — also read-only.

Inputs (all optional except --aegisaxiom/Mongo):
  --aegisaxiom-from-mongo            read import_source='zoho' contracts
                                via MONGO_URL + DB_NAME.
  --contracts-csv PATH         optional Zoho Contracts export.
  --transactions-csv PATH      optional Zoho Transactions export.
  --projects-csv PATH          optional Zoho Projects export (for
                                related-record counts).
  --out PATH                   destination JSON.

Run::

    python backend/scripts/zoho_contracts_missing_fields_audit.py \\
      --aegisaxiom-from-mongo \\
      --contracts-csv imports/zoho/raw/Contract.csv \\
      --transactions-csv imports/zoho/raw/Transactions.csv \\
      --projects-csv imports/zoho/raw/Projects.csv \\
      --out imports/zoho/reports/contracts_missing_fields_audit.json

Output shape (top-level keys)::

    {
      "source": {...},
      "generated_at": "...",
      "aegisaxiom_imported_count": 94,
      "zoho_contract_count": 94,
      "aegisaxiom_only_zoho_contract_ids": [...],
      "zoho_only_zoho_contract_ids": [...],
      "field_population": {
        "aegisaxiom":  {<field>: {present: int, empty: int}},
        "zoho":  {<column>: {present: int, empty: int}}
      },
      "mapping_gaps": {
        "aegisaxiom_fields_empty_with_zoho_value": {<field>: count},
        "zoho_columns_not_mapped_to_aegisaxiom":  [<column>, ...]
      },
      "hour_mismatches": [...],
      "status_mismatches": [...],
      "contract_type_mismatches": [...],
      "invoice_type_mismatches": [...],
      "ap_issues_observed": {...},
      "number_of_invoices_observed": {...},
      "owner_mapping_gaps": [...],
      "related_counts": {
        "transactions_per_contract": {<zoho_contract_id>: int},
        "projects_per_contract":     {<zoho_contract_id>: int}
      },
      "per_contract": [...]
    }

The companion mapping doc is
``docs/zoho-migration/contracts_field_mapping.md``.
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


logger = logging.getLogger("zoho_contracts_missing_fields_audit")


# ---------------------------------------------------------------------------
# Header / field tables (kept aligned with the importer)
# ---------------------------------------------------------------------------

# Apostrophe characters Zoho exports use interchangeably. Normalise to
# ASCII so curly apostrophes don't double-count.
_APOSTROPHE_CHARS = ("'", "’", "ʼ")


def _normalize_header(raw: str | None) -> str:
    if raw is None:
        return ""
    s = raw.lstrip("﻿").strip()
    for apo in _APOSTROPHE_CHARS:
        s = s.replace(apo, "'")
    return s


# Canonical-key -> tuple of accepted Zoho header spellings. We deliberately
# enumerate every field the May 22 parity review surfaced as visible in the
# Zoho UI so the audit can flag any that landed empty AEGISAXIOM-side.
_ZOHO_CONTRACT_FIELDS: dict[str, tuple[str, ...]] = {
    "record_id":             ("Record Id",),
    "contract_number":       ("Contract Number",),
    "contract_name":         ("Contract Name",),
    "account_name":          ("Account",),
    "account_id":            ("Account.id",),
    "deal_name":             ("Deals", "Deal"),
    "deal_id":               ("Deals.id", "Deal.id"),
    "contract_type":         ("Contract Type",),
    "invoice_type":          ("Invoice Type",),
    "number_of_invoices":    ("Number of Invoices",),
    "bill_on_actual_hours":  ("Bill On Actual Hours", "Bill on Actual Hours"),
    "hours_to_be_paid":      ("Hours To Be Paid", "Hours to be Paid"),
    "paid_hours":            ("Paid Hours",),
    "contract_owner":        ("Contract Owner",),
    "total_hours":           ("Total Contract Hour's", "Total Contract Hours"),
    "remaining_hours":       ("Remaining Hour's", "Remaining Hours"),
    "consumed_hours":        ("Total Consumed Hour's", "Total Consumed Hours"),
    "consumed_pct":          ("Consumed Hour's %", "Consumed Hours %"),
    "contract_status":       ("Contract Status",),
    "ap_issues":             ("AP Issues",),
    "status":                ("Status",),
    "created_by":            ("Created By",),
    "created_time":          ("Created Time",),
    "modified_time":         ("Modified Time",),
}

# AEGISAXIOM-side canonical fields the importer writes (or could write). The
# audit reports population stats for these against the imported contract
# set.
_AEGISAXIOM_CONTRACT_FIELDS: tuple[str, ...] = (
    "contract_id",
    "contract_number",
    "name",
    "account_name",
    "external_ids.zoho_contract_id",
    "external_ids.zoho_account_id",
    "external_ids.zoho_deal_id",
    "contract_type",
    "invoice_type_raw",
    "status",
    "status_raw",
    "hourly_rate",
    "resource_types",
    "resources",
    "total_hours",
    "total_hours_purchased",
    "hours_consumed",
    "imported_total_hours",
    "imported_remaining_hours",
    "imported_consumed_hours",
    "imported_consumed_percent",
    "contract_type_raw",
    "import_source",
    "imported_at",
    "source_created_at",
    "source_modified_at",
)


# Fields the May 22 parity review specifically called out as missing or
# empty on AEGISAXIOM-side. The audit prioritises these in the gap report so
# operators can see at a glance how many imports lack each one.
_PARITY_FIELDS_OBSERVED_MAY_22 = (
    "imported_total_hours",
    "imported_remaining_hours",
    "imported_consumed_hours",
    "imported_consumed_percent",
    "contract_status_raw",
    "status_raw",
    "ap_issues_raw",
    "number_of_invoices",
    "bill_on_actual_hours",
    "hours_to_be_paid",
    "paid_hours",
    "source_created_by",
    "source_created_at",
    "invoice_type_raw",
)


# Contract-type mapping the AEGISAXIOM importer uses today. Anything else
# lands as "other" with the raw value preserved under contract_type_raw.
_CONTRACT_TYPE_TO_AEGISAXIOM: dict[str, str] = {
    "BOH":                        "boh",
    "BOH and Bill on Actuals":    "boh_with_boa",
    "Bill on Actuals":            "boa",
    "Fixed Fee":                  "fixed_fee",
}

# Tolerance for "consumed + remaining == total" cross-check. Three
# minutes is small enough to catch real drift but absorbs Zoho rounding.
_HOUR_TOLERANCE = 0.05


# ---------------------------------------------------------------------------
# Coercion helpers (shared with the planner)
# ---------------------------------------------------------------------------


def _coerce_float(raw: Any) -> tuple[Optional[float], bool]:
    """Return ``(value, was_empty)``. ``value`` is None when the cell
    holds a non-numeric token, was_empty is True for blank/missing."""
    if raw is None:
        return None, True
    if isinstance(raw, (int, float)):
        return float(raw), False
    s = str(raw).strip()
    if not s:
        return None, True
    s2 = s.replace(",", "").rstrip("%").strip()
    try:
        return float(s2), False
    except ValueError:
        return None, False


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _get_path(doc: dict[str, Any], path: str) -> Any:
    """Fetch a dotted path (e.g. ``external_ids.zoho_contract_id``).
    Returns None when any segment is missing."""
    cur: Any = doc
    for seg in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------


def _read_csv(path: str) -> list[dict[str, str]]:
    if not os.path.isfile(path):
        raise RuntimeError(f"CSV not found: {path!r}")
    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    if reader.fieldnames is None:
        raise RuntimeError(f"CSV {path!r} has no header row.")
    for raw in reader:
        norm: dict[str, str] = {}
        for k, v in raw.items():
            if k is None:
                continue
            norm[_normalize_header(k)] = v if v is not None else ""
        rows.append(norm)
    return rows


def _resolve_field(row: dict[str, str], variants: tuple[str, ...]) -> str:
    for v in variants:
        val = row.get(v)
        if val is None:
            continue
        if isinstance(val, str):
            return val.strip()
        return val
    return ""


async def _load_aegisaxiom_contracts_from_db() -> list[dict[str, Any]]:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        return []
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        return await db.contracts.find(
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
    if isinstance(data, dict) and "contracts" in data:
        data = data["contracts"]
    if not isinstance(data, list):
        raise RuntimeError(f"Manifest {path!r} must be a list of contract docs.")
    return [d for d in data if isinstance(d, dict)]


# ---------------------------------------------------------------------------
# Field-population stats
# ---------------------------------------------------------------------------


def _population(docs: list[dict[str, Any]], paths: Iterable[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for p in paths:
        present = 0
        empty = 0
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
        present = 0
        empty = 0
        for r in rows:
            v = _resolve_field(r, variants)
            if _is_empty(v):
                empty += 1
            else:
                present += 1
        out[key] = {"present": present, "empty": empty}
    return out


# ---------------------------------------------------------------------------
# Cross-source comparisons
# ---------------------------------------------------------------------------


def _index_zoho_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        rid = _resolve_field(r, _ZOHO_CONTRACT_FIELDS["record_id"])
        if rid:
            out[rid] = r
    return out


def _compare_hours(
    aegisaxiom: dict[str, Any], zoho: dict[str, str],
) -> list[dict[str, Any]]:
    """Find numeric drift between AEGISAXIOM-side imported hour snapshots and
    the Zoho values. Both sides are tolerated when blank."""
    out: list[dict[str, Any]] = []
    pairs = [
        ("imported_total_hours",     _ZOHO_CONTRACT_FIELDS["total_hours"]),
        ("imported_remaining_hours", _ZOHO_CONTRACT_FIELDS["remaining_hours"]),
        ("imported_consumed_hours",  _ZOHO_CONTRACT_FIELDS["consumed_hours"]),
        ("imported_consumed_percent", _ZOHO_CONTRACT_FIELDS["consumed_pct"]),
    ]
    for aegisaxiom_field, zoho_variants in pairs:
        aegisaxiom_val, _ = _coerce_float(_get_path(aegisaxiom, aegisaxiom_field))
        zoho_raw = _resolve_field(zoho, zoho_variants)
        zoho_val, _ = _coerce_float(zoho_raw)
        if aegisaxiom_val is None and zoho_val is None:
            continue
        if aegisaxiom_val is None and zoho_val is not None:
            out.append({"field": aegisaxiom_field, "aegisaxiom": None, "zoho": zoho_val, "kind": "aegisaxiom_missing"})
            continue
        if zoho_val is None and aegisaxiom_val is not None:
            out.append({"field": aegisaxiom_field, "aegisaxiom": aegisaxiom_val, "zoho": None, "kind": "zoho_missing"})
            continue
        if abs((aegisaxiom_val or 0) - (zoho_val or 0)) > _HOUR_TOLERANCE:
            out.append({
                "field": aegisaxiom_field,
                "aegisaxiom": aegisaxiom_val,
                "zoho": zoho_val,
                "kind": "value_drift",
            })
    return out


def _compare_enums(aegisaxiom: dict[str, Any], zoho: dict[str, str]) -> list[dict[str, Any]]:
    """contract_type, invoice_type, status enum comparisons."""
    out: list[dict[str, Any]] = []

    zoho_ctype = _resolve_field(zoho, _ZOHO_CONTRACT_FIELDS["contract_type"])
    aegisaxiom_ctype = aegisaxiom.get("contract_type")
    expected_aegisaxiom = _CONTRACT_TYPE_TO_AEGISAXIOM.get(zoho_ctype, "other")
    if zoho_ctype and aegisaxiom_ctype and aegisaxiom_ctype != expected_aegisaxiom:
        out.append({
            "field": "contract_type",
            "zoho_raw": zoho_ctype,
            "expected_aegisaxiom": expected_aegisaxiom,
            "aegisaxiom_actual": aegisaxiom_ctype,
        })

    zoho_invoice = _resolve_field(zoho, _ZOHO_CONTRACT_FIELDS["invoice_type"])
    aegisaxiom_invoice_raw = aegisaxiom.get("invoice_type_raw")
    if zoho_invoice and aegisaxiom_invoice_raw and zoho_invoice != aegisaxiom_invoice_raw:
        out.append({
            "field": "invoice_type",
            "zoho_raw": zoho_invoice,
            "aegisaxiom_raw": aegisaxiom_invoice_raw,
        })

    zoho_status = _resolve_field(zoho, _ZOHO_CONTRACT_FIELDS["contract_status"])
    aegisaxiom_status_raw = aegisaxiom.get("status_raw")
    if zoho_status and aegisaxiom_status_raw and zoho_status != aegisaxiom_status_raw:
        out.append({
            "field": "contract_status",
            "zoho_raw": zoho_status,
            "aegisaxiom_raw": aegisaxiom_status_raw,
        })

    return out


# ---------------------------------------------------------------------------
# Audit builder
# ---------------------------------------------------------------------------


def build_audit(
    *,
    aegisaxiom_contracts: list[dict[str, Any]],
    zoho_contracts_csv: list[dict[str, str]],
    zoho_transactions_csv: list[dict[str, str]],
    zoho_projects_csv: list[dict[str, str]],
    source_label: str,
) -> dict[str, Any]:
    zoho_index = _index_zoho_by_id(zoho_contracts_csv)

    aegisaxiom_by_zoho_id: dict[str, dict[str, Any]] = {}
    for c in aegisaxiom_contracts:
        zid = (c.get("external_ids") or {}).get("zoho_contract_id") or c.get("external_contract_id")
        if zid:
            aegisaxiom_by_zoho_id[zid] = c

    aegisaxiom_only = sorted(set(aegisaxiom_by_zoho_id) - set(zoho_index))
    zoho_only = sorted(set(zoho_index) - set(aegisaxiom_by_zoho_id))

    # Field-population stats
    aegisaxiom_pop = _population(aegisaxiom_contracts, _AEGISAXIOM_CONTRACT_FIELDS)
    zoho_pop = _csv_population(zoho_contracts_csv, _ZOHO_CONTRACT_FIELDS) if zoho_contracts_csv else {}

    # Mapping gaps: for each AEGISAXIOM-side field flagged in the May 22 parity
    # review, count how many imports left it empty when Zoho carries a
    # non-empty value.
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
            # Map the AEGISAXIOM field to a likely Zoho column to check.
            zoho_check = {
                "imported_total_hours":      _ZOHO_CONTRACT_FIELDS["total_hours"],
                "imported_remaining_hours":  _ZOHO_CONTRACT_FIELDS["remaining_hours"],
                "imported_consumed_hours":   _ZOHO_CONTRACT_FIELDS["consumed_hours"],
                "imported_consumed_percent": _ZOHO_CONTRACT_FIELDS["consumed_pct"],
                "contract_status_raw":       _ZOHO_CONTRACT_FIELDS["contract_status"],
                "status_raw":                _ZOHO_CONTRACT_FIELDS["status"],
                "ap_issues_raw":             _ZOHO_CONTRACT_FIELDS["ap_issues"],
                "number_of_invoices":        _ZOHO_CONTRACT_FIELDS["number_of_invoices"],
                "bill_on_actual_hours":      _ZOHO_CONTRACT_FIELDS["bill_on_actual_hours"],
                "hours_to_be_paid":          _ZOHO_CONTRACT_FIELDS["hours_to_be_paid"],
                "paid_hours":                _ZOHO_CONTRACT_FIELDS["paid_hours"],
                "source_created_by":         _ZOHO_CONTRACT_FIELDS["created_by"],
                "source_created_at":         _ZOHO_CONTRACT_FIELDS["created_time"],
                "invoice_type_raw":          _ZOHO_CONTRACT_FIELDS["invoice_type"],
            }.get(field)
            if not zoho_check:
                continue
            zval = _resolve_field(zrow, zoho_check)
            if not _is_empty(zval):
                count += 1
        aegisaxiom_fields_empty_with_zoho_value[field] = count

    # Zoho columns present in the CSV but not mapped to a AEGISAXIOM field.
    mapped_variants: set[str] = set()
    for variants in _ZOHO_CONTRACT_FIELDS.values():
        mapped_variants.update(variants)
    csv_columns_seen: set[str] = set()
    for r in zoho_contracts_csv:
        csv_columns_seen.update(r.keys())
    unmapped_zoho_columns = sorted(c for c in csv_columns_seen if c not in mapped_variants)

    # Per-contract drift
    per_contract: list[dict[str, Any]] = []
    hour_mismatches: list[dict[str, Any]] = []
    status_mismatches: list[dict[str, Any]] = []
    ctype_mismatches: list[dict[str, Any]] = []
    invoice_mismatches: list[dict[str, Any]] = []
    owner_gaps: list[dict[str, Any]] = []

    for zid, aegisaxiom in aegisaxiom_by_zoho_id.items():
        zrow = zoho_index.get(zid)
        per: dict[str, Any] = {
            "zoho_contract_id": zid,
            "aegisaxiom_contract_id": aegisaxiom.get("contract_id"),
            "contract_number": aegisaxiom.get("contract_number"),
            "aegisaxiom_only": zrow is None,
        }
        if zrow is None:
            per_contract.append(per)
            continue
        # Hour drift
        h_drift = _compare_hours(aegisaxiom, zrow)
        if h_drift:
            per["hour_drift"] = h_drift
            for d in h_drift:
                hour_mismatches.append({"zoho_contract_id": zid, **d})
        # Enum drift
        enum_drift = _compare_enums(aegisaxiom, zrow)
        if enum_drift:
            per["enum_drift"] = enum_drift
            for d in enum_drift:
                if d["field"] == "contract_type":
                    ctype_mismatches.append({"zoho_contract_id": zid, **d})
                elif d["field"] == "invoice_type":
                    invoice_mismatches.append({"zoho_contract_id": zid, **d})
                elif d["field"] == "contract_status":
                    status_mismatches.append({"zoho_contract_id": zid, **d})
        # Owner gap: imports have no per-owner mapping yet, so flag every
        # row where Zoho carries a non-empty owner.
        zoho_owner = _resolve_field(zrow, _ZOHO_CONTRACT_FIELDS["contract_owner"])
        if zoho_owner:
            owner_gaps.append({
                "zoho_contract_id": zid,
                "zoho_owner": zoho_owner,
                "aegisaxiom_assigned_sales_rep_id": aegisaxiom.get("assigned_sales_rep_id"),
            })
        per_contract.append(per)

    # AP Issues distribution
    ap_issues_obs: Counter[str] = Counter()
    for r in zoho_contracts_csv:
        v = _resolve_field(r, _ZOHO_CONTRACT_FIELDS["ap_issues"])
        if v:
            ap_issues_obs[v] += 1

    number_of_invoices_obs: Counter[str] = Counter()
    for r in zoho_contracts_csv:
        v = _resolve_field(r, _ZOHO_CONTRACT_FIELDS["number_of_invoices"])
        if v:
            number_of_invoices_obs[v] += 1

    # Related counts
    transactions_per_contract: dict[str, int] = defaultdict(int)
    if zoho_transactions_csv:
        for r in zoho_transactions_csv:
            cid = (
                _resolve_field(r, ("Contract.id",))
                or _resolve_field(r, ("Contract Id",))
            )
            if cid:
                transactions_per_contract[cid] += 1

    projects_per_contract: dict[str, int] = defaultdict(int)
    if zoho_projects_csv:
        for r in zoho_projects_csv:
            cid = (
                _resolve_field(r, ("Contract.id",))
                or _resolve_field(r, ("Contract Id",))
            )
            if cid:
                projects_per_contract[cid] += 1

    return {
        "source": source_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aegisaxiom_imported_count": len(aegisaxiom_contracts),
        "zoho_contract_count": len(zoho_contracts_csv),
        "aegisaxiom_only_zoho_contract_ids": aegisaxiom_only,
        "zoho_only_zoho_contract_ids": zoho_only,
        "field_population": {"aegisaxiom": aegisaxiom_pop, "zoho": zoho_pop},
        "mapping_gaps": {
            "aegisaxiom_fields_empty_with_zoho_value": aegisaxiom_fields_empty_with_zoho_value,
            "zoho_columns_not_mapped_to_aegisaxiom": unmapped_zoho_columns,
        },
        "hour_mismatches": hour_mismatches,
        "status_mismatches": status_mismatches,
        "contract_type_mismatches": ctype_mismatches,
        "invoice_type_mismatches": invoice_mismatches,
        "ap_issues_observed": dict(ap_issues_obs),
        "number_of_invoices_observed": dict(number_of_invoices_obs),
        "owner_mapping_gaps": owner_gaps,
        "related_counts": {
            "transactions_per_contract": dict(transactions_per_contract),
            "projects_per_contract": dict(projects_per_contract),
        },
        "per_contract": per_contract,
        "notes": [
            (
                "Read-only audit. No DB writes, no Zoho API calls, no "
                "--execute flag. Companion planner: "
                "zoho_contracts_missing_fields_plan.py."
            ),
            (
                "aegisaxiom_fields_empty_with_zoho_value counts only rows where "
                "Zoho carries a non-empty value AND the AEGISAXIOM-side import "
                "left the field blank — the actionable hydration set."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def format_console_summary(audit: dict[str, Any]) -> str:
    lines = []
    lines.append("Zoho Contracts Missing-Fields Audit")
    lines.append("=" * 64)
    lines.append(f"Source:                {audit['source']}")
    lines.append(f"AEGISAXIOM imported count:   {audit['aegisaxiom_imported_count']}")
    lines.append(f"Zoho contract count:   {audit['zoho_contract_count']}")
    lines.append(f"AEGISAXIOM-only (no Zoho match): {len(audit['aegisaxiom_only_zoho_contract_ids'])}")
    lines.append(f"Zoho-only (not imported):  {len(audit['zoho_only_zoho_contract_ids'])}")
    lines.append("")
    lines.append("AegisAxiom-side empty with Zoho value (actionable hydration):")
    gaps = audit["mapping_gaps"]["aegisaxiom_fields_empty_with_zoho_value"]
    for f, n in sorted(gaps.items(), key=lambda kv: -kv[1]):
        if n > 0:
            lines.append(f"  - {f:32s}: {n}")
    lines.append("")
    lines.append(f"Hour mismatches:       {len(audit['hour_mismatches'])}")
    lines.append(f"Status mismatches:     {len(audit['status_mismatches'])}")
    lines.append(f"Contract-type drift:   {len(audit['contract_type_mismatches'])}")
    lines.append(f"Invoice-type drift:    {len(audit['invoice_type_mismatches'])}")
    lines.append(f"Owner mapping gaps:    {len(audit['owner_mapping_gaps'])}")
    lines.append(f"Unmapped Zoho columns: {len(audit['mapping_gaps']['zoho_columns_not_mapped_to_aegisaxiom'])}")
    lines.append(f"AP Issues observed:    {sum(audit['ap_issues_observed'].values())}")
    lines.append("")
    rc = audit["related_counts"]
    lines.append(f"Transactions/contract: {len(rc['transactions_per_contract'])} contracts have >=1 transaction")
    lines.append(f"Projects/contract:     {len(rc['projects_per_contract'])} contracts have >=1 project")
    lines.append("=" * 64)
    lines.append(
        "Mode: READ-ONLY. No DB writes. The companion planner emits "
        "per-contract candidate $set patches — Finance review required "
        "before any hydration step is applied."
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
    transactions_csv: Optional[str],
    projects_csv: Optional[str],
    out_path: Optional[str],
) -> dict[str, Any]:
    sources: list[str] = []
    aegisaxiom_contracts: list[dict[str, Any]] = []
    if aegisaxiom_manifest:
        aegisaxiom_contracts = _load_aegisaxiom_manifest(aegisaxiom_manifest)
        sources.append(f"aegisaxiom_manifest={aegisaxiom_manifest}")
    elif aegisaxiom_from_mongo:
        aegisaxiom_contracts = await _load_aegisaxiom_contracts_from_db()
        sources.append("aegisaxiom=mongo" if aegisaxiom_contracts else "aegisaxiom=mongo (empty / no credentials)")
    else:
        sources.append("aegisaxiom=none")

    zoho_csv: list[dict[str, str]] = []
    if contracts_csv:
        zoho_csv = _read_csv(contracts_csv)
        sources.append(f"contracts_csv={contracts_csv}")
    txn_csv: list[dict[str, str]] = []
    if transactions_csv:
        txn_csv = _read_csv(transactions_csv)
        sources.append(f"transactions_csv={transactions_csv}")
    proj_csv: list[dict[str, str]] = []
    if projects_csv:
        proj_csv = _read_csv(projects_csv)
        sources.append(f"projects_csv={projects_csv}")

    audit = build_audit(
        aegisaxiom_contracts=aegisaxiom_contracts,
        zoho_contracts_csv=zoho_csv,
        zoho_transactions_csv=txn_csv,
        zoho_projects_csv=proj_csv,
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
            "Read-only audit of imported Zoho contract fields against "
            "the Zoho CSV exports. No DB writes; no --execute flag."
        ),
    )
    parser.add_argument(
        "--aegisaxiom-from-mongo", action="store_true",
        help="Read import_source='zoho' contracts via MONGO_URL + DB_NAME.",
    )
    parser.add_argument(
        "--aegisaxiom-manifest", default=None,
        help="Optional JSON manifest of AEGISAXIOM contracts (list or {'contracts':[...]}).",
    )
    parser.add_argument("--contracts-csv", default=None)
    parser.add_argument("--transactions-csv", default=None)
    parser.add_argument("--projects-csv", default=None)
    parser.add_argument(
        "--out", default="imports/zoho/reports/contracts_missing_fields_audit.json",
        help="Destination path for the JSON audit report.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    try:
        audit = asyncio.run(run(
            aegisaxiom_from_mongo=args.aegisaxiom_from_mongo,
            aegisaxiom_manifest=args.aegisaxiom_manifest,
            contracts_csv=args.contracts_csv,
            transactions_csv=args.transactions_csv,
            projects_csv=args.projects_csv,
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
