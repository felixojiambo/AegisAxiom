"""Unit tests for pure formula functions.

Implements the rules locked in Friday's call with Asad:
  - Trailing 3-month strict averaging
  - Zero-safe gp_ratio and cogs_ratio
  - No normalization in distribute_weekly
"""
import pytest
from services.nova_engine import formulas
from services.nova_engine.models import WeeklyDistribution


def test_module_imports():
    assert hasattr(formulas, "mean_last_3_months")
    assert hasattr(formulas, "gp_ratio")
    assert hasattr(formulas, "cogs_ratio")
    assert hasattr(formulas, "distribute_weekly")


# mean_last_3_months — strict length 3 ----------------------------------

def test_mean_basic():
    assert formulas.mean_last_3_months([100.0, 200.0, 300.0]) == 200.0


def test_mean_with_zeros():
    """Slow quarter — all zeros. Result is 0.0, no error."""
    assert formulas.mean_last_3_months([0.0, 0.0, 0.0]) == 0.0


def test_mean_with_negative_values():
    """Refund/discount-heavy month is real. Mean reflects it."""
    assert formulas.mean_last_3_months([-100.0, 0.0, 100.0]) == 0.0
    assert formulas.mean_last_3_months([-300.0, -300.0, -300.0]) == -300.0


def test_mean_with_realistic_ps_numbers():
    """Plausible Q1 PS revenue figures."""
    result = formulas.mean_last_3_months([65000.0, 52000.0, 65000.0])
    assert result == pytest.approx(60666.67, abs=0.01)


def test_mean_rejects_empty_list():
    with pytest.raises(ValueError, match="requires exactly 3 values"):
        formulas.mean_last_3_months([])


def test_mean_rejects_two_values():
    with pytest.raises(ValueError, match="requires exactly 3 values, got 2"):
        formulas.mean_last_3_months([100.0, 200.0])


def test_mean_rejects_four_values():
    with pytest.raises(ValueError, match="requires exactly 3 values, got 4"):
        formulas.mean_last_3_months([100.0, 200.0, 300.0, 400.0])


# gp_ratio — strict length 3, zero-safe ----------------------------------

def test_gp_ratio_simple():
    assert formulas.gp_ratio([100, 100, 100], [50, 50, 50]) == 0.5


def test_gp_ratio_uses_aggregate():
    """Ratio is computed on totals, not per-month average of ratios."""
    revenue = [100.0, 200.0, 300.0]
    gross_profit = [50.0, 100.0, 150.0]
    assert formulas.gp_ratio(revenue, gross_profit) == 0.5


def test_gp_ratio_with_realistic_ps_numbers():
    """Plausible Q1 PS at ~68% GP margin."""
    revenue = [65000.0, 52000.0, 65000.0]
    gross_profit = [44200.0, 35360.0, 44200.0]
    assert formulas.gp_ratio(revenue, gross_profit) == pytest.approx(0.68, abs=0.001)


def test_gp_ratio_zero_revenue_returns_zero():
    """Line with no activity over the period — returns 0.0."""
    assert formulas.gp_ratio([0.0, 0.0, 0.0], [0.0, 0.0, 0.0]) == 0.0


def test_gp_ratio_negative_gp_is_real():
    """Loss-making period — ratio is negative, returned as-is."""
    result = formulas.gp_ratio([100.0, 100.0, 100.0], [-50.0, -50.0, -50.0])
    assert result == -0.5


def test_gp_ratio_rejects_short_revenue():
    with pytest.raises(ValueError, match="revenue of length 3"):
        formulas.gp_ratio([100, 100], [50, 50])


def test_gp_ratio_rejects_short_gp():
    with pytest.raises(ValueError, match="gross_profit of length 3"):
        formulas.gp_ratio([100, 100, 100], [50, 50])


# cogs_ratio — CRITICAL: zero-safety returns 0.0 not 1.0 ----------------

def test_cogs_ratio_complement_of_gp():
    """In normal cases, cogs_ratio = 1 - gp_ratio."""
    revenue = [100.0, 100.0, 100.0]
    gp = [50.0, 50.0, 50.0]
    assert formulas.cogs_ratio(revenue, gp) == 0.5


def test_cogs_ratio_zero_revenue_returns_zero_not_one():
    """CRITICAL — zero-safety. With no revenue, cogs_ratio is 0.0, not 1.0.

    A naive '1 - gp_ratio()' would give 1.0 (because gp_ratio returns 0.0).
    But '100% COGS' is misleading — the right answer is 'no activity'.
    """
    assert formulas.cogs_ratio([0.0, 0.0, 0.0], [0.0, 0.0, 0.0]) == 0.0


def test_cogs_ratio_realistic_ps_period():
    """Plausible Q1 PS at ~32% COGS, materially different from the
    spreadsheet's hardcoded 56% — the engine derives, doesn't hardcode."""
    revenue = [65000.0, 52000.0, 65000.0]
    gross_profit = [44200.0, 35360.0, 44200.0]
    result = formulas.cogs_ratio(revenue, gross_profit)
    assert result == pytest.approx(0.32, abs=0.001)


def test_cogs_ratio_high_cogs_period():
    revenue = [100.0, 100.0, 100.0]
    gp = [30.0, 30.0, 30.0]
    assert formulas.cogs_ratio(revenue, gp) == pytest.approx(0.7, abs=0.001)


def test_cogs_ratio_rejects_short_input():
    with pytest.raises(ValueError, match="length 3"):
        formulas.cogs_ratio([100, 100], [50, 50, 50])


# distribute_weekly — no normalization -----------------------------------

def test_distribute_weekly_default_25_25_25_25():
    """Default WeeklyDistribution is even quarters."""
    dist = WeeklyDistribution()
    result = formulas.distribute_weekly(1000.0, dist)
    assert result == [250.0, 250.0, 250.0, 250.0]
    assert sum(result) == pytest.approx(1000.0)


def test_distribute_weekly_mrr_pattern():
    """MRR: 100% in week 1, per Asad's call."""
    dist = WeeklyDistribution(week1_pct=1.0, week2_pct=0.0, week3_pct=0.0, week4_pct=0.0)
    result = formulas.distribute_weekly(8000.0, dist)
    assert result == [8000.0, 0.0, 0.0, 0.0]


def test_distribute_weekly_biweekly_payroll():
    dist = WeeklyDistribution(week1_pct=0.0, week2_pct=0.5, week3_pct=0.0, week4_pct=0.5)
    result = formulas.distribute_weekly(50000.0, dist)
    assert result == [0.0, 25000.0, 0.0, 25000.0]


def test_distribute_weekly_does_not_normalize():
    """If weights don't sum to 1.0, the engine does NOT silently rescale.

    Workbook has heuristic weights like 0.3/0.5/0.5/0.3 summing to 1.6.
    Nova does not 'fix' those — returns what the math says.
    """
    dist = WeeklyDistribution(week1_pct=0.3, week2_pct=0.5, week3_pct=0.5, week4_pct=0.3)
    result = formulas.distribute_weekly(1000.0, dist)
    assert result == [300.0, 500.0, 500.0, 300.0]
    assert sum(result) == pytest.approx(1600.0)


def test_distribute_weekly_zero_amount():
    """No revenue this month — every week is zero."""
    dist = WeeklyDistribution(week1_pct=0.4, week2_pct=0.2, week3_pct=0.4, week4_pct=0.0)
    result = formulas.distribute_weekly(0.0, dist)
    assert result == [0.0, 0.0, 0.0, 0.0]


def test_distribute_weekly_negative_amount():
    """Refund/credit — negative distributes the same way."""
    dist = WeeklyDistribution()
    result = formulas.distribute_weekly(-1000.0, dist)
    assert result == [-250.0, -250.0, -250.0, -250.0]


# ---------------------------------------------------------------------------
# Canonical company-level gross-profit helper
# ---------------------------------------------------------------------------
#
# Audit found query_finance_data subtracting only PS_COGS + FS_COGS while
# Total Revenue included OTHER_REVENUE-eligible rows — an asymmetric
# formula that overstated company GP / GM. compute_gross_profit centralises
# the canonical category coverage; these tests pin it.


def test_canonical_revenue_categories_match_account_categories():
    from services.nova_engine.account_categories import is_revenue, Category
    from typing import get_args

    expected = {c for c in get_args(Category) if is_revenue(c)}
    assert formulas.REVENUE_CATEGORIES == expected


def test_canonical_cogs_categories_match_account_categories():
    # COGS subset of expense categories — PS_COGS, FS_COGS, OTHER_COGS.
    # PAYROLL / ADMIN_EXPENSES / OTHER_OPEX are operating expenses, not
    # COGS, and must NOT be in the company-level GP COGS bucket.
    assert formulas.COGS_CATEGORIES == {"PS_COGS", "FS_COGS", "OTHER_COGS"}


def _synthetic_revenue() -> dict[str, float]:
    """All canonical revenue categories with non-trivial fixture values."""
    return {
        "PS_REVENUE": 100.0,
        "FS_REVENUE": 60.0,
        "MRR": 25.0,
        "WHOLESALE_REVENUE": 40.0,
        "OTHER_REVENUE": 15.0,
    }


def _synthetic_cogs() -> dict[str, float]:
    """All canonical COGS categories with non-trivial fixture values."""
    return {
        "PS_COGS": 30.0,
        "FS_COGS": 20.0,
        "OTHER_COGS": 10.0,
    }


def test_compute_gross_profit_includes_all_revenue_categories_from_fixture():
    rev = _synthetic_revenue()
    cogs = _synthetic_cogs()

    result = formulas.compute_gross_profit(rev, cogs)

    expected_total_revenue = sum(rev[c] for c in formulas.REVENUE_CATEGORIES if c in rev)
    assert result["total_revenue"] == pytest.approx(expected_total_revenue)
    assert set(result["included_revenue_categories"]) == set(rev.keys())


def test_compute_gross_profit_subtracts_ps_fs_and_other_cogs():
    rev = _synthetic_revenue()
    cogs = _synthetic_cogs()

    result = formulas.compute_gross_profit(rev, cogs)

    expected_total_cogs = sum(cogs.values())
    expected_total_revenue = sum(rev.values())
    expected_gp = expected_total_revenue - expected_total_cogs

    assert result["total_cogs"] == pytest.approx(expected_total_cogs)
    assert result["gross_profit"] == pytest.approx(expected_gp)
    # OTHER_COGS must be in the included list so the LLM can quote coverage.
    assert "OTHER_COGS" in result["included_cogs_categories"]


def test_compute_gross_profit_includes_other_cogs_in_total():
    """Specifically guard against the audited bug: PS+FS only."""
    rev = {"PS_REVENUE": 100.0}
    cogs_with_other = {"PS_COGS": 20.0, "OTHER_COGS": 30.0}
    cogs_ps_only = {"PS_COGS": 20.0}

    full = formulas.compute_gross_profit(rev, cogs_with_other)
    partial = formulas.compute_gross_profit(rev, cogs_ps_only)

    # The OTHER_COGS bucket must change the totals, not be silently dropped.
    assert full["total_cogs"] != partial["total_cogs"]
    assert full["gross_profit"] != partial["gross_profit"]
    assert full["total_cogs"] - partial["total_cogs"] == pytest.approx(cogs_with_other["OTHER_COGS"])


def test_compute_gross_profit_margin_from_fixture():
    rev = _synthetic_revenue()
    cogs = _synthetic_cogs()

    result = formulas.compute_gross_profit(rev, cogs)

    expected_revenue = sum(rev.values())
    expected_cogs = sum(cogs.values())
    expected_margin = (expected_revenue - expected_cogs) / expected_revenue

    assert result["gross_margin"] == pytest.approx(expected_margin)


def test_compute_gross_profit_zero_revenue_returns_null_margin():
    result = formulas.compute_gross_profit({}, {"PS_COGS": 50.0})
    assert result["total_revenue"] == 0.0
    assert result["gross_margin"] is None
    # GP is still well-defined: 0 - 50 = -50
    assert result["gross_profit"] == pytest.approx(-50.0)


def test_compute_gross_profit_returns_excluded_categories():
    # PAYROLL is a real category but NOT a COGS category. It must be
    # surfaced in excluded_categories rather than silently summed.
    rev = {"PS_REVENUE": 100.0, "FAKE_CAT": 999.0}
    cogs = {"PS_COGS": 30.0, "PAYROLL": 50.0}

    result = formulas.compute_gross_profit(rev, cogs)

    assert "FAKE_CAT" in result["excluded_categories"]
    assert "PAYROLL" in result["excluded_categories"]
    # And it must NOT contribute to the totals.
    assert result["total_revenue"] == pytest.approx(100.0)
    assert result["total_cogs"] == pytest.approx(30.0)


def test_compute_gross_profit_response_carries_formula_metadata():
    result = formulas.compute_gross_profit({"PS_REVENUE": 1.0}, {"PS_COGS": 0.5})
    assert result["formula_version"] == formulas.GROSS_PROFIT_FORMULA_VERSION
    assert result["formula_source"] == formulas.GROSS_PROFIT_FORMULA_SOURCE_GL


def test_compute_gross_profit_rejects_non_dict_inputs():
    with pytest.raises(TypeError):
        formulas.compute_gross_profit([], {})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        formulas.compute_gross_profit({}, [])  # type: ignore[arg-type]


def test_compute_gross_profit_with_negative_values_accepted():
    """Refund-heavy month: a category can carry a negative total. The
    helper must still compute coherent totals — no clamping to zero."""
    rev = {"PS_REVENUE": -50.0, "FS_REVENUE": 100.0}
    cogs = {"PS_COGS": -10.0, "FS_COGS": 30.0}

    result = formulas.compute_gross_profit(rev, cogs)
    assert result["total_revenue"] == pytest.approx(50.0)
    assert result["total_cogs"] == pytest.approx(20.0)
    assert result["gross_profit"] == pytest.approx(30.0)


def test_compute_gross_profit_symmetric_other_revenue_other_cogs():
    """Asymmetric-formula regression: OTHER_REVENUE in revenue must be
    matched by OTHER_COGS in cogs. The helper enforces this by
    construction; this test pins that contract."""
    rev = {"PS_REVENUE": 100.0, "OTHER_REVENUE": 50.0}
    cogs_symmetric = {"PS_COGS": 20.0, "OTHER_COGS": 10.0}

    result = formulas.compute_gross_profit(rev, cogs_symmetric)
    # If OTHER_COGS were silently excluded, GP would be 130 instead of 120.
    expected_gp = (100.0 + 50.0) - (20.0 + 10.0)
    assert result["gross_profit"] == pytest.approx(expected_gp)


# ---------------------------------------------------------------------------
# K4. compute_reversal_metadata shared helper
# ---------------------------------------------------------------------------


def test_compute_reversal_metadata_mixed_signed_totals():
    """Mixed signed amounts split cleanly across the positive and
    negative buckets and signed_total equals positive_total plus
    negative_total."""
    amounts = [100.0, -25.0, 50.0, -10.0, 0.0]
    meta = formulas.compute_reversal_metadata(amounts)
    assert meta["positive_total"] == pytest.approx(150.0)
    assert meta["negative_total"] == pytest.approx(-35.0)
    assert meta["positive_row_count"] == 2
    assert meta["negative_row_count"] == 2
    # signed_total derived from positive plus negative.
    assert meta["signed_total"] == pytest.approx(115.0)
    assert meta["signed_total"] == pytest.approx(
        meta["positive_total"] + meta["negative_total"]
    )


def test_compute_reversal_metadata_all_positive():
    amounts = [10.0, 20.0, 30.0]
    meta = formulas.compute_reversal_metadata(amounts)
    assert meta["positive_total"] == pytest.approx(60.0)
    assert meta["negative_total"] == pytest.approx(0.0)
    assert meta["positive_row_count"] == 3
    assert meta["negative_row_count"] == 0
    assert meta["signed_total"] == pytest.approx(60.0)


def test_compute_reversal_metadata_all_negative():
    amounts = [-10.0, -20.0, -30.0]
    meta = formulas.compute_reversal_metadata(amounts)
    assert meta["positive_total"] == pytest.approx(0.0)
    assert meta["negative_total"] == pytest.approx(-60.0)
    assert meta["positive_row_count"] == 0
    assert meta["negative_row_count"] == 3
    assert meta["signed_total"] == pytest.approx(-60.0)


def test_compute_reversal_metadata_empty_iterable():
    """Empty input returns zeros across the board. Callers that have
    no rows in scope still get a usable dict to merge into a response
    envelope."""
    meta = formulas.compute_reversal_metadata([])
    assert meta["signed_total"] == 0.0
    assert meta["positive_total"] == 0.0
    assert meta["negative_total"] == 0.0
    assert meta["positive_row_count"] == 0
    assert meta["negative_row_count"] == 0


def test_compute_reversal_metadata_zero_amounts_contribute_to_neither_bucket():
    """A zero amount must not bump either count. The earlier inline
    loops at the three duplicate sites used strict greater than and
    less than comparisons. Preserving that contract here is what makes
    the extraction a safe drop in replacement."""
    meta = formulas.compute_reversal_metadata([0.0, 0.0, 0.0])
    assert meta["positive_row_count"] == 0
    assert meta["negative_row_count"] == 0
    assert meta["signed_total"] == 0.0
