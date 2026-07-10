"""Customer / deal finance aggregation for the Nova chat agent.

Nova needs to answer "which customers drive the most revenue?", "which
deals are biggest?", and "what deals carry the largest cost exposure?".
The cash-flow projection engine cannot answer these because it rolls up
GL revenue without per-customer split - QBO general_ledger does not carry
account_id (AEGISAXIOM customer) or deal_id on the row.

Reliable join keys that DO exist in the AEGISAXIOM app DB:
  - finance_drafts (kind=ar_invoice): counterparty_id (maps to account_id /
    customer), source_contract_id (optional deal link), amount (total).
    Direct keys, no fuzzy matching. Replaces the legacy ar_invoices reads.
  - contracts: account_id (customer), deal_ids[] (a list of deal IDs the
    contract serves), resources[] with cost_rate. Collection name is
    unchanged after Thread 2G's operations spinout — the route file moved,
    not the collection.
  - time_entries: contract_id, status, duration (minutes), resource_type_id.
    The same path /api/finance/accounts-payable already uses to compute
    AP from approved hours.

Revenue path: ``finance_drafts(kind=ar_invoice).amount`` grouped by
``counterparty_id`` (customer dimension) or ``source_contract_id`` /
deal_id (deal dimension). Failed drafts drop out. A draft with no
counterparty_id is bucketed as ``unattributed`` so the caller can see
how much of the AR book lacks deal-level mapping rather than silently
disappearing.

Cost path: approved ``time_entries`` × resolved ``cost_rate`` from the
linked contract, attributed to ``contract.account_id`` (customer) or
expanded across ``contract.deal_ids[]`` (deal). When a contract has
multiple deals on it, the cost is split evenly across the deals - any
allocation is a heuristic, but even-split is at least documented and
auditable rather than picking the first deal silently.

The aggregation runs in Python after a couple of bounded find()s. It is
not a hot-path tool - chat usage is interactive, the data shape is wide
(many small contracts) rather than deep, and the join is too multi-hop
for a clean Mongo $lookup pipeline. If the AR/AP volumes outgrow the
cap (5k drafts / 10k time entries), the function logs a warning and
the caller gets a partial result rather than a wedge.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Literal

from motor.motor_asyncio import AsyncIOMotorDatabase

from ._gl_helpers import _safe_float
from .db import get_app_db


logger = logging.getLogger(__name__)


_AR_DRAFT_LIMIT = 5_000
_TIME_ENTRY_LIMIT = 10_000

# kind value for AR drafts in the finance_drafts collection.
_AR_DRAFT_KIND = "ar_invoice"

# sync_status values we count as real revenue. Failed drafts (sync errors,
# cancelled records) drop out so the customer ranking reflects what was
# actually billed or is legitimately owed. Draft + Sent + Synced are all
# in-scope: Synced means it reached QBO/was paid, Draft/Sent means it is
# an outstanding obligation — both represent real economic activity.
_AR_REVENUE_SYNC_STATUSES_EXCLUDE = {"Failed"}

# unattributed bucket label - keep stable so the LLM and tests can both
# reach it by string.
_UNATTRIBUTED_KEY = "__unattributed__"


Dimension = Literal["customer", "deal"]
Metric = Literal["revenue", "cost", "margin"]


def _within_window(
    record_date: str | None,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    """All-string YYYY-MM-DD comparison. Records without a usable date
    are always included - excluding them on a missing field would silently
    drop AR rows and surprise the user."""
    if record_date is None:
        return True
    if not isinstance(record_date, str):
        return True
    if date_from and record_date < date_from:
        return False
    if date_to and record_date > date_to:
        return False
    return True


def _ar_draft_date(draft: dict) -> str | None:
    """Pick the most user-meaningful date for filtering a finance_drafts record.

    source_period_start reflects the work window the draft covers (set on
    auto_time_entries drafts). Falls back to created_at for manually-created
    drafts that do not carry a period start date.
    """
    candidate = draft.get("source_period_start") or draft.get("due_date")
    if isinstance(candidate, str):
        return candidate
    created = draft.get("created_at")
    if isinstance(created, str):
        return created[:10]
    return None


def _time_entry_date(entry: dict) -> str | None:
    candidate = entry.get("date")
    return candidate if isinstance(candidate, str) else None


# ---------------------------------------------------------------------------
# Revenue: ar_invoices grouped by dimension
# ---------------------------------------------------------------------------


async def _aggregate_revenue(
    db: AsyncIOMotorDatabase,
    *,
    dimension: Dimension,
    date_from: str | None,
    date_to: str | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return (rows_by_key, meta).

    Source: finance_drafts filtered to kind=ar_invoice. Failed drafts are
    excluded (they represent sync errors / cancelled records and should not
    count toward customer revenue). Draft, Sent, and Synced are all included
    as they represent real economic activity.

    rows_by_key:
        key -> {label, revenue, invoice_count}
        key is counterparty_id (customer dim) or source_contract_id/deal_id
        (deal dim). The special _UNATTRIBUTED_KEY collects rows whose
        dimension key is missing.
    meta:
        {draft_count_total, draft_count_considered, unattributed_draft_count,
         source_available}
    """
    cursor = db.finance_drafts.find({"kind": _AR_DRAFT_KIND}, {"_id": 0})
    drafts = await cursor.to_list(length=_AR_DRAFT_LIMIT)
    if len(drafts) >= _AR_DRAFT_LIMIT:
        logger.warning(
            "analyze_customer_deal_finance finance_drafts(kind=ar_invoice) "
            "read hit cap of %d; result may be partial.",
            _AR_DRAFT_LIMIT,
        )

    rows: dict[str, dict[str, Any]] = {}
    considered = 0
    unattributed_count = 0
    unscoped_count = 0
    # Only count records that bypassed the window filter when there
    # actually is a window. With no bounds the "included regardless"
    # framing is vacuously true and the count is misleading.
    date_filter_active = date_from is not None or date_to is not None

    for draft in drafts:
        sync_status = draft.get("sync_status") or ""
        if sync_status in _AR_REVENUE_SYNC_STATUSES_EXCLUDE:
            continue
        record_date = _ar_draft_date(draft)
        if not _within_window(record_date, date_from, date_to):
            continue
        # A record with no usable date passes _within_window
        # unconditionally. Track those so the LLM can quote
        # "X invoices had no date and were included regardless"
        # instead of silently rolling them up - but only when the
        # caller actually asked for a date window.
        if record_date is None and date_filter_active:
            unscoped_count += 1
        considered += 1

        # finance_drafts uses `amount` (not `total` / `total_amount`).
        amount = _safe_float(draft.get("amount", 0))

        if dimension == "customer":
            # counterparty_id is the optional FK to a AEGISAXIOM account/customer.
            key = draft.get("counterparty_id") or _UNATTRIBUTED_KEY
            label = draft.get("counterparty") or (
                "Unattributed (no counterparty_id)" if key == _UNATTRIBUTED_KEY
                else key
            )
        else:  # deal
            # source_contract_id links auto-generated AR drafts to a contract
            # (and transitively to deal_ids). Use it as the deal-dimension key.
            key = draft.get("source_contract_id") or _UNATTRIBUTED_KEY
            label = (
                "Unattributed (no source_contract_id)" if key == _UNATTRIBUTED_KEY
                else key
            )
            if key == _UNATTRIBUTED_KEY:
                unattributed_count += 1

        bucket = rows.setdefault(key, {
            "key": key,
            "label": label,
            "revenue": 0.0,
            "invoice_count": 0,
        })
        bucket["revenue"] += amount
        bucket["invoice_count"] += 1
        # Preserve label preference: a non-id label wins over an id-only
        # label so customer/counterparty names surface in the response.
        if bucket["label"] == bucket["key"] and label != key:
            bucket["label"] = label

    meta = {
        "draft_count_total": len(drafts),
        "draft_count_considered": considered,
        "unattributed_draft_count": unattributed_count,
        "unscoped_draft_count": unscoped_count,
        "source_available": len(drafts) > 0,
    }
    return rows, meta


# ---------------------------------------------------------------------------
# Cost: time_entries × cost_rate joined through contracts
# ---------------------------------------------------------------------------


def _resolve_cost_rate(contract: dict, entry: dict) -> float:
    """Same fallback ladder finance.py:get_accounts_payable uses.

    Match a (user_id, resource_type_id) pair on the contract first; fall
    back to the first user_id match if the resource_type_id mismatches
    (legacy rows). Returns 0.0 when no rate is found - the entry is
    still counted in the pass-through metadata, just contributes $0
    cost so the caller can see "Y entries had no cost_rate".
    """
    user_id = entry.get("user_id")
    if user_id is None:
        return 0.0
    entry_resource_type_id = entry.get("resource_type_id")
    resources = contract.get("resources", []) or []
    for resource in resources:
        if (
            resource.get("user_id") == user_id
            and resource.get("resource_type_id") == entry_resource_type_id
        ):
            return _safe_float(resource.get("cost_rate", 0))
    for resource in resources:
        if resource.get("user_id") == user_id:
            return _safe_float(resource.get("cost_rate", 0))
    return 0.0


async def _aggregate_cost(
    db: AsyncIOMotorDatabase,
    *,
    dimension: Dimension,
    date_from: str | None,
    date_to: str | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return (rows_by_key, meta) for cost by customer/deal.

    Customer dim: contract.account_id is the key (1:1).
    Deal dim:     contract.deal_ids[] explodes - cost is split evenly
                  across all deal ids on the contract. When deal_ids is
                  empty, the cost lands in _UNATTRIBUTED_KEY.
    """
    time_filter: dict[str, Any] = {"status": "approved"}
    if date_from or date_to:
        date_clause: dict[str, Any] = {}
        if date_from:
            date_clause["$gte"] = date_from
        if date_to:
            date_clause["$lte"] = date_to
        time_filter["date"] = date_clause

    entries = await db.time_entries.find(
        time_filter, {"_id": 0}
    ).to_list(length=_TIME_ENTRY_LIMIT)
    if len(entries) >= _TIME_ENTRY_LIMIT:
        logger.warning(
            "analyze_customer_deal_finance time_entries read hit cap of %d; "
            "result may be partial.",
            _TIME_ENTRY_LIMIT,
        )

    contract_ids = list({
        e.get("contract_id") for e in entries if e.get("contract_id")
    })
    contracts: dict[str, dict] = {}
    if contract_ids:
        contract_docs = await db.contracts.find(
            {"contract_id": {"$in": contract_ids}}, {"_id": 0}
        ).to_list(length=len(contract_ids))
        contracts = {c["contract_id"]: c for c in contract_docs}

    rows: dict[str, dict[str, Any]] = {}
    considered = 0
    no_contract = 0
    no_deal_mapping = 0

    for entry in entries:
        contract_id = entry.get("contract_id")
        if not contract_id:
            no_contract += 1
            continue
        contract = contracts.get(contract_id)
        if not contract:
            no_contract += 1
            continue
        considered += 1

        duration_hours = _safe_float(entry.get("duration", 0)) / 60.0
        cost_rate = _resolve_cost_rate(contract, entry)
        cost_amount = duration_hours * cost_rate

        if dimension == "customer":
            key = contract.get("account_id") or _UNATTRIBUTED_KEY
            label = contract.get("account_name") or (
                "Unattributed (contract has no account_id)"
                if key == _UNATTRIBUTED_KEY else key
            )
            bucket = rows.setdefault(key, {
                "key": key, "label": label, "cost": 0.0, "hours": 0.0,
                "entry_count": 0,
            })
            bucket["cost"] += cost_amount
            bucket["hours"] += duration_hours
            bucket["entry_count"] += 1
            if bucket["label"] == bucket["key"] and label != key:
                bucket["label"] = label
        else:  # deal
            deal_ids = [d for d in (contract.get("deal_ids") or []) if d]
            if not deal_ids:
                no_deal_mapping += 1
                bucket = rows.setdefault(_UNATTRIBUTED_KEY, {
                    "key": _UNATTRIBUTED_KEY,
                    "label": "Unattributed (contract has no deal_ids)",
                    "cost": 0.0, "hours": 0.0, "entry_count": 0,
                })
                bucket["cost"] += cost_amount
                bucket["hours"] += duration_hours
                bucket["entry_count"] += 1
                continue
            # Even-split allocation across the deals on this contract.
            split = 1.0 / len(deal_ids)
            for did in deal_ids:
                bucket = rows.setdefault(did, {
                    "key": did,
                    "label": did,
                    "cost": 0.0, "hours": 0.0, "entry_count": 0,
                })
                bucket["cost"] += cost_amount * split
                bucket["hours"] += duration_hours * split
                # Count an entry as "1" against each deal it touches; the
                # row count will exceed considered, surfaced via
                # meta.deal_split_active so the LLM does not narrate the
                # number as unique entries.
                bucket["entry_count"] += 1

    meta = {
        "time_entries_total": len(entries),
        "time_entries_considered": considered,
        "time_entries_without_contract": no_contract,
        "time_entries_without_deal_mapping": no_deal_mapping,
        "deal_split_active": dimension == "deal",
        "source_available": len(entries) > 0,
    }
    return rows, meta


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def aggregate_customer_deal_finance(
    *,
    dimension: Dimension,
    metric: Metric,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any]:
    """Rank customers or deals by AR-invoiced revenue, contracted cost,
    or margin.

    Returns a dict with:
        dimension, metric, filters, rows, row_count, unattributed,
        source_metadata, notes (string, optional).

    When neither source collection has any data for the requested
    metric, returns an ``unavailable_mapping`` error. The caller
    (nova_tools.execute_tool) surfaces this verbatim so Nova can
    redirect to the AR/AP dashboards.
    """
    if db is None:
        db = get_app_db()

    revenue_rows: dict[str, dict[str, Any]] = {}
    cost_rows: dict[str, dict[str, Any]] = {}
    revenue_meta: dict[str, Any] = {}
    cost_meta: dict[str, Any] = {}

    if metric in ("revenue", "margin"):
        revenue_rows, revenue_meta = await _aggregate_revenue(
            db,
            dimension=dimension,
            date_from=date_from,
            date_to=date_to,
        )
    if metric in ("cost", "margin"):
        cost_rows, cost_meta = await _aggregate_cost(
            db,
            dimension=dimension,
            date_from=date_from,
            date_to=date_to,
        )

    # If the caller's chosen metric has no source data, that's an
    # unavailable_mapping (vs. an empty result with valid zeros). Nova
    # must redirect to the dashboard rather than rank an empty list.
    if metric == "revenue" and not revenue_meta.get("source_available"):
        return _unavailable(metric, "finance_drafts(ar_invoice)", dimension)
    if metric == "cost" and not cost_meta.get("source_available"):
        return _unavailable(metric, "time_entries", dimension)
    if metric == "margin" and not (
        revenue_meta.get("source_available") or cost_meta.get("source_available")
    ):
        return _unavailable(metric, "finance_drafts+time_entries", dimension)

    keys = set(revenue_rows.keys()) | set(cost_rows.keys())
    rows: list[dict[str, Any]] = []
    unattributed: dict[str, float] = {"revenue": 0.0, "cost": 0.0}

    for key in keys:
        rev_bucket = revenue_rows.get(key) or {}
        cost_bucket = cost_rows.get(key) or {}
        # Prefer label from whichever side actually has it.
        label = (
            rev_bucket.get("label")
            or cost_bucket.get("label")
            or key
        )
        revenue = _safe_float(rev_bucket.get("revenue", 0.0))
        cost = _safe_float(cost_bucket.get("cost", 0.0))
        margin = revenue - cost
        row = {
            "key": key,
            "label": label,
            "revenue": round(revenue, 2),
            "cost": round(cost, 2),
            "margin": round(margin, 2),
            "invoice_count": int(rev_bucket.get("invoice_count", 0) or 0),
            "time_entry_count": int(cost_bucket.get("entry_count", 0) or 0),
            "billable_hours": round(_safe_float(cost_bucket.get("hours", 0.0)), 2),
        }
        if key == _UNATTRIBUTED_KEY:
            unattributed["revenue"] = row["revenue"]
            unattributed["cost"] = row["cost"]
            row["is_unattributed"] = True
        rows.append(row)

    # Sort by the requested metric, descending. Unattributed rows are
    # pushed to the end regardless of size so the user-meaningful ranking
    # is not crowded out by a single bucket. metric_key matches a row
    # field directly (revenue / cost / margin).
    metric_key = metric

    rows.sort(key=lambda r: (
        # is_unattributed last (False sorts before True), then by metric
        # desc, then label asc as a stable tiebreaker.
        r.get("is_unattributed", False),
        -_safe_float(r.get(metric_key, 0)),
        r.get("label") or "",
    ))

    # Apply the limit AFTER sorting; keep unattributed visible if non-zero
    # so the user always sees how much sits outside the dimension.
    main_rows = [r for r in rows if not r.get("is_unattributed")]
    unattrib_rows = [r for r in rows if r.get("is_unattributed")]
    pre_cap_main_count = len(main_rows)
    truncated = pre_cap_main_count > limit
    main_rows = main_rows[:limit]
    final_rows = main_rows + [r for r in unattrib_rows if (
        r.get("revenue") or r.get("cost") or r.get("margin")
    )]

    notes: list[str] = []
    if revenue_meta.get("unattributed_draft_count"):
        notes.append(
            f"{revenue_meta['unattributed_draft_count']} AR drafts had "
            f"no {dimension} key and were bucketed as unattributed."
        )
    # AR invoices that lack every date field (period_start, due_date,
    # created_at) bypass the date window unconditionally. Surface the
    # count so the LLM can quote "X invoice(s) had no date and were
    # included regardless" instead of letting them roll up silently.
    # The aggregator only increments unscoped_invoice_count when a
    # window is set, so the note is suppressed when no window is in
    # play (where "included regardless" would be vacuously true).
    unscoped_n = revenue_meta.get("unscoped_invoice_count") or 0
    if unscoped_n:
        noun = "invoice" if unscoped_n == 1 else "invoices"
        notes.append(
            f"{unscoped_n} AR {noun} had no date field and were "
            "included in the rollup regardless of date_from / date_to."
        )
    if cost_meta.get("time_entries_without_contract"):
        notes.append(
            f"{cost_meta['time_entries_without_contract']} approved time "
            "entries were skipped because their contract could not be resolved."
        )
    if dimension == "deal" and cost_meta.get("time_entries_without_deal_mapping"):
        notes.append(
            f"{cost_meta['time_entries_without_deal_mapping']} approved time "
            "entries belonged to contracts with no deal_ids and were "
            "bucketed as unattributed cost."
        )
    if dimension == "deal" and cost_meta.get("deal_split_active"):
        notes.append(
            "Cost on contracts that touch multiple deals was split evenly "
            "across the deal_ids on each contract - any single-deal "
            "attribution is a heuristic."
        )
    if truncated:
        notes.append(
            f"Showing top {limit} {dimension}s by {metric}; "
            f"{limit} of {pre_cap_main_count} returned."
        )

    return {
        "dimension": dimension,
        "metric": metric,
        "filters": {"date_from": date_from, "date_to": date_to, "limit": limit},
        "rows": final_rows,
        "row_count": len(final_rows),
        "unattributed": unattributed,
        "source_metadata": {
            "revenue": revenue_meta,
            "cost": cost_meta,
        },
        "notes": notes,
    }


def _unavailable(metric: str, source: str, dimension: str) -> dict[str, Any]:
    if "finance_drafts" in source or "ar_invoice" in source:
        hint = (
            "Direct the user to /api/finance/drafts to confirm AR drafts "
            "exist, or wait for the next draft sync / auto-generation run."
        )
    elif "time_entries" in source:
        hint = (
            "Direct the user to /api/finance/accounts-payable to confirm "
            "approved time entries exist for the period."
        )
    else:
        hint = (
            "Direct the user to the finance portal to confirm the source "
            "data exists for the requested period."
        )
    return {
        "error": "unavailable_mapping",
        "metric": metric,
        "dimension": dimension,
        "detail": (
            f"No source data is available to compute {metric} by "
            f"{dimension}. The {source} collection had no rows in scope."
        ),
        "hint": hint,
    }


__all__ = [
    "aggregate_customer_deal_finance",
]
