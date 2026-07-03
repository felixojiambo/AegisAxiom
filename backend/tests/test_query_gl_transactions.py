"""Tests for the query_gl_transactions Nova tool.

Every numeric assertion is computed from the fixture rows in the same
test setup. No literal that happens to match a real tenant's books is
hardcoded here — that would defeat the purpose of fixture-driven
testing and create a regression surface tied to one tenant.

The tool dispatches under tool_name='query_gl_transactions' through
``nova_tools.execute_tool``. Tests construct a fake qbo_db whose
``general_ledger`` collection mirrors the subset of Mongo's API the
tool uses (find/find_one + cursor.sort/to_list).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from services.ai.agents.tools.nova_tools import (
    execute_tool,
    _is_blank_location,
    _is_blank_value,
    _value_at_path,
)


# ---------------------------------------------------------------------------
# Fake Mongo
# ---------------------------------------------------------------------------
# A tiny in-Python evaluator for the subset of $match operators the tool
# emits. Implementing this rather than letting the mock return all docs
# means the Mongo-side date+basis+location filtering is exercised, not
# just the Python categorization that runs after.


def _match_value(value: Any, criterion: Any) -> bool:
    """True iff value satisfies the Mongo-style criterion for one field."""
    import re as _re

    if isinstance(criterion, dict):
        # Operator dict: each key is a Mongo operator
        for op, target in criterion.items():
            if op == "$gte":
                if not (value is not None and value >= target):
                    return False
            elif op == "$lte":
                if not (value is not None and value <= target):
                    return False
            elif op == "$lt":
                if not (value is not None and value < target):
                    return False
            elif op == "$exists":
                # Caller handles $exists at the field level
                return target is True if value is not None else target is False
            elif op == "$ne":
                if value == target:
                    return False
            elif op == "$in":
                if value not in target:
                    return False
            elif op == "$regex":
                # Mongo's $regex only matches if the field is a string.
                if not isinstance(value, str):
                    return False
                if not _re.search(target, value):
                    return False
            else:  # pragma: no cover — should not be emitted by the tool
                raise NotImplementedError(f"Unsupported operator {op!r}")
        return True
    return value == criterion


def _doc_matches(doc: dict, match: dict) -> bool:
    """Recursive Mongo $match evaluator covering $and / $or / $exists /
    equality, which is the entire surface area the tool uses today."""
    for key, criterion in match.items():
        if key == "$and":
            if not all(_doc_matches(doc, sub) for sub in criterion):
                return False
        elif key == "$or":
            if not any(_doc_matches(doc, sub) for sub in criterion):
                return False
        else:
            # Field-level criterion. $exists must be evaluated with
            # awareness of doc.__contains__, not doc.get() which can't
            # tell missing-vs-null apart.
            if isinstance(criterion, dict) and "$exists" in criterion:
                target = criterion["$exists"]
                present = key in doc
                if target is True and not present:
                    return False
                if target is False and present:
                    return False
                # Plus other ops in the same dict
                remaining = {k: v for k, v in criterion.items() if k != "$exists"}
                if remaining and not _match_value(doc.get(key), remaining):
                    return False
            else:
                if not _match_value(doc.get(key), criterion):
                    return False
    return True


class _FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def sort(self, *args, **kwargs) -> "_FakeCursor":
        return self

    def limit(self, n: int) -> "_FakeCursor":
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length: int | None = None):
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _FakeCollection:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def find(self, match: dict | None = None, projection: dict | None = None) -> _FakeCursor:
        match = match or {}
        # Apply filter, then strip _id (the projection from the tool always
        # excludes _id); other projected fields are returned as-is so the
        # tool can read whichever it expects.
        filtered = [d for d in self._docs if _doc_matches(d, match)]
        out = []
        for d in filtered:
            d2 = dict(d)
            d2.pop("_id", None)
            out.append(d2)
        return _FakeCursor(out)

    async def find_one(self, match: dict | None = None, projection: dict | None = None):
        match = match or {}
        for d in self._docs:
            if _doc_matches(d, match):
                d2 = dict(d)
                d2.pop("_id", None)
                return d2
        return None


class _FakeDb:
    def __init__(self, gl_docs: list[dict]) -> None:
        self.general_ledger = _FakeCollection(gl_docs)


def _run(coro):
    """Single source of asyncio.run — keeps tests synchronous-looking."""
    return asyncio.run(coro)


def _call_tool(gl_docs: list[dict], **tool_args) -> dict:
    """Dispatch query_gl_transactions against an in-memory fake DB."""
    fake_db = _FakeDb(gl_docs)
    with patch("services.nova_engine.db.get_qbo_db", return_value=fake_db):
        return _run(execute_tool(
            tool_name="query_gl_transactions",
            tool_args=tool_args,
            state=None,
            user_id="u_test",
            session_id="s_test",
            message_id="m_test",
        ))


# Synthetic doc factory. Defaults yield a row that categorizes as
# PS_REVENUE (50101 prefix) so individual tests only need to override
# the fields they care about.
def _gl_row(
    *,
    tx_date: str,
    amount: float,
    account_name: str = "50101 Professional Services Income",
    basis: str = "accrual",
    txn_type: str = "Invoice",
    doc_num: str = "10000",
    name: str = "Acme Corp",
    memo: str = "",
    location_id: Any = None,
    location_name: Any = None,
    department_ref_id: Any = None,
    department_ref_name: Any = None,
    include_location_fields: bool = True,
) -> dict:
    """Build a fixture row.

    include_location_fields controls whether the location-like keys are
    present on the document at all. Defaults to True (matches the live
    AEGISAXIOM schema where location_id / location_name / department_ref_id /
    department_ref_name are all present-but-often-null). The location-
    unavailable test passes False so the row carries no location keys
    whatsoever.
    """
    row: dict[str, Any] = {
        "tx_date": tx_date,
        "basis": basis,
        "account_name": account_name,
        "amount": amount,
        # debit/credit derived from amount under the credit-balance
        # convention: credit-balance accounts have positive amount when
        # credited, negative when debited (refund/reversal).
        "credit": amount if amount >= 0 else 0.0,
        "debit": -amount if amount < 0 else 0.0,
        "doc_num": doc_num,
        "txn_type": txn_type,
        "name": name,
        "memo": memo,
    }
    if include_location_fields:
        row["location_id"] = location_id
        row["location_name"] = location_name
        row["department_ref_id"] = department_ref_id
        row["department_ref_name"] = department_ref_name
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestArbitraryDateRange:
    """Test 1: Synthetic 2027-03 window. Expected total derived from fixture."""

    def test_total_income_matches_fixture_sum(self):
        # Synthetic future year is intentional — surfaces any year
        # hardcode in the tool's logic as a regression.
        rows = [
            _gl_row(tx_date="2027-03-05", amount=1234.56),
            _gl_row(tx_date="2027-03-12", amount=789.10, doc_num="10001"),
            _gl_row(tx_date="2027-03-29", amount=4500.00, doc_num="10002"),
        ]
        expected_total = sum(r["amount"] for r in rows)  # FIXTURE-DERIVED

        result = _call_tool(
            rows,
            date_from="2027-03-01",
            date_to="2027-03-31",
        )

        assert result["row_count"] == len(rows)
        assert result["rows_returned"] == len(rows)
        assert result["preview_truncated"] is False
        # spec section F: total_income is positive
        assert result["total_income"] == pytest.approx(abs(expected_total), abs=0.01)
        assert result["total_income"] > 0
        assert result["location_field_status"] == "available"


class TestIncomeCategoryFiltering:
    """Test 2: arbitrary January window. Mix of revenue + COGS rows; the
    tool keeps the revenue categories and drops the COGS rows. Every
    assertion is computed from the fixture - no real-tenant figure ever
    touches the test."""

    def test_cogs_excluded_other_revenue_included_total_matches_sum(self):
        rows = [
            # PS_REVENUE
            _gl_row(tx_date="2030-01-05", amount=10000.00,
                    account_name="50101 Professional Services Income",
                    doc_num="INV-1001"),
            # FS_REVENUE
            _gl_row(tx_date="2030-01-12", amount=5500.00,
                    account_name="50201 Field Services Income",
                    doc_num="INV-1002"),
            # OTHER_REVENUE — categorize() routes any "INCOME"-named
            # account without a 5xxxx prefix to OTHER_REVENUE.
            _gl_row(tx_date="2030-01-18", amount=750.00,
                    account_name="Miscellaneous Income",
                    doc_num="INV-1003"),
            # PS_COGS — must be excluded by the income-category filter.
            _gl_row(tx_date="2030-01-22", amount=2000.00,
                    account_name="60302 Professional Services COGS",
                    doc_num="JE-2001"),
            # FS_COGS — must be excluded too.
            _gl_row(tx_date="2030-01-28", amount=900.00,
                    account_name="60201 Field Services COGS",
                    doc_num="JE-2002"),
        ]

        # FIXTURE-DERIVED expected sum: revenue rows only.
        expected_total = sum(
            r["amount"] for r in rows
            if r["account_name"].startswith(("50101", "50201"))
            or "Income" in r["account_name"]
            and not r["account_name"].startswith("60")
        )

        result = _call_tool(
            rows,
            date_from="2030-01-01",
            date_to="2030-01-31",
        )

        # Three income rows expected, two COGS excluded.
        income_only = [r for r in rows
                       if not r["account_name"].startswith("60")]
        assert result["row_count"] == len(income_only)

        # Categories returned should be exactly the three income kinds.
        cats = {r["category"] for r in result["rows"]}
        assert cats == {"PS_REVENUE", "FS_REVENUE", "OTHER_REVENUE"}, (
            f"Expected only income categories, got {cats}"
        )

        # COGS account names must not appear.
        returned_account_names = [r["account_name"] for r in result["rows"]]
        for cogs_row in [r for r in rows if r["account_name"].startswith("60")]:
            assert cogs_row["account_name"] not in returned_account_names

        # OTHER_REVENUE must be included.
        assert any(r["category"] == "OTHER_REVENUE" for r in result["rows"])

        # Total matches the fixture-derived sum, positive.
        assert result["total_income"] == pytest.approx(abs(expected_total), abs=0.01)
        assert result["total_income"] > 0


class TestTruncation:
    """Test 3: 250 fixture rows, limit=50."""

    def test_row_count_full_rows_returned_capped_total_full(self):
        FIXTURE_COUNT = 250
        rows = [
            _gl_row(
                # Spread within a single month so date filter accepts all.
                tx_date=f"2027-04-{(i % 28) + 1:02d}",
                amount=100.0 + i,  # distinct so total is non-trivial
                doc_num=f"INV-{i:05d}",
            )
            for i in range(FIXTURE_COUNT)
        ]
        # FIXTURE-DERIVED: sum across all 250 rows.
        expected_total_full = sum(r["amount"] for r in rows)

        result = _call_tool(
            rows,
            date_from="2027-04-01",
            date_to="2027-04-30",
            limit=50,
        )

        assert result["row_count"] == FIXTURE_COUNT
        assert result["rows_returned"] == 50
        assert len(result["rows"]) == 50
        assert result["preview_truncated"] is True
        # Critical: total reflects ALL matching rows, not just the slice.
        assert result["total_income"] == pytest.approx(
            abs(expected_total_full), abs=0.01
        )
        # Notes must surface the truncation so the LLM tells the user.
        assert result["notes"] is not None
        assert "first 50 of 250" in result["notes"] or "Showing first 50" in result["notes"]


class TestSignConvention:
    """Test 4: amount_raw negative for a revenue posting (refund/reversal)."""

    def test_normalized_amount_positive_amount_raw_preserved(self):
        # A refund/reversal posts negative `amount` against a revenue
        # account. Per spec section F: amount_raw is preserved exactly
        # as stored; normalized_amount is always positive.
        refund_amount = -6181.25
        rows = [
            _gl_row(
                tx_date="2027-05-15",
                amount=refund_amount,
                account_name="50101 Professional Services Income",
                doc_num="INV-RFND-1",
                txn_type="Journal Entry",
            ),
        ]

        result = _call_tool(
            rows,
            date_from="2027-05-01",
            date_to="2027-05-31",
        )

        assert result["row_count"] == 1
        row = result["rows"][0]
        # amount_raw: preserved as stored (negative).
        assert row["amount_raw"] == refund_amount  # FIXTURE-DERIVED literal
        # normalized_amount: always positive per spec.
        assert row["normalized_amount"] == abs(refund_amount)
        assert row["normalized_amount"] > 0
        # User-facing total is positive too — even though raw sum is negative.
        assert result["total_income"] == abs(refund_amount)
        assert result["total_income"] > 0
        # Notes warn about net-negative raw activity so the LLM mentions it.
        assert result["notes"] is not None
        assert "negative" in result["notes"].lower()


class TestLimitClamp:
    """Test 5: limit=900 → effective 500 + notes warning, no error."""

    def test_limit_900_clamps_to_500_with_notes(self):
        FIXTURE_COUNT = 600
        rows = [
            _gl_row(
                tx_date=f"2027-06-{(i % 28) + 1:02d}",
                amount=42.0,
                doc_num=f"INV-{i:05d}",
            )
            for i in range(FIXTURE_COUNT)
        ]
        # FIXTURE-DERIVED expected total.
        expected_total = sum(r["amount"] for r in rows)

        result = _call_tool(
            rows,
            date_from="2027-06-01",
            date_to="2027-06-30",
            limit=900,
        )

        # No validation error.
        assert "error" not in result
        # Effective preview is clamped to 500 even though the fixture has 600.
        assert result["row_count"] == FIXTURE_COUNT
        assert result["rows_returned"] == 500
        assert len(result["rows"]) == 500
        assert result["preview_truncated"] is True
        # Notes carries both the clamp warning AND the truncation note.
        assert result["notes"] is not None
        assert "500" in result["notes"]
        assert "900" in result["notes"]
        # Total still reflects ALL matching rows.
        assert result["total_income"] == pytest.approx(expected_total, abs=0.01)


class TestBlankLocationAvailable:
    """Test 6: schema carries location_name. Mix of blank + populated rows.
    location_blank=true returns only the blank ones."""

    def test_only_blank_location_rows_returned(self):
        rows = [
            # Blank-location rows — location_name is None, "", whitespace.
            _gl_row(tx_date="2027-07-02", amount=1000.00, doc_num="A1",
                    location_name=None),
            _gl_row(tx_date="2027-07-08", amount=2000.00, doc_num="A2",
                    location_name=""),
            _gl_row(tx_date="2027-07-15", amount=3000.00, doc_num="A3",
                    location_name="   "),
            # Populated-location rows — must NOT come back when
            # location_blank=true.
            _gl_row(tx_date="2027-07-20", amount=9999.00, doc_num="B1",
                    location_name="Headquarters"),
            _gl_row(tx_date="2027-07-25", amount=7777.00, doc_num="B2",
                    location_name="Branch Office"),
        ]
        # FIXTURE-DERIVED: sum across blank-location revenue rows only.
        expected_blank_total = sum(
            r["amount"] for r in rows
            if r["location_name"] is None
            or (isinstance(r["location_name"], str) and r["location_name"].strip() == "")
        )

        result = _call_tool(
            rows,
            date_from="2027-07-01",
            date_to="2027-07-31",
            location_blank=True,
        )

        assert result["location_field_status"] == "available"
        # Three blank rows, two populated excluded.
        assert result["row_count"] == 3
        assert result["total_income"] == pytest.approx(expected_blank_total, abs=0.01)
        # No populated-location row leaked through.
        assert all(
            r["location"] is None or
            (isinstance(r["location"], str) and r["location"].strip() == "")
            for r in result["rows"]
        )


class TestValueAtPath:
    """Walk into a list-typed segment (raw_data.Line) and pick a per-line
    field on the other side. The recursion needs to advance into each
    line item without losing or double-consuming the segment that
    triggered the list-walk."""

    def test_walks_into_line_list_and_returns_per_line_department(self):
        doc = {
            "raw_data": {
                "Line": [
                    {"DepartmentRef": {"name": "DeptA"}},
                    {"DepartmentRef": {"name": "DeptB"}},
                    {"DepartmentRef": {"name": "DeptC"}},
                ]
            }
        }
        result = _value_at_path(doc, ("raw_data", "Line", "DepartmentRef"))
        assert result == [
            {"name": "DeptA"},
            {"name": "DeptB"},
            {"name": "DeptC"},
        ]

    def test_returns_none_for_missing_intermediate_segment(self):
        # The middle "Line" key is absent — recursion must short-circuit
        # to None rather than raise.
        doc = {"raw_data": {"OtherField": 1}}
        assert _value_at_path(doc, ("raw_data", "Line", "DepartmentRef")) is None

    def test_returns_empty_when_line_list_is_empty(self):
        # Empty list short-circuits to None (treated as "no rows to
        # walk into" by the caller, not as "every row is blank").
        doc = {"raw_data": {"Line": []}}
        assert _value_at_path(doc, ("raw_data", "Line", "DepartmentRef")) is None

    def test_walks_dict_only_path_unchanged(self):
        # Sanity: the dict-only path still returns the leaf value.
        doc = {"raw_data": {"DepartmentRef": {"name": "HQ"}}}
        assert _value_at_path(doc, ("raw_data", "DepartmentRef")) == {"name": "HQ"}


class TestLocationUnavailable:
    """Test 7: no location/department fields anywhere. location_blank=true
    must yield zero rows and an explanatory notes string."""

    def test_unavailable_returns_zero_rows_and_notes(self):
        # include_location_fields=False strips every location-like key
        # from the doc, simulating a tenant whose GL sync hasn't
        # surfaced those fields at all.
        rows = [
            _gl_row(tx_date="2027-08-01", amount=500.00,
                    doc_num="X1", include_location_fields=False),
            _gl_row(tx_date="2027-08-15", amount=750.00,
                    doc_num="X2", include_location_fields=False),
        ]

        result = _call_tool(
            rows,
            date_from="2027-08-01",
            date_to="2027-08-31",
            location_blank=True,
        )

        assert result["location_field_status"] == "unavailable"
        assert result["row_count"] == 0
        assert result["rows_returned"] == 0
        assert result["rows"] == []
        # total_income with no rows is 0 — also positive (vacuously).
        assert result["total_income"] == 0.0
        # Notes carries the "fields are not present" explanation so the
        # LLM passes it through to the user.
        assert result["notes"] is not None
        notes_lower = result["notes"].lower()
        assert "location" in notes_lower or "department" in notes_lower
        # Spec wording: "does not contain ... cannot be distinguished".
        assert "not" in notes_lower


class TestValidationErrors:
    """Test 8: malformed dates, inverted range, unknown basis, limit < 1."""

    def test_invalid_date_from_format(self):
        result = _call_tool(
            [],
            date_from="January twenty-thirty",  # not YYYY-MM-DD
            date_to="2030-01-31",
        )
        assert result.get("error") == "invalid_date_format"
        assert "date_from" in result["detail"]

    def test_invalid_date_to_format(self):
        result = _call_tool(
            [],
            date_from="2030-01-01",
            date_to="2030-13-01",  # invalid calendar month
        )
        assert result.get("error") == "invalid_date_format"
        assert "date_to" in result["detail"]

    def test_date_from_after_date_to(self):
        result = _call_tool(
            [],
            date_from="2027-09-30",
            date_to="2027-09-01",
        )
        assert result.get("error") == "invalid_date_range"

    def test_unknown_basis(self):
        result = _call_tool(
            [],
            date_from="2027-09-01",
            date_to="2027-09-30",
            basis="modified_accrual",  # not accrual or cash
        )
        assert result.get("error") == "invalid_basis"

    def test_limit_less_than_one(self):
        result = _call_tool(
            [],
            date_from="2027-09-01",
            date_to="2027-09-30",
            limit=0,
        )
        assert result.get("error") == "invalid_limit"

    def test_limit_negative(self):
        result = _call_tool(
            [],
            date_from="2027-09-01",
            date_to="2027-09-30",
            limit=-5,
        )
        assert result.get("error") == "invalid_limit"


# ---------------------------------------------------------------------------
# Helper unit tests — _is_blank_value, _is_blank_location
# ---------------------------------------------------------------------------


class TestBlankValueHelpers:
    """Direct unit coverage of the location-field helpers so a regression
    in either rule surfaces at the helper level rather than a higher-up
    integration test."""

    @pytest.mark.parametrize("v,expected", [
        (None, True),
        ("", True),
        ("   ", True),
        ("\t\n  ", True),
        ("HQ", False),
        ("0", False),
        ({"name": None, "id": None, "value": None}, True),
        ({"name": "", "id": "", "value": ""}, True),
        ({"name": "HQ", "id": None, "value": None}, False),
        ({"name": None, "id": "1", "value": None}, False),
        ([], True),  # empty list is vacuously blank
        ([None, "  ", ""], True),
        ([{"name": "HQ"}], False),
    ])
    def test_is_blank_value(self, v, expected):
        assert _is_blank_value(v) is expected

    def test_is_blank_location_top_level_only(self):
        # All location-like top-level fields blank.
        assert _is_blank_location({"location_name": None}) is True
        assert _is_blank_location({"location_name": ""}) is True
        assert _is_blank_location({"location_name": "HQ"}) is False
        # department field populated alone trips the check.
        assert _is_blank_location({"location_name": None, "department_name": "Ops"}) is False


# ---------------------------------------------------------------------------
# Per-field blank-location exclusion (each location-like field on its own
# blocks the row from the location_blank=true result)
# ---------------------------------------------------------------------------


class TestBlankLocationPerFieldExclusion:
    """Each of the four location-like fields on its own marks a row as
    non-blank. A row that pollutes any one of these fields must NOT come
    back when location_blank=true."""

    def _populated_kwargs(self, field: str, value: Any = "POP-1") -> dict:
        # Build a row where every location-like field is null EXCEPT the
        # one being tested, which is populated.
        kwargs: dict[str, Any] = {
            "location_id": None,
            "location_name": None,
            "department_ref_id": None,
            "department_ref_name": None,
        }
        kwargs[field] = value
        return kwargs

    @pytest.mark.parametrize("field", [
        "location_id",
        "location_name",
        "department_ref_id",
        "department_ref_name",
    ])
    def test_populated_field_excludes_row(self, field: str):
        # Two rows in window:
        #  - one with the field-under-test populated  (must be excluded)
        #  - one with every location-like field null  (must come back)
        populated = _gl_row(
            tx_date="2029-04-10",
            amount=1000.00,
            doc_num="POP",
            **self._populated_kwargs(field, value="POP-1"),
        )
        # _gl_row defaults all the populated kwargs back to None, so a
        # second row with no overrides has every location-like field null.
        all_blank = _gl_row(
            tx_date="2029-04-12",
            amount=2500.00,
            doc_num="BLANK",
        )
        rows = [populated, all_blank]
        # FIXTURE-DERIVED: only the all-blank row is expected to come back.
        expected_total = all_blank["amount"]

        result = _call_tool(
            rows,
            date_from="2029-04-01",
            date_to="2029-04-30",
            location_blank=True,
        )

        assert result["location_field_status"] == "available"
        assert result["row_count"] == 1, (
            f"Expected only the all-blank row when {field!r} populates "
            f"the other; got {result['row_count']} rows."
        )
        assert result["rows"][0]["doc_num"] == "BLANK"
        assert result["total_income"] == pytest.approx(expected_total, abs=0.01)


class TestBlankLocationAllFieldsBlank:
    """A row where every location-like field is missing/null/empty/
    whitespace MUST come back when location_blank=true."""

    def test_all_blank_variants_come_back(self):
        # Three rows, one per blank flavour, all four fields blank.
        rows = [
            # Flavour 1: every field null
            _gl_row(tx_date="2029-05-02", amount=100.00, doc_num="N1",
                    location_id=None, location_name=None,
                    department_ref_id=None, department_ref_name=None),
            # Flavour 2: every field empty string
            _gl_row(tx_date="2029-05-09", amount=200.00, doc_num="E1",
                    location_id="", location_name="",
                    department_ref_id="", department_ref_name=""),
            # Flavour 3: every field whitespace-only
            _gl_row(tx_date="2029-05-15", amount=300.00, doc_num="W1",
                    location_id="   ", location_name="\t\n",
                    department_ref_id="  ", department_ref_name=" "),
        ]
        # FIXTURE-DERIVED expected total — every row should be included.
        expected_total = sum(r["amount"] for r in rows)

        result = _call_tool(
            rows,
            date_from="2029-05-01",
            date_to="2029-05-31",
            location_blank=True,
        )

        assert result["location_field_status"] == "available"
        assert result["row_count"] == len(rows)
        assert result["total_income"] == pytest.approx(expected_total, abs=0.01)


# ---------------------------------------------------------------------------
# Heterogeneous schema — location_field_status probe must check the WHOLE
# collection, not just the first row, so a doc that lacks the field
# doesn't shadow the rest of the collection.
# ---------------------------------------------------------------------------


class TestLocationFieldStatusAcrossCollection:
    """If ANY doc carries a location-like field, status must be
    'available' even when the first/sample doc lacks it. The earlier
    single-sample probe was unreliable for heterogeneous syncs."""

    def test_first_doc_lacks_field_but_later_doc_has_it(self):
        # First row: NO location keys at all.
        # Second row: location_name field present (value can be null).
        # The collection-wide $exists probe should still return
        # "available" because at least one doc carries the field.
        rows = [
            _gl_row(tx_date="2029-06-01", amount=100.00, doc_num="N1",
                    include_location_fields=False),
            _gl_row(tx_date="2029-06-15", amount=200.00, doc_num="N2",
                    location_name=None,
                    location_id=None,
                    department_ref_id=None,
                    department_ref_name=None),
        ]

        result = _call_tool(
            rows,
            date_from="2029-06-01",
            date_to="2029-06-30",
        )

        # The collection has at least one doc with location_name; status
        # must be available even though the first doc lacks all location
        # fields. A regression to the single-sample probe would return
        # "unavailable" here.
        assert result["location_field_status"] == "available"


# ---------------------------------------------------------------------------
# Language guards — LLM-facing text in the response must not promise CSV /
# download / link / export. The new code (production + tests) must not
# carry the forbidden tenant / person / year strings.
# ---------------------------------------------------------------------------


_FORBIDDEN_LLM_FACING_WORDS = ("csv", "download", "link", "export")


class TestNoExportLanguageInResponse:
    """No LLM-facing text returned by the tool may mention CSV /
    download / link / export. Covers both the truncation note path and
    the location-unavailable path."""

    def test_truncation_notes_have_no_forbidden_words(self):
        # Force preview_truncated=True with a small fixture + small limit.
        rows = [
            _gl_row(tx_date="2029-07-05", amount=100.0 + i, doc_num=f"D{i:03d}")
            for i in range(20)
        ]
        result = _call_tool(
            rows,
            date_from="2029-07-01",
            date_to="2029-07-31",
            limit=5,
        )
        notes = (result.get("notes") or "").lower()
        for word in _FORBIDDEN_LLM_FACING_WORDS:
            assert word not in notes, (
                f"truncation notes contains forbidden word {word!r}: {notes!r}"
            )

    def test_location_unavailable_notes_have_no_forbidden_words(self):
        rows = [
            _gl_row(tx_date="2029-07-05", amount=42.0, doc_num="X1",
                    include_location_fields=False),
        ]
        result = _call_tool(
            rows,
            date_from="2029-07-01",
            date_to="2029-07-31",
            location_blank=True,
        )
        notes = (result.get("notes") or "").lower()
        for word in _FORBIDDEN_LLM_FACING_WORDS:
            assert word not in notes, (
                f"unavailable notes contains forbidden word {word!r}: {notes!r}"
            )

    def test_clamp_notes_have_no_forbidden_words(self):
        rows = [
            _gl_row(tx_date="2029-07-05", amount=10.0, doc_num=f"C{i:03d}")
            for i in range(5)
        ]
        result = _call_tool(
            rows,
            date_from="2029-07-01",
            date_to="2029-07-31",
            limit=900,  # triggers the clamp warning
        )
        notes = (result.get("notes") or "").lower()
        for word in _FORBIDDEN_LLM_FACING_WORDS:
            assert word not in notes, (
                f"clamp notes contains forbidden word {word!r}: {notes!r}"
            )


class TestNoForbiddenStringsInNewCode:
    """The new tool code, system prompt entry, and this test file must
    not carry the forbidden tenant / person / year strings.

    The system prompt has a meta-instruction ("Do not mention CSV,
    download, link, or export") which legitimately uses those words to
    forbid them — that is allowed because it is a guardrail directive,
    not LLM-output content. The check below targets only the demo
    tenant / person / year strings, which have no such carve-out.
    """

    _BANNED = (
        "SimpliMeta",
        "Asad",
        "Cody",
        "Aidan",
        "Davies",
        "SimpliComply",
        "Trenching",
        "2026",
        "2025",
    )

    def _read(self, *parts: str) -> str:
        from pathlib import Path
        repo_backend = Path(__file__).resolve().parent.parent
        return (repo_backend.joinpath(*parts)).read_text(encoding="utf-8")

    def _query_gl_transactions_section(self, source: str) -> str:
        """Slice the nova_tools.py source down to ONLY the two regions
        owned by this commit: the schema entry and the dispatch branch
        for query_gl_transactions. Pre-existing strings elsewhere in
        the file are out of scope per the spec.

        The schema entry starts at ``"name": "query_gl_transactions"``
        and runs to the closing brace of the schemas list — we anchor
        on the trailing ``    },\n]`` pattern emitted by black-style
        formatting. The dispatch branch is bounded by the elif anchor
        and the next ``elif tool_name ==`` (or the unknown-tool return
        when there's no following branch).
        """
        # ---- Schema region ----
        schema_anchor = '"name": "query_gl_transactions"'
        i = source.find(schema_anchor)
        assert i != -1, "query_gl_transactions schema not found"
        # The schema is the LAST entry in NOVA_TOOL_SCHEMAS, ending
        # with the list's closing ']'. Slice up through that bracket.
        schema_end = source.find("\n]\n", i)
        assert schema_end != -1, "NOVA_TOOL_SCHEMAS closing bracket not found"
        schema_region = source[i:schema_end]

        # ---- Dispatch branch region ----
        dispatch_anchor = 'elif tool_name == "query_gl_transactions":'
        j = source.find(dispatch_anchor)
        assert j != -1, "query_gl_transactions dispatch branch not found"
        # The dispatch branch is also the last branch before the
        # unknown_tool return, so we bound by that. (If a future tool
        # is added below, change to the next 'elif tool_name ==' anchor.)
        unknown_tool = source.find('return {"error": "unknown_tool"', j)
        assert unknown_tool != -1
        dispatch_region = source[j:unknown_tool]

        return schema_region + "\n" + dispatch_region

    def _query_gl_section_in_nova_py(self, source: str) -> str:
        """Slice nova.py to the GL transaction detail section only.
        Anchor is the section heading we added; ends at the next
        section heading."""
        start_anchor = "### GL transaction detail"
        i = source.find(start_anchor)
        assert i != -1, "GL transaction detail section not found in nova.py"
        # Next ### heading marks the end of this section.
        j = source.find("### ", i + len(start_anchor))
        return source[i:j] if j != -1 else source[i:]

    def test_query_gl_transactions_section_in_nova_tools_is_clean(self):
        src = self._read("services", "ai", "agents", "tools", "nova_tools.py")
        section = self._query_gl_transactions_section(src)
        for s in self._BANNED:
            assert s not in section, (
                f"forbidden string {s!r} in new tool code section"
            )

    def test_gl_transaction_section_in_nova_py_is_clean(self):
        src = self._read("services", "ai", "agents", "nova.py")
        section = self._query_gl_section_in_nova_py(src)
        for s in self._BANNED:
            assert s not in section, (
                f"forbidden string {s!r} in nova.py system-prompt section"
            )

    def test_this_test_file_is_clean(self):
        src = self._read("tests", "test_query_gl_transactions.py")
        # Allow this very class's _BANNED tuple — the strings appear
        # there as data, not as code-path content. We strip the tuple
        # before scanning by anchoring on its surrounding markers.
        marker_start = "_BANNED = ("
        marker_end = "    )"
        i = src.find(marker_start)
        assert i != -1
        j = src.find(marker_end, i)
        assert j != -1
        scrubbed = src[:i] + src[j + len(marker_end):]
        for s in self._BANNED:
            assert s not in scrubbed, (
                f"forbidden string {s!r} in test_query_gl_transactions.py"
            )


# ---------------------------------------------------------------------------
# PR #158 review backlog regressions
# ---------------------------------------------------------------------------


def test_scan_cap_is_module_level_constant():
    """Fix 7: _SCAN_CAP must be importable from the module so callers
    and tests can reference one canonical value instead of redefining
    the literal in the dispatcher branch."""
    from services.ai.agents.tools import nova_tools

    assert hasattr(nova_tools, "_SCAN_CAP")
    assert nova_tools._SCAN_CAP == 50_000


def test_scan_truncated_false_at_exactly_scan_cap_rows(monkeypatch):
    """Fix 6: Cursor returning exactly _SCAN_CAP rows must NOT trip
    the truncation flag — the previous `>= _SCAN_CAP` check produced
    a false positive at the boundary. The visible signal is the
    "Mongo scan was capped" sentence in result['notes']: it must
    NOT appear when the row count equals the cap exactly."""
    from services.ai.agents.tools import nova_tools

    # Synthetic cap small enough that the test runs fast.
    monkeypatch.setattr(nova_tools, "_SCAN_CAP", 5)

    rows = [
        _gl_row(
            tx_date="2099-04-15",
            account_name="50101 Synthetic Revenue",
            amount=10.0,
        )
        for _ in range(5)  # exactly _SCAN_CAP rows
    ]
    result = _call_tool(
        rows,
        date_from="2099-04-01",
        date_to="2099-04-30",
        basis="accrual",
    )

    notes = (result.get("notes") or "").lower()
    assert "mongo scan was capped" not in notes
    # And all 5 fixture rows are accounted for in the row_count.
    assert result["row_count"] == 5


def test_scan_truncated_true_above_scan_cap(monkeypatch):
    """Fix 6: When more than _SCAN_CAP rows match, the over-by-one
    read pattern flips the truncation flag and the rows get sliced
    back to _SCAN_CAP. The visible signal is the "Mongo scan was
    capped" sentence in result['notes']."""
    from services.ai.agents.tools import nova_tools

    monkeypatch.setattr(nova_tools, "_SCAN_CAP", 5)

    rows = [
        _gl_row(
            tx_date="2099-04-15",
            account_name="50101 Synthetic Revenue",
            amount=10.0,
        )
        for _ in range(7)  # _SCAN_CAP + 2 rows
    ]
    result = _call_tool(
        rows,
        date_from="2099-04-01",
        date_to="2099-04-30",
        basis="accrual",
    )

    notes = (result.get("notes") or "").lower()
    assert "mongo scan was capped" in notes
    # And the post-slice row_count is exactly _SCAN_CAP — the +1
    # over-read row is sliced off before the rest of the dispatcher
    # iterates.
    assert result["row_count"] == nova_tools._SCAN_CAP


def test_per_row_dict_drops_always_none_account_id_and_class_name():
    """Fix 8: account_id and class_name were always None in the AEGISAXIOM
    GL schema and added noise to every row. They must no longer
    appear as keys in the per-row preview dict."""
    rows = [
        _gl_row(
            tx_date="2099-04-15",
            account_name="50101 Synthetic Revenue",
            amount=100.0,
        ),
    ]
    result = _call_tool(
        rows,
        date_from="2099-04-01",
        date_to="2099-04-30",
        basis="accrual",
    )

    assert result["rows"], "fixture must yield at least one preview row"
    for row in result["rows"]:
        assert "account_id" not in row, (
            "PR #158 Fix 8: account_id was always None and must be "
            "dropped from per-row dicts"
        )
        assert "class_name" not in row, (
            "PR #158 Fix 8: class_name was always None and must be "
            "dropped from per-row dicts"
        )
