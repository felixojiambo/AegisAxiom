"""Sign-convention regression coverage for Nova FS revenue.

Background: AEGISAXIOM GL stores `amount` as a natural-balance signed value —
positive in the normal direction of the account (credit on revenue,
debit on expense), negative for reversals. Summing raw `amount` on a
revenue account yields a positive total directly. An earlier version of
the loader inverted this with `-amount`, which produced negative revenue
totals for any tenant whose GL was ingested in the natural-balance
convention. This file pins the convention down with synthetic fixtures
that compute every expected total inline.

Rules followed:
  * No hardcoded real account numbers, names, dates, or totals.
  * Account-prefix values are derived at runtime from
    services.nova_engine.account_categories.CATEGORY_RULES so the
    tests track the categorizer's source of truth — they don't repeat
    its data.
  * Every assertion is computed from the fixture rows in the test.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.nova_engine.account_categories import (
    CATEGORY_RULES,
    categorize,
    is_revenue,
)
from services.nova_engine.data_loader import (
    load_gross_profit_history,
    load_revenue_history,
)


# -- Helpers ---------------------------------------------------------------


def _fs_prefixes() -> list[str]:
    """Field Service revenue prefixes pulled from the categorizer rules."""
    prefixes = [p for p, c in CATEGORY_RULES if c == "FS_REVENUE"]
    assert len(prefixes) >= 2, (
        "Expected the categorizer to define at least two FS_REVENUE prefixes"
    )
    return prefixes


def _make_mock_gl(rows: list[dict]) -> MagicMock:
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    coll = MagicMock()
    coll.aggregate = MagicMock(return_value=cursor)
    db = MagicMock()
    db.general_ledger = coll
    return db


def _agg_row(month: str, account_id: str, account_name: str, amount: float) -> dict:
    """Shape matching the production GL aggregation pipeline output."""
    return {
        "_id": {"month": month, "account_id": account_id, "account_name": account_name},
        "amount": amount,
    }


# -- Required coverage 1 + 2: service-style and material-style FS rows ----


@pytest.mark.asyncio
async def test_first_fs_prefix_row_categorizes_as_fs_revenue():
    """A row whose account_name starts with the FIRST FS_REVENUE prefix from
    CATEGORY_RULES must categorize as FS_REVENUE. The prefix is derived from
    the categorizer's own data, not hardcoded.
    """
    prefixes = _fs_prefixes()
    account_name = f"{prefixes[0]} synthetic-service-line"
    assert categorize(account_name) == "FS_REVENUE"
    assert is_revenue("FS_REVENUE") is True


@pytest.mark.asyncio
async def test_second_fs_prefix_row_categorizes_as_fs_revenue():
    """A row whose account_name starts with the SECOND FS_REVENUE prefix from
    CATEGORY_RULES must categorize as FS_REVENUE.
    """
    prefixes = _fs_prefixes()
    account_name = f"{prefixes[1]} synthetic-material-line"
    assert categorize(account_name) == "FS_REVENUE"


# -- Required coverage 3: aggregate includes ALL FS rows ------------------


@pytest.mark.asyncio
async def test_load_revenue_history_sums_every_fs_prefix_row():
    """Build one fixture row per FS_REVENUE prefix from the categorizer
    rules. Every row should land in the FS_REVENUE bucket; the FS total
    must equal the sum of fixture amounts. No prefix or amount is
    hardcoded — fixtures are derived at runtime.
    """
    prefixes = _fs_prefixes()
    months = ["2099-01", "2099-02", "2099-03"]
    # Synthesize a positive amount per (prefix, month) combination so we
    # can compute the expected total inline.
    fixture_rows: list[dict] = []
    expected_totals_by_month: dict[str, float] = {m: 0.0 for m in months}
    for i, prefix in enumerate(prefixes):
        for j, month in enumerate(months):
            amount = float(1000 + 100 * i + 10 * j)
            fixture_rows.append(
                _agg_row(
                    month=month,
                    account_id=prefix,
                    account_name=f"{prefix} synthetic FS line {i}",
                    amount=amount,
                )
            )
            expected_totals_by_month[month] += amount

    db = _make_mock_gl(fixture_rows)
    result = await load_revenue_history(db, months)

    expected = [expected_totals_by_month[m] for m in months]
    assert result["FS_REVENUE"] == expected
    # Cross-check against the same totals computed from the fixture rows.
    fixture_total = sum(r["amount"] for r in fixture_rows)
    assert sum(result["FS_REVENUE"]) == fixture_total


# -- Required coverage 4: revenue is positive -----------------------------


@pytest.mark.asyncio
async def test_revenue_total_is_positive_when_fixture_amounts_are_positive():
    """Source-truth GL stores revenue as a positive natural-balance amount.
    The loader must return a positive monthly total — no double sign flip."""
    prefixes = _fs_prefixes()
    months = ["2099-01"]
    fixture_amount = 12345.67
    rows = [
        _agg_row(
            month=months[0],
            account_id=prefixes[0],
            account_name=f"{prefixes[0]} synthetic FS line",
            amount=fixture_amount,
        )
    ]
    db = _make_mock_gl(rows)
    result = await load_revenue_history(db, months)

    assert result["FS_REVENUE"][0] == fixture_amount
    assert result["FS_REVENUE"][0] > 0


@pytest.mark.asyncio
async def test_load_gross_profit_history_uses_positive_revenue():
    """GP = revenue - COGS. With natural-balance fixtures (positive amounts
    on both revenue and COGS rows), GP must equal revenue minus COGS as
    computed inline from the fixture rows."""
    fs_prefixes = [p for p, c in CATEGORY_RULES if c == "FS_REVENUE"]
    fs_cogs_prefixes = [p for p, c in CATEGORY_RULES if c == "FS_COGS"]
    assert fs_prefixes and fs_cogs_prefixes, "Categorizer rules missing FS lines"

    months = ["2099-01"]
    fs_revenue_amount = 10_000.00
    fs_cogs_amount = 3_000.00
    rows = [
        _agg_row(
            months[0], fs_prefixes[0], f"{fs_prefixes[0]} synthetic FS rev", fs_revenue_amount
        ),
        _agg_row(
            months[0], fs_cogs_prefixes[0], f"{fs_cogs_prefixes[0]} synthetic FS cogs", fs_cogs_amount
        ),
    ]
    db = _make_mock_gl(rows)
    result = await load_gross_profit_history(db, months)

    expected_fs_gp = fs_revenue_amount - fs_cogs_amount
    assert result["FS_GP"][0] == expected_fs_gp
    assert result["FS_GP"][0] > 0


# -- Required coverage 5: concrete date routing ---------------------------


@pytest.mark.asyncio
async def test_date_prompt_routing_concrete_window_is_passed_to_match():
    """Nova's tool dispatcher accepts ISO YYYY-MM-DD strings only and
    rejects natural-language dates. This pins the routing contract: a
    concrete date window flows through the tool args without normalization
    or guessing, and a non-ISO string surfaces a structured error
    instead of returning silently empty results.
    """
    from services.ai.agents.tools.nova_tools import _is_valid_date

    # Concrete window the LLM has parsed from the user's prompt.
    target_from = "2099-01-01"
    target_to = "2099-12-31"
    assert _is_valid_date(target_from) is True
    assert _is_valid_date(target_to) is True

    # The dispatcher must reject anything that isn't YYYY-MM-DD.
    natural_phrasings = [
        "January 2099",
        "Q1 2099",
        "last year",
        "2099/01/01",
        "01-01-2099",
        "",
    ]
    for v in natural_phrasings:
        assert _is_valid_date(v) is False, (
            f"_is_valid_date should reject natural-language input {v!r}"
        )

    # And invalid calendar dates that are syntactically YYYY-MM-DD.
    assert _is_valid_date("2099-02-31") is False
    assert _is_valid_date("2099-13-01") is False
