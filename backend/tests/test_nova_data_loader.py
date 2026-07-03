"""Tests for the Nova data loader.

Two layers:

  Unit tests (always run): mocked Motor clients exercise the aggregation
  pipelines and Python categorization logic. No network, no env vars.

  Integration test (gated): runs only if NOVA_RUN_DATA_LOADER_TESTS=1.
  Connects to the real Atlas cluster, builds a ProjectionInputs against
  live data, asserts STRUCTURAL correctness only.
"""
import os
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.nova_engine import data_loader
from services.nova_engine.data_loader import (
    _coerce_to_date,
    _next_n_months,
    _prior_year,
    _start_of_next_month,
    build_projection_inputs,
    get_latest_tx_date,
    load_account_groupings,
    load_gross_profit_history,
    load_mrr_history,
    load_revenue_history,
)
from services.nova_engine.models import ProjectionInputs


# Sanity ----------------------------------------------------------------

def test_module_imports():
    assert hasattr(data_loader, "build_projection_inputs")
    assert hasattr(data_loader, "load_revenue_history")
    assert hasattr(data_loader, "load_gross_profit_history")
    assert hasattr(data_loader, "load_mrr_history")
    assert hasattr(data_loader, "get_latest_tx_date")
    assert hasattr(data_loader, "get_mongo_client")


# Pure helpers ---------------------------------------------------------

def test_coerce_to_date_from_datetime():
    assert _coerce_to_date(datetime(2026, 4, 14, 12, 0, 0)) == date(2026, 4, 14)


def test_coerce_to_date_from_date():
    assert _coerce_to_date(date(2026, 4, 14)) == date(2026, 4, 14)


def test_coerce_to_date_from_iso_string():
    assert _coerce_to_date("2026-04-14") == date(2026, 4, 14)


def test_coerce_to_date_from_iso_string_with_time():
    assert _coerce_to_date("2026-04-14T12:00:00Z") == date(2026, 4, 14)


def test_coerce_to_date_rejects_unsupported_type():
    with pytest.raises(TypeError):
        _coerce_to_date(12345)


def test_start_of_next_month_simple():
    assert _start_of_next_month("2026-05") == "2026-06-01"


def test_start_of_next_month_year_rollover():
    assert _start_of_next_month("2026-12") == "2027-01-01"


def test_prior_year_simple():
    assert _prior_year("2026-05") == "2025-05"


def test_prior_year_january():
    assert _prior_year("2026-01") == "2025-01"


def test_next_n_months_basic():
    assert _next_n_months("2026-05", 4) == ["2026-05", "2026-06", "2026-07", "2026-08"]


def test_next_n_months_year_rollover():
    assert _next_n_months("2026-11", 4) == ["2026-11", "2026-12", "2027-01", "2027-02"]


def test_next_n_months_zero():
    assert _next_n_months("2026-05", 0) == []


# Mock factories -------------------------------------------------------

def _make_mock_db_with_aggregate(rows: list[dict]) -> MagicMock:
    """Mock Motor db whose general_ledger.aggregate() yields given rows."""
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=rows)

    mock_collection = MagicMock()
    mock_collection.aggregate = MagicMock(return_value=mock_cursor)

    mock_db = MagicMock()
    mock_db.general_ledger = mock_collection
    return mock_db


def _make_mock_app_db_with_groupings(docs: list[dict]) -> MagicMock:
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=docs)

    mock_collection = MagicMock()
    mock_collection.find = MagicMock(return_value=mock_cursor)

    mock_db = MagicMock()
    mock_db.account_groupings = mock_collection
    return mock_db


def _make_sequential_qbo_db(*aggregate_results: list[dict]) -> MagicMock:
    """Mock qbo_db that returns DIFFERENT rows on consecutive aggregate() calls.

    Used for build_projection_inputs orchestration tests.

    Also provides general_ledger.find_one as an AsyncMock that returns a
    document with a string tx_date — this matches the AEGISAXIOM GL schema and
    exercises the orchestrator's tx_date type sanity check on the
    "no warning" path. If a test wants to exercise the warning path,
    override the find_one return value after construction.
    """
    call_index = {"i": 0}

    def aggregate_side_effect(*args, **kwargs):
        idx = call_index["i"]
        call_index["i"] += 1
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=aggregate_results[idx])
        return cursor

    mock_collection = MagicMock()
    mock_collection.aggregate = MagicMock(side_effect=aggregate_side_effect)
    mock_collection.find_one = AsyncMock(return_value={"tx_date": "2026-01-15"})

    mock_db = MagicMock()
    mock_db.general_ledger = mock_collection
    return mock_db


# get_latest_tx_date ---------------------------------------------------

@pytest.mark.asyncio
async def test_get_latest_tx_date_from_string():
    mock_db = _make_mock_db_with_aggregate([{"_id": None, "max_date": "2026-04-14"}])
    result = await get_latest_tx_date(mock_db)
    assert result == date(2026, 4, 14)


@pytest.mark.asyncio
async def test_get_latest_tx_date_from_datetime():
    mock_db = _make_mock_db_with_aggregate([
        {"_id": None, "max_date": datetime(2026, 4, 14)}
    ])
    result = await get_latest_tx_date(mock_db)
    assert result == date(2026, 4, 14)


@pytest.mark.asyncio
async def test_get_latest_tx_date_empty_collection_raises():
    mock_db = _make_mock_db_with_aggregate([])
    with pytest.raises(ValueError, match="general_ledger is empty"):
        await get_latest_tx_date(mock_db)


# load_revenue_history -------------------------------------------------

@pytest.mark.asyncio
async def test_load_revenue_history_categorizes_correctly():
    rows = [
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0},
        {"_id": {"month": "2026-01", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 5000.0},
        {"_id": {"month": "2026-02", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 12000.0},
        {"_id": {"month": "2026-02", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 6000.0},
        {"_id": {"month": "2026-03", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 11000.0},
        {"_id": {"month": "2026-03", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 5500.0},
        # COGS row (must be ignored by revenue loader)
        {"_id": {"month": "2026-01", "account_id": "60302",
                 "account_name": "60302 PS COGS - Subcontractor"},
         "amount": 4000.0},
    ]
    mock_db = _make_mock_db_with_aggregate(rows)
    result = await load_revenue_history(
        mock_db, ["2026-01", "2026-02", "2026-03"]
    )

    assert result["PS_REVENUE"] == [10000.0, 12000.0, 11000.0]
    assert result["FS_REVENUE"] == [5000.0, 6000.0, 5500.0]
    # No OTHER_REVENUE rows in the fixture, so all months are zero —
    # the bucket is always present in the result shape.
    assert result["OTHER_REVENUE"] == [0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_load_revenue_history_buckets_other_revenue_separately():
    """Accounts that categorize() routes to OTHER_REVENUE (legacy QBO
    names with no 5-digit code, caught by the 'INCOME' heuristic) flow
    into the OTHER_REVENUE bucket — pre-Bug-A-fix they were silently
    dropped here, causing projection inputs to under-count revenue."""
    rows = [
        {"_id": {"month": "2026-01", "account_id": "any",
                 "account_name": "Other Income:SimpliComply"},
         "amount": 3000.0},
        {"_id": {"month": "2026-02", "account_id": "any",
                 "account_name": "Other Income:SimpliComply"},
         "amount": 3500.0},
        {"_id": {"month": "2026-03", "account_id": "any",
                 "account_name": "Other Income:SimpliComply"},
         "amount": 4000.0},
        # Decoy: PS revenue must not bleed into the OTHER bucket.
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0},
    ]
    mock_db = _make_mock_db_with_aggregate(rows)
    result = await load_revenue_history(
        mock_db, ["2026-01", "2026-02", "2026-03"]
    )

    assert result["OTHER_REVENUE"] == [3000.0, 3500.0, 4000.0]
    assert result["PS_REVENUE"] == [10000.0, 0.0, 0.0]
    assert result["FS_REVENUE"] == [0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_load_revenue_history_empty_input_returns_empty_lists():
    mock_db = _make_mock_db_with_aggregate([])
    result = await load_revenue_history(mock_db, [])
    assert result == {"PS_REVENUE": [], "FS_REVENUE": [], "OTHER_REVENUE": []}


@pytest.mark.asyncio
async def test_load_revenue_history_zero_months_zero_buckets():
    """Months with no GL rows return 0.0 (not absent)."""
    mock_db = _make_mock_db_with_aggregate([])
    result = await load_revenue_history(mock_db, ["2026-01", "2026-02", "2026-03"])
    assert result["PS_REVENUE"] == [0.0, 0.0, 0.0]
    assert result["FS_REVENUE"] == [0.0, 0.0, 0.0]
    assert result["OTHER_REVENUE"] == [0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_load_revenue_history_ignores_uncategorized_accounts():
    rows = [
        {"_id": {"month": "2026-01", "account_id": "99999",
                 "account_name": "Some random uncategorized account"},
         "amount": 50000.0},
    ]
    mock_db = _make_mock_db_with_aggregate(rows)
    result = await load_revenue_history(mock_db, ["2026-01", "2026-02", "2026-03"])
    assert result["PS_REVENUE"] == [0.0, 0.0, 0.0]
    assert result["FS_REVENUE"] == [0.0, 0.0, 0.0]
    # 'Some random uncategorized account' has no INCOME/COGS/EXPENSE
    # substring, so categorize() returns UNCATEGORIZED — not OTHER_REVENUE.
    assert result["OTHER_REVENUE"] == [0.0, 0.0, 0.0]


# load_gross_profit_history --------------------------------------------

@pytest.mark.asyncio
async def test_load_gross_profit_history_subtracts_cogs():
    rows = [
        # PS revenue Jan: $10K
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0},
        # PS COGS Jan: $3K
        {"_id": {"month": "2026-01", "account_id": "60302",
                 "account_name": "60302 PS COGS - Subcontractor"},
         "amount": 3000.0},
        # FS revenue Jan: $5K
        {"_id": {"month": "2026-01", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 5000.0},
        # FS COGS Jan: $2K
        {"_id": {"month": "2026-01", "account_id": "60201",
                 "account_name": "60201 FS COGS - Materials"},
         "amount": 2000.0},
        # PS revenue Feb: $12K
        {"_id": {"month": "2026-02", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 12000.0},
        # PS COGS Feb: $4K
        {"_id": {"month": "2026-02", "account_id": "60302",
                 "account_name": "60302 PS COGS - Subcontractor"},
         "amount": 4000.0},
        # PS revenue Mar: $11K
        {"_id": {"month": "2026-03", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 11000.0},
        # PS COGS Mar: $3.5K
        {"_id": {"month": "2026-03", "account_id": "60302",
                 "account_name": "60302 PS COGS - Subcontractor"},
         "amount": 3500.0},
    ]
    mock_db = _make_mock_db_with_aggregate(rows)
    result = await load_gross_profit_history(
        mock_db, ["2026-01", "2026-02", "2026-03"]
    )

    # PS GP: 10000-3000=7000, 12000-4000=8000, 11000-3500=7500
    assert result["PS_GP"] == [7000.0, 8000.0, 7500.0]
    # FS GP: 5000-2000=3000, 0-0=0, 0-0=0
    assert result["FS_GP"] == [3000.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_load_gross_profit_history_with_no_data_returns_zeros():
    mock_db = _make_mock_db_with_aggregate([])
    result = await load_gross_profit_history(
        mock_db, ["2026-01", "2026-02", "2026-03"]
    )
    assert result["PS_GP"] == [0.0, 0.0, 0.0]
    assert result["FS_GP"] == [0.0, 0.0, 0.0]
    # OTHER_COGS surfaced separately so callers know whether company GP
    # diverges from the per-service-line PS+FS GP.
    assert result["OTHER_COGS_TOTAL"] == [0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_load_gross_profit_history_surfaces_other_cogs_separately():
    """PS_GP and FS_GP are intentionally per-service-line — OTHER_COGS
    must NOT be folded into them. Callers that need company-level GP
    look at OTHER_COGS_TOTAL to know whether the per-service split
    diverges from company GP."""
    other_cogs_jan = 750.0
    other_cogs_feb = 250.0
    rows = [
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0},
        {"_id": {"month": "2026-01", "account_id": "60302",
                 "account_name": "60302 PS COGS"},
         "amount": 3000.0},
        {"_id": {"month": "2026-01", "account_id": "OTHER",
                 "account_name": "Generic Cost of Goods"},
         "amount": other_cogs_jan},
        {"_id": {"month": "2026-02", "account_id": "OTHER",
                 "account_name": "Generic Cost of Goods"},
         "amount": other_cogs_feb},
    ]
    mock_db = _make_mock_db_with_aggregate(rows)
    result = await load_gross_profit_history(
        mock_db, ["2026-01", "2026-02", "2026-03"]
    )

    # PS_GP is unchanged: still revenue - PS_COGS only.
    assert result["PS_GP"][0] == 10000.0 - 3000.0
    # OTHER_COGS surfaced per-month from fixture amounts.
    assert result["OTHER_COGS_TOTAL"] == [other_cogs_jan, other_cogs_feb, 0.0]


# load_mrr_history -----------------------------------------------------

@pytest.mark.asyncio
async def test_load_mrr_history_returns_prior_year_same_month_totals():
    """Forecast 2026-05 / 2026-06 looks up GL rows in 2025-05 / 2025-06
    that categorize as MRR (account prefix 50300). The result is keyed
    by FORECAST month, with values summed and sign-flipped to positive."""
    rows = [
        # 2025-05: $10k MRR via the hierarchical 50300 form.
        {"_id": {"month": "2025-05", "account_id": "50300",
                 "account_name": "50300 Other Income:Contract Commission Income"},
         "amount": 10000.0},
        # 2025-06: split across two flavors of 50300, both categorize as MRR.
        {"_id": {"month": "2025-06", "account_id": "50300",
                 "account_name": "50300 Other Income:Contract Commission Income"},
         "amount": 7500.0},
        {"_id": {"month": "2025-06", "account_id": "50300",
                 "account_name": "50300 Contract Commission Income"},
         "amount": 2500.0},
        # Decoy: 2025-05 PS revenue must NOT contribute to MRR total.
        {"_id": {"month": "2025-05", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 50000.0},
    ]
    mock_db = _make_mock_db_with_aggregate(rows)
    result = await load_mrr_history(mock_db, ["2026-05", "2026-06"])

    assert result == {"2026-05": 10000.0, "2026-06": 10000.0}


@pytest.mark.asyncio
async def test_load_mrr_history_empty_forecast_months_returns_empty_dict():
    """Empty input is a no-op: return {} without hitting Mongo."""
    mock_db = _make_mock_db_with_aggregate([])
    result = await load_mrr_history(mock_db, [])
    assert result == {}


@pytest.mark.asyncio
async def test_load_mrr_history_zero_mrr_rows_returns_empty_dict():
    """A tenant whose GL has no 50300 activity in the relevant prior-year
    months gets an empty dict, NOT an exception. Empty MRR is a real
    state (a tenant that hasn't booked MRR yet) and the engine will
    surface it via InsufficientHistoryError downstream when a manual
    MRR assumption is also missing."""
    # GL has only PS revenue rows; nothing categorizes as MRR.
    rows = [
        {"_id": {"month": "2025-05", "account_id": "50101",
                 "account_name": "50101 PS Revenue"},
         "amount": 10000.0},
    ]
    mock_db = _make_mock_db_with_aggregate(rows)
    result = await load_mrr_history(mock_db, ["2026-05", "2026-06"])
    assert result == {}


@pytest.mark.asyncio
async def test_load_mrr_history_accepts_basis_param():
    """The basis kwarg threads through to the $match stage of the
    pipeline. Sanity check that the call accepts basis='cash' without
    raising and returns the expected shape."""
    rows = [
        {"_id": {"month": "2025-05", "account_id": "50300",
                 "account_name": "50300 Contract Commission Income"},
         "amount": 8000.0},
    ]
    mock_db = _make_mock_db_with_aggregate(rows)
    result = await load_mrr_history(mock_db, ["2026-05"], basis="cash")
    assert result == {"2026-05": 8000.0}


# ---------------------------------------------------------------------------
# Audited regression: prefix $match must work when account_id is absent
# ---------------------------------------------------------------------------
#
# The live AEGISAXIOM general_ledger schema does NOT carry an ``account_id``
# field; the prefix lives only inside ``account_name``. Nova's
# previous account_id-only filter excluded every live row, returning
# an empty MRR dict and surfacing the misleading "MRR mapping
# unavailable" error. The fix in _gl_helpers._build_history_pipeline
# switches the $match to an $or across account_id AND account_name.


def test_build_history_pipeline_prefix_match_uses_or_across_account_id_and_name():
    """The emitted $match stage must scope by EITHER account_id OR
    account_name, not account_id alone — tenants whose general_ledger
    omits account_id rely on the account_name half."""
    from services.nova_engine._gl_helpers import _build_history_pipeline

    pipeline = _build_history_pipeline(
        ["2099-04", "2099-05"],
        basis="accrual",
        account_id_prefix="50300",
    )
    match_stage = pipeline[0]["$match"]

    assert "$or" in match_stage
    or_clauses = match_stage["$or"]
    fields = {next(iter(clause.keys())) for clause in or_clauses}
    assert fields == {"account_id", "account_name"}

    # account_id_prefix must not appear as a top-level key any more —
    # leaving the bare account_id constraint there alongside the $or
    # would AND it with the OR and silently re-introduce the bug for
    # tenants without account_id.
    assert "account_id" not in match_stage


def test_build_history_pipeline_no_prefix_filter_when_arg_absent():
    """Without account_id_prefix, the $match stage carries only
    tx_date + basis (no $or, no account fields)."""
    from services.nova_engine._gl_helpers import _build_history_pipeline

    pipeline = _build_history_pipeline(["2099-04"], basis="accrual")
    match_stage = pipeline[0]["$match"]

    assert "$or" not in match_stage
    assert "account_id" not in match_stage
    assert "account_name" not in match_stage


def test_build_history_pipeline_account_name_regex_anchors_with_word_boundary():
    """The account_name half of the $or must require the prefix to be
    followed by a space or end-of-string, so '50300X' or
    '50300-something' don't get false-matched alongside the real
    '50300 Contract Commission Income' rows."""
    from services.nova_engine._gl_helpers import _build_history_pipeline

    pipeline = _build_history_pipeline(
        ["2099-04"], basis="accrual", account_id_prefix="50300",
    )
    or_clauses = pipeline[0]["$match"]["$or"]
    name_clause = next(c for c in or_clauses if "account_name" in c)
    pattern = name_clause["account_name"]["$regex"]
    # Must include a trailing whitespace / EOL anchor.
    assert pattern.startswith("^50300")
    assert "(\\s|$)" in pattern


# Integration test: a fake collection that ACTUALLY evaluates the
# $match against live-schema-shaped fixture rows. The earlier
# _make_mock_db_with_aggregate helper bypasses $match entirely (it
# returns the canned rows verbatim from to_list), so it can't catch
# the audited bug. This fake walks the pipeline manually.


def _matches_or(doc: dict, or_clauses: list[dict]) -> bool:
    for clause in or_clauses:
        ((field, condition),) = clause.items()
        value = doc.get(field)
        if isinstance(condition, dict) and "$regex" in condition:
            import re as _re

            if not isinstance(value, str):
                continue
            options = condition.get("$options", "")
            flags = _re.IGNORECASE if "i" in options else 0
            if _re.search(condition["$regex"], value, flags=flags):
                return True
        elif value == condition:
            return True
    return False


def _matches_match_stage(doc: dict, match: dict) -> bool:
    for key, condition in match.items():
        if key == "$or":
            if not _matches_or(doc, condition):
                return False
            continue
        value = doc.get(key)
        if isinstance(condition, dict):
            for op, op_val in condition.items():
                if op == "$gte":
                    if value is None or value < op_val:
                        return False
                elif op == "$lt":
                    if value is None or not (value < op_val):
                        return False
                elif op == "$regex":
                    import re as _re

                    if not isinstance(value, str) or not _re.search(op_val, value):
                        return False
                else:
                    raise NotImplementedError(f"fake op: {op}")
        else:
            if value != condition:
                return False
    return True


class _MatchEvaluatingCollection:
    """Tiny fake that runs the $match + $project + $group pipeline
    over an in-memory list of GL rows. Just enough surface for
    load_mrr_history to round-trip."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = list(rows)

    def aggregate(self, pipeline: list[dict]):
        rows = list(self.rows)
        for stage in pipeline:
            if "$match" in stage:
                rows = [r for r in rows if _matches_match_stage(r, stage["$match"])]
            elif "$project" in stage:
                projected: list[dict] = []
                for r in rows:
                    out: dict = {}
                    for key, spec in stage["$project"].items():
                        if isinstance(spec, dict) and "$substr" in spec:
                            ref, start, length = spec["$substr"]
                            field = ref.lstrip("$")
                            v = r.get(field) or ""
                            out[key] = v[start : start + length]
                        elif spec in (1, True):
                            if key in r:
                                out[key] = r[key]
                    projected.append(out)
                rows = projected
            elif "$group" in stage:
                spec = stage["$group"]
                buckets: dict = {}
                for r in rows:
                    key = tuple(
                        (k, r.get(v.lstrip("$")) if isinstance(v, str) else v)
                        for k, v in spec["_id"].items()
                    )
                    bucket = buckets.setdefault(key, {"_id": dict(key), "amount": 0.0})
                    sum_spec = spec.get("amount", {}).get("$sum", 0)
                    if isinstance(sum_spec, str) and sum_spec.startswith("$"):
                        bucket["amount"] += float(r.get(sum_spec.lstrip("$")) or 0)
                rows = list(buckets.values())

        class _Cursor:
            def __init__(self, docs):
                self._docs = docs

            async def to_list(self, length=None):
                return list(self._docs) if length is None else list(self._docs[:length])

        return _Cursor(rows)


class _FakeDb:
    def __init__(self, *, general_ledger: list[dict]) -> None:
        self.general_ledger = _MatchEvaluatingCollection(general_ledger)


def _live_schema_mrr_row(*, tx_date: str, amount: float, basis: str = "accrual") -> dict:
    """A row shaped like live AEGISAXIOM general_ledger: account_name carries
    the prefix, account_id is absent entirely. Mirrors the audit's
    schema-of-record finding."""
    return {
        "tx_date": tx_date,
        "basis": basis,
        "amount": amount,
        "account_name": "50300 Other Income:Contract Commission Income",
    }


@pytest.mark.asyncio
async def test_load_mrr_history_matches_when_account_id_field_is_absent():
    """The audited bug, end-to-end: rows carry the prefix only on
    account_name (no account_id field at all). Nova must still
    derive MRR totals."""
    rows = [
        _live_schema_mrr_row(tx_date="2025-05-10", amount=6000.0),
        _live_schema_mrr_row(tx_date="2025-05-20", amount=4000.0),
        _live_schema_mrr_row(tx_date="2025-06-05", amount=7500.0),
    ]
    db = _FakeDb(general_ledger=rows)

    result = await load_mrr_history(db, ["2026-05", "2026-06"])

    expected_may = sum(r["amount"] for r in rows if r["tx_date"].startswith("2025-05"))
    expected_june = sum(r["amount"] for r in rows if r["tx_date"].startswith("2025-06"))
    assert result == {"2026-05": expected_may, "2026-06": expected_june}


@pytest.mark.asyncio
async def test_load_mrr_history_matches_when_account_id_is_null():
    """Some QBO syncs write account_id=None rather than omitting the
    field. Treat both shapes the same."""
    rows = [
        {**_live_schema_mrr_row(tx_date="2025-05-10", amount=5000.0), "account_id": None},
    ]
    db = _FakeDb(general_ledger=rows)
    result = await load_mrr_history(db, ["2026-05"])
    assert result == {"2026-05": 5000.0}


@pytest.mark.asyncio
async def test_load_mrr_history_matches_when_account_id_does_not_start_with_prefix():
    """Some tenants store a QBO internal id (e.g. UUID) on account_id
    while account_name carries the 5-digit code. The account_name
    half of the $or must catch this row."""
    rows = [
        {
            **_live_schema_mrr_row(tx_date="2025-05-10", amount=2500.0),
            "account_id": "qbo-internal-uuid-not-a-prefix",
        },
    ]
    db = _FakeDb(general_ledger=rows)
    result = await load_mrr_history(db, ["2026-05"])
    assert result == {"2026-05": 2500.0}


@pytest.mark.asyncio
async def test_load_mrr_history_matches_when_account_id_starts_with_prefix():
    """The account_id half of the $or must STILL fire for tenants whose
    sync does write the prefix to account_id. Backward compatibility
    with the previous behavior."""
    rows = [
        {
            "tx_date": "2025-05-10",
            "basis": "accrual",
            "amount": 1234.0,
            "account_id": "50300",
            "account_name": "50300 Other Income:Contract Commission Income",
        },
    ]
    db = _FakeDb(general_ledger=rows)
    result = await load_mrr_history(db, ["2026-05"])
    assert result == {"2026-05": 1234.0}


@pytest.mark.asyncio
async def test_load_mrr_history_does_not_match_unrelated_account_with_50300_substring():
    """The account_name regex anchors at start-of-string with a
    word-boundary trailing space/EOL, so '503001 Other Account' or
    'Other Income:50300 Foo' must NOT be picked up server-side. The
    Python categorize() belt is the cross-validator if a rare embedded
    prefix shape ever needs to be supported."""
    rows = [
        {
            "tx_date": "2025-05-10",
            "basis": "accrual",
            "amount": 9999.0,
            "account_name": "503001 Different Account",
        },
        {
            "tx_date": "2025-05-15",
            "basis": "accrual",
            "amount": 8888.0,
            "account_name": "Other Income:50300 Embedded Prefix",
        },
    ]
    db = _FakeDb(general_ledger=rows)
    result = await load_mrr_history(db, ["2026-05"])
    assert result == {}


# load_account_groupings ----------------------------------------------

@pytest.mark.asyncio
async def test_load_account_groupings_strips_object_id():
    docs = [
        {"_id": "fake-1", "name": "Admin", "account_ids": ["a1", "a2"]},
        {"_id": "fake-2", "name": "Payroll", "account_ids": ["a3"]},
    ]
    mock_db = _make_mock_app_db_with_groupings(docs)
    result = await load_account_groupings(mock_db)

    assert len(result) == 2
    assert all("_id" not in d for d in result)
    assert result[0] == {"name": "Admin", "account_ids": ["a1", "a2"]}


@pytest.mark.asyncio
async def test_load_account_groupings_empty():
    mock_db = _make_mock_app_db_with_groupings([])
    result = await load_account_groupings(mock_db)
    assert result == []


# build_projection_inputs (orchestrator) -------------------------------

@pytest.mark.asyncio
async def test_build_projection_inputs_full_path():
    """End-to-end: latest_tx + revenue + GP + MRR + groupings →
    valid ProjectionInputs."""

    history_rows = [
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0},
        {"_id": {"month": "2026-02", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 12000.0},
        {"_id": {"month": "2026-03", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 11000.0},
        {"_id": {"month": "2026-01", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 5000.0},
        {"_id": {"month": "2026-02", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 6000.0},
        {"_id": {"month": "2026-03", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 5500.0},
        {"_id": {"month": "2026-01", "account_id": "60302",
                 "account_name": "60302 PS COGS"}, "amount": 3000.0},
        {"_id": {"month": "2026-02", "account_id": "60302",
                 "account_name": "60302 PS COGS"}, "amount": 4000.0},
        {"_id": {"month": "2026-03", "account_id": "60302",
                 "account_name": "60302 PS COGS"}, "amount": 3500.0},
    ]

    # No MRR rows in the prior-year-same-month queries AND no rows in
    # the fallback window: load_mrr_baseline_with_fallback returns
    # {} for every forecast month and the engine has nothing to add
    # as base.
    mrr_prior_year_rows: list[dict] = []
    mrr_fallback_rows: list[dict] = []

    # No wholesale rows: detect_wholesale_history_from_gl returns zeroes
    # so auto_recurring stays empty. Aggregate-call order in
    # build_projection_inputs: latest_tx, revenue, GP, MRR prior-year,
    # MRR fallback (only when prior-year leaves any forecast month
    # uncovered), wholesale.
    mock_qbo_db = _make_sequential_qbo_db(
        [{"_id": None, "max_date": "2026-04-14"}],  # latest tx
        history_rows,                                 # revenue
        history_rows,                                 # GP (same data)
        mrr_prior_year_rows,                          # MRR prior-year same-month
        mrr_fallback_rows,                            # MRR historical run-rate fallback
        [],                                           # wholesale
    )

    mock_app_db = _make_mock_app_db_with_groupings([
        {"_id": "fake", "name": "Admin", "account_ids": ["a1"]},
    ])

    inputs = await build_projection_inputs(
        mock_qbo_db, mock_app_db,
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
    )

    assert isinstance(inputs, ProjectionInputs)
    assert inputs.starting_cash == 50000.0
    assert inputs.projection_start_month == "2026-05"
    assert inputs.horizon_weeks == 13

    assert len(inputs.ps_revenue_last_3_months) == 3
    assert len(inputs.fs_revenue_last_3_months) == 3
    assert len(inputs.ps_gross_profit_last_3_months) == 3
    assert len(inputs.fs_gross_profit_last_3_months) == 3

    assert inputs.ps_revenue_last_3_months == [10000.0, 12000.0, 11000.0]
    assert inputs.fs_revenue_last_3_months == [5000.0, 6000.0, 5500.0]
    # No OTHER_REVENUE rows in this fixture — bucket present, all zeros.
    assert inputs.other_revenue_last_3_months == [0.0, 0.0, 0.0]

    # GP = revenue - COGS
    assert inputs.ps_gross_profit_last_3_months == [7000.0, 8000.0, 7500.0]
    # FS has no COGS in this fixture
    assert inputs.fs_gross_profit_last_3_months == [5000.0, 6000.0, 5500.0]

    # MRR: empty in this fixture because mrr_rows is []. With 50300 GL
    # activity in the prior-year same-month, mrr_last_year_same_months
    # would carry per-forecast-month totals.
    assert inputs.mrr_last_year_same_months == {}

    # Wholesale stays empty (Asad's rule)
    assert inputs.wholesale_invoices == []

    # Account groupings flowed through, _id stripped
    assert len(inputs.account_groupings) == 1
    assert inputs.account_groupings[0] == {"name": "Admin", "account_ids": ["a1"]}


@pytest.mark.asyncio
async def test_build_projection_inputs_handles_zero_mrr_gracefully():
    """A tenant whose GL has no 50300 (MRR) rows in the prior-year
    same-month queries gets mrr_last_year_same_months={}. The engine
    then falls through to manual MRR; if neither base nor manual is
    present for a forecast month, _resolve_mrr_base raises
    InsufficientHistoryError - that's the expected contract."""
    history_rows = [
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0},
        {"_id": {"month": "2026-02", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0},
        {"_id": {"month": "2026-03", "account_id": "50101",
                 "account_name": "50101 Professional Services Revenue"},
         "amount": 10000.0},
        {"_id": {"month": "2026-01", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 5000.0},
        {"_id": {"month": "2026-02", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 5000.0},
        {"_id": {"month": "2026-03", "account_id": "50202",
                 "account_name": "50202 Field Services Revenue"},
         "amount": 5000.0},
    ]

    # Aggregate-call order: latest_tx, revenue, GP, MRR prior-year,
    # MRR fallback, wholesale.
    mock_qbo_db = _make_sequential_qbo_db(
        [{"_id": None, "max_date": "2026-04-14"}],
        history_rows,
        history_rows,
        [],   # no MRR rows in prior-year same-month
        [],   # no MRR rows in the historical fallback window either
        [],   # no wholesale rows
    )

    mock_app_db = _make_mock_app_db_with_groupings([])

    inputs = await build_projection_inputs(
        mock_qbo_db, mock_app_db,
        starting_cash=0.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
    )

    # Empty MRR is now a real, non-error state.
    assert inputs.mrr_last_year_same_months == {}
    assert inputs.ps_revenue_last_3_months == [10000.0, 10000.0, 10000.0]
    assert inputs.fs_revenue_last_3_months == [5000.0, 5000.0, 5000.0]


@pytest.mark.asyncio
async def test_build_projection_inputs_auto_loads_mrr_from_50300_rows():
    """End-to-end: GL has 50300 rows in prior-year same-month, the
    orchestrator surfaces them as mrr_last_year_same_months keyed by
    forecast month. No manual MRR assumption supplied - the auto-MRR
    path is what the engine consumes."""
    history_rows = [
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 PS Revenue"}, "amount": 10000.0},
        {"_id": {"month": "2026-02", "account_id": "50101",
                 "account_name": "50101 PS Revenue"}, "amount": 10000.0},
        {"_id": {"month": "2026-03", "account_id": "50101",
                 "account_name": "50101 PS Revenue"}, "amount": 10000.0},
        {"_id": {"month": "2026-01", "account_id": "50202",
                 "account_name": "50202 FS Revenue"}, "amount": 5000.0},
        {"_id": {"month": "2026-02", "account_id": "50202",
                 "account_name": "50202 FS Revenue"}, "amount": 5000.0},
        {"_id": {"month": "2026-03", "account_id": "50202",
                 "account_name": "50202 FS Revenue"}, "amount": 5000.0},
    ]
    # Forecast months for projection_start=2026-05, horizon=13 weeks
    # are 4 months: 2026-05..2026-08. Their prior-year-same-month
    # lookups are 2025-05..2025-08.
    mrr_rows = [
        {"_id": {"month": "2025-05", "account_id": "50300",
                 "account_name": "50300 Other Income:Contract Commission Income"},
         "amount": 8000.0},
        {"_id": {"month": "2025-06", "account_id": "50300",
                 "account_name": "50300 Contract Commission Income"},
         "amount": 9500.0},
    ]
    mock_qbo_db = _make_sequential_qbo_db(
        [{"_id": None, "max_date": "2026-04-14"}],
        history_rows,   # revenue
        history_rows,   # GP
        mrr_rows,       # MRR prior-year-same-month
        # Forecast months 2026-07 / 2026-08 have no prior-year coverage,
        # so the helper triggers the historical run-rate fallback. The
        # fallback window here has no 50300 rows either, so those two
        # forecast months simply omit their key (the engine then needs
        # a manual MRR assumption for them or surfaces the gap).
        [],             # MRR fallback window — no recent 50300 activity
        [],             # wholesale
    )
    mock_app_db = _make_mock_app_db_with_groupings([])

    inputs = await build_projection_inputs(
        mock_qbo_db, mock_app_db,
        starting_cash=0.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
    )
    # Forecast months that had prior-year MRR rows are populated;
    # months without prior-year activity AND no fallback rows simply
    # omit their key.
    assert inputs.mrr_last_year_same_months == {
        "2026-05": 8000.0,
        "2026-06": 9500.0,
    }


@pytest.mark.asyncio
async def test_build_projection_inputs_passes_other_revenue_through():
    """End-to-end (Bug A): GL rows that categorize as OTHER_REVENUE
    must reach ProjectionInputs.other_revenue_last_3_months so the
    engine can project them. Pre-fix they were silently dropped at
    load_revenue_history and the field would have been [0,0,0]."""
    history_rows = [
        {"_id": {"month": "2026-01", "account_id": "50101",
                 "account_name": "50101 PS Revenue"}, "amount": 10000.0},
        {"_id": {"month": "2026-02", "account_id": "50101",
                 "account_name": "50101 PS Revenue"}, "amount": 10000.0},
        {"_id": {"month": "2026-03", "account_id": "50101",
                 "account_name": "50101 PS Revenue"}, "amount": 10000.0},
        # OTHER_REVENUE rows — caught by the 'INCOME' heuristic in
        # categorize() because there is no 5-digit code in the name.
        {"_id": {"month": "2026-01", "account_id": "x",
                 "account_name": "Other Income:SimpliComply"},
         "amount": 2000.0},
        {"_id": {"month": "2026-02", "account_id": "x",
                 "account_name": "Other Income:SimpliComply"},
         "amount": 2500.0},
        {"_id": {"month": "2026-03", "account_id": "x",
                 "account_name": "Other Income:SimpliComply"},
         "amount": 3000.0},
    ]
    mock_qbo_db = _make_sequential_qbo_db(
        [{"_id": None, "max_date": "2026-04-14"}],
        history_rows,   # revenue
        history_rows,   # GP
        [],             # MRR prior-year same-month
        [],             # MRR historical run-rate fallback window
        [],             # wholesale
    )
    mock_app_db = _make_mock_app_db_with_groupings([])

    inputs = await build_projection_inputs(
        mock_qbo_db, mock_app_db,
        starting_cash=0.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
    )

    assert inputs.ps_revenue_last_3_months == [10000.0, 10000.0, 10000.0]
    assert inputs.other_revenue_last_3_months == [2000.0, 2500.0, 3000.0]


# Integration test (gated) --------------------------------------------

@pytest.mark.skipif(
    os.environ.get("NOVA_RUN_DATA_LOADER_TESTS") != "1",
    reason="Set NOVA_RUN_DATA_LOADER_TESTS=1 to run live Mongo integration test"
)
@pytest.mark.asyncio
async def test_can_connect_and_build_projection_inputs_against_real_db():
    """Live integration: connect to Atlas, build a real ProjectionInputs.

    Asserts STRUCTURAL correctness only. Numerical correctness belongs in
    the engine self-consistency acceptance test (test_nova_engine_e2e.py).

    Requires:
      - MONGO_URL env var set
      - QBO_DB defaults to "aegisaxiom"
      - DB_NAME defaults to "aegisaxiom_crm"
      - Credential has read access to both databases
      - At least 3 complete months of GL data
    """
    import os

    import database
    from services.nova_engine.data_loader import (
        get_mongo_client, get_qbo_db_name, get_app_db_name,
    )

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

    latest = await get_latest_tx_date(qbo_db)
    # Use the month after latest_tx as projection_start.
    if latest.month == 12:
        projection_start = f"{latest.year + 1}-01"
    else:
        projection_start = f"{latest.year}-{latest.month + 1:02d}"

    inputs = await build_projection_inputs(
        qbo_db, app_db,
        starting_cash=0.0,
        projection_start_month=projection_start,
        horizon_weeks=13,
    )

    # Structural assertions
    assert isinstance(inputs, ProjectionInputs)
    assert inputs.starting_cash == 0.0
    assert inputs.projection_start_month == projection_start
    assert inputs.horizon_weeks == 13
    assert inputs.basis == "accrual"

    # History fields have length exactly 3 (Pydantic-enforced)
    assert len(inputs.ps_revenue_last_3_months) == 3
    assert len(inputs.fs_revenue_last_3_months) == 3
    assert len(inputs.ps_gross_profit_last_3_months) == 3
    assert len(inputs.fs_gross_profit_last_3_months) == 3

    # All history values are numeric
    for v in inputs.ps_revenue_last_3_months:
        assert isinstance(v, (int, float))

    # MRR keys are well-formed YYYY-MM (or empty dict)
    for k in inputs.mrr_last_year_same_months:
        assert len(k) == 7 and k[4] == "-", f"Bad MRR key: {k!r}"

    # Wholesale stays empty (Asad's rule)
    assert inputs.wholesale_invoices == []

    # Account groupings is a list
    assert isinstance(inputs.account_groupings, list)
