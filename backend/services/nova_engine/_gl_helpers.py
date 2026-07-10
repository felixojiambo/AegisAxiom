"""Shared general-ledger aggregation helpers.

Extracted from data_loader.py during PR #144 review to break the
``wholesale_detection -> data_loader`` import cycle. Both modules need
the same Mongo pipeline shape and the same row-cap constant; importing
them from a third, dependency-free module removes the cycle entirely.

Symbols start with an underscore by convention - they're private to the
nova_engine package, not part of the public engine API. data_loader
re-exports them for backwards compatibility with existing tests.
"""
from __future__ import annotations

from typing import Any


# Hard cap on GL aggregation result size. Result rows scale with
# CoA-width x history-months. SimpliMeta's CoA is ~100 accounts x
# 3 months = 300 rows max in practice, so 5000 is comfortable
# headroom. Raise if a deployment hits the cap (logged warning surfaces
# this).
_GL_QUERY_LIMIT = 5000


def _safe_float(value: Any) -> float:
    """Best-effort float coercion that returns 0.0 for None or
    unparseable values. Centralised here so finance_snapshots.py,
    customer_deal_finance.py, and fs_job_margin.py share one
    implementation - PR #144 second-pass review item 9 dedupe."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _start_of_next_month(month: str) -> str:
    """Given 'YYYY-MM', return 'YYYY-MM-01' for the FOLLOWING month."""
    year_str, month_str = month.split("-")
    year = int(year_str)
    mo = int(month_str)
    if mo == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{mo + 1:02d}-01"


def _build_history_pipeline(
    history_months: list[str],
    basis: str,
    account_id_prefix: str | None = None,
) -> list[dict]:
    """Shared Mongo pipeline for revenue, GP, and wholesale history.

    Filters by date range + basis. Groups by (month, account_id,
    account_name). Returns rows with structure:
        {"_id": {"month": "YYYY-MM", "account_id": str, "account_name": str},
         "amount": float}

    When ``account_id_prefix`` is supplied, the $match stage constrains
    rows to that prefix using an $or across BOTH ``account_id`` AND
    ``account_name`` — many tenants do not carry an ``account_id``
    field at all (the live AEGISAXIOM general_ledger schema is one such),
    so an account_id-only filter would silently return zero rows even
    when the matching account exists on ``account_name``. Callers
    using this to scope to a single category (e.g. load_mrr_history
    filtering to "50300") keep the Python-side categorize() belt
    as the authoritative cross-validator on the rows that come
    back. The prefix string itself comes from
    ``account_categories.CATEGORY_RULES`` — that file is the source
    of truth for category-to-prefix mapping; if the rule shape ever
    shifts (e.g. to a list of account IDs), this parameter shape
    needs to follow.

    The account_name regex matches either a bare prefix at the start
    (``"50300 Other Income:Contract Commission Income"``) or the
    same prefix followed by EOL — both shapes appear in QBO syncs.
    Rows with the prefix embedded mid-name (``"Other Income:50300
    ..."``) are NOT matched server-side; the categorize() belt would
    pick them up if they reached the Python loop, but the server-side
    scope keeps non-prefix-bearing rows out of the GL_QUERY_LIMIT
    budget. If a tenant emerges with embedded-prefix names, broaden
    this regex.

    Date types: this pipeline compares tx_date against YYYY-MM-DD
    strings via $gte/$lt. This works because AEGISAXIOM stores tx_date as
    ISO 8601 strings, NOT as BSON Date / ISODate. If the storage type
    ever migrates to ISODate, these comparisons will silently return
    zero rows. data_loader.build_projection_inputs runs a runtime
    sanity check on the actual tx_date type and logs a warning if it's
    not a string.
    """
    earliest = history_months[0]
    latest = history_months[-1]
    start_date = f"{earliest}-01"
    end_date = _start_of_next_month(latest)

    match_stage: dict[str, Any] = {
        "tx_date": {"$gte": start_date, "$lt": end_date},
        "basis": basis,
    }
    if account_id_prefix is not None:
        # Anchored prefix match. account_name is checked with a
        # trailing space or EOL so "50300X" account names don't
        # collide with the "50300" prefix. The categorize() belt in
        # the caller's row loop is the authoritative validator.
        prefix_re = f"^{account_id_prefix}"
        name_re = f"^{account_id_prefix}(\\s|$)"
        match_stage["$or"] = [
            {"account_id": {"$regex": prefix_re}},
            {"account_name": {"$regex": name_re}},
        ]

    return [
        {"$match": match_stage},
        {
            "$project": {
                "month": {"$substr": ["$tx_date", 0, 7]},
                "account_id": 1,
                "account_name": 1,
                "amount": 1,
            }
        },
        {
            "$group": {
                "_id": {
                    "month": "$month",
                    "account_id": "$account_id",
                    "account_name": "$account_name",
                },
                "amount": {"$sum": "$amount"},
            }
        },
    ]


__all__ = [
    "_GL_QUERY_LIMIT",
    "_build_history_pipeline",
    "_safe_float",
    "_start_of_next_month",
]
