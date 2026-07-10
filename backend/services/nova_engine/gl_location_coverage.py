"""GL location / department coverage helper.

Nova supports location-scoped finance answers but the AEGISAXIOM general_ledger
schema carries location and department fields inconsistently — many rows
have null location_name, blank department_ref_id, or no location-related
field at all. Quoting "revenue by location" without disclosing how many
rows actually carry a location silently mis-attributes the unattributed
remainder, so this helper computes the coverage signal explicitly.

Coverage MUST match the metric/category scope of the answer Nova is
giving. The audit case: May 2026 revenue had 103 GL rows, ZERO of
which carried a location, but the helper had been computing all-GL
coverage and reporting 100%. The metric/category-aware path filters
rows by ``categorize(account_name)`` against the canonical category
sets in ``services.nova_engine.formulas`` so revenue / expense / cogs
/ gross_profit each get an accurate coverage number for their own
row set.

Pure-ish: the only I/O is Mongo reads against the supplied database
handle. No QBO calls, no n8n calls. Reads only ``general_ledger``.

Field set is shared with ``services.ai.agents.tools.nova_tools._LOCATION_LIKE_FIELDS``
so the coverage helper and the location_blank filter on
query_gl_transactions / query_finance_data agree on what "has a
location" means. The shared list is re-exported here as the
``location_like_fields`` field of the response.

Out of scope for v1 (documented as a coverage warning):
  * Nested raw_data.DepartmentRef paths — the existing flat fields
    are the primary signal; nested support is a follow-up that mirrors
    the Python iteration in nova_tools._is_blank_location.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from .account_categories import categorize
from .formulas import COGS_CATEGORIES, REVENUE_CATEGORIES

logger = logging.getLogger(__name__)


# Metric → set of canonical categories. Mirrors finance_driver_analysis
# so all "metric"-aware tools agree on what counts as revenue / cogs /
# expenses / gross_profit. PAYROLL / ADMIN_EXPENSES / OTHER_OPEX are
# operating-expense (not COGS); the broad ``expenses`` set includes
# both COGS and OpEx to match query_finance_data's metric=expense.
_EXPENSE_CATEGORIES: frozenset[str] = frozenset(
    {"PS_COGS", "FS_COGS", "OTHER_COGS", "PAYROLL", "ADMIN_EXPENSES", "OTHER_OPEX"}
)

_METRIC_CATEGORY_SETS: dict[str, frozenset[str]] = {
    "revenue": REVENUE_CATEGORIES,
    "cogs": COGS_CATEGORIES,
    "expenses": _EXPENSE_CATEGORIES,
    # gross_profit spans both revenue + COGS rows; the response carries
    # a scope_description note so the LLM can disclose the mixed scope.
    "gross_profit": REVENUE_CATEGORIES | COGS_CATEGORIES,
}

_SUPPORTED_METRICS: frozenset[str] = frozenset(_METRIC_CATEGORY_SETS)


# Mirrors services.ai.agents.tools.nova_tools._LOCATION_LIKE_FIELDS so
# both surfaces agree on what counts as "has a location." Listed here
# (rather than imported) to keep this module free of LLM-tool imports;
# the test suite asserts the two lists stay in sync.
LOCATION_LIKE_FIELDS: tuple[str, ...] = (
    "location",
    "location_id",
    "location_name",
    "department",
    "department_name",
    "department_ref_id",
    "department_ref_name",
)


# Default coverage threshold under which the helper emits a warning.
# 80% is a soft floor — below it, location-scoped totals understate
# enough activity that quoting them without disclosure is misleading.
_LOW_COVERAGE_THRESHOLD = 0.80


def _build_scope_match(
    date_from: Optional[str],
    date_to: Optional[str],
    basis: Optional[str],
) -> dict[str, Any]:
    """Build a Mongo $match filter from the scope inputs."""
    match: dict[str, Any] = {}
    if date_from is not None or date_to is not None:
        tx_filter: dict[str, str] = {}
        if date_from is not None:
            tx_filter["$gte"] = date_from
        if date_to is not None:
            tx_filter["$lte"] = date_to
        match["tx_date"] = tx_filter
    if basis is not None:
        match["basis"] = basis
    return match


def _has_location_expr() -> dict[str, Any]:
    """A Mongo aggregation expression that evaluates True when ANY
    location-like field on the row is non-null and non-empty.

    Whitespace-only string values count as populated under this
    expression; the docstring on get_gl_location_coverage notes the
    minor over-count and the Python helpers in nova_tools handle the
    whitespace case for the per-row classification used by
    location_blank filters.
    """
    or_clauses: list[dict[str, Any]] = []
    for field in LOCATION_LIKE_FIELDS:
        ref = f"${field}"
        or_clauses.append(
            {
                "$and": [
                    {"$ne": [ref, None]},
                    {"$ne": [ref, ""]},
                ]
            }
        )
    return {"$or": or_clauses}


def _safe_pct(num: float, denom: float) -> float:
    if denom <= 0:
        return 0.0
    return float(num) / float(denom)


async def _aggregate_coverage(
    db: Any,
    scope_match: dict[str, Any],
) -> tuple[int, int, dict[str, int]]:
    """Run the coverage aggregation and return
    (row_count, with_location_count, basis_breakdown).

    basis_breakdown maps basis string -> {"row_count": int,
    "with_location_count": int}. Bases with empty / non-string labels
    are dropped from the breakdown so the response doesn't surface a
    "null" bucket — same convention as get_finance_data_status.
    """
    pipeline: list[dict[str, Any]] = []
    if scope_match:
        pipeline.append({"$match": scope_match})
    pipeline.extend(
        [
            {
                "$project": {
                    "basis": 1,
                    "has_location": _has_location_expr(),
                }
            },
            {
                "$group": {
                    "_id": {"basis": "$basis", "has_location": "$has_location"},
                    "count": {"$sum": 1},
                }
            },
        ]
    )

    try:
        cursor = db["general_ledger"].aggregate(pipeline)
        docs = await cursor.to_list(length=None)
    except Exception as exc:  # noqa: BLE001 — never break Nova on a bad query
        logger.warning("get_gl_location_coverage aggregation failed: %s", exc)
        return 0, 0, {}

    row_count = 0
    with_location = 0
    by_basis_acc: dict[str, dict[str, int]] = {}
    for doc in docs:
        key = doc.get("_id") or {}
        basis = key.get("basis")
        has_location = bool(key.get("has_location"))
        count = int(doc.get("count") or 0)

        row_count += count
        if has_location:
            with_location += count

        if isinstance(basis, str) and basis:
            entry = by_basis_acc.setdefault(
                basis, {"row_count": 0, "with_location_count": 0}
            )
            entry["row_count"] += count
            if has_location:
                entry["with_location_count"] += count

    return row_count, with_location, by_basis_acc


async def _aggregate_field_presence(
    db: Any,
    scope_match: dict[str, Any],
    fields: Iterable[str],
) -> dict[str, int]:
    """Per-field count of rows where the field is non-null AND non-empty.

    A separate count_documents per field. Cheap with the typical
    general_ledger indexes; slow without them, but the alternative —
    one giant aggregation — is harder to reason about. Nova calls
    this rarely (only when the user asks about location coverage).
    """
    out: dict[str, int] = {}
    for field in fields:
        per_field_query: dict[str, Any] = dict(scope_match)
        per_field_query[field] = {"$nin": [None, ""]}
        try:
            count = await db["general_ledger"].count_documents(per_field_query)
        except Exception as exc:  # noqa: BLE001
            logger.debug("field_presence count for %s failed: %s", field, exc)
            count = 0
        try:
            out[field] = int(count)
        except (TypeError, ValueError):
            out[field] = 0
    return out


def _row_has_location(row: dict[str, Any]) -> bool:
    """Cross-validator that mirrors the Mongo ``_has_location_expr``
    used in the all-GL aggregation path. Used by the metric/category-
    scoped path that walks rows in Python."""
    for field in LOCATION_LIKE_FIELDS:
        value = row.get(field)
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return True
    return False


def _row_field_presence(row: dict[str, Any]) -> dict[str, int]:
    """Per-field 0/1 presence map for one row. Caller sums these to get
    the field_presence histogram across the filtered row set."""
    return {
        field: 1 if _row_has_location({field: row.get(field)}) else 0
        for field in LOCATION_LIKE_FIELDS
    }


async def _fetch_scoped_rows(
    db: Any,
    scope_match: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pull the rows the metric/category path needs and project the
    minimal field set: basis, account_name, and every location-like
    field. The Python categorize() belt then filters down to the
    metric scope without a second Mongo round trip."""
    project: dict[str, Any] = {"_id": 0, "basis": 1, "account_name": 1}
    for field in LOCATION_LIKE_FIELDS:
        project[field] = 1
    pipeline = [
        {"$match": scope_match},
        {"$project": project},
    ]
    try:
        cursor = db["general_ledger"].aggregate(pipeline)
        return await cursor.to_list(length=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_gl_location_coverage scoped fetch failed: %s", exc
        )
        return []


def _resolve_scope(
    metric: Optional[str],
    category: Optional[str],
) -> tuple[Optional[frozenset[str]], dict[str, Any], Optional[str]]:
    """Resolve metric/category into (allowed_categories, scope_block, error).

    Returns:
      * ``allowed_categories`` — frozenset of canonical categories the
        coverage path should keep, or ``None`` for "all GL rows"
        (broad path).
      * ``scope_block`` — the dict the response surfaces so the LLM
        can quote which scope was actually applied.
      * ``error`` — a structured error string when metric/category are
        incompatible, or ``None`` on success.
    """
    if metric is not None and metric not in _SUPPORTED_METRICS:
        return None, {}, (
            f"metric={metric!r} is not supported. Use one of "
            f"{sorted(_SUPPORTED_METRICS)} or omit metric for all-GL "
            "coverage."
        )

    if metric is None and category is None:
        return None, {
            "applied_metric": None,
            "applied_category": None,
            "applied_categories": [],
            "category_filter_applied": False,
            "scope_description": "all GL rows in date / basis scope",
        }, None

    if metric is not None and category is not None:
        metric_set = _METRIC_CATEGORY_SETS[metric]
        if category not in metric_set:
            return None, {}, (
                f"category={category!r} is not a member of metric="
                f"{metric!r}'s canonical category set "
                f"{sorted(metric_set)}. Pass a compatible category or "
                "drop one of the two."
            )
        allowed = frozenset({category})
        scope = {
            "applied_metric": metric,
            "applied_category": category,
            "applied_categories": [category],
            "category_filter_applied": True,
            "scope_description": f"{category} GL rows (subset of metric={metric!r})",
        }
        return allowed, scope, None

    if category is not None:
        # category-only path
        allowed = frozenset({category})
        scope = {
            "applied_metric": None,
            "applied_category": category,
            "applied_categories": [category],
            "category_filter_applied": True,
            "scope_description": f"{category} GL rows",
        }
        return allowed, scope, None

    # metric-only path
    allowed = _METRIC_CATEGORY_SETS[metric]  # type: ignore[index]
    if metric == "gross_profit":
        scope_description = (
            "revenue + COGS GL rows (mixed gross-profit source scope)"
        )
    else:
        scope_description = f"{metric} GL rows"
    scope = {
        "applied_metric": metric,
        "applied_category": None,
        "applied_categories": sorted(allowed),
        "category_filter_applied": True,
        "scope_description": scope_description,
    }
    return allowed, scope, None


async def get_gl_location_coverage(
    db: Any,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    basis: Optional[str] = None,
    metric: Optional[str] = None,
    category: Optional[str] = None,
) -> dict[str, Any]:
    """Read-only GL location coverage report, scoped to the same row
    set the answer is being computed over.

    Inputs:
      * ``date_from`` / ``date_to``: YYYY-MM-DD filters against
        ``tx_date``. Either may be omitted.
      * ``basis``: one of "accrual" / "cash" — restricts the scope
        when supplied.
      * ``metric``: one of "revenue", "expenses", "cogs", or
        "gross_profit". When set, coverage is computed only over
        rows whose ``categorize(account_name)`` falls into that
        metric's canonical category set.
      * ``category``: a single canonical category (e.g. "FS_REVENUE").
        When paired with ``metric`` the category must be a member of
        the metric's canonical set, else the helper returns a
        structured error rather than silently dropping the filter.

    Response shape adds a ``scope`` block alongside the existing
    ``filters`` block so the LLM can disclose which row set the
    coverage number reflects:

      {
        "filters": {date_from, date_to, basis, metric, category},
        "scope": {
          "applied_metric": "...",
          "applied_category": "...",
          "applied_categories": [...],
          "category_filter_applied": <bool>,
          "scope_description": "...",
        },
        "row_count": int,
        "with_location_count": int,
        "blank_location_count": int,
        "coverage_pct": float,
        "location_like_fields": [...],
        "field_presence": {field: int, ...},
        "by_basis": [...],
        "warning": str | None,
      }

    Never raises for a normal Mongo failure — returns empty counts +
    a diagnostic warning so Nova can disclose the data gap rather
    than crash.
    """
    allowed_categories, scope_block, scope_error = _resolve_scope(metric, category)
    if scope_error is not None:
        return {
            "filters": {
                "date_from": date_from,
                "date_to": date_to,
                "basis": basis,
                "metric": metric,
                "category": category,
            },
            "error": "invalid_scope",
            "detail": scope_error,
        }

    scope_match = _build_scope_match(date_from, date_to, basis)

    if allowed_categories is None:
        # Broad / all-GL path — same as the prior behavior, kept so
        # callers that legitimately want all-GL coverage still work.
        row_count, with_location_count, by_basis_acc = await _aggregate_coverage(db, scope_match)
        field_presence = await _aggregate_field_presence(db, scope_match, LOCATION_LIKE_FIELDS)
    else:
        # Metric/category-scoped path — fetch matching rows and
        # filter via categorize() (mirror the MRR fix's strategy).
        rows = await _fetch_scoped_rows(db, scope_match)
        row_count = 0
        with_location_count = 0
        by_basis_acc = {}
        field_presence = {f: 0 for f in LOCATION_LIKE_FIELDS}
        for row in rows:
            account_name = row.get("account_name") or ""
            row_category = categorize(account_name) if account_name else "UNCATEGORIZED"
            if row_category not in allowed_categories:
                continue
            row_count += 1
            located = _row_has_location(row)
            if located:
                with_location_count += 1
            for field, present in _row_field_presence(row).items():
                field_presence[field] += present
            row_basis = row.get("basis")
            if isinstance(row_basis, str) and row_basis:
                entry = by_basis_acc.setdefault(
                    row_basis, {"row_count": 0, "with_location_count": 0}
                )
                entry["row_count"] += 1
                if located:
                    entry["with_location_count"] += 1

    blank_location_count = max(row_count - with_location_count, 0)
    coverage_pct = _safe_pct(with_location_count, row_count)

    by_basis: list[dict[str, Any]] = []
    for basis_label in sorted(by_basis_acc.keys()):
        entry = by_basis_acc[basis_label]
        by_basis.append(
            {
                "basis": basis_label,
                "row_count": entry["row_count"],
                "with_location_count": entry["with_location_count"],
                "coverage_pct": _safe_pct(entry["with_location_count"], entry["row_count"]),
            }
        )

    warnings: list[str] = []
    if row_count == 0:
        warnings.append(
            "No GL rows match the supplied scope; coverage cannot be computed."
        )
    elif with_location_count == 0:
        warnings.append(
            "None of the GL rows in this scoped result carry a populated "
            "location/department field. Location-level reporting is not "
            "reliable for this scope."
        )
    elif coverage_pct < 1.0:
        warnings.append(
            "Location coverage is incomplete for this scoped result. "
            f"Rows without location cannot be attributed "
            f"({blank_location_count} of {row_count} rows blank, "
            f"coverage {coverage_pct:.0%})."
        )

    warning = " ".join(warnings) if warnings else None

    return {
        "filters": {
            "date_from": date_from,
            "date_to": date_to,
            "basis": basis,
            "metric": metric,
            "category": category,
        },
        "scope": scope_block,
        "row_count": row_count,
        "with_location_count": with_location_count,
        "blank_location_count": blank_location_count,
        "coverage_pct": coverage_pct,
        "location_like_fields": list(LOCATION_LIKE_FIELDS),
        "field_presence": field_presence,
        "by_basis": by_basis,
        "warning": warning,
    }


__all__ = [
    "LOCATION_LIKE_FIELDS",
    "get_gl_location_coverage",
]
