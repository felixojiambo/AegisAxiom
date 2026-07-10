"""Smoke tests for the 11 new FCC Nova tools (Wave 2J).

Each test verifies:
  1. The tool is present in NOVA_TOOL_SCHEMAS.
  2. The dispatcher routes to the correct branch (returns data or a
     structured error, never raises).
  3. The unavailable_data path fires correctly when QBO collections are empty.

Tests mock get_qbo_db() so they never hit live data.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai.agents.tools.nova_tools import (
    NOVA_TOOL_SCHEMAS,
    _ScenarioState,
    execute_tool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOOL_NAMES = {s["name"] for s in NOVA_TOOL_SCHEMAS}


def _state() -> _ScenarioState:
    return _ScenarioState()


def _cursor_with(rows: list) -> MagicMock:
    """A MagicMock cursor that supports .find().sort().limit().to_list()."""
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _cursor_empty() -> MagicMock:
    return _cursor_with([])


def _make_qbo_db(**collection_rows: list) -> MagicMock:
    """Build a mock qbo_db where each kwarg name -> collection name -> rows."""
    db = MagicMock()

    def _getitem(name):
        rows = collection_rows.get(name, [])
        col = MagicMock()
        col.find = MagicMock(return_value=_cursor_with(rows))
        col.aggregate = MagicMock(return_value=_cursor_with([]))
        return col

    db.__getitem__ = MagicMock(side_effect=_getitem)

    for name, rows in collection_rows.items():
        col = MagicMock()
        col.find = MagicMock(return_value=_cursor_with(rows))
        setattr(db, name, col)

    return db


async def _call(tool_name: str, args: dict, qbo_db=None) -> dict[str, Any]:
    """Dispatch through execute_tool with a mocked qbo_db."""
    if qbo_db is None:
        qbo_db = _make_qbo_db()
    with patch(
        "services.nova_engine.db.get_qbo_db", return_value=qbo_db
    ):
        return await execute_tool(
            tool_name=tool_name,
            tool_args=args,
            state=_state(),
            user_id="u_test",
            session_id=None,
            message_id=None,
        )


# ---------------------------------------------------------------------------
# Schema presence checks (fail fast if a tool was not registered)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", [
    "get_pnl_summary",
    "get_balance_sheet_summary",
    "get_cash_flow_summary",
    "get_trial_balance_summary",
    "get_ar_aging",
    "get_ap_aging",
    "get_dso_dpo_trend",
    "get_revenue_trend",
    "get_top_customers_by_income",
    "get_top_vendors_by_spend",
    "get_coa_lookup",
])
def test_tool_registered_in_schema(tool_name: str):
    assert tool_name in _TOOL_NAMES, (
        f"{tool_name} not found in NOVA_TOOL_SCHEMAS. "
        "Did you forget to add it to the list?"
    )


# ---------------------------------------------------------------------------
# get_pnl_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pnl_summary_returns_report():
    pnl_doc = {"period": "2026-04", "summary": {"net_income": 12000}}
    db = _make_qbo_db(reports_pnl=[pnl_doc])
    result = await _call("get_pnl_summary", {"period": "current"}, qbo_db=db)
    assert "report" in result
    assert result["period"] == "current"


@pytest.mark.asyncio
async def test_get_pnl_summary_prior_period():
    prior_doc = {"period": "2025-04", "summary": {"net_income": 9000}}
    db = _make_qbo_db(reports_pnl_prior_year=[prior_doc])
    result = await _call("get_pnl_summary", {"period": "prior"}, qbo_db=db)
    assert result["period"] == "prior"
    assert "report" in result


@pytest.mark.asyncio
async def test_get_pnl_summary_empty_returns_unavailable():
    db = _make_qbo_db()
    result = await _call("get_pnl_summary", {}, qbo_db=db)
    assert result["error"] == "unavailable_data"


@pytest.mark.asyncio
async def test_get_pnl_summary_invalid_period():
    result = await _call("get_pnl_summary", {"period": "invalid"})
    assert result["error"] == "invalid_period"


# ---------------------------------------------------------------------------
# get_balance_sheet_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_balance_sheet_summary_returns_report():
    doc = {"total_assets": 500000, "total_liabilities": 200000}
    db = _make_qbo_db(reports_balance_sheet=[doc])
    result = await _call("get_balance_sheet_summary", {}, qbo_db=db)
    assert "report" in result


@pytest.mark.asyncio
async def test_get_balance_sheet_empty_returns_unavailable():
    result = await _call("get_balance_sheet_summary", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# get_cash_flow_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cash_flow_summary_returns_report():
    doc = {"operating_activities": 80000}
    db = _make_qbo_db(reports_cash_flow=[doc])
    result = await _call("get_cash_flow_summary", {}, qbo_db=db)
    assert "report" in result


@pytest.mark.asyncio
async def test_get_cash_flow_empty_returns_unavailable():
    result = await _call("get_cash_flow_summary", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# get_trial_balance_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_trial_balance_summary_returns_report():
    doc = {"total_debits": 1000000, "total_credits": 1000000}
    db = _make_qbo_db(reports_trial_balance=[doc])
    result = await _call("get_trial_balance_summary", {}, qbo_db=db)
    assert "report" in result


@pytest.mark.asyncio
async def test_get_trial_balance_empty_returns_unavailable():
    result = await _call("get_trial_balance_summary", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# get_ar_aging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ar_aging_returns_detail_report():
    doc = {"current": 5000, "1_30": 2000, "31_60": 500}
    db = _make_qbo_db(reports_ar_aging_detail=[doc])
    result = await _call("get_ar_aging", {}, qbo_db=db)
    assert "report" in result


@pytest.mark.asyncio
async def test_get_ar_aging_falls_back_to_summary():
    """Falls back to reports_ar_aging when detail report is empty."""
    summary_doc = {"total_ar": 7500}
    # Detailed collection empty, summary has data.
    # The dispatcher checks detail first, then summary.
    # Since both share the same mock db, we need collections set up appropriately.
    db = MagicMock()
    empty_cursor = _cursor_empty()
    summary_cursor = _cursor_with([summary_doc])

    reports_ar_aging_detail = MagicMock()
    reports_ar_aging_detail.find = MagicMock(return_value=empty_cursor)

    reports_ar_aging = MagicMock()
    reports_ar_aging.find = MagicMock(return_value=summary_cursor)

    db.reports_ar_aging_detail = reports_ar_aging_detail
    db.reports_ar_aging = reports_ar_aging

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            tool_name="get_ar_aging",
            tool_args={},
            state=_state(),
            user_id="u_test",
            session_id=None,
            message_id=None,
        )
    assert "report" in result


@pytest.mark.asyncio
async def test_get_ar_aging_empty_returns_unavailable():
    result = await _call("get_ar_aging", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# get_ap_aging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ap_aging_returns_report():
    doc = {"current": 3000, "1_30": 1000}
    db = _make_qbo_db(reports_ap_aging_detail=[doc])
    result = await _call("get_ap_aging", {}, qbo_db=db)
    assert "report" in result


@pytest.mark.asyncio
async def test_get_ap_aging_empty_returns_unavailable():
    result = await _call("get_ap_aging", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# get_dso_dpo_trend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dso_dpo_trend_returns_snapshots():
    ar_snap = {"period": "2026-04", "total_ar": 50000}
    ap_snap = {"period": "2026-04", "total_ap": 20000}
    db = _make_qbo_db(
        reports_ar_aging_detail=[ar_snap],
        reports_ap_aging_detail=[ap_snap],
    )
    result = await _call("get_dso_dpo_trend", {"months": 6}, qbo_db=db)
    assert "ar_aging_snapshots" in result
    assert "ap_aging_snapshots" in result
    assert result["months_requested"] == 6


@pytest.mark.asyncio
async def test_get_dso_dpo_trend_default_months():
    ar_snap = {"period": "2026-04"}
    db = _make_qbo_db(
        reports_ar_aging_detail=[ar_snap],
        reports_ap_aging_detail=[ar_snap],
    )
    result = await _call("get_dso_dpo_trend", {}, qbo_db=db)
    assert result["months_requested"] == 12


@pytest.mark.asyncio
async def test_get_dso_dpo_trend_clamps_max():
    ar_snap = {"period": "2026-04"}
    db = _make_qbo_db(
        reports_ar_aging_detail=[ar_snap],
        reports_ap_aging_detail=[ar_snap],
    )
    result = await _call("get_dso_dpo_trend", {"months": 999}, qbo_db=db)
    assert result["months_requested"] == 24


@pytest.mark.asyncio
async def test_get_dso_dpo_trend_empty_returns_unavailable():
    result = await _call("get_dso_dpo_trend", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# get_revenue_trend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_revenue_trend_returns_reports():
    pnl_doc = {"period": "2026-04", "summary": {"income": 40000}}
    db = _make_qbo_db(reports_pnl=[pnl_doc])
    result = await _call("get_revenue_trend", {"months": 3}, qbo_db=db)
    assert "monthly_pnl_reports" in result
    assert result["months_requested"] == 3


@pytest.mark.asyncio
async def test_get_revenue_trend_empty_returns_unavailable():
    result = await _call("get_revenue_trend", {})
    assert result["error"] == "unavailable_data"


@pytest.mark.asyncio
async def test_get_revenue_trend_clamps_max():
    pnl_doc = {"period": "2026-04"}
    db = _make_qbo_db(reports_pnl=[pnl_doc])
    result = await _call("get_revenue_trend", {"months": 100}, qbo_db=db)
    assert result["months_requested"] == 24


# ---------------------------------------------------------------------------
# get_top_customers_by_income
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_top_customers_by_income_returns_report():
    doc = {"rows": [{"customer": "Acme", "income": 80000}]}
    db = _make_qbo_db(reports_customer_income=[doc])
    result = await _call("get_top_customers_by_income", {"limit": 10}, qbo_db=db)
    assert "report" in result
    assert result["limit"] == 10


@pytest.mark.asyncio
async def test_get_top_customers_default_limit():
    doc = {"rows": []}
    db = _make_qbo_db(reports_customer_income=[doc])
    result = await _call("get_top_customers_by_income", {}, qbo_db=db)
    assert result["limit"] == 15


@pytest.mark.asyncio
async def test_get_top_customers_clamps_max():
    doc = {"rows": []}
    db = _make_qbo_db(reports_customer_income=[doc])
    result = await _call("get_top_customers_by_income", {"limit": 999}, qbo_db=db)
    assert result["limit"] == 50


@pytest.mark.asyncio
async def test_get_top_customers_empty_returns_unavailable():
    result = await _call("get_top_customers_by_income", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# get_top_vendors_by_spend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_top_vendors_by_spend_returns_report():
    doc = {"rows": [{"vendor": "Supplier", "spend": 25000}]}
    db = _make_qbo_db(reports_vendor_expenses=[doc])
    result = await _call("get_top_vendors_by_spend", {"limit": 5}, qbo_db=db)
    assert "report" in result
    assert result["limit"] == 5


@pytest.mark.asyncio
async def test_get_top_vendors_by_spend_empty_returns_unavailable():
    result = await _call("get_top_vendors_by_spend", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# get_coa_lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_coa_lookup_all_accounts():
    coa_docs = [
        {"qbo_account_id": "a1", "name": "Revenue", "classification": "Revenue"},
        {"qbo_account_id": "a2", "name": "Salary", "classification": "Expense"},
    ]
    db = _make_qbo_db(chart_of_accounts=coa_docs)
    result = await _call("get_coa_lookup", {}, qbo_db=db)
    assert "accounts" in result
    assert result["kind_filter"] is None


@pytest.mark.asyncio
async def test_get_coa_lookup_income_filter():
    coa_docs = [
        {"qbo_account_id": "a1", "name": "Services Revenue", "classification": "Revenue"},
    ]
    db = _make_qbo_db(chart_of_accounts=coa_docs)
    result = await _call("get_coa_lookup", {"kind": "income"}, qbo_db=db)
    assert result["kind_filter"] == "income"
    assert "accounts" in result


@pytest.mark.asyncio
async def test_get_coa_lookup_expense_filter():
    coa_docs = [
        {"qbo_account_id": "a2", "name": "Office Supplies", "classification": "Expense"},
    ]
    db = _make_qbo_db(chart_of_accounts=coa_docs)
    result = await _call("get_coa_lookup", {"kind": "expense"}, qbo_db=db)
    assert result["kind_filter"] == "expense"


@pytest.mark.asyncio
async def test_get_coa_lookup_invalid_kind():
    result = await _call("get_coa_lookup", {"kind": "liabilities"})
    assert result["error"] == "invalid_kind"


@pytest.mark.asyncio
async def test_get_coa_lookup_empty_returns_unavailable():
    result = await _call("get_coa_lookup", {})
    assert result["error"] == "unavailable_data"


# ---------------------------------------------------------------------------
# Unknown tool still returns structured error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    result = await _call("nonexistent_fcc_tool", {})
    assert result["error"] == "unknown_tool"
