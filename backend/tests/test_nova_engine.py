"""Engine-layer unit tests.

Exercises project_cash_flow end-to-end with hand-built ProjectionInputs.
No Mongo, no env vars, no network. The engine_e2e tests cover real-data
self-consistency.
"""
from datetime import date, datetime, timezone
import pytest

from services.nova_engine.engine import (
    _compute_inputs_hash,
    _is_active,
    _next_n_months,
    _resolve_mrr,
    project_cash_flow,
)
from services.nova_engine.models import (
    InsufficientHistoryError,
    LINE_TIMING_DEFAULTS,
    ManualCostAssumption,
    Override,
    ProjectionInputs,
    ProjectionOutput,
    RecurringRevenueAssumption,
    WeeklyDistribution,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _baseline_inputs(**overrides) -> ProjectionInputs:
    """Standard test inputs.

    PS revenue: $10K/mo (mean of [10K, 10K, 10K])
    FS revenue: $5K/mo
    PS GP: $7K/mo → COGS ratio = 0.30
    FS GP: $3K/mo → COGS ratio = 0.40

    Includes a zero-value manual MRR assumption to neutralize MRR.
    """
    base = dict(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        basis="accrual",
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        mrr_last_year_same_months={},
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="test neutralize MRR",
                amount_per_month=0.0,
                line_type="MRR",
                starts_month="2026-05",
                ends_month="2026-12",
            ),
        ],
    )
    base.update(overrides)
    return ProjectionInputs(**base)


# ---------------------------------------------------------------------------
# _is_active
# ---------------------------------------------------------------------------

def test_is_active_open_ended():
    a = ManualCostAssumption(
        name="test", amount_per_month=100.0, line_type="PAYROLL",
        starts_month=None, ends_month=None,
    )
    assert _is_active(a, "2026-05")
    assert _is_active(a, "2030-12")


def test_is_active_within_range():
    a = ManualCostAssumption(
        name="test", amount_per_month=100.0, line_type="PAYROLL",
        starts_month="2026-05", ends_month="2026-08",
    )
    assert _is_active(a, "2026-05")
    assert _is_active(a, "2026-06")
    assert _is_active(a, "2026-08")


def test_is_active_before_starts():
    a = ManualCostAssumption(
        name="test", amount_per_month=100.0, line_type="PAYROLL",
        starts_month="2026-05", ends_month=None,
    )
    assert not _is_active(a, "2026-04")
    assert _is_active(a, "2026-05")


def test_is_active_after_ends():
    a = ManualCostAssumption(
        name="test", amount_per_month=100.0, line_type="PAYROLL",
        starts_month=None, ends_month="2026-08",
    )
    assert _is_active(a, "2026-08")
    assert not _is_active(a, "2026-09")


# ---------------------------------------------------------------------------
# _resolve_mrr (additive rule)
# ---------------------------------------------------------------------------

def test_resolve_mrr_base_only():
    inp = _baseline_inputs(
        mrr_last_year_same_months={"2026-05": 8000.0},
        manual_recurring_revenue=[
            # Zero-value neutralizer covers other months
            RecurringRevenueAssumption(
                name="neutralize", amount_per_month=0.0, line_type="MRR",
                starts_month="2026-05", ends_month="2026-12",
            ),
        ],
    )
    assert _resolve_mrr("2026-05", inp) == 8000.0


def test_resolve_mrr_manual_only():
    inp = _baseline_inputs(
        mrr_last_year_same_months={},  # no historical
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="new referral", amount_per_month=2500.0, line_type="MRR",
                starts_month="2026-05", ends_month="2026-12",
            ),
        ],
    )
    assert _resolve_mrr("2026-05", inp) == 2500.0


def test_resolve_mrr_additive():
    """Critical: historical base + manual addition, NOT replacement."""
    inp = _baseline_inputs(
        mrr_last_year_same_months={"2026-05": 8000.0},
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="new referral", amount_per_month=2500.0, line_type="MRR",
                starts_month="2026-05", ends_month="2026-12",
            ),
        ],
    )
    # 8000 (historical) + 2500 (manual) = 10500
    assert _resolve_mrr("2026-05", inp) == 10500.0


def test_resolve_mrr_multiple_manual_assumptions_sum():
    inp = _baseline_inputs(
        mrr_last_year_same_months={"2026-05": 8000.0},
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="referral A", amount_per_month=2500.0, line_type="MRR",
                starts_month="2026-05", ends_month="2026-12",
            ),
            RecurringRevenueAssumption(
                name="referral B", amount_per_month=1500.0, line_type="MRR",
                starts_month="2026-05", ends_month="2026-12",
            ),
        ],
    )
    # 8000 + 2500 + 1500 = 12000
    assert _resolve_mrr("2026-05", inp) == 12000.0


def test_resolve_mrr_inactive_manual_excluded():
    """Manual MRR outside its active window is not added."""
    inp = _baseline_inputs(
        mrr_last_year_same_months={"2026-05": 8000.0},
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="future MRR", amount_per_month=5000.0, line_type="MRR",
                starts_month="2026-09", ends_month="2026-12",  # not active in May
            ),
        ],
    )
    assert _resolve_mrr("2026-05", inp) == 8000.0


def test_resolve_mrr_raises_when_both_missing():
    """Strict rule: no historical AND no active manual MRR → raise."""
    inp = ProjectionInputs(
        starting_cash=0.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        ps_revenue_last_3_months=[1.0, 1.0, 1.0],
        fs_revenue_last_3_months=[1.0, 1.0, 1.0],
        ps_gross_profit_last_3_months=[0.5, 0.5, 0.5],
        fs_gross_profit_last_3_months=[0.5, 0.5, 0.5],
        mrr_last_year_same_months={},
        manual_recurring_revenue=[],  # no MRR safety net
    )
    with pytest.raises(InsufficientHistoryError, match="mrr"):
        _resolve_mrr("2026-05", inp)


# ---------------------------------------------------------------------------
# inputs_hash determinism
# ---------------------------------------------------------------------------

def test_inputs_hash_same_inputs_same_hash():
    inp1 = _baseline_inputs()
    inp2 = _baseline_inputs()
    assert _compute_inputs_hash(inp1) == _compute_inputs_hash(inp2)


def test_inputs_hash_different_starting_cash_different_hash():
    inp1 = _baseline_inputs(starting_cash=50000.0)
    inp2 = _baseline_inputs(starting_cash=60000.0)
    assert _compute_inputs_hash(inp1) != _compute_inputs_hash(inp2)


def test_inputs_hash_dict_order_insensitive():
    """Two inputs with logically same MRR dict but different insertion
    order must produce the same hash."""
    inp1 = _baseline_inputs(
        mrr_last_year_same_months={"2026-05": 8000.0, "2026-06": 9000.0},
    )
    inp2 = _baseline_inputs(
        mrr_last_year_same_months={"2026-06": 9000.0, "2026-05": 8000.0},
    )
    assert _compute_inputs_hash(inp1) == _compute_inputs_hash(inp2)


# ---------------------------------------------------------------------------
# Full project_cash_flow — composition correctness
# ---------------------------------------------------------------------------

def test_project_cash_flow_returns_projection_output():
    inputs = _baseline_inputs()
    output = project_cash_flow(inputs)
    assert isinstance(output, ProjectionOutput)
    assert isinstance(output.computed_at, datetime)
    assert output.computed_at.tzinfo is not None  # UTC-aware
    assert len(output.weeks) == inputs.horizon_weeks


def test_project_cash_flow_first_4_weeks_match_first_forecast_month():
    """sum(weeks[0..3].ps_revenue) == projected first-month PS revenue.

    With sales_modifier=1.0 and mean_ps_rev = $10K/mo, the engine should
    distribute exactly $10K of PS revenue across weeks 1-4.
    """
    inputs = _baseline_inputs()  # sales_modifier=1.0, cost_modifier=1.0
    output = project_cash_flow(inputs)

    week_1_to_4_ps = sum(output.weeks[i].ps_revenue for i in range(4))
    assert week_1_to_4_ps == pytest.approx(10000.0, abs=0.01)


def test_project_cash_flow_first_4_weeks_match_first_forecast_month_fs():
    inputs = _baseline_inputs()
    output = project_cash_flow(inputs)

    week_1_to_4_fs = sum(output.weeks[i].fs_revenue for i in range(4))
    assert week_1_to_4_fs == pytest.approx(5000.0, abs=0.01)


def test_project_cash_flow_ps_cogs_derived_from_gp_ratio():
    """PS COGS = PS revenue * (1 - GP ratio). With $10K rev and $7K GP,
    COGS ratio is 0.30, so projected month-1 PS COGS is $3K.
    """
    inputs = _baseline_inputs()
    output = project_cash_flow(inputs)

    week_1_to_4_ps_cogs = sum(output.weeks[i].ps_cogs for i in range(4))
    assert week_1_to_4_ps_cogs == pytest.approx(3000.0, abs=0.01)


def test_project_cash_flow_sales_modifier_scales_revenue():
    inputs = _baseline_inputs(sales_modifier=1.20)
    output = project_cash_flow(inputs)

    # PS revenue scaled by 1.20 → $12K/mo, distributed across 4 weeks
    week_1_to_4_ps = sum(output.weeks[i].ps_revenue for i in range(4))
    assert week_1_to_4_ps == pytest.approx(12000.0, abs=0.01)


def test_project_cash_flow_cost_modifier_scales_cogs():
    inputs = _baseline_inputs(cost_modifier=0.80)
    output = project_cash_flow(inputs)

    # PS COGS = $10K * 0.30 * 0.80 = $2400 in first month
    week_1_to_4_ps_cogs = sum(output.weeks[i].ps_cogs for i in range(4))
    assert week_1_to_4_ps_cogs == pytest.approx(2400.0, abs=0.01)


def test_project_cash_flow_manual_cost_assumption_applied():
    """A $5K/month payroll assumption should appear in weekly payroll totals."""
    inputs = _baseline_inputs(
        manual_cost_assumptions=[
            ManualCostAssumption(
                name="senior eng",
                amount_per_month=5000.0,
                line_type="PAYROLL",
                starts_month="2026-05",
                ends_month=None,
            ),
        ],
    )
    output = project_cash_flow(inputs)

    week_1_to_4_payroll = sum(output.weeks[i].payroll for i in range(4))
    assert week_1_to_4_payroll == pytest.approx(5000.0, abs=0.01)


def test_project_cash_flow_manual_cost_weekly_dist_affects_distribution():
    inputs = _baseline_inputs(
        manual_cost_assumptions=[
            ManualCostAssumption(
                name="one-shot admin",
                amount_per_month=4000.0,
                line_type="ADMIN_EXPENSES",
                starts_month="2026-05",
                weekly_dist=WeeklyDistribution(
                    week1_pct=1.0,
                    week2_pct=0.0,
                    week3_pct=0.0,
                    week4_pct=0.0,
                ),
            ),
        ],
    )
    output = project_cash_flow(inputs)

    assert output.weeks[0].administrative_expenses == pytest.approx(4000.0, abs=0.01)
    assert output.weeks[1].administrative_expenses == pytest.approx(0.0, abs=0.01)
    assert output.weeks[2].administrative_expenses == pytest.approx(0.0, abs=0.01)
    assert output.weeks[3].administrative_expenses == pytest.approx(0.0, abs=0.01)


def test_project_cash_flow_manual_recurring_revenue_non_mrr():
    """A non-MRR RecurringRevenueAssumption (e.g., WHOLESALE) should add
    to that line's monthly total."""
    inputs = _baseline_inputs(
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="test neutralize MRR",
                amount_per_month=0.0,
                line_type="MRR",
                starts_month="2026-05",
                ends_month="2026-12",
            ),
            RecurringRevenueAssumption(
                name="recurring wholesale",
                amount_per_month=4000.0,
                line_type="WHOLESALE",
                starts_month="2026-05",
                ends_month=None,
            ),
        ],
    )
    output = project_cash_flow(inputs)

    week_1_to_4_wholesale = sum(output.weeks[i].wholesale_revenue for i in range(4))
    assert week_1_to_4_wholesale == pytest.approx(4000.0, abs=0.01)


def test_project_cash_flow_manual_revenue_weekly_dist_affects_distribution():
    inputs = _baseline_inputs(
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="MRR on week 3",
                amount_per_month=4000.0,
                line_type="MRR",
                starts_month="2026-05",
                ends_month="2026-12",
                weekly_dist=WeeklyDistribution(
                    week1_pct=0.0,
                    week2_pct=0.0,
                    week3_pct=1.0,
                    week4_pct=0.0,
                ),
            ),
        ],
    )
    output = project_cash_flow(inputs)

    assert output.weeks[0].mrr_revenue == pytest.approx(0.0, abs=0.01)
    assert output.weeks[1].mrr_revenue == pytest.approx(0.0, abs=0.01)
    assert output.weeks[2].mrr_revenue == pytest.approx(4000.0, abs=0.01)
    assert output.weeks[3].mrr_revenue == pytest.approx(0.0, abs=0.01)


def test_project_cash_flow_weekly_timing_overrides_affect_distribution():
    inputs = _baseline_inputs(
        weekly_timing_overrides={
            "PS_REVENUE": WeeklyDistribution(
                week1_pct=0.0,
                week2_pct=1.0,
                week3_pct=0.0,
                week4_pct=0.0,
            )
        }
    )
    output = project_cash_flow(inputs)

    assert output.weeks[0].ps_revenue == pytest.approx(0.0, abs=0.01)
    assert output.weeks[1].ps_revenue == pytest.approx(10000.0, abs=0.01)
    assert output.weeks[2].ps_revenue == pytest.approx(0.0, abs=0.01)
    assert output.weeks[3].ps_revenue == pytest.approx(0.0, abs=0.01)


def test_project_cash_flow_rent_maps_to_admin_not_payroll():
    inputs = _baseline_inputs(
        manual_cost_assumptions=[
            ManualCostAssumption(
                name="Office rent",
                amount_per_month=4000.0,
                line_type="RENT",
                starts_month="2026-05",
            ),
        ],
    )
    output = project_cash_flow(inputs)

    week_1_to_4_payroll = sum(output.weeks[i].payroll for i in range(4))
    week_1_to_4_admin = sum(output.weeks[i].administrative_expenses for i in range(4))
    assert week_1_to_4_payroll == pytest.approx(0.0, abs=0.01)
    assert week_1_to_4_admin == pytest.approx(4000.0, abs=0.01)
    assert output.weeks[2].administrative_expenses == pytest.approx(4000.0, abs=0.01)


def test_project_cash_flow_ending_cash_rolls_forward():
    """ending_cash[i] = ending_cash[i-1] + net_cash_flow[i].

    With $50K starting cash, $10K PS rev/mo + $5K FS rev/mo,
    $3K PS COGS/mo + $2K FS COGS/mo = $10K net per month
    distributed evenly across 4 weeks = $2.5K net per week.

    Week 1 ending: 50000 + 2500 = 52500
    """
    inputs = _baseline_inputs()  # 50K start, no payroll/admin
    output = project_cash_flow(inputs)

    # First week ending cash = starting + net of week 1
    # PS rev/4 - PS COGS/4 + FS rev/4 - FS COGS/4
    # = 2500 - 750 + 1250 - 500 = 2500
    expected_week_1_ending = 50000.0 + 2500.0
    assert output.weeks[0].ending_cash == pytest.approx(expected_week_1_ending, abs=0.01)


def test_project_cash_flow_overrides_applied():
    """An Override(new_cost) should appear in the relevant week."""
    inputs = _baseline_inputs(
        overrides=[
            Override(
                type="new_cost",
                target="OTHER_COSTS",
                week_number=2,
                amount=15000.0,
            ),
        ],
    )
    output = project_cash_flow(inputs)

    # Other costs in week 2 should be 15000 (no other contributors)
    assert output.weeks[1].other_costs == pytest.approx(15000.0, abs=0.01)


def test_admin_expenses_naming_round_trip():
    """Confirms the ADMIN_EXPENSES <-> ADMINISTRATIVE_EXPENSES naming
    split is consistent: a ManualCostAssumption(line_type='ADMIN_EXPENSES')
    and an Override(target='ADMINISTRATIVE_EXPENSES') both write to
    WeekRow.administrative_expenses.

    LINE_TIMING_DEFAULTS["ADMIN_EXPENSES"] is even quarters (0.25 each),
    so $4000/month distributes to $1000/week.
    """
    inputs = ProjectionInputs(
        starting_cash=10000.0,
        projection_start_month="2026-05",
        horizon_weeks=4,
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        manual_cost_assumptions=[
            ManualCostAssumption(
                name="admin baseline",
                amount_per_month=4000.0,
                line_type="ADMIN_EXPENSES",  # CostLineType key
                starts_month="2026-05",
                ends_month=None,
            ),
        ],
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="MRR neutralizer",
                amount_per_month=0.0,
                line_type="MRR",
                starts_month="2026-05",
                ends_month="2026-12",
            ),
        ],
        overrides=[
            Override(
                type="new_cost",
                target="ADMINISTRATIVE_EXPENSES",  # OverrideTarget key
                week_number=2,
                amount=1000.0,
            ),
        ],
    )

    output = project_cash_flow(inputs)

    # Week 1: only the manual cost assumption ($4000/mo / 4 weeks = $1000)
    assert output.weeks[0].administrative_expenses == pytest.approx(1000.0, abs=0.01)
    # Week 2: manual cost ($1000) + override ($1000) = $2000
    assert output.weeks[1].administrative_expenses == pytest.approx(2000.0, abs=0.01)
    # Week 3 & 4: only manual cost
    assert output.weeks[2].administrative_expenses == pytest.approx(1000.0, abs=0.01)
    assert output.weeks[3].administrative_expenses == pytest.approx(1000.0, abs=0.01)


def test_project_cash_flow_deterministic():
    """Running twice gives identical weeks (timestamps differ, weeks don't)."""
    inputs = _baseline_inputs()
    out1 = project_cash_flow(inputs)
    out2 = project_cash_flow(inputs)

    assert out1.inputs_hash == out2.inputs_hash
    assert len(out1.weeks) == len(out2.weeks)
    for w1, w2 in zip(out1.weeks, out2.weeks):
        assert w1.ps_revenue == w2.ps_revenue
        assert w1.fs_revenue == w2.fs_revenue
        assert w1.ps_cogs == w2.ps_cogs
        assert w1.fs_cogs == w2.fs_cogs
        assert w1.ending_cash == w2.ending_cash


def test_project_cash_flow_horizon_13_weeks_exactly():
    inputs = _baseline_inputs(horizon_weeks=13)
    output = project_cash_flow(inputs)
    assert len(output.weeks) == 13


def test_project_cash_flow_raises_when_mrr_unresolvable():
    """If MRR can't be resolved for any forecast month, the engine
    propagates InsufficientHistoryError."""
    inputs = ProjectionInputs(
        starting_cash=0.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        mrr_last_year_same_months={},
        manual_recurring_revenue=[],  # no MRR safety net
    )
    with pytest.raises(InsufficientHistoryError, match="mrr"):
        project_cash_flow(inputs)


# ---------------------------------------------------------------------------
# Summary fields derived in Step 7
# ---------------------------------------------------------------------------

def test_project_cash_flow_starting_cash_passes_through():
    inputs = _baseline_inputs(starting_cash=75000.0)
    output = project_cash_flow(inputs)
    assert output.starting_cash == 75000.0


def test_project_cash_flow_ending_cash_matches_last_week():
    inputs = _baseline_inputs()
    output = project_cash_flow(inputs)
    assert output.ending_cash == output.weeks[-1].ending_cash


def test_project_cash_flow_net_change_matches_revenue_minus_costs():
    inputs = _baseline_inputs()
    output = project_cash_flow(inputs)
    assert output.net_change == pytest.approx(
        output.total_revenue - output.total_costs, abs=0.01
    )


def test_project_cash_flow_other_revenue_projects_to_weekrow():
    """OTHER_REVENUE = mean of other_revenue_last_3_months * sales_modifier,
    distributed via LINE_TIMING_DEFAULTS['OTHER_REVENUE'] (even quarters
    today). Pre-Bug-A this bucket was hardcoded to 0.0 in engine.py and
    real OTHER_REVENUE GL activity never reached the projection."""
    inputs = _baseline_inputs(
        other_revenue_last_3_months=[2000.0, 2500.0, 3000.0],
    )
    output = project_cash_flow(inputs)

    # mean = 2500/mo. First 4 weeks should sum to one month of OTHER.
    first_month_other = sum(output.weeks[i].other_revenue for i in range(4))
    assert first_month_other == pytest.approx(2500.0, abs=0.01)

    # And total_revenue must include OTHER (PS=10K + FS=5K + OTHER=2.5K
    # = 17.5K per month, MRR neutralized in _baseline_inputs).
    first_month_total = sum(output.weeks[i].total_revenue for i in range(4))
    assert first_month_total == pytest.approx(17500.0, abs=0.05)


def test_project_cash_flow_other_revenue_default_is_zero():
    """Callers that omit other_revenue_last_3_months get the [0,0,0]
    default — engine produces zero OTHER contribution, preserving
    pre-Bug-A behavior for inputs built before this field existed."""
    inputs = _baseline_inputs()  # no other_revenue_last_3_months override
    output = project_cash_flow(inputs)

    first_month_other = sum(output.weeks[i].other_revenue for i in range(4))
    assert first_month_other == pytest.approx(0.0, abs=0.01)


def test_project_cash_flow_other_revenue_honors_sales_modifier():
    inputs = _baseline_inputs(
        other_revenue_last_3_months=[1000.0, 1000.0, 1000.0],
        sales_modifier=1.50,
    )
    output = project_cash_flow(inputs)

    first_month_other = sum(output.weeks[i].other_revenue for i in range(4))
    assert first_month_other == pytest.approx(1500.0, abs=0.01)


def test_project_cash_flow_minimum_cash_is_actual_minimum():
    """minimum_cash should be the smallest ending_cash across all weeks,
    and minimum_cash_week the 1-based week number where it occurs."""
    # Force a dip in week 2 via a large new_cost override.
    inputs = _baseline_inputs(
        overrides=[
            Override(
                type="new_cost",
                target="OTHER_COSTS",
                week_number=2,
                amount=100000.0,
            ),
        ],
    )
    output = project_cash_flow(inputs)

    expected_min = min(w.ending_cash for w in output.weeks)
    assert output.minimum_cash == pytest.approx(expected_min, abs=0.01)
    # 1-based week index of the dip
    expected_week = next(
        w.week_number for w in output.weeks if w.ending_cash == expected_min
    )
    assert output.minimum_cash_week == expected_week


# ---------------------------------------------------------------------------
# cash_receipt_timing — engine cash-shift semantics
# ---------------------------------------------------------------------------
#
# Per the V1 audit, "Add $10K MRR starting next month" was silently
# choosing whether cash hit the recognition month. The engine now reads
# cash_receipt_timing off the assumption and routes the entry into
# either the recognition month (same_month, default) or the following
# month (next_month). Tests use synthetic monthly amounts so the
# expected weekly totals are derived from the fixture itself.


def _projection_with_single_recurring_cost(
    *,
    cash_receipt_timing: str,
    monthly_amount: float = 8000.0,
) -> ProjectionOutput:
    """Run a 13-week projection whose only manual assumption is a
    PAYROLL cost active starting in the recognition month
    (projection_start_month). Returns the projection so a test can
    inspect which weeks carry the cost."""
    inputs = _baseline_inputs(
        manual_cost_assumptions=[
            ManualCostAssumption(
                name="single payroll line",
                amount_per_month=monthly_amount,
                line_type="PAYROLL",
                starts_month="2026-05",  # matches default projection_start_month
                ends_month="2026-05",
                cash_receipt_timing=cash_receipt_timing,
            ),
        ],
    )
    return project_cash_flow(inputs)


def test_same_month_cash_lands_in_recognition_month_weeks():
    """Default cash_receipt_timing='same_month' preserves prior engine
    math: the assumption hits weeks 1-4 (the recognition month)."""
    out = _projection_with_single_recurring_cost(cash_receipt_timing="same_month")

    payroll_first_4 = sum(out.weeks[i].payroll for i in range(4))
    payroll_weeks_5_to_8 = sum(out.weeks[i].payroll for i in range(4, 8))

    assert payroll_first_4 > 0
    # Anything outside the recognition month must be zero contribution
    # from this assumption (baseline payroll is 0 in _baseline_inputs).
    assert payroll_weeks_5_to_8 == 0


def test_next_month_cash_lands_in_following_month_weeks():
    """cash_receipt_timing='next_month' shifts the cash impact one
    month later: the assumption hits weeks 5-8, NOT weeks 1-4."""
    out = _projection_with_single_recurring_cost(cash_receipt_timing="next_month")

    payroll_first_4 = sum(out.weeks[i].payroll for i in range(4))
    payroll_weeks_5_to_8 = sum(out.weeks[i].payroll for i in range(4, 8))

    assert payroll_first_4 == 0, (
        "next_month cash timing must NOT land in the recognition month; "
        "this is the audited regression."
    )
    assert payroll_weeks_5_to_8 > 0


def test_next_month_total_equals_same_month_total_when_both_within_horizon():
    """Same monthly amount, same active range — only the placement
    differs. The aggregate cost across the horizon must match."""
    monthly_amount = 4321.0  # synthetic, derived from fixture

    same = _projection_with_single_recurring_cost(
        cash_receipt_timing="same_month",
        monthly_amount=monthly_amount,
    )
    nxt = _projection_with_single_recurring_cost(
        cash_receipt_timing="next_month",
        monthly_amount=monthly_amount,
    )

    same_total = sum(w.payroll for w in same.weeks)
    nxt_total = sum(w.payroll for w in nxt.weeks)

    assert same_total == pytest.approx(nxt_total)
    assert same_total == pytest.approx(monthly_amount)


def test_next_month_cash_falling_past_horizon_drops_silently():
    """A single-month assumption ending in the LAST forecast month
    cannot land its cash anywhere if shifted by one month — the cash
    impact silently drops past horizon. Mirrors the existing
    apply_overrides shift-past-horizon convention."""
    # Build a 13-week horizon (3 forecast months: 2026-05, 06, 07)
    # and place the assumption only in 2026-07 — the last forecast
    # month. Shifting cash to 2026-08 puts it out of horizon.
    inputs = _baseline_inputs(
        manual_cost_assumptions=[
            ManualCostAssumption(
                name="last-month payroll",
                amount_per_month=9999.0,
                line_type="PAYROLL",
                starts_month="2026-07",
                ends_month="2026-07",
                cash_receipt_timing="next_month",
            ),
        ],
    )
    out = project_cash_flow(inputs)

    payroll_total = sum(w.payroll for w in out.weeks)
    assert payroll_total == 0, (
        "next_month shift past the last forecast month must silently drop, "
        "matching the apply_overrides shift-past-horizon convention."
    )


# ---------------------------------------------------------------------------
# Model-level invariants for cash_receipt_timing / cash_receipt_date
# ---------------------------------------------------------------------------


def test_specific_date_requires_cash_receipt_date():
    with pytest.raises(Exception):
        ManualCostAssumption(
            name="bad",
            amount_per_month=100.0,
            line_type="PAYROLL",
            starts_month="2099-01",
            cash_receipt_timing="specific_date",
            cash_receipt_date=None,
        )


def test_cash_receipt_date_without_specific_date_is_rejected():
    with pytest.raises(Exception):
        RecurringRevenueAssumption(
            name="bad",
            amount_per_month=100.0,
            line_type="MRR",
            starts_month="2099-01",
            cash_receipt_timing="same_month",
            cash_receipt_date="2099-02-15",
        )


def test_default_cash_receipt_timing_is_same_month():
    """Backward-compat: callers that don't pass cash_receipt_timing get
    same_month, which preserves prior engine behavior."""
    a = ManualCostAssumption(
        name="legacy",
        amount_per_month=100.0,
        line_type="PAYROLL",
        starts_month="2099-01",
    )
    assert a.cash_receipt_timing == "same_month"
    assert a.cash_receipt_date is None
