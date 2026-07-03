"""End-to-end engine tests.

Two layers:

  test_engine_pure_unit (always runs): full project_cash_flow against
  hand-built inputs, checks structural and numerical correctness without
  any Mongo dependency.

  test_engine_self_consistency_against_trailing_3mo_aggregation (gated):
  runs only if NOVA_RUN_DATA_LOADER_TESTS=1. Loads real data via
  data_loader, runs the engine, independently aggregates trailing-3-month
  data via direct Mongo, asserts engine's first-forecast-month totals
  match the recomputed values within tolerance.
"""
import os
from datetime import date

import pytest

from services.nova_engine.engine import project_cash_flow
from services.nova_engine.models import (
    ProjectionInputs,
    ProjectionOutput,
    RecurringRevenueAssumption,
)


def test_engine_pure_unit():
    """Self-contained engine smoke test — no Mongo, no env vars.

    Verifies the engine produces a valid ProjectionOutput for a baseline
    set of inputs that explicitly neutralizes MRR. Stronger composition
    tests live in test_nova_engine.py.
    """
    inputs = ProjectionInputs(
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
    output = project_cash_flow(inputs)

    assert isinstance(output, ProjectionOutput)
    assert len(output.weeks) == 13
    assert output.inputs_hash  # non-empty hex string
    assert len(output.inputs_hash) == 64  # SHA-256 hex digest

    # Structural sanity
    for i, w in enumerate(output.weeks):
        assert w.week_number == i + 1
        assert isinstance(w.week_starting, date)

    # Composition sanity: first 4 weeks of PS revenue should sum to $10K
    first_month_ps = sum(output.weeks[i].ps_revenue for i in range(4))
    assert first_month_ps == pytest.approx(10000.0, abs=0.01)


@pytest.mark.skipif(
    os.environ.get("NOVA_RUN_DATA_LOADER_TESTS") != "1",
    reason="Set NOVA_RUN_DATA_LOADER_TESTS=1 to run live Mongo self-consistency test"
)
@pytest.mark.asyncio
async def test_engine_self_consistency_against_trailing_3mo_aggregation():
    """Self-consistency: engine's first-forecast-month totals match
    independently recomputed trailing-3-month aggregations.

    NOT an actuals-parity test. We validate that data_loader + formulas +
    engine compose correctly: the same trailing-3-month average must
    appear when computed two different ways.

    MRR is neutralized via a zero-value manual MRR assumption to isolate
    the PS/FS forecast composition path.
    """
    import os

    import database
    from services.nova_engine.data_loader import (
        build_projection_inputs,
        get_app_db_name,
        get_latest_tx_date,
        get_mongo_client,
        get_qbo_db_name,
        load_revenue_history,
        load_gross_profit_history,
    )
    from services.nova_engine.data_freshness import last_n_complete_months

    # database.get_client() now raises RuntimeError pre-init. Initialize the
    # global Motor client from env (this gated test already requires
    # MONGO_URL + DB_NAME) so the shim's get_qbo_db().client path resolves.
    database.init(os.environ["MONGO_URL"], os.environ["DB_NAME"])

    # NOTE: get_mongo_client() now returns the process-wide global client
    # (via get_qbo_db().client). Do NOT call .close() on it — closing the
    # global tears down Motor for every subsequent caller in this process.
    # The client's lifetime is process-scoped; database.init() set it up
    # and process teardown handles cleanup.
    client = get_mongo_client()
    qbo_db = client[get_qbo_db_name()]
    app_db = client[get_app_db_name()]

    # Pick projection_start_month = month AFTER latest_tx_date's month.
    # This makes the trailing-3-month window the 3 most recent
    # complete months, ALL of which exist in the data.
    latest = await get_latest_tx_date(qbo_db)
    if latest.month == 12:
        projection_start = f"{latest.year + 1}-01"
    else:
        projection_start = f"{latest.year}-{latest.month + 1:02d}"

    # Build inputs from real data
    inputs = await build_projection_inputs(
        qbo_db, app_db,
        starting_cash=0.0,
        projection_start_month=projection_start,
        horizon_weeks=13,
    )

    # Neutralize MRR so the engine doesn't raise on missing MRR
    # categorization. Replace manual_recurring_revenue with the
    # zero-value MRR assumption.
    inputs = inputs.model_copy(update={
        "manual_recurring_revenue": [
            RecurringRevenueAssumption(
                name="test neutralize MRR",
                amount_per_month=0.0,
                line_type="MRR",
                starts_month=projection_start,
                ends_month="2030-12",
            ),
        ],
    })

    # Run the engine
    output = project_cash_flow(inputs)

    # Independently recompute first-forecast-month totals via direct
    # data_loader aggregations (same Mongo pipelines, no engine math)
    history_months = last_n_complete_months(
        reference_month=projection_start,
        latest_tx_date=latest,
        n=3,
    )
    revenue = await load_revenue_history(qbo_db, history_months)
    gp = await load_gross_profit_history(qbo_db, history_months)

    expected_ps_rev = sum(revenue["PS_REVENUE"]) / 3.0
    expected_fs_rev = sum(revenue["FS_REVENUE"]) / 3.0
    expected_other_rev = sum(revenue["OTHER_REVENUE"]) / 3.0
    expected_ps_gp = sum(gp["PS_GP"]) / 3.0
    expected_fs_gp = sum(gp["FS_GP"]) / 3.0

    # COGS = revenue * (1 - GP/revenue) = revenue - GP, on the average
    expected_ps_cogs = expected_ps_rev - expected_ps_gp
    expected_fs_cogs = expected_fs_rev - expected_fs_gp

    # Engine's first 4 weeks should sum to these expected values
    actual_ps_rev = sum(output.weeks[i].ps_revenue for i in range(4))
    actual_fs_rev = sum(output.weeks[i].fs_revenue for i in range(4))
    actual_other_rev = sum(output.weeks[i].other_revenue for i in range(4))
    actual_ps_cogs = sum(output.weeks[i].ps_cogs for i in range(4))
    actual_fs_cogs = sum(output.weeks[i].fs_cogs for i in range(4))

    # Tolerance: float arithmetic accumulates small rounding errors
    # across 4-week distributions, but the totals should be very close.
    assert actual_ps_rev == pytest.approx(expected_ps_rev, abs=0.10)
    assert actual_fs_rev == pytest.approx(expected_fs_rev, abs=0.10)
    assert actual_other_rev == pytest.approx(expected_other_rev, abs=0.10)
    assert actual_ps_cogs == pytest.approx(expected_ps_cogs, abs=0.10)
    assert actual_fs_cogs == pytest.approx(expected_fs_cogs, abs=0.10)

    # Total revenue (PS + FS + MRR + WHOLESALE + OTHER) for first
    # forecast month. With MRR neutralized to 0 and no wholesale
    # contribution, this equals PS + FS + OTHER. (Pre-Bug-A this
    # asserted PS+FS only; live OTHER_REVENUE rows were silently
    # dropped at load_revenue_history and never reached the engine.)
    actual_total_rev = sum(output.weeks[i].total_revenue for i in range(4))
    assert actual_total_rev == pytest.approx(
        expected_ps_rev + expected_fs_rev + expected_other_rev, abs=0.20
    )

    # Total costs for first forecast month: with no manual cost
    # assumptions, this should equal PS COGS + FS COGS.
    actual_total_costs = sum(output.weeks[i].total_costs for i in range(4))
    assert actual_total_costs == pytest.approx(
        expected_ps_cogs + expected_fs_cogs, abs=0.20
    )


# ---------------------------------------------------------------------------
# Auto-MRR end-to-end (no Mongo, hand-built ProjectionInputs)
# ---------------------------------------------------------------------------


def test_auto_mrr_baseline_feeds_projection_when_no_manual_supplied():
    """When mrr_last_year_same_months carries values for the forecast
    months and the caller supplies NO manual MRR, the engine pulls the
    auto baseline through into wholesale_revenue / mrr_revenue lines.
    Locks in the contract: auto-MRR is sufficient on its own."""
    inputs = ProjectionInputs(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        basis="accrual",
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        mrr_last_year_same_months={
            "2026-05": 8000.0, "2026-06": 8000.0,
            "2026-07": 8000.0, "2026-08": 8000.0,
        },
        # NO manual_recurring_revenue supplied - auto baseline is
        # the entire MRR contribution.
    )
    output = project_cash_flow(inputs)
    # First month's MRR weeks should sum to $8,000 (the auto baseline).
    first_month_mrr = sum(output.weeks[i].mrr_revenue for i in range(4))
    assert first_month_mrr == pytest.approx(8000.0, abs=0.01)


def test_manual_mrr_adds_on_top_of_auto_baseline():
    """The engine's MRR resolution is ADDITIVE: manual MRR adds to
    auto baseline rather than replacing it. A user who wants to
    model 'what if MRR grows $2k/mo on top of last year' supplies
    a manual entry and the projection sees base + manual."""
    inputs = ProjectionInputs(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        basis="accrual",
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        mrr_last_year_same_months={
            "2026-05": 8000.0, "2026-06": 8000.0,
            "2026-07": 8000.0, "2026-08": 8000.0,
        },
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="MRR growth what-if",
                amount_per_month=2000.0,
                line_type="MRR",
                starts_month="2026-05",
                ends_month="2026-12",
            ),
        ],
    )
    output = project_cash_flow(inputs)
    # First month's MRR weeks should sum to $10,000 (8k base + 2k manual).
    first_month_mrr = sum(output.weeks[i].mrr_revenue for i in range(4))
    assert first_month_mrr == pytest.approx(10000.0, abs=0.01)


def test_auto_mrr_absent_without_manual_raises_insufficient_history():
    """If neither auto-MRR (mrr_last_year_same_months={}) nor manual
    MRR is supplied, _resolve_mrr_base raises - that's the contract
    that surfaces as mrr_mapping_unavailable to chat callers."""
    from services.nova_engine.models import InsufficientHistoryError

    inputs = ProjectionInputs(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        basis="accrual",
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        mrr_last_year_same_months={},   # no auto-MRR
        # no manual MRR either
    )
    with pytest.raises(InsufficientHistoryError) as exc_info:
        project_cash_flow(inputs)
    assert "mrr" in exc_info.value.line.lower()
