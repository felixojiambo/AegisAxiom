"""Tests for the Nova persistence layer.

Mocked Motor database via AsyncMock — no network, no env vars. The
projection_runs collection contract is exercised: save shape, scoped
reads, latest-by-user, listing order, hash lookup.
"""
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.nova_engine.persistence import (
    _COLLECTION,
    _new_run_id,
    find_by_inputs_hash,
    get_latest_run,
    get_run,
    list_runs,
    save_run,
)
from services.nova_engine.models import (
    ProjectionInputs,
    ProjectionOutput,
    RecurringRevenueAssumption,
    WeekRow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs() -> ProjectionInputs:
    return ProjectionInputs(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="MRR neutralizer",
                amount_per_month=0.0,
                line_type="MRR",
                starts_month="2026-05",
                ends_month="2026-12",
            ),
        ],
    )


def _make_output(inputs_hash: str = "abc123") -> ProjectionOutput:
    weeks = [
        WeekRow(week_number=1, week_starting=date(2026, 5, 1), ending_cash=51000.0),
    ]
    return ProjectionOutput(
        weeks=weeks,
        starting_cash=50000.0,
        ending_cash=51000.0,
        minimum_cash=51000.0,
        minimum_cash_week=1,
        total_revenue=15000.0,
        total_costs=10000.0,
        net_change=5000.0,
        inputs_hash=inputs_hash,
        computed_at=datetime.now(timezone.utc),
    )


def _mock_collection_db(collection_mock: MagicMock) -> MagicMock:
    """Wrap a collection mock in a db-like object so db[_COLLECTION] returns it."""
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection_mock)
    return db


def _cursor_from(rows: list[dict]) -> MagicMock:
    """A find-cursor mock that supports .sort().limit().to_list()."""
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


# ---------------------------------------------------------------------------
# _new_run_id
# ---------------------------------------------------------------------------

def test_new_run_id_format():
    rid = _new_run_id()
    assert rid.startswith("pr_")
    assert len(rid) == 3 + 12  # "pr_" + 12 hex chars


def test_new_run_id_unique():
    a = _new_run_id()
    b = _new_run_id()
    assert a != b


# ---------------------------------------------------------------------------
# save_run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_run_returns_doc_with_expected_shape():
    inputs = _make_inputs()
    output = _make_output(inputs_hash="hash_abc")
    coll = MagicMock()
    coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="bson_oid"))
    db = _mock_collection_db(coll)

    doc = await save_run(
        user_id="user_123",
        inputs=inputs,
        output=output,
        session_id="sess_1",
        message_id="msg_1",
        db=db,
    )

    coll.insert_one.assert_awaited_once()
    inserted = coll.insert_one.await_args.args[0]
    assert inserted["user_id"] == "user_123"
    assert inserted["session_id"] == "sess_1"
    assert inserted["message_id"] == "msg_1"
    assert inserted["projection_run_id"].startswith("pr_")
    assert inserted["inputs_hash"] == "hash_abc"
    assert inserted["engine_version"] == "0.1.0"
    assert inserted["parent_run_id"] is None
    assert isinstance(inserted["created_at"], datetime)
    assert inserted["created_at"].tzinfo is not None
    assert inserted["inputs"]["starting_cash"] == 50000.0
    assert inserted["output"]["ending_cash"] == 51000.0

    # Returned doc is the same shape but with _id stripped.
    assert "_id" not in doc
    assert doc["projection_run_id"] == inserted["projection_run_id"]


@pytest.mark.asyncio
async def test_save_run_uses_app_db_when_db_omitted(monkeypatch):
    """When db kwarg is None, save_run resolves it via get_app_db()."""
    inputs = _make_inputs()
    output = _make_output()
    coll = MagicMock()
    coll.insert_one = AsyncMock()
    db = _mock_collection_db(coll)

    monkeypatch.setattr(
        "services.nova_engine.persistence.get_app_db",
        lambda: db,
    )

    await save_run(user_id="user_x", inputs=inputs, output=output)
    coll.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_run_with_parent_run_id():
    inputs = _make_inputs()
    output = _make_output()
    coll = MagicMock()
    coll.insert_one = AsyncMock()
    db = _mock_collection_db(coll)

    await save_run(
        user_id="user_1",
        inputs=inputs,
        output=output,
        parent_run_id="pr_abc",
        db=db,
    )

    inserted = coll.insert_one.await_args.args[0]
    assert inserted["parent_run_id"] == "pr_abc"


@pytest.mark.asyncio
async def test_save_run_freshness_warnings_default_empty():
    inputs = _make_inputs()
    output = _make_output()
    coll = MagicMock()
    coll.insert_one = AsyncMock()
    db = _mock_collection_db(coll)

    await save_run(user_id="user_1", inputs=inputs, output=output, db=db)

    inserted = coll.insert_one.await_args.args[0]
    assert inserted["freshness_warnings"] == []


# ---------------------------------------------------------------------------
# get_run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_run_returns_match():
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value={
        "projection_run_id": "pr_abc",
        "user_id": "user_1",
    })
    db = _mock_collection_db(coll)

    result = await get_run("pr_abc", "user_1", db=db)
    assert result == {"projection_run_id": "pr_abc", "user_id": "user_1"}
    coll.find_one.assert_awaited_once_with(
        {"projection_run_id": "pr_abc", "user_id": "user_1"},
        {"_id": 0},
    )


@pytest.mark.asyncio
async def test_get_run_returns_none_for_cross_user_access():
    """get_run scopes by user_id — a request for someone else's run is filtered out."""
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=None)  # filter excludes the doc
    db = _mock_collection_db(coll)

    result = await get_run("pr_abc", "user_attacker", db=db)
    assert result is None
    # Verify the query included user_id filter (not just the run id).
    args = coll.find_one.await_args.args
    assert args[0]["user_id"] == "user_attacker"


@pytest.mark.asyncio
async def test_get_run_returns_none_when_missing():
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=None)
    db = _mock_collection_db(coll)

    result = await get_run("pr_missing", "user_1", db=db)
    assert result is None


# ---------------------------------------------------------------------------
# get_latest_run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_latest_run_no_session_filter():
    coll = MagicMock()
    coll.find = MagicMock(return_value=_cursor_from([
        {"projection_run_id": "pr_latest", "user_id": "user_1"}
    ]))
    db = _mock_collection_db(coll)

    result = await get_latest_run("user_1", db=db)
    assert result == {"projection_run_id": "pr_latest", "user_id": "user_1"}
    coll.find.assert_called_once_with({"user_id": "user_1"}, {"_id": 0})


@pytest.mark.asyncio
async def test_get_latest_run_with_session_filter():
    coll = MagicMock()
    coll.find = MagicMock(return_value=_cursor_from([
        {"projection_run_id": "pr_latest", "session_id": "sess_42"}
    ]))
    db = _mock_collection_db(coll)

    await get_latest_run("user_1", session_id="sess_42", db=db)
    coll.find.assert_called_once_with(
        {"user_id": "user_1", "session_id": "sess_42"},
        {"_id": 0},
    )


@pytest.mark.asyncio
async def test_get_latest_run_returns_none_when_no_runs():
    coll = MagicMock()
    coll.find = MagicMock(return_value=_cursor_from([]))
    db = _mock_collection_db(coll)

    result = await get_latest_run("user_1", db=db)
    assert result is None


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_runs_returns_rows_in_cursor_order():
    rows = [
        {"projection_run_id": "pr_3"},
        {"projection_run_id": "pr_2"},
        {"projection_run_id": "pr_1"},
    ]
    cursor = _cursor_from(rows)
    coll = MagicMock()
    coll.find = MagicMock(return_value=cursor)
    db = _mock_collection_db(coll)

    result = await list_runs("user_1", limit=20, db=db)
    assert [r["projection_run_id"] for r in result] == ["pr_3", "pr_2", "pr_1"]
    cursor.sort.assert_called_once_with("created_at", -1)
    cursor.limit.assert_called_once_with(20)


@pytest.mark.asyncio
async def test_list_runs_passes_user_id_filter():
    coll = MagicMock()
    coll.find = MagicMock(return_value=_cursor_from([]))
    db = _mock_collection_db(coll)

    await list_runs("user_xyz", limit=5, db=db)
    coll.find.assert_called_once_with({"user_id": "user_xyz"}, {"_id": 0})


@pytest.mark.asyncio
async def test_list_runs_default_limit_20():
    cursor = _cursor_from([])
    coll = MagicMock()
    coll.find = MagicMock(return_value=cursor)
    db = _mock_collection_db(coll)

    await list_runs("user_1", db=db)
    cursor.limit.assert_called_once_with(20)


# ---------------------------------------------------------------------------
# find_by_inputs_hash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_by_inputs_hash_hit():
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value={
        "projection_run_id": "pr_existing",
        "user_id": "user_1",
        "inputs_hash": "abc123",
    })
    db = _mock_collection_db(coll)

    result = await find_by_inputs_hash("user_1", "abc123", db=db)
    assert result["projection_run_id"] == "pr_existing"
    coll.find_one.assert_awaited_once_with(
        {"user_id": "user_1", "inputs_hash": "abc123"},
        {"_id": 0},
    )


@pytest.mark.asyncio
async def test_find_by_inputs_hash_miss():
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=None)
    db = _mock_collection_db(coll)

    result = await find_by_inputs_hash("user_1", "no_match", db=db)
    assert result is None
