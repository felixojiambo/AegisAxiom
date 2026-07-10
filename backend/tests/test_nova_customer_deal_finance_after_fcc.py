"""Tests for customer_deal_finance.py after FCC ingestion (Wave 2J).

Verifies the new data source: finance_drafts(kind=ar_invoice) replaces the
deprecated ar_invoices reads. The cost path (time_entries x contracts) is
unchanged.

Covers:
  - Failed drafts excluded from revenue
  - Draft/Sent/Synced drafts included in revenue
  - amount field used (not total/total_amount)
  - counterparty_id used as customer key (not account_id)
  - source_contract_id used as deal key (not deal_id)
  - unattributed bucket when counterparty_id is missing
  - unavailable_mapping when finance_drafts returns no rows
  - cost path is unchanged (contracts collection stays contracts)
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.nova_engine.customer_deal_finance import (
    _AR_DRAFT_KIND,
    _AR_REVENUE_SYNC_STATUSES_EXCLUDE,
    _UNATTRIBUTED_KEY,
    aggregate_customer_deal_finance,
)


# ---------------------------------------------------------------------------
# DB mock helpers
# ---------------------------------------------------------------------------


def _cursor(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _make_db(*, ar_drafts=None, contracts=None, time_entries=None):
    """Build a MagicMock db for customer_deal_finance tests.

    finance_drafts.find() with kind=ar_invoice returns ar_drafts.
    contracts.find() returns contracts.
    time_entries.find() returns time_entries.
    """
    ar_drafts = ar_drafts or []
    contracts = contracts or []
    time_entries = time_entries or []

    drafts_collection = MagicMock()

    def _drafts_find(query, *args, **kwargs):
        if query.get("kind") == _AR_DRAFT_KIND:
            return _cursor(ar_drafts)
        return _cursor([])

    drafts_collection.find = MagicMock(side_effect=_drafts_find)

    contracts_collection = MagicMock()
    contracts_collection.find = MagicMock(return_value=_cursor(contracts))

    entries_collection = MagicMock()
    entries_collection.find = MagicMock(return_value=_cursor(time_entries))

    db = MagicMock()
    db.finance_drafts = drafts_collection
    db.contracts = contracts_collection
    db.time_entries = entries_collection
    return db


# ---------------------------------------------------------------------------
# Revenue — sync_status filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_drafts_excluded_from_revenue():
    """Failed drafts must not count toward customer revenue."""
    ar_drafts = [
        {"kind": "ar_invoice", "amount": 1000, "sync_status": "Sent",
         "counterparty_id": "acc_1", "counterparty": "Acme"},
        {"kind": "ar_invoice", "amount": 9999, "sync_status": "Failed",
         "counterparty_id": "acc_1", "counterparty": "Acme"},
    ]
    db = _make_db(ar_drafts=ar_drafts)
    result = await aggregate_customer_deal_finance(
        dimension="customer", metric="revenue", db=db
    )
    rows = {r["key"]: r for r in result["rows"] if not r.get("is_unattributed")}
    assert rows["acc_1"]["revenue"] == 1000.0


@pytest.mark.asyncio
async def test_draft_sent_synced_all_count_as_revenue():
    """Draft, Sent, and Synced all represent real economic activity."""
    ar_drafts = [
        {"kind": "ar_invoice", "amount": 100, "sync_status": "Draft",
         "counterparty_id": "acc_a"},
        {"kind": "ar_invoice", "amount": 200, "sync_status": "Sent",
         "counterparty_id": "acc_a"},
        {"kind": "ar_invoice", "amount": 300, "sync_status": "Synced",
         "counterparty_id": "acc_a"},
    ]
    db = _make_db(ar_drafts=ar_drafts)
    result = await aggregate_customer_deal_finance(
        dimension="customer", metric="revenue", db=db
    )
    rows = {r["key"]: r for r in result["rows"] if not r.get("is_unattributed")}
    assert rows["acc_a"]["revenue"] == 600.0


# ---------------------------------------------------------------------------
# Revenue — amount field (not total)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revenue_uses_amount_field():
    """finance_drafts uses `amount` — not `total` or `total_amount`."""
    ar_drafts = [
        {
            "kind": "ar_invoice",
            "amount": 4500,
            "total": 99999,          # legacy field — must be ignored
            "total_amount": 88888,   # legacy field — must be ignored
            "sync_status": "Sent",
            "counterparty_id": "acc_x",
        }
    ]
    db = _make_db(ar_drafts=ar_drafts)
    result = await aggregate_customer_deal_finance(
        dimension="customer", metric="revenue", db=db
    )
    rows = {r["key"]: r for r in result["rows"] if not r.get("is_unattributed")}
    assert rows["acc_x"]["revenue"] == 4500.0


# ---------------------------------------------------------------------------
# Revenue — customer dimension uses counterparty_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_dimension_uses_counterparty_id():
    """Customer dimension groups by counterparty_id, not account_id."""
    ar_drafts = [
        {
            "kind": "ar_invoice",
            "amount": 750,
            "sync_status": "Sent",
            "counterparty_id": "acc_aegisaxiom_123",
            "account_id": "legacy_id",   # old field — must not be used as key
            "counterparty": "ClientCo",
        },
    ]
    db = _make_db(ar_drafts=ar_drafts)
    result = await aggregate_customer_deal_finance(
        dimension="customer", metric="revenue", db=db
    )
    keys = {r["key"] for r in result["rows"]}
    assert "acc_aegisaxiom_123" in keys
    assert "legacy_id" not in keys


# ---------------------------------------------------------------------------
# Revenue — deal dimension uses source_contract_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deal_dimension_uses_source_contract_id():
    """Deal dimension groups by source_contract_id (FCC field), not deal_id."""
    ar_drafts = [
        {
            "kind": "ar_invoice",
            "amount": 1200,
            "sync_status": "Sent",
            "source_contract_id": "contract_abc",
            "deal_id": "old_deal_id",   # old field — must not be the key
        },
    ]
    db = _make_db(ar_drafts=ar_drafts)
    result = await aggregate_customer_deal_finance(
        dimension="deal", metric="revenue", db=db
    )
    keys = {r["key"] for r in result["rows"]}
    assert "contract_abc" in keys
    assert "old_deal_id" not in keys


# ---------------------------------------------------------------------------
# Revenue — unattributed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unattributed_bucket_when_counterparty_id_missing():
    """Drafts without counterparty_id land in the unattributed bucket."""
    ar_drafts = [
        {"kind": "ar_invoice", "amount": 800, "sync_status": "Sent"},  # no counterparty_id
        {"kind": "ar_invoice", "amount": 200, "sync_status": "Sent",
         "counterparty_id": "acc_real"},
    ]
    db = _make_db(ar_drafts=ar_drafts)
    result = await aggregate_customer_deal_finance(
        dimension="customer", metric="revenue", db=db
    )
    unattrib = [r for r in result["rows"] if r.get("is_unattributed")]
    attributed = [r for r in result["rows"] if not r.get("is_unattributed")]
    assert len(unattrib) == 1
    assert unattrib[0]["revenue"] == 800.0
    assert attributed[0]["key"] == "acc_real"
    assert attributed[0]["revenue"] == 200.0


# ---------------------------------------------------------------------------
# Revenue — unavailable_mapping when no drafts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revenue_unavailable_when_no_drafts():
    """Empty finance_drafts(ar_invoice) → unavailable_mapping error."""
    db = _make_db(ar_drafts=[])
    result = await aggregate_customer_deal_finance(
        dimension="customer", metric="revenue", db=db
    )
    assert result["error"] == "unavailable_mapping"
    assert "finance_drafts" in result["detail"] or "ar_invoice" in result["detail"]


# ---------------------------------------------------------------------------
# Cost path — contracts collection name unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_path_reads_contracts_collection():
    """Cost path still reads `contracts` collection (name unchanged after spinout)."""
    time_entries = [
        {"contract_id": "c1", "status": "approved",
         "user_id": "u1", "duration": 60, "resource_type_id": "dev"},
    ]
    contracts = [
        {
            "contract_id": "c1",
            "account_id": "acc_client",
            "account_name": "ClientCo",
            "resources": [
                {"user_id": "u1", "resource_type_id": "dev", "cost_rate": 100}
            ],
        }
    ]
    db = _make_db(time_entries=time_entries, contracts=contracts)
    result = await aggregate_customer_deal_finance(
        dimension="customer", metric="cost", db=db
    )
    # Confirm contracts collection was queried (not operations_contracts or similar).
    db.contracts.find.assert_called()
    rows = {r["key"]: r for r in result["rows"] if not r.get("is_unattributed")}
    # 60 min = 1 hour × $100/hr = $100 cost
    assert rows["acc_client"]["cost"] == pytest.approx(100.0, rel=1e-4)


# ---------------------------------------------------------------------------
# Margin — combines revenue (drafts) + cost (time_entries)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_margin_combines_draft_revenue_and_time_entry_cost():
    """Margin = draft revenue - time_entry cost on the same customer."""
    ar_drafts = [
        {
            "kind": "ar_invoice", "amount": 2000, "sync_status": "Sent",
            "counterparty_id": "acc_margin",
        }
    ]
    time_entries = [
        {"contract_id": "c2", "status": "approved",
         "user_id": "u2", "duration": 120, "resource_type_id": "pm"},
    ]
    contracts = [
        {
            "contract_id": "c2",
            "account_id": "acc_margin",
            "resources": [
                {"user_id": "u2", "resource_type_id": "pm", "cost_rate": 50}
            ],
        }
    ]
    db = _make_db(ar_drafts=ar_drafts, time_entries=time_entries, contracts=contracts)
    result = await aggregate_customer_deal_finance(
        dimension="customer", metric="margin", db=db
    )
    rows = {r["key"]: r for r in result["rows"] if not r.get("is_unattributed")}
    # revenue $2000, cost 2h × $50 = $100, margin = $1900
    assert rows["acc_margin"]["revenue"] == 2000.0
    assert rows["acc_margin"]["cost"] == pytest.approx(100.0, rel=1e-4)
    assert rows["acc_margin"]["margin"] == pytest.approx(1900.0, rel=1e-4)


# ---------------------------------------------------------------------------
# Constant sanity
# ---------------------------------------------------------------------------


def test_excluded_sync_statuses():
    """Only Failed should be excluded from revenue — not Draft/Sent/Synced."""
    assert _AR_REVENUE_SYNC_STATUSES_EXCLUDE == {"Failed"}
    assert "Draft" not in _AR_REVENUE_SYNC_STATUSES_EXCLUDE
    assert "Sent" not in _AR_REVENUE_SYNC_STATUSES_EXCLUDE
    assert "Synced" not in _AR_REVENUE_SYNC_STATUSES_EXCLUDE
