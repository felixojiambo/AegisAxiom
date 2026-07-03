"""Tests for wholesale-revenue auto-detection.

Three layers covered:

  1. Per-record classifier (classify_record): rules-only attribution
     from AEGISAXIOM structured fields. UNKNOWN is the explicit return when
     no rule fires - never a guess.

  2. GL history aggregator + auto-fill gate: detect_wholesale_history_from_gl
     plus compute_auto_wholesale_assumption together decide whether the
     GL signal is consistent enough to seed a recurring assumption.

  3. End-to-end through data_loader.build_projection_inputs: the
     orchestrator must auto-fill manual_recurring_revenue with a
     WHOLESALE assumption when GL is consistent, and leave it empty
     otherwise. The wrapper must preserve the auto-detected entry
     across caller-supplied scenario splices.
"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.nova_engine.wholesale_detection import (
    AUTO_DETECTED_NAME_PREFIX,
    MIN_MONTHS_FOR_AUTO_FILL,
    classify_record,
    compute_auto_wholesale_assumption,
    detect_wholesale_history_from_gl,
    is_auto_detected,
)
from services.nova_engine.models import (
    ProjectionInputs,
    RecurringRevenueAssumption,
)


# ---------------------------------------------------------------------------
# classify_record: deterministic per-record signals
# ---------------------------------------------------------------------------


def test_classify_record_gl_account_50400_is_wholesale():
    """The QBO chart-of-accounts prefix 50400 is the schema-encoded
    wholesale revenue category. Detection MUST surface this."""
    result = classify_record(account_name="50400 Wholesale MRR Income")
    assert result["classification"] == "WHOLESALE"
    assert "gl_account_50400" in result["signals"]


def test_classify_record_gl_account_50101_is_not_wholesale():
    """50101 is PS_REVENUE - a record explicitly tagged as PS revenue
    cannot also be wholesale, so we return NOT_WHOLESALE rather than
    UNKNOWN."""
    result = classify_record(account_name="50101 Professional Services")
    assert result["classification"] == "NOT_WHOLESALE"


def test_classify_record_contract_type_wholesale_mrr():
    result = classify_record(contract_type="wholesale_mrr")
    assert result["classification"] == "WHOLESALE"
    assert "contract_type=wholesale_mrr" in result["signals"]


def test_classify_record_sow_line_of_business_wholesale_mrr():
    result = classify_record(line_of_business="wholesale_mrr")
    assert result["classification"] == "WHOLESALE"
    assert "line_of_business=wholesale_mrr" in result["signals"]


def test_classify_record_opportunity_lines_of_business_includes_wholesale_mrr():
    """Opportunity.lines_of_business is a list - any wholesale_mrr
    entry counts."""
    result = classify_record(
        lines_of_business=["professional_services", "wholesale_mrr"],
    )
    assert result["classification"] == "WHOLESALE"


def test_classify_record_item_category_wholesale_mrr():
    result = classify_record(item_category="wholesale_mrr")
    assert result["classification"] == "WHOLESALE"


def test_classify_record_aggregates_multiple_signals():
    """When several structured fields point to wholesale, every signal
    is reported - so the audit trail shows which rules fired, not just
    'wholesale because reasons'."""
    result = classify_record(
        account_name="50400 Wholesale Income",
        contract_type="wholesale_mrr",
        item_category="wholesale_mrr",
    )
    assert result["classification"] == "WHOLESALE"
    assert len(result["signals"]) == 3


def test_classify_record_no_signals_returns_unknown():
    """No structured signal => UNKNOWN, NEVER a guess. Locking this in
    to prevent future drift toward fuzzy text matching."""
    result = classify_record()
    assert result["classification"] == "UNKNOWN"
    assert result["signals"] == []


def test_classify_record_unrecognized_account_name_returns_unknown():
    """An account name with no prefix match and no other signal must
    return UNKNOWN - we don't try to guess from free text."""
    result = classify_record(account_name="Something Wholesale-ish")
    assert result["classification"] == "UNKNOWN"


def test_classify_record_only_inactive_lob_returns_unknown():
    """professional_services / field_services don't establish wholesale
    one way or the other. UNKNOWN, not NOT_WHOLESALE - the absence of
    the wholesale_mrr value is not the same as a contradiction."""
    result = classify_record(line_of_business="field_services")
    assert result["classification"] == "UNKNOWN"


def test_classify_record_case_and_whitespace_tolerant():
    """The structured value is canonical 'wholesale_mrr' but the seed
    fixtures sometimes include whitespace; lower() the input."""
    result = classify_record(contract_type="  WHOLESALE_MRR  ")
    assert result["classification"] == "WHOLESALE"


# ---------------------------------------------------------------------------
# detect_wholesale_history_from_gl
# ---------------------------------------------------------------------------


def _make_qbo_with_aggregate(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    collection = MagicMock()
    collection.aggregate = MagicMock(return_value=cursor)
    db = MagicMock()
    db.general_ledger = collection
    return db


@pytest.mark.asyncio
async def test_detect_wholesale_history_sums_50400_rows_per_month():
    """Only WHOLESALE_REVENUE-categorized rows count. PS_REVENUE rows
    in the fixture must not leak into the wholesale total."""
    db = _make_qbo_with_aggregate([
        {"_id": {"month": "2026-01", "account_id": "50400",
                 "account_name": "50400 Wholesale MRR"},
         "amount": -8000.0},
        {"_id": {"month": "2026-02", "account_id": "50400",
                 "account_name": "50400 Wholesale MRR"},
         "amount": -7500.0},
        {"_id": {"month": "2026-03", "account_id": "50400",
                 "account_name": "50400 Wholesale MRR"},
         "amount": -9000.0},
        # Decoy: PS revenue must not be counted toward wholesale.
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 Professional Services"},
         "amount": -50000.0},
    ])
    result = await detect_wholesale_history_from_gl(
        db, ["2026-01", "2026-02", "2026-03"],
    )
    assert result == {"2026-01": 8000.0, "2026-02": 7500.0, "2026-03": 9000.0}


@pytest.mark.asyncio
async def test_detect_wholesale_history_zeros_months_with_no_signal():
    """Months without GL rows return 0.0 (not missing)."""
    db = _make_qbo_with_aggregate([
        {"_id": {"month": "2026-03", "account_id": "50400",
                 "account_name": "50400 Wholesale"},
         "amount": -1000.0},
    ])
    result = await detect_wholesale_history_from_gl(
        db, ["2026-01", "2026-02", "2026-03"],
    )
    assert result == {"2026-01": 0.0, "2026-02": 0.0, "2026-03": 1000.0}


@pytest.mark.asyncio
async def test_detect_wholesale_history_empty_history_months():
    db = _make_qbo_with_aggregate([])
    result = await detect_wholesale_history_from_gl(db, [])
    assert result == {}


# ---------------------------------------------------------------------------
# compute_auto_wholesale_assumption: the confidence gate
# ---------------------------------------------------------------------------


def test_compute_auto_wholesale_consistent_signal_returns_assumption():
    """3 of 3 history months show wholesale -> auto-fill. Avg is the
    monthly amount."""
    monthly = {"2026-01": 8000.0, "2026-02": 7500.0, "2026-03": 9000.0}
    result = compute_auto_wholesale_assumption(
        monthly, ["2026-01", "2026-02", "2026-03"],
        starts_month="2026-04",
    )
    assert result is not None
    assert isinstance(result, RecurringRevenueAssumption)
    assert result.line_type == "WHOLESALE"
    assert result.amount_per_month == pytest.approx(8166.67, rel=1e-3)
    assert result.starts_month == "2026-04"
    assert is_auto_detected(result)


def test_compute_auto_wholesale_two_of_three_months_is_enough():
    """The threshold MIN_MONTHS_FOR_AUTO_FILL=2 means 2/3 months trigger
    auto-fill. Lock that boundary in so tweaks are visible in tests."""
    assert MIN_MONTHS_FOR_AUTO_FILL == 2
    monthly = {"2026-01": 5000.0, "2026-02": 0.0, "2026-03": 4000.0}
    result = compute_auto_wholesale_assumption(
        monthly, ["2026-01", "2026-02", "2026-03"], starts_month="2026-04",
    )
    assert result is not None
    assert result.amount_per_month == pytest.approx(3000.0)


def test_compute_auto_wholesale_one_month_is_not_enough():
    """A single isolated month of wholesale activity is not consistent
    enough to seed a recurring assumption - it could be a one-off."""
    monthly = {"2026-01": 0.0, "2026-02": 0.0, "2026-03": 5000.0}
    result = compute_auto_wholesale_assumption(
        monthly, ["2026-01", "2026-02", "2026-03"],
    )
    assert result is None


def test_compute_auto_wholesale_zero_average_returns_none():
    """All zeros => no assumption. Edge case for completeness."""
    monthly = {"2026-01": 0.0, "2026-02": 0.0, "2026-03": 0.0}
    result = compute_auto_wholesale_assumption(
        monthly, ["2026-01", "2026-02", "2026-03"],
    )
    assert result is None


def test_compute_auto_wholesale_empty_inputs_returns_none():
    assert compute_auto_wholesale_assumption({}, []) is None
    assert compute_auto_wholesale_assumption(
        {"2026-01": 100.0}, [],
    ) is None


def test_is_auto_detected_marker_round_trip():
    auto = RecurringRevenueAssumption(
        name=f"{AUTO_DETECTED_NAME_PREFIX} wholesale revenue (GL 50400)",
        amount_per_month=1000.0, line_type="WHOLESALE",
        starts_month="2026-04",
    )
    manual = RecurringRevenueAssumption(
        name="user-supplied",
        amount_per_month=500.0, line_type="WHOLESALE",
        starts_month="2026-04",
    )
    assert is_auto_detected(auto) is True
    assert is_auto_detected(manual) is False


# ---------------------------------------------------------------------------
# data_loader integration: confident detection auto-fills inputs
# ---------------------------------------------------------------------------


def _make_sequential_qbo_db(*aggregate_results):
    """Mirror of the helper in test_nova_data_loader.py - produces a
    Motor mock that returns each list on consecutive aggregate() calls
    and serves a string tx_date for the orchestrator's sanity check."""
    call_index = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_index["i"]
        call_index["i"] += 1
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=aggregate_results[idx])
        return cursor

    collection = MagicMock()
    collection.aggregate = MagicMock(side_effect=aggregate_side_effect)
    collection.find_one = AsyncMock(return_value={"tx_date": "2026-01-15"})

    db = MagicMock()
    db.general_ledger = collection
    return db


def _make_app_db():
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    collection = MagicMock()
    collection.find = MagicMock(return_value=cursor)
    db = MagicMock()
    db.account_groupings = collection
    return db


def _ps_fs_history_rows():
    """3 months of PS + FS revenue with matching COGS - the minimum the
    orchestrator needs to satisfy InsufficientHistoryError checks."""
    return [
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 PS"}, "amount": -10000.0},
        {"_id": {"month": "2026-02", "account_id": "50101",
                 "account_name": "50101 PS"}, "amount": -12000.0},
        {"_id": {"month": "2026-03", "account_id": "50101",
                 "account_name": "50101 PS"}, "amount": -11000.0},
        {"_id": {"month": "2026-01", "account_id": "50202",
                 "account_name": "50202 FS"}, "amount": -5000.0},
        {"_id": {"month": "2026-02", "account_id": "50202",
                 "account_name": "50202 FS"}, "amount": -6000.0},
        {"_id": {"month": "2026-03", "account_id": "50202",
                 "account_name": "50202 FS"}, "amount": -5500.0},
        {"_id": {"month": "2026-01", "account_id": "60302",
                 "account_name": "60302 PS COGS"}, "amount": 3000.0},
        {"_id": {"month": "2026-02", "account_id": "60302",
                 "account_name": "60302 PS COGS"}, "amount": 4000.0},
        {"_id": {"month": "2026-03", "account_id": "60302",
                 "account_name": "60302 PS COGS"}, "amount": 3500.0},
    ]


def _wholesale_history_rows():
    """3 months of consistent 50400 wholesale activity."""
    return [
        {"_id": {"month": "2026-01", "account_id": "50400",
                 "account_name": "50400 Wholesale MRR"},
         "amount": -8000.0},
        {"_id": {"month": "2026-02", "account_id": "50400",
                 "account_name": "50400 Wholesale MRR"},
         "amount": -7500.0},
        {"_id": {"month": "2026-03", "account_id": "50400",
                 "account_name": "50400 Wholesale MRR"},
         "amount": -9000.0},
    ]


@pytest.mark.asyncio
async def test_build_projection_inputs_auto_fills_wholesale_when_consistent():
    """Three months of consistent GL wholesale signal triggers auto-fill;
    the resulting ProjectionInputs has a single auto-detected WHOLESALE
    recurring revenue entry the engine will consume."""
    from services.nova_engine.data_loader import build_projection_inputs

    # Aggregate call order under build_projection_inputs:
    #   1. get_latest_tx_date           -> max_date row
    #   2. load_revenue_history         -> ps/fs rows
    #   3. load_gross_profit_history    -> same rows (reused)
    #   4. load_mrr_history             -> empty, no 50300 rows
    #   5. detect_wholesale_history     -> 50400 rows
    qbo_db = _make_sequential_qbo_db(
        [{"_id": None, "max_date": "2026-04-14"}],
        _ps_fs_history_rows(),
        _ps_fs_history_rows(),
        [],   # MRR (no 50300 rows)
        _wholesale_history_rows(),
    )
    app_db = _make_app_db()

    inputs = await build_projection_inputs(
        qbo_db, app_db,
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
    )
    wholesale_entries = [
        a for a in inputs.manual_recurring_revenue
        if a.line_type == "WHOLESALE"
    ]
    assert len(wholesale_entries) == 1
    entry = wholesale_entries[0]
    assert entry.amount_per_month == pytest.approx(8166.67, rel=1e-3)
    assert is_auto_detected(entry)
    assert entry.starts_month == "2026-05"


@pytest.mark.asyncio
async def test_build_projection_inputs_skips_wholesale_when_signal_sparse():
    """One isolated wholesale month is not enough to seed an
    assumption. manual_recurring_revenue must stay empty."""
    from services.nova_engine.data_loader import build_projection_inputs

    sparse = [
        {"_id": {"month": "2026-03", "account_id": "50400",
                 "account_name": "50400 Wholesale"},
         "amount": -1000.0},
    ]
    qbo_db = _make_sequential_qbo_db(
        [{"_id": None, "max_date": "2026-04-14"}],
        _ps_fs_history_rows(),
        _ps_fs_history_rows(),
        [],   # MRR (no 50300 rows)
        sparse,
    )
    app_db = _make_app_db()
    inputs = await build_projection_inputs(
        qbo_db, app_db,
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
    )
    assert inputs.manual_recurring_revenue == []


@pytest.mark.asyncio
async def test_build_projection_inputs_skips_wholesale_with_no_gl_signal():
    """Zero wholesale rows in the GL => no assumption, no projection
    impact. The legacy 'skip wholesale' behavior is preserved when the
    50400 prefix has no activity."""
    from services.nova_engine.data_loader import build_projection_inputs

    qbo_db = _make_sequential_qbo_db(
        [{"_id": None, "max_date": "2026-04-14"}],
        _ps_fs_history_rows(),
        _ps_fs_history_rows(),
        [],   # MRR (no 50300 rows)
        [],   # wholesale (no 50400 rows)
    )
    app_db = _make_app_db()
    inputs = await build_projection_inputs(
        qbo_db, app_db,
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
    )
    assert inputs.manual_recurring_revenue == []


# ---------------------------------------------------------------------------
# Wrapper merge: auto-detected entries survive caller-supplied scenario state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrapper_preserves_auto_detected_when_caller_supplies_no_wholesale(
    monkeypatch,
):
    """The chat-tool flow always passes manual_recurring_revenue=[].
    Without the merge, auto-detection would be wiped on every chat
    projection. This test locks in the merge behavior."""
    from services.nova_engine import data_loader

    auto_assumption = RecurringRevenueAssumption(
        name=f"{AUTO_DETECTED_NAME_PREFIX} wholesale revenue (GL 50400)",
        amount_per_month=8000.0, line_type="WHOLESALE",
        starts_month="2026-05",
    )
    base_inputs = ProjectionInputs(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        ps_revenue_last_3_months=[10000.0, 12000.0, 11000.0],
        fs_revenue_last_3_months=[5000.0, 6000.0, 5500.0],
        ps_gross_profit_last_3_months=[7000.0, 8000.0, 7500.0],
        fs_gross_profit_last_3_months=[5000.0, 6000.0, 5500.0],
        manual_recurring_revenue=[auto_assumption],
    )

    async def fake_build_projection_inputs(*args, **kwargs):
        return base_inputs

    monkeypatch.setattr(
        data_loader, "build_projection_inputs", fake_build_projection_inputs,
    )
    monkeypatch.setattr(data_loader, "get_qbo_db", lambda: MagicMock())
    monkeypatch.setattr(data_loader, "get_app_db", lambda: MagicMock())

    merged = await data_loader.build_projection_inputs_from_globals(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        manual_recurring_revenue=[],  # caller-supplied empty list
    )

    wholesale = [a for a in merged.manual_recurring_revenue
                 if a.line_type == "WHOLESALE"]
    assert len(wholesale) == 1
    assert is_auto_detected(wholesale[0])


# ---------------------------------------------------------------------------
# Nova tool: detect_wholesale_revenue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nova_tool_detect_wholesale_returns_confident_classification(
    monkeypatch,
):
    """Three months of GL signal => classification=WHOLESALE_DETECTED
    AND auto_assumption present so callers know the projection will
    be seeded automatically."""
    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        execute_tool,
    )

    qbo_db = _make_qbo_with_aggregate(_wholesale_history_rows())
    monkeypatch.setattr(
        "services.nova_engine.db.get_qbo_db", lambda: qbo_db,
    )

    state = _ScenarioState()
    result = await execute_tool(
        "detect_wholesale_revenue",
        {"history_months": ["2026-01", "2026-02", "2026-03"]},
        state, "user_x", None, None,
    )

    assert result["classification"] == "WHOLESALE_DETECTED"
    assert result["months_with_signal"] == 3
    assert result["total_wholesale_revenue"] == pytest.approx(24500.0)
    assert result["auto_assumption"] is not None
    assert result["auto_assumption"]["line_type"] == "WHOLESALE"


@pytest.mark.asyncio
async def test_nova_tool_detect_wholesale_returns_ambiguous_for_sparse_signal(
    monkeypatch,
):
    """One isolated month of wholesale activity => AMBIGUOUS, no
    auto_assumption. The hint nudges the caller toward a manual add
    if they trust the single data point."""
    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        execute_tool,
    )

    qbo_db = _make_qbo_with_aggregate([
        {"_id": {"month": "2026-03", "account_id": "50400",
                 "account_name": "50400 Wholesale"},
         "amount": -1000.0},
    ])
    monkeypatch.setattr(
        "services.nova_engine.db.get_qbo_db", lambda: qbo_db,
    )

    state = _ScenarioState()
    result = await execute_tool(
        "detect_wholesale_revenue",
        {"history_months": ["2026-01", "2026-02", "2026-03"]},
        state, "user_x", None, None,
    )

    assert result["classification"] == "AMBIGUOUS"
    assert result["auto_assumption"] is None
    assert "hint" in result


@pytest.mark.asyncio
async def test_nova_tool_detect_wholesale_returns_unknown_when_no_gl_signal(
    monkeypatch,
):
    """Zero wholesale rows => UNKNOWN, with a hint pointing at the
    chart-of-accounts prefix."""
    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        execute_tool,
    )

    qbo_db = _make_qbo_with_aggregate([])
    monkeypatch.setattr(
        "services.nova_engine.db.get_qbo_db", lambda: qbo_db,
    )

    state = _ScenarioState()
    result = await execute_tool(
        "detect_wholesale_revenue",
        {"history_months": ["2026-01", "2026-02", "2026-03"]},
        state, "user_x", None, None,
    )

    assert result["classification"] == "UNKNOWN"
    assert result["auto_assumption"] is None
    assert result["total_wholesale_revenue"] == 0.0


@pytest.mark.asyncio
async def test_nova_tool_detect_wholesale_invalid_history_months_rejected(
    monkeypatch,
):
    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        execute_tool,
    )

    qbo_db = _make_qbo_with_aggregate([])
    monkeypatch.setattr(
        "services.nova_engine.db.get_qbo_db", lambda: qbo_db,
    )

    state = _ScenarioState()
    result = await execute_tool(
        "detect_wholesale_revenue",
        {"history_months": ["2026/01"]},  # bad format
        state, "user_x", None, None,
    )
    assert result.get("error") == "invalid_history_months"


@pytest.mark.asyncio
async def test_wrapper_caller_supplied_wholesale_wins_over_auto_detected(
    monkeypatch,
):
    """When the caller explicitly supplies a WHOLESALE assumption, the
    auto-detected one is dropped - caller knows best, and double-counting
    would be the worst possible outcome."""
    from services.nova_engine import data_loader

    auto_assumption = RecurringRevenueAssumption(
        name=f"{AUTO_DETECTED_NAME_PREFIX} wholesale",
        amount_per_month=8000.0, line_type="WHOLESALE",
        starts_month="2026-05",
    )
    user_assumption = RecurringRevenueAssumption(
        name="my wholesale forecast",
        amount_per_month=15000.0, line_type="WHOLESALE",
        starts_month="2026-05",
    )
    base_inputs = ProjectionInputs(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        ps_revenue_last_3_months=[10000.0, 12000.0, 11000.0],
        fs_revenue_last_3_months=[5000.0, 6000.0, 5500.0],
        ps_gross_profit_last_3_months=[7000.0, 8000.0, 7500.0],
        fs_gross_profit_last_3_months=[5000.0, 6000.0, 5500.0],
        manual_recurring_revenue=[auto_assumption],
    )

    async def fake_build_projection_inputs(*args, **kwargs):
        return base_inputs

    monkeypatch.setattr(
        data_loader, "build_projection_inputs", fake_build_projection_inputs,
    )
    monkeypatch.setattr(data_loader, "get_qbo_db", lambda: MagicMock())
    monkeypatch.setattr(data_loader, "get_app_db", lambda: MagicMock())

    merged = await data_loader.build_projection_inputs_from_globals(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        manual_recurring_revenue=[user_assumption],
    )

    wholesale = [a for a in merged.manual_recurring_revenue
                 if a.line_type == "WHOLESALE"]
    assert len(wholesale) == 1
    assert wholesale[0].amount_per_month == 15000.0
    assert not is_auto_detected(wholesale[0])
