"""Tests for get_gl_location_coverage.

Nova needs to disclose how many GL rows in scope carry a populated
location/department field before quoting location-attributed totals.
These tests pin the contract end-to-end using synthetic GL rows so
the assertions survive any future production-data shift.

The fake collection below implements just enough of the Mongo
aggregation surface that the helper emits:

  * count_documents({...}) with simple key/value, $nin, and basic
    range / dotted operators
  * aggregate(pipeline) where the pipeline is
    [$match?, $project, $group]

It is NOT a general Mongo simulator — every supported operator is
present because the helper actually emits it. Anything else raises so
a future helper change that emits an unhandled operator surfaces here
instead of silently returning empty.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")

import pytest

from services.nova_engine.gl_location_coverage import (
    LOCATION_LIKE_FIELDS,
    get_gl_location_coverage,
)


# ---------------------------------------------------------------------------
# In-memory Mongo-ish fake (only what the helper exercises)
# ---------------------------------------------------------------------------


def _matches(doc: dict, query: dict) -> bool:
    for key, condition in query.items():
        if key in ("$and",):
            if not all(_matches(doc, sub) for sub in condition):
                return False
            continue
        if key in ("$or",):
            if not any(_matches(doc, sub) for sub in condition):
                return False
            continue
        value = doc.get(key)
        if isinstance(condition, dict):
            for op, op_val in condition.items():
                if op == "$nin":
                    if value in op_val:
                        return False
                elif op == "$gte":
                    if value is None or value < op_val:
                        return False
                elif op == "$lte":
                    if value is None or value > op_val:
                        return False
                elif op == "$ne":
                    if value == op_val:
                        return False
                elif op == "$exists":
                    present = key in doc
                    if bool(op_val) != present:
                        return False
                else:
                    raise NotImplementedError(f"fake op: {op}")
        else:
            if value != condition:
                return False
    return True


def _eval_expr(expr: Any, doc: dict) -> Any:
    """Tiny evaluator for the $project expressions the helper emits."""
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr.lstrip("$"))
    if isinstance(expr, dict):
        # Single-op shape like {"$or": [...]} or {"$and": [...]} or {"$ne": [...]}
        if len(expr) != 1:
            # Pass-through shapes like {"basis": 1} are project-include
            # markers, not expressions; the helper's $project stage uses
            # them only on top-level keys, handled in _apply_project.
            raise NotImplementedError(f"fake multi-key expr: {expr!r}")
        ((op, args),) = expr.items()
        if op == "$or":
            return any(_eval_expr(a, doc) for a in args)
        if op == "$and":
            return all(_eval_expr(a, doc) for a in args)
        if op == "$ne":
            left, right = args
            return _eval_expr(left, doc) != _eval_expr(right, doc)
        if op == "$ifNull":
            value = _eval_expr(args[0], doc)
            return args[1] if value is None else value
        raise NotImplementedError(f"fake expr op: {op}")
    return expr


def _apply_project(doc: dict, projection: dict) -> dict:
    out: dict[str, Any] = {}
    for key, spec in projection.items():
        if isinstance(spec, dict):
            out[key] = _eval_expr(spec, doc)
        elif spec in (1, True):
            if key in doc:
                out[key] = doc[key]
        elif spec in (0, False):
            # _id: 0 / field: 0 — exclusion. Tiny fake mirrors Mongo
            # by simply not copying that field through.
            continue
        else:
            raise NotImplementedError(f"fake project spec for {key}: {spec!r}")
    return out


class _FakeCursor:
    def __init__(self, docs: Iterable[dict]) -> None:
        self._docs = list(docs)

    async def to_list(self, length: int | None = None) -> list[dict]:
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _FakeGLCollection:
    def __init__(self, docs: Iterable[dict]) -> None:
        self.docs = [dict(d) for d in docs]

    async def count_documents(self, query: dict) -> int:
        if not query:
            return len(self.docs)
        return sum(1 for d in self.docs if _matches(d, query))

    def aggregate(self, pipeline: list[dict]) -> _FakeCursor:
        rows = list(self.docs)
        for stage in pipeline:
            if "$match" in stage:
                rows = [d for d in rows if _matches(d, stage["$match"])]
            elif "$project" in stage:
                rows = [_apply_project(d, stage["$project"]) for d in rows]
            elif "$group" in stage:
                spec = stage["$group"]
                _id_spec = spec["_id"]
                buckets: dict[Any, list[dict]] = {}
                for r in rows:
                    if isinstance(_id_spec, dict):
                        key = tuple(
                            (k, _eval_expr(v, r) if isinstance(v, str) else v)
                            for k, v in _id_spec.items()
                        )
                    elif isinstance(_id_spec, str) and _id_spec.startswith("$"):
                        key = r.get(_id_spec.lstrip("$"))
                    else:
                        key = _id_spec
                    buckets.setdefault(key, []).append(r)
                grouped: list[dict] = []
                for key, group_rows in buckets.items():
                    out: dict[str, Any] = {}
                    if isinstance(_id_spec, dict):
                        out["_id"] = {k: v for (k, v) in key}
                    else:
                        out["_id"] = key
                    for out_field, expr in spec.items():
                        if out_field == "_id":
                            continue
                        if not isinstance(expr, dict) or len(expr) != 1:
                            raise NotImplementedError(
                                f"fake group expr: {expr!r}"
                            )
                        ((op, op_arg),) = expr.items()
                        if op == "$sum" and op_arg == 1:
                            out[out_field] = len(group_rows)
                        elif op == "$sum" and isinstance(op_arg, str) and op_arg.startswith("$"):
                            out[out_field] = sum(
                                (r.get(op_arg.lstrip("$")) or 0) for r in group_rows
                            )
                        else:
                            raise NotImplementedError(
                                f"fake group op: {op}={op_arg!r}"
                            )
                    grouped.append(out)
                rows = grouped
            else:
                raise NotImplementedError(f"fake stage: {list(stage)!r}")
        return _FakeCursor(rows)


class _FakeDb:
    def __init__(self, *, general_ledger: Iterable[dict]) -> None:
        self._collections = {"general_ledger": _FakeGLCollection(general_ledger)}

    def __getitem__(self, name: str) -> _FakeGLCollection:
        if name not in self._collections:
            self._collections[name] = _FakeGLCollection([])
        return self._collections[name]


# ---------------------------------------------------------------------------
# Synthetic fixture rows
# ---------------------------------------------------------------------------


def _row(
    *,
    tx_date: str = "2099-06-15",
    basis: str = "accrual",
    location: str | None = None,
    location_id: str | None = None,
    location_name: str | None = None,
    department: str | None = None,
    department_name: str | None = None,
    department_ref_id: str | None = None,
    department_ref_name: str | None = None,
) -> dict:
    """Build a synthetic GL row. Only fields explicitly passed are set —
    omitted fields are absent from the row, mirroring how Mongo treats
    missing fields. The fixture deliberately uses a far-future synthetic
    year to avoid any chance of pasting a production date."""
    out: dict[str, Any] = {"tx_date": tx_date, "basis": basis}
    for name, value in (
        ("location", location),
        ("location_id", location_id),
        ("location_name", location_name),
        ("department", department),
        ("department_name", department_name),
        ("department_ref_id", department_ref_id),
        ("department_ref_name", department_ref_name),
    ):
        if value is not None:
            out[name] = value
    return out


# ---------------------------------------------------------------------------
# Field-level "is this row located?" cases (1-5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name,fixture_value",
    [
        ("location", "HQ"),
        ("location_id", "loc-1"),
        ("location_name", "HQ"),
        ("department", "Sales"),
        ("department_name", "Sales"),
        ("department_ref_id", "dept-1"),
        ("department_ref_name", "Sales"),
    ],
)
def test_row_with_any_location_like_field_counts_as_located(field_name, fixture_value):
    db = _FakeDb(general_ledger=[_row(**{field_name: fixture_value})])

    result = asyncio.run(get_gl_location_coverage(db))

    assert result["row_count"] == 1
    assert result["with_location_count"] == 1
    assert result["blank_location_count"] == 0
    assert result["coverage_pct"] == pytest.approx(1.0)


def test_row_with_no_location_like_fields_counts_as_blank():
    db = _FakeDb(general_ledger=[_row()])

    result = asyncio.run(get_gl_location_coverage(db))

    assert result["row_count"] == 1
    assert result["with_location_count"] == 0
    assert result["blank_location_count"] == 1
    assert result["coverage_pct"] == 0.0


def test_row_with_explicit_empty_string_counts_as_blank():
    """A row that carries the field but with an empty string is
    indistinguishable from a row missing the field for coverage
    purposes — neither can be attributed to a location."""
    rows = [
        _row(location_name=""),
        _row(department_ref_id=""),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db))

    assert result["row_count"] == 2
    assert result["with_location_count"] == 0


# ---------------------------------------------------------------------------
# Coverage % derived from fixture rows (6)
# ---------------------------------------------------------------------------


def test_coverage_pct_is_computed_from_fixture_rows():
    located = [_row(location_id=f"loc-{i}") for i in range(3)]
    blank = [_row() for _ in range(7)]
    db = _FakeDb(general_ledger=located + blank)

    result = asyncio.run(get_gl_location_coverage(db))

    expected_total = len(located) + len(blank)
    expected_with_location = len(located)
    assert result["row_count"] == expected_total
    assert result["with_location_count"] == expected_with_location
    assert result["coverage_pct"] == pytest.approx(expected_with_location / expected_total)


# ---------------------------------------------------------------------------
# by_basis (7)
# ---------------------------------------------------------------------------


def test_by_basis_breakdown_is_computed_from_fixture_rows():
    rows = [
        _row(basis="accrual", location_name="HQ"),
        _row(basis="accrual", location_name="HQ"),
        _row(basis="accrual"),  # blank
        _row(basis="cash", location_name="HQ"),
        _row(basis="cash"),  # blank
        _row(basis="cash"),  # blank
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db))

    by_basis = {entry["basis"]: entry for entry in result["by_basis"]}
    assert set(by_basis.keys()) == {"accrual", "cash"}

    accrual_total = sum(1 for r in rows if r["basis"] == "accrual")
    accrual_with = sum(
        1 for r in rows if r["basis"] == "accrual" and any(r.get(f) for f in LOCATION_LIKE_FIELDS)
    )
    cash_total = sum(1 for r in rows if r["basis"] == "cash")
    cash_with = sum(
        1 for r in rows if r["basis"] == "cash" and any(r.get(f) for f in LOCATION_LIKE_FIELDS)
    )

    assert by_basis["accrual"]["row_count"] == accrual_total
    assert by_basis["accrual"]["with_location_count"] == accrual_with
    assert by_basis["accrual"]["coverage_pct"] == pytest.approx(accrual_with / accrual_total)
    assert by_basis["cash"]["row_count"] == cash_total
    assert by_basis["cash"]["with_location_count"] == cash_with
    assert by_basis["cash"]["coverage_pct"] == pytest.approx(cash_with / cash_total)


# ---------------------------------------------------------------------------
# Coverage warning when low (10)
# ---------------------------------------------------------------------------


def test_coverage_warning_appears_when_partial_coverage():
    """Coverage > 0 but < 1 trips the new "incomplete" warning whose
    text is contracted by the v1-readiness-Part-A spec."""
    located = [_row(location_id=f"loc-{i}") for i in range(3)]
    blank = [_row() for _ in range(7)]
    db = _FakeDb(general_ledger=located + blank)

    result = asyncio.run(get_gl_location_coverage(db))

    assert result["warning"] is not None
    lowered = result["warning"].lower()
    assert "location coverage is incomplete" in lowered
    assert "rows without location cannot be attributed" in lowered
    assert "30%" in lowered
    assert str(len(blank)) in result["warning"]


def test_zero_coverage_warning_says_location_reporting_is_not_reliable():
    """When row_count > 0 but with_location_count == 0, the new
    contracted warning says location-level reporting is not reliable
    for that scope (this is the audited bug Nova was missing)."""
    db = _FakeDb(general_ledger=[_row() for _ in range(5)])

    result = asyncio.run(get_gl_location_coverage(db))

    assert result["row_count"] == 5
    assert result["with_location_count"] == 0
    assert result["coverage_pct"] == 0.0
    assert result["warning"] is not None
    lowered = result["warning"].lower()
    assert "none of the gl rows in this scoped result carry a populated location" in lowered
    assert "location-level reporting is not reliable for this scope" in lowered


def test_coverage_warning_absent_when_full_coverage():
    located = [_row(location_id=f"loc-{i}") for i in range(5)]
    db = _FakeDb(general_ledger=located)

    result = asyncio.run(get_gl_location_coverage(db))

    assert result["coverage_pct"] == pytest.approx(1.0)
    assert result["warning"] is None


def test_coverage_warning_for_empty_scope_says_no_rows():
    db = _FakeDb(general_ledger=[])

    result = asyncio.run(get_gl_location_coverage(db))

    assert result["row_count"] == 0
    assert result["warning"]
    assert "no gl rows" in result["warning"].lower()


# ---------------------------------------------------------------------------
# Field set parity with nova_tools._LOCATION_LIKE_FIELDS
# ---------------------------------------------------------------------------


def test_location_like_fields_match_nova_tools_canonical_set():
    """The two surfaces (this helper and the location_blank filter on
    query_gl_transactions) must agree on what counts as 'has a
    location'. If one drifts ahead of the other, location_blank=true
    and coverage will report contradictory numbers."""
    from services.ai.agents.tools.nova_tools import _LOCATION_LIKE_FIELDS as canonical

    assert set(LOCATION_LIKE_FIELDS) == set(canonical)


def test_response_carries_location_like_fields_list():
    db = _FakeDb(general_ledger=[_row(location_id="loc-1")])
    result = asyncio.run(get_gl_location_coverage(db))
    assert set(result["location_like_fields"]) == set(LOCATION_LIKE_FIELDS)


# ---------------------------------------------------------------------------
# Field presence per field
# ---------------------------------------------------------------------------


def test_field_presence_counts_per_field_from_fixture_rows():
    rows = [
        _row(location_id="loc-1"),
        _row(location_id="loc-2"),
        _row(location_name="HQ"),  # different field, no location_id
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db))

    expected_id_count = sum(1 for r in rows if r.get("location_id"))
    expected_name_count = sum(1 for r in rows if r.get("location_name"))
    assert result["field_presence"]["location_id"] == expected_id_count
    assert result["field_presence"]["location_name"] == expected_name_count
    assert result["field_presence"]["department_ref_name"] == 0


# ---------------------------------------------------------------------------
# Filters echo back to the response
# ---------------------------------------------------------------------------


def test_filters_echo_back_into_response():
    db = _FakeDb(general_ledger=[_row()])
    result = asyncio.run(
        get_gl_location_coverage(
            db,
            date_from="2099-01-01",
            date_to="2099-12-31",
            basis="accrual",
            category="PS_REVENUE",
        )
    )
    assert result["filters"]["date_from"] == "2099-01-01"
    assert result["filters"]["date_to"] == "2099-12-31"
    assert result["filters"]["basis"] == "accrual"
    assert result["filters"]["category"] == "PS_REVENUE"


def test_category_filter_now_actually_scopes_coverage():
    """The previous "category recognized but not yet applied"
    warning is GONE — category filtering is now applied via the
    Python categorize() belt. The response carries an explicit
    scope block confirming the filter was applied."""
    # PS revenue row is located; FS revenue row is blank.
    rows = [
        _ps_revenue_row_located(location_id="loc-1"),
        _fs_revenue_row_blank(),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, category="PS_REVENUE"))

    assert result.get("error") is None
    assert result["scope"]["category_filter_applied"] is True
    assert result["scope"]["applied_category"] == "PS_REVENUE"
    # Only the PS_REVENUE row counted — coverage is 1/1 = 100%.
    assert result["row_count"] == 1
    assert result["with_location_count"] == 1
    assert result["coverage_pct"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Metric / category scope (Part A of the V1 readiness fix)
# ---------------------------------------------------------------------------
#
# The audited bug: Nova reported May 2026 revenue location coverage at
# 100% from all-GL data while the actual revenue rows had 0% coverage.
# These tests pin that the metric-aware path filters rows by
# categorize(account_name) before computing coverage.


def _ps_revenue_row_located(*, location_id="HQ-1") -> dict:
    return {
        "tx_date": "2099-05-10",
        "basis": "accrual",
        "account_name": "50101 Professional Services Revenue",
        "location_id": location_id,
    }


def _ps_revenue_row_blank() -> dict:
    return {
        "tx_date": "2099-05-10",
        "basis": "accrual",
        "account_name": "50101 Professional Services Revenue",
    }


def _fs_revenue_row_blank() -> dict:
    return {
        "tx_date": "2099-05-10",
        "basis": "accrual",
        "account_name": "50201 Field Services Revenue",
    }


def _fs_revenue_row_located(*, location_id="HQ-1") -> dict:
    return {
        "tx_date": "2099-05-10",
        "basis": "accrual",
        "account_name": "50201 Field Services Revenue",
        "location_id": location_id,
    }


def _payroll_row_located(*, location_id="HQ-1") -> dict:
    return {
        "tx_date": "2099-05-10",
        "basis": "accrual",
        "account_name": "70601 Payroll Expenses",
        "location_id": location_id,
    }


def _payroll_row_blank() -> dict:
    return {
        "tx_date": "2099-05-10",
        "basis": "accrual",
        "account_name": "70601 Payroll Expenses",
    }


def _ps_cogs_row_blank() -> dict:
    return {
        "tx_date": "2099-05-10",
        "basis": "accrual",
        "account_name": "60302 PS COGS",
    }


def _ps_cogs_row_located(*, location_id="HQ-1") -> dict:
    return {
        "tx_date": "2099-05-10",
        "basis": "accrual",
        "account_name": "60302 PS COGS",
        "location_id": location_id,
    }


def test_all_gl_coverage_path_unchanged_when_no_metric_or_category():
    """The broad path that prior callers rely on must keep returning
    overall GL coverage. Scope block is present but flagged as
    unfiltered."""
    rows = [
        _ps_revenue_row_located(),
        _payroll_row_blank(),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db))

    assert result["scope"]["category_filter_applied"] is False
    assert result["scope"]["applied_metric"] is None
    assert result["row_count"] == len(rows)
    expected_with = sum(1 for r in rows if r.get("location_id"))
    assert result["with_location_count"] == expected_with


def test_metric_revenue_scopes_coverage_to_revenue_rows_only():
    """Audited regression: revenue-by-location coverage must only
    count revenue rows. A located payroll row must NOT inflate the
    revenue coverage number."""
    rows = [
        _ps_revenue_row_blank(),
        _ps_revenue_row_blank(),
        _ps_revenue_row_blank(),
        _payroll_row_located(),  # located but NOT a revenue row
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, metric="revenue"))

    assert result["scope"]["applied_metric"] == "revenue"
    assert result["row_count"] == 3
    assert result["with_location_count"] == 0
    assert result["coverage_pct"] == 0.0
    assert "location-level reporting is not reliable" in result["warning"].lower()


def test_metric_expenses_scopes_coverage_to_expense_rows_only():
    rows = [
        _payroll_row_located(),
        _payroll_row_blank(),
        _ps_revenue_row_located(),  # NOT an expense row
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, metric="expenses"))

    assert result["scope"]["applied_metric"] == "expenses"
    assert result["row_count"] == 2  # only the two payroll rows
    assert result["with_location_count"] == 1
    assert result["coverage_pct"] == pytest.approx(0.5)


def test_metric_cogs_scopes_coverage_to_cogs_rows_only():
    rows = [
        _ps_cogs_row_located(),
        _ps_cogs_row_blank(),
        _payroll_row_located(),  # NOT cogs (operating expense)
        _ps_revenue_row_located(),  # NOT cogs
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, metric="cogs"))

    assert result["scope"]["applied_metric"] == "cogs"
    assert result["row_count"] == 2  # only the two cogs rows
    assert result["with_location_count"] == 1


def test_category_fs_revenue_scopes_to_fs_revenue_only():
    rows = [
        _ps_revenue_row_located(),  # PS, not FS
        _fs_revenue_row_blank(),
        _fs_revenue_row_blank(),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, category="FS_REVENUE"))

    assert result["scope"]["applied_category"] == "FS_REVENUE"
    assert result["row_count"] == 2  # only the two FS rows
    assert result["with_location_count"] == 0


def test_metric_and_category_must_be_compatible():
    """metric=revenue + category=FS_COGS must return a structured
    invalid_scope error — silently dropping the filter would
    mislead the user."""
    db = _FakeDb(general_ledger=[_ps_revenue_row_located()])

    result = asyncio.run(
        get_gl_location_coverage(db, metric="revenue", category="FS_COGS")
    )

    assert result.get("error") == "invalid_scope"
    assert "FS_COGS" in result["detail"]


def test_by_basis_breakdown_is_after_metric_filter():
    rows = [
        _ps_revenue_row_located(),
        _ps_revenue_row_blank(),
        # Payroll rows must NOT show up in revenue's by_basis count.
        _payroll_row_located(),
        _payroll_row_located(),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, metric="revenue"))

    by_basis = {entry["basis"]: entry for entry in result["by_basis"]}
    assert by_basis["accrual"]["row_count"] == 2  # the two PS revenue rows
    assert by_basis["accrual"]["with_location_count"] == 1


def test_field_presence_is_after_metric_filter():
    rows = [
        _ps_revenue_row_blank(),
        _ps_revenue_row_blank(),
        # Payroll row's location must NOT count toward revenue's
        # field_presence histogram.
        _payroll_row_located(location_id="HQ-1"),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, metric="revenue"))

    assert result["field_presence"]["location_id"] == 0


def test_metric_gross_profit_spans_revenue_and_cogs():
    rows = [
        _ps_revenue_row_located(),
        _ps_cogs_row_located(),
        _payroll_row_located(),  # payroll is OpEx, not part of GP
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, metric="gross_profit"))

    assert result["scope"]["applied_metric"] == "gross_profit"
    assert "mixed" in result["scope"]["scope_description"].lower()
    # 2 rows in scope (revenue + cogs); payroll excluded.
    assert result["row_count"] == 2


def test_invalid_metric_returns_invalid_scope_error():
    db = _FakeDb(general_ledger=[_ps_revenue_row_located()])

    result = asyncio.run(get_gl_location_coverage(db, metric="ebitda"))

    assert result.get("error") == "invalid_scope"


def test_basis_filter_restricts_scope():
    rows = [
        _row(basis="accrual", location_id="loc-1"),
        _row(basis="cash", location_id="loc-2"),
        _row(basis="cash"),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(get_gl_location_coverage(db, basis="accrual"))

    assert result["row_count"] == 1
    assert result["with_location_count"] == 1
    # by_basis only shows the basis we filtered to.
    assert [e["basis"] for e in result["by_basis"]] == ["accrual"]


def test_date_range_filter_restricts_scope():
    rows = [
        _row(tx_date="2099-01-15", location_id="loc-1"),
        _row(tx_date="2099-06-15", location_id="loc-2"),
        _row(tx_date="2099-12-15", location_id="loc-3"),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        get_gl_location_coverage(db, date_from="2099-04-01", date_to="2099-09-30")
    )

    assert result["row_count"] == 1
    assert result["with_location_count"] == 1


# ---------------------------------------------------------------------------
# Tool-level integration via the dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_returns_coverage_payload(monkeypatch):
    """Round-trip through execute_tool to confirm dispatch + arg
    validation + qbo_db wiring."""
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool

    db = _FakeDb(
        general_ledger=[
            _row(basis="accrual", location_id="loc-1"),
            _row(basis="accrual"),
        ]
    )
    monkeypatch.setattr("services.nova_engine.db.get_qbo_db", lambda: db)

    result = await execute_tool(
        "get_gl_location_coverage",
        {"basis": "accrual"},
        _ScenarioState(),
        "user_x",
        None,
        None,
    )

    assert "row_count" in result
    assert result["row_count"] == 2
    assert result["with_location_count"] == 1
    assert result["filters"]["basis"] == "accrual"
    assert result["location_like_fields"]


@pytest.mark.asyncio
async def test_dispatcher_rejects_invalid_basis():
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool

    result = await execute_tool(
        "get_gl_location_coverage",
        {"basis": "trust_me"},
        _ScenarioState(),
        "user_x",
        None,
        None,
    )

    assert result.get("error") == "invalid_basis"


@pytest.mark.asyncio
async def test_dispatcher_rejects_invalid_date():
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool

    result = await execute_tool(
        "get_gl_location_coverage",
        {"date_from": "2099/01/01"},
        _ScenarioState(),
        "user_x",
        None,
        None,
    )

    assert result.get("error") == "invalid_date_from"
