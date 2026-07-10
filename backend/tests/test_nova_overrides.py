"""Tests for the post-distribution override layer.

Covers:
  - All four override types (new_cost, new_revenue, shift_payment, remove_payment)
  - Phase ordering (new -> shift -> remove)
  - Conflict detection (raises ValueError on conflicting shifts)
  - Idempotent duplicate removes
  - Delta-semantics removes (remove subtracts original amount, not whole cell)
  - Original-state anchoring (shifts don't move new_cost rows)
  - Out-of-horizon validation (week_number / source_week > horizon raises)
  - Out-of-horizon shifts drop silently
  - Recomputation of total_revenue, total_costs, net_cash_flow
  - Ending cash roll-forward correctness
  - TARGET_FIELD_MAP completeness
  - Pydantic-level validation per override type
"""
from datetime import date
import pytest
from pydantic import ValidationError

from services.nova_engine import overrides
from services.nova_engine.overrides import (
    TARGET_FIELD_MAP,
    apply_overrides,
)
from services.nova_engine.models import Override, OverrideTarget, WeekRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_week(week_number: int, **fields) -> WeekRow:
    """Construct a WeekRow with sensible defaults plus any overrides."""
    base = dict(
        week_number=week_number,
        week_starting=date(2026, 5, 1),
    )
    base.update(fields)
    return WeekRow(**base)


def _make_baseline(horizon: int = 4) -> list[WeekRow]:
    """A 4-week baseline with $1000 PS revenue and $200 payroll per week."""
    return [
        _make_week(i + 1, ps_revenue=1000.0, payroll=200.0)
        for i in range(horizon)
    ]


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------

def test_module_imports():
    assert hasattr(overrides, "apply_overrides")
    assert hasattr(overrides, "TARGET_FIELD_MAP")


def test_target_field_map_completeness():
    """Every OverrideTarget literal must have a TARGET_FIELD_MAP entry."""
    from typing import get_args
    target_literals = set(get_args(OverrideTarget))
    assert set(TARGET_FIELD_MAP.keys()) == target_literals


def test_target_field_map_values_are_weekrow_fields():
    """Every mapped field name must actually exist on WeekRow."""
    week = _make_week(1)
    for target, field_name in TARGET_FIELD_MAP.items():
        assert hasattr(week, field_name), (
            f"TARGET_FIELD_MAP[{target!r}] -> {field_name!r}, "
            f"but WeekRow has no such field"
        )


# ---------------------------------------------------------------------------
# Empty / no-op cases
# ---------------------------------------------------------------------------

def test_no_overrides_returns_recomputed_input():
    """With no overrides, output equals input but with totals recomputed."""
    baseline = _make_baseline()
    result = apply_overrides(baseline, [], starting_cash=10000.0)

    # ps_revenue and payroll preserved
    assert all(r.ps_revenue == 1000.0 for r in result)
    assert all(r.payroll == 200.0 for r in result)
    # totals recomputed from line items
    assert all(r.total_revenue == 1000.0 for r in result)
    assert all(r.total_costs == 200.0 for r in result)
    assert all(r.net_cash_flow == 800.0 for r in result)
    # ending cash rolls forward from starting_cash
    assert result[0].ending_cash == 10800.0
    assert result[1].ending_cash == 11600.0
    assert result[2].ending_cash == 12400.0
    assert result[3].ending_cash == 13200.0


def test_input_not_mutated():
    """Input weeks list and individual WeekRows must not be mutated."""
    baseline = _make_baseline()
    original_first_payroll = baseline[0].payroll

    overrides_list = [Override(
        type="new_cost", target="PAYROLL", week_number=1, amount=500.0
    )]
    apply_overrides(baseline, overrides_list, starting_cash=0.0)

    # Original WeekRow unchanged
    assert baseline[0].payroll == original_first_payroll


# ---------------------------------------------------------------------------
# new_cost / new_revenue
# ---------------------------------------------------------------------------

def test_new_cost_adds_to_target_week():
    baseline = _make_baseline()
    ov = Override(
        type="new_cost", target="PAYROLL", week_number=2, amount=9000.0
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    assert result[0].payroll == 200.0       # week 1 unchanged
    assert result[1].payroll == 9200.0      # week 2 += 9000
    assert result[2].payroll == 200.0       # week 3 unchanged
    assert result[1].total_costs == 9200.0  # total recomputed


def test_new_revenue_adds_to_target_week():
    baseline = _make_baseline()
    ov = Override(
        type="new_revenue", target="MRR", week_number=1, amount=8000.0
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    assert result[0].mrr_revenue == 8000.0
    assert result[0].total_revenue == 9000.0  # 1000 ps + 8000 mrr


def test_multiple_new_costs_same_week_sum():
    """Multiple new_cost overrides on the same target+week add up."""
    baseline = _make_baseline()
    ovs = [
        Override(type="new_cost", target="OTHER_COSTS", week_number=2, amount=1000.0),
        Override(type="new_cost", target="OTHER_COSTS", week_number=2, amount=2500.0),
    ]
    result = apply_overrides(baseline, ovs, starting_cash=0.0)

    assert result[1].other_costs == 3500.0


# ---------------------------------------------------------------------------
# shift_payment
# ---------------------------------------------------------------------------

def test_shift_payment_basic_forward():
    """Shift the original amount forward; source week loses it, dest gains it."""
    baseline = _make_baseline()
    ov = Override(
        type="shift_payment", target="PAYROLL", source_week=1, shift_weeks=2
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    assert result[0].payroll == 0.0        # week 1: original 200 removed
    assert result[2].payroll == 400.0      # week 3: 200 (orig) + 200 (shifted in)


def test_shift_payment_negative_shifts_backward():
    """Negative shift_weeks moves backward."""
    baseline = _make_baseline()
    ov = Override(
        type="shift_payment", target="PAYROLL", source_week=3, shift_weeks=-1
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    assert result[2].payroll == 0.0        # week 3: original 200 removed
    assert result[1].payroll == 400.0      # week 2: 200 (orig) + 200 (shifted in)


def test_shift_payment_outside_horizon_drops_silently():
    """Shift past the end of the horizon: original removed, nothing added."""
    baseline = _make_baseline(horizon=4)
    ov = Override(
        type="shift_payment", target="PAYROLL", source_week=3, shift_weeks=10
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    # Week 3 source removed
    assert result[2].payroll == 0.0
    # Other weeks untouched
    assert result[0].payroll == 200.0
    assert result[1].payroll == 200.0
    assert result[3].payroll == 200.0


def test_shift_payment_before_horizon_drops_silently():
    """Shift before week 1: original removed, nothing added."""
    baseline = _make_baseline(horizon=4)
    ov = Override(
        type="shift_payment", target="PAYROLL", source_week=2, shift_weeks=-5
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    assert result[1].payroll == 0.0  # original removed
    assert result[0].payroll == 200.0  # untouched


def test_shift_payment_idempotent_duplicate():
    """Same shift applied twice is the same as once."""
    baseline = _make_baseline()
    ov = Override(
        type="shift_payment", target="PAYROLL", source_week=1, shift_weeks=2
    )
    result = apply_overrides(baseline, [ov, ov], starting_cash=0.0)

    assert result[0].payroll == 0.0
    assert result[2].payroll == 400.0


def test_conflicting_shifts_raise():
    """Same target+source_week with different shift_weeks must raise."""
    baseline = _make_baseline()
    ovs = [
        Override(type="shift_payment", target="PAYROLL", source_week=1, shift_weeks=2),
        Override(type="shift_payment", target="PAYROLL", source_week=1, shift_weeks=3),
    ]
    with pytest.raises(ValueError, match="Conflicting shift_payment"):
        apply_overrides(baseline, ovs, starting_cash=0.0)


# ---------------------------------------------------------------------------
# remove_payment
# ---------------------------------------------------------------------------

def test_remove_payment_basic():
    """Remove zeroes out the source week's contribution."""
    baseline = _make_baseline()
    ov = Override(
        type="remove_payment", target="PAYROLL", source_week=2
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    assert result[1].payroll == 0.0
    # Other weeks untouched
    assert result[0].payroll == 200.0
    assert result[2].payroll == 200.0


def test_remove_payment_idempotent_duplicate():
    """Same remove applied twice equals once."""
    baseline = _make_baseline()
    ov = Override(
        type="remove_payment", target="PAYROLL", source_week=2
    )
    result = apply_overrides(baseline, [ov, ov], starting_cash=0.0)

    assert result[1].payroll == 0.0


def test_remove_payment_does_not_touch_independent_contributions():
    """CRITICAL — delta semantics. If new_cost added 9000 to week 2 payroll,
    and remove_payment targets the original week 2 payroll, only the
    original 200 is subtracted, not the 9000."""
    baseline = _make_baseline()
    ovs = [
        Override(type="new_cost", target="PAYROLL", week_number=2, amount=9000.0),
        Override(type="remove_payment", target="PAYROLL", source_week=2),
    ]
    result = apply_overrides(baseline, ovs, starting_cash=0.0)

    # Week 2: started 200, +9000 (new_cost), then -200 (remove of original)
    # = 9000 remaining (the new_cost contribution is preserved)
    assert result[1].payroll == 9000.0


def test_remove_after_shift_delta_semantics():
    """Remove wins over shift via delta semantics.

    Original week 1 payroll = 200. Shift it to week 3. Then remove it.
    Final state: original week 1 payroll is absent, and the shifted
    contribution at week 3 is also gone.
    """
    baseline = _make_baseline()
    ovs = [
        Override(type="shift_payment", target="PAYROLL", source_week=1, shift_weeks=2),
        Override(type="remove_payment", target="PAYROLL", source_week=1),
    ]
    result = apply_overrides(baseline, ovs, starting_cash=0.0)

    # Week 1: original 200 removed by shift (-200), then remove finds
    # destination at week 3, subtracts 200 there. Week 1 stays at 0.
    assert result[0].payroll == 0.0
    # Week 3: original 200 (was already there) + 200 (shifted in) - 200 (removed) = 200
    assert result[2].payroll == 200.0


# ---------------------------------------------------------------------------
# Original-state anchoring
# ---------------------------------------------------------------------------

def test_shift_does_not_move_new_cost_added_in_same_call():
    """A new_cost added to week 3 should NOT be moved by a
    shift_payment(source_week=3). The shift looks at the original
    baseline only."""
    baseline = _make_baseline(horizon=5)
    # Original week 3 payroll = 200.
    ovs = [
        Override(type="new_cost", target="PAYROLL", week_number=3, amount=9000.0),
        Override(type="shift_payment", target="PAYROLL", source_week=3, shift_weeks=2),
    ]
    result = apply_overrides(baseline, ovs, starting_cash=0.0)

    # Phase 1: week 3 payroll = 200 + 9000 = 9200
    # Phase 2: shift looks at ORIGINAL week 3 payroll (200), removes 200
    #          from week 3, adds 200 to week 5.
    # So week 3: 9200 - 200 = 9000 (new_cost preserved)
    # Week 5: 200 + 200 = 400
    assert result[2].payroll == 9000.0
    assert result[4].payroll == 400.0


# ---------------------------------------------------------------------------
# Recomputation
# ---------------------------------------------------------------------------

def test_total_revenue_recomputed_after_new_revenue():
    baseline = _make_baseline()
    ov = Override(
        type="new_revenue", target="WHOLESALE", week_number=1, amount=5000.0
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    assert result[0].wholesale_revenue == 5000.0
    assert result[0].total_revenue == 6000.0  # 1000 ps + 5000 wholesale


def test_total_costs_recomputed_after_new_cost():
    baseline = _make_baseline()
    ov = Override(
        type="new_cost", target="OTHER_COSTS", week_number=2, amount=3000.0
    )
    result = apply_overrides(baseline, [ov], starting_cash=0.0)

    assert result[1].total_costs == 3200.0  # 200 payroll + 3000 other


def test_net_cash_flow_recomputed():
    baseline = _make_baseline()
    ovs = [
        Override(type="new_revenue", target="MRR", week_number=1, amount=8000.0),
        Override(type="new_cost", target="OTHER_COSTS", week_number=1, amount=3000.0),
    ]
    result = apply_overrides(baseline, ovs, starting_cash=0.0)

    # Week 1: revenue = 1000 + 8000 = 9000, costs = 200 + 3000 = 3200
    # net = 9000 - 3200 = 5800
    assert result[0].net_cash_flow == 5800.0


def test_ending_cash_rolls_forward():
    """ending_cash[i] = starting_cash + sum(net_cash_flow[0..i])."""
    baseline = _make_baseline(horizon=3)
    # Each week: net = 1000 - 200 = 800
    result = apply_overrides(baseline, [], starting_cash=5000.0)

    assert result[0].ending_cash == 5800.0   # 5000 + 800
    assert result[1].ending_cash == 6600.0   # 5800 + 800
    assert result[2].ending_cash == 7400.0   # 6600 + 800


def test_ending_cash_respects_overrides():
    """A new_cost in week 2 should reduce ending_cash from week 2 onward."""
    baseline = _make_baseline(horizon=3)
    ov = Override(
        type="new_cost", target="OTHER_COSTS", week_number=2, amount=10000.0
    )
    result = apply_overrides(baseline, [ov], starting_cash=5000.0)

    assert result[0].ending_cash == 5800.0     # unchanged
    assert result[1].ending_cash == -3400.0    # 5800 + 800 - 10000
    assert result[2].ending_cash == -2600.0    # -3400 + 800


# ---------------------------------------------------------------------------
# Out-of-horizon validation (apply-level)
# ---------------------------------------------------------------------------

def test_new_cost_beyond_horizon_raises():
    baseline = _make_baseline(horizon=4)
    ov = Override(
        type="new_cost", target="PAYROLL", week_number=10, amount=500.0
    )
    with pytest.raises(ValueError, match="horizon is 4 weeks"):
        apply_overrides(baseline, [ov], starting_cash=0.0)


def test_shift_source_beyond_horizon_raises():
    baseline = _make_baseline(horizon=4)
    ov = Override(
        type="shift_payment", target="PAYROLL", source_week=10, shift_weeks=1
    )
    with pytest.raises(ValueError, match="horizon is 4 weeks"):
        apply_overrides(baseline, [ov], starting_cash=0.0)


def test_remove_source_beyond_horizon_raises():
    baseline = _make_baseline(horizon=4)
    ov = Override(
        type="remove_payment", target="PAYROLL", source_week=10
    )
    with pytest.raises(ValueError, match="horizon is 4 weeks"):
        apply_overrides(baseline, [ov], starting_cash=0.0)


# ---------------------------------------------------------------------------
# Pydantic-level validation
# ---------------------------------------------------------------------------

def test_override_new_cost_requires_week_number():
    with pytest.raises((ValidationError, ValueError), match="week_number"):
        Override(type="new_cost", target="PAYROLL", amount=100.0)


def test_override_new_cost_requires_amount():
    with pytest.raises((ValidationError, ValueError), match="amount"):
        Override(type="new_cost", target="PAYROLL", week_number=1)


def test_override_new_cost_forbids_source_week():
    with pytest.raises((ValidationError, ValueError), match="source_week"):
        Override(
            type="new_cost", target="PAYROLL", week_number=1,
            amount=100.0, source_week=2
        )


def test_override_shift_payment_requires_source_week():
    with pytest.raises((ValidationError, ValueError), match="source_week"):
        Override(type="shift_payment", target="PAYROLL", shift_weeks=1)


def test_override_shift_payment_requires_shift_weeks():
    with pytest.raises((ValidationError, ValueError), match="shift_weeks"):
        Override(type="shift_payment", target="PAYROLL", source_week=1)


def test_override_shift_payment_forbids_amount():
    with pytest.raises((ValidationError, ValueError), match="amount"):
        Override(
            type="shift_payment", target="PAYROLL", source_week=1,
            shift_weeks=1, amount=100.0
        )


def test_override_remove_payment_requires_source_week():
    with pytest.raises((ValidationError, ValueError), match="source_week"):
        Override(type="remove_payment", target="PAYROLL")


def test_override_remove_payment_forbids_shift_weeks():
    with pytest.raises((ValidationError, ValueError), match="shift_weeks"):
        Override(
            type="remove_payment", target="PAYROLL",
            source_week=1, shift_weeks=2
        )


def test_override_rejects_zero_week_number():
    with pytest.raises(ValidationError):
        Override(
            type="new_cost", target="PAYROLL", week_number=0, amount=100.0
        )


def test_override_rejects_negative_source_week():
    with pytest.raises(ValidationError):
        Override(
            type="shift_payment", target="PAYROLL",
            source_week=-1, shift_weeks=1
        )


def test_override_rejects_invalid_target():
    with pytest.raises(ValidationError):
        Override(
            type="new_cost", target="OVERRIDES_COSTS",  # dropped from enum
            week_number=1, amount=100.0
        )


def test_override_rejects_modify_type():
    """'modify' was dropped from OverrideType in v1."""
    with pytest.raises(ValidationError):
        Override(
            type="modify", target="PAYROLL", week_number=1, amount=100.0
        )


# ---------------------------------------------------------------------------
# Phase ordering — comprehensive scenario
# ---------------------------------------------------------------------------

def test_full_phase_ordering_scenario():
    """Comprehensive: all four override types on a 4-week baseline.

    Baseline: ps_revenue=1000, payroll=200 each week.

    Overrides (in random order):
      1. new_cost: PAYROLL week 2, +500
      2. new_revenue: MRR week 1, +8000
      3. shift_payment: PAYROLL source_week=3, shift_weeks=1
      4. remove_payment: PAYROLL source_week=1

    Expected per-week payroll after all phases:
      Week 1: original 200 - removed 200 = 0
      Week 2: original 200 + new_cost 500 = 700 (shift reads ORIGINAL week 3,
              not affected here)
      Week 3: original 200 - shifted out 200 = 0
      Week 4: original 200 + shifted in 200 = 400

    Expected MRR: week 1 = 8000, others = 0.
    """
    baseline = _make_baseline(horizon=4)
    ovs = [
        Override(type="shift_payment", target="PAYROLL", source_week=3, shift_weeks=1),
        Override(type="new_revenue", target="MRR", week_number=1, amount=8000.0),
        Override(type="remove_payment", target="PAYROLL", source_week=1),
        Override(type="new_cost", target="PAYROLL", week_number=2, amount=500.0),
    ]
    result = apply_overrides(baseline, ovs, starting_cash=0.0)

    # Payroll
    assert result[0].payroll == 0.0
    assert result[1].payroll == 700.0
    assert result[2].payroll == 0.0
    assert result[3].payroll == 400.0

    # MRR
    assert result[0].mrr_revenue == 8000.0
    assert result[1].mrr_revenue == 0.0

    # Totals recomputed
    assert result[0].total_revenue == 9000.0  # 1000 ps + 8000 mrr
    assert result[1].total_costs == 700.0     # only payroll

    # Net cash flow
    assert result[0].net_cash_flow == 9000.0  # 9000 - 0
    assert result[1].net_cash_flow == 300.0   # 1000 - 700
