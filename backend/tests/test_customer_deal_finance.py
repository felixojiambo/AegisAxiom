"""Tests for the customer/deal finance analysis tool.

Covers:
  - customer revenue ranking from ar_invoices
  - deal revenue + cost summary (margin)
  - missing-mapping cases for both AR and AP source collections
  - join-key reliability: cancelled invoices drop out, contracts without
    deal_ids bucket as unattributed, contract→account_id is honored
  - permission/scoping: the conductor's nova.use gate is the boundary;
    the dispatcher itself never accepts a user-supplied tenant override

The dispatcher branch is exercised end-to-end so test coverage matches
how Nova actually calls the tool. Source collections are mocked at the
Motor `find()` level - lower than the helper would be more brittle and
miss the join logic the helper performs.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai.agents.tools.nova_tools import (
    NOVA_TOOL_SCHEMAS,
    _ScenarioState,
    execute_tool,
)


# ---------------------------------------------------------------------------
# Mongo mock: db.<collection>.find(query, proj).to_list(N) -> list[dict]
# ---------------------------------------------------------------------------


def _make_app_db(*, ar_invoices=None, time_entries=None, contracts=None):
    """Wire a MagicMock app db with three collections the helper reads.

    Each collection's find() returns a cursor whose to_list() resolves to
    the supplied list. The helper inspects time_entries' contract_ids and
    runs a follow-up contracts.find({"contract_id": {"$in": ...}}, ...);
    we honor that filter so test fixtures don't have to mirror the full
    collection.
    """
    ar_invoices = ar_invoices or []
    time_entries = time_entries or []
    contracts = contracts or []

    def _flat_cursor(rows):
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=list(rows))
        return cursor

    db = MagicMock()
    db.ar_invoices.find = MagicMock(return_value=_flat_cursor(ar_invoices))

    # time_entries.find honors the status="approved" + date filter so we
    # can verify the dispatcher passes them through.
    captured_time_filters = []

    def _time_find(query, projection):
        captured_time_filters.append(query)
        rows = [e for e in time_entries if (e.get("status") or "").lower()
                == (query.get("status") or "approved").lower()]
        # Apply optional date range
        date_clause = query.get("date") or {}
        if "$gte" in date_clause:
            rows = [r for r in rows if r.get("date", "") >= date_clause["$gte"]]
        if "$lte" in date_clause:
            rows = [r for r in rows if r.get("date", "") <= date_clause["$lte"]]
        return _flat_cursor(rows)

    db.time_entries.find = MagicMock(side_effect=_time_find)

    def _contracts_find(query, projection):
        ids = (query.get("contract_id") or {}).get("$in", [])
        rows = [c for c in contracts if c.get("contract_id") in ids]
        return _flat_cursor(rows)

    db.contracts.find = MagicMock(side_effect=_contracts_find)
    return db, captured_time_filters


def _patch_app_db(db):
    """The helper resolves the app db via two import paths depending on
    whether ``db`` was passed in or fetched from the global helper. Patch
    the canonical seam so direct + dispatcher entrypoints both see the
    mock."""
    return patch(
        "services.nova_engine.customer_deal_finance.get_app_db",
        return_value=db,
    )


# ---------------------------------------------------------------------------
# Schema-shape sanity
# ---------------------------------------------------------------------------


def test_customer_deal_finance_schema_has_required_dimensions_and_metrics():
    schema = next(
        t for t in NOVA_TOOL_SCHEMAS
        if t["name"] == "analyze_customer_deal_finance"
    )
    props = schema["parameters"]["properties"]
    assert set(props["dimension"]["enum"]) == {"customer", "deal"}
    assert set(props["metric"]["enum"]) == {"revenue", "cost", "margin"}
    assert schema["parameters"]["required"] == ["dimension", "metric"]


# ---------------------------------------------------------------------------
# Customer revenue ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_revenue_ranking_sums_by_account_and_sorts_desc():
    """Wayne's $47,500 invoice + LexCorp's $24,000 invoice => Wayne first.
    Cancelled invoices drop out so the ranking is what was actually billed."""
    db, _ = _make_app_db(
        ar_invoices=[
            {"invoice_id": "i1", "account_id": "acc_wayne",
             "account_name": "Wayne Enterprises", "deal_id": "d1",
             "total": 47500, "status": "sent",
             "period_start": "2026-04-01"},
            {"invoice_id": "i2", "account_id": "acc_lex",
             "account_name": "LexCorp", "deal_id": "d2",
             "total": 24000, "status": "draft",
             "period_start": "2026-04-15"},
            # Cancelled - must be excluded.
            {"invoice_id": "i3", "account_id": "acc_wayne",
             "account_name": "Wayne Enterprises", "deal_id": "d1",
             "total": 99999, "status": "cancelled",
             "period_start": "2026-04-01"},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "customer", "metric": "revenue"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["dimension"] == "customer"
    assert result["metric"] == "revenue"
    assert len(result["rows"]) == 2
    assert result["rows"][0]["key"] == "acc_wayne"
    assert result["rows"][0]["label"] == "Wayne Enterprises"
    assert result["rows"][0]["revenue"] == 47500.0
    assert result["rows"][1]["key"] == "acc_lex"
    assert result["rows"][1]["revenue"] == 24000.0


@pytest.mark.asyncio
async def test_customer_revenue_respects_date_window():
    db, _ = _make_app_db(
        ar_invoices=[
            {"invoice_id": "in_window", "account_id": "acc_a",
             "total": 1000, "status": "sent", "period_start": "2026-04-15"},
            {"invoice_id": "out_window", "account_id": "acc_a",
             "total": 9999, "status": "sent", "period_start": "2026-01-01"},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {
                "dimension": "customer", "metric": "revenue",
                "date_from": "2026-04-01", "date_to": "2026-04-30",
            },
            state, "user_x", None, None,
        )

    assert result["rows"][0]["revenue"] == 1000.0
    assert result["filters"]["date_from"] == "2026-04-01"
    assert result["filters"]["date_to"] == "2026-04-30"


# ---------------------------------------------------------------------------
# Deal revenue / cost summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deal_margin_summary_combines_ar_revenue_and_time_entry_cost():
    """Deal d1 has $47,500 AR revenue and 11 hours x $50 cost_rate = $550
    AP cost. Margin should be revenue - cost = 46950."""
    db, _ = _make_app_db(
        ar_invoices=[
            {"invoice_id": "i1", "account_id": "acc_wayne",
             "account_name": "Wayne", "deal_id": "d1", "deal_name": "Batcave",
             "total": 47500, "status": "sent", "period_start": "2026-04-01"},
        ],
        time_entries=[
            {"time_entry_id": "te1", "user_id": "u1",
             "contract_id": "c1", "duration": 660,  # 11 hours
             "resource_type_id": "rt1", "status": "approved",
             "date": "2026-04-10"},
        ],
        contracts=[
            {"contract_id": "c1", "account_id": "acc_wayne",
             "account_name": "Wayne", "deal_ids": ["d1"],
             "resources": [
                 {"user_id": "u1", "resource_type_id": "rt1",
                  "cost_rate": 50.0},
             ]},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "deal", "metric": "margin"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    main_rows = [r for r in result["rows"] if not r.get("is_unattributed")]
    assert len(main_rows) == 1
    row = main_rows[0]
    assert row["key"] == "d1"
    assert row["revenue"] == 47500.0
    assert row["cost"] == 550.0
    assert row["margin"] == 46950.0


@pytest.mark.asyncio
async def test_deal_cost_split_evenly_when_contract_serves_multiple_deals():
    """A contract that lists deal_ids=[d1, d2] splits each entry's cost
    50/50 across the two deals. Single-deal attribution is a heuristic
    so the response notes flag it."""
    db, _ = _make_app_db(
        time_entries=[
            {"time_entry_id": "te1", "user_id": "u1", "contract_id": "c_multi",
             "duration": 600, "resource_type_id": "rt1",
             "status": "approved", "date": "2026-04-10"},
        ],
        contracts=[
            {"contract_id": "c_multi", "account_id": "acc_z",
             "deal_ids": ["d1", "d2"],
             "resources": [
                 {"user_id": "u1", "resource_type_id": "rt1",
                  "cost_rate": 60.0},
             ]},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "deal", "metric": "cost"},
            state, "user_x", None, None,
        )

    # 10 hours x $60 = $600 split 50/50 across d1 + d2.
    by_key = {r["key"]: r for r in result["rows"]}
    assert by_key["d1"]["cost"] == 300.0
    assert by_key["d2"]["cost"] == 300.0
    # The note about even-split must be present so the LLM doesn't
    # narrate either number as authoritative single-deal cost.
    notes_blob = " ".join(result.get("notes", []))
    assert "split evenly" in notes_blob


# ---------------------------------------------------------------------------
# Missing mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_ar_data_returns_unavailable_mapping():
    """No ar_invoices => can't rank revenue. Nova redirects to the AR
    dashboard rather than ranking an empty list."""
    db, _ = _make_app_db(ar_invoices=[])
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "customer", "metric": "revenue"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unavailable_mapping"
    assert result.get("metric") == "revenue"
    assert result.get("dimension") == "customer"
    assert "/api/finance/accounts-receivable" in result.get("hint", "")


@pytest.mark.asyncio
async def test_missing_time_entries_returns_unavailable_mapping_for_cost():
    """No approved time entries => cost cannot be computed."""
    db, _ = _make_app_db(time_entries=[])
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "deal", "metric": "cost"},
            state, "user_x", None, None,
        )

    assert result.get("error") == "unavailable_mapping"
    assert "/api/finance/accounts-payable" in result.get("hint", "")


@pytest.mark.asyncio
async def test_deal_revenue_buckets_invoices_without_deal_id_as_unattributed():
    """An invoice with deal_id=None must NOT silently disappear; it lands
    in the unattributed bucket and the notes call out how many."""
    db, _ = _make_app_db(
        ar_invoices=[
            {"invoice_id": "i1", "account_id": "acc_a", "deal_id": "d1",
             "total": 1000, "status": "sent", "period_start": "2026-04-01"},
            {"invoice_id": "i2", "account_id": "acc_a", "deal_id": None,
             "total": 500, "status": "sent", "period_start": "2026-04-01"},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "deal", "metric": "revenue"},
            state, "user_x", None, None,
        )

    unattrib = [r for r in result["rows"] if r.get("is_unattributed")]
    assert len(unattrib) == 1
    assert unattrib[0]["revenue"] == 500.0
    assert result["unattributed"]["revenue"] == 500.0
    notes_blob = " ".join(result.get("notes", []))
    assert "no deal_id" in notes_blob


@pytest.mark.asyncio
async def test_deal_cost_flags_contracts_without_deal_ids():
    """Cost on a contract with no deal_ids is bucketed as unattributed
    so the user sees how much exposure isn't deal-mapped."""
    db, _ = _make_app_db(
        time_entries=[
            {"time_entry_id": "te1", "user_id": "u1", "contract_id": "c_no_deals",
             "duration": 60, "resource_type_id": "rt1",
             "status": "approved", "date": "2026-04-10"},
        ],
        contracts=[
            {"contract_id": "c_no_deals", "account_id": "acc_a",
             "deal_ids": [],
             "resources": [
                 {"user_id": "u1", "resource_type_id": "rt1",
                  "cost_rate": 100.0},
             ]},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "deal", "metric": "cost"},
            state, "user_x", None, None,
        )

    unattrib = [r for r in result["rows"] if r.get("is_unattributed")]
    assert len(unattrib) == 1
    assert unattrib[0]["cost"] == 100.0
    notes_blob = " ".join(result.get("notes", []))
    assert "no deal_ids" in notes_blob


# ---------------------------------------------------------------------------
# Validation / scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_dimension_returns_structured_error():
    state = _ScenarioState()
    db, _ = _make_app_db()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "vendor", "metric": "revenue"},
            state, "user_x", None, None,
        )
    assert result.get("error") == "invalid_dimension"
    assert "customer" in result.get("valid", [])


@pytest.mark.asyncio
async def test_invalid_metric_returns_structured_error():
    state = _ScenarioState()
    db, _ = _make_app_db()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "customer", "metric": "DSO"},
            state, "user_x", None, None,
        )
    assert result.get("error") == "invalid_metric"
    assert set(result["valid"]) == {"revenue", "cost", "margin"}


@pytest.mark.asyncio
async def test_invalid_date_from_returns_structured_error():
    state = _ScenarioState()
    db, _ = _make_app_db(ar_invoices=[
        {"invoice_id": "i1", "account_id": "acc_a", "total": 1,
         "status": "sent", "period_start": "2026-04-01"},
    ])
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "customer", "metric": "revenue",
             "date_from": "2026/04/01"},
            state, "user_x", None, None,
        )
    assert result.get("error") == "invalid_date_from"


@pytest.mark.asyncio
async def test_dispatcher_does_not_accept_tenant_overrides_in_args():
    """The chat surface enforces user_id via nova.use at the conductor.
    The dispatcher must NOT accept a tenant_id / account_id from the
    LLM-supplied tool_args - extra keys are simply ignored, and the
    aggregation runs against the platform-wide collections without
    branching on caller-supplied scope. This locks in that behavior."""
    state = _ScenarioState()
    db, _ = _make_app_db(ar_invoices=[
        {"invoice_id": "i1", "account_id": "acc_a", "deal_id": "d1",
         "total": 100.0, "status": "sent", "period_start": "2026-04-01"},
    ])
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {
                "dimension": "customer", "metric": "revenue",
                # Bogus scoping fields - must be ignored, not honored.
                "tenant_id": "other-tenant",
                "account_id": "acc_should_be_ignored",
            },
            state, "user_x", None, None,
        )
    assert "error" not in result
    # Aggregation ran against the only invoice in the fixture.
    assert result["rows"][0]["key"] == "acc_a"


@pytest.mark.asyncio
async def test_limit_is_clamped_and_applied_to_main_rows():
    """limit caps the main ranking, but the unattributed row is appended
    after the limit if non-zero so the user always sees off-dimension
    spend."""
    invoices = []
    for i in range(15):
        invoices.append({
            "invoice_id": f"i{i}", "account_id": f"acc_{i}",
            "total": 100 + i, "status": "sent",
            "period_start": "2026-04-01",
        })
    invoices.append({
        "invoice_id": "i_unattr", "account_id": None,
        "total": 9999, "status": "sent", "period_start": "2026-04-01",
    })

    db, _ = _make_app_db(ar_invoices=invoices)
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "customer", "metric": "revenue", "limit": 3},
            state, "user_x", None, None,
        )

    main = [r for r in result["rows"] if not r.get("is_unattributed")]
    unattrib = [r for r in result["rows"] if r.get("is_unattributed")]
    assert len(main) == 3
    assert len(unattrib) == 1
    assert unattrib[0]["revenue"] == 9999.0


@pytest.mark.asyncio
async def test_truncation_note_reports_pre_cap_total_not_post_slice():
    """Regression: the truncation note used to read len(main_rows) AFTER
    the limit slice, which always rendered 'Showing top N of N'. Pre-PR
    review fix: capture the pre-cap count and quote that instead so the
    user sees how many were dropped."""
    invoices = [
        {"invoice_id": f"i{i}", "account_id": f"acc_{i}",
         "total": 100 + i, "status": "sent", "period_start": "2026-04-01"}
        for i in range(15)
    ]
    db, _ = _make_app_db(ar_invoices=invoices)
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "customer", "metric": "revenue", "limit": 3},
            state, "user_x", None, None,
        )
    notes_blob = " ".join(result.get("notes", []))
    assert "3 of 15 returned" in notes_blob


@pytest.mark.asyncio
async def test_unattributed_row_is_last_after_sort():
    """The inline lambda must keep the unattributed bucket at the END,
    not the front. Lock in the ordering so a future refactor that
    swaps the boolean direction is caught."""
    db, _ = _make_app_db(
        ar_invoices=[
            {"invoice_id": "small", "account_id": "acc_small",
             "total": 50, "status": "sent", "period_start": "2026-04-01"},
            {"invoice_id": "huge_unattr", "account_id": None,
             "total": 100_000, "status": "sent",
             "period_start": "2026-04-01"},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {"dimension": "customer", "metric": "revenue"},
            state, "user_x", None, None,
        )
    rows = result["rows"]
    # acc_small (real customer) must come first even though its
    # revenue is tiny next to the unattributed bucket.
    assert rows[0]["key"] == "acc_small"
    assert rows[-1].get("is_unattributed") is True


@pytest.mark.asyncio
async def test_unscoped_invoice_count_singular_form_for_one_record():
    """One date-less invoice + a date window => the note pluralises
    correctly ('1 AR invoice had no date field', not the previous
    grammar-broken '1 AR invoices had no date field')."""
    db, _ = _make_app_db(
        ar_invoices=[
            {"invoice_id": "with_date", "account_id": "acc_a",
             "total": 100, "status": "sent", "period_start": "2026-04-15"},
            {"invoice_id": "no_date", "account_id": "acc_a",
             "total": 50, "status": "sent"},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {
                "dimension": "customer", "metric": "revenue",
                "date_from": "2026-04-01", "date_to": "2026-04-30",
            },
            state, "user_x", None, None,
        )
    rev_meta = result["source_metadata"]["revenue"]
    assert rev_meta["unscoped_invoice_count"] == 1
    assert rev_meta["invoice_count_considered"] == 2
    notes_blob = " ".join(result.get("notes", []))
    assert "1 AR invoice had no date field" in notes_blob
    # Must not render the broken plural form.
    assert "1 AR invoices had no date field" not in notes_blob


@pytest.mark.asyncio
async def test_unscoped_invoice_count_plural_form_for_two_or_more_records():
    """Two-plus date-less invoices retain the plural noun. Locks both
    branches of the singular/plural switch."""
    db, _ = _make_app_db(
        ar_invoices=[
            {"invoice_id": "with_date", "account_id": "acc_a",
             "total": 100, "status": "sent", "period_start": "2026-04-15"},
            {"invoice_id": "no_date_1", "account_id": "acc_a",
             "total": 50, "status": "sent"},
            {"invoice_id": "no_date_2", "account_id": "acc_a",
             "total": 75, "status": "sent"},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            {
                "dimension": "customer", "metric": "revenue",
                "date_from": "2026-04-01", "date_to": "2026-04-30",
            },
            state, "user_x", None, None,
        )
    rev_meta = result["source_metadata"]["revenue"]
    assert rev_meta["unscoped_invoice_count"] == 2
    notes_blob = " ".join(result.get("notes", []))
    assert "2 AR invoices had no date field" in notes_blob


@pytest.mark.asyncio
async def test_unscoped_count_zero_when_no_date_filter_applied():
    """With no date window, every record's date is moot - the
    'included regardless of date_from / date_to' framing is vacuously
    true. Suppress both the count and the note in that case."""
    db, _ = _make_app_db(
        ar_invoices=[
            {"invoice_id": "with_date", "account_id": "acc_a",
             "total": 100, "status": "sent", "period_start": "2026-04-15"},
            {"invoice_id": "no_date_1", "account_id": "acc_a",
             "total": 50, "status": "sent"},
            {"invoice_id": "no_date_2", "account_id": "acc_a",
             "total": 75, "status": "sent"},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_customer_deal_finance",
            # No date_from / date_to.
            {"dimension": "customer", "metric": "revenue"},
            state, "user_x", None, None,
        )
    rev_meta = result["source_metadata"]["revenue"]
    assert rev_meta["unscoped_invoice_count"] == 0
    notes_blob = " ".join(result.get("notes", []))
    assert "had no date field" not in notes_blob
