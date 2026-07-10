"""Locks the Nova average payables time answer end to end.

Two layers under test.

  Layer 1 is the pure helper compute_payables_timing. The tests cover
  every priority branch plus the edge cases the spec calls out.

  Layer 2 is the Nova dispatcher branch for the get_payables_timing
  tool. The tests mock the Mongo collections the handler reads from
  so the suite runs without a live database. The expected behaviour
  is that the tool always returns a number and a method label, and
  never produces the old refusal text the spec wants gone.

Synthetic fixtures only. No real customer or vendor data.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Helper level.
# ---------------------------------------------------------------------------


def _open_row(balance, txn_date, due_date, vendor="vendor_synth"):
    return {
        "balance": balance,
        "txn_date": txn_date,
        "due_date": due_date,
        "vendor": vendor,
    }


def _paid_row(amount, bill_date, payment_date, due_date, vendor="vendor_synth"):
    return {
        "amount": amount,
        "bill_date": bill_date,
        "payment_date": payment_date,
        "due_date": due_date,
        "vendor": vendor,
    }


class TestHelperOpenApFallback:
    """Open AP timing branch covers the fallback case the user runs
    into today. Paid bill history is absent and the DPO proxy inputs
    are absent, so the helper always lands here when only open bills
    exist."""

    def test_weighted_open_ap_age_uses_balance_weighting(self):
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[
                _open_row(1000.0, "2099-05-01", "2099-05-31"),
                _open_row(500.0, "2099-06-01", "2099-07-01"),
            ],
        )
        assert result["metric"] == "open_ap_timing"
        # Weighted age. 1000 times 45 plus 500 times 14 over 1500.
        assert result["weighted_average_open_ap_age_days"] == pytest.approx(34.67)
        assert result["open_ap_total"] == pytest.approx(1500.0)
        assert result["open_bill_count"] == 2
        assert result["vendors_count"] == 1
        assert result["row_count"] == 2

    def test_weighted_days_past_due_clamps_negative_to_zero(self):
        """A bill that is twenty days early adds zero to the weighted
        past due so an upcoming bill never pulls the average down."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[
                _open_row(1000.0, "2099-05-01", "2099-05-31"),
                _open_row(500.0, "2099-06-01", "2099-07-01"),
            ],
        )
        # Weighted past due. 1000 times 15 plus 500 times 0 over 1500.
        assert result["weighted_average_days_past_due"] == pytest.approx(10.0)
        assert result["overdue_bill_count"] == 1
        # Bill 2 is 16 days out. The default due_soon window is 14 days
        # so this bill is not counted as due soon. The default window
        # is centralised in the helper so the assertion locks the
        # current contract.
        assert result["due_soon_count"] == 0

    def test_missing_due_date_rows_excluded_from_past_due_only(self):
        """A row without a due date keeps its balance in the open AP
        total and the open bill count, but cannot contribute to the
        weighted past due. The missing count is surfaced explicitly."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[
                _open_row(1000.0, "2099-05-01", "2099-05-31"),
                _open_row(500.0, "2099-06-01", None),
            ],
        )
        assert result["missing_due_date_count"] == 1
        # Only the row with a due date influences the weighted past due.
        # 1000 times 15 over 1000.
        assert result["weighted_average_days_past_due"] == pytest.approx(15.0)
        # Both rows still count toward open AP total and bill count.
        assert result["open_ap_total"] == pytest.approx(1500.0)
        assert result["open_bill_count"] == 2

    def test_missing_bill_date_rows_excluded_from_age_only(self):
        """A row without a bill date drops out of the weighted age but
        stays in the open AP total and contributes to the past due
        weight when a due date is present."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[
                _open_row(1000.0, "2099-05-01", "2099-05-31"),
                _open_row(500.0, None, "2099-05-31"),
            ],
        )
        assert result["missing_bill_date_count"] == 1
        # Only the row with a bill date contributes to the age.
        # 1000 times 45 over 1000.
        assert result["weighted_average_open_ap_age_days"] == pytest.approx(45.0)
        # Both rows contribute to the past due weight.
        assert result["weighted_average_days_past_due"] == pytest.approx(15.0)

    def test_zero_balance_rows_excluded(self):
        """Closed paid bills with a zero balance must not inflate the
        open AP timing. The exclusion is counted in the warnings."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[
                _open_row(1000.0, "2099-05-01", "2099-05-31"),
                _open_row(0.0, "2099-04-01", "2099-04-30"),
            ],
        )
        assert result["open_bill_count"] == 1
        assert result["open_ap_total"] == pytest.approx(1000.0)
        assert any("zero or negative" in w for w in result["warnings"])

    def test_negative_balance_rows_excluded(self):
        """Credit memos with negative open balance are excluded so the
        weighted timing reflects only true payables."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[
                _open_row(1000.0, "2099-05-01", "2099-05-31"),
                _open_row(-200.0, "2099-04-01", "2099-04-30"),
            ],
        )
        assert result["open_bill_count"] == 1
        assert result["open_ap_total"] == pytest.approx(1000.0)

    def test_no_open_rows_warns_but_does_not_refuse(self):
        """The empty data case still produces a structured answer with
        the open AP timing label. Zero numerator and zero denominator
        do not raise."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[],
        )
        assert result["metric"] == "open_ap_timing"
        assert result["weighted_average_open_ap_age_days"] == 0.0
        assert result["weighted_average_days_past_due"] == 0.0
        assert result["open_ap_total"] == 0.0
        assert any("No open AP rows" in w for w in result["warnings"])

    def test_response_envelope_includes_required_keys(self):
        """Locks the field set the Nova dispatcher branch relies on.
        Adding a key is fine. Removing one breaks the response shape."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[],
        )
        for key in (
            "metric",
            "as_of_date",
            "basis",
            "period_start",
            "period_end",
            "average_days_to_pay",
            "average_days_late",
            "dpo",
            "weighted_average_open_ap_age_days",
            "weighted_average_days_past_due",
            "open_ap_total",
            "open_bill_count",
            "overdue_bill_count",
            "due_soon_count",
            "missing_bill_date_count",
            "missing_due_date_count",
            "row_count",
            "vendors_count",
            "method",
            "data_sources",
            "warnings",
        ):
            assert key in result, f"envelope missing key {key!r}"


class TestHelperDpoProxy:
    """DPO proxy branch covers the working capital dashboard case
    where balance sheet snapshots and period purchases are available
    but real paid bill payment dates are not."""

    def test_dpo_proxy_runs_when_inputs_present(self):
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[_open_row(100.0, "2099-06-01", "2099-07-01")],
            beginning_ap=10000.0,
            ending_ap=12000.0,
            period_purchases=30000.0,
            period_days=30,
            purchases_source="general_ledger_period_expense",
        )
        assert result["metric"] == "dpo_proxy"
        # Average AP is 11000. Daily purchases is 30000 over 30 days
        # which is 1000. DPO equals 11000 divided by 1000 which is 11.
        assert result["dpo"] == pytest.approx(11.0)
        assert result["ending_ap" if False else "open_ap_total"] == pytest.approx(12000.0)
        assert "proxy" in result["method"].lower()
        assert "general_ledger_period_expense" in result["data_sources"]

    def test_dpo_proxy_falls_back_when_purchases_missing(self):
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[_open_row(100.0, "2099-06-01", "2099-07-01")],
            beginning_ap=10000.0,
            ending_ap=12000.0,
            period_purchases=None,
        )
        assert result["metric"] == "open_ap_timing"

    def test_dpo_proxy_skips_on_zero_daily_purchases(self):
        """Daily purchases at zero must not divide by zero. The branch
        skips and the fallback runs instead."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[_open_row(100.0, "2099-06-01", "2099-07-01")],
            beginning_ap=10000.0,
            ending_ap=12000.0,
            period_purchases=0.0,
            period_days=30,
        )
        assert result["metric"] == "open_ap_timing"
        assert any("positive daily purchases" in w for w in result["warnings"])


class TestHelperPaidBillCyclePriority:
    """The paid bill cycle branch wins when at least one paid row has
    the required dates. The other branches are skipped silently in
    that case so the highest priority metric reaches the caller."""

    def test_paid_bill_cycle_preferred_when_payment_dates_available(self):
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[_open_row(100.0, "2099-06-01", "2099-07-01")],
            paid_bill_rows=[
                _paid_row(1000.0, "2099-05-01", "2099-05-31", "2099-05-15"),
                _paid_row(500.0, "2099-04-01", "2099-04-21", "2099-04-30"),
            ],
            beginning_ap=10000.0,
            ending_ap=12000.0,
            period_purchases=30000.0,
            period_days=30,
        )
        assert result["metric"] == "paid_bill_cycle"
        # Days to pay. Row 1 is 30 and row 2 is 20. Mean is 25.
        assert result["average_days_to_pay"] == pytest.approx(25.0)
        # Days late. Row 1 is 16 and row 2 is minus 9. Mean is 3.5.
        assert result["average_days_late"] == pytest.approx(3.5)

    def test_paid_bill_cycle_skipped_when_payment_dates_missing(self):
        """Paid rows without payment dates fail the branch. The next
        priority metric runs instead. The skip count is surfaced as a
        warning so the caller can explain the data gap."""
        from services.nova_engine.payables_timing import compute_payables_timing

        result = compute_payables_timing(
            as_of_date="2099-06-15",
            open_ap_rows=[_open_row(100.0, "2099-06-01", "2099-07-01")],
            paid_bill_rows=[
                {"amount": 1000.0, "bill_date": "2099-05-01", "due_date": "2099-05-31"},
            ],
        )
        assert result["metric"] == "open_ap_timing"


# ---------------------------------------------------------------------------
# Dispatcher level.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, _n: int):
        return self

    async def to_list(self, length: int | None = None):
        return list(self._docs[:length] if length is not None else self._docs)


class _FakeApBills:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def find(self, _query: dict, _projection: dict | None = None):
        return _FakeCursor(self._docs)


class _FakeApAging:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def find(self, _query: dict, _projection: dict | None = None):
        return _FakeCursor(self._docs)


class _FakeGl:
    def __init__(self, expense_sum: float | None) -> None:
        self._expense_sum = expense_sum

    def aggregate(self, _pipeline: list[dict]):
        if self._expense_sum is None:
            return _FakeCursor([])
        return _FakeCursor([{"_id": None, "expense_sum": self._expense_sum}])


class _FakeQboDb:
    def __init__(
        self,
        bills: list[dict],
        aging: list[dict] | None = None,
        expense_sum: float | None = None,
    ) -> None:
        self.ap_bills = _FakeApBills(bills)
        self.reports_ap_aging = _FakeApAging(aging or [])
        self.general_ledger = _FakeGl(expense_sum)


def _wire_dispatcher(monkeypatch, db) -> None:
    import services.nova_engine.db as pdb

    monkeypatch.setattr(pdb, "get_qbo_db", lambda: db)


def _bill(balance, txn_date, due_date, vendor="vendor_synth"):
    """Mongo shape mirrors what get_open_bills consumes."""
    return {
        "balance": balance,
        "txn_date": txn_date,
        "due_date": due_date,
        "raw_data": {"VendorRef": {"name": vendor, "value": "v1"}},
    }


@pytest.mark.asyncio
async def test_dispatcher_returns_answer_and_metric(monkeypatch):
    """End to end. The user phrase the spec calls out routes here and
    the dispatcher always returns an answer string and a metric label
    rather than the refusal text."""
    from services.ai.agents.tools.nova_tools import (
        execute_tool,
        _ScenarioState,
    )

    db = _FakeQboDb(
        bills=[
            _bill(1000.0, "2099-05-01", "2099-05-31"),
            _bill(500.0, "2099-06-01", "2099-07-01"),
        ],
        aging=[],
        expense_sum=None,
    )
    _wire_dispatcher(monkeypatch, db)

    result = await execute_tool(
        "get_payables_timing",
        {"as_of_date": "2099-06-15"},
        _ScenarioState(),
        "u_synth",
        "sess_synth",
        None,
    )
    assert result["metric"] == "open_ap_timing"
    assert result["confidence"] in ("low", "medium", "high")
    # Answer string identifies the open AP source.
    assert "open Accounts Payable" in result["answer"]
    # Limitation language matches the spec template and explicitly
    # mentions the open AP timing view label.
    assert any("paid bill" in lim.lower() for lim in result["limitations"])
    assert any(
        "open AP timing view" in lim for lim in result["limitations"]
    )
    # Details payload carries the full envelope.
    assert "open_ap_total" in result["details"]
    assert result["details"]["metric"] == "open_ap_timing"


@pytest.mark.asyncio
async def test_dispatcher_does_not_emit_refusal_text(monkeypatch):
    """The old failure mode is the refusal phrase the user reported.
    The new dispatcher never emits any of those strings even with an
    empty data set."""
    from services.ai.agents.tools.nova_tools import (
        execute_tool,
        _ScenarioState,
    )

    db = _FakeQboDb(bills=[], aging=[], expense_sum=None)
    _wire_dispatcher(monkeypatch, db)

    result = await execute_tool(
        "get_payables_timing",
        {},
        _ScenarioState(),
        "u_synth",
        "sess_synth",
        None,
    )
    answer = result["answer"].lower()
    forbidden_phrases = (
        "cannot calculate",
        "lack payment dates",
        "lack total credit purchases",
        "i do not have the tools",
        "i lack the tools",
    )
    for phrase in forbidden_phrases:
        assert phrase not in answer, f"refusal phrase leaked: {phrase!r}"
    assert result["metric"] == "open_ap_timing"


@pytest.mark.asyncio
async def test_dispatcher_invalid_as_of_date_returns_structured_error(monkeypatch):
    """The route still validates input. An unparseable as_of_date
    gives a structured error so the LLM does not retry blindly."""
    from services.ai.agents.tools.nova_tools import (
        execute_tool,
        _ScenarioState,
    )

    db = _FakeQboDb(bills=[], aging=[], expense_sum=None)
    _wire_dispatcher(monkeypatch, db)

    result = await execute_tool(
        "get_payables_timing",
        {"as_of_date": "not-a-date"},
        _ScenarioState(),
        "u_synth",
        "sess_synth",
        None,
    )
    assert result.get("error") == "invalid_date_format"


@pytest.mark.asyncio
async def test_dispatcher_emits_dpo_proxy_when_inputs_present(monkeypatch):
    """When the AP aging history and the GL expense window are both
    available, the dispatcher elevates to the DPO proxy branch and
    surfaces the proxy disclaimer."""
    from services.ai.agents.tools.nova_tools import (
        execute_tool,
        _ScenarioState,
    )

    db = _FakeQboDb(
        bills=[_bill(1000.0, "2099-06-01", "2099-07-01")],
        aging=[
            {"grand_total": 12000.0},
            {"grand_total": 10000.0},
        ],
        expense_sum=30000.0,
    )
    _wire_dispatcher(monkeypatch, db)

    result = await execute_tool(
        "get_payables_timing",
        {"as_of_date": "2099-06-15", "period_days": 30},
        _ScenarioState(),
        "u_synth",
        "sess_synth",
        None,
    )
    assert result["metric"] == "dpo_proxy"
    assert "proxy" in result["answer"].lower()
    assert any("proxy" in lim.lower() for lim in result["limitations"])


# ---------------------------------------------------------------------------
# Phrase routing surface area.
# ---------------------------------------------------------------------------


def test_nova_prompt_lists_get_payables_timing():
    """The Nova system prompt must reference the tool explicitly and
    the tool selection rules of thumb must mention the phrasing list
    that routes here."""
    from services.ai.agents.nova import _SYSTEM_PROMPT as NOVA_SYSTEM_PROMPT

    text = NOVA_SYSTEM_PROMPT
    assert "get_payables_timing" in text
    for phrase in (
        "average payables time",
        "DPO",
        "days payable outstanding",
        "how long are we taking to pay vendors",
    ):
        assert phrase.lower() in text.lower(), f"missing phrase {phrase!r}"


def test_nova_prompt_forbids_refusal_for_payables_timing():
    """The prompt must spell out that Nova does not respond with
    cannot calculate for this question family."""
    from services.ai.agents.nova import _SYSTEM_PROMPT as NOVA_SYSTEM_PROMPT

    assert "cannot calculate" in NOVA_SYSTEM_PROMPT.lower()


def test_tool_schemas_register_get_payables_timing():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    names = [t["name"] for t in NOVA_TOOL_SCHEMAS]
    assert "get_payables_timing" in names
