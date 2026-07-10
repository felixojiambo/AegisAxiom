"""Tests for the MRR baseline resolver and its run-rate fallback.

Background: Asad confirmed that account code 50300 ("Other
Income:Contract Commission Income") IS the right MRR account, but
some tenants have no prior-year same-month activity on 50300 even
though they have recent activity on it. The old loader returned an
empty dict for those forecast months, the engine raised
``InsufficientHistoryError``, and Nova replied
``mrr_mapping_unavailable`` — misleading, because 50300 IS mapped
and DOES have rows, just not on the comparator month.

This file pins the new behaviour:

  * Match by account code prefix 50300, not exact account name.
  * Prior-year same-month is tried first.
  * When prior-year same-month is missing, fall back to the average
    of the 3 most-recent calendar months that have 50300 activity.
  * The fallback is labelled "historical contract commission
    run-rate", NEVER "fixed MRR".
  * Nova only asks for a manual MRR assumption when NEITHER prior
    year NOR recent activity exists.
  * The response carries the audit block: basis, account filter,
    row count, months used, fallback method.

All synthetic. Every expected value is computed from in-test
fixture rows; no production dates, customers, vendors, totals.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest

from services.nova_engine.data_loader import (
    MRR_FALLBACK_METHOD_LABEL,
    load_mrr_baseline_with_fallback,
)


# ---------------------------------------------------------------------------
# Mock helpers — Motor / Mongo stand-ins
# ---------------------------------------------------------------------------


def _mock_db_with_sequential_aggregates(*results: list[dict]) -> MagicMock:
    """Mock qbo_db whose general_ledger.aggregate() returns the supplied
    row lists in order. The helper performs two aggregate() calls (one
    for prior-year, one for fallback); tests pass two lists to control
    each step independently."""
    call_index = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_index["i"]
        call_index["i"] += 1
        cursor = MagicMock()
        # If a test passes fewer result lists than calls happen, the
        # next call returns [] — mirroring "no rows" rather than
        # KeyErroring, so the helper's no-history path is exercised.
        rows = results[idx] if idx < len(results) else []
        cursor.to_list = AsyncMock(return_value=rows)
        return cursor

    collection = MagicMock()
    collection.aggregate = MagicMock(side_effect=aggregate_side_effect)

    db = MagicMock()
    db.general_ledger = collection
    return db


# Synthetic row factories. ``_id.account_name`` carries the 50300
# prefix so categorize() routes the row to "MRR" — matches the live
# QBO format ("50300 Other Income:Contract Commission Income" and
# the bare-prefix form). Year 2099 is deliberately far-future so a
# repo-wide grep never confuses fixtures with real data.


def _mrr_row(month: str, amount: float, account_name: str | None = None) -> dict:
    return {
        "_id": {
            "month": month,
            "account_id": "50300",
            "account_name": account_name or "50300 Other Income:Contract Commission Income",
        },
        "amount": amount,
    }


# ---------------------------------------------------------------------------
# 1. Prior-year same-month path (happy path, unchanged)
# ---------------------------------------------------------------------------


def test_prior_year_same_month_used_when_50300_has_activity_in_comparator():
    """Forecast 2099-05 / 2099-06 with prior-year same-month 50300
    rows present → method=prior_year_same_month for both, fallback
    aggregate is never queried."""
    prior_year_rows = [
        _mrr_row("2098-05", 10_000.0),
        _mrr_row("2098-06", 12_000.0),
    ]
    fallback_rows: list[dict] = []  # should not be consulted

    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05", "2099-06"], basis="accrual",
        )
    )

    assert monthly == {"2099-05": 10_000.0, "2099-06": 12_000.0}
    assert metadata["fallback_method"] == "prior_year_same_month"
    assert metadata["method_by_month"] == {
        "2099-05": "prior_year_same_month",
        "2099-06": "prior_year_same_month",
    }
    assert metadata["prior_year"]["row_count"] == 2
    assert metadata["fallback"]["row_count"] == 0
    assert metadata["fallback"]["months_used"] == []


def test_prior_year_same_month_zero_net_is_a_real_signal():
    """A prior-year same-month row that nets to zero (refund cancels
    out the original booking) is a real signal — the projection
    uses 0.0 as the baseline rather than falling back."""
    # Single 50300 row with amount=0 (or a pair that cancels). The
    # helper aggregates upstream so we represent this as one row.
    prior_year_rows = [_mrr_row("2098-05", 0.0)]
    fallback_rows: list[dict] = [_mrr_row("2099-04", 999_999.0)]

    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(db, ["2099-05"], basis="accrual")
    )

    assert monthly == {"2099-05": 0.0}
    assert metadata["method_by_month"]["2099-05"] == "prior_year_same_month"
    # Fallback must NOT have been the source — the misleading
    # $999,999 from the fallback fixture would surface here if the
    # helper treated a zero-net month as "missing".
    assert monthly["2099-05"] != 999_999.0


# ---------------------------------------------------------------------------
# 2. Historical run-rate fallback path (new behaviour)
# ---------------------------------------------------------------------------


def test_fallback_used_when_prior_year_same_month_has_no_50300_rows():
    """Forecast 2099-05 with no prior-year same-month 50300 activity
    but recent 50300 activity in 2099-02 / 2099-03 / 2099-04 →
    fallback run-rate = average of those three months."""
    prior_year_rows: list[dict] = []   # 2098-05 has no 50300 rows
    fallback_rows = [
        _mrr_row("2099-02", 6_000.0),
        _mrr_row("2099-03", 9_000.0),
        _mrr_row("2099-04", 12_000.0),
    ]

    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )

    # Run-rate = (6000 + 9000 + 12000) / 3 = 9000
    expected = (6_000.0 + 9_000.0 + 12_000.0) / 3
    assert monthly == {"2099-05": expected}
    assert metadata["fallback_method"] == "historical_run_rate"
    assert metadata["method_by_month"] == {"2099-05": "historical_run_rate"}
    assert metadata["fallback"]["months_used"] == ["2099-04", "2099-03", "2099-02"]
    assert metadata["fallback"]["run_rate_monthly_amount"] == expected
    # Audit row counts surface both paths so a reviewer can verify
    # why the helper chose fallback over prior-year.
    assert metadata["prior_year"]["row_count"] == 0
    assert metadata["fallback"]["row_count"] == 3


def test_fallback_skips_inactive_calendar_months_between_active_ones():
    """If 50300 had rows in 2099-04 and 2099-02 but NOT 2099-03,
    the run-rate averages the two active months. The inactive
    calendar month does not pull the average down."""
    prior_year_rows: list[dict] = []
    fallback_rows = [
        _mrr_row("2099-02", 4_000.0),
        # 2099-03 deliberately absent — no 50300 activity at all
        _mrr_row("2099-04", 8_000.0),
    ]

    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )

    expected = (4_000.0 + 8_000.0) / 2
    assert monthly == {"2099-05": expected}
    # Months used must be the active ones only.
    assert metadata["fallback"]["months_used"] == ["2099-04", "2099-02"]
    # Helper surfaces a "thin signal" warning when fewer than 3
    # months feed the run-rate.
    assert any("fewer months" in w.lower() for w in metadata["warnings"])


def test_fallback_caps_at_three_months_even_with_more_activity():
    """Lookback finds 6 active months → run-rate uses ONLY the 3
    most-recent ones (matches the engine's 3-month convention)."""
    prior_year_rows: list[dict] = []
    fallback_rows = [
        _mrr_row("2098-11", 100.0),
        _mrr_row("2098-12", 100.0),
        _mrr_row("2099-01", 100.0),
        _mrr_row("2099-02", 6_000.0),
        _mrr_row("2099-03", 9_000.0),
        _mrr_row("2099-04", 12_000.0),
    ]

    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )

    expected = (6_000.0 + 9_000.0 + 12_000.0) / 3
    assert monthly == {"2099-05": expected}
    assert metadata["fallback"]["months_used"] == [
        "2099-04", "2099-03", "2099-02",
    ]
    # The older months are listed as "months with activity" but not
    # "months used" — auditors should be able to see them.
    assert "2098-11" in metadata["fallback"]["months_with_activity"]


def test_mixed_forecast_months_resolve_per_month():
    """Two forecast months: one has prior-year coverage, the other
    falls back. Each gets the right method individually; the
    overall ``fallback_method`` is reported as ``mixed``."""
    # 2099-05 has prior-year coverage on 2098-05; 2099-06 does not.
    prior_year_rows = [_mrr_row("2098-05", 10_000.0)]
    fallback_rows = [
        _mrr_row("2099-02", 3_000.0),
        _mrr_row("2099-03", 4_000.0),
        _mrr_row("2099-04", 5_000.0),
    ]

    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05", "2099-06"], basis="accrual",
            reference_month="2099-05",
        )
    )

    expected_run_rate = (3_000.0 + 4_000.0 + 5_000.0) / 3
    assert monthly == {
        "2099-05": 10_000.0,
        "2099-06": expected_run_rate,
    }
    assert metadata["method_by_month"] == {
        "2099-05": "prior_year_same_month",
        "2099-06": "historical_run_rate",
    }
    assert metadata["fallback_method"] == "mixed"


# ---------------------------------------------------------------------------
# 3. No-history path (manual MRR required)
# ---------------------------------------------------------------------------


def test_no_history_when_neither_prior_year_nor_recent_50300_activity_exists():
    """Both prior-year and fallback windows are empty → monthly
    baseline is empty, fallback_method=no_history, and a warning
    tells the chat tool to request a manual assumption."""
    prior_year_rows: list[dict] = []
    fallback_rows: list[dict] = []

    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )

    assert monthly == {}
    assert metadata["fallback_method"] == "no_history"
    assert metadata["method_by_month"] == {"2099-05": "no_history"}
    assert metadata["fallback"]["months_used"] == []
    assert metadata["fallback"]["run_rate_monthly_amount"] is None
    # Surface a concrete instruction for the chat layer.
    assert any(
        "manual MRR assumption" in w
        for w in metadata["warnings"]
    )


def test_partial_no_history_when_some_months_resolve_and_others_dont():
    """Edge case: prior-year covers one month, fallback window is
    empty so the other month cannot be resolved. Overall label is
    ``partial_no_history`` — the chat tool decides whether to
    accept the partial baseline or ask for a manual assumption."""
    prior_year_rows = [_mrr_row("2098-05", 10_000.0)]
    fallback_rows: list[dict] = []  # no recent 50300 activity

    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05", "2099-06"], basis="accrual",
            reference_month="2099-05",
        )
    )

    assert monthly == {"2099-05": 10_000.0}
    assert metadata["method_by_month"] == {
        "2099-05": "prior_year_same_month",
        "2099-06": "no_history",
    }
    assert metadata["fallback_method"] == "partial_no_history"


# ---------------------------------------------------------------------------
# 4. Metadata audit fields (basis, account filter, row count, months
# used, fallback method)
# ---------------------------------------------------------------------------


def test_metadata_carries_basis_and_account_filter():
    """The audit block must include basis and the 50300 account
    mapping so a reviewer reading the response knows which scope
    produced the baseline."""
    db = _mock_db_with_sequential_aggregates([], [])
    _, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="cash",
            reference_month="2099-05",
        )
    )

    assert metadata["basis"] == "cash"
    assert metadata["account_filter"]["code_prefix"] == "50300"
    assert metadata["account_filter"]["category"] == "MRR"
    # Account label is a human-readable string the chat tool can quote.
    assert (
        "Contract Commission Income"
        in metadata["account_filter"]["account_label"]
    )


def test_metadata_reports_actual_lookup_windows():
    """The prior-year and fallback lookup windows must be quoted
    accurately — this is the bug the readiness spec called out
    (the old MRR_MAPPING_UNAVAILABLE message lied about which
    window had been queried)."""
    db = _mock_db_with_sequential_aggregates([], [])
    _, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05", "2099-06"], basis="accrual",
            reference_month="2099-05",
        )
    )

    # Prior-year window = comparator months (one year before
    # forecast).
    assert metadata["prior_year"]["lookup_window"] == "2098-05 .. 2098-06"
    assert sorted(metadata["prior_year"]["months_queried"]) == [
        "2098-05", "2098-06",
    ]
    # Fallback window walks back 24 months from reference_month.
    # Bounds quoted oldest..newest for human reading. With
    # reference_month="2099-05" the newest in-window month is
    # 2099-04 (immediately before) and the oldest is 2097-05.
    assert metadata["fallback"]["lookup_window"] == "2097-05 .. 2099-04"
    assert metadata["fallback"]["search_window_months"] == 24


def test_metadata_surfaces_fallback_run_rate_label():
    """The label distinguishes the run-rate from guaranteed MRR.
    Nova must quote this verbatim — paraphrasing would soften the
    'this is an estimate' warning."""
    db = _mock_db_with_sequential_aggregates([], [])
    _, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )
    # The module-level constant the chat tool quotes.
    assert MRR_FALLBACK_METHOD_LABEL == metadata["fallback_method_label"]
    assert "run-rate" in MRR_FALLBACK_METHOD_LABEL
    assert "guaranteed" not in MRR_FALLBACK_METHOD_LABEL.lower()
    assert "fixed mrr" not in MRR_FALLBACK_METHOD_LABEL.lower()


def test_metadata_row_count_reflects_aggregated_pipeline_rows():
    """A reviewer should be able to spot 'thin signal' situations
    (e.g. only 1 row supporting the entire run-rate) from the
    metadata alone."""
    prior_year_rows: list[dict] = []
    fallback_rows = [
        _mrr_row("2099-02", 1_000.0),
        _mrr_row("2099-03", 2_000.0),
        _mrr_row("2099-04", 3_000.0),
    ]
    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    _, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )

    assert metadata["prior_year"]["row_count"] == 0
    assert metadata["fallback"]["row_count"] == 3


# ---------------------------------------------------------------------------
# 5. Account-code prefix matching (NOT exact name)
# ---------------------------------------------------------------------------


def test_matches_by_50300_prefix_for_bare_form_account_name():
    """The bare-prefix form '50300 Contract Commission Income'
    must categorize as MRR and feed the run-rate. The previous
    Nova error blamed 'differently named' accounts; the prefix
    rule is the correct match."""
    prior_year_rows: list[dict] = []
    fallback_rows = [
        # Bare-prefix form, no parent-account path.
        _mrr_row(
            "2099-04",
            5_000.0,
            account_name="50300 Contract Commission Income",
        ),
    ]
    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )
    assert monthly == {"2099-05": 5_000.0}
    assert metadata["method_by_month"]["2099-05"] == "historical_run_rate"


def test_matches_by_50300_prefix_for_hierarchical_form_account_name():
    """The hierarchical form '50300 Other Income:Contract Commission
    Income' must ALSO match — both forms are present in live syncs."""
    prior_year_rows: list[dict] = []
    fallback_rows = [
        _mrr_row(
            "2099-04",
            7_000.0,
            account_name="50300 Other Income:Contract Commission Income",
        ),
    ]
    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, _ = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )
    assert monthly == {"2099-05": 7_000.0}


def test_rejects_account_name_without_50300_prefix():
    """A row with a non-50300 prefix (here the synthetic 50101 PS
    revenue account) must NOT feed the MRR baseline — the
    Python-side categorize() belt strips it before averaging."""
    # We can't easily force the Mongo $match to return a non-50300
    # row in real usage because the pipeline filters server-side.
    # But the loader still applies the categorize() check as a
    # belt: if a row that doesn't categorize as MRR somehow comes
    # back, it must be ignored.
    prior_year_rows: list[dict] = []
    fallback_rows = [
        # Synthetic 50101 row leaking through — categorize() routes
        # it to PS_REVENUE, so the loader drops it.
        {
            "_id": {
                "month": "2099-04",
                "account_id": "50101",
                "account_name": "50101 PS Revenue (synthetic)",
            },
            "amount": 999_999.0,
        },
        # And a real MRR row alongside it.
        _mrr_row("2099-04", 5_000.0),
    ]
    db = _mock_db_with_sequential_aggregates(prior_year_rows, fallback_rows)
    monthly, _ = asyncio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-05"], basis="accrual",
            reference_month="2099-05",
        )
    )
    assert monthly == {"2099-05": 5_000.0}


# ---------------------------------------------------------------------------
# 6. Empty forecast input is a no-op
# ---------------------------------------------------------------------------


def test_empty_forecast_months_returns_empty_baseline_and_skeletal_metadata():
    db = _mock_db_with_sequential_aggregates([], [])
    monthly, metadata = asyncio.run(
        load_mrr_baseline_with_fallback(db, [], basis="accrual")
    )
    assert monthly == {}
    # Skeletal metadata still carries the audit headers so callers
    # can branch on basis / account_filter without a None check.
    assert metadata["basis"] == "accrual"
    assert metadata["account_filter"]["code_prefix"] == "50300"
    assert metadata["fallback_method"] == "none"


# ---------------------------------------------------------------------------
# 7. Integration with the engine + run_projection error path
# ---------------------------------------------------------------------------
#
# When the helper resolves a baseline for every forecast month, the
# engine completes the projection. When neither path resolves,
# InsufficientHistoryError carries the audit block so the chat
# tool's ``mrr_mapping_unavailable`` response can quote which
# windows were queried.


def test_insufficient_history_error_carries_baseline_metadata():
    """The engine's _resolve_mrr_base attaches the loader metadata
    to InsufficientHistoryError so the chat tool can surface the
    actual lookup windows in the user-facing error."""
    from services.nova_engine.engine import _resolve_mrr_base
    from services.nova_engine.models import (
        InsufficientHistoryError,
        ProjectionInputs,
    )

    metadata = {
        "basis": "accrual",
        "account_filter": {
            "code_prefix": "50300",
            "category": "MRR",
            "account_label": "Other Income:Contract Commission Income",
        },
        "fallback_method": "no_history",
        "method_by_month": {"2099-05": "no_history"},
        "prior_year": {
            "lookup_window": "2098-05 .. 2098-05",
            "row_count": 0,
        },
        "fallback": {
            "lookup_window": "2097-05 .. 2099-04",
            "row_count": 0,
            "months_used": [],
        },
        "warnings": ["No 50300 activity found in any window."],
    }
    inputs = ProjectionInputs(
        starting_cash=10_000.0,
        projection_start_month="2099-05",
        ps_revenue_last_3_months=[1.0, 1.0, 1.0],
        fs_revenue_last_3_months=[1.0, 1.0, 1.0],
        ps_gross_profit_last_3_months=[1.0, 1.0, 1.0],
        fs_gross_profit_last_3_months=[1.0, 1.0, 1.0],
        mrr_last_year_same_months={},
        mrr_baseline_metadata=metadata,
    )

    with pytest.raises(InsufficientHistoryError) as excinfo:
        _resolve_mrr_base("2099-05", inputs)

    err = excinfo.value
    assert err.metadata is not None
    assert err.metadata["fallback_method"] == "no_history"
    assert err.metadata["account_filter"]["code_prefix"] == "50300"


def test_insufficient_history_tool_error_includes_mrr_baseline_payload():
    """The chat tool wrapper splices the metadata into the response
    so Nova does not have to guess which window failed."""
    from services.ai.agents.tools.nova_tools import (
        _insufficient_history_tool_error,
    )
    from services.nova_engine.models import InsufficientHistoryError

    metadata = {
        "basis": "accrual",
        "account_filter": {"code_prefix": "50300", "category": "MRR"},
        "fallback_method": "no_history",
        "prior_year": {"row_count": 0, "lookup_window": "2098-05 .. 2098-05"},
        "fallback": {"row_count": 0, "months_used": []},
        "method_by_month": {"2099-05": "no_history"},
        "warnings": [],
    }
    err = InsufficientHistoryError(
        line="mrr (2099-05)", available_months=0,
        required_months=1, metadata=metadata,
    )
    payload = _insufficient_history_tool_error(err)
    assert payload["error"] == "mrr_mapping_unavailable"
    assert payload["mrr_baseline"] == metadata


def test_insufficient_history_tool_error_omits_mrr_baseline_when_no_metadata():
    """Non-MRR callers (legacy tests that construct
    InsufficientHistoryError without metadata) must not get a
    ``mrr_baseline`` key in the error payload."""
    from services.ai.agents.tools.nova_tools import (
        _insufficient_history_tool_error,
    )
    from services.nova_engine.models import InsufficientHistoryError

    err = InsufficientHistoryError(
        line="mrr (2099-05)", available_months=0, required_months=1,
    )
    payload = _insufficient_history_tool_error(err)
    assert payload["error"] == "mrr_mapping_unavailable"
    assert "mrr_baseline" not in payload


# ---------------------------------------------------------------------------
# 8. End-to-end: build_projection_inputs threads the baseline metadata
# ---------------------------------------------------------------------------


def test_build_projection_inputs_threads_mrr_baseline_metadata():
    """build_projection_inputs must populate ProjectionInputs.
    mrr_baseline_metadata so the chat tool can surface it. We
    mock the qbo_db to return prior-year rows for MRR and minimal
    revenue/GP rows for the other history loaders."""
    import asyncio as _asyncio

    from services.nova_engine.data_loader import build_projection_inputs

    # Sequence of aggregate calls inside build_projection_inputs:
    #   1. get_latest_tx_date  → max_date doc
    #   2. load_revenue_history  → no revenue rows needed for this test
    #   3. load_gross_profit_history → no GP rows needed
    #   4. load_mrr_baseline_with_fallback prior-year  → 50300 rows
    #   5. load_mrr_baseline_with_fallback fallback   → not consulted
    #   6. detect_wholesale_history_from_gl → no wholesale rows
    # The exact order depends on the orchestrator; we make the
    # mock tolerant by always returning the right shape regardless
    # of the pipeline.

    aggregate_results: list[list[dict]] = [
        # 1. get_latest_tx_date — returns one doc with max_date.
        [{"_id": None, "max_date": "2099-04-30"}],
        # 2. load_revenue_history (history months) — 3 months of
        #    nonzero revenue so the loader does not raise
        #    InsufficientHistoryError on revenue.
        [
            {"_id": {"month": "2099-02", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 1.0},
            {"_id": {"month": "2099-03", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 1.0},
            {"_id": {"month": "2099-04", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 1.0},
        ],
        # 3. load_gross_profit_history — mirror revenue (cost=0 by
        #    omission keeps GP positive). Engine cares about length,
        #    not magnitude.
        [
            {"_id": {"month": "2099-02", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 1.0},
            {"_id": {"month": "2099-03", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 1.0},
            {"_id": {"month": "2099-04", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 1.0},
        ],
        # 4. MRR prior-year same-month — 1 row.
        [_mrr_row("2098-05", 7_500.0)],
        # 5. MRR fallback — not consulted because prior year resolved.
        [],
        # 6. detect_wholesale_history_from_gl — no wholesale rows.
        [],
    ]

    call_idx = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_idx["i"]
        call_idx["i"] += 1
        cursor = MagicMock()
        rows = aggregate_results[idx] if idx < len(aggregate_results) else []
        cursor.to_list = AsyncMock(return_value=rows)
        return cursor

    qbo_db = MagicMock()
    qbo_db.general_ledger.aggregate = MagicMock(side_effect=aggregate_side_effect)
    qbo_db.general_ledger.find_one = AsyncMock(
        return_value={"tx_date": "2099-04-30"}
    )

    app_db = MagicMock()
    app_db.account_groupings.find = MagicMock()
    app_db.account_groupings.find.return_value.to_list = AsyncMock(
        return_value=[]
    )

    inputs = _asyncio.run(
        build_projection_inputs(
            qbo_db=qbo_db,
            app_db=app_db,
            starting_cash=10_000.0,
            projection_start_month="2099-05",
            horizon_weeks=4,
            basis="accrual",
        )
    )

    assert inputs.mrr_baseline_metadata is not None
    assert (
        inputs.mrr_baseline_metadata["fallback_method"]
        == "prior_year_same_month"
    )
    # The MRR figure is threaded onto mrr_last_year_same_months.
    assert inputs.mrr_last_year_same_months == {"2099-05": 7_500.0}
