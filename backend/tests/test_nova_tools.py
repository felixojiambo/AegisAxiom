"""Tests for the Nova chat agent tool dispatcher.

execute_tool is exercised in isolation: build_projection_inputs_from_globals,
project_cash_flow, save_run, get_run are patched. Verifies state mutation,
schema-driven dispatch, and error paths.
"""
from datetime import date, datetime, timezone
from typing import get_args
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai.agents.tools.nova_tools import (
    NOVA_TOOL_SCHEMAS,
    _ScenarioState,
    execute_tool,
)
from services.nova_engine.models import (
    InsufficientHistoryError,
    ManualCostAssumption,
    Override,
    OverrideType,
    OverrideTarget,
    ProjectionInputs,
    ProjectionOutput,
    RecurringRevenueAssumption,
    WeekRow,
)


# ---------------------------------------------------------------------------
# Schema-shape sanity
# ---------------------------------------------------------------------------

def test_seven_tools_exposed():
    # Tool surface keeps growing; the legacy test name stays for blame
    # readability but the assertion tracks the full canonical set. Bump
    # both the count and the names set together when adding a tool — the
    # equality check catches accidental duplicates and renames at the
    # schema level before they reach the LLM.
    assert len(NOVA_TOOL_SCHEMAS) == 34
    names = {t["name"] for t in NOVA_TOOL_SCHEMAS}
    assert names == {
        # Projection / scenario tools
        "run_projection",
        "apply_override",
        "add_manual_assumption",
        "explain_week",
        "reset_scenario_state",
        "compare_runs",
        # Custom GL aggregations
        "query_finance_data",
        # Customer / deal / job analysis
        "analyze_customer_deal_finance",
        "analyze_fs_job_margin",
        "detect_wholesale_revenue",
        # FCC reports & lists
        "get_pnl_summary",
        "get_balance_sheet_summary",
        "get_cash_flow_summary",
        "get_trial_balance_summary",
        "get_ar_aging",
        "get_ap_aging",
        "get_dso_dpo_trend",
        # Single number AP timing answer. Used for average payables
        # time, DPO, days payable outstanding, how long are we taking
        # to pay vendors. The tool picks the best available metric
        # paid bill cycle, DPO proxy, or open AP timing fallback and
        # never returns a refusal.
        "get_payables_timing",
        # QBO payables answer surface backed by synced ap_bills.
        # Distinct from get_payables_timing which runs the older
        # priority-order helper; get_qbo_payables_metrics returns the
        # three structured metrics with formulas, counts, and warnings
        # straight from the lake. The weekly plan + export tools sit
        # next to it so the assistant can fan from a payables question
        # to the spreadsheet without inventing a URL.
        "get_qbo_payables_metrics",
        "get_qbo_weekly_finance_report",
        "export_qbo_weekly_finance_report",
        "get_revenue_trend",
        "get_top_customers_by_income",
        "get_top_vendors_by_spend",
        "get_coa_lookup",
        "get_open_invoices",
        "get_open_bills",
        # Per-row GL transaction detail
        "query_gl_transactions",
        # Issued-invoice aggregate totals (open-invoice tool stays distinct)
        "query_invoice_totals",
        # Read-only finance-data freshness probe (AEGISAXIOM QBO sync state).
        # Nova calls this before quoting time-sensitive totals so the
        # reply can disclose the data age. Nova is NOT allowed to
        # trigger a sync; that path requires finance.sync.trigger and
        # is gated to humans/admins via /api/qbo/sync/trigger.
        "get_finance_data_freshness",
        # Read-only snapshot combining sync freshness with actual
        # finance-collection presence. Distinct from
        # get_finance_data_freshness: lets Nova answer "can you access
        # the finance database?" without confusing a missing
        # qbo_sync_requests record for an empty data lake.
        "get_finance_data_status",
        # Read-only GL location coverage probe. Lets Nova disclose how
        # many rows in scope carry populated location/department fields
        # before quoting location-attributed totals.
        "get_gl_location_coverage",
        # Deterministic driver / variance breakdown for revenue,
        # expenses, COGS, and gross_profit. Compute happens in
        # services/nova_engine/finance_driver_analysis.py — Nova
        # narrates returned rows but never recomputes variance.
        "explain_finance_drivers",
        # Chat-native CSV/XLSX export tool. Persists the file in
        # GridFS and returns artifact_id + download_url so the chat
        # UI renders a download card instead of asking the user to
        # POST to the API endpoint.
        "create_finance_export",
    }


def test_every_schema_has_parameters():
    for schema in NOVA_TOOL_SCHEMAS:
        assert "name" in schema
        assert "description" in schema
        assert "parameters" in schema
        assert schema["parameters"]["type"] == "OBJECT"
        assert "properties" in schema["parameters"]
        assert "required" in schema["parameters"]


def test_apply_override_enum_matches_models():
    """The Gemini schema enum for override type must match the model literal."""
    schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "apply_override")
    enum = schema["parameters"]["properties"]["type"]["enum"]
    assert set(enum) == set(get_args(OverrideType))


# ---------------------------------------------------------------------------
# _ScenarioState
# ---------------------------------------------------------------------------

def test_scenario_state_starts_empty():
    s = _ScenarioState()
    assert s.starting_cash is None
    assert s.projection_start_month is None
    assert s.horizon_weeks == 13
    assert s.overrides == []
    assert s.manual_cost_assumptions == []
    assert s.manual_recurring_revenue == []


def test_scenario_state_round_trip_dict():
    """to_dict/from_dict must round-trip every field used by the tools.

    The conductor persists this between turns on the chat session document
    so manual assumptions and overrides survive across messages.
    """
    s = _ScenarioState()
    s.starting_cash = 75000.0
    s.projection_start_month = "2026-06"
    s.horizon_weeks = 8
    s.sales_modifier = 1.10
    s.cost_modifier = 0.95
    s.last_projection_run_id = "pr_round_trip"
    s.overrides.append(Override(
        type="new_cost", target="PAYROLL", week_number=3, amount=5000.0,
    ))
    s.manual_cost_assumptions.append(ManualCostAssumption(
        name="senior eng hire", amount_per_month=12000.0,
        line_type="PAYROLL", starts_month="2026-06",
    ))
    s.manual_recurring_revenue.append(RecurringRevenueAssumption(
        name="Q3 MRR", amount_per_month=8000.0,
        line_type="MRR", starts_month="2026-07",
    ))

    blob = s.to_dict()
    restored = _ScenarioState.from_dict(blob)

    assert restored.starting_cash == 75000.0
    assert restored.projection_start_month == "2026-06"
    assert restored.horizon_weeks == 8
    assert restored.sales_modifier == 1.10
    assert restored.cost_modifier == 0.95
    assert restored.last_projection_run_id == "pr_round_trip"
    assert len(restored.overrides) == 1
    assert restored.overrides[0].target == "PAYROLL"
    assert restored.overrides[0].amount == 5000.0
    assert len(restored.manual_cost_assumptions) == 1
    assert restored.manual_cost_assumptions[0].line_type == "PAYROLL"
    assert len(restored.manual_recurring_revenue) == 1
    assert restored.manual_recurring_revenue[0].line_type == "MRR"


def test_scenario_state_from_dict_handles_none_and_empty():
    """from_dict(None) and from_dict({}) yield a fresh, empty state."""
    a = _ScenarioState.from_dict(None)
    b = _ScenarioState.from_dict({})
    for s in (a, b):
        assert s.starting_cash is None
        assert s.projection_start_month is None
        assert s.horizon_weeks == 13
        assert s.overrides == []
        assert s.manual_cost_assumptions == []
        assert s.manual_recurring_revenue == []
        assert s.sales_modifier is None
        assert s.cost_modifier is None
        assert s.last_projection_run_id is None


def test_scenario_state_from_dict_drops_malformed_rows():
    """A corrupted persisted assumption must not break the next turn -
    drop the bad row, keep the rest."""
    blob = {
        "manual_cost_assumptions": [
            {"name": "good", "amount_per_month": 1000.0,
             "line_type": "PAYROLL", "starts_month": "2026-05"},
            {"name": "bad", "amount_per_month": "not-a-number",
             "line_type": "BOGUS", "starts_month": "2026-05"},
        ],
    }
    s = _ScenarioState.from_dict(blob)
    assert len(s.manual_cost_assumptions) == 1
    assert s.manual_cost_assumptions[0].name == "good"


def test_scenario_state_to_inputs_kwargs():
    s = _ScenarioState()
    s.starting_cash = 50000.0
    s.projection_start_month = "2026-05"
    s.horizon_weeks = 8
    kwargs = s.to_inputs_kwargs()
    assert kwargs == {
        "starting_cash": 50000.0,
        "projection_start_month": "2026-05",
        "horizon_weeks": 8,
        "overrides": [],
        "manual_cost_assumptions": [],
        "manual_recurring_revenue": [],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(**kwargs) -> ProjectionInputs:
    base = dict(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="MRR neutralizer",
                amount_per_month=0.0,
                line_type="MRR",
                starts_month="2026-05",
                ends_month="2026-12",
            ),
        ],
    )
    base.update(kwargs)
    return ProjectionInputs(**base)


def _make_output(inputs_hash: str = "abc") -> ProjectionOutput:
    weeks = [
        WeekRow(
            week_number=i + 1,
            week_starting=date(2026, 5, 1),
            ps_revenue=2500.0,
            fs_revenue=1250.0,
            ending_cash=50000.0 + (i + 1) * 1000.0,
        )
        for i in range(4)
    ]
    return ProjectionOutput(
        weeks=weeks,
        starting_cash=50000.0,
        ending_cash=54000.0,
        minimum_cash=51000.0,
        minimum_cash_week=1,
        total_revenue=15000.0,
        total_costs=11000.0,
        net_change=4000.0,
        inputs_hash=inputs_hash,
        computed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# run_projection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_projection_happy_path():
    state = _ScenarioState()
    inputs = _make_inputs()
    output = _make_output(inputs_hash="hash_run_1")

    saved_run = {
        "projection_run_id": "pr_abc123",
        "user_id": "user_1",
    }

    with patch(
        "services.nova_engine.data_loader.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=inputs),
    ), patch(
        "services.nova_engine.engine.project_cash_flow",
        return_value=output,
    ), patch(
        "services.nova_engine.persistence.save_run",
        new=AsyncMock(return_value=saved_run),
    ):
        result = await execute_tool(
            "run_projection",
            {"starting_cash": 50000.0, "projection_start_month": "2026-05"},
            state,
            user_id="user_1",
            session_id="sess_1",
            message_id="msg_1",
        )

    assert result["projection_run_id"] == "pr_abc123"
    assert result["inputs_hash"] == "hash_run_1"
    assert result["summary"]["ending_cash"] == 54000.0
    assert result["summary"]["minimum_cash_week"] == 1
    assert len(result["weeks"]) == 4
    # State was mutated by the tool
    assert state.starting_cash == 50000.0
    assert state.projection_start_month == "2026-05"


@pytest.mark.asyncio
async def test_run_projection_uses_horizon_default_13():
    state = _ScenarioState()
    inputs = _make_inputs()
    output = _make_output()

    with patch(
        "services.nova_engine.data_loader.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=inputs),
    ), patch(
        "services.nova_engine.engine.project_cash_flow",
        return_value=output,
    ), patch(
        "services.nova_engine.persistence.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_x"}),
    ):
        await execute_tool(
            "run_projection",
            {"starting_cash": 50000.0, "projection_start_month": "2026-05"},
            state,
            user_id="user_1",
            session_id=None,
            message_id=None,
        )

    assert state.horizon_weeks == 13


@pytest.mark.asyncio
async def test_run_projection_returns_error_on_insufficient_history():
    state = _ScenarioState()

    with patch(
        "services.nova_engine.data_loader.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=InsufficientHistoryError(
            line="history", available_months=2, required_months=3,
        )),
    ):
        result = await execute_tool(
            "run_projection",
            {"starting_cash": 50000.0, "projection_start_month": "2026-05"},
            state,
            user_id="user_1",
            session_id=None,
            message_id=None,
        )

    assert result["error"] == "insufficient_history"
    assert "history" in result["detail"].lower() or "month" in result["detail"].lower()


# ---------------------------------------------------------------------------
# apply_override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_override_appends_to_state():
    state = _ScenarioState()
    result = await execute_tool(
        "apply_override",
        {
            "type": "new_cost",
            "target": "PAYROLL",
            "week_number": 3,
            "amount": 5000.0,
        },
        state,
        user_id="user_1",
        session_id=None,
        message_id=None,
    )

    assert result == {"status": "override_added", "override_count": 1}
    assert len(state.overrides) == 1
    assert state.overrides[0].type == "new_cost"
    assert state.overrides[0].target == "PAYROLL"
    assert state.overrides[0].amount == 5000.0


@pytest.mark.asyncio
async def test_apply_override_shift_payment():
    state = _ScenarioState()
    await execute_tool(
        "apply_override",
        {
            "type": "shift_payment",
            "target": "ADMINISTRATIVE_EXPENSES",
            "source_week": 4,
            "shift_weeks": 2,
        },
        state,
        user_id="user_1",
        session_id=None,
        message_id=None,
    )

    assert state.overrides[0].type == "shift_payment"
    assert state.overrides[0].shift_weeks == 2


@pytest.mark.asyncio
async def test_apply_override_count_increments():
    state = _ScenarioState()
    args = {"type": "new_cost", "target": "OTHER_COSTS", "week_number": 1, "amount": 100.0}
    r1 = await execute_tool("apply_override", args, state, "user_1", None, None)
    r2 = await execute_tool("apply_override", args, state, "user_1", None, None)
    assert r1["override_count"] == 1
    assert r2["override_count"] == 2
    assert len(state.overrides) == 2


# ---------------------------------------------------------------------------
# add_manual_assumption
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_manual_assumption_cost():
    state = _ScenarioState()
    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "cost",
            "name": "senior eng hire",
            "amount_per_month": 12000.0,
            "line_type": "PAYROLL",
            "starts_month": "2026-06",
            "ends_month": None,
        },
        state,
        user_id="user_1",
        session_id=None,
        message_id=None,
    )

    # Assertion is now subset-only because the dispatcher also surfaces
    # the resolved scenario timing (timing_summary,
    # recognition_start_month, cash_receipt_timing,
    # cash_receipt_start_month, warnings). cash_receipt_timing defaults
    # to same_month when the caller doesn't specify it, so the engine
    # math is identical to the pre-timing behavior this test pinned.
    assert result["status"] == "assumption_added"
    assert result["kind"] == "cost"
    assert result["recognition_start_month"] == "2026-06"
    assert result["cash_receipt_timing"] == "same_month"
    assert result["cash_receipt_start_month"] == "2026-06"
    assert len(state.manual_cost_assumptions) == 1
    assert isinstance(state.manual_cost_assumptions[0], ManualCostAssumption)
    assert state.manual_cost_assumptions[0].amount_per_month == 12000.0
    assert state.manual_cost_assumptions[0].line_type == "PAYROLL"
    assert state.manual_cost_assumptions[0].cash_receipt_timing == "same_month"
    assert state.manual_recurring_revenue == []


@pytest.mark.asyncio
async def test_add_manual_assumption_revenue():
    state = _ScenarioState()
    await execute_tool(
        "add_manual_assumption",
        {
            "kind": "revenue",
            "name": "Q3 MRR pipeline",
            "amount_per_month": 8000.0,
            "line_type": "MRR",
            "starts_month": "2026-07",
            "ends_month": "2026-12",
        },
        state,
        user_id="user_1",
        session_id=None,
        message_id=None,
    )

    assert len(state.manual_recurring_revenue) == 1
    assert isinstance(state.manual_recurring_revenue[0], RecurringRevenueAssumption)
    assert state.manual_recurring_revenue[0].line_type == "MRR"
    assert state.manual_cost_assumptions == []


# ---------------------------------------------------------------------------
# explain_week
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explain_week_returns_breakdown():
    state = _ScenarioState()
    fake_run = {
        "projection_run_id": "pr_xyz",
        "user_id": "user_1",
        "inputs": {
            "starting_cash": 50000.0,
            "overrides": [
                {"type": "new_cost", "target": "PAYROLL", "week_number": 2, "amount": 5000.0}
            ],
            "manual_cost_assumptions": [],
            "manual_recurring_revenue": [],
        },
        "output": {
            "weeks": [
                {"week_number": 1, "week_starting": "2026-05-01", "ps_revenue": 2500.0,
                 "payroll": 0.0, "ending_cash": 51000.0},
                {"week_number": 2, "week_starting": "2026-05-08", "ps_revenue": 2500.0,
                 "payroll": 5000.0, "ending_cash": 47500.0},
            ],
        },
    }

    with patch(
        "services.nova_engine.persistence.get_run",
        new=AsyncMock(return_value=fake_run),
    ):
        result = await execute_tool(
            "explain_week",
            {"projection_run_id": "pr_xyz", "week_number": 2},
            state,
            user_id="user_1",
            session_id=None,
            message_id=None,
        )

    assert result["week_number"] == 2
    assert result["ending_cash"] == 47500.0
    assert result["previous_ending_cash"] == 51000.0
    assert result["delta"] == 47500.0 - 51000.0
    assert result["line_breakdown"]["payroll"] == 5000.0
    assert result["line_breakdown"]["ps_revenue"] == 2500.0
    # week_number and ending_cash excluded from line_breakdown
    assert "week_number" not in result["line_breakdown"]
    assert "ending_cash" not in result["line_breakdown"]
    assert len(result["active_overrides"]) == 1
    assert result["active_overrides"][0]["target"] == "PAYROLL"


@pytest.mark.asyncio
async def test_explain_week_first_week_uses_starting_cash():
    state = _ScenarioState()
    fake_run = {
        "projection_run_id": "pr_xyz",
        "user_id": "user_1",
        "inputs": {
            "starting_cash": 50000.0,
            "overrides": [],
            "manual_cost_assumptions": [],
            "manual_recurring_revenue": [],
        },
        "output": {
            "weeks": [
                {"week_number": 1, "week_starting": "2026-05-01", "ending_cash": 51000.0},
            ],
        },
    }

    with patch(
        "services.nova_engine.persistence.get_run",
        new=AsyncMock(return_value=fake_run),
    ):
        result = await execute_tool(
            "explain_week",
            {"projection_run_id": "pr_xyz", "week_number": 1},
            state,
            user_id="user_1",
            session_id=None,
            message_id=None,
        )

    # First week's previous_ending_cash falls back to starting_cash
    assert result["previous_ending_cash"] == 50000.0
    assert result["delta"] == 1000.0


@pytest.mark.asyncio
async def test_explain_week_run_not_found():
    state = _ScenarioState()
    with patch(
        "services.nova_engine.persistence.get_run",
        new=AsyncMock(return_value=None),
    ):
        result = await execute_tool(
            "explain_week",
            {"projection_run_id": "pr_missing", "week_number": 1},
            state,
            user_id="user_1",
            session_id=None,
            message_id=None,
        )

    assert result == {"error": "run_not_found"}


@pytest.mark.asyncio
async def test_explain_week_out_of_range():
    state = _ScenarioState()
    fake_run = {
        "projection_run_id": "pr_xyz",
        "user_id": "user_1",
        "inputs": {"starting_cash": 50000.0, "overrides": [],
                   "manual_cost_assumptions": [], "manual_recurring_revenue": []},
        "output": {"weeks": [{"week_number": 1, "ending_cash": 51000.0}]},
    }

    with patch(
        "services.nova_engine.persistence.get_run",
        new=AsyncMock(return_value=fake_run),
    ):
        result = await execute_tool(
            "explain_week",
            {"projection_run_id": "pr_xyz", "week_number": 99},
            state,
            user_id="user_1",
            session_id=None,
            message_id=None,
        )

    assert result["error"] == "week_out_of_range"
    assert result["valid_range"] == [1, 1]


# ---------------------------------------------------------------------------
# Unknown tool name
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    state = _ScenarioState()
    result = await execute_tool(
        "make_coffee",
        {},
        state,
        user_id="user_1",
        session_id=None,
        message_id=None,
    )
    assert result == {"error": "unknown_tool", "tool_name": "make_coffee"}


# ---------------------------------------------------------------------------
# query_finance_data tests
# ---------------------------------------------------------------------------
#
# Notes on test fixtures vs production GL schema:
# - AEGISAXIOM general_ledger has a single `amount` field (no `credit`/`debit`).
#   Revenue is stored as NEGATIVE amount (credit-balance accounts in QBO);
#   the dispatcher sign-flips in Python after categorize(), matching
#   data_loader.py:load_revenue_history. Test fixtures honor this:
#   revenue rows have amount<0; cost rows have amount>0.
# - Account names use CATEGORY_RULES prefixes (50101 PS_REVENUE,
#   50201 FS_REVENUE, 60302 PS_COGS, 60201 FS_COGS, 70601 PAYROLL).
#   Names that don't match a prefix categorize as UNCATEGORIZED.
# - Each mock row carries a `count` field because the dispatcher's
#   first-stage $group also computes a per-bucket count alongside the
#   summed amount.


def _make_mock_qbo_db_for_aggregation(rows_or_sequence):
    """Return a (qbo_db, collection) MagicMock pair whose
    general_ledger.aggregate(...).to_list(...) resolves to rows.

    If rows_or_sequence is a list of lists, to_list returns each list on
    consecutive aggregate calls (kept for forward-compat; the current
    dispatcher only makes ONE aggregate call per query).
    """
    if rows_or_sequence and isinstance(rows_or_sequence[0], list):
        cursor = MagicMock()
        cursor.to_list = AsyncMock(side_effect=rows_or_sequence)
    else:
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=rows_or_sequence)

    collection = MagicMock()
    collection.aggregate = MagicMock(return_value=cursor)
    db = MagicMock()
    db.general_ledger = collection
    return db, collection


@pytest.mark.asyncio
async def test_query_finance_data_revenue_by_month():
    """Happy path: revenue, group_by=month. Revenue rows are stored with
    negative `amount` in AEGISAXIOM GL; dispatcher sign-flips to positive."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-01", "account_name": "50101 Professional Services Revenue"},
         "amount": 50000.0, "count": 1},
        {"_id": {"bucket": "2026-02", "account_name": "50101 Professional Services Revenue"},
         "amount": 55000.0, "count": 1},
        {"_id": {"bucket": "2026-03", "account_name": "50201 Field Services Revenue"},
         "amount": 30000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert result["metric"] == "revenue"
    assert result["group_by"] == "month"
    assert len(result["rows"]) == 3
    # Sorted ascending by bucket key
    assert result["rows"][0]["bucket"] == "2026-01"
    assert result["rows"][0]["amount"] == 50000.0  # sign-flipped
    assert result["rows"][1]["amount"] == 55000.0
    assert result["rows"][2]["amount"] == 30000.0


@pytest.mark.asyncio
async def test_query_finance_data_default_date_range():
    """When date_from/date_to are not supplied, defaults are 12 months
    ago to today."""
    from datetime import date, timedelta
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month"},
            state, "user_x", None, None,
        )

    today_iso = date.today().isoformat()
    assert result["filters"]["date_to"] == today_iso
    expected_from = (date.today() - timedelta(days=365)).isoformat()
    assert result["filters"]["date_from"] == expected_from


@pytest.mark.asyncio
async def test_query_finance_data_with_category_filter():
    """category filter applies categorize() and excludes non-matching rows."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-01", "account_name": "50101 Professional Services Revenue"},
         "amount": 50000.0, "count": 1},
        {"_id": {"bucket": "2026-01", "account_name": "70601 Payroll Expenses"},
         "amount": 30000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month", "category": "PS_REVENUE"},
            state, "user_x", None, None,
        )

    # Only the PS_REVENUE row survives the filter; payroll is excluded.
    assert len(result["rows"]) == 1
    assert result["rows"][0]["amount"] == 50000.0


@pytest.mark.asyncio
async def test_query_finance_data_account_prefix_in_pipeline():
    """account_prefix builds a regex match in the $match stage."""
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month", "account_prefix": "5022"},
            state, "user_x", None, None,
        )

    pipeline = collection.aggregate.call_args[0][0]
    match_stage = pipeline[0]["$match"]
    assert "account_name" in match_stage
    assert match_stage["account_name"]["$regex"] == "^5022"


@pytest.mark.asyncio
async def test_query_finance_data_group_by_account_category():
    """group_by=account_category re-buckets by category in Python."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-01", "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0, "count": 1},
        {"_id": {"bucket": "2026-01", "account_name": "50201 Field Services Revenue"},
         "amount": 5000.0, "count": 1},
        {"_id": {"bucket": "2026-02", "account_name": "50101 Professional Services Revenue"},
         "amount": 8000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "account_category"},
            state, "user_x", None, None,
        )

    # PS_REVENUE bucket sums across both months: 10000 + 8000 = 18000
    ps_rev_row = next(r for r in result["rows"] if r["bucket"] == "PS_REVENUE")
    assert ps_rev_row["amount"] == 18000.0
    fs_rev_row = next(r for r in result["rows"] if r["bucket"] == "FS_REVENUE")
    assert fs_rev_row["amount"] == 5000.0


@pytest.mark.asyncio
async def test_query_finance_data_group_by_account_name():
    """group_by=account_name returns one row per account."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-01", "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0, "count": 1},
        {"_id": {"bucket": "2026-02", "account_name": "50101 Professional Services Revenue"},
         "amount": 8000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "account_name"},
            state, "user_x", None, None,
        )

    # Both rows aggregate into one bucket (shared account_name).
    assert len(result["rows"]) == 1
    assert result["rows"][0]["bucket"] == "50101 Professional Services Revenue"
    assert result["rows"][0]["amount"] == 18000.0


@pytest.mark.asyncio
async def test_query_finance_data_quarter_bucketing():
    """group_by=quarter uses a $concat quarter-bucket expression."""
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "quarter"},
            state, "user_x", None, None,
        )

    pipeline = collection.aggregate.call_args[0][0]
    project_stage = pipeline[1]["$project"]
    bucket_str = str(project_stage["bucket"])
    assert "-Q" in bucket_str
    assert "$concat" in bucket_str


@pytest.mark.asyncio
async def test_query_finance_data_year_bucketing():
    """group_by=year produces YYYY buckets via $substr 0,4."""
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "year"},
            state, "user_x", None, None,
        )

    pipeline = collection.aggregate.call_args[0][0]
    project_stage = pipeline[1]["$project"]
    assert project_stage["bucket"] == {"$substr": ["$tx_date", 0, 4]}


@pytest.mark.asyncio
async def test_query_finance_data_transaction_count():
    """metric=transaction_count returns the per-bucket row count."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-01", "account_name": "50101 Professional Services Revenue"},
         "amount": 50000.0, "count": 5},
        {"_id": {"bucket": "2026-02", "account_name": "50101 Professional Services Revenue"},
         "amount": 30000.0, "count": 3},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "transaction_count", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert result["metric"] == "transaction_count"
    jan = next(r for r in result["rows"] if r["bucket"] == "2026-01")
    assert jan["amount"] == 5  # transaction count, not dollar amount


@pytest.mark.asyncio
async def test_query_finance_data_gross_profit_margin_zero_revenue_safe():
    """When a bucket has zero revenue, the ratio is 0.0 — no NaN, no exception."""
    state = _ScenarioState()
    # PS COGS only — no revenue rows, so revenue stays at 0.
    rows = [
        {"_id": {"bucket": "2026-01", "account_name": "60302 PS COGS"},
         "amount": 10000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "gross_profit_margin", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert len(result["rows"]) == 1
    assert result["rows"][0]["amount"] == 0.0


@pytest.mark.asyncio
async def test_query_finance_data_limit_caps_at_200():
    """limit > 200 is clamped to 200."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": f"bucket_{i:03d}", "account_name": f"acct_{i}"},
         "amount": 100.0, "count": 1}
        for i in range(250)
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "account_name", "limit": 500},
            state, "user_x", None, None,
        )

    assert len(result["rows"]) <= 200


@pytest.mark.asyncio
async def test_query_finance_data_unknown_metric_returns_error():
    """Defensive: an unknown metric returns a structured error, not an exception."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "DSO", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unknown_metric"
    assert "valid" in result


@pytest.mark.asyncio
async def test_query_finance_data_unknown_group_by_returns_error():
    """Defensive: an unknown group_by returns a structured error."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "customer"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unknown_group_by"
    assert "valid" in result


# ---------------------------------------------------------------------------
# Validation / structured-error tests added in PR review pass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_projection_invalid_month_format_returns_error():
    """projection_start_month must be YYYY-MM. Other formats return error."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "run_projection",
            {"starting_cash": 50000, "projection_start_month": "abcdef"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "invalid_projection_start_month"
    assert "YYYY-MM" in result.get("detail", "")


@pytest.mark.asyncio
async def test_run_projection_invalid_month_value_returns_error():
    """projection_start_month with invalid month (e.g., 2026-13) is rejected."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "run_projection",
            {"starting_cash": 50000, "projection_start_month": "2026-13"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "invalid_projection_start_month"


@pytest.mark.asyncio
async def test_run_projection_malformed_assumption_does_not_mutate_state():
    """PR #144 second-pass review item 2: pre-fix the dispatcher wrote
    starting_cash / projection_start_month / horizon_weeks to state
    BEFORE coercing assumptions. A malformed assumption left those
    scalars + a partially-populated assumption list on state, so the
    next tool call ran against a scenario the user believed they had
    reverted. Lock in atomic mutation: a coercion failure must leave
    every state field unchanged."""
    state = _ScenarioState()
    # Pre-set a known-good baseline so we can detect any half-mutation.
    state.starting_cash = 99_999.0
    state.projection_start_month = "2025-12"
    state.horizon_weeks = 4
    pre_call_cost_count = len(state.manual_cost_assumptions)
    pre_call_revenue_count = len(state.manual_recurring_revenue)
    pre_call_override_count = len(state.overrides)

    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "run_projection",
            {
                "starting_cash": 50000,
                "projection_start_month": "2026-05",
                "horizon_weeks": 13,
                # Malformed: line_type is required and "BOGUS_LINE" is
                # not in the ManualCostAssumption enum, so coercion
                # raises mid-loop.
                "manual_cost_assumptions": [
                    {"name": "ok", "amount_per_month": 1000.0,
                     "line_type": "PAYROLL"},
                    {"name": "bad", "amount_per_month": 2000.0,
                     "line_type": "BOGUS_LINE"},
                ],
            },
            state, "user_x", None, None,
        )

    assert result.get("error") == "invalid_projection_input"
    # State must look exactly as it did before the call.
    assert state.starting_cash == 99_999.0
    assert state.projection_start_month == "2025-12"
    assert state.horizon_weeks == 4
    assert len(state.manual_cost_assumptions) == pre_call_cost_count
    assert len(state.manual_recurring_revenue) == pre_call_revenue_count
    assert len(state.overrides) == pre_call_override_count


@pytest.mark.asyncio
async def test_apply_override_invalid_combination_returns_structured_error():
    """An Override missing required fields (e.g., shift_payment without
    source_week) returns a structured error, not a 500/exception."""
    state = _ScenarioState()

    # shift_payment requires source_week and shift_weeks; we omit both.
    result = await execute_tool(
        "apply_override",
        {"type": "shift_payment", "target": "PAYROLL"},
        state, "user_x", None, None,
    )

    assert result.get("error") == "invalid_override"
    assert "detail" in result
    # State must NOT have been mutated
    assert len(state.overrides) == 0


@pytest.mark.asyncio
async def test_add_manual_assumption_invalid_kind_returns_error():
    """An invalid 'kind' returns a structured error, not silently creating
    a RecurringRevenueAssumption (the previous else-catchall behavior)."""
    state = _ScenarioState()

    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "hello",
            "name": "test",
            "amount_per_month": 1000.0,
            "line_type": "PAYROLL",
            "starts_month": "2026-05",
        },
        state, "user_x", None, None,
    )

    assert result.get("error") == "invalid_kind"
    assert "valid" in result
    # State must NOT have been mutated
    assert len(state.manual_cost_assumptions) == 0
    assert len(state.manual_recurring_revenue) == 0


@pytest.mark.asyncio
async def test_query_finance_data_account_prefix_is_regex_escaped():
    """A malicious account_prefix like '.*' is escaped to literal '\\.\\*'.

    Without re.escape, an LLM-supplied regex metacharacter could produce
    a full-collection scan or catastrophic-backtracking pattern.
    """
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month", "account_prefix": ".*"},
            state, "user_x", None, None,
        )

    pipeline = collection.aggregate.call_args[0][0]
    match_stage = pipeline[0]["$match"]
    # '.' and '*' must be escaped, not interpreted as regex metacharacters.
    assert match_stage["account_name"]["$regex"] == r"^\.\*"


# ---------------------------------------------------------------------------
# PR review round 2 — basis filter, date validation, parent_run_id chain,
# explain_week active_assumptions filtering, assumption ValidationError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_finance_data_basis_filter_in_pipeline():
    """basis is included in the $match stage. Default 'accrual'."""
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month"},
            state, "user_x", None, None,
        )

    pipeline = collection.aggregate.call_args[0][0]
    match_stage = pipeline[0]["$match"]
    assert match_stage.get("basis") == "accrual"


@pytest.mark.asyncio
async def test_query_finance_data_basis_cash_passes_through():
    """basis='cash' is honored in the $match stage and the response filters."""
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month", "basis": "cash"},
            state, "user_x", None, None,
        )

    pipeline = collection.aggregate.call_args[0][0]
    assert pipeline[0]["$match"]["basis"] == "cash"
    assert result["filters"]["basis"] == "cash"


@pytest.mark.asyncio
async def test_query_finance_data_invalid_basis_returns_error():
    """basis='garbage' returns a structured error."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month", "basis": "garbage"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "invalid_basis"


@pytest.mark.asyncio
async def test_query_finance_data_invalid_date_from_returns_error():
    """Malformed date_from returns a structured error."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month", "date_from": "last quarter"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "invalid_date_from"


@pytest.mark.asyncio
async def test_query_finance_data_invalid_date_to_returns_error():
    """Malformed date_to returns a structured error."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month", "date_to": "2026/05/01"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "invalid_date_to"


@pytest.mark.asyncio
async def test_add_manual_assumption_invalid_line_type_returns_structured_error():
    """An invalid line_type for ManualCostAssumption returns structured
    error, not an unhandled ValidationError 500."""
    state = _ScenarioState()

    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "cost",
            "name": "test",
            "amount_per_month": 1000.0,
            "line_type": "HEADCOUNT",  # not a valid CostLineType
            "starts_month": "2026-05",
        },
        state, "user_x", None, None,
    )

    assert result.get("error") == "invalid_assumption"
    assert "detail" in result
    assert len(state.manual_cost_assumptions) == 0


# ---------------------------------------------------------------------------
# add_manual_assumption — scenario timing wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_manual_assumption_default_cash_timing_is_same_month():
    """Callers that don't pass cash_receipt_timing get the documented
    'same_month' default; tool surfaces it in the response."""
    state = _ScenarioState()

    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "revenue",
            "name": "new MRR",
            "amount_per_month": 8000.0,
            "line_type": "MRR",
            "starts_month": "2099-06",
        },
        state, "user_x", None, None,
    )

    assert result["status"] == "assumption_added"
    assert result["recognition_start_month"] == "2099-06"
    assert result["cash_receipt_timing"] == "same_month"
    assert result["cash_receipt_start_month"] == "2099-06"
    assert "timing_summary" in result
    assert state.manual_recurring_revenue[0].cash_receipt_timing == "same_month"


@pytest.mark.asyncio
async def test_add_manual_assumption_explicit_next_month_shifts_cash():
    """cash_receipt_timing='next_month' surfaces the shifted cash month."""
    state = _ScenarioState()

    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "revenue",
            "name": "AR-backed MRR",
            "amount_per_month": 5000.0,
            "line_type": "MRR",
            "starts_month": "2099-06",
            "cash_receipt_timing": "next_month",
        },
        state, "user_x", None, None,
    )

    assert result["recognition_start_month"] == "2099-06"
    assert result["cash_receipt_timing"] == "next_month"
    assert result["cash_receipt_start_month"] == "2099-07"


@pytest.mark.asyncio
async def test_add_manual_assumption_resolves_starting_next_month_phrase():
    """When the LLM passes a phrase + current_date, the tool resolves
    the recognition month deterministically rather than leaving it to
    the model."""
    state = _ScenarioState()

    # Use a fixed synthetic date so the expected month is derived in
    # the test rather than read from today's clock.
    anchor_date = "2099-06-15"
    expected_recognition = "2099-07"
    expected_cash = "2099-08"

    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "cost",
            "name": "new contractor",
            "amount_per_month": 3000.0,
            "line_type": "PAYROLL",
            "timing_phrase": "starting next month",
            "current_date": anchor_date,
            "cash_receipt_timing": "next_month",
        },
        state, "user_x", None, None,
    )

    assert result["status"] == "assumption_added"
    assert result["recognition_start_month"] == expected_recognition
    assert result["cash_receipt_timing"] == "next_month"
    assert result["cash_receipt_start_month"] == expected_cash


@pytest.mark.asyncio
async def test_add_manual_assumption_phrase_without_anchor_requires_confirmation():
    """No starts_month, a phrase, no current_date, no
    projection_start_month on the scenario state — the tool MUST
    return requires_confirmation rather than guess."""
    state = _ScenarioState()
    state.projection_start_month = None

    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "revenue",
            "name": "ambiguous MRR",
            "amount_per_month": 1000.0,
            "line_type": "MRR",
            "timing_phrase": "starting next month",
            "cash_receipt_timing": "same_month",
        },
        state, "user_x", None, None,
    )

    assert result["error"] == "timing_requires_confirmation"
    assert result["requires_confirmation"] is True
    assert result["confirmation_question"]
    # No assumption may be persisted on a confirmation-required path.
    assert state.manual_recurring_revenue == []


@pytest.mark.asyncio
async def test_add_manual_assumption_specific_date_rejected_at_tool_layer():
    """The model accepts specific_date for forward compatibility, but
    the engine does not yet apply specific-date cash schedules. The
    tool must return a clear error rather than persist the assumption."""
    state = _ScenarioState()

    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "cost",
            "name": "loan payment",
            "amount_per_month": 5000.0,
            "line_type": "OTHER_COSTS",
            "starts_month": "2099-06",
            "cash_receipt_timing": "specific_date",
            # cash_receipt_date is intentionally omitted so the helper
            # asks for confirmation rather than constructing a half-
            # specified assumption.
        },
        state, "user_x", None, None,
    )

    # Either path is correct: the helper asks for confirmation OR the
    # tool surfaces the not-yet-supported error. Both keep the
    # assumption out of state.
    assert result.get("error") in (
        "timing_requires_confirmation",
        "cash_receipt_specific_date_not_supported",
    )
    assert state.manual_cost_assumptions == []


@pytest.mark.asyncio
async def test_add_manual_assumption_explicit_starts_month_overrides_phrase():
    """If both starts_month and timing_phrase are supplied, explicit
    starts_month wins. (The schema description tells the LLM this.)"""
    state = _ScenarioState()

    result = await execute_tool(
        "add_manual_assumption",
        {
            "kind": "cost",
            "name": "explicit wins",
            "amount_per_month": 1000.0,
            "line_type": "PAYROLL",
            "starts_month": "2099-09",  # explicit
            "timing_phrase": "starting next month",
            "current_date": "2099-06-01",  # would resolve to 2099-07 if used
            "cash_receipt_timing": "same_month",
        },
        state, "user_x", None, None,
    )

    assert result["recognition_start_month"] == "2099-09"


@pytest.mark.asyncio
async def test_explain_week_filters_assumptions_to_active_window():
    """active_assumptions excludes assumptions whose [starts_month,
    ends_month] window doesn't cover the requested week's month."""
    state = _ScenarioState()

    fake_run = {
        "projection_run_id": "pr_test",
        "user_id": "user_x",
        "inputs": {
            "manual_cost_assumptions": [
                {"name": "active_now", "amount_per_month": 1000.0,
                 "line_type": "PAYROLL", "starts_month": "2026-04",
                 "ends_month": "2026-06"},
                {"name": "future_only", "amount_per_month": 5000.0,
                 "line_type": "PAYROLL", "starts_month": "2026-08",
                 "ends_month": None},
            ],
            "manual_recurring_revenue": [],
            "overrides": [],
            "starting_cash": 50000,
        },
        "output": {
            "weeks": [
                {"week_number": 1, "week_starting": "2026-05-04",
                 "ending_cash": 48000.0, "payroll": 1000.0},
            ],
        },
    }

    with patch(
        "services.nova_engine.persistence.get_run",
        new=AsyncMock(return_value=fake_run),
    ):
        result = await execute_tool(
            "explain_week",
            {"projection_run_id": "pr_test", "week_number": 1},
            state, "user_x", None, None,
        )

    names = [a.get("name") for a in result.get("active_assumptions", [])]
    assert "active_now" in names
    assert "future_only" not in names


@pytest.mark.asyncio
async def test_run_projection_threads_parent_run_id():
    """Sequential run_projection calls in the same chat turn link via
    parent_run_id, and _ScenarioState tracks the latest run id."""
    state = _ScenarioState()

    fake_inputs = MagicMock()
    fake_output = MagicMock()
    fake_output.weeks = []
    fake_output.inputs_hash = "abc123"
    fake_output.starting_cash = 50000.0
    fake_output.ending_cash = 50000.0
    fake_output.minimum_cash = 50000.0
    fake_output.minimum_cash_week = 1
    fake_output.total_revenue = 0.0
    fake_output.total_costs = 0.0
    fake_output.net_change = 0.0

    call_log = []

    async def _fake_save_run(**kwargs):
        call_log.append(kwargs)
        return {"projection_run_id": f"pr_run_{len(call_log)}"}

    with patch(
        "services.nova_engine.data_loader.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=fake_inputs),
    ), patch(
        "services.nova_engine.engine.project_cash_flow",
        return_value=fake_output,
    ), patch(
        "services.nova_engine.persistence.save_run",
        new=AsyncMock(side_effect=_fake_save_run),
    ):
        # First run — no parent
        await execute_tool(
            "run_projection",
            {"starting_cash": 50000, "projection_start_month": "2026-05"},
            state, "user_x", None, None,
        )
        # Second run — should link to the first via parent_run_id
        await execute_tool(
            "run_projection",
            {"starting_cash": 50000, "projection_start_month": "2026-05"},
            state, "user_x", None, None,
        )

    assert len(call_log) == 2
    assert call_log[0].get("parent_run_id") is None
    assert call_log[1].get("parent_run_id") == "pr_run_1"
    assert state.last_projection_run_id == "pr_run_2"


# ---------------------------------------------------------------------------
# cashflow_chart artifact (Build 3)
# ---------------------------------------------------------------------------

def test_build_cashflow_chart_payload_shape():
    """The helper returns the payload dict the frontend chart consumes."""
    from services.ai.agents.tools.nova_tools import build_cashflow_chart_payload

    fake_run = {
        "projection_run_id": "pr_test123",
        "inputs": {"starting_cash": 50000.0, "horizon_weeks": 13},
        "output": {
            "weeks": [
                {
                    "week_number": 1,
                    "week_starting": "2026-05-01",
                    "ending_cash": 51000.0,
                    "total_revenue": 15000.0,
                    "total_costs": 14000.0,
                },
                {
                    "week_number": 2,
                    "week_starting": "2026-05-08",
                    "ending_cash": 52500.0,
                    "total_revenue": 16000.0,
                    "total_costs": 14500.0,
                },
            ],
        },
    }

    payload = build_cashflow_chart_payload(fake_run)

    assert payload["projection_run_id"] == "pr_test123"
    assert payload["starting_cash"] == 50000.0
    assert payload["horizon_weeks"] == 13
    assert len(payload["weeks"]) == 2
    # Renamed total_revenue -> revenue for frontend consumption
    assert payload["weeks"][0] == {
        "week_number": 1,
        "week_starting": "2026-05-01",
        "ending_cash": 51000.0,
        "revenue": 15000.0,
        "total_costs": 14000.0,
    }
    assert payload["weeks"][1]["revenue"] == 16000.0


def test_build_cashflow_chart_payload_handles_missing_fields():
    """Helper is defensive against partial run docs."""
    from services.ai.agents.tools.nova_tools import build_cashflow_chart_payload

    payload = build_cashflow_chart_payload({})
    assert payload["projection_run_id"] is None
    assert payload["starting_cash"] is None
    assert payload["horizon_weeks"] == 13  # default
    assert payload["weeks"] == []


@pytest.mark.asyncio
async def test_run_projection_emits_artifact_id():
    """run_projection's tool response includes artifact_id and the
    cashflow_chart artifact is persisted to ai_chat_artifacts."""
    state = _ScenarioState()

    fake_inputs = MagicMock()
    fake_output = MagicMock()
    fake_output.weeks = []
    fake_output.inputs_hash = "hash_xyz"
    fake_output.starting_cash = 50000.0
    fake_output.ending_cash = 60000.0
    fake_output.minimum_cash = 50000.0
    fake_output.minimum_cash_week = 1
    fake_output.total_revenue = 0.0
    fake_output.total_costs = 0.0
    fake_output.net_change = 10000.0

    saved_run = {
        "projection_run_id": "pr_chart_test",
        "inputs": {"starting_cash": 50000.0, "horizon_weeks": 13},
        "output": {"weeks": []},
    }

    insert_one_mock = AsyncMock()
    fake_db = MagicMock()
    fake_db.ai_chat_artifacts.insert_one = insert_one_mock

    with patch(
        "services.nova_engine.data_loader.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=fake_inputs),
    ), patch(
        "services.nova_engine.engine.project_cash_flow",
        return_value=fake_output,
    ), patch(
        "services.nova_engine.persistence.save_run",
        new=AsyncMock(return_value=saved_run),
    ), patch(
        "services.nova_engine.db.get_app_db",
        return_value=fake_db,
    ):
        result = await execute_tool(
            "run_projection",
            {"starting_cash": 50000, "projection_start_month": "2026-05"},
            state, "user_x", "sess_1", "msg_1",
        )

    # The tool response carries artifact_id alongside the projection_run_id
    # so the agent can quote both in its reply.
    assert "artifact_id" in result
    assert result["artifact_id"] is not None
    assert result["artifact_id"].startswith("art_")
    assert result["projection_run_id"] == "pr_chart_test"

    # The artifact doc was inserted with the right shape.
    insert_one_mock.assert_awaited_once()
    inserted = insert_one_mock.await_args.args[0]
    assert inserted["artifact_type"] == "cashflow_chart"
    assert inserted["user_id"] == "user_x"
    assert inserted["session_id"] == "sess_1"
    assert inserted["status"] == "delivered"
    assert inserted["payload"]["projection_run_id"] == "pr_chart_test"
    assert inserted["payload"]["starting_cash"] == 50000.0


@pytest.mark.asyncio
async def test_explain_week_run_doc_missing_output_returns_clean_error():
    """A persisted run with no 'output' key (failed save, older schema)
    must return a structured tool error - never raise KeyError -> 500.
    Mirrors PR #143 Blocker 2: dict[] access in explain_week was crashing
    on partial run docs."""
    state = _ScenarioState()

    # Run doc rehydrated from Mongo with the output key entirely absent.
    fake_run = {
        "projection_run_id": "pr_no_output",
        "user_id": "user_x",
        "inputs": {"starting_cash": 50000.0},
    }

    with patch(
        "services.nova_engine.persistence.get_run",
        new=AsyncMock(return_value=fake_run),
    ):
        result = await execute_tool(
            "explain_week",
            {"projection_run_id": "pr_no_output", "week_number": 1},
            state, "user_x", None, None,
        )

    assert result.get("error") == "run_missing_output"
    assert result.get("projection_run_id") == "pr_no_output"
    assert "detail" in result


@pytest.mark.asyncio
async def test_explain_week_run_doc_missing_inputs_does_not_raise():
    """A run doc missing 'inputs' must not raise. starting_cash falls
    back to 0, active_assumptions is empty, active_overrides is empty."""
    state = _ScenarioState()

    fake_run = {
        "projection_run_id": "pr_no_inputs",
        "user_id": "user_x",
        "output": {
            "weeks": [
                {"week_number": 1, "week_starting": "2026-05-04",
                 "ending_cash": 51000.0},
            ],
        },
    }

    with patch(
        "services.nova_engine.persistence.get_run",
        new=AsyncMock(return_value=fake_run),
    ):
        result = await execute_tool(
            "explain_week",
            {"projection_run_id": "pr_no_inputs", "week_number": 1},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["week_number"] == 1
    assert result["ending_cash"] == 51000.0
    # Falls back to 0 when starting_cash is unknown
    assert result["previous_ending_cash"] == 0
    assert result["active_overrides"] == []
    assert result["active_assumptions"] == []


@pytest.mark.asyncio
async def test_run_projection_artifact_failure_does_not_break_run():
    """If artifact persistence raises, the projection response still
    succeeds with artifact_id=None — the projection run is never lost
    because of an audit-style insert failure."""
    state = _ScenarioState()

    fake_inputs = MagicMock()
    fake_output = MagicMock()
    fake_output.weeks = []
    fake_output.inputs_hash = "hash_xyz"
    fake_output.starting_cash = 50000.0
    fake_output.ending_cash = 60000.0
    fake_output.minimum_cash = 50000.0
    fake_output.minimum_cash_week = 1
    fake_output.total_revenue = 0.0
    fake_output.total_costs = 0.0
    fake_output.net_change = 10000.0

    saved_run = {
        "projection_run_id": "pr_no_artifact",
        "inputs": {"starting_cash": 50000.0, "horizon_weeks": 13},
        "output": {"weeks": []},
    }

    fake_db = MagicMock()
    fake_db.ai_chat_artifacts.insert_one = AsyncMock(
        side_effect=RuntimeError("simulated mongo outage"),
    )

    with patch(
        "services.nova_engine.data_loader.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=fake_inputs),
    ), patch(
        "services.nova_engine.engine.project_cash_flow",
        return_value=fake_output,
    ), patch(
        "services.nova_engine.persistence.save_run",
        new=AsyncMock(return_value=saved_run),
    ), patch(
        "services.nova_engine.db.get_app_db",
        return_value=fake_db,
    ):
        result = await execute_tool(
            "run_projection",
            {"starting_cash": 50000, "projection_start_month": "2026-05"},
            state, "user_x", None, None,
        )

    # Run still succeeds; artifact_id is None to signal absence.
    assert result["projection_run_id"] == "pr_no_artifact"
    assert result["artifact_id"] is None


# ---------------------------------------------------------------------------
# reset_scenario_state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_scenario_state_clears_all_fields():
    """reset_scenario_state wipes every mutable field on _ScenarioState.

    Without an explicit reset, the agent and user have no clean way to
    start a fresh scenario once state persists across turns.
    """
    state = _ScenarioState()
    state.starting_cash = 50000.0
    state.projection_start_month = "2026-05"
    state.horizon_weeks = 26
    state.sales_modifier = 1.5
    state.cost_modifier = 0.9
    state.last_projection_run_id = "pr_old"
    state.overrides.append(Override(
        type="new_cost", target="PAYROLL", week_number=1, amount=100.0,
    ))
    state.manual_cost_assumptions.append(ManualCostAssumption(
        name="anything", amount_per_month=1000.0,
        line_type="PAYROLL", starts_month="2026-05",
    ))
    state.manual_recurring_revenue.append(RecurringRevenueAssumption(
        name="anything", amount_per_month=1000.0,
        line_type="MRR", starts_month="2026-05",
    ))

    result = await execute_tool(
        "reset_scenario_state", {}, state, "user_x", None, None,
    )

    assert result == {"status": "scenario_reset"}
    assert state.starting_cash is None
    assert state.projection_start_month is None
    assert state.horizon_weeks == 13
    assert state.overrides == []
    assert state.manual_cost_assumptions == []
    assert state.manual_recurring_revenue == []
    assert state.sales_modifier is None
    assert state.cost_modifier is None
    assert state.last_projection_run_id is None


# ---------------------------------------------------------------------------
# query_finance_data category enum alignment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_finance_data_category_enum_matches_canonical_literal():
    """The schema's ``category`` enum must equal the canonical Category
    Literal from account_categories.py. Without this guard, the LLM kept
    inventing names like RENT/FTE_COSTS/OPERATING_EXPENSES that silently
    matched zero rows."""
    from services.nova_engine.account_categories import Category as _Category
    from typing import get_args

    schema = next(
        t for t in NOVA_TOOL_SCHEMAS if t["name"] == "query_finance_data"
    )
    enum = schema["parameters"]["properties"]["category"]["enum"]
    assert set(enum) == set(get_args(_Category))


@pytest.mark.asyncio
async def test_query_finance_data_invalid_category_returns_structured_error():
    """An LLM-supplied category outside the canonical list (e.g., RENT)
    must produce invalid_category, not a silent no-rows result."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "expense", "group_by": "month", "category": "RENT"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "invalid_category"
    assert "valid" in result
    # The fix points the LLM at the canonical list.
    assert "PS_REVENUE" in result["valid"]


@pytest.mark.asyncio
async def test_query_finance_data_each_canonical_category_filters_correctly():
    """For every category that categorize() can return on a real account
    name, query_finance_data with category=<X> returns only matching rows.

    This covers Blocker 4 directly: each canonical Category must work
    end-to-end through the dispatcher's in-Python filter.
    """
    from services.nova_engine.account_categories import Category as _Category
    from typing import get_args

    # Mirrors CATEGORY_RULES + the heuristic fallbacks in
    # account_categories.categorize(). For BALANCE_SHEET there is no
    # categorize() rule today, so it always returns 0 rows -- still a
    # valid filter that must not error.
    SAMPLES: dict[str, str] = {
        "PS_REVENUE": "50101 Professional Services Revenue",
        "FS_REVENUE": "50201 Field Services Revenue",
        "MRR": "50300 Other Income:Contract Commission Income",
        "WHOLESALE_REVENUE": "50400 Wholesale Revenue",
        "OTHER_REVENUE": "Misc Income",          # heuristic: "INCOME"
        "PS_COGS": "60302 PS COGS",
        "FS_COGS": "60201 FS COGS",
        "OTHER_COGS": "Generic Cost of Goods",   # heuristic: "COST OF GOODS"
        "PAYROLL": "70601 Payroll Expenses",
        "ADMIN_EXPENSES": "70511 Admin Expenses",
        "OTHER_OPEX": "Marketing Expense",       # heuristic: "EXPENSE"
        "UNCATEGORIZED": "9999 Uncategorized",   # no rule, no heuristic
        "BALANCE_SHEET": "1000 Balance Sheet",   # not reachable today
    }
    canonical = set(get_args(_Category))
    assert canonical == set(SAMPLES.keys())

    # PS_REVENUE: filter keeps exactly the PS_REVENUE row; sign-flipped
    # to positive on the revenue side.
    state = _ScenarioState()
    revenue_rows = [
        {"_id": {"bucket": "2026-01", "account_name": "50101 PS Revenue"},
         "amount": 50000.0, "count": 1},
        {"_id": {"bucket": "2026-01", "account_name": "70601 Payroll"},
         "amount": 30000.0, "count": 1},
    ]
    db_rev, _ = _make_mock_qbo_db_for_aggregation(revenue_rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db_rev):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month", "category": "PS_REVENUE"},
            state, "user_x", None, None,
        )
    assert "error" not in result
    assert len(result["rows"]) == 1
    assert result["rows"][0]["amount"] == 50000.0

    # Each remaining category passes the schema enum check and produces
    # a non-error response. Categories whose categorize() output never
    # matches a stored row (BALANCE_SHEET) just yield an empty rows list.
    for cat in canonical - {"PS_REVENUE"}:
        per_cat_rows = [
            {"_id": {"bucket": "2026-01", "account_name": SAMPLES[cat]},
             "amount": 1000.0, "count": 1},
        ]
        db_cat, _ = _make_mock_qbo_db_for_aggregation(per_cat_rows)
        state = _ScenarioState()
        with patch("services.nova_engine.db.get_qbo_db", return_value=db_cat):
            r = await execute_tool(
                "query_finance_data",
                {"metric": "expense", "group_by": "month", "category": cat},
                state, "user_x", None, None,
            )
        assert "error" not in r, f"{cat} unexpectedly errored: {r}"
        assert "rows" in r
        if cat == "BALANCE_SHEET":
            # categorize() never returns BALANCE_SHEET today, so the
            # filter always rejects every row.
            assert r["rows"] == []
        else:
            # Every other canonical category has a sample row that
            # categorize() maps to it.
            assert len(r["rows"]) == 1, f"{cat} produced unexpected rows: {r}"


# ---------------------------------------------------------------------------
# compare_runs (Aidan: scenario-comparison v1)
# ---------------------------------------------------------------------------

def _make_run_doc(
    *,
    run_id: str,
    user_id: str = "user_x",
    starting_cash: float = 50000.0,
    ending_cash: float = 60000.0,
    minimum_cash: float = 48000.0,
    minimum_cash_week: int = 3,
    total_revenue: float = 30000.0,
    total_costs: float = 20000.0,
    net_change: float = 10000.0,
    weeks: list[dict] | None = None,
    inputs_overrides: dict | None = None,
    inputs_hash: str = "h1",
) -> dict:
    """Build a persisted projection_run doc shaped like persistence.save_run
    would write. Defaults to a 4-week run; tests override `weeks` for
    weekly-delta scenarios."""
    if weeks is None:
        weeks = [
            {"week_number": 1, "week_starting": "2026-05-04",
             "ending_cash": 51000.0, "total_revenue": 7500.0, "total_costs": 6500.0},
            {"week_number": 2, "week_starting": "2026-05-11",
             "ending_cash": 52000.0, "total_revenue": 7500.0, "total_costs": 6500.0},
            {"week_number": 3, "week_starting": "2026-05-18",
             "ending_cash": 48000.0, "total_revenue": 7500.0, "total_costs": 6500.0},
            {"week_number": 4, "week_starting": "2026-05-25",
             "ending_cash": 60000.0, "total_revenue": 7500.0, "total_costs": 500.0},
        ]
    inputs = {
        "starting_cash": starting_cash,
        "projection_start_month": "2026-05",
        "horizon_weeks": len(weeks),
    }
    if inputs_overrides:
        inputs.update(inputs_overrides)
    return {
        "projection_run_id": run_id,
        "user_id": user_id,
        "inputs": inputs,
        "inputs_hash": inputs_hash,
        "output": {
            "starting_cash": starting_cash,
            "ending_cash": ending_cash,
            "minimum_cash": minimum_cash,
            "minimum_cash_week": minimum_cash_week,
            "total_revenue": total_revenue,
            "total_costs": total_costs,
            "net_change": net_change,
            "inputs_hash": inputs_hash,
            "weeks": weeks,
        },
    }


def test_compare_runs_schema_present():
    schema = next(
        (s for s in NOVA_TOOL_SCHEMAS if s["name"] == "compare_runs"), None
    )
    assert schema is not None
    props = schema["parameters"]["properties"]
    assert {"base_run_id", "compare_run_id", "latest_count",
            "include_weekly_deltas"} <= set(props.keys())
    # No required params - the agent can call it bare for "compare last two".
    assert schema["parameters"]["required"] == []


@pytest.mark.asyncio
async def test_compare_runs_with_explicit_pair():
    """When both ids are supplied, the dispatcher loads both runs by
    user-scoped get_run and emits KPI deltas + the largest weekly swing."""
    state = _ScenarioState()
    base = _make_run_doc(
        run_id="pr_base", ending_cash=60000.0, minimum_cash=48000.0,
        minimum_cash_week=3, total_revenue=30000.0, total_costs=20000.0,
        net_change=10000.0,
    )
    # compare_run: ending up 5K, costs up 2K, min cash week shifts to W2
    compare_weeks = [
        {"week_number": 1, "week_starting": "2026-05-04", "ending_cash": 51500.0,
         "total_revenue": 8000.0, "total_costs": 6500.0},
        {"week_number": 2, "week_starting": "2026-05-11", "ending_cash": 47000.0,
         "total_revenue": 7500.0, "total_costs": 12000.0},
        {"week_number": 3, "week_starting": "2026-05-18", "ending_cash": 53000.0,
         "total_revenue": 8000.0, "total_costs": 2000.0},
        {"week_number": 4, "week_starting": "2026-05-25", "ending_cash": 65000.0,
         "total_revenue": 8500.0, "total_costs": 0.0},
    ]
    compare = _make_run_doc(
        run_id="pr_compare", ending_cash=65000.0, minimum_cash=47000.0,
        minimum_cash_week=2, total_revenue=32000.0, total_costs=22000.0,
        net_change=15000.0, weeks=compare_weeks,
    )

    async def _fake_get_run(run_id, user_id):
        # Mirrors persistence.get_run: scope-by-user-id is enforced here.
        if user_id != "user_x":
            return None
        if run_id == "pr_base":
            return base
        if run_id == "pr_compare":
            return compare
        return None

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {"base_run_id": "pr_base", "compare_run_id": "pr_compare"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["base_run_id"] == "pr_base"
    assert result["compare_run_id"] == "pr_compare"
    deltas = result["deltas"]
    assert deltas["ending_cash"] == 5000.0
    assert deltas["minimum_cash"] == -1000.0
    assert deltas["total_revenue"] == 2000.0
    assert deltas["total_costs"] == 2000.0
    assert deltas["net_change"] == 5000.0
    # starting_cash is a user input, not a computed cash-flow delta;
    # it lives in `input_changes`, not `deltas`, so Nova does not
    # narrate it next to ending_cash.
    assert "starting_cash" not in deltas
    assert result["input_changes"]["starting_cash"] == 0.0  # both 50K
    assert result["minimum_cash_week"]["base"] == 3
    assert result["minimum_cash_week"]["compare"] == 2
    assert result["minimum_cash_week"]["shifted"] is True

    # Per-run summaries are present so the LLM can quote either side.
    assert result["summaries"]["base"]["projection_run_id"] == "pr_base"
    assert result["summaries"]["compare"]["projection_run_id"] == "pr_compare"

    # Weekly deltas are sorted by absolute swing, capped at top-K.
    weekly = result["weekly_deltas"]
    assert weekly, "expected weekly_deltas"
    assert weekly[0]["week_number"] in (3, 1, 4, 2)
    assert weekly == sorted(weekly, key=lambda r: abs(r["delta"]), reverse=True)

    # Plain-English summary present and references both run IDs.
    assert "summary_text" in result
    assert "pr_base" in result["summary_text"]
    assert "pr_compare" in result["summary_text"]


@pytest.mark.asyncio
async def test_compare_runs_defaults_to_latest_two():
    """No ids supplied -> latest_count=2 -> compare runs[0] vs runs[-1].
    list_runs returns newest-first so this is newest vs second-newest."""
    state = _ScenarioState()
    older = _make_run_doc(
        run_id="pr_older", ending_cash=50000.0, total_revenue=20000.0,
        total_costs=18000.0, net_change=2000.0,
    )
    newer = _make_run_doc(
        run_id="pr_newer", ending_cash=58000.0, total_revenue=25000.0,
        total_costs=17000.0, net_change=8000.0,
    )

    async def _fake_list_runs(user_id, limit=20):
        assert user_id == "user_x"
        # Newest-first matches persistence.list_runs's `.sort('created_at', -1)`.
        return [newer, older][:limit]

    with patch(
        "services.nova_engine.persistence.list_runs", new=AsyncMock(side_effect=_fake_list_runs),
    ):
        result = await execute_tool(
            "compare_runs", {}, state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["base_run_id"] == "pr_older"  # second-newest
    assert result["compare_run_id"] == "pr_newer"  # newest
    assert result["deltas"]["ending_cash"] == 8000.0
    assert result["deltas"]["net_change"] == 6000.0


@pytest.mark.asyncio
async def test_compare_runs_blocks_cross_user_access():
    """get_run already filters by user_id; compare_runs must surface a
    not_found error rather than leaking 'this id exists for someone else.'
    The fake get_run below returns None when user_id mismatches, the
    same way persistence does it - the dispatcher must not treat that
    as 'try again with broader scope.'"""
    state = _ScenarioState()
    other_user_run = _make_run_doc(run_id="pr_other_user", user_id="another_user")

    async def _fake_get_run(run_id, user_id):
        # Simulate persistence: only returns docs owned by user_id.
        if other_user_run["user_id"] == user_id and run_id == "pr_other_user":
            return other_user_run
        return None

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {"base_run_id": "pr_other_user", "compare_run_id": "pr_other_user"},
            state, "user_x", None, None,  # <- different user
        )

    # Either base- or compare- side is fine here; both fire from the same
    # missing-doc condition. The point is we never expose existence.
    assert result.get("error") in ("base_run_not_found", "compare_run_not_found")
    assert "pr_other_user" not in str(result.get("summary_text", ""))


@pytest.mark.asyncio
async def test_compare_runs_insufficient_runs_returns_clear_error():
    """If the user has fewer than two persisted runs, return a
    structured error nudging them to run another projection first."""
    state = _ScenarioState()

    with patch(
        "services.nova_engine.persistence.list_runs",
        new=AsyncMock(return_value=[_make_run_doc(run_id="pr_only")]),
    ):
        result = await execute_tool(
            "compare_runs", {}, state, "user_x", None, None,
        )

    assert result.get("error") == "insufficient_runs"
    assert result.get("available_runs") == 1
    assert "another projection" in result.get("detail", "").lower() or "two" in result.get("detail", "")


@pytest.mark.asyncio
async def test_compare_runs_zero_runs_returns_clear_error():
    """A user with no persisted runs gets the same error path as 1-run."""
    state = _ScenarioState()
    with patch(
        "services.nova_engine.persistence.list_runs",
        new=AsyncMock(return_value=[]),
    ):
        result = await execute_tool(
            "compare_runs", {}, state, "user_x", None, None,
        )

    assert result.get("error") == "insufficient_runs"
    assert result.get("available_runs") == 0


@pytest.mark.asyncio
async def test_compare_runs_rejects_partial_pair():
    """Refuse half-specified pairs - silently auto-resolving the missing
    side would mean the same prompt produces different answers depending
    on whether the user typed one id or zero."""
    state = _ScenarioState()
    result = await execute_tool(
        "compare_runs",
        {"base_run_id": "pr_one"},  # compare_run_id missing
        state, "user_x", None, None,
    )
    assert result.get("error") == "incomplete_run_pair"


@pytest.mark.asyncio
async def test_compare_runs_base_not_found_distinguishes_from_compare_not_found():
    """Distinct error codes per missing slot so the agent can ask the
    user to fix the right side. 'Not found' never reveals whether
    another user owns the id."""
    state = _ScenarioState()
    base = _make_run_doc(run_id="pr_base")

    async def _fake_get_run(run_id, user_id):
        if user_id == "user_x" and run_id == "pr_base":
            return base
        return None

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {"base_run_id": "pr_base", "compare_run_id": "pr_missing"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "compare_run_not_found"
    assert result.get("projection_run_id") == "pr_missing"


@pytest.mark.asyncio
async def test_compare_runs_include_weekly_deltas_false_skips_weekly_block():
    """include_weekly_deltas=false returns an empty weekly_deltas list -
    the LLM may want a quick summary without the weekly noise."""
    state = _ScenarioState()
    base = _make_run_doc(run_id="pr_base")
    compare = _make_run_doc(run_id="pr_compare")

    async def _fake_get_run(run_id, user_id):
        return {"pr_base": base, "pr_compare": compare}.get(run_id)

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {
                "base_run_id": "pr_base", "compare_run_id": "pr_compare",
                "include_weekly_deltas": False,
            },
            state, "user_x", None, None,
        )

    assert result["weekly_deltas"] == []
    assert "deltas" in result


@pytest.mark.asyncio
async def test_compare_runs_handles_missing_output_keys_with_zero_default():
    """A run doc with partial output (older schema, failed save) must
    not raise. Missing KPIs default to 0 in the delta computation."""
    state = _ScenarioState()
    base = {
        "projection_run_id": "pr_partial_base", "user_id": "user_x",
        "inputs": {"starting_cash": 50000.0, "projection_start_month": "2026-05"},
        "output": {
            "starting_cash": 50000.0,
            "ending_cash": 50000.0,
            "weeks": [],
        },
    }
    compare = _make_run_doc(run_id="pr_compare", ending_cash=58000.0)

    async def _fake_get_run(run_id, user_id):
        return {"pr_partial_base": base, "pr_compare": compare}.get(run_id)

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {"base_run_id": "pr_partial_base", "compare_run_id": "pr_compare"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["deltas"]["ending_cash"] == 8000.0
    # Missing total_revenue on base treated as 0 -> compare_runs's 30000 is the delta.
    assert result["deltas"]["total_revenue"] == 30000.0


@pytest.mark.asyncio
async def test_compare_runs_summary_text_renders_table_and_runs():
    """The fallback prose block - used by the conductor when the LLM
    produces a tool call but no narration - must include both run IDs
    and a markdown KPI table the chat UI renders."""
    state = _ScenarioState()
    base = _make_run_doc(run_id="pr_base", ending_cash=60000.0)
    compare = _make_run_doc(run_id="pr_compare", ending_cash=65000.0)

    async def _fake_get_run(run_id, user_id):
        return {"pr_base": base, "pr_compare": compare}.get(run_id)

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {"base_run_id": "pr_base", "compare_run_id": "pr_compare"},
            state, "user_x", None, None,
        )

    text = result["summary_text"]
    assert "pr_base" in text and "pr_compare" in text
    assert "| Metric |" in text
    assert "Ending cash" in text
    # The verb states the direction; the magnitude must render WITHOUT
    # a duplicate sign character. The previous "{:+,.2f}" format
    # rendered "$+5,000.00" - the abs(),.2f form drops the sign so
    # the rendered phrase reads cleanly.
    assert "improved by $5,000.00" in text
    assert "$+5,000.00" not in text


@pytest.mark.asyncio
async def test_compare_runs_summary_text_negative_delta_drops_minus_sign():
    """Symmetric coverage for the worsened branch: 'worsened by $X' must
    render the magnitude only, no leading '-'."""
    state = _ScenarioState()
    base = _make_run_doc(run_id="pr_base", ending_cash=60000.0)
    compare = _make_run_doc(run_id="pr_compare", ending_cash=52000.0)

    async def _fake_get_run(run_id, user_id):
        return {"pr_base": base, "pr_compare": compare}.get(run_id)

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {"base_run_id": "pr_base", "compare_run_id": "pr_compare"},
            state, "user_x", None, None,
        )

    text = result["summary_text"]
    assert "worsened by $8,000.00" in text
    assert "worsened by $-8,000.00" not in text


@pytest.mark.asyncio
async def test_compare_runs_starting_cash_change_renders_in_input_changes_section():
    """PR #144 second-pass review item 6: starting_cash is a user input,
    not a computed cash-flow delta. When the user re-runs with a
    different starting_cash, the change must render in its own
    "Inputs that changed" section, NOT inside the main KPI table next
    to ending_cash."""
    state = _ScenarioState()
    base = _make_run_doc(
        run_id="pr_base", starting_cash=50000.0, ending_cash=60000.0,
    )
    compare = _make_run_doc(
        run_id="pr_compare", starting_cash=75000.0, ending_cash=85000.0,
    )

    async def _fake_get_run(run_id, user_id):
        return {"pr_base": base, "pr_compare": compare}.get(run_id)

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {"base_run_id": "pr_base", "compare_run_id": "pr_compare"},
            state, "user_x", None, None,
        )

    # Payload shape: starting_cash lives in input_changes, not deltas.
    assert "starting_cash" not in result["deltas"]
    assert result["input_changes"]["starting_cash"] == 25000.0

    text = result["summary_text"]
    # Rendered text must mention the input change in its own section.
    assert "Inputs that changed: starting_cash from $50,000.00 to $75,000.00" in text
    # Main KPI table must NOT contain a row labeled "Starting cash" -
    # the table is for computed cash-flow movement only.
    assert "| Starting cash |" not in text


@pytest.mark.asyncio
async def test_compare_runs_zero_starting_cash_change_omits_input_changes_section():
    """When the user kept starting_cash the same, the input_changes
    section is suppressed - no point cluttering the reply with a
    no-op line."""
    state = _ScenarioState()
    base = _make_run_doc(
        run_id="pr_base", starting_cash=50000.0, ending_cash=60000.0,
    )
    compare = _make_run_doc(
        run_id="pr_compare", starting_cash=50000.0, ending_cash=65000.0,
    )

    async def _fake_get_run(run_id, user_id):
        return {"pr_base": base, "pr_compare": compare}.get(run_id)

    with patch(
        "services.nova_engine.persistence.get_run", new=AsyncMock(side_effect=_fake_get_run),
    ):
        result = await execute_tool(
            "compare_runs",
            {"base_run_id": "pr_base", "compare_run_id": "pr_compare"},
            state, "user_x", None, None,
        )

    assert result["input_changes"]["starting_cash"] == 0.0
    assert "Inputs that changed" not in result["summary_text"]


# ---------------------------------------------------------------------------
# query_finance_data — expanded metrics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_finance_data_payroll_metric_returns_only_payroll_amounts():
    """metric=payroll filters to PAYROLL category amounts. Mixed-category
    rows (revenue, payroll, admin) collapse to just the payroll line per
    bucket - no cross-contamination from generic 'expense'."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-03", "account_name": "70601 Payroll Expenses"},
         "amount": 30000.0, "count": 4},
        {"_id": {"bucket": "2026-03", "account_name": "70511 Admin Expenses"},
         "amount": 8000.0, "count": 2},
        {"_id": {"bucket": "2026-03", "account_name": "50101 Professional Services Revenue"},
         "amount": 50000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "payroll", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["metric"] == "payroll"
    rows_out = result["rows"]
    assert len(rows_out) == 1
    assert rows_out[0]["bucket"] == "2026-03"
    # Only the PAYROLL row contributes; admin and revenue are excluded.
    assert rows_out[0]["amount"] == 30000.0


@pytest.mark.asyncio
async def test_query_finance_data_admin_expenses_metric_returns_only_admin_amounts():
    """metric=admin_expenses filters to ADMIN_EXPENSES category."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-03", "account_name": "70511 Admin Expenses"},
         "amount": 8000.0, "count": 2},
        {"_id": {"bucket": "2026-03", "account_name": "70601 Payroll Expenses"},
         "amount": 30000.0, "count": 4},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "admin_expenses", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["rows"][0]["amount"] == 8000.0
    assert result["rows"][0]["bucket"] == "2026-03"


@pytest.mark.asyncio
async def test_query_finance_data_expenses_alias_maps_to_expense():
    """metric=expenses (plural alias) resolves to the canonical expense
    branch and includes payroll + admin + COGS just like 'expense'."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-03", "account_name": "70601 Payroll"},
         "amount": 30000.0, "count": 4},
        {"_id": {"bucket": "2026-03", "account_name": "70511 Admin"},
         "amount": 8000.0, "count": 2},
        {"_id": {"bucket": "2026-03", "account_name": "60302 PS COGS"},
         "amount": 5000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "expenses", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    # alias normalized to canonical name in the response so the LLM sees
    # exactly which branch ran.
    assert result["metric"] == "expense"
    assert result["rows"][0]["amount"] == 43000.0


@pytest.mark.asyncio
async def test_query_finance_data_gross_margin_alias_maps_to_gross_profit_margin():
    """metric=gross_margin resolves to gross_profit_margin and computes
    the same ratio."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-03", "account_name": "50101 PS Revenue"},
         "amount": 100000.0, "count": 1},
        {"_id": {"bucket": "2026-03", "account_name": "60302 PS COGS"},
         "amount": 30000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "gross_margin", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["metric"] == "gross_profit_margin"
    # Compute the expected ratio from the fixture rows so a future
    # fixture-value change can't quietly desync this assertion.
    rev_fixture = next(r["amount"] for r in rows if "Revenue" in r["_id"]["account_name"])
    cogs_fixture = next(r["amount"] for r in rows if "COGS" in r["_id"]["account_name"])
    expected_margin = (rev_fixture - cogs_fixture) / rev_fixture
    assert result["rows"][0]["amount"] == pytest.approx(expected_margin, rel=1e-6)


# ---------------------------------------------------------------------------
# OTHER_COGS coverage in company-level gross_profit / gross_profit_margin
# ---------------------------------------------------------------------------


def _company_gp_fixture_rows():
    """Synthetic GL rows covering every canonical revenue and COGS
    category. Expected totals are derived in the assertions below from
    these row amounts directly."""
    return [
        # Revenue side — every canonical revenue category present.
        {"_id": {"bucket": "2026-04", "account_name": "50101 PS Revenue"},
         "amount": 1000.0, "count": 1},
        {"_id": {"bucket": "2026-04", "account_name": "50201 FS Revenue"},
         "amount": 600.0, "count": 1},
        {"_id": {"bucket": "2026-04", "account_name": "50300 Other Income:Contract Commission Income"},
         "amount": 250.0, "count": 1},
        {"_id": {"bucket": "2026-04", "account_name": "50400 Wholesale Revenue"},
         "amount": 400.0, "count": 1},
        {"_id": {"bucket": "2026-04", "account_name": "Misc Income"},
         "amount": 150.0, "count": 1},
        # COGS side — every canonical COGS category present.
        {"_id": {"bucket": "2026-04", "account_name": "60302 PS COGS"},
         "amount": 300.0, "count": 1},
        {"_id": {"bucket": "2026-04", "account_name": "60201 FS COGS"},
         "amount": 200.0, "count": 1},
        {"_id": {"bucket": "2026-04", "account_name": "Generic Cost of Goods"},
         "amount": 100.0, "count": 1},
        # PAYROLL: not COGS — must NOT pull into the GP calculation.
        {"_id": {"bucket": "2026-04", "account_name": "70601 Payroll Expenses"},
         "amount": 500.0, "count": 1},
    ]


def _expected_company_gp(rows):
    revenue_accounts = ("50101", "50201", "50300", "50400", "Misc Income")
    cogs_substrings = ("60302", "60201", "Generic Cost of Goods")

    rev = sum(
        r["amount"] for r in rows
        if any(r["_id"]["account_name"].startswith(p) or p in r["_id"]["account_name"]
               for p in revenue_accounts)
    )
    cogs = sum(
        r["amount"] for r in rows
        if any(token in r["_id"]["account_name"] for token in cogs_substrings)
    )
    return rev, cogs, rev - cogs, (rev - cogs) / rev


@pytest.mark.asyncio
async def test_query_finance_data_gross_profit_subtracts_other_cogs():
    """Audited regression: prior code subtracted only PS+FS COGS while
    counting OTHER_REVENUE in revenue, overstating GP. With the fix,
    GP must equal Total Revenue - (PS_COGS + FS_COGS + OTHER_COGS)."""
    state = _ScenarioState()
    rows = _company_gp_fixture_rows()
    expected_rev, expected_cogs, expected_gp, _ = _expected_company_gp(rows)

    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "gross_profit", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert len(result["rows"]) == 1
    assert result["rows"][0]["amount"] == pytest.approx(expected_gp)


@pytest.mark.asyncio
async def test_query_finance_data_gross_profit_margin_subtracts_other_cogs():
    state = _ScenarioState()
    rows = _company_gp_fixture_rows()
    expected_rev, _, _, expected_margin = _expected_company_gp(rows)

    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "gross_profit_margin", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["rows"][0]["amount"] == pytest.approx(expected_margin)


@pytest.mark.asyncio
async def test_query_finance_data_gross_profit_response_carries_coverage_metadata():
    state = _ScenarioState()
    rows = _company_gp_fixture_rows()
    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "gross_profit", "group_by": "month"},
            state, "user_x", None, None,
        )

    from services.nova_engine.formulas import (
        COGS_CATEGORIES,
        GROSS_PROFIT_FORMULA_SOURCE_GL,
        REVENUE_CATEGORIES,
    )

    assert result["formula_source"] == GROSS_PROFIT_FORMULA_SOURCE_GL
    assert "formula_version" in result
    assert set(result["included_revenue_categories"]) == set(REVENUE_CATEGORIES)
    assert set(result["included_cogs_categories"]) == set(COGS_CATEGORIES)
    assert "OTHER_COGS" in result["included_cogs_categories"]
    assert "formula_notes" in result


@pytest.mark.asyncio
async def test_query_finance_data_other_cogs_changes_gp_versus_ps_fs_only():
    """Same revenue side; one fixture has OTHER_COGS, one doesn't.
    The GP totals MUST differ by exactly the OTHER_COGS amount."""
    state = _ScenarioState()
    other_cogs_amount = 250.0

    rows_without_other = [
        {"_id": {"bucket": "2026-05", "account_name": "50101 PS Revenue"},
         "amount": 1000.0, "count": 1},
        {"_id": {"bucket": "2026-05", "account_name": "60302 PS COGS"},
         "amount": 200.0, "count": 1},
    ]
    rows_with_other = rows_without_other + [
        {"_id": {"bucket": "2026-05", "account_name": "Generic Cost of Goods"},
         "amount": other_cogs_amount, "count": 1},
    ]

    db_a, _ = _make_mock_qbo_db_for_aggregation(rows_without_other)
    db_b, _ = _make_mock_qbo_db_for_aggregation(rows_with_other)

    with patch("services.nova_engine.db.get_qbo_db", return_value=db_a):
        result_a = await execute_tool(
            "query_finance_data",
            {"metric": "gross_profit", "group_by": "month"},
            state, "user_x", None, None,
        )
    state = _ScenarioState()
    with patch("services.nova_engine.db.get_qbo_db", return_value=db_b):
        result_b = await execute_tool(
            "query_finance_data",
            {"metric": "gross_profit", "group_by": "month"},
            state, "user_x", None, None,
        )

    gp_a = result_a["rows"][0]["amount"]
    gp_b = result_b["rows"][0]["amount"]
    assert gp_a - gp_b == pytest.approx(other_cogs_amount), (
        "Adding OTHER_COGS to the fixture must reduce gross profit by the "
        "OTHER_COGS amount; this is the audited regression."
    )


@pytest.mark.asyncio
async def test_query_finance_data_rent_returns_unavailable_data_with_hint():
    """rent has no CATEGORY_RULES mapping today - falling through to GL
    would silently match no rows. unavailable_data tells Nova to redirect
    instead of fabricating zeros."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "rent", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unavailable_data"
    assert result.get("metric") == "rent"
    assert "detail" in result and "hint" in result
    assert "rent" in result["detail"].lower() or "RENT" in result["detail"]


@pytest.mark.asyncio
async def test_query_finance_data_ar_total_with_snapshot_returns_value():
    """When the hourly snapshot job has run, ar_total reads the persisted
    aggregate so Nova answers from durable state instead of the GL."""
    state = _ScenarioState()
    snapshot = {
        "snapshot_id": "fs_test_ar",
        "tenant_id": "platform",
        "snapshot_date": "2026-05-04",
        "ar_total": 71500.0,
        "ap_total": 1632.50,
        "ar_aging": {"current": 47500.0, "1_30": 24000.0, "31_60": 0.0,
                     "61_90": 0.0, "over_90": 0.0, "unknown": 0.0},
        "ap_aging": {"current": 1632.50, "1_30": 0.0, "31_60": 0.0,
                     "61_90": 0.0, "over_90": 0.0, "unknown": 0.0},
        "source": "ar_invoices,ap_bills",
        "source_metadata": {
            "ar_invoice_count": 2, "ap_bill_count": 2,
            "ar_data_available": True, "ap_data_available": True,
        },
    }
    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db), \
         patch(
             "services.nova_engine.finance_snapshots.get_latest_snapshot",
             new=AsyncMock(return_value=snapshot),
         ):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "ar_total", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["metric"] == "ar_total"
    assert result["value"] == 71500.0
    assert result["snapshot_date"] == "2026-05-04"
    assert result["tenant_id"] == "platform"


@pytest.mark.asyncio
async def test_query_finance_data_ar_aging_returns_buckets_from_snapshot():
    """ar_aging surfaces the aging-bucket dict so Nova can quote which
    invoices are current vs. over_90."""
    state = _ScenarioState()
    snapshot = {
        "snapshot_id": "fs_test_aging",
        "tenant_id": "platform",
        "snapshot_date": "2026-05-04",
        "ar_total": 50000.0,
        "ap_total": 0.0,
        "ar_aging": {"current": 30000.0, "1_30": 15000.0, "31_60": 5000.0,
                     "61_90": 0.0, "over_90": 0.0, "unknown": 0.0},
        "ap_aging": {"current": 0.0, "1_30": 0.0, "31_60": 0.0,
                     "61_90": 0.0, "over_90": 0.0, "unknown": 0.0},
        "source": "ar_invoices,ap_bills",
        "source_metadata": {
            "ar_invoice_count": 3, "ap_bill_count": 0,
            "ar_data_available": True, "ap_data_available": False,
        },
    }
    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db), \
         patch(
             "services.nova_engine.finance_snapshots.get_latest_snapshot",
             new=AsyncMock(return_value=snapshot),
         ):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "ar_aging", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["metric"] == "ar_aging"
    assert result["value"]["current"] == 30000.0
    assert result["value"]["1_30"] == 15000.0
    assert result["value"]["31_60"] == 5000.0


@pytest.mark.asyncio
async def test_query_finance_data_ar_total_no_snapshot_returns_unavailable_data():
    """Fresh deploy / scheduler disabled: no finance_snapshots row exists,
    so Nova must redirect to the dashboard rather than fabricate a zero."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db), \
         patch(
             "services.nova_engine.finance_snapshots.get_latest_snapshot",
             new=AsyncMock(return_value=None),
         ):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "ar_total", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unavailable_data"
    assert result.get("metric") == "ar_total"
    assert "/api/finance/accounts-receivable" in result.get("hint", "")


@pytest.mark.asyncio
async def test_query_finance_data_ap_total_with_snapshot_returns_value():
    state = _ScenarioState()
    snapshot = {
        "snapshot_id": "fs_test_ap",
        "tenant_id": "platform",
        "snapshot_date": "2026-05-04",
        "ar_total": 0.0,
        "ap_total": 12345.67,
        "ar_aging": {b: 0.0 for b in
                     ("current", "1_30", "31_60", "61_90", "over_90", "unknown")},
        "ap_aging": {"current": 12345.67, "1_30": 0.0, "31_60": 0.0,
                     "61_90": 0.0, "over_90": 0.0, "unknown": 0.0},
        "source": "ar_invoices,ap_bills",
        "source_metadata": {
            "ar_invoice_count": 0, "ap_bill_count": 1,
            "ar_data_available": False, "ap_data_available": True,
        },
    }
    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db), \
         patch(
             "services.nova_engine.finance_snapshots.get_latest_snapshot",
             new=AsyncMock(return_value=snapshot),
         ):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "ap_total", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["metric"] == "ap_total"
    assert result["value"] == 12345.67


@pytest.mark.asyncio
async def test_query_finance_data_ap_total_empty_source_returns_unavailable():
    """Snapshot exists but the ap_bills collection was empty when it ran.
    Nova must distinguish "no source data" from "real zero" so the
    answer redirects to the dashboard."""
    state = _ScenarioState()
    snapshot = {
        "snapshot_id": "fs_no_ap",
        "tenant_id": "platform",
        "snapshot_date": "2026-05-04",
        "ar_total": 0.0,
        "ap_total": 0.0,
        "ar_aging": {b: 0.0 for b in
                     ("current", "1_30", "31_60", "61_90", "over_90", "unknown")},
        "ap_aging": {b: 0.0 for b in
                     ("current", "1_30", "31_60", "61_90", "over_90", "unknown")},
        "source": "ar_invoices,ap_bills",
        "source_metadata": {
            "ar_invoice_count": 0, "ap_bill_count": 0,
            "ar_data_available": False, "ap_data_available": False,
        },
    }
    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db), \
         patch(
             "services.nova_engine.finance_snapshots.get_latest_snapshot",
             new=AsyncMock(return_value=snapshot),
         ):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "ap_total", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unavailable_data"
    assert "/api/finance/accounts-payable" in result.get("hint", "")
    assert result.get("snapshot_date") == "2026-05-04"


@pytest.mark.asyncio
async def test_query_finance_data_current_month_revenue_overrides_date_range():
    """current_month_* metrics ignore date_from/date_to/group_by and
    always scope to the current calendar month. The forced window is
    surfaced in forced_filters so the LLM doesn't quote a stale window."""
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-05", "account_name": "50101 PS Revenue"},
         "amount": 25000.0, "count": 1},
    ]
    db, collection = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {
                "metric": "current_month_revenue",
                # Caller-supplied date range and group_by must be IGNORED.
                "group_by": "year",
                "date_from": "2020-01-01",
                "date_to": "2020-12-31",
            },
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["metric"] == "current_month_revenue"
    # forced_filters explicitly tells the LLM what window we used.
    assert "forced_filters" in result
    assert result["forced_filters"]["group_by"] == "month"
    # date_from is the first of *some* month, date_to is today.
    from datetime import date
    today_iso = date.today().isoformat()
    month_start_iso = date.today().replace(day=1).isoformat()
    assert result["forced_filters"]["date_from"] == month_start_iso
    assert result["forced_filters"]["date_to"] == today_iso
    # The actual aggregation pipeline received the forced match window.
    pipeline = collection.aggregate.call_args[0][0]
    match = pipeline[0]["$match"]
    assert match["tx_date"] == {"$gte": month_start_iso, "$lte": today_iso}
    # And revenue is sign-flipped to positive.
    assert result["rows"][0]["amount"] == 25000.0


@pytest.mark.asyncio
async def test_query_finance_data_current_month_expenses_uses_expense_branch():
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-05", "account_name": "70601 Payroll"},
         "amount": 12000.0, "count": 2},
        {"_id": {"bucket": "2026-05", "account_name": "70511 Admin"},
         "amount": 3000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "current_month_expenses", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["metric"] == "current_month_expenses"
    assert result["rows"][0]["amount"] == 15000.0
    assert "forced_filters" in result


@pytest.mark.asyncio
async def test_query_finance_data_current_month_net_change_is_revenue_minus_expense():
    state = _ScenarioState()
    rows = [
        {"_id": {"bucket": "2026-05", "account_name": "50101 PS Revenue"},
         "amount": 40000.0, "count": 1},
        {"_id": {"bucket": "2026-05", "account_name": "70601 Payroll"},
         "amount": 12000.0, "count": 2},
        {"_id": {"bucket": "2026-05", "account_name": "60302 PS COGS"},
         "amount": 5000.0, "count": 1},
    ]
    db, _ = _make_mock_qbo_db_for_aggregation(rows)
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "current_month_net_change", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    # 40000 (revenue, sign-flipped) - (12000 payroll + 5000 COGS) = 23000
    assert result["rows"][0]["amount"] == 23000.0


@pytest.mark.asyncio
async def test_query_finance_data_current_month_with_no_matching_rows_returns_empty():
    """If the GL has no rows in the current month the response is well-
    formed with an empty rows list - no fake zero bucket."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "current_month_revenue", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["rows"] == []
    assert result["row_count"] == 0
    assert "forced_filters" in result


@pytest.mark.asyncio
async def test_query_finance_data_unsupported_metric_lists_valid_metrics():
    """A metric outside the registry returns unknown_metric with the full
    valid list so the LLM can self-correct."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "DPO", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unknown_metric"
    valid = result.get("valid", [])
    # The list must include every metric the schema enum advertises so the
    # LLM has a single source of truth for self-correction.
    for m in (
        "revenue", "expense", "expenses", "gross_profit", "gross_profit_margin",
        "gross_margin", "expense_ratio", "transaction_count",
        "payroll", "admin_expenses",
        "rent", "ar_total", "ap_total", "ar_aging", "ap_aging",
        "current_month_revenue", "current_month_expenses",
        "current_month_net_change",
    ):
        assert m in valid, f"missing canonical metric in unknown_metric.valid: {m}"


def test_query_finance_data_schema_enum_lists_every_registered_metric():
    """Schema-level guard: the enum advertised to the LLM must equal the
    dispatcher's registry so no metric exists in only one place."""
    from services.ai.agents.tools.nova_tools import _VALID_METRICS

    schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "query_finance_data")
    enum = set(schema["parameters"]["properties"]["metric"]["enum"])
    assert enum == set(_VALID_METRICS)


@pytest.mark.asyncio
async def test_query_finance_data_unavailable_metric_does_not_hit_qbo():
    """unavailable_data short-circuits before any aggregation. Important
    so we don't waste the qbo_db roundtrip and don't leak which user/
    tenant asked - the dispatcher returns the same payload regardless."""
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "rent", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unavailable_data"
    # No aggregate call made.
    collection.aggregate.assert_not_called()


# ---------------------------------------------------------------------------
# query_finance_data — location filter integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_finance_data_location_blank_injects_blank_filter():
    """location_blank=true must inject an $and of "field missing/blank"
    predicates into the Mongo $match stage and surface a coverage
    warning in the response."""
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {
                "metric": "revenue",
                "group_by": "month",
                "location_blank": True,
            },
            state, "user_x", None, None,
        )

    # The $match stage emitted to Mongo must carry the $and clause.
    pipeline = collection.aggregate.call_args[0][0]
    match_stage = pipeline[0]["$match"]
    assert "$and" in match_stage
    # Each clause should reference one location-like field.
    flattened = repr(match_stage["$and"])
    for field in ("location", "location_id", "department_ref_id"):
        assert field in flattened

    # Response carries the filter echo + coverage warning.
    assert result["location_filter"] == {"location_blank": True}
    assert "location" in result["location_coverage_warning"].lower()


@pytest.mark.asyncio
async def test_query_finance_data_location_substring_filter_matches_id_and_name_fields():
    state = _ScenarioState()
    db, collection = _make_mock_qbo_db_for_aggregation([])

    needle = "WAREHOUSE-A"
    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {
                "metric": "revenue",
                "group_by": "month",
                "location": needle,
            },
            state, "user_x", None, None,
        )

    pipeline = collection.aggregate.call_args[0][0]
    match_stage = pipeline[0]["$match"]
    assert "$or" in match_stage
    flattened = repr(match_stage["$or"])
    # Substring fields use case-insensitive regex.
    assert "$regex" in flattened
    assert "$options" in flattened
    # Id fields use exact match against the needle.
    assert needle in flattened
    # Response carries the filter echo.
    assert result["location_filter"] == {"location": needle}


@pytest.mark.asyncio
async def test_query_finance_data_location_blank_and_location_are_mutually_exclusive():
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {
                "metric": "revenue",
                "group_by": "month",
                "location": "HQ",
                "location_blank": True,
            },
            state, "user_x", None, None,
        )

    assert result.get("error") == "conflicting_location_filters"


@pytest.mark.asyncio
async def test_query_finance_data_no_location_filter_does_not_emit_coverage_warning():
    """Default behavior unchanged when neither location filter is set —
    the response must not carry a location_filter or
    location_coverage_warning key."""
    state = _ScenarioState()
    db, _ = _make_mock_qbo_db_for_aggregation([])

    with patch("services.nova_engine.db.get_qbo_db", return_value=db):
        result = await execute_tool(
            "query_finance_data",
            {"metric": "revenue", "group_by": "month"},
            state, "user_x", None, None,
        )

    assert "location_filter" not in result
    assert "location_coverage_warning" not in result


# ---------------------------------------------------------------------------
# Nova prompt + capability summary mention location coverage
# ---------------------------------------------------------------------------


def test_prompt_says_to_disclose_location_coverage_when_incomplete():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    text = _SYSTEM_PROMPT
    assert "## Location coverage" in text
    assert "get_gl_location_coverage" in text
    lowered = text.lower()
    assert "do not invent location values" in lowered
    assert "coverage_pct" in lowered or "coverage pct" in lowered


def test_capability_summary_includes_location_coverage_only_when_tool_registered():
    """Existing OR-of-backing-tools design: pruning
    get_gl_location_coverage from the available_tools list must drop
    the Location Coverage category from capabilities."""
    from services.ai.agents.nova_capabilities import build_nova_capability_summary

    schema_names = [s["name"] for s in NOVA_TOOL_SCHEMAS]
    full = build_nova_capability_summary(available_tools=schema_names)
    full_categories = {c["category"] for c in full["capabilities"]}
    assert "Location Coverage" in full_categories

    pruned = [s for s in schema_names if s != "get_gl_location_coverage"]
    pruned_summary = build_nova_capability_summary(available_tools=pruned)
    pruned_categories = {c["category"] for c in pruned_summary["capabilities"]}
    assert "Location Coverage" not in pruned_categories
