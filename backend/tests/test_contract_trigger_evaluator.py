"""Wave 4 (contracts-time-mgmt) — trigger evaluator behavior locks.

Pins the trigger-type math, idempotency, and side-effect rules in
``services/contract_trigger_evaluator.py``:

  - date triggers fire when now >= threshold.
  - pct_consumed fires when consumed / total >= threshold.
  - remaining_threshold fires when remaining <= threshold.
  - block_exhausted fires when bill_and_actuals_active is true OR
    hours_consumed >= total.
  - biweekly_cycle returns an eligibility marker but does NOT create
    invoice_batches.
  - One-shot triggers flip to state="fired" after firing; biweekly
    stays armed.
  - Disabled triggers are not evaluated.
  - Action handlers degrade with "_not_configured" / eligibility
    markers when their dependencies are absent.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


def _contract(**overrides):
    base = {
        "contract_id": "c_001",
        "contract_type": "boh_with_boa",
        "total_hours": 20.0,
        "hours_consumed": 5.0,
        "overage_hours": 0.0,
        "bill_and_actuals_active": False,
    }
    base.update(overrides)
    return base


class _FakeTriggers:
    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]
        self.updates: list = []
        self.invoice_batches_writes: list = []  # always stays empty

    def find(self, query, _projection=None):
        results = [r for r in self._rows if all(r.get(k) == v for k, v in query.items())]

        class _C:
            def __init__(s, items):
                s._items = items

            async def to_list(s, _n):
                return list(s._items)
        return _C(results)

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        for r in self._rows:
            if all(r.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    r.update(update["$set"])
        return SimpleNamespace(modified_count=1)


class _FakeInvoiceBatches:
    """Strictly prove invoice_batches is NOT touched by the evaluator.
    Every write method raises so a single accidental call surfaces."""

    async def insert_one(self, _doc):
        raise AssertionError("Evaluator must not write invoice_batches in Wave 4")

    async def update_one(self, _q, _u):
        raise AssertionError("Evaluator must not update invoice_batches in Wave 4")


def _db(triggers_rows):
    class _DB:
        pass
    db = _DB()
    db.contract_billing_triggers = _FakeTriggers(triggers_rows)
    db.invoice_batches = _FakeInvoiceBatches()
    return db


# ---------------------------------------------------------------------------
# Trigger-type math
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_date_trigger_fires_when_due():
    past_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db = _db([{
        "trigger_id": "trig_date",
        "contract_id": "c_001",
        "trigger_type": "date",
        "threshold": past_iso,
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(db, _contract())
    assert len(result["fired"]) == 1
    assert result["fired"][0]["trigger_id"] == "trig_date"


@pytest.mark.asyncio
async def test_date_trigger_does_not_fire_when_future():
    future_iso = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    db = _db([{
        "trigger_id": "trig_date",
        "contract_id": "c_001",
        "trigger_type": "date",
        "threshold": future_iso,
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(db, _contract())
    assert result["fired"] == []
    assert len(result["skipped"]) == 1


@pytest.mark.asyncio
async def test_pct_consumed_fires_at_threshold():
    """80% threshold; 16 of 20 hours consumed → 80%, fire."""
    db = _db([{
        "trigger_id": "trig_pct",
        "contract_id": "c_001",
        "trigger_type": "pct_consumed",
        "threshold": 80,
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(
        db, _contract(hours_consumed=16.0),
    )
    assert len(result["fired"]) == 1


@pytest.mark.asyncio
async def test_pct_consumed_below_threshold_does_not_fire():
    db = _db([{
        "trigger_id": "trig_pct",
        "contract_id": "c_001",
        "trigger_type": "pct_consumed",
        "threshold": 80,
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(
        db, _contract(hours_consumed=10.0),
    )
    assert result["fired"] == []


@pytest.mark.asyncio
async def test_pct_consumed_skips_when_total_missing():
    db = _db([{
        "trigger_id": "trig_pct",
        "contract_id": "c_001",
        "trigger_type": "pct_consumed",
        "threshold": 50,
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    # No total_hours / resource_types — the resolver returns 0.
    result = await evaluate_contract_billing_triggers(
        db,
        {"contract_id": "c_001", "hours_consumed": 5.0},
    )
    assert result["fired"] == []
    assert result["skipped"][0]["reason"] == "missing_total_hours"


@pytest.mark.asyncio
async def test_remaining_threshold_fires_at_threshold():
    """5 remaining (20 - 15), threshold 5 → fire."""
    db = _db([{
        "trigger_id": "trig_rem",
        "contract_id": "c_001",
        "trigger_type": "remaining_threshold",
        "threshold": 5,
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(
        db, _contract(hours_consumed=15.0),
    )
    assert len(result["fired"]) == 1


@pytest.mark.asyncio
async def test_block_exhausted_fires_when_flag_set():
    db = _db([{
        "trigger_id": "trig_block",
        "contract_id": "c_001",
        "trigger_type": "block_exhausted",
        "action": "queue_invoice_batch",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(
        db, _contract(hours_consumed=20.0, bill_and_actuals_active=True),
    )
    assert len(result["fired"]) == 1
    # Action returns the invoice-batch eligibility marker.
    assert result["fired"][0]["action_result"] == "invoice_batch_eligible"


@pytest.mark.asyncio
async def test_block_exhausted_fires_when_consumed_reaches_total():
    """Flag may not have been set yet but the math agrees."""
    db = _db([{
        "trigger_id": "trig_block",
        "contract_id": "c_001",
        "trigger_type": "block_exhausted",
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(
        db, _contract(hours_consumed=20.0, bill_and_actuals_active=False),
    )
    assert len(result["fired"]) == 1


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oneshot_trigger_flips_to_fired_after_match():
    db = _db([{
        "trigger_id": "trig_block",
        "contract_id": "c_001",
        "trigger_type": "block_exhausted",
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    await evaluate_contract_billing_triggers(
        db, _contract(hours_consumed=20.0, bill_and_actuals_active=True),
    )
    # State was flipped on the row.
    assert db.contract_billing_triggers._rows[0]["state"] == "fired"


@pytest.mark.asyncio
async def test_already_fired_trigger_is_not_re_evaluated():
    """Trigger that's already state='fired' is ignored — the
    evaluator only loads state='armed' rows."""
    db = _db([{
        "trigger_id": "trig_block",
        "contract_id": "c_001",
        "trigger_type": "block_exhausted",
        "action": "notify_finance",
        "state": "fired",  # already fired previously
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(
        db, _contract(hours_consumed=20.0, bill_and_actuals_active=True),
    )
    assert result["fired"] == []
    assert result["skipped"] == []


@pytest.mark.asyncio
async def test_disabled_trigger_is_not_evaluated():
    db = _db([{
        "trigger_id": "trig_block",
        "contract_id": "c_001",
        "trigger_type": "block_exhausted",
        "action": "notify_finance",
        "state": "disabled",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(
        db, _contract(bill_and_actuals_active=True),
    )
    assert result["fired"] == []


@pytest.mark.asyncio
async def test_biweekly_cycle_stays_armed_after_firing():
    db = _db([{
        "trigger_id": "trig_biweekly",
        "contract_id": "c_001",
        "trigger_type": "biweekly_cycle",
        "threshold": None,
        "action": "queue_invoice_batch",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(db, _contract())
    assert len(result["fired"]) == 1
    # The row stayed armed so the next cycle can fire again.
    assert db.contract_billing_triggers._rows[0]["state"] == "armed"
    # last_fired_at WAS stamped.
    assert db.contract_billing_triggers._rows[0].get("last_fired_at") is not None


# ---------------------------------------------------------------------------
# Action handler rules — no invoice batches, no broad notifications.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_invoice_batch_does_not_create_invoice_batches():
    """The strict assertion: the evaluator must never touch
    invoice_batches in Wave 4. The fake collection's insert_one /
    update_one both raise AssertionError; a successful evaluation
    proves the rule."""
    db = _db([{
        "trigger_id": "trig_batch",
        "contract_id": "c_001",
        "trigger_type": "biweekly_cycle",
        "action": "queue_invoice_batch",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(db, _contract())
    assert result["fired"][0]["action_result"] == "invoice_batch_eligible"


@pytest.mark.asyncio
async def test_notify_finance_returns_not_configured_marker():
    db = _db([{
        "trigger_id": "trig_notify",
        "contract_id": "c_001",
        "trigger_type": "date",
        "threshold": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "action": "notify_finance",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(db, _contract())
    assert result["fired"][0]["action_result"] == "notification_not_configured"


@pytest.mark.asyncio
async def test_create_task_returns_not_configured_marker():
    db = _db([{
        "trigger_id": "trig_task",
        "contract_id": "c_001",
        "trigger_type": "date",
        "threshold": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "action": "create_task",
        "state": "armed",
    }])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    result = await evaluate_contract_billing_triggers(db, _contract())
    assert result["fired"][0]["action_result"] == "task_creation_not_configured"


# ---------------------------------------------------------------------------
# Mixed batch — multiple triggers on one contract.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_triggers_evaluated_independently():
    db = _db([
        {
            "trigger_id": "trig_pct_50",
            "contract_id": "c_001",
            "trigger_type": "pct_consumed",
            "threshold": 50,
            "action": "notify_finance",
            "state": "armed",
        },
        {
            "trigger_id": "trig_pct_90",
            "contract_id": "c_001",
            "trigger_type": "pct_consumed",
            "threshold": 90,
            "action": "notify_finance",
            "state": "armed",
        },
    ])
    from services.contract_trigger_evaluator import evaluate_contract_billing_triggers
    # 70% consumed — 50 fires, 90 skips.
    result = await evaluate_contract_billing_triggers(
        db, _contract(hours_consumed=14.0),
    )
    fired_ids = {f["trigger_id"] for f in result["fired"]}
    skipped_ids = {s["trigger_id"] for s in result["skipped"]}
    assert fired_ids == {"trig_pct_50"}
    assert skipped_ids == {"trig_pct_90"}
