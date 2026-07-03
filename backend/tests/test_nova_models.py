"""Tests for engine models, exception types, and timing defaults.

Covers:
  - InsufficientHistoryError construction and message format
  - LINE_TIMING_DEFAULTS coverage and content (all 11 line types, all sum to 1.0)
  - ManualCostAssumption Pydantic validation, including CostLineType restriction
  - RecurringRevenueAssumption Pydantic validation, including
    RevenueLineType restriction
  - ProjectionInputs strict length-3 validation at model construction
  - YYYY-MM format validation across all month-string fields
  - weekly_timing_overrides accepts entries for any line type
"""
import pytest
from pydantic import ValidationError

from services.nova_engine.models import (
    InsufficientHistoryError,
    LINE_TIMING_DEFAULTS,
    ManualCostAssumption,
    ProjectionInputs,
    RecurringRevenueAssumption,
    WeeklyDistribution,
)


# InsufficientHistoryError ----------------------------------------------

def test_insufficient_history_error_basic():
    err = InsufficientHistoryError(line="ps_revenue", available_months=2)
    assert err.line == "ps_revenue"
    assert err.available_months == 2
    assert err.required_months == 3
    msg = str(err)
    assert "ps_revenue" in msg
    assert "2 complete month(s)" in msg
    assert "3 required" in msg
    assert "manual assumption" in msg


def test_insufficient_history_error_zero_available():
    err = InsufficientHistoryError(line="fs_revenue", available_months=0)
    assert err.available_months == 0
    assert "0 complete month(s)" in str(err)


def test_insufficient_history_error_is_raisable():
    with pytest.raises(InsufficientHistoryError) as exc_info:
        raise InsufficientHistoryError(line="mrr", available_months=1)
    assert exc_info.value.line == "mrr"


def test_insufficient_history_error_custom_required():
    err = InsufficientHistoryError(
        line="ps_revenue", available_months=4, required_months=6
    )
    assert err.required_months == 6
    assert "6 required" in str(err)


# LINE_TIMING_DEFAULTS --------------------------------------------------

def test_line_timing_defaults_covers_all_line_types():
    """Every LineType (revenue + cost) must have a defaults entry."""
    expected = {
        # Revenue
        "PS_REVENUE", "FS_REVENUE", "MRR", "WHOLESALE", "OTHER_REVENUE",
        # Cost
        "PS_COGS", "FS_COGS",
        "PAYROLL", "RENT", "ADMIN_EXPENSES", "OTHER_COSTS",
    }
    assert set(LINE_TIMING_DEFAULTS.keys()) == expected


def test_line_timing_defaults_all_sum_to_1():
    """Each line type's default weights sum to 1.0."""
    for line_type, dist in LINE_TIMING_DEFAULTS.items():
        total = dist.week1_pct + dist.week2_pct + dist.week3_pct + dist.week4_pct
        assert total == pytest.approx(1.0, abs=0.001), (
            f"Line type {line_type} sums to {total}, expected 1.0"
        )


def test_mrr_defaults_to_first_week_only():
    """Asad's call: MRR usually lands in the first week."""
    dist = LINE_TIMING_DEFAULTS["MRR"]
    assert dist.week1_pct == 1.0
    assert dist.week2_pct == 0.0
    assert dist.week3_pct == 0.0
    assert dist.week4_pct == 0.0


def test_wholesale_defaults_to_first_or_second_week():
    """Asad's call: wholesale tends to land first or second week."""
    dist = LINE_TIMING_DEFAULTS["WHOLESALE"]
    assert dist.week1_pct == 0.5
    assert dist.week2_pct == 0.5
    assert dist.week3_pct == 0.0
    assert dist.week4_pct == 0.0


def test_payroll_defaults_to_biweekly():
    """Default payroll cadence: weeks 2 and 4."""
    dist = LINE_TIMING_DEFAULTS["PAYROLL"]
    assert dist.week1_pct == 0.0
    assert dist.week2_pct == 0.5
    assert dist.week3_pct == 0.0
    assert dist.week4_pct == 0.5


# ManualCostAssumption (CostLineType-restricted) -----------------------

def test_manual_cost_assumption_minimal():
    a = ManualCostAssumption(
        name="Q2 hire — engineer",
        amount_per_month=9000.0,
        line_type="PAYROLL",
    )
    assert a.name == "Q2 hire — engineer"
    assert a.amount_per_month == 9000.0
    assert a.line_type == "PAYROLL"
    assert a.starts_month is None
    assert a.ends_month is None
    assert a.weekly_dist is None


def test_manual_cost_assumption_with_bounds_and_dist():
    a = ManualCostAssumption(
        name="Office lease",
        amount_per_month=5000.0,
        line_type="RENT",
        starts_month="2026-05",
        ends_month="2026-12",
        weekly_dist=WeeklyDistribution(
            week1_pct=1.0, week2_pct=0.0, week3_pct=0.0, week4_pct=0.0
        ),
    )
    assert a.starts_month == "2026-05"
    assert a.ends_month == "2026-12"
    assert a.weekly_dist.week1_pct == 1.0


def test_manual_cost_assumption_other_costs_line_type():
    """OTHER_COSTS is the home for ad-hoc one-offs, loan repayments, etc."""
    a = ManualCostAssumption(
        name="Q3 equipment purchase",
        amount_per_month=40000.0,
        line_type="OTHER_COSTS",
    )
    assert a.line_type == "OTHER_COSTS"


def test_manual_cost_assumption_is_frozen():
    a = ManualCostAssumption(name="X", amount_per_month=1.0, line_type="PAYROLL")
    with pytest.raises(ValidationError):
        a.amount_per_month = 2.0


def test_manual_cost_assumption_rejects_revenue_line_type():
    """CRITICAL: cost assumption cannot accept a revenue line type."""
    with pytest.raises(ValidationError):
        ManualCostAssumption(
            name="Bad config", amount_per_month=1.0, line_type="MRR"
        )


def test_manual_cost_assumption_rejects_ps_revenue():
    with pytest.raises(ValidationError):
        ManualCostAssumption(
            name="Bad", amount_per_month=1.0, line_type="PS_REVENUE"
        )


def test_manual_cost_assumption_rejects_unknown_line_type():
    with pytest.raises(ValidationError):
        ManualCostAssumption(
            name="X", amount_per_month=1.0, line_type="NOT_A_REAL_TYPE"
        )


def test_manual_cost_assumption_rejects_bad_month_format():
    """starts_month must be YYYY-MM."""
    with pytest.raises(ValidationError):
        ManualCostAssumption(
            name="X", amount_per_month=1.0, line_type="PAYROLL",
            starts_month="May-2026"
        )


def test_manual_cost_assumption_rejects_bad_month_separator():
    with pytest.raises(ValidationError):
        ManualCostAssumption(
            name="X", amount_per_month=1.0, line_type="PAYROLL",
            starts_month="2026/05"
        )


def test_manual_cost_assumption_rejects_invalid_month_number():
    """Month 13 is not a real month."""
    with pytest.raises(ValidationError):
        ManualCostAssumption(
            name="X", amount_per_month=1.0, line_type="PAYROLL",
            starts_month="2026-13"
        )


# RecurringRevenueAssumption (RevenueLineType-restricted) --------------

def test_recurring_revenue_assumption_minimal():
    r = RecurringRevenueAssumption(
        name="New customer Acme — MRR",
        amount_per_month=8000.0,
        line_type="MRR",
    )
    assert r.name == "New customer Acme — MRR"
    assert r.amount_per_month == 8000.0
    assert r.line_type == "MRR"


def test_recurring_revenue_assumption_with_bounds():
    r = RecurringRevenueAssumption(
        name="Pilot deal — Q3 only",
        amount_per_month=12000.0,
        line_type="WHOLESALE",
        starts_month="2026-07",
        ends_month="2026-09",
    )
    assert r.starts_month == "2026-07"
    assert r.ends_month == "2026-09"


def test_recurring_revenue_assumption_other_revenue_line_type():
    """OTHER_REVENUE is the home for recurring revenue not modeled by
    rolling-3-month logic."""
    r = RecurringRevenueAssumption(
        name="Sponsorship deal",
        amount_per_month=2500.0,
        line_type="OTHER_REVENUE",
    )
    assert r.line_type == "OTHER_REVENUE"


def test_recurring_revenue_assumption_is_frozen():
    r = RecurringRevenueAssumption(name="X", amount_per_month=1.0, line_type="MRR")
    with pytest.raises(ValidationError):
        r.amount_per_month = 2.0


def test_recurring_revenue_assumption_rejects_cost_line_type():
    """CRITICAL: revenue assumption cannot accept a cost line type."""
    with pytest.raises(ValidationError):
        RecurringRevenueAssumption(
            name="Bad config", amount_per_month=1.0, line_type="PAYROLL"
        )


def test_recurring_revenue_assumption_rejects_rent():
    with pytest.raises(ValidationError):
        RecurringRevenueAssumption(
            name="Bad", amount_per_month=1.0, line_type="RENT"
        )


def test_recurring_revenue_assumption_rejects_ps_cogs():
    with pytest.raises(ValidationError):
        RecurringRevenueAssumption(
            name="Bad", amount_per_month=1.0, line_type="PS_COGS"
        )


def test_recurring_revenue_assumption_rejects_bad_month_format():
    with pytest.raises(ValidationError):
        RecurringRevenueAssumption(
            name="X", amount_per_month=1.0, line_type="MRR",
            starts_month="2026-5"  # Missing leading zero
        )


# ProjectionInputs — strict length 3 + month format -------------------

def _make_minimal_inputs(**overrides):
    """Helper: build minimal-but-valid ProjectionInputs."""
    base = dict(
        starting_cash=100000.0,
        projection_start_month="2026-05",
        ps_revenue_last_3_months=[60000.0, 60000.0, 60000.0],
        fs_revenue_last_3_months=[30000.0, 30000.0, 30000.0],
        ps_gross_profit_last_3_months=[40000.0, 40000.0, 40000.0],
        fs_gross_profit_last_3_months=[12000.0, 12000.0, 12000.0],
    )
    base.update(overrides)
    return ProjectionInputs(**base)


def test_projection_inputs_minimal_constructs():
    inputs = _make_minimal_inputs()
    assert inputs.horizon_weeks == 13  # default updated to 13 weeks ~ 90 days
    assert inputs.basis == "accrual"


def test_projection_inputs_defaults_no_manual_assumptions():
    inputs = _make_minimal_inputs()
    assert inputs.manual_cost_assumptions == []
    assert inputs.manual_recurring_revenue == []
    assert inputs.sales_modifier == 1.0
    assert inputs.cost_modifier == 1.0
    assert inputs.weekly_timing_overrides == {}


def test_projection_inputs_accepts_manual_cost_assumptions():
    cost = ManualCostAssumption(
        name="Payroll", amount_per_month=55000.0, line_type="PAYROLL"
    )
    inputs = _make_minimal_inputs(manual_cost_assumptions=[cost])
    assert len(inputs.manual_cost_assumptions) == 1
    assert inputs.manual_cost_assumptions[0].name == "Payroll"


def test_projection_inputs_accepts_manual_recurring_revenue():
    rev = RecurringRevenueAssumption(
        name="New MRR — June", amount_per_month=8000.0, line_type="MRR"
    )
    inputs = _make_minimal_inputs(manual_recurring_revenue=[rev])
    assert len(inputs.manual_recurring_revenue) == 1
    assert inputs.manual_recurring_revenue[0].amount_per_month == 8000.0


def test_projection_inputs_accepts_modifiers():
    """Sales/cost modifiers from workbook cells Q2/T2."""
    inputs = _make_minimal_inputs(sales_modifier=1.10, cost_modifier=0.95)
    assert inputs.sales_modifier == 1.10
    assert inputs.cost_modifier == 0.95


def test_projection_inputs_accepts_weekly_timing_overrides():
    """Per-line weekly timing overrides should accept any line type."""
    overrides = {
        "MRR": WeeklyDistribution(
            week1_pct=0.0, week2_pct=1.0, week3_pct=0.0, week4_pct=0.0
        ),
        "RENT": WeeklyDistribution(
            week1_pct=1.0, week2_pct=0.0, week3_pct=0.0, week4_pct=0.0
        ),
        "PAYROLL": WeeklyDistribution(
            week1_pct=0.5, week2_pct=0.0, week3_pct=0.5, week4_pct=0.0
        ),
    }
    inputs = _make_minimal_inputs(weekly_timing_overrides=overrides)
    assert inputs.weekly_timing_overrides["MRR"].week2_pct == 1.0
    assert inputs.weekly_timing_overrides["RENT"].week1_pct == 1.0
    assert inputs.weekly_timing_overrides["PAYROLL"].week1_pct == 0.5


def test_projection_inputs_is_frozen():
    inputs = _make_minimal_inputs()
    with pytest.raises(ValidationError):
        inputs.starting_cash = 999.0


# Strict length 3 enforced at MODEL level -------------------------------

def test_projection_inputs_rejects_short_ps_history():
    with pytest.raises(ValidationError):
        _make_minimal_inputs(ps_revenue_last_3_months=[60000.0, 60000.0])


def test_projection_inputs_rejects_long_ps_history():
    with pytest.raises(ValidationError):
        _make_minimal_inputs(
            ps_revenue_last_3_months=[60000.0, 60000.0, 60000.0, 60000.0]
        )


def test_projection_inputs_rejects_empty_fs_history():
    with pytest.raises(ValidationError):
        _make_minimal_inputs(fs_revenue_last_3_months=[])


def test_projection_inputs_rejects_short_ps_gp():
    with pytest.raises(ValidationError):
        _make_minimal_inputs(ps_gross_profit_last_3_months=[40000.0])


def test_projection_inputs_rejects_short_fs_gp():
    with pytest.raises(ValidationError):
        _make_minimal_inputs(fs_gross_profit_last_3_months=[12000.0, 12000.0])


# YYYY-MM format enforced at MODEL level --------------------------------

def test_projection_inputs_rejects_bad_start_month_format():
    """projection_start_month must be YYYY-MM."""
    with pytest.raises(ValidationError):
        _make_minimal_inputs(projection_start_month="May-2026")


def test_projection_inputs_rejects_start_month_with_slash():
    with pytest.raises(ValidationError):
        _make_minimal_inputs(projection_start_month="2026/05")


def test_projection_inputs_rejects_start_month_invalid_month_number():
    """Month 13 should be rejected by the regex."""
    with pytest.raises(ValidationError):
        _make_minimal_inputs(projection_start_month="2026-13")


def test_projection_inputs_rejects_start_month_no_leading_zero():
    """Single-digit months without leading zero should be rejected."""
    with pytest.raises(ValidationError):
        _make_minimal_inputs(projection_start_month="2026-5")


def test_projection_inputs_accepts_valid_month_formats():
    """Valid YYYY-MM strings construct successfully."""
    for valid in ["2026-01", "2026-05", "2026-12", "2025-09"]:
        inputs = _make_minimal_inputs(projection_start_month=valid)
        assert inputs.projection_start_month == valid
