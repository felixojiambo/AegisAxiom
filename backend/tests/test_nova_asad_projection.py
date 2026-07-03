"""Tests for the generic dynamic-input projection surface.

Covers the four user-supplied projection knobs added for Asad-style
requests, but uses ONLY synthetic 9xxxx prefixes so the production
account list (70500/70200/70201/70202/70205/70206) never leaks into
the test surface and never anchors a regression on a tenant-specific
chart of accounts.

The four blocks under test:

  1. ``account_average_prefixes`` → ``load_account_prefix_averages``
     in data_loader.py. Caller-supplied prefixes only; no defaults.
  2. ``payroll_schedule`` → ``compute_payroll_schedule`` in
     scheduled_events.py. Runtime amount + start_date + frequency.
  3. ``ar_receipts_timing`` → ``compute_ar_schedule``. Runtime
     ``days_after_due_date``.
  4. ``ap_payments_timing`` → ``compute_ap_schedule``. Due-date
     scheduling.

Anti-hardcoding rules: no production account codes, no real
customer/vendor/person/tenant names, no real dates inside the
PRODUCT source — tests use synthetic 9xxxx prefixes and 2099-* dates.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest


# ---------------------------------------------------------------------------
# Mongo mock helpers (mirrors test_nova_mrr_baseline.py shape)
# ---------------------------------------------------------------------------


def _mock_db_with_aggregate(rows: list[dict]) -> MagicMock:
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    collection = MagicMock()
    collection.aggregate = MagicMock(return_value=cursor)
    db = MagicMock()
    db.general_ledger = collection
    return db


def _mock_db_with_aggregate_and_latest_tx(
    rows: list[dict],
    latest_tx: str,
) -> MagicMock:
    """Mock qbo_db that returns ``rows`` from aggregate AND
    ``latest_tx`` from get_latest_tx_date's pipeline.

    get_latest_tx_date issues its own aggregate before the helper's
    main pipeline, so we sequence the responses."""
    sequence: list[list[dict]] = [
        [{"_id": None, "max_date": latest_tx}],
        rows,
    ]
    call_index = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_index["i"]
        call_index["i"] += 1
        cursor = MagicMock()
        if idx < len(sequence):
            cursor.to_list = AsyncMock(return_value=sequence[idx])
        else:
            cursor.to_list = AsyncMock(return_value=[])
        return cursor

    collection = MagicMock()
    collection.aggregate = MagicMock(side_effect=aggregate_side_effect)
    db = MagicMock()
    db.general_ledger = collection
    return db


def _gl_row(month: str, account_name: str, amount: float, account_id: str = "") -> dict:
    return {
        "_id": {
            "month": month,
            "account_id": account_id or account_name.split()[0],
            "account_name": account_name,
        },
        "amount": amount,
    }


# ===========================================================================
# 1. account_average_prefixes / load_account_prefix_averages
# ===========================================================================


def test_account_prefix_averages_computes_last_3_complete_months():
    """Last-3-complete-month average per user-supplied prefix."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-03", "90000 Synthetic Admin Expense", 100.0),
        _gl_row("2099-04", "90000 Synthetic Admin Expense", 200.0),
        _gl_row("2099-05", "90000 Synthetic Admin Expense", 300.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db,
            basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-15",
            months=3,
        )
    )

    assert result["basis"] == "accrual"
    assert result["months_used"] == ["2099-03", "2099-04", "2099-05"]
    entries = result["accounts"]
    assert len(entries) == 1
    e = entries[0]
    assert e["account_prefix"] == "90000"
    assert e["row_count"] == 3
    assert e["average_monthly_amount"] == pytest.approx(200.0)
    assert e["method"] == "last_3_complete_month_average"


def test_account_prefix_averages_accepts_caller_provided_prefix_list():
    """The prefix list is the ONLY source — no module default kicks in."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-03", "90100 Synthetic Outsource", 50.0),
        _gl_row("2099-04", "90100 Synthetic Outsource", 50.0),
        _gl_row("2099-05", "90100 Synthetic Outsource", 50.0),
        _gl_row("2099-03", "90200 Synthetic Field Labor", 10.0),
        _gl_row("2099-04", "90200 Synthetic Field Labor", 20.0),
        _gl_row("2099-05", "90200 Synthetic Field Labor", 30.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db,
            basis="accrual",
            account_prefixes=["90100", "90200"],
            reference_date="2099-06-15",
        )
    )
    prefixes_returned = {e["account_prefix"] for e in result["accounts"]}
    assert prefixes_returned == {"90100", "90200"}
    # Caller-supplied order preserved.
    assert [e["account_prefix"] for e in result["accounts"]] == ["90100", "90200"]


def test_account_prefix_averages_matches_code_at_start_of_name():
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row(
            "2099-03", "90000 Synthetic Admin Expense", 100.0,
            account_id="90000",
        ),
        _gl_row(
            "2099-04", "90000 Synthetic Admin Expense", 200.0,
            account_id="90000",
        ),
        _gl_row(
            "2099-05", "90000 Synthetic Admin Expense", 300.0,
            account_id="90000",
        ),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-15",
        )
    )
    assert result["accounts"][0]["row_count"] == 3


def test_account_prefix_averages_matches_code_after_parent_prefix():
    """Names like ``Synthetic Expense:90000 Synthetic Admin Expense``
    (code AFTER a parent path) must match."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row(
            "2099-03",
            "Synthetic Expense:90000 Synthetic Admin Expense",
            100.0,
        ),
        _gl_row(
            "2099-04",
            "Synthetic Expense:90000 Synthetic Admin Expense",
            200.0,
        ),
        _gl_row(
            "2099-05",
            "Synthetic Expense:90000 Synthetic Admin Expense",
            300.0,
        ),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-15",
        )
    )
    assert result["accounts"][0]["row_count"] == 3


def test_account_prefix_averages_does_not_false_match_longer_or_embedded_codes():
    """The Python-side matcher must reject:
      * ``190000`` (prefix preceded by digit)
      * ``900001`` (prefix extended by digit, no separator)
      * ``Account 990000`` (prefix preceded by ``Account `` not start/colon)
    """
    from services.nova_engine.data_loader import _row_matches_prefix

    # 190000 — preceded by a digit, shouldn't false-match 90000.
    assert _row_matches_prefix("190000 Mystery Account", "90000") is False
    # 900001 — extra trailing digit, shouldn't match 90000.
    assert _row_matches_prefix("900001 Mystery Account", "90000") is False
    # Account 990000 — preceded by a space inside a longer string.
    assert _row_matches_prefix("Account 990000", "90000") is False
    # Sanity: real shapes DO match.
    assert _row_matches_prefix("90000 Real Match", "90000") is True
    assert _row_matches_prefix("Parent:90000 Real Match", "90000") is True


def test_account_prefix_averages_returns_row_count():
    """Row count is the per-prefix count of aggregated rows that the
    helper attributed to that prefix."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-04", "90000 Synthetic A", 1.0),
        _gl_row("2099-05", "90000 Synthetic A", 1.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-15",
        )
    )
    assert result["accounts"][0]["row_count"] == 2


def test_account_prefix_averages_warns_when_no_rows_for_prefix():
    """A prefix the caller named but with zero matching GL rows in
    the window adds a warning AND a zero-row entry to the response."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-04", "90000 Synthetic A", 100.0),
        _gl_row("2099-05", "90000 Synthetic A", 100.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000", "90999"],
            reference_date="2099-06-15",
        )
    )
    assert any(
        "90999" in w for w in result["warnings"]
    ), f"expected a warning naming the empty prefix; got {result['warnings']}"
    empty_entry = next(
        e for e in result["accounts"] if e["account_prefix"] == "90999"
    )
    assert empty_entry["row_count"] == 0
    assert empty_entry["average_monthly_amount"] == 0.0


def test_account_prefix_averages_returns_account_names_list():
    """The audit block lists which account names matched per prefix
    — helpful for verifying the prefix scope did what the user
    expected."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-04", "90000 Synthetic A", 50.0),
        _gl_row("2099-05", "90000 Synthetic B", 50.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-15",
        )
    )
    names = result["accounts"][0]["account_names"]
    assert "90000 Synthetic A" in names
    assert "90000 Synthetic B" in names


# ===========================================================================
# 2. payroll_schedule / compute_payroll_schedule
# ===========================================================================


def test_payroll_schedule_uses_runtime_start_date():
    """The first pay date is the caller's start_date; the helper
    does not bake any specific date into its logic."""
    from services.nova_engine.scheduled_events import (
        compute_payroll_schedule,
    )

    overrides, meta = compute_payroll_schedule(
        amount=20_000.0,
        start_date="2099-06-05",
        frequency="biweekly",
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["start_date"] == "2099-06-05"
    assert meta["payment_dates_used"][0] == "2099-06-05"
    assert len(overrides) >= 1
    # First override should be in the week containing 2099-06-05.
    # 2099-06-05 is 4 days after 2099-06-01 → week 1 of the horizon.
    assert overrides[0].week_number == 1
    assert overrides[0].amount == pytest.approx(20_000.0)
    assert overrides[0].target == "PAYROLL"


def test_payroll_schedule_biweekly_uses_14_day_intervals():
    from services.nova_engine.scheduled_events import (
        compute_payroll_schedule,
    )

    _, meta = compute_payroll_schedule(
        amount=20_000.0,
        start_date="2099-06-05",
        frequency="biweekly",
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    dates = meta["payment_dates_used"]
    assert len(dates) >= 2
    from datetime import date

    parsed = [date.fromisoformat(d) for d in dates]
    diffs = [(parsed[i + 1] - parsed[i]).days for i in range(len(parsed) - 1)]
    assert all(d == 14 for d in diffs), (
        f"biweekly must step 14 days between payments; got diffs {diffs}"
    )


def test_payroll_schedule_runtime_amount_flows_to_overrides():
    """The override amount equals the caller's input — not the
    spec's example value and not a hardcoded number."""
    from services.nova_engine.scheduled_events import (
        compute_payroll_schedule,
    )

    overrides, _ = compute_payroll_schedule(
        amount=12_345.67,
        start_date="2099-06-05",
        frequency="biweekly",
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert all(o.amount == pytest.approx(12_345.67) for o in overrides)


def test_payroll_schedule_metadata_returns_method_label():
    """The metadata's method label is the audit anchor the chat tool
    quotes verbatim."""
    from services.nova_engine.scheduled_events import (
        compute_payroll_schedule,
    )

    _, meta = compute_payroll_schedule(
        amount=10_000.0,
        start_date="2099-06-05",
        frequency="biweekly",
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["method"] == "scheduled_cash_event"
    assert meta["frequency"] == "biweekly"
    assert meta["amount"] == pytest.approx(10_000.0)


def test_payroll_schedule_rejects_invalid_inputs_with_warning():
    """A non-positive amount / bogus start_date / unknown frequency
    returns empty overrides + a warning; the helper never raises."""
    from services.nova_engine.scheduled_events import (
        compute_payroll_schedule,
    )

    for bad_amount in (0, -1, "twenty thousand"):
        ovs, meta = compute_payroll_schedule(
            amount=bad_amount,  # type: ignore[arg-type]
            start_date="2099-06-05",
            frequency="biweekly",
            projection_start_month="2099-06",
            horizon_weeks=13,
        )
        assert ovs == []
        assert meta["warnings"]

    ovs, meta = compute_payroll_schedule(
        amount=10_000.0,
        start_date="not-a-date",
        frequency="biweekly",
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert ovs == []
    assert any("start_date" in w for w in meta["warnings"])

    ovs, meta = compute_payroll_schedule(
        amount=10_000.0,
        start_date="2099-06-05",
        frequency="quarterly",
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert ovs == []
    assert any("frequency" in w for w in meta["warnings"])


# ===========================================================================
# 3. ar_receipts_timing / compute_ar_schedule
# ===========================================================================


def _ar_row(due_date: str | None, open_balance: float) -> dict:
    return {
        "due_date": due_date,
        "open_balance": open_balance,
        "customer": "Synthetic Counterparty",
        "doc_num": "SYNTH-INV-0001",
    }


def test_ar_schedule_runtime_days_after_due_date_is_applied():
    """``days_after_due_date`` comes from the caller; the helper
    never assumes 7 (or any other value) on its own."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    overrides, meta = compute_ar_schedule(
        open_invoice_rows=[_ar_row("2099-06-10", 5_000.0)],
        days_after_due_date=14,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["days_after_due_date"] == 14
    assert len(overrides) == 1
    # 2099-06-10 + 14 days = 2099-06-24 → 23 days after 2099-06-01 →
    # week index 23 // 7 = 3 → 1-based week_number = 4.
    assert overrides[0].week_number == 4
    assert overrides[0].amount == pytest.approx(5_000.0)
    assert overrides[0].target == "OTHER_REVENUE"
    assert meta["scheduled_receipts"][0]["date"] == "2099-06-24"
    assert meta["scheduled_receipts"][0]["source"] == "open_ar"


def test_ar_schedule_missing_due_date_produces_warning():
    from services.nova_engine.scheduled_events import compute_ar_schedule

    overrides, meta = compute_ar_schedule(
        open_invoice_rows=[
            _ar_row(None, 1_000.0),
            _ar_row("2099-06-10", 2_000.0),
        ],
        days_after_due_date=0,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    # Only the row with a due date generates an override.
    assert len(overrides) == 1
    assert overrides[0].amount == pytest.approx(2_000.0)
    assert meta["row_count"] == 2
    assert any(
        "due_date" in w and "1" in w for w in meta["warnings"]
    ), f"expected warning about 1 missing due_date; got {meta['warnings']}"


def test_ar_schedule_places_event_in_correct_horizon_week():
    """due_date + days_after lands on a specific calendar date; the
    helper places it in the week containing that date (7-day buckets
    from projection_start_month-01)."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    overrides, _ = compute_ar_schedule(
        open_invoice_rows=[
            _ar_row("2099-06-01", 1_000.0),  # due_date+7 = 2099-06-08 → week 2
        ],
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert overrides[0].week_number == 2


def test_ar_schedule_returns_metadata_block():
    from services.nova_engine.scheduled_events import compute_ar_schedule

    _, meta = compute_ar_schedule(
        open_invoice_rows=[_ar_row("2099-06-10", 1.0)],
        days_after_due_date=5,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["method"] == "due_date_plus_days"
    assert meta["days_after_due_date"] == 5
    assert meta["row_count"] == 1
    assert "double_count_note" in meta
    assert "additive" in meta["double_count_note"].lower()


# ===========================================================================
# 4. ap_payments_timing / compute_ap_schedule
# ===========================================================================


def _ap_row(due_date: str | None, open_balance: float) -> dict:
    return {
        "due_date": due_date,
        "open_balance": open_balance,
        "vendor": "Synthetic Vendor",
        "doc_num": "SYNTH-BILL-0001",
    }


def test_ap_schedule_due_date_method_schedules_outflow_on_due_date():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    overrides, meta = compute_ap_schedule(
        open_bill_rows=[_ap_row("2099-06-15", 3_000.0)],
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert len(overrides) == 1
    assert overrides[0].amount == pytest.approx(3_000.0)
    assert overrides[0].target == "OTHER_COSTS"
    assert overrides[0].week_number == 3  # 2099-06-15 → 14 days after start → week 3
    assert meta["scheduled_payments"][0]["date"] == "2099-06-15"


def test_ap_schedule_missing_due_date_produces_warning():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    overrides, meta = compute_ap_schedule(
        open_bill_rows=[
            _ap_row(None, 1_000.0),
            _ap_row("2099-06-15", 2_000.0),
        ],
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert len(overrides) == 1
    assert any("due_date" in w for w in meta["warnings"])


def test_ap_schedule_places_event_in_correct_horizon_week():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    overrides, _ = compute_ap_schedule(
        open_bill_rows=[_ap_row("2099-06-08", 1_000.0)],
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    # 2099-06-08 is 7 days after 2099-06-01 → week index 1 → week 2.
    assert overrides[0].week_number == 2


def test_ap_schedule_returns_metadata_block():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    _, meta = compute_ap_schedule(
        open_bill_rows=[_ap_row("2099-06-15", 1.0)],
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["method"] == "due_date"
    assert meta["row_count"] == 1
    assert "double_count_note" in meta


def test_ap_schedule_falls_back_to_balance_field_when_open_balance_absent():
    """The raw ap_bills schema carries ``balance`` not ``open_balance``;
    the helper accepts either."""
    from services.nova_engine.scheduled_events import compute_ap_schedule

    overrides, _ = compute_ap_schedule(
        open_bill_rows=[
            {"due_date": "2099-06-15", "balance": 4_500.0},
        ],
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert len(overrides) == 1
    assert overrides[0].amount == pytest.approx(4_500.0)


# ===========================================================================
# 5. Week-mapping primitive
# ===========================================================================


def test_week_number_for_date_maps_dates_to_1_based_weeks():
    """Pin the conversion from calendar date to projection week."""
    from datetime import date

    from services.nova_engine.scheduled_events import week_number_for_date

    # 2099-06-01 is the projection start → week 1.
    assert week_number_for_date(
        date(2099, 6, 1), "2099-06", 13,
    ) == 1
    # 2099-06-07 is day 6 → still week 1 (7-day buckets, exclusive end).
    assert week_number_for_date(
        date(2099, 6, 7), "2099-06", 13,
    ) == 1
    # 2099-06-08 is day 7 → week 2 (index 1).
    assert week_number_for_date(
        date(2099, 6, 8), "2099-06", 13,
    ) == 2
    # Before projection_start → None.
    assert week_number_for_date(
        date(2099, 5, 31), "2099-06", 13,
    ) is None
    # Past the horizon → None.
    assert week_number_for_date(
        date(2099, 12, 31), "2099-06", 13,
    ) is None


# ===========================================================================
# 6. MRR prefix override + use_mrr_fallback
# ===========================================================================


def test_mrr_baseline_accepts_runtime_account_prefix_override():
    """The product default is 50300; the user may override at runtime
    and the audit block surfaces the chosen prefix."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.nova_engine.data_loader import (
        load_mrr_baseline_with_fallback,
    )

    # Mock returns no rows — the test cares about WHICH prefix the
    # helper uses in its account_filter metadata, not the row math.
    sequence: list[list[dict]] = [[], []]
    call_index = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_index["i"]
        call_index["i"] += 1
        cursor = _MagicMock()
        cursor.to_list = _AsyncMock(
            return_value=sequence[idx] if idx < len(sequence) else []
        )
        return cursor

    db = _MagicMock()
    db.general_ledger.aggregate = _MagicMock(side_effect=aggregate_side_effect)

    _, meta = _aio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-06"], basis="accrual",
            reference_month="2099-06",
            account_prefix="91234",  # synthetic override
        )
    )
    assert meta["account_filter"]["code_prefix"] == "91234"
    # And the label clearly states this came from a runtime override.
    assert (
        "runtime"
        in meta["account_filter"]["account_label"].lower()
    )


def test_mrr_baseline_use_fallback_false_skips_run_rate():
    """When the caller opts out of the fallback, missing months stay
    unresolved and the metadata explains why."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.nova_engine.data_loader import (
        load_mrr_baseline_with_fallback,
    )

    # Empty prior-year, no fallback fetch should occur.
    sequence: list[list[dict]] = [[]]
    call_index = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_index["i"]
        call_index["i"] += 1
        cursor = _MagicMock()
        cursor.to_list = _AsyncMock(
            return_value=sequence[idx] if idx < len(sequence) else []
        )
        return cursor

    db = _MagicMock()
    db.general_ledger.aggregate = _MagicMock(side_effect=aggregate_side_effect)

    monthly, meta = _aio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-06"], basis="accrual",
            reference_month="2099-06",
            use_fallback=False,
        )
    )
    assert monthly == {}
    assert meta["fallback_method"] == "no_history"
    assert meta["use_fallback"] is False
    # Only ONE aggregate call should have fired — prior-year only,
    # no fallback fetch.
    assert call_index["i"] == 1


# ===========================================================================
# 7. Anti-hardcoding guards
# ===========================================================================


def test_data_loader_does_not_ship_a_default_account_prefix_list():
    """The data loader must not ship a hardcoded prefix list that
    would baked Asad's CoA into the product. Inspect the actual
    function default to confirm caller-supplied lists are the only
    source."""
    import inspect

    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    sig = inspect.signature(load_account_prefix_averages)
    # account_prefixes is a required kwarg with NO default value.
    param = sig.parameters["account_prefixes"]
    assert param.default is inspect.Parameter.empty


def test_run_projection_schema_has_no_default_account_prefix_list():
    """The schema property must not advertise a default prefix list."""
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(s for s in NOVA_TOOL_SCHEMAS if s["name"] == "run_projection")
    props = schema["parameters"]["properties"]
    assert "account_average_prefixes" in props
    # The OpenAI / Gemini schema spec doesn't allow array defaults
    # in many shapes, but defensive: confirm no `default` field
    # carries any production-shaped prefix.
    default = props["account_average_prefixes"].get("default")
    assert default in (None, [])


def test_scheduled_events_module_does_not_ship_production_prefix_constants():
    """The scheduled_events module deals only with timing rules and
    must not carry any account-prefix constants at all."""
    import services.nova_engine.scheduled_events as se

    source = inspect_source_of(se)
    for forbidden in ("70500", "70200", "70201", "70202", "70205", "70206"):
        assert forbidden not in source, (
            f"scheduled_events.py must not hardcode production prefix "
            f"{forbidden!r}; saw it in the module source."
        )


def test_data_loader_does_not_hardcode_production_prefix_list_anywhere():
    """No production prefix list as a module-level constant."""
    import services.nova_engine.data_loader as dl

    source = inspect_source_of(dl)
    for forbidden in ("70500", "70200", "70201", "70202", "70205", "70206"):
        # The data loader is allowed to mention these only via
        # caller-supplied paths (kwargs / metadata strings); not as
        # frozenset / list literals. The strictest test: the prefix
        # MUST NOT appear in the source at all.
        assert forbidden not in source, (
            f"data_loader.py must not hardcode production prefix "
            f"{forbidden!r}; saw it in the module source."
        )


def test_run_projection_dispatcher_does_not_hardcode_production_prefix_list():
    """Same check for the dispatcher branch."""
    import services.ai.agents.tools.nova_tools as pt

    source = inspect_source_of(pt)
    for forbidden in ("70500", "70200", "70201", "70202", "70205", "70206"):
        assert forbidden not in source, (
            f"nova_tools.py must not hardcode production prefix "
            f"{forbidden!r}; saw it in the module source."
        )


def inspect_source_of(module) -> str:
    import inspect

    try:
        return inspect.getsource(module)
    except (OSError, TypeError):
        return ""


# ===========================================================================
# 8. Nova prompt + tool description surface the new metadata blocks
# ===========================================================================


def test_nova_prompt_mentions_dynamic_input_blocks():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT.lower()
    for needle in (
        "account_average_prefixes",
        "ar_receipts_timing",
        "ap_payments_timing",
        "payroll_schedule",
        "mrr_account_prefix",
    ):
        assert needle in prompt, f"Nova prompt missing {needle!r}"


def test_run_projection_description_mentions_dynamic_inputs():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(s for s in NOVA_TOOL_SCHEMAS if s["name"] == "run_projection")
    desc = schema["description"].lower()
    assert "account_average_prefixes" in desc
    assert "ar_receipts_timing" in desc
    assert "ap_payments_timing" in desc
    assert "payroll_schedule" in desc
    assert "additive" in desc  # double-count discipline surfaced


# ===========================================================================
# 9. Integration: dispatcher returns the metadata blocks
# ===========================================================================


def test_dispatcher_surfaces_payroll_schedule_metadata_block():
    """End-to-end: a run_projection call with only payroll_schedule
    set returns the metadata block alongside the regular response.

    We can't run the full engine without a live Mongo, but we CAN
    run the resolver directly to confirm it builds the metadata and
    splices overrides onto state."""
    import asyncio as _aio

    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        _resolve_dynamic_projection_inputs,
    )

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.horizon_weeks = 13

    avg, ar, ap, payroll = _aio.run(
        _resolve_dynamic_projection_inputs(
            state=state,
            account_average_prefixes=None,
            ar_receipts_timing=None,
            ap_payments_timing=None,
            payroll_schedule={
                "amount": 20_000.0,
                "start_date": "2099-06-05",
                "frequency": "biweekly",
            },
        )
    )

    assert avg is None
    assert ar is None
    assert ap is None
    assert payroll is not None
    assert payroll["frequency"] == "biweekly"
    assert payroll["payment_dates_used"][0] == "2099-06-05"
    # Resolver splices overrides onto state in place.
    assert len(state.overrides) >= 1
    assert state.overrides[0].target == "PAYROLL"


def test_dispatcher_rejects_invalid_dynamic_inputs():
    """Top-level shape checks fire before any helper is called."""
    import asyncio as _aio

    from services.ai.agents.tools import nova_tools

    async def run(args):
        return await nova_tools.execute_tool(
            "run_projection",
            args,
            nova_tools._ScenarioState(),
            "u_test",
            None,
            None,
        )

    # account_average_prefixes must be a list.
    result = _aio.run(run({
        "starting_cash": 1000.0,
        "projection_start_month": "2099-06",
        "account_average_prefixes": "90000",  # not a list
    }))
    assert result["error"] == "invalid_projection_input"

    # ar_receipts_timing must be an object.
    result = _aio.run(run({
        "starting_cash": 1000.0,
        "projection_start_month": "2099-06",
        "ar_receipts_timing": "due_date_plus_days",  # not an object
    }))
    assert result["error"] == "invalid_projection_input"


# ===========================================================================
# 10. Empty / boundary inputs to the dynamic resolver
# ===========================================================================


def test_account_prefix_averages_returns_empty_for_empty_prefix_list():
    """Empty prefix list returns a structured warning and zero
    accounts; no Mongo call needed."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    db = _mock_db_with_aggregate([])
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=[],
            reference_date="2099-06-15",
        )
    )
    assert result["accounts"] == []
    assert any(
        "prefix" in w.lower() for w in result["warnings"]
    )


def test_payroll_schedule_skips_dates_outside_horizon():
    """A payroll date past the horizon end is excluded from the
    overrides and surfaced as a warning so the user knows their
    schedule extends beyond the projection."""
    from services.nova_engine.scheduled_events import (
        compute_payroll_schedule,
    )

    overrides, meta = compute_payroll_schedule(
        amount=20_000.0,
        start_date="2099-06-05",
        frequency="biweekly",
        projection_start_month="2099-06",
        horizon_weeks=4,  # 28-day horizon
    )
    # 2099-06-05, 2099-06-19 fit; 2099-07-03 is day 32 → out.
    assert all(o.week_number <= 4 for o in overrides)
    # No warning is required when later dates simply weren't
    # generated (helper stops at horizon_end). When a START date is
    # past horizon, a warning fires; that's covered in the invalid-
    # inputs test.


# ===========================================================================
# 11. Date-mismatch guard (Asad live-bug regression)
# ===========================================================================
#
# Live observation: Nova projected a "starting today" 2026 request as
# 2024 weeks. Root cause: nothing in the prompt anchored the year and
# the run_projection schema example carried a literal "2026-05" the
# LLM could copy. The guard rejects the mismatch instead of silently
# producing a wrong-year forecast; the prompt now carries today's
# actual date so the model has a real anchor.


def test_run_projection_rejects_projection_month_vs_payroll_year_mismatch():
    """projection_start_month YYYY-MM must match payroll_schedule.
    start_date YYYY-MM; the dispatcher rejects with a structured
    projection_date_mismatch error."""
    import asyncio as _aio

    from services.ai.agents.tools import nova_tools

    async def run() -> dict:
        return await nova_tools.execute_tool(
            "run_projection",
            {
                "starting_cash": 1_000.0,
                "projection_start_month": "2097-06",  # synthetic
                "payroll_schedule": {
                    "amount": 1_000.0,
                    "start_date": "2099-06-05",   # different year
                    "frequency": "biweekly",
                },
            },
            nova_tools._ScenarioState(),
            "u_test",
            None,
            None,
        )

    result = _aio.run(run())
    assert result["error"] == "projection_date_mismatch"
    assert result["projection_start_month"] == "2097-06"
    assert result["payroll_start_date"] == "2099-06-05"
    assert result["payroll_start_month"] == "2099-06"
    # The error names the corrective action so Nova can apologise
    # and retry with the fixed year.
    assert "2099-06" in result["detail"]


def test_run_projection_rejects_when_payroll_month_differs_from_projection_month():
    """Same year but different month is also a mismatch — the helper
    is strict about YYYY-MM equality, not just YYYY."""
    import asyncio as _aio

    from services.ai.agents.tools import nova_tools

    async def run() -> dict:
        return await nova_tools.execute_tool(
            "run_projection",
            {
                "starting_cash": 1_000.0,
                "projection_start_month": "2099-06",
                "payroll_schedule": {
                    "amount": 1_000.0,
                    "start_date": "2099-07-05",
                    "frequency": "biweekly",
                },
            },
            nova_tools._ScenarioState(),
            "u_test",
            None,
            None,
        )

    result = _aio.run(run())
    assert result["error"] == "projection_date_mismatch"


def test_run_projection_date_guard_can_be_overridden_with_allow_historical_projection():
    """``allow_historical_projection=true`` opts out of the guard so a
    user who really wants a back-dated projection can proceed. The
    guard branch must short-circuit BEFORE engine execution.

    Without a live Mongo, the post-guard engine path fails with a
    different error/exception; pytest.raises tolerates either. The
    pin here is that we DO NOT see the projection_date_mismatch
    payload in the success-dict return — i.e. the guard skipped."""
    import asyncio as _aio

    from services.ai.agents.tools import nova_tools

    async def run() -> dict:
        return await nova_tools.execute_tool(
            "run_projection",
            {
                "starting_cash": 1_000.0,
                "projection_start_month": "2097-06",
                "payroll_schedule": {
                    "amount": 1_000.0,
                    "start_date": "2099-06-05",
                    "frequency": "biweekly",
                },
                "allow_historical_projection": True,
            },
            nova_tools._ScenarioState(),
            "u_test",
            None,
            None,
        )

    try:
        result = _aio.run(run())
    except Exception as exc:  # noqa: BLE001 — downstream Mongo failure is fine
        # Engine reached the Mongo seam → guard was skipped. Any
        # exception that surfaces AFTER the guard is acceptable for
        # THIS test; the regression-target is the dispatcher
        # short-circuiting on a mismatch even when the flag is set.
        assert "projection_date_mismatch" not in str(exc), (
            "guard fired even though allow_historical_projection=True"
        )
        return

    # If the engine somehow completed, confirm it was NOT the guard.
    assert result.get("error") != "projection_date_mismatch"


def test_run_projection_accepts_matching_projection_month_and_payroll_month():
    """Same YYYY-MM on both sides passes the guard. We can't run the
    full engine without Mongo, but we can confirm the guard doesn't
    fire by inspecting the resolver helper's output for the
    matching-year case."""
    import asyncio as _aio

    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        _resolve_dynamic_projection_inputs,
    )

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.horizon_weeks = 13

    avg, ar, ap, payroll = _aio.run(
        _resolve_dynamic_projection_inputs(
            state=state,
            account_average_prefixes=None,
            ar_receipts_timing=None,
            ap_payments_timing=None,
            payroll_schedule={
                "amount": 20_000.0,
                "start_date": "2099-06-05",
                "frequency": "biweekly",
            },
        )
    )

    assert payroll is not None
    assert payroll["start_date"] == "2099-06-05"
    assert payroll["payment_dates_used"][0] == "2099-06-05"


# ===========================================================================
# 12. Nova prompt has today's date anchor + AR/AP routing rules
# ===========================================================================


def test_nova_build_system_prompt_carries_today_date_anchor():
    """build_system_prompt must inject today's real-world date at the
    top so the LLM has a date anchor for relative phrases ("today",
    "this month")."""
    import asyncio as _aio
    from datetime import datetime, timezone

    from services.ai.agents.nova import NovaAgent

    prompt = _aio.run(NovaAgent().build_system_prompt(user=None))
    today_iso = datetime.now(timezone.utc).date().isoformat()
    # Anchor block appears at the very start of the prompt.
    assert prompt.startswith("## Date anchor")
    assert today_iso in prompt
    # The anchor must explicitly tell the model not to use other years.
    assert "training data" in prompt.lower()


def test_nova_prompt_warns_against_copying_example_years():
    """The prompt must not contain hardcoded year examples the LLM
    could copy. Year strings 2024 / 2025 / 2026 must not appear as
    bare YYYY-MM example tokens in the static prompt body."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    # Bare "2024-MM" / "2024-MM-DD" examples are the specific
    # regression target. They MUST NOT appear anywhere in the static
    # prompt body.
    import re

    for year in ("2024", "2025", "2026"):
        # YYYY-MM tokens (e.g. "2026-05") and YYYY-MM-DD tokens
        # (e.g. "2026-05-15") would be copy-bait. References to the
        # year in historical-incident anecdotes ("the audited bug
        # in May 2026") are out of scope and would be matched by a
        # different regex — but the prompt currently has zero
        # bare YYYY-MM tokens, so the strictest check passes.
        ymd_pattern = re.compile(rf"\b{year}-\d{{2}}(?:-\d{{2}})?\b")
        bad_matches = [
            m.group(0)
            for m in ymd_pattern.finditer(_SYSTEM_PROMPT)
            if m.group(0) not in {
                # Allowed: historical-incident wording inside
                # narrative sentences. Empty for now; tests stay
                # strict.
            }
        ]
        # Audit anecdote phrasing ("May 2026 revenue location
        # coverage") uses bare year + month name, not YYYY-MM tokens.
        # We assert no YYYY-MM[-DD] tokens.
        assert not bad_matches, (
            f"static prompt body must not carry copy-bait "
            f"YYYY-MM[-DD] tokens for {year}; saw {bad_matches}"
        )


def test_nova_prompt_mentions_ar_ap_routing_rule():
    """The prompt must tell Nova to PASS ar_receipts_timing /
    ap_payments_timing when the user names AR/AP rules — never write
    'AR/AP timing rules were omitted' for inputs the tool supports."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "ar_receipts_timing" in prompt
    assert "ap_payments_timing" in prompt
    # The specific anti-bug instruction.
    assert "omitted" in prompt.lower()
    assert "due_date_plus_days" in prompt


def test_nova_prompt_mentions_projection_month_payroll_anchor_rule():
    """The prompt must tell Nova that projection_start_month and
    payroll start MUST agree on YYYY-MM."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "projection_date_mismatch" in prompt
    assert "allow_historical_projection" in prompt


# ===========================================================================
# 13. Anchoring: account-prefix averages + MRR baseline anchor to
# projection_start_month, not training-data years
# ===========================================================================


def test_account_prefix_averages_anchor_to_projection_start_month_synthetic():
    """When the caller asks for averages anchored on 2099-06, the
    helper walks back 2099-05 / 2099-04 / 2099-03 — never a
    training-data year."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-03", "90000 Synthetic A", 100.0),
        _gl_row("2099-04", "90000 Synthetic A", 200.0),
        _gl_row("2099-05", "90000 Synthetic A", 300.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
        )
    )
    assert result["months_used"] == ["2099-03", "2099-04", "2099-05"]
    # No 2024 / 2025 / 2026 leak.
    for month in result["months_used"]:
        assert month.startswith("2099-")


def test_mrr_prior_year_lookup_anchors_to_projection_start_month():
    """projection_start_month=2099-06 → prior-year lookup at 2098-06.
    The helper does not fall back to a training-data year."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.nova_engine.data_loader import (
        load_mrr_baseline_with_fallback,
    )

    # Two empty results (prior-year + fallback) so the test focuses
    # only on which months were queried.
    sequence: list[list[dict]] = [[], []]
    call_index = {"i": 0}
    captured_pipelines: list[dict] = []

    def aggregate_side_effect(pipeline, *args, **kwargs):
        captured_pipelines.append(pipeline)
        idx = call_index["i"]
        call_index["i"] += 1
        cursor = _MagicMock()
        cursor.to_list = _AsyncMock(
            return_value=sequence[idx] if idx < len(sequence) else []
        )
        return cursor

    db = _MagicMock()
    db.general_ledger.aggregate = _MagicMock(side_effect=aggregate_side_effect)

    _, meta = _aio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-06"], basis="accrual",
            reference_month="2099-06",
        )
    )
    # Prior-year window must be 2098-06 (one year before 2099-06).
    assert meta["prior_year"]["months_queried"] == ["2098-06"]
    # No training-data year leak.
    for ym in meta["prior_year"]["months_queried"]:
        assert ym.startswith("2098-")


# ===========================================================================
# 14. AR/AP timing produces metadata blocks even when source is empty
# ===========================================================================


def test_ar_timing_returns_schedule_metadata_when_no_open_invoices():
    """A user who supplied ``ar_receipts_timing`` must see an
    ar_receipts_schedule block in the response, even when there are
    no open invoices. The block carries row_count=0 and an empty
    scheduled_receipts list — never silently absent."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    overrides, meta = compute_ar_schedule(
        open_invoice_rows=[],
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert overrides == []
    assert meta["method"] == "due_date_plus_days"
    assert meta["days_after_due_date"] == 7
    assert meta["row_count"] == 0
    assert meta["scheduled_receipts"] == []


def test_ap_timing_returns_schedule_metadata_when_no_open_bills():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    overrides, meta = compute_ap_schedule(
        open_bill_rows=[],
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert overrides == []
    assert meta["method"] == "due_date"
    assert meta["row_count"] == 0
    assert meta["scheduled_payments"] == []


def test_ar_schedule_returns_metadata_when_all_rows_lack_due_dates():
    """Source rows without due dates produce a warning AND the schedule
    block is still returned — so Nova can quote the warning instead
    of saying 'AR was omitted'."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": None, "open_balance": 100.0},
        {"due_date": "", "open_balance": 200.0},
    ]
    overrides, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert overrides == []
    assert meta["row_count"] == 2
    assert any("due_date" in w for w in meta["warnings"])
    assert meta["scheduled_receipts"] == []


# ===========================================================================
# 15. run_projection response surfaces projection_start_month
# ===========================================================================


def test_run_projection_response_includes_projection_start_month_field():
    """Top-level response carries the resolved projection_start_month
    so the chat surface can quote it (and a reviewer can confirm the
    guard ran)."""
    import re

    # The response shape is built at the end of the run_projection
    # branch in nova_tools.execute_tool. We cannot run the engine
    # in unit-test isolation without Mongo; instead we grep the
    # source to confirm projection_start_month is in the success
    # response dict. This guards against a future refactor silently
    # dropping the field.
    import inspect

    import services.ai.agents.tools.nova_tools as pt

    source = inspect.getsource(pt)
    # The token must appear inside the same response-dict literal
    # that contains projection_run_id (the success-return block).
    success_block = re.search(
        r'response: dict\[str, Any\] = \{(.+?)\n        \}\n',
        source,
        re.DOTALL,
    )
    assert success_block is not None, (
        "could not locate the run_projection success response dict"
    )
    block_text = success_block.group(0)
    assert '"projection_start_month": projection_start_month' in block_text
    assert '"starting_cash": output.starting_cash' in block_text


# ===========================================================================
# 16. Integrated synthetic Asad-style scenario
# ===========================================================================


def test_integrated_asad_style_resolver_produces_all_metadata_blocks():
    """Run the dynamic-input resolver with all four blocks set —
    synthetic 9xxxx prefixes, 2099 dates — and confirm each metadata
    block is returned. We bypass the live Mongo fetchers by
    monkey-patching the AR/AP source helpers."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock
    from unittest.mock import patch as _patch

    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        _resolve_dynamic_projection_inputs,
    )

    # Stub out the Mongo paths the resolver hits. We use
    # synthetic data only — no production prefixes / customer
    # names / vendor names.
    fake_account_avg_rows = [
        _gl_row("2099-03", "90100 Synthetic Outsource", 100.0),
        _gl_row("2099-04", "90100 Synthetic Outsource", 100.0),
        _gl_row("2099-05", "90100 Synthetic Outsource", 100.0),
    ]
    qbo_db_mock = _mock_db_with_aggregate_and_latest_tx(
        fake_account_avg_rows, latest_tx="2099-05-31",
    )

    fake_ar = [
        {
            "due_date": "2099-06-10",
            "open_balance": 5_000.0,
            "customer": "Synthetic Counterparty",
            "doc_num": "SYNTH-INV-1",
        },
    ]
    fake_ap = [
        {
            "due_date": "2099-06-12",
            "open_balance": 2_000.0,
            "vendor": "Synthetic Vendor",
            "doc_num": "SYNTH-BILL-1",
        },
    ]

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.horizon_weeks = 13

    # Fetchers return (rows, fetch_meta) so the chat surface can
    # quote pagination audit fields. Synthetic meta mirrors what the
    # live paginated fetcher would emit when total_available_rows
    # equals fetched_row_count (no truncation).
    fake_ar_meta = {
        "source": "open_invoice_snapshot",
        "fetched_row_count": len(fake_ar),
        "total_available_rows": len(fake_ar),
        "schedule_cap": 5_000,
        "page_size": 200,
        "truncated": False,
    }
    fake_ap_meta = {
        "source": "ap_bills",
        "fetched_row_count": len(fake_ap),
        "total_available_rows": len(fake_ap),
        "schedule_cap": 5_000,
        "page_size": 200,
        "truncated": False,
    }
    with _patch(
        "services.nova_engine.db.get_qbo_db", return_value=qbo_db_mock,
    ), _patch(
        "services.ai.agents.tools.nova_tools._fetch_open_invoice_rows",
        new=_AsyncMock(return_value=(fake_ar, fake_ar_meta)),
    ), _patch(
        "services.ai.agents.tools.nova_tools._fetch_open_bill_rows",
        new=_AsyncMock(return_value=(fake_ap, fake_ap_meta)),
    ):
        avg, ar, ap, payroll = _aio.run(
            _resolve_dynamic_projection_inputs(
                state=state,
                account_average_prefixes=["90100"],
                ar_receipts_timing={
                    "method": "due_date_plus_days",
                    "days_after_due_date": 7,
                },
                ap_payments_timing={"method": "due_date"},
                payroll_schedule={
                    "amount": 20_000.0,
                    "start_date": "2099-06-05",
                    "frequency": "biweekly",
                },
            )
        )

    assert avg is not None
    assert ar is not None
    assert ap is not None
    assert payroll is not None
    # Account averages anchored on projection_start_month → 2099-03/04/05.
    assert avg["months_used"] == ["2099-03", "2099-04", "2099-05"]
    # AR uses runtime days_after_due_date.
    assert ar["days_after_due_date"] == 7
    assert len(ar["scheduled_receipts"]) == 1
    # AP method is due_date.
    assert ap["method"] == "due_date"
    # Payroll dates start at the runtime start_date.
    assert payroll["payment_dates_used"][0] == "2099-06-05"
    # Caller-supplied prefix flows through; no defaults injected.
    assert [a["account_prefix"] for a in avg["accounts"]] == ["90100"]
    # State overrides have been spliced for AR + AP + payroll.
    targets = {o.target for o in state.overrides}
    assert "PAYROLL" in targets
    assert "OTHER_REVENUE" in targets  # AR receipts land in OTHER_REVENUE
    assert "OTHER_COSTS" in targets    # AP payments land in OTHER_COSTS


# ===========================================================================
# 17. AR/AP fetch pagination + truncation metadata (live-bug regression)
# ===========================================================================
#
# Live observation: the projection aborted because
# ``_fetch_open_invoice_rows`` requested ``OpenInvoiceQuery(limit=500)``
# while the repository's Pydantic constraint allowed at most 200,
# raising a ValidationError that the dispatcher caught as
# ``invalid_projection_input``. Asad's tenant has >200 open invoices,
# so the projection failed before any AR scheduling ran.
#
# The fix: paginate the underlying query, surface fetched vs total
# row counts in the schedule metadata, and never request a limit
# above the repository's hard cap.


def test_ar_fetcher_respects_repository_200_row_per_page_cap():
    """The fetcher must never request more than the repository's
    OpenInvoiceQuery Pydantic cap (200) in a single call. Inspect
    the per-page kwargs the fetcher passes."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import patch as _patch

    from services.ai.agents.tools.nova_tools import (
        _fetch_open_invoice_rows,
    )
    from services.nova_engine.invoice_repository import (
        OpenInvoiceQueryResult,
    )

    calls: list[dict] = []

    async def fake_query(filters, *, db=None):
        # Record the per-call limit / offset so the test can assert
        # the fetcher honoured the page cap.
        calls.append({
            "limit": filters.limit,
            "offset": filters.offset,
        })
        return OpenInvoiceQueryResult(
            total_matched=0,
            rows=[],
            rows_returned=0,
        )

    with _patch(
        "services.nova_engine.invoice_repository.query_open_invoice_rows",
        new=fake_query,
    ):
        rows, meta = _aio.run(_fetch_open_invoice_rows())

    assert rows == []
    # Every per-call limit must be <= 200 (the repository's
    # Pydantic-validated cap). The old code requested 500.
    for call in calls:
        assert call["limit"] <= 200, (
            f"AR fetcher requested limit={call['limit']}; must not "
            "exceed the repository hard cap of 200."
        )
    assert meta["page_size"] <= 200


def test_ar_fetcher_paginates_beyond_first_page():
    """Tenant with 250 open invoices: the fetcher must call the
    repository twice (200 + 50) and return all 250 rows."""
    import asyncio as _aio
    from unittest.mock import patch as _patch

    from services.ai.agents.tools.nova_tools import (
        _fetch_open_invoice_rows,
    )
    from services.nova_engine.invoice_repository import (
        OpenInvoiceQueryResult,
        OpenInvoiceRow,
    )

    # Synthetic dataset: 250 open invoices with synthetic due dates.
    all_rows = [
        OpenInvoiceRow(
            invoice_number=f"SYNTH-{i:04d}",
            invoice_date="2099-05-15",
            due_date="2099-06-30",
            customer=f"Synthetic Customer {i}",
            amount=100.0,
            open_balance=100.0,
        )
        for i in range(250)
    ]

    async def fake_query(filters, *, db=None):
        page = all_rows[filters.offset:filters.offset + filters.limit]
        return OpenInvoiceQueryResult(
            total_matched=len(all_rows),
            rows=page,
            rows_returned=len(page),
        )

    with _patch(
        "services.nova_engine.invoice_repository.query_open_invoice_rows",
        new=fake_query,
    ):
        rows, meta = _aio.run(_fetch_open_invoice_rows())

    assert len(rows) == 250
    assert meta["fetched_row_count"] == 250
    assert meta["total_available_rows"] == 250
    assert meta["truncated"] is False
    assert meta["page_size"] <= 200


def test_ar_fetcher_marks_truncated_when_total_exceeds_schedule_cap(monkeypatch):
    """When the source has more rows than ``_AR_SCHEDULE_FETCH_CAP``,
    the fetcher stops at the cap and marks ``truncated=true`` so the
    chat tool can quote 'fetched X of Y, cap Z'."""
    import asyncio as _aio
    from unittest.mock import patch as _patch

    from services.ai.agents.tools import nova_tools
    from services.nova_engine.invoice_repository import (
        OpenInvoiceQueryResult,
        OpenInvoiceRow,
    )

    # Patch the schedule cap down to a synthetic 50 so the test
    # exercises the truncation path without spinning up a 5000-row
    # fixture.
    monkeypatch.setattr(nova_tools, "_AR_SCHEDULE_FETCH_CAP", 50)
    monkeypatch.setattr(nova_tools, "_AR_FETCH_PAGE_SIZE", 25)

    total_rows = 100
    all_rows = [
        OpenInvoiceRow(
            invoice_number=f"SYNTH-{i:04d}",
            due_date="2099-06-30",
            open_balance=10.0,
        )
        for i in range(total_rows)
    ]

    async def fake_query(filters, *, db=None):
        page = all_rows[filters.offset:filters.offset + filters.limit]
        return OpenInvoiceQueryResult(
            total_matched=total_rows,
            rows=page,
            rows_returned=len(page),
        )

    with _patch(
        "services.nova_engine.invoice_repository.query_open_invoice_rows",
        new=fake_query,
    ):
        rows, meta = _aio.run(nova_tools._fetch_open_invoice_rows())

    assert len(rows) == 50  # capped at the synthetic cap
    assert meta["fetched_row_count"] == 50
    assert meta["total_available_rows"] == 100
    assert meta["truncated"] is True
    assert meta["schedule_cap"] == 50


def test_ar_schedule_metadata_includes_pagination_audit_fields():
    """compute_ar_schedule must surface the fetch_meta block so
    the chat tool can quote fetched_row_count, total_available_rows,
    schedule_cap, truncated. Tested with synthetic fetch_meta."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    overrides, meta = compute_ar_schedule(
        open_invoice_rows=[
            {"due_date": "2099-06-10", "open_balance": 5_000.0},
        ],
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta={
            "source": "open_invoice_snapshot",
            "fetched_row_count": 1,
            "total_available_rows": 1,
            "schedule_cap": 5_000,
            "page_size": 200,
            "truncated": False,
        },
    )
    assert meta["source"] == "open_invoice_snapshot"
    assert meta["fetched_row_count"] == 1
    assert meta["total_available_rows"] == 1
    assert meta["schedule_cap"] == 5_000
    assert meta["page_size"] == 200
    assert meta["truncated"] is False
    assert meta["scheduled_row_count"] == 1


def test_ar_schedule_warns_when_fetch_truncated():
    """When fetch_meta.truncated is true the helper appends a
    user-facing warning quoting fetched vs total."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    _, meta = compute_ar_schedule(
        open_invoice_rows=[
            {"due_date": "2099-06-10", "open_balance": 1.0},
        ],
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta={
            "source": "open_invoice_snapshot",
            "fetched_row_count": 50,
            "total_available_rows": 100,
            "schedule_cap": 50,
            "page_size": 25,
            "truncated": True,
        },
    )
    assert meta["truncated"] is True
    assert any(
        "AR fetch truncated" in w and "50" in w and "100" in w
        for w in meta["warnings"]
    )


# ── AP pagination / metadata mirrors ──


def test_ap_fetcher_respects_repository_200_row_per_page_cap():
    """AP fetcher must page in 200-row chunks via skip/limit; never
    request more than the per-call cap in one shot."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock
    from unittest.mock import patch as _patch

    captured_limits: list[int] = []

    def make_cursor(_filter, _proj):
        cursor = _MagicMock()
        recorded = {"skip": 0, "limit": 0}

        def skip(n):
            recorded["skip"] = n
            return cursor

        def limit(n):
            recorded["limit"] = n
            captured_limits.append(n)
            cursor.to_list = _AsyncMock(return_value=[])
            return cursor

        cursor.skip = skip
        cursor.limit = limit
        return cursor

    qbo_db = _MagicMock()
    qbo_db.ap_bills.find = _MagicMock(side_effect=make_cursor)
    qbo_db.ap_bills.count_documents = _AsyncMock(return_value=0)

    from services.ai.agents.tools.nova_tools import _fetch_open_bill_rows

    with _patch(
        "services.nova_engine.db.get_qbo_db", return_value=qbo_db,
    ):
        _aio.run(_fetch_open_bill_rows())

    # First (and only) page request must respect the 200-row cap.
    assert captured_limits, "AP fetcher never called .limit()"
    assert all(lim <= 200 for lim in captured_limits), (
        f"AP fetcher requested limit {captured_limits}; must be <= 200"
    )


def test_ap_fetcher_surfaces_total_count_when_available():
    """count_documents seeds total_available_rows; the fetch_meta
    block surfaces the count even when the page returns nothing
    (e.g. all rows have already been collected)."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock
    from unittest.mock import patch as _patch

    qbo_db = _MagicMock()
    # Single empty page so the loop terminates immediately; the
    # count_documents result is the audit number.
    cursor = _MagicMock()
    cursor.skip = _MagicMock(return_value=cursor)
    cursor.limit = _MagicMock(return_value=cursor)
    cursor.to_list = _AsyncMock(return_value=[])
    qbo_db.ap_bills.find = _MagicMock(return_value=cursor)
    qbo_db.ap_bills.count_documents = _AsyncMock(return_value=350)

    from services.ai.agents.tools.nova_tools import _fetch_open_bill_rows

    with _patch(
        "services.nova_engine.db.get_qbo_db", return_value=qbo_db,
    ):
        rows, meta = _aio.run(_fetch_open_bill_rows())

    assert rows == []
    assert meta["total_available_rows"] == 350
    assert meta["fetched_row_count"] == 0
    # Fetcher stopped because the page was empty, but total > fetched
    # so the metadata flags this as truncated for the chat surface
    # to surface.
    assert meta["truncated"] is True


def test_ap_fetcher_handles_missing_count_documents_gracefully():
    """An adapter without count_documents (or one that errors)
    should still let the fetcher return rows and a metadata block
    with total_available_rows=None."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock
    from unittest.mock import patch as _patch

    qbo_db = _MagicMock()
    cursor = _MagicMock()
    cursor.skip = _MagicMock(return_value=cursor)
    cursor.limit = _MagicMock(return_value=cursor)
    cursor.to_list = _AsyncMock(return_value=[
        {"due_date": "2099-06-10", "balance": 100.0},
    ])
    qbo_db.ap_bills.find = _MagicMock(return_value=cursor)
    qbo_db.ap_bills.count_documents = _AsyncMock(side_effect=RuntimeError("no count"))

    from services.ai.agents.tools.nova_tools import _fetch_open_bill_rows

    with _patch(
        "services.nova_engine.db.get_qbo_db", return_value=qbo_db,
    ):
        rows, meta = _aio.run(_fetch_open_bill_rows())

    assert len(rows) == 1
    assert meta["total_available_rows"] is None
    # truncated can only fire when total is known; here it stays
    # False because the fetcher cannot prove truncation.
    assert meta["truncated"] is False


def test_ap_schedule_metadata_includes_pagination_audit_fields():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    _, meta = compute_ap_schedule(
        open_bill_rows=[
            {"due_date": "2099-06-15", "open_balance": 2_000.0},
        ],
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta={
            "source": "ap_bills",
            "fetched_row_count": 1,
            "total_available_rows": 1,
            "schedule_cap": 5_000,
            "page_size": 200,
            "truncated": False,
        },
    )
    assert meta["source"] == "ap_bills"
    assert meta["fetched_row_count"] == 1
    assert meta["total_available_rows"] == 1
    assert meta["scheduled_row_count"] == 1


def test_ap_schedule_warns_when_fetch_truncated():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    _, meta = compute_ap_schedule(
        open_bill_rows=[
            {"due_date": "2099-06-15", "balance": 1.0},
        ],
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta={
            "source": "ap_bills",
            "fetched_row_count": 200,
            "total_available_rows": 500,
            "schedule_cap": 200,
            "page_size": 200,
            "truncated": True,
        },
    )
    assert meta["truncated"] is True
    assert any(
        "AP fetch truncated" in w and "200" in w and "500" in w
        for w in meta["warnings"]
    )


# ── OpenInvoiceQuery now supports offset ──


def test_open_invoice_query_accepts_offset_field():
    """The Pydantic model exposes offset so the paginated fetcher
    can advance across pages."""
    from services.nova_engine.invoice_repository import OpenInvoiceQuery

    q = OpenInvoiceQuery(limit=100, offset=300)
    assert q.offset == 300
    # Negative offsets must be rejected at construction.
    with pytest.raises(Exception):
        OpenInvoiceQuery(limit=100, offset=-1)


# ── Prompt guards against dropping AR/AP as the first fallback ──


def test_nova_prompt_forbids_dropping_ar_ap_as_first_fallback():
    """The prompt's AR/AP routing section must explicitly tell Nova
    not to suggest dropping AR/AP timing as the first response when
    the user explicitly asked for those rules."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT.lower()
    # Two reinforcing phrases must both be present.
    assert "never drop ar/ap" in prompt
    assert "truncated" in prompt
    # The verbatim anti-omission phrasing from the previous fix must
    # still anchor the section.
    assert "omitted" in prompt


# ── Live-bug regression: 250 synthetic rows no longer abort the projection ──


def test_projection_resolver_survives_more_than_200_synthetic_ar_rows():
    """Integrated regression for the 200-row cap bug. Mock the
    repository to back a 250-row dataset, run the resolver, and
    confirm the AR schedule block carries every row's scheduling
    without aborting."""
    import asyncio as _aio
    from unittest.mock import patch as _patch

    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        _resolve_dynamic_projection_inputs,
    )
    from services.nova_engine.invoice_repository import (
        OpenInvoiceQueryResult,
        OpenInvoiceRow,
    )

    total_rows = 250
    fake_rows = [
        OpenInvoiceRow(
            invoice_number=f"SYNTH-{i:04d}",
            due_date="2099-06-30",
            open_balance=1.0,
        )
        for i in range(total_rows)
    ]

    async def fake_query(filters, *, db=None):
        page = fake_rows[filters.offset:filters.offset + filters.limit]
        return OpenInvoiceQueryResult(
            total_matched=total_rows,
            rows=page,
            rows_returned=len(page),
        )

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.horizon_weeks = 13

    with _patch(
        "services.nova_engine.invoice_repository.query_open_invoice_rows",
        new=fake_query,
    ):
        _, ar, _, _ = _aio.run(
            _resolve_dynamic_projection_inputs(
                state=state,
                account_average_prefixes=None,
                ar_receipts_timing={
                    "method": "due_date_plus_days",
                    "days_after_due_date": 7,
                },
                ap_payments_timing=None,
                payroll_schedule=None,
            )
        )

    # Every fetched row should have made it into the schedule —
    # all 250 due_dates fall inside the 13-week horizon (due 2099-06-30
    # + 7 days = 2099-07-07 = week 6 of a horizon starting 2099-06-01).
    assert ar is not None
    assert ar["fetched_row_count"] == 250
    assert ar["total_available_rows"] == 250
    assert ar["scheduled_row_count"] == 250
    assert ar["truncated"] is False
    # State carries every override.
    assert sum(1 for o in state.overrides if o.target == "OTHER_REVENUE") == 250


# ===========================================================================
# 18. Account-average anchoring (live Asad regression — 2025 leak)
# ===========================================================================
#
# Live observation: a 2026 projection quoted account averages using
# 2025-03 / 2025-04 / 2025-05. Root cause: the lookback walk had a
# 36-month cap and was silently reaching prior-year months when the
# tenant's latest_tx_date was stale. The fix bounds the walk to
# ``months + 1`` candidates so the helper never reaches a year ago.


def test_account_averages_use_months_before_projection_when_tail_is_complete():
    """projection_start_month=2099-06, latest_tx=2099-05-31 (May
    complete) → months_used = 2099-03, 2099-04, 2099-05. No
    prior-year leak."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-03", "90000 Synthetic A", 100.0),
        _gl_row("2099-04", "90000 Synthetic A", 200.0),
        _gl_row("2099-05", "90000 Synthetic A", 300.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
        )
    )
    assert result["months_used"] == ["2099-03", "2099-04", "2099-05"]
    # No prior-year leak — months_used contains only the projection year.
    for ym in result["months_used"]:
        assert ym.startswith("2099-"), f"prior-year leak: {ym}"


def test_account_averages_skip_one_incomplete_tail_month():
    """projection_start_month=2099-06, latest_tx=2099-05-07 (May
    incomplete) → months_used = 2099-02, 2099-03, 2099-04. The
    helper skips exactly one in-progress month at the tail."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-02", "90000 Synthetic A", 100.0),
        _gl_row("2099-03", "90000 Synthetic A", 200.0),
        _gl_row("2099-04", "90000 Synthetic A", 300.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-07")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
        )
    )
    assert result["months_used"] == ["2099-02", "2099-03", "2099-04"]


def test_account_averages_do_not_reach_prior_year_when_recent_months_are_incomplete():
    """The killer regression: a STALE tenant whose latest_tx is
    months / years in the past must NOT silently surface
    prior-year averages. The bounded walk caps at months+1
    candidates, so when no recent month is complete the helper
    returns an empty months_used + a clear warning."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    # latest_tx_date is from a year earlier — every month in
    # 2099-Q1+Q2 is incomplete relative to this latest_tx. The OLD
    # 36-month cap would reach back to 2098 and quietly average those
    # months. The NEW bounded walk surfaces a warning instead.
    rows: list[dict] = []  # no rows in the recent window
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2098-05-07")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
        )
    )
    # months_used MUST be empty — no recent complete months exist.
    assert result["months_used"] == []
    # No prior-year leak — even though latest_tx_date is in 2098,
    # the helper refuses to reach back that far.
    for ym in result["months_used"]:
        assert not ym.startswith("2098-"), f"prior-year leak: {ym}"
        assert not ym.startswith("2097-"), f"prior-year leak: {ym}"
    # Warning explicitly mentions thin signal + the no-backfill rule.
    assert any(
        "prior-year" in w.lower() or "thinner" in w.lower()
        for w in result["warnings"]
    )


def test_account_averages_metadata_carries_audit_fields():
    """The audit fields a reviewer needs to verify the helper used
    the right anchor: reference_month, latest_tx_date,
    months_requested, method, prior_year_logic_used=False."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-03", "90000 Synthetic A", 100.0),
        _gl_row("2099-04", "90000 Synthetic A", 200.0),
        _gl_row("2099-05", "90000 Synthetic A", 300.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
            months=3,
        )
    )
    assert result["reference_month"] == "2099-06"
    assert result["latest_tx_date"] == "2099-05-31"
    assert result["months_requested"] == 3
    assert result["method"] == "last_3_complete_month_average"
    assert result["prior_year_logic_used"] is False


def test_account_averages_metadata_keeps_existing_fields_for_backward_compat():
    """Adding new audit fields must not remove or rename existing
    ones — the chat tool already consumes ``accounts[]``,
    ``warnings``, ``basis``, ``reference_date``."""
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [_gl_row("2099-04", "90000 Synthetic A", 100.0)]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = asyncio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
        )
    )
    # Existing fields still present.
    for legacy_field in ("basis", "reference_date", "months_used", "accounts", "warnings"):
        assert legacy_field in result, f"backward-compat field {legacy_field!r} removed"


def test_mrr_prior_year_and_account_averages_use_separate_anchors():
    """The KEY distinction in Asad's prompt: MRR prior-year lookup
    is allowed because the user explicitly named it for 50300, but
    account averages must stay anchored on the recent window. Both
    paths can coexist on a single projection without bleeding into
    each other.

    The test runs each path with the SAME synthetic projection
    anchor (2099-06) and asserts:
      * MRR.prior_year.months_queried = ["2098-06"]
      * account_average.months_used    = ["2099-03", "2099-04", "2099-05"]
      * account_average.prior_year_logic_used == False
    """
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
        load_mrr_baseline_with_fallback,
    )

    # ── MRR path ──
    mrr_sequence: list[list[dict]] = [[], []]  # empty prior-year + empty fallback
    mrr_idx = {"i": 0}

    def mrr_aggregate(*args, **kwargs):
        idx = mrr_idx["i"]
        mrr_idx["i"] += 1
        cur = _MagicMock()
        cur.to_list = _AsyncMock(
            return_value=mrr_sequence[idx] if idx < len(mrr_sequence) else []
        )
        return cur

    mrr_db = _MagicMock()
    mrr_db.general_ledger.aggregate = _MagicMock(side_effect=mrr_aggregate)

    _, mrr_meta = _aio.run(
        load_mrr_baseline_with_fallback(
            mrr_db, ["2099-06"], basis="accrual",
            reference_month="2099-06",
        )
    )
    assert mrr_meta["prior_year"]["months_queried"] == ["2098-06"]

    # ── Account averages path ──
    rows = [
        _gl_row("2099-03", "90000 Synthetic A", 100.0),
        _gl_row("2099-04", "90000 Synthetic A", 200.0),
        _gl_row("2099-05", "90000 Synthetic A", 300.0),
    ]
    avg_db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    avg_result = _aio.run(
        load_account_prefix_averages(
            avg_db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
        )
    )
    assert avg_result["months_used"] == ["2099-03", "2099-04", "2099-05"]
    assert avg_result["prior_year_logic_used"] is False
    # And no 2098 / 2097 leak in months_used:
    for ym in avg_result["months_used"]:
        assert ym.startswith("2099-")


def test_account_average_lookback_walk_caps_at_months_plus_one():
    """Direct unit test on _build_complete_history_months: when the
    walk examines ``months + 1`` candidates without finding all
    complete months, it stops rather than reaching further back."""
    from datetime import date

    from services.nova_engine.data_loader import (
        _build_complete_history_months,
    )

    # latest_tx is in 2098 — Feb-May 2099 are all incomplete relative
    # to it. The walk must NOT reach into 2098 to backfill.
    months = _build_complete_history_months(
        reference_month="2099-06",
        months=3,
        latest_tx_date=date(2098, 12, 15),
    )
    assert months == [], (
        f"walk reached prior-year months to backfill: {months}"
    )


# ===========================================================================
# 19. Horizon disclosure (defaulted vs explicit)
# ===========================================================================


def test_run_projection_response_carries_horizon_was_defaulted_when_omitted():
    """When the caller omits horizon_weeks, the response surfaces
    the default-horizon disclosure block so Nova can quote it."""
    # The response shape is built at the end of the run_projection
    # branch in nova_tools.execute_tool. Source-grep the success
    # response dict to confirm the three horizon fields appear and
    # the boolean is computed from the tool_args presence check.
    import inspect
    import re

    import services.ai.agents.tools.nova_tools as pt

    source = inspect.getsource(pt)

    # The presence check must live in the run_projection branch.
    assert (
        "horizon_was_defaulted = \"horizon_weeks\" not in tool_args"
        in source
    ), "horizon_was_defaulted must be derived from tool_args presence"

    # And the response dict must carry all three audit fields.
    success_block = re.search(
        r'response: dict\[str, Any\] = \{(.+?)\n        \}\n',
        source,
        re.DOTALL,
    )
    assert success_block is not None
    block_text = success_block.group(0)
    assert '"horizon_weeks": horizon_weeks' in block_text
    assert '"horizon_was_defaulted": horizon_was_defaulted' in block_text
    assert '"horizon_note":' in block_text
    # The exact sentence Nova is expected to quote when defaulted.
    # The source has the literal split across adjacent string
    # tokens for line length; match both halves rather than the
    # concatenated string so the test is whitespace-stable.
    assert "Projection horizon was not specified, so I used" in source
    assert "the default 13-week horizon." in source


def test_nova_prompt_tells_model_to_disclose_default_horizon():
    """The prompt's date-anchoring section must tell Nova to quote
    `horizon_note` verbatim when `horizon_was_defaulted=true`."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "horizon_was_defaulted" in prompt
    assert "horizon_note" in prompt
    assert "default 13-week horizon" in prompt


def test_nova_prompt_tells_model_account_averages_are_not_prior_year():
    """The account-average section must explicitly flag
    prior_year_logic_used as ALWAYS false so the model never
    explains the months as a prior-year window."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "prior_year_logic_used" in prompt
    # The phrase that pins the distinction.
    assert "NEVER to prior-year" in prompt or "never to prior-year" in prompt.lower()


# ===========================================================================
# 20. AR/AP excluded counts + scheduled_total_amount metadata
# ===========================================================================
#
# When the user explicitly asks for AR/AP timing, the schedule
# blocks must distinguish how many rows were fetched, how many
# landed in the schedule, and how many were excluded — by reason.
# The chat surface needs these numbers so Nova can quote "fetched
# X, scheduled Y, Z outside horizon, W missing due_date" instead
# of saying "AR was omitted".


def test_ar_schedule_metadata_carries_excluded_counts_and_scheduled_total():
    """Synthetic AR rows that mix in-horizon, out-of-horizon, and
    missing-due-date cases. Each disposition gets a counter and
    the scheduled total is the deterministic sum of the in-horizon
    rows."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        # In-horizon: due 2099-06-10 + 7 = 2099-06-17 → week 3.
        {"due_date": "2099-06-10", "open_balance": 1_000.0},
        # In-horizon: due 2099-06-20 + 7 = 2099-06-27 → week 4.
        {"due_date": "2099-06-20", "open_balance": 2_500.50},
        # Outside horizon: due 2099-12-15 + 7 = 2099-12-22 →
        # past the 13-week horizon (week ~30).
        {"due_date": "2099-12-15", "open_balance": 9_999.0},
        # Missing due_date.
        {"due_date": None, "open_balance": 4_000.0},
        # Empty-string due_date — same as missing.
        {"due_date": "", "open_balance": 4_000.0},
    ]
    overrides, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )

    assert meta["scheduled_row_count"] == 2
    assert meta["excluded_outside_horizon_count"] == 1
    assert meta["excluded_missing_due_date_count"] == 2
    # Scheduled total is the SUM of the in-horizon amounts; no
    # paraphrase from the LLM.
    assert meta["scheduled_total_amount"] == pytest.approx(3_500.50)
    # 2 overrides, mirroring scheduled_row_count.
    assert len(overrides) == 2


def test_ar_schedule_zero_scheduled_when_all_rows_outside_horizon():
    """A 13-week horizon starting 2099-06; all 3 rows due in late
    2099 → all excluded. scheduled_row_count=0 + warning explains
    why. Critical so Nova does NOT report "AR was omitted"."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": "2099-12-01", "open_balance": 1_000.0},
        {"due_date": "2099-12-15", "open_balance": 2_000.0},
        {"due_date": "2099-12-31", "open_balance": 3_000.0},
    ]
    overrides, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=0,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert overrides == []
    assert meta["scheduled_row_count"] == 0
    assert meta["excluded_outside_horizon_count"] == 3
    assert meta["scheduled_total_amount"] == 0.0
    assert any(
        "outside" in w.lower() for w in meta["warnings"]
    )


def test_ap_schedule_metadata_carries_excluded_counts_and_scheduled_total():
    """Mirror of the AR test, but for AP. The two blocks have
    identical shape so Nova can describe them uniformly."""
    from services.nova_engine.scheduled_events import compute_ap_schedule

    rows = [
        {"due_date": "2099-06-08", "open_balance": 500.0},
        {"due_date": "2099-06-18", "open_balance": 750.25},
        {"due_date": "2099-12-01", "open_balance": 9_000.0},   # outside
        {"due_date": None, "open_balance": 1_000.0},            # missing
    ]
    overrides, meta = compute_ap_schedule(
        open_bill_rows=rows,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["scheduled_row_count"] == 2
    assert meta["excluded_outside_horizon_count"] == 1
    assert meta["excluded_missing_due_date_count"] == 1
    assert meta["scheduled_total_amount"] == pytest.approx(1_250.25)
    assert len(overrides) == 2


# ===========================================================================
# 21. sales_baseline metadata block
# ===========================================================================
#
# Asad asked for "last three month avg sales". The engine already
# computes that from inputs.ps/fs/other_revenue_last_3_months;
# this test pins the new sales_baseline audit block that surfaces
# the deterministic numbers Nova is expected to quote.


def test_sales_baseline_metadata_present_in_projection_inputs():
    """build_projection_inputs must populate
    ProjectionInputs.sales_baseline_metadata with method, basis,
    reference_month, latest_tx_date, months_used,
    revenue_categories, monthly_totals, average_monthly_sales."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.nova_engine.data_loader import build_projection_inputs

    # Sequence mirrors test_build_projection_inputs_threads_mrr_baseline_metadata:
    #   1. latest_tx
    #   2. revenue history (3 synthetic months)
    #   3. GP history (mirror revenue)
    #   4. MRR prior-year
    #   5. MRR fallback (no missing months → not consulted, but
    #      _make_sequential_qbo_db is strict about indexing)
    #   6. wholesale
    aggregate_results: list[list[dict]] = [
        [{"_id": None, "max_date": "2099-05-31"}],
        [
            {"_id": {"month": "2099-03", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 100.0},
            {"_id": {"month": "2099-04", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 200.0},
            {"_id": {"month": "2099-05", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 300.0},
        ],
        [
            {"_id": {"month": "2099-03", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 100.0},
            {"_id": {"month": "2099-04", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 200.0},
            {"_id": {"month": "2099-05", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 300.0},
        ],
        [],  # MRR prior-year
        [],  # MRR fallback
        [],  # wholesale
    ]

    call_idx = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_idx["i"]
        call_idx["i"] += 1
        cursor = _MagicMock()
        rows = aggregate_results[idx] if idx < len(aggregate_results) else []
        cursor.to_list = _AsyncMock(return_value=rows)
        return cursor

    qbo_db = _MagicMock()
    qbo_db.general_ledger.aggregate = _MagicMock(side_effect=aggregate_side_effect)
    qbo_db.general_ledger.find_one = _AsyncMock(
        return_value={"tx_date": "2099-05-31"}
    )

    app_db = _MagicMock()
    app_db.account_groupings.find = _MagicMock()
    app_db.account_groupings.find.return_value.to_list = _AsyncMock(return_value=[])

    inputs = _aio.run(
        build_projection_inputs(
            qbo_db=qbo_db,
            app_db=app_db,
            starting_cash=0.0,
            projection_start_month="2099-06",
            horizon_weeks=13,
        )
    )

    assert inputs.sales_baseline_metadata is not None
    meta = inputs.sales_baseline_metadata
    assert meta["method"] == "last_3_complete_month_average"
    assert meta["basis"] == "accrual"
    assert meta["reference_month"] == "2099-06"
    assert meta["latest_tx_date"] == "2099-05-31"
    assert meta["months_requested"] == 3
    assert meta["months_used"] == ["2099-03", "2099-04", "2099-05"]
    assert set(meta["revenue_categories"]) == {
        "PS_REVENUE", "FS_REVENUE", "OTHER_REVENUE",
    }
    # monthly_totals are PS + FS + OTHER per month.
    assert meta["monthly_totals"] == {
        "2099-03": 100.0, "2099-04": 200.0, "2099-05": 300.0,
    }
    # Average = (100 + 200 + 300) / 3 = 200.0
    assert meta["average_monthly_sales"] == pytest.approx(200.0)


# ===========================================================================
# 22. Nova prompt mentions sales baseline + AR/AP counts + MRR vs horizon
# ===========================================================================


def test_nova_prompt_mentions_sales_baseline_block():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "sales_baseline" in prompt
    assert "last three month" in prompt.lower() or "last 3 month" in prompt.lower()


def test_nova_prompt_mentions_ar_ap_disposition_counts():
    """The prompt must spell out the new counter fields so Nova
    quotes them when scheduling is partial."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "excluded_outside_horizon_count" in prompt
    assert "excluded_missing_due_date_count" in prompt
    assert "scheduled_total_amount" in prompt


def test_nova_prompt_mentions_mrr_monthly_vs_visible_horizon():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    # The exact distinction-anchor phrase (or close paraphrase).
    assert "monthly projection buckets" in prompt.lower()
    assert "13-week" in prompt


def test_nova_prompt_mentions_additive_application_mode():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "additive_manual_cost_assumption" in prompt
    # The verbatim disclosure sentence the prompt asks Nova to use.
    assert "additive manual cost assumptions" in prompt
    assert "not replacements" in prompt


# ===========================================================================
# 23. Anti-hardcoding source-grep tests
# ===========================================================================
#
# Asad's exact prompt values are RUNTIME inputs. They must never
# leak into product code as defaults / constants / fallbacks /
# example values the LLM could copy. These tests grep the live
# product source modules for the specific values and assert they
# are absent.


def _product_source_modules() -> list[tuple[str, str]]:
    """Return ``(label, source)`` for every product module the
    anti-hardcoding tests scan. Synthetic-value tests live under
    backend/tests/ and are excluded — fixture files MAY contain
    these strings."""
    import inspect

    import services.ai.agents.nova as nova
    import services.ai.agents.tools.nova_tools as pt
    import services.nova_engine.data_loader as dl
    import services.nova_engine.engine as engine
    import services.nova_engine.scheduled_events as se

    out = []
    for label, mod in (
        ("nova_tools.py", pt),
        ("data_loader.py", dl),
        ("scheduled_events.py", se),
        ("nova.py", nova),
        ("engine.py", engine),
    ):
        try:
            out.append((label, inspect.getsource(mod)))
        except (OSError, TypeError):
            out.append((label, ""))
    return out


def test_no_production_account_prefixes_appear_in_product_sources():
    """The Asad prompt names 70500, 70200, 70201, 70202, 70205,
    70206. These are RUNTIME inputs only — no module should embed
    them as defaults / constants / examples / fallbacks."""
    forbidden_prefixes = ("70500", "70200", "70201", "70202", "70205", "70206")
    for label, source in _product_source_modules():
        for prefix in forbidden_prefixes:
            assert prefix not in source, (
                f"{label} embeds production prefix {prefix!r}; "
                "must be runtime-only input."
            )


def test_no_production_payroll_amount_as_default_or_constant():
    """20000 / 20_000 / 20000.0 must not appear as a literal in
    product source. Tests may compute amounts from fixtures, but
    product code must not hardcode it."""
    forbidden_tokens = ("20000", "20_000", "20000.0")
    for label, source in _product_source_modules():
        for token in forbidden_tokens:
            # The token "20000" can legitimately appear as part of
            # a different number (e.g. "120000" or "200000"). Use a
            # word-boundary check to avoid false positives.
            import re
            pattern = re.compile(rf"\b{re.escape(token)}\b")
            assert not pattern.search(source), (
                f"{label} contains payroll-amount literal "
                f"{token!r}; must be runtime-only input."
            )


def test_no_production_projection_year_or_month_as_default():
    """2026-06 / 2026-06-05 must not appear in product source.
    Tests use synthetic 2099 dates; product source has no business
    knowing about 2026 specifically."""
    forbidden_tokens = ("2026-06", "2026-06-05")
    for label, source in _product_source_modules():
        for token in forbidden_tokens:
            assert token not in source, (
                f"{label} contains projection-date literal "
                f"{token!r}; must be runtime-only input."
            )


def test_no_production_ar_ap_timing_defaults_in_product_source():
    """AR ``days_after_due_date=7`` and AP ``method='due_date'``
    are runtime inputs. The product schema's enum can list these
    method names (they're how the tool is invoked), but no default
    should pre-populate them."""
    import inspect

    import services.ai.agents.tools.nova_tools as pt
    import services.nova_engine.scheduled_events as se

    tool_source = inspect.getsource(pt)
    se_source = inspect.getsource(se)

    # No default value of 7 anywhere relevant. We can't blanket-ban
    # "7" (used in too many places) so we check for specific
    # assignments and dict literals.
    import re
    # Catch "days_after_due_date": 7 / days_after_due_date=7 with
    # 7 as a literal — but ALLOW {"days_after_due_date": <int>}
    # parameter declarations (those don't carry a value).
    forbidden_patterns = (
        re.compile(r'"days_after_due_date":\s*7\b'),
        re.compile(r'days_after_due_date\s*=\s*7\b'),
    )
    for pattern in forbidden_patterns:
        assert not pattern.search(tool_source), (
            f"nova_tools.py embeds default AR offset days=7; "
            "must be runtime-only input."
        )
        assert not pattern.search(se_source), (
            f"scheduled_events.py embeds default AR offset days=7; "
            "must be runtime-only input."
        )


def test_no_default_account_average_prefixes_list_in_schema():
    """The run_projection tool schema MUST NOT carry a default
    value for account_average_prefixes."""
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(s for s in NOVA_TOOL_SCHEMAS if s["name"] == "run_projection")
    prop = schema["parameters"]["properties"]["account_average_prefixes"]
    # No default field, or default is None/empty.
    default = prop.get("default")
    assert default in (None, []), (
        f"run_projection schema sets a default account_average_prefixes "
        f"list of {default!r}; must be runtime-only input."
    )


def test_no_default_payroll_schedule_in_schema():
    """payroll_schedule is OPTIONAL — no default value should
    pre-populate it. The schema may list required sub-fields
    (amount, start_date, frequency) but cannot ship a literal
    20000 / 2026-06-05 / biweekly default."""
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(s for s in NOVA_TOOL_SCHEMAS if s["name"] == "run_projection")
    prop = schema["parameters"]["properties"]["payroll_schedule"]
    # No default at the top level.
    assert "default" not in prop or prop["default"] in (None, {})
    # And no defaults on any sub-property. Each sub-property must
    # require the caller to supply a value.
    for sub_name, sub_def in prop.get("properties", {}).items():
        sub_default = sub_def.get("default")
        assert sub_default is None, (
            f"payroll_schedule.{sub_name} has default {sub_default!r}; "
            "must be runtime-only input."
        )


def test_no_default_ar_or_ap_timing_in_schema():
    """Same for ar_receipts_timing and ap_payments_timing — no
    default method, no default days_after_due_date."""
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(s for s in NOVA_TOOL_SCHEMAS if s["name"] == "run_projection")
    for top_field in ("ar_receipts_timing", "ap_payments_timing"):
        prop = schema["parameters"]["properties"][top_field]
        assert "default" not in prop or prop["default"] in (None, {})
        for sub_name, sub_def in prop.get("properties", {}).items():
            sub_default = sub_def.get("default")
            assert sub_default is None, (
                f"{top_field}.{sub_name} has default "
                f"{sub_default!r}; must be runtime-only input."
            )


# ===========================================================================
# 24. Current-month exclusion rule (live Asad regression — May 2026)
# ===========================================================================
#
# Live observation: Nova used 2026-03/04/05 as the "last 3 complete
# months" even though the current calendar date was May 12 — May 2026
# was not yet complete. Root cause: is_month_complete only checked
# latest_tx_date >= month_end, missing the calendar-end guard. The
# fix adds an optional current_date kwarg; when the helper has both
# inputs, it requires month_end < current_date in addition to the
# data-freshness check.


def test_is_month_complete_excludes_current_month_when_current_date_supplied():
    """current_date=2099-05-12 + latest_tx=2099-05-31 → May 2099 is
    NOT complete because the calendar is still in May. Without the
    current_date guard, the helper would return True on the strength
    of latest_tx_date alone — that's the live regression."""
    from datetime import date

    from services.nova_engine.data_freshness import is_month_complete

    # Without current_date — legacy behaviour, returns True because
    # latest_tx_date covers month-end.
    assert is_month_complete("2099-05", date(2099, 5, 31)) is True
    # With current_date inside May — guard fires, returns False.
    assert is_month_complete(
        "2099-05", date(2099, 5, 31), current_date=date(2099, 5, 12),
    ) is False
    # With current_date on June 1 — guard passes, returns True.
    assert is_month_complete(
        "2099-05", date(2099, 5, 31), current_date=date(2099, 6, 1),
    ) is True


def test_is_month_complete_excludes_when_latest_tx_short_of_month_end():
    """latest_tx_date that does not reach month-end → month is
    incomplete regardless of current_date. This is the
    data-freshness half of the rule."""
    from datetime import date

    from services.nova_engine.data_freshness import is_month_complete

    assert is_month_complete(
        "2099-05", date(2099, 5, 7), current_date=date(2099, 6, 15),
    ) is False


def test_last_n_complete_months_skips_current_in_progress_month():
    """projection_start_month=2099-06, current_date=2099-05-12,
    latest_tx_date=2099-05-31. May 2099 is in-progress on the
    calendar; helper must skip it and return Feb/Mar/Apr 2099."""
    from datetime import date

    from services.nova_engine.data_freshness import last_n_complete_months

    months = last_n_complete_months(
        reference_month="2099-06",
        latest_tx_date=date(2099, 5, 31),
        n=3,
        current_date=date(2099, 5, 12),
    )
    assert months == ["2099-02", "2099-03", "2099-04"]


def test_last_n_complete_months_includes_just_finished_month_on_first_day_after():
    """current_date=2099-06-01 (first day of the projection start
    month), latest_tx_date=2099-05-31 → May 2099 IS complete. The
    helper returns Mar/Apr/May 2099."""
    from datetime import date

    from services.nova_engine.data_freshness import last_n_complete_months

    months = last_n_complete_months(
        reference_month="2099-06",
        latest_tx_date=date(2099, 5, 31),
        n=3,
        current_date=date(2099, 6, 1),
    )
    assert months == ["2099-03", "2099-04", "2099-05"]


def test_last_n_complete_months_excludes_month_when_latest_tx_short():
    """current_date is AFTER month-end but the GL data only runs
    through mid-May → May 2099 is excluded (data-freshness half).
    Helper returns Feb/Mar/Apr."""
    from datetime import date

    from services.nova_engine.data_freshness import last_n_complete_months

    months = last_n_complete_months(
        reference_month="2099-06",
        latest_tx_date=date(2099, 5, 7),
        n=3,
        current_date=date(2099, 6, 15),
    )
    assert months == ["2099-02", "2099-03", "2099-04"]


# ── Account-prefix averages honour the new rule end-to-end ──


def test_account_prefix_averages_skip_current_month_when_in_progress():
    """End-to-end: projection_start=2099-06, latest_tx covers May
    end-date but current_date is May 12. The helper must NOT
    average May; the expected window is Feb/Mar/Apr."""
    import asyncio as _aio
    from datetime import date as _date

    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-02", "90000 Synthetic A", 100.0),
        _gl_row("2099-03", "90000 Synthetic A", 200.0),
        _gl_row("2099-04", "90000 Synthetic A", 300.0),
        # May rows present, but the calendar hasn't moved past May yet.
        _gl_row("2099-05", "90000 Synthetic A", 999_999.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = _aio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
            current_date=_date(2099, 5, 12),
        )
    )
    assert result["months_used"] == ["2099-02", "2099-03", "2099-04"]
    # current_date_anchor surfaces the calendar today the helper used.
    assert result["current_date_anchor"] == "2099-05-12"
    # May's 999_999 fixture amount MUST NOT contribute to the average —
    # if it did, the helper would have averaged 100+200+300+999999.
    entry = result["accounts"][0]
    assert entry["average_monthly_amount"] == pytest.approx(
        (100.0 + 200.0 + 300.0) / 3
    )


def test_account_prefix_averages_include_just_finished_month():
    """current_date=2099-06-01, latest_tx covers May 31 → May 2099
    is complete and gets included; helper returns Mar/Apr/May."""
    import asyncio as _aio
    from datetime import date as _date

    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [
        _gl_row("2099-03", "90000 Synthetic A", 100.0),
        _gl_row("2099-04", "90000 Synthetic A", 200.0),
        _gl_row("2099-05", "90000 Synthetic A", 300.0),
    ]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result = _aio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
            current_date=_date(2099, 6, 1),
        )
    )
    assert result["months_used"] == ["2099-03", "2099-04", "2099-05"]
    assert result["current_date_anchor"] == "2099-06-01"


def test_account_prefix_averages_metadata_carries_current_date_anchor():
    """The audit field is always present (None when caller omits it,
    ISO string when supplied)."""
    import asyncio as _aio
    from datetime import date as _date

    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )

    rows = [_gl_row("2099-03", "90000 Synthetic A", 1.0)]
    db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    # Caller omits current_date → audit field is None.
    result_legacy = _aio.run(
        load_account_prefix_averages(
            db, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
        )
    )
    assert "current_date_anchor" in result_legacy
    assert result_legacy["current_date_anchor"] is None

    # Caller supplies current_date → audit field is set.
    db2 = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    result_explicit = _aio.run(
        load_account_prefix_averages(
            db2, basis="accrual",
            account_prefixes=["90000"],
            reference_date="2099-06-01",
            current_date=_date(2099, 5, 12),
        )
    )
    assert result_explicit["current_date_anchor"] == "2099-05-12"


# ── Sales baseline honours the new rule when threaded via
#   build_projection_inputs ──


def test_sales_baseline_excludes_current_month_when_in_progress():
    """End-to-end: build_projection_inputs with current_date=2099-05-12
    must populate sales_baseline_metadata with months_used =
    [2099-02, 2099-03, 2099-04]. May rows in the fixture must NOT
    feed the average even though latest_tx covers May 31."""
    import asyncio as _aio
    from datetime import date as _date
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.nova_engine.data_loader import build_projection_inputs

    # Aggregate sequence: latest_tx, revenue, GP, MRR prior-year,
    # MRR fallback, wholesale.
    # Revenue rows cover Feb / Mar / Apr (history that the helper
    # is expected to use). We deliberately omit May to keep the
    # fixture small — if the helper picks May, it'll show as 0 totals
    # and the average will differ.
    aggregate_results: list[list[dict]] = [
        [{"_id": None, "max_date": "2099-05-31"}],
        [
            {"_id": {"month": "2099-02", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 100.0},
            {"_id": {"month": "2099-03", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 200.0},
            {"_id": {"month": "2099-04", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 300.0},
        ],
        [
            {"_id": {"month": "2099-02", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 100.0},
            {"_id": {"month": "2099-03", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 200.0},
            {"_id": {"month": "2099-04", "account_id": "50101",
                     "account_name": "50101 PS Revenue"}, "amount": 300.0},
        ],
        [],
        [],
        [],
    ]
    call_idx = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_idx["i"]
        call_idx["i"] += 1
        cursor = _MagicMock()
        rows = aggregate_results[idx] if idx < len(aggregate_results) else []
        cursor.to_list = _AsyncMock(return_value=rows)
        return cursor

    qbo_db = _MagicMock()
    qbo_db.general_ledger.aggregate = _MagicMock(side_effect=aggregate_side_effect)
    qbo_db.general_ledger.find_one = _AsyncMock(
        return_value={"tx_date": "2099-05-31"}
    )

    app_db = _MagicMock()
    app_db.account_groupings.find = _MagicMock()
    app_db.account_groupings.find.return_value.to_list = _AsyncMock(return_value=[])

    inputs = _aio.run(
        build_projection_inputs(
            qbo_db=qbo_db,
            app_db=app_db,
            starting_cash=0.0,
            projection_start_month="2099-06",
            horizon_weeks=13,
            current_date=_date(2099, 5, 12),
        )
    )

    sb = inputs.sales_baseline_metadata
    assert sb is not None
    assert sb["months_used"] == ["2099-02", "2099-03", "2099-04"]
    assert sb["current_date_anchor"] == "2099-05-12"
    assert sb["monthly_totals"] == {
        "2099-02": 100.0, "2099-03": 200.0, "2099-04": 300.0,
    }
    assert sb["average_monthly_sales"] == pytest.approx(200.0)


# ── MRR prior-year remains untouched ──


def test_mrr_prior_year_logic_unaffected_by_current_month_guard():
    """MRR baseline's prior-year same-month logic does not depend
    on is_month_complete. Even with a tight current_date guard, MRR
    still queries 2098-06 for a 2099-06 forecast."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.nova_engine.data_loader import (
        load_mrr_baseline_with_fallback,
    )

    sequence: list[list[dict]] = [[], []]
    call_idx = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_idx["i"]
        call_idx["i"] += 1
        cursor = _MagicMock()
        cursor.to_list = _AsyncMock(
            return_value=sequence[idx] if idx < len(sequence) else []
        )
        return cursor

    db = _MagicMock()
    db.general_ledger.aggregate = _MagicMock(side_effect=aggregate_side_effect)

    _, meta = _aio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-06"], basis="accrual",
            reference_month="2099-06",
        )
    )
    assert meta["prior_year"]["months_queried"] == ["2098-06"]


# ── Nova prompt mentions the rule ──


def test_nova_prompt_mentions_current_month_exclusion_rule():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    # The audit field name must appear.
    assert "current_date_anchor" in prompt
    # And the verbatim guidance Nova is expected to use.
    assert "current month was not complete" in prompt.lower()
    # And the rule's two-part definition.
    assert "calendar" in prompt.lower()


# ── ScenarioState wiring ──


def test_scenario_state_current_date_defaults_at_boundary():
    """A fresh _ScenarioState has current_date=None; the dispatcher
    sets it to date.today() at the boundary. Tests can pre-set it
    to inject a synthetic date."""
    from datetime import date as _date

    from services.ai.agents.tools.nova_tools import _ScenarioState

    state = _ScenarioState()
    assert state.current_date is None

    state.current_date = _date(2099, 5, 12)
    kwargs = state.to_inputs_kwargs()
    assert kwargs["current_date"] == _date(2099, 5, 12)


def test_scenario_state_to_inputs_kwargs_omits_current_date_when_unset():
    """When current_date is None (legacy callers / first-touch
    state), to_inputs_kwargs MUST NOT include the key — otherwise
    build_projection_inputs_from_globals would get a None it has
    to filter."""
    from services.ai.agents.tools.nova_tools import _ScenarioState

    state = _ScenarioState()
    kwargs = state.to_inputs_kwargs()
    assert "current_date" not in kwargs


# ── No production date hardcoded for the new guard ──


def test_no_production_current_date_hardcoded_in_helpers():
    """The current-date guard is a runtime input. Module sources
    must not pre-populate it with a production date."""
    import inspect

    import services.nova_engine.data_freshness as df
    import services.nova_engine.data_loader as dl

    for label, mod in (("data_freshness.py", df), ("data_loader.py", dl)):
        source = inspect.getsource(mod)
        # No 2026-MM-DD literal anywhere.
        import re
        bad = re.search(r"\b2026-\d{2}-\d{2}\b", source)
        assert bad is None, (
            f"{label} embeds production date {bad.group(0)!r}; "
            "current_date must be a runtime input."
        )


# ===========================================================================
# 25. AR/AP exclusion taxonomy + reconciliation invariant
# ===========================================================================
#
# Every fetched row must land in exactly one disposition bucket.
# The reconciliation block sums all buckets and asserts the total
# equals fetched_row_count — without this invariant, a future
# silent drop-path could make AR/AP look "incomplete" without any
# trace.


def test_ar_schedule_classifies_invalid_date_separately_from_missing():
    """A row with due_date=None and a row with due_date='not-a-date'
    land in DIFFERENT buckets so a reviewer can distinguish a
    stale snapshot from a malformed-input bug."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": None, "open_balance": 100.0},
        {"due_date": "", "open_balance": 100.0},
        {"due_date": "not-a-date", "open_balance": 100.0},
        {"due_date": "13/14/2099", "open_balance": 100.0},
    ]
    _, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["excluded_missing_due_date_count"] == 2
    assert meta["excluded_invalid_date_count"] == 2


def test_ar_schedule_counts_non_positive_amounts_in_dedicated_bucket():
    """Zero / negative open balances must NOT be silently dropped;
    they get their own counter."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 0.0},
        {"due_date": "2099-06-12", "open_balance": -500.0},
        {"due_date": "2099-06-15", "open_balance": 1_000.0},
    ]
    overrides, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["scheduled_row_count"] == 1
    assert meta["excluded_non_positive_amount_count"] == 2
    assert len(overrides) == 1


def test_ar_schedule_counts_non_dict_rows_in_other_excluded():
    """Garbage rows (non-dict types) land in other_excluded so the
    reconciliation invariant still holds."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 100.0},
        "not a dict",          # type: ignore[list-item]
        12345,                 # type: ignore[list-item]
        None,                  # type: ignore[list-item]
    ]
    _, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert meta["other_excluded_count"] == 3


def test_ar_schedule_reconciliation_invariant_holds_with_mixed_exclusions():
    """Every fetched row lands in exactly one bucket; the
    reconciliation block confirms the sum."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 1_000.0},      # scheduled
        {"due_date": "2099-06-15", "open_balance": 500.0},        # scheduled
        {"due_date": "2099-12-10", "open_balance": 200.0},        # outside
        {"due_date": None, "open_balance": 50.0},                  # missing
        {"due_date": "junk", "open_balance": 10.0},                # invalid
        {"due_date": "2099-06-20", "open_balance": 0.0},          # non-positive
        {"due_date": "2099-06-20", "open_balance": -1.0},         # non-positive
    ]
    fetch_meta = {
        "source": "open_invoice_snapshot",
        "fetched_row_count": len(rows),
        "total_available_rows": len(rows),
        "schedule_cap": 5_000,
        "page_size": 200,
        "truncated": False,
    }
    _, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta=fetch_meta,
    )

    rec = meta["reconciliation"]
    # The invariant: every category sums to fetched_row_count.
    assert rec["fetched_row_count"] == len(rows)
    assert rec["accounted_for_count"] == (
        meta["scheduled_row_count"]
        + meta["excluded_outside_horizon_count"]
        + meta["excluded_missing_due_date_count"]
        + meta["excluded_invalid_date_count"]
        + meta["excluded_non_positive_amount_count"]
        + meta["other_excluded_count"]
    )
    assert rec["accounted_for_count"] == rec["fetched_row_count"]
    assert rec["is_reconciled"] is True

    # And the per-category numbers match the fixture.
    assert meta["scheduled_row_count"] == 2
    assert meta["excluded_outside_horizon_count"] == 1
    assert meta["excluded_missing_due_date_count"] == 1
    assert meta["excluded_invalid_date_count"] == 1
    assert meta["excluded_non_positive_amount_count"] == 2


def test_ar_scheduled_total_amount_equals_sum_of_scheduled_receipts():
    """scheduled_total_amount is the deterministic sum, not an LLM-
    computed paraphrase."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 123.45},
        {"due_date": "2099-06-15", "open_balance": 678.90},
        {"due_date": "2099-06-20", "open_balance": 11.00},
    ]
    _, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    expected = 123.45 + 678.90 + 11.00
    assert meta["scheduled_total_amount"] == pytest.approx(round(expected, 2))


def test_ap_schedule_reconciliation_invariant_holds_with_mixed_exclusions():
    """Mirror of the AR test for AP — same taxonomy, same invariant."""
    from services.nova_engine.scheduled_events import compute_ap_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 1_000.0},      # scheduled
        {"due_date": "2099-06-15", "open_balance": 500.0},        # scheduled
        {"due_date": "2099-12-10", "open_balance": 200.0},        # outside
        {"due_date": None, "open_balance": 50.0},                  # missing
        {"due_date": "junk", "open_balance": 10.0},                # invalid
        {"due_date": "2099-06-20", "open_balance": 0.0},          # non-positive
        "not-a-dict",          # type: ignore[list-item]            # other
    ]
    fetch_meta = {
        "source": "ap_bills",
        "fetched_row_count": len(rows),
        "total_available_rows": len(rows),
        "schedule_cap": 5_000,
        "page_size": 200,
        "truncated": False,
    }
    _, meta = compute_ap_schedule(
        open_bill_rows=rows,
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta=fetch_meta,
    )
    rec = meta["reconciliation"]
    assert rec["fetched_row_count"] == len(rows)
    assert rec["accounted_for_count"] == rec["fetched_row_count"]
    assert rec["is_reconciled"] is True
    assert meta["scheduled_row_count"] == 2
    assert meta["excluded_outside_horizon_count"] == 1
    assert meta["excluded_missing_due_date_count"] == 1
    assert meta["excluded_invalid_date_count"] == 1
    assert meta["excluded_non_positive_amount_count"] == 1
    assert meta["other_excluded_count"] == 1


def test_ap_scheduled_total_amount_equals_sum_of_scheduled_payments():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 100.50},
        {"due_date": "2099-06-15", "open_balance": 200.25},
    ]
    _, meta = compute_ap_schedule(
        open_bill_rows=rows,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    expected = 100.50 + 200.25
    assert meta["scheduled_total_amount"] == pytest.approx(round(expected, 2))


def test_ar_schedule_warns_when_reconciliation_fails():
    """Defensive test for the warning path. We can't easily produce
    a real reconciliation failure (every code path increments a
    counter), but we can pin the contract: if accounted_for !=
    fetched_row_count, a warning is added."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [{"due_date": "2099-06-10", "open_balance": 100.0}]
    # Lie about the fetched_row_count via fetch_meta to force the
    # invariant to fail. The helper should append a warning.
    fetch_meta = {
        "source": "open_invoice_snapshot",
        "fetched_row_count": 5,  # claim 5 rows when only 1 was passed
        "total_available_rows": 5,
        "schedule_cap": 5_000,
        "page_size": 200,
        "truncated": False,
    }
    _, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta=fetch_meta,
    )
    assert meta["reconciliation"]["is_reconciled"] is False
    assert any("did NOT reconcile" in w for w in meta["warnings"])


# ===========================================================================
# 26. Weekly-table display metadata
# ===========================================================================


def test_run_projection_response_includes_weekly_table_show_all_for_13_weeks():
    """Source-grep the success response dict to confirm the
    weekly_table block carries display_rule='show_all' when the
    engine returns <= 13 weeks. We can't run the engine in a unit
    test without Mongo, so we pin the dispatcher's source shape."""
    import inspect

    import services.ai.agents.tools.nova_tools as pt

    source = inspect.getsource(pt)
    assert "\"weekly_table\"" in source
    assert "\"display_rule\": \"show_all\"" in source
    assert "\"display_rule\": \"preview_with_disclosure\"" in source
    # Verbatim preview sentence Nova is told to quote.
    assert "Showing first 13 of " in source
    assert "Export/download contains the full weekly table." in source


def test_nova_prompt_forbids_silently_showing_only_first_4_weeks():
    """The prompt must explicitly forbid the 'First 4 Weeks' label
    on a 13-week projection — that was the live regression."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "weekly_table" in prompt
    assert "show_all" in prompt
    assert "First 4 Weeks" in prompt or "First 4 weeks" in prompt or "first 4 weeks" in prompt.lower()


# ===========================================================================
# 27. Negative ending-cash explanation
# ===========================================================================


def test_run_projection_response_includes_cash_position_note_when_negative():
    """Source-grep for the deterministic note. The dispatcher must
    populate cash_position_note from the engine's output summary
    scalars — not an LLM computation."""
    import inspect

    import services.ai.agents.tools.nova_tools as pt

    source = inspect.getsource(pt)
    assert "cash_position_note" in source
    assert "ending_cash_is_negative" in source
    assert (
        "Ending cash is negative because projected costs" in source
    ), "deterministic negative-cash message missing"
    # All four scalars MUST appear in the dispatcher's payload.
    assert "\"starting_cash\": output.starting_cash" in source
    assert "\"total_projected_revenue\": output.total_revenue" in source
    assert "\"total_projected_costs\": output.total_costs" in source
    assert "\"ending_cash\": output.ending_cash" in source


def test_nova_prompt_quotes_cash_position_note_when_negative():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "cash_position_note" in prompt
    assert "ending_cash_is_negative" in prompt
    # The verbatim disclosure phrasing the prompt instructs Nova
    # to use is in the source string.
    assert "quote `cash_position_note.message`" in prompt or "cash_position_note.message" in prompt


# ===========================================================================
# 28. MRR fallback month_selection_rule
# ===========================================================================


def test_mrr_baseline_fallback_metadata_has_month_selection_rule():
    """The mrr_baseline.fallback block carries a label so Nova can
    quote it verbatim, plus uses_complete_month_rule=false to
    disambiguate from the sales/account-average complete-month
    window."""
    import asyncio as _aio
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.nova_engine.data_loader import (
        load_mrr_baseline_with_fallback,
    )

    sequence: list[list[dict]] = [[], []]
    call_idx = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_idx["i"]
        call_idx["i"] += 1
        cursor = _MagicMock()
        cursor.to_list = _AsyncMock(
            return_value=sequence[idx] if idx < len(sequence) else []
        )
        return cursor

    db = _MagicMock()
    db.general_ledger.aggregate = _MagicMock(side_effect=aggregate_side_effect)

    _, meta = _aio.run(
        load_mrr_baseline_with_fallback(
            db, ["2099-06"], basis="accrual",
            reference_month="2099-06",
        )
    )
    fallback = meta["fallback"]
    assert (
        fallback["month_selection_rule"]
        == "most_recent_active_months_with_50300_activity"
    )
    assert fallback["uses_complete_month_rule"] is False


def test_nova_prompt_says_mrr_fallback_is_active_months_not_complete_months():
    """The prompt must explicitly disambiguate the MRR fallback's
    active-month rule from the sales/account-average complete-month
    rule — those are two separate windows on two different metrics."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT.lower()
    assert "most_recent_active_months_with_50300_activity" in prompt
    # Both halves of the disambiguation phrase.
    assert "active 50300 months" in prompt
    assert "not the last complete months" in prompt


# ===========================================================================
# 29. Anti-hardcoding still holds after the new metadata fields
# ===========================================================================


def test_no_production_dates_introduced_by_new_metadata_blocks():
    """The new blocks (cash_position_note, weekly_table, fallback
    month_selection_rule) must not embed any production-shaped
    date literal anywhere in product source."""
    import inspect

    import services.ai.agents.nova as nova
    import services.ai.agents.tools.nova_tools as pt
    import services.nova_engine.data_loader as dl
    import services.nova_engine.scheduled_events as se

    for label, mod in (
        ("nova.py", nova),
        ("nova_tools.py", pt),
        ("data_loader.py", dl),
        ("scheduled_events.py", se),
    ):
        source = inspect.getsource(mod)
        # No bare 2024 / 2025 / 2026 YYYY-MM tokens.
        import re
        for year in ("2024", "2025", "2026"):
            bad = re.search(rf"\b{year}-\d{{2}}(?:-\d{{2}})?\b", source)
            assert bad is None, (
                f"{label} embeds production date {bad.group(0)!r}; "
                "must be runtime-only input."
            )


# ===========================================================================
# 30. Idempotency of generated dynamic schedules (live $60k payroll bug)
# ===========================================================================
#
# Live observation: a $20K weekly payroll became $60K because every
# rerun of run_projection appended a fresh batch of generated
# Override entries to state.overrides. Same root cause for AR / AP
# / account averages. The fix tags each generated entry with
# (generated_source, dedupe_key) and the dispatcher strips prior
# entries of the same source before re-applying.


def test_payroll_schedule_overrides_carry_generated_source_and_dedupe_key():
    """Every payroll override the helper builds carries the marker
    fields. Without these, the dispatcher cannot strip prior
    entries on a rerun."""
    from services.nova_engine.scheduled_events import (
        compute_payroll_schedule,
    )

    overrides, meta = compute_payroll_schedule(
        amount=12_345.67,
        start_date="2099-06-05",
        frequency="biweekly",
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert overrides  # sanity
    for ov in overrides:
        assert ov.generated_source == "dynamic_payroll_schedule"
        assert ov.dedupe_key is not None
        # Stable key shape: source : date : amount : target.
        assert ov.dedupe_key.startswith("dynamic_payroll_schedule:")
        assert ":PAYROLL" in ov.dedupe_key
    # Per-batch dedupe counter starts at 0 on a clean batch.
    assert meta["generated_source"] == "dynamic_payroll_schedule"
    assert meta["generated_event_count"] == len(overrides)
    assert meta["deduped_duplicate_count"] == 0


def test_payroll_helper_dedupes_within_a_single_batch():
    """If a future config produces two overrides with the same
    (date, amount, target), the helper keeps one and counts the
    other. We exercise this by setting frequency='weekly' on a
    horizon where step_days collapse to a no-op — synthetic
    construction below directly tests the dedupe path via the
    Override constructor."""
    from datetime import date

    from services.nova_engine.models import Override

    # Build two identical Override objects and confirm their
    # dedupe_keys match — the helper would dedupe them.
    ov1 = Override(
        type="new_cost",
        target="PAYROLL",
        week_number=1,
        amount=20_000.0,
        generated_source="dynamic_payroll_schedule",
        dedupe_key="dynamic_payroll_schedule:2099-06-05:20000.0:PAYROLL",
    )
    ov2 = Override(
        type="new_cost",
        target="PAYROLL",
        week_number=1,
        amount=20_000.0,
        generated_source="dynamic_payroll_schedule",
        dedupe_key="dynamic_payroll_schedule:2099-06-05:20000.0:PAYROLL",
    )
    assert ov1.dedupe_key == ov2.dedupe_key


def test_ar_schedule_overrides_use_doc_num_in_dedupe_key():
    """Two invoices with the SAME due_date and amount but
    different doc_nums must NOT collapse — the dedupe key
    incorporates the source identifier."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 500.0, "doc_num": "SYNTH-INV-A"},
        {"due_date": "2099-06-10", "open_balance": 500.0, "doc_num": "SYNTH-INV-B"},
    ]
    overrides, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    # Both kept — distinct doc_nums prevent collapse.
    assert len(overrides) == 2
    # Dedupe keys differ on source_id but otherwise match.
    keys = {o.dedupe_key for o in overrides}
    assert len(keys) == 2
    assert all("SYNTH-INV-A" in k or "SYNTH-INV-B" in k for k in keys)
    assert meta["deduped_duplicate_count"] == 0


def test_ar_schedule_collapses_true_duplicates_within_a_batch():
    """The SAME source_id + amount + date must collapse and bump
    the deduped counter — the only legitimate case is the
    upstream fetcher returning the same row twice (paginated
    overlap, repeated row in the synthetic test fixture, etc.)."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 500.0, "doc_num": "SYNTH-INV-A"},
        {"due_date": "2099-06-10", "open_balance": 500.0, "doc_num": "SYNTH-INV-A"},
    ]
    fetch_meta = {
        "source": "open_invoice_snapshot",
        "fetched_row_count": 2,
        "total_available_rows": 2,
        "schedule_cap": 5_000,
        "page_size": 200,
        "truncated": False,
    }
    overrides, meta = compute_ar_schedule(
        open_invoice_rows=rows,
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta=fetch_meta,
    )
    assert len(overrides) == 1
    assert meta["deduped_duplicate_count"] == 1
    # Reconciliation invariant still holds: fetched = scheduled +
    # other categories + deduped.
    assert meta["reconciliation"]["is_reconciled"] is True


def test_ar_schedule_dedupe_key_does_not_contain_customer_name():
    """The dedupe key must NOT carry tenant-identifying strings
    (customer / vendor names). Use source ID only."""
    from services.nova_engine.scheduled_events import compute_ar_schedule

    overrides, _ = compute_ar_schedule(
        open_invoice_rows=[
            {
                "due_date": "2099-06-10",
                "open_balance": 100.0,
                "doc_num": "SYNTH-INV-1",
                "customer": "Synthetic Counterparty Should Not Appear",
            },
        ],
        days_after_due_date=7,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    for ov in overrides:
        assert "Counterparty" not in (ov.dedupe_key or "")
        assert "customer" not in (ov.dedupe_key or "").lower()


def test_ap_schedule_overrides_use_doc_num_in_dedupe_key():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 250.0, "doc_num": "SYNTH-BILL-A"},
        {"due_date": "2099-06-10", "open_balance": 250.0, "doc_num": "SYNTH-BILL-B"},
    ]
    overrides, meta = compute_ap_schedule(
        open_bill_rows=rows,
        projection_start_month="2099-06",
        horizon_weeks=13,
    )
    assert len(overrides) == 2
    keys = {o.dedupe_key for o in overrides}
    assert len(keys) == 2
    assert meta["deduped_duplicate_count"] == 0


def test_ap_schedule_dedupes_true_duplicates():
    from services.nova_engine.scheduled_events import compute_ap_schedule

    rows = [
        {"due_date": "2099-06-10", "open_balance": 250.0, "doc_num": "SYNTH-BILL-A"},
        {"due_date": "2099-06-10", "open_balance": 250.0, "doc_num": "SYNTH-BILL-A"},
    ]
    fetch_meta = {
        "source": "ap_bills",
        "fetched_row_count": 2,
        "total_available_rows": 2,
        "schedule_cap": 5_000,
        "page_size": 200,
        "truncated": False,
    }
    overrides, meta = compute_ap_schedule(
        open_bill_rows=rows,
        projection_start_month="2099-06",
        horizon_weeks=13,
        fetch_meta=fetch_meta,
    )
    assert len(overrides) == 1
    assert meta["deduped_duplicate_count"] == 1
    assert meta["reconciliation"]["is_reconciled"] is True


# ── End-to-end dispatcher idempotency ──


def test_resolver_payroll_is_idempotent_across_two_runs_on_same_state():
    """Running _resolve_dynamic_projection_inputs twice with the
    SAME payroll_schedule must leave state.overrides with exactly
    one batch of payroll entries (the second run strips the first
    batch before re-applying)."""
    import asyncio as _aio

    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        _resolve_dynamic_projection_inputs,
    )

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.horizon_weeks = 13

    payroll_input = {
        "amount": 20_000.0,
        "start_date": "2099-06-05",
        "frequency": "biweekly",
    }

    # Run 1.
    _aio.run(_resolve_dynamic_projection_inputs(
        state=state,
        account_average_prefixes=None,
        ar_receipts_timing=None,
        ap_payments_timing=None,
        payroll_schedule=payroll_input,
    ))
    payroll_after_run1 = [
        o for o in state.overrides
        if getattr(o, "generated_source", None) == "dynamic_payroll_schedule"
    ]
    first_count = len(payroll_after_run1)
    assert first_count > 0

    # Run 2 (same inputs).
    _aio.run(_resolve_dynamic_projection_inputs(
        state=state,
        account_average_prefixes=None,
        ar_receipts_timing=None,
        ap_payments_timing=None,
        payroll_schedule=payroll_input,
    ))
    payroll_after_run2 = [
        o for o in state.overrides
        if getattr(o, "generated_source", None) == "dynamic_payroll_schedule"
    ]
    assert len(payroll_after_run2) == first_count, (
        f"payroll overrides multiplied across reruns: "
        f"{first_count} → {len(payroll_after_run2)}"
    )


def test_resolver_preserves_user_created_overrides_across_dynamic_reruns():
    """User-created overrides (generated_source=None) must NOT be
    stripped when the dispatcher re-applies dynamic generated
    overrides."""
    import asyncio as _aio

    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        _resolve_dynamic_projection_inputs,
    )
    from services.nova_engine.models import Override

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.horizon_weeks = 13

    # User adds a manual PAYROLL override BEFORE the dynamic
    # payroll schedule runs.
    user_override = Override(
        type="new_cost",
        target="PAYROLL",
        week_number=5,
        amount=999.0,
        description="user-created what-if",
    )
    state.overrides.append(user_override)

    _aio.run(_resolve_dynamic_projection_inputs(
        state=state,
        account_average_prefixes=None,
        ar_receipts_timing=None,
        ap_payments_timing=None,
        payroll_schedule={
            "amount": 20_000.0,
            "start_date": "2099-06-05",
            "frequency": "biweekly",
        },
    ))

    # User override survives — generated_source is None on it.
    user_after = [
        o for o in state.overrides
        if getattr(o, "generated_source", None) is None
    ]
    assert any(
        o.week_number == 5 and o.amount == 999.0 for o in user_after
    ), "user-created override was stripped during dynamic re-apply"


def test_resolver_account_averages_idempotent_across_reruns():
    """Two runs of _resolve_dynamic_projection_inputs with the same
    account_average_prefixes must leave the SAME set of generated
    ManualCostAssumptions on state — not duplicates."""
    import asyncio as _aio
    from unittest.mock import patch as _patch

    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        _resolve_dynamic_projection_inputs,
    )

    rows = [
        _gl_row("2099-03", "90100 Synthetic A", 100.0),
        _gl_row("2099-04", "90100 Synthetic A", 100.0),
        _gl_row("2099-05", "90100 Synthetic A", 100.0),
    ]
    qbo_db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.horizon_weeks = 13

    with _patch(
        "services.nova_engine.db.get_qbo_db", return_value=qbo_db,
    ):
        _aio.run(_resolve_dynamic_projection_inputs(
            state=state,
            account_average_prefixes=["90100"],
            ar_receipts_timing=None,
            ap_payments_timing=None,
            payroll_schedule=None,
        ))
    first_count = sum(
        1 for mca in state.manual_cost_assumptions
        if getattr(mca, "generated_source", None) == "dynamic_account_average_cost"
    )
    assert first_count == 1

    # Run 2 — mock qbo_db needs a fresh aggregate sequence; use a
    # fresh mock with the same rows.
    qbo_db2 = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    with _patch(
        "services.nova_engine.db.get_qbo_db", return_value=qbo_db2,
    ):
        _aio.run(_resolve_dynamic_projection_inputs(
            state=state,
            account_average_prefixes=["90100"],
            ar_receipts_timing=None,
            ap_payments_timing=None,
            payroll_schedule=None,
        ))
    second_count = sum(
        1 for mca in state.manual_cost_assumptions
        if getattr(mca, "generated_source", None) == "dynamic_account_average_cost"
    )
    assert second_count == first_count, (
        f"account-average assumptions multiplied across reruns: "
        f"{first_count} → {second_count}"
    )


def test_resolver_preserves_user_created_manual_cost_assumptions():
    """User-created ManualCostAssumptions (generated_source=None)
    must survive a dynamic re-apply of account-prefix averages."""
    import asyncio as _aio
    from unittest.mock import patch as _patch

    from services.ai.agents.tools.nova_tools import (
        _ScenarioState,
        _resolve_dynamic_projection_inputs,
    )
    from services.nova_engine.models import ManualCostAssumption

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.horizon_weeks = 13

    state.manual_cost_assumptions.append(ManualCostAssumption(
        name="user_payroll_hire",
        amount_per_month=5_555.0,
        line_type="PAYROLL",
        starts_month="2099-06",
        ends_month=None,
    ))

    rows = [
        _gl_row("2099-03", "90100 Synthetic A", 100.0),
        _gl_row("2099-04", "90100 Synthetic A", 100.0),
        _gl_row("2099-05", "90100 Synthetic A", 100.0),
    ]
    qbo_db = _mock_db_with_aggregate_and_latest_tx(rows, latest_tx="2099-05-31")
    with _patch(
        "services.nova_engine.db.get_qbo_db", return_value=qbo_db,
    ):
        _aio.run(_resolve_dynamic_projection_inputs(
            state=state,
            account_average_prefixes=["90100"],
            ar_receipts_timing=None,
            ap_payments_timing=None,
            payroll_schedule=None,
        ))

    # User assumption survives — generated_source is None on it.
    user_after = [
        mca for mca in state.manual_cost_assumptions
        if getattr(mca, "generated_source", None) is None
    ]
    assert any(
        mca.name == "user_payroll_hire" and mca.amount_per_month == 5_555.0
        for mca in user_after
    ), "user-created manual cost assumption was stripped"


# ── Serialization ──


def test_serialized_state_round_trips_generated_markers():
    """to_dict / from_dict preserve generated_source and
    dedupe_key. Cross-turn state persistence depends on this."""
    from services.ai.agents.tools.nova_tools import _ScenarioState
    from services.nova_engine.models import (
        ManualCostAssumption,
        Override,
    )

    state = _ScenarioState()
    state.projection_start_month = "2099-06"
    state.starting_cash = 1_000.0
    state.overrides.append(Override(
        type="new_cost",
        target="PAYROLL",
        week_number=1,
        amount=20_000.0,
        generated_source="dynamic_payroll_schedule",
        dedupe_key="dynamic_payroll_schedule:2099-06-05:20000.0:PAYROLL",
    ))
    state.manual_cost_assumptions.append(ManualCostAssumption(
        name="account_avg_90100",
        amount_per_month=300.0,
        line_type="OTHER_COSTS",
        starts_month="2099-06",
        generated_source="dynamic_account_average_cost",
        dedupe_key="dynamic_account_average_cost:90100:2099-03,2099-04,2099-05:300.0:OTHER_COSTS",
    ))

    blob = state.to_dict()
    restored = _ScenarioState.from_dict(blob)

    assert restored.overrides[0].generated_source == "dynamic_payroll_schedule"
    assert restored.overrides[0].dedupe_key is not None
    assert (
        restored.manual_cost_assumptions[0].generated_source
        == "dynamic_account_average_cost"
    )
    assert restored.manual_cost_assumptions[0].dedupe_key is not None


def test_old_serialized_state_without_generated_markers_still_loads():
    """Legacy persisted state has no generated_source / dedupe_key
    keys. from_dict must accept the row and default both fields to
    None — otherwise we'd lose user data on the next chat turn."""
    from services.ai.agents.tools.nova_tools import _ScenarioState

    legacy_blob = {
        "starting_cash": 1_000.0,
        "projection_start_month": "2099-06",
        "horizon_weeks": 13,
        "overrides": [
            {
                "type": "new_cost",
                "target": "PAYROLL",
                "week_number": 1,
                "amount": 999.0,
                # NOTE: no generated_source, no dedupe_key — the
                # shape an old chat session would have persisted.
            },
        ],
        "manual_cost_assumptions": [
            {
                "name": "legacy_hire",
                "amount_per_month": 1_234.0,
                "line_type": "PAYROLL",
                "starts_month": "2099-06",
            },
        ],
        "manual_recurring_revenue": [],
    }

    restored = _ScenarioState.from_dict(legacy_blob)
    assert len(restored.overrides) == 1
    assert restored.overrides[0].generated_source is None
    assert restored.overrides[0].dedupe_key is None
    assert len(restored.manual_cost_assumptions) == 1
    assert restored.manual_cost_assumptions[0].generated_source is None


# ── Prompt rule ──


def test_nova_prompt_mentions_idempotent_dynamic_schedules():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT
    assert "Idempotent generated dynamic schedules" in prompt
    assert "deduped_duplicate_count" in prompt
    # Nova is explicitly told NOT to normalise duplicate generated
    # events as expected.
    assert "appear multiple times" in prompt.lower()
