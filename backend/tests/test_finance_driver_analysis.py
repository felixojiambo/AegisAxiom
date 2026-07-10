"""Tests for analyze_finance_drivers.

The helper turns a single metric + date window into a deterministic
driver / variance breakdown so the LLM can narrate WHY a number moved
without inventing arithmetic. All expected values are derived from
synthetic fixture rows in-test (no production dates, customers,
vendors, account numbers, or totals).

The fake collection below implements the subset of the Mongo
aggregation surface the helper actually emits — a single $match +
$project pipeline whose output the helper consumes as a flat list
of {tx_date, account_name, amount} dicts. Anything else raises so a
future helper change that emits an unhandled stage surfaces here
instead of silently returning empty.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")

import pytest

from services.nova_engine.finance_driver_analysis import analyze_finance_drivers


# ---------------------------------------------------------------------------
# In-memory Mongo-ish fake (only what the helper exercises)
# ---------------------------------------------------------------------------


def _matches(doc: dict, query: dict) -> bool:
    for key, condition in query.items():
        value = doc.get(key)
        if isinstance(condition, dict):
            for op, op_val in condition.items():
                if op == "$gte":
                    if value is None or value < op_val:
                        return False
                elif op == "$lte":
                    if value is None or value > op_val:
                        return False
                else:
                    raise NotImplementedError(f"fake op: {op}")
        else:
            if value != condition:
                return False
    return True


def _eval_expr(expr: Any, doc: dict) -> Any:
    if isinstance(expr, dict) and len(expr) == 1:
        ((op, args),) = expr.items()
        if op == "$ifNull":
            value = doc.get(args[0].lstrip("$")) if isinstance(args[0], str) else None
            return args[1] if value is None else value
        raise NotImplementedError(f"fake expr op: {op}")
    return expr


def _apply_project(doc: dict, projection: dict) -> dict:
    out: dict[str, Any] = {}
    for key, spec in projection.items():
        if key == "_id":
            continue
        if isinstance(spec, dict):
            out[key] = _eval_expr(spec, doc)
        elif spec in (1, True):
            if key in doc:
                out[key] = doc[key]
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

    def aggregate(self, pipeline: list[dict]) -> _FakeCursor:
        rows = list(self.docs)
        for stage in pipeline:
            if "$match" in stage:
                rows = [d for d in rows if _matches(d, stage["$match"])]
            elif "$project" in stage:
                rows = [_apply_project(d, stage["$project"]) for d in rows]
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
# Synthetic fixture rows — ALL expected values are computed from these
# in the assertions below. No production dates, customers, vendors,
# account numbers, or totals are ever pasted as literals.
# ---------------------------------------------------------------------------


def _row(
    *,
    tx_date: str,
    account_name: str,
    amount: float,
    basis: str = "accrual",
) -> dict:
    return {
        "tx_date": tx_date,
        "account_name": account_name,
        "amount": amount,
        "basis": basis,
    }


def _revenue_fixture() -> list[dict]:
    """Two months of revenue rows across PS and FS revenue categories."""
    return [
        _row(tx_date="2099-04-05", account_name="50101 Professional Services Revenue", amount=100.0),
        _row(tx_date="2099-04-20", account_name="50101 Professional Services Revenue", amount=200.0),
        _row(tx_date="2099-04-25", account_name="50201 Field Services Revenue", amount=80.0),
        _row(tx_date="2099-05-10", account_name="50101 Professional Services Revenue", amount=300.0),
        _row(tx_date="2099-05-15", account_name="50201 Field Services Revenue", amount=120.0),
    ]


def _comparison_revenue_fixture() -> list[dict]:
    """The previous-period window (March 2099) used in some tests."""
    return [
        _row(tx_date="2099-03-05", account_name="50101 Professional Services Revenue", amount=50.0),
        _row(tx_date="2099-03-20", account_name="50201 Field Services Revenue", amount=30.0),
    ]


# ---------------------------------------------------------------------------
# 1-2. Revenue + expense summary current_total derived from fixtures
# ---------------------------------------------------------------------------


def test_revenue_summary_total_is_sum_of_fixture_rows():
    rows = _revenue_fixture()
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
        )
    )

    expected_total = sum(
        r["amount"] for r in rows if "Revenue" in r["account_name"]
    )
    assert result["summary"]["current_total"] == pytest.approx(expected_total)


def test_expense_summary_total_is_sum_of_expense_rows():
    rows = [
        _row(tx_date="2099-06-05", account_name="60302 PS COGS", amount=40.0),
        _row(tx_date="2099-06-15", account_name="60201 FS COGS", amount=25.0),
        _row(tx_date="2099-06-20", account_name="70601 Payroll Expenses", amount=200.0),
        _row(tx_date="2099-06-25", account_name="50101 Professional Services Revenue", amount=999.0),  # not expense
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="expenses",
            date_from="2099-06-01",
            date_to="2099-06-30",
            compare_to=None,
        )
    )

    expected_total = sum(
        r["amount"] for r in rows if "Revenue" not in r["account_name"]
    )
    assert result["summary"]["current_total"] == pytest.approx(expected_total)


# ---------------------------------------------------------------------------
# 3. Previous-period comparison dates are deterministic
# ---------------------------------------------------------------------------


def test_previous_period_snaps_to_previous_calendar_month_for_full_month_current():
    """When the current period is exactly a full calendar month, the
    previous_period comparison snaps to the FULL previous calendar
    month (canonical mode = previous_month). The earlier "30 days
    ending the day before date_from" behavior produced a window that
    didn't line up with any user-recognisable month boundary —
    "April 2099 vs the prior period" reads as "April 2099 vs March
    2099", not "April 2099 vs March 2 → March 31"."""
    db = _FakeDb(general_ledger=[])

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-04-30",
            compare_to="previous_period",
        )
    )

    cmp = result["comparison"]
    assert cmp["compare_to"] == "previous_period"
    # Full April → comparison is full March (1st through 31st).
    assert cmp["comparison_date_from"] == "2099-03-01"
    assert cmp["comparison_date_to"] == "2099-03-31"
    # And the canonical mode label reflects the snap.
    assert result["scope"]["comparison_mode"] == "previous_month"


def test_previous_period_keeps_same_length_rolling_for_non_calendar_window():
    """A 31-day window that doesn't start on the 1st falls back to the
    original same-length rolling semantics; the canonical mode is
    prior_period_same_length, NOT previous_month."""
    db = _FakeDb(general_ledger=[])
    # April 5 → May 5 (31 days, straddles two calendar months).
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-05",
            date_to="2099-05-05",
            compare_to="previous_period",
        )
    )
    cmp = result["comparison"]
    # 31-day current → comparison is the 31 days ending the day
    # before date_from, i.e. March 5 → April 4.
    assert cmp["comparison_date_to"] == "2099-04-04"
    assert cmp["comparison_date_from"] == "2099-03-05"
    assert result["scope"]["comparison_mode"] == "prior_period_same_length"


def test_same_period_prior_year_window_shifts_by_one_year():
    db = _FakeDb(general_ledger=[])

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-04-30",
            compare_to="same_period_prior_year",
        )
    )

    cmp = result["comparison"]
    assert cmp["compare_to"] == "same_period_prior_year"
    assert cmp["comparison_date_from"] == "2098-04-01"
    assert cmp["comparison_date_to"] == "2098-04-30"


# ---------------------------------------------------------------------------
# 4-5. Variance amount + percentage computed by helper
# ---------------------------------------------------------------------------


def test_variance_amount_and_pct_are_computed_by_helper():
    """Caller never pastes the variance — the helper computes it from
    the fixture rows and the comparison window."""
    current_rows = _revenue_fixture()
    comparison_rows = _comparison_revenue_fixture()
    db = _FakeDb(general_ledger=current_rows + comparison_rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to="previous_period",
        )
    )

    expected_current = sum(r["amount"] for r in current_rows)
    expected_comparison = sum(r["amount"] for r in comparison_rows)
    expected_variance = expected_current - expected_comparison
    expected_pct = expected_variance / abs(expected_comparison)

    assert result["summary"]["current_total"] == pytest.approx(expected_current)
    assert result["summary"]["comparison_total"] == pytest.approx(expected_comparison)
    assert result["summary"]["variance_amount"] == pytest.approx(expected_variance)
    assert result["summary"]["variance_pct"] == pytest.approx(expected_pct)


def test_variance_pct_is_null_when_comparison_total_is_zero():
    db = _FakeDb(
        general_ledger=[
            _row(tx_date="2099-04-15", account_name="50101 PS Revenue", amount=100.0),
        ]
    )

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-04-30",
            compare_to="previous_period",
        )
    )

    assert result["summary"]["comparison_total"] == 0.0
    assert result["summary"]["variance_pct"] is None


def test_no_comparison_returns_null_variance_fields():
    db = _FakeDb(general_ledger=_revenue_fixture())

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
        )
    )

    assert result["summary"]["comparison_total"] is None
    assert result["summary"]["variance_amount"] is None
    assert result["summary"]["variance_pct"] is None


# ---------------------------------------------------------------------------
# 6. Top accounts sorted by absolute contribution
# ---------------------------------------------------------------------------


def test_top_accounts_sorted_by_absolute_contribution():
    rows = _revenue_fixture()
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
            limit=10,
        )
    )

    expected_per_account: dict[str, float] = {}
    for r in rows:
        expected_per_account[r["account_name"]] = (
            expected_per_account.get(r["account_name"], 0.0) + r["amount"]
        )
    expected_ranked = sorted(
        expected_per_account.items(), key=lambda kv: abs(kv[1]), reverse=True
    )

    actual_ranked = [
        (entry["account_name"], entry["amount"]) for entry in result["top_accounts"]
    ]
    assert actual_ranked == [(name, pytest.approx(amt)) for name, amt in expected_ranked]


def test_top_accounts_share_pct_sums_to_one_for_uniform_signs():
    rows = _revenue_fixture()
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
        )
    )

    total_share = sum(entry["share_pct"] for entry in result["top_accounts"])
    assert total_share == pytest.approx(1.0)


def test_top_accounts_respects_limit():
    # 7 distinct revenue accounts, all in the same window.
    rows = [
        _row(
            tx_date="2099-04-15",
            account_name=f"50101 PS Revenue Account {i}",
            amount=10.0 + i,
        )
        for i in range(7)
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-04-30",
            compare_to=None,
            limit=3,
        )
    )

    assert len(result["top_accounts"]) == 3


# ---------------------------------------------------------------------------
# 7. Monthly buckets computed from tx_date
# ---------------------------------------------------------------------------


def test_monthly_buckets_match_fixture_months():
    rows = _revenue_fixture()
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
        )
    )

    expected_april = sum(
        r["amount"] for r in rows if r["tx_date"].startswith("2099-04")
    )
    expected_may = sum(
        r["amount"] for r in rows if r["tx_date"].startswith("2099-05")
    )

    monthly = {entry["month"]: entry["amount"] for entry in result["monthly"]}
    assert monthly["2099-04"] == pytest.approx(expected_april)
    assert monthly["2099-05"] == pytest.approx(expected_may)
    # And the months are returned sorted.
    assert [entry["month"] for entry in result["monthly"]] == sorted(monthly.keys())


def test_monthly_buckets_carry_comparison_amounts_when_present():
    current = _revenue_fixture()
    comparison = _comparison_revenue_fixture()
    db = _FakeDb(general_ledger=current + comparison)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to="previous_period",
        )
    )

    # Every monthly entry must have a comparison_amount field set
    # (None where no rows fell into the comparison window).
    for entry in result["monthly"]:
        assert "comparison_amount" in entry
        if entry["comparison_amount"] is not None:
            assert "variance_amount" in entry
            assert entry["variance_amount"] == pytest.approx(
                entry["amount"] - entry["comparison_amount"]
            )


# ---------------------------------------------------------------------------
# 8. Missing customer/vendor fields returns warning
# ---------------------------------------------------------------------------


def test_top_customers_and_vendors_return_empty_with_warning():
    """GL row schema does not carry per-row customer/vendor; the helper
    must NEVER invent names — it returns empty lists with a warning
    routing the LLM to get_top_customers_by_income / get_top_vendors_by_spend."""
    rows = _revenue_fixture()
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
        )
    )

    assert result["top_customers"] == []
    assert result["top_vendors"] == []
    warnings_text = " ".join(result["warnings"]).lower()
    assert "get_top_customers_by_income" in warnings_text
    assert "get_top_vendors_by_spend" in warnings_text


# ---------------------------------------------------------------------------
# Gross-profit metric uses canonical formula coverage
# ---------------------------------------------------------------------------


def test_gross_profit_metric_uses_canonical_formula():
    """Canonical company-level GP = Total Revenue (PS+FS+MRR+WHOLESALE+
    OTHER) - Total COGS (PS+FS+OTHER COGS). The driver helper must not
    drop OTHER_COGS — that was the bug fixed in
    services/nova_engine/formulas.py."""
    rows = [
        _row(tx_date="2099-06-05", account_name="50101 PS Revenue", amount=1000.0),
        _row(tx_date="2099-06-10", account_name="50201 FS Revenue", amount=600.0),
        _row(tx_date="2099-06-15", account_name="60302 PS COGS", amount=200.0),
        _row(tx_date="2099-06-20", account_name="60201 FS COGS", amount=100.0),
        _row(tx_date="2099-06-25", account_name="Generic Cost of Goods", amount=50.0),
    ]
    db = _FakeDb(general_ledger=rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="gross_profit",
            date_from="2099-06-01",
            date_to="2099-06-30",
            compare_to=None,
        )
    )

    expected_revenue = sum(
        r["amount"] for r in rows if "Revenue" in r["account_name"]
    )
    expected_cogs = sum(
        r["amount"] for r in rows if "COGS" in r["account_name"] or "Cost of Goods" in r["account_name"]
    )
    expected_gp = expected_revenue - expected_cogs

    assert result["summary"]["current_total"] == pytest.approx(expected_gp)


# ---------------------------------------------------------------------------
# Unsupported metrics return routing hint, not invented numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metric", ["cash", "ar", "ap"])
def test_cash_ar_ap_return_routing_hint_without_querying(metric):
    db = _FakeDb(general_ledger=[])

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric=metric,
            date_from="2099-06-01",
            date_to="2099-06-30",
        )
    )

    assert result["error"] == "metric_not_supported_in_driver_analysis"
    assert "summary" not in result  # no invented numbers
    detail = result["detail"].lower()
    if metric == "cash":
        assert "get_finance_data_status" in detail or "run_projection" in detail
    elif metric == "ar":
        assert "get_ar_aging" in detail or "get_open_invoices" in detail
    elif metric == "ap":
        assert "get_ap_aging" in detail or "get_open_bills" in detail


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_unknown_metric_returns_structured_error():
    db = _FakeDb(general_ledger=[])
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="ebitda",
            date_from="2099-06-01",
            date_to="2099-06-30",
        )
    )
    assert result["error"] == "unknown_metric"


def test_invalid_date_range_returns_error():
    db = _FakeDb(general_ledger=[])
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-06-30",
            date_to="2099-06-01",  # before date_from
        )
    )
    assert result["error"] == "invalid_date_range"


def test_missing_date_returns_error():
    db = _FakeDb(general_ledger=[])
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="not-a-date",
            date_to="2099-06-30",
        )
    )
    assert result["error"] == "invalid_date_range"


# ---------------------------------------------------------------------------
# Drivers list contains period_variance entry when comparison present
# ---------------------------------------------------------------------------


def test_drivers_includes_period_variance_when_comparison_present():
    db = _FakeDb(
        general_ledger=_revenue_fixture() + _comparison_revenue_fixture()
    )
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to="previous_period",
        )
    )

    labels = [d["label"] for d in result["drivers"]]
    assert "period_variance" in labels


def test_drivers_contains_account_explanations_grounded_in_share_pct():
    db = _FakeDb(general_ledger=_revenue_fixture())
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
        )
    )

    # At least one driver should be an account-name explanation that
    # mentions the share_pct it computed.
    explanations = [d["explanation"] for d in result["drivers"] if d["label"] != "period_variance"]
    assert explanations
    assert any("%" in e for e in explanations)


# ---------------------------------------------------------------------------
# Tool-level integration via the dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_returns_driver_payload(monkeypatch):
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool

    db = _FakeDb(general_ledger=_revenue_fixture())
    monkeypatch.setattr("services.nova_engine.db.get_qbo_db", lambda: db)

    result = await execute_tool(
        "explain_finance_drivers",
        {
            "metric": "revenue",
            "date_from": "2099-04-01",
            "date_to": "2099-05-31",
            "compare_to": None,
        },
        _ScenarioState(),
        "user_x",
        None,
        None,
    )

    assert "summary" in result
    assert result["summary"]["current_total"] > 0
    assert isinstance(result["monthly"], list)
    assert isinstance(result["top_accounts"], list)


@pytest.mark.asyncio
async def test_dispatcher_rejects_invalid_date():
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool

    result = await execute_tool(
        "explain_finance_drivers",
        {
            "metric": "revenue",
            "date_from": "2099/04/01",
            "date_to": "2099-05-31",
        },
        _ScenarioState(),
        "user_x",
        None,
        None,
    )

    assert result.get("error") == "invalid_date_from"


@pytest.mark.asyncio
async def test_dispatcher_rejects_invalid_basis():
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool

    result = await execute_tool(
        "explain_finance_drivers",
        {
            "metric": "revenue",
            "date_from": "2099-04-01",
            "date_to": "2099-05-31",
            "basis": "trust_me",
        },
        _ScenarioState(),
        "user_x",
        None,
        None,
    )

    assert result.get("error") == "invalid_basis"


# ---------------------------------------------------------------------------
# Canonical comparison metadata, period labeling, and variance wording
# ---------------------------------------------------------------------------
#
# The audited bug: Nova was labeling the immediately-prior same-length
# range as "April 2026" when it was actually e.g. April 5 → May 5. The
# fix introduces a deterministic comparison_mode + label the LLM is
# required to quote verbatim. All expected dates / totals below are
# computed from synthetic fixture variables in-test — no production
# dates / customer names / totals.


def _calendar_month_revenue_fixture(year: int, month: int) -> tuple[list[dict], str, str]:
    """Synthetic GL revenue rows that fall inside ``year-month``.
    Returns (rows, first_day_iso, last_day_iso). Used to build a
    full-calendar-month current period."""
    from datetime import date as _date, timedelta as _td

    first = _date(year, month, 1)
    if month == 12:
        last = _date(year, 12, 31)
    else:
        last = _date(year, month + 1, 1) - _td(days=1)
    rows = [
        _row(
            tx_date=(first + _td(days=2)).isoformat(),
            account_name="50101 Professional Services Revenue",
            amount=200.0,
        ),
        _row(
            tx_date=(first + _td(days=15)).isoformat(),
            account_name="50201 Field Services Revenue",
            amount=150.0,
        ),
    ]
    return rows, first.isoformat(), last.isoformat()


def _previous_calendar_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


# ---------------------------------------------------------------------------
# Comparison-mode tests
# ---------------------------------------------------------------------------


def test_previous_month_mode_when_window_exactly_matches_prior_calendar_month():
    """When current period is exactly a calendar month and the
    auto-resolved comparison range exactly equals the previous
    calendar month, the canonical mode is `previous_month`."""
    cur_year, cur_month = 2099, 6
    prev_year, prev_month = _previous_calendar_month(cur_year, cur_month)
    cur_rows, cur_from, cur_to = _calendar_month_revenue_fixture(cur_year, cur_month)
    prev_rows, prev_from, prev_to = _calendar_month_revenue_fixture(prev_year, prev_month)
    db = _FakeDb(general_ledger=cur_rows + prev_rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from=cur_from,
            date_to=cur_to,
            compare_to="previous_period",
        )
    )

    scope = result["scope"]
    assert scope["comparison_mode"] == "previous_month"
    assert "previous calendar month" in scope["comparison_label"].lower()
    assert scope["current_period_start"] == cur_from
    assert scope["current_period_end"] == cur_to
    assert scope["comparison_period_start"] == prev_from
    assert scope["comparison_period_end"] == prev_to


def test_prior_period_same_length_when_window_does_not_match_calendar_month():
    """A 31-day window that doesn't start on the 1st must NOT be
    relabeled as previous_month — the canonical mode is
    prior_period_same_length and comparison_n_days carries the count."""
    from datetime import date as _date, timedelta as _td

    cur_from = _date(2099, 5, 5)
    cur_to = _date(2099, 6, 5)
    expected_n_days = (cur_to - cur_from).days + 1  # inclusive
    expected_cmp_to = cur_from - _td(days=1)
    expected_cmp_from = expected_cmp_to - _td(days=(cur_to - cur_from).days)
    db = _FakeDb(general_ledger=[])

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from=cur_from.isoformat(),
            date_to=cur_to.isoformat(),
            compare_to="previous_period",
        )
    )

    scope = result["scope"]
    assert scope["comparison_mode"] == "prior_period_same_length"
    assert "prior same-length period" in scope["comparison_label"].lower()
    assert scope["comparison_n_days"] == expected_n_days
    assert scope["comparison_period_start"] == expected_cmp_from.isoformat()
    assert scope["comparison_period_end"] == expected_cmp_to.isoformat()
    # Critical: must NOT be relabeled to previous_month even though it's
    # a 31-day rolling window adjacent to a calendar month.
    assert scope["comparison_mode"] != "previous_month"


def test_same_period_prior_year_mode_label():
    cur_from = "2099-04-01"
    cur_to = "2099-04-30"
    expected_cmp_from = "2098-04-01"
    expected_cmp_to = "2098-04-30"
    db = _FakeDb(general_ledger=[])

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from=cur_from,
            date_to=cur_to,
            compare_to="same_period_prior_year",
        )
    )

    scope = result["scope"]
    assert scope["comparison_mode"] == "same_period_prior_year"
    assert "prior year" in scope["comparison_label"].lower()
    assert scope["comparison_period_start"] == expected_cmp_from
    assert scope["comparison_period_end"] == expected_cmp_to
    # comparison_n_days is None for prior-year mode (the windows
    # already line up by construction, no need to disclose a count).
    assert scope["comparison_n_days"] is None


def test_no_comparison_mode_is_null_when_compare_to_is_none():
    db = _FakeDb(general_ledger=[])
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-04-30",
            compare_to=None,
        )
    )
    scope = result["scope"]
    assert scope["comparison_mode"] is None
    assert scope["comparison_label"] is None
    assert scope["comparison_period_start"] is None
    assert scope["comparison_period_end"] is None


# Custom-range / prior_n_days are not yet supported by the helper.
# These tests pin that the helper either rejects them with a structured
# error OR the planner refuses to send them — Nova must not silently
# fall through to "previous_period" and mislabel the result.


def test_unsupported_compare_to_returns_invalid_compare_to():
    db = _FakeDb(general_ledger=[])
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-04-30",
            compare_to="custom_range",  # not supported in v1
        )
    )
    assert result.get("error") == "invalid_compare_to"
    # Importantly, the response must NOT include a comparison_mode that
    # could be paraphrased as "previous_month" by the LLM.
    assert "scope" not in result


# ---------------------------------------------------------------------------
# Scope metadata — basis, source, included revenue categories
# ---------------------------------------------------------------------------


def test_scope_includes_basis_and_source_collection_for_revenue():
    db = _FakeDb(general_ledger=_revenue_fixture())
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            basis="accrual",
            compare_to=None,
        )
    )

    scope = result["scope"]
    assert scope["metric"] == "revenue"
    assert scope["basis"] == "accrual"
    assert scope["source"] == {
        "collection": "general_ledger",
        "scope": "gl_revenue_categories",
    }
    assert scope["account_filter_label"] == "canonical revenue categories"


def test_scope_includes_canonical_revenue_categories_from_formulas():
    """The included_revenue_categories list must come from the
    canonical REVENUE_CATEGORIES set in
    services.nova_engine.formulas — NOT a hand-maintained list in
    the driver helper."""
    from services.nova_engine.formulas import REVENUE_CATEGORIES

    db = _FakeDb(general_ledger=_revenue_fixture())
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
        )
    )

    assert set(result["scope"]["included_revenue_categories"]) == set(REVENUE_CATEGORIES)
    # PS / FS / MRR / WHOLESALE / OTHER must all show up — no
    # silent omission.
    for required in ("PS_REVENUE", "FS_REVENUE", "MRR", "WHOLESALE_REVENUE", "OTHER_REVENUE"):
        assert required in result["scope"]["included_revenue_categories"]


def test_scope_excluded_categories_present_for_revenue():
    db = _FakeDb(general_ledger=_revenue_fixture())
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to=None,
        )
    )
    # The canonical revenue scope intentionally has no exclusions.
    assert result["scope"]["excluded_categories"] == []


# ---------------------------------------------------------------------------
# Variance — formula correctness without abs()
# ---------------------------------------------------------------------------


def test_variance_amount_is_current_minus_comparison():
    """Pin the canonical formula. Expected derived from fixtures."""
    cur_rows = _revenue_fixture()
    cmp_rows = _comparison_revenue_fixture()
    db = _FakeDb(general_ledger=cur_rows + cmp_rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to="previous_period",
        )
    )

    expected_current = sum(r["amount"] for r in cur_rows)
    expected_comparison = sum(r["amount"] for r in cmp_rows)
    expected_variance = expected_current - expected_comparison

    s = result["summary"]
    assert s["current_period_total"] == pytest.approx(expected_current)
    assert s["comparison_period_total"] == pytest.approx(expected_comparison)
    assert s["variance_amount"] == pytest.approx(expected_variance)


def test_variance_percent_uses_signed_denominator_not_abs():
    """The previous helper used abs(comparison_total) which silently
    flipped the sign of the percent when comparison_total was
    negative (legitimate refund-heavy prior period). The canonical
    formula is variance / comparison_total, no abs."""
    # Synthetic rows where the comparison window has a NET-NEGATIVE
    # revenue total (refunds exceeded revenue). The current window
    # has positive revenue, so variance is positive but comparison is
    # negative — only the bare-denominator formula gives the
    # correctly-signed percent.
    cur_rows = [
        _row(tx_date="2099-04-15", account_name="50101 PS Revenue", amount=100.0),
    ]
    cmp_rows = [
        _row(tx_date="2099-03-05", account_name="50101 PS Revenue", amount=50.0),
        _row(tx_date="2099-03-20", account_name="50101 PS Revenue", amount=-200.0),  # big refund
    ]
    db = _FakeDb(general_ledger=cur_rows + cmp_rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-04-30",
            compare_to="previous_period",
        )
    )

    expected_cmp_total = sum(r["amount"] for r in cmp_rows)
    assert expected_cmp_total < 0  # fixture sanity: comparison is negative
    expected_variance = sum(r["amount"] for r in cur_rows) - expected_cmp_total
    expected_pct = expected_variance / expected_cmp_total  # NO abs

    s = result["summary"]
    assert s["variance_amount"] == pytest.approx(expected_variance)
    assert s["variance_percent"] == pytest.approx(expected_pct)
    # Backward-compat alias agrees.
    assert s["variance_pct"] == pytest.approx(expected_pct)


def test_zero_comparison_total_returns_null_percent_and_na_display():
    """When comparison_total is 0, variance_percent is null AND
    variance_percent_display is 'N/A' so the LLM has one consistent
    string to quote without dividing by zero."""
    cur_rows = [_row(tx_date="2099-04-15", account_name="50101 PS Revenue", amount=100.0)]
    db = _FakeDb(general_ledger=cur_rows)  # no rows in comparison window

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-04-30",
            compare_to="previous_period",
        )
    )

    s = result["summary"]
    assert s["comparison_period_total"] == 0.0
    assert s["variance_percent"] is None
    assert s["variance_percent_display"] == "N/A"


# ---------------------------------------------------------------------------
# Formula block + backward compatibility
# ---------------------------------------------------------------------------


def test_response_carries_formula_block():
    db = _FakeDb(general_ledger=_revenue_fixture())
    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to="previous_period",
        )
    )
    assert result["formula"] == {
        "variance_amount": "current_period_total - comparison_period_total",
        "variance_percent": "variance_amount / comparison_period_total",
    }


def test_summary_keeps_backward_compat_aliases():
    """Existing callers / tests still read current_total /
    comparison_total / variance_pct; the canonical fields shadow
    those keys so nothing breaks."""
    cur_rows = _revenue_fixture()
    cmp_rows = _comparison_revenue_fixture()
    db = _FakeDb(general_ledger=cur_rows + cmp_rows)

    result = asyncio.run(
        analyze_finance_drivers(
            db,
            metric="revenue",
            date_from="2099-04-01",
            date_to="2099-05-31",
            compare_to="previous_period",
        )
    )

    s = result["summary"]
    # Aliases must equal the canonical fields exactly.
    assert s["current_total"] == s["current_period_total"]
    assert s["comparison_total"] == s["comparison_period_total"]
    assert s["variance_pct"] == s["variance_percent"]


# ---------------------------------------------------------------------------
# Prompt / tool-description wording rules
# ---------------------------------------------------------------------------


def test_prompt_says_not_to_relabel_prior_period_as_previous_month():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    text = _SYSTEM_PROMPT.lower()
    # Prompt must explicitly forbid the audited misnomer.
    assert "do not call it \"previous month\"" in text or 'do not call it "previous month"' in text
    # And teach the prior_period_same_length wording the helper emits.
    assert "prior_period_same_length" in _SYSTEM_PROMPT


def test_prompt_says_revenue_categories_must_be_comma_separated_not_code_block():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    text = _SYSTEM_PROMPT.lower()
    assert "comma-separated text" in text
    assert "not as a code block" in text


def test_tool_description_documents_comparison_mode_and_no_relabel_rule():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "explain_finance_drivers")
    description = schema["description"]
    lowered = description.lower()
    # Mode + label must be documented.
    assert "comparison_mode" in description
    assert "comparison_label" in description
    # The audited misnomer must be explicitly forbidden in the schema too.
    assert "do not relabel" in lowered
    assert "previous_month" in description
    # Comma-separated category rule is documented.
    assert "comma-separated" in lowered
    # Variance must come from the helper, not be recomputed.
    assert "never recompute" in lowered or "do not recompute" in lowered
