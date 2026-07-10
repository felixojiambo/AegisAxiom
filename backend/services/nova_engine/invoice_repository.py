"""Invoice data-access helper for Nova.

Two logical sources, separated on purpose:

  * **open-invoice snapshot source** — the latest AR-aging snapshot.
    Open invoices only. Backs ``get_open_invoices``.
  * **issued-invoices source** — the full issued-invoice register.
    Includes paid AND open invoices. Backs ``query_invoice_totals``.

Nova tool branches MUST go through this module rather than reading
the underlying store directly. Two reasons:

  1. The current AEGISAXIOM deployment uses Mongo to back both sources, but
     Nova tool semantics MUST NOT bake in the storage engine. A
     future PostgreSQL adapter can replace this module's internals
     without changing tool contracts or prompt text.
  2. Date / customer / class filtering is pushed into the data-access
     query layer here. The Nova tool branches receive
     already-filtered rows + server-computed totals — they never
     iterate the full snapshot themselves.

Public surface (database-agnostic):

  * ``OpenInvoiceQuery`` / ``OpenInvoiceQueryResult``
  * ``IssuedInvoiceQuery`` / ``IssuedInvoiceQueryResult``
  * ``query_open_invoice_rows(filters)``
  * ``query_issued_invoice_totals(filters)``

Storage-specific query syntax (aggregation pipelines, ``$match``,
``$facet``, ``$unwind``) lives strictly inside the private functions
below. Tests assert against the public result shapes, not against
pipeline syntax.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .db import get_qbo_db


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public query / result models — database-agnostic
# ---------------------------------------------------------------------------


class OpenInvoiceQuery(BaseModel):
    """Logical filter object for the open-invoice snapshot source.

    Fields that the data-access layer can push into the storage
    engine's query plan:
        * date_from / date_to (inclusive, YYYY-MM-DD against invoice date)
        * search (substring match on customer name OR invoice doc number)
        * bucket (aging bucket label — e.g. "Current", "1 - 30")
        * class_name (QBO class label, joined from the issued-invoice
          register; falls back to in-helper filtering if a join isn't
          natively supported by the adapter)
    """

    model_config = ConfigDict(extra="forbid")

    date_from: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    search: Optional[str] = None
    bucket: Optional[str] = None
    class_name: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=200)
    # Pagination offset for callers that need to walk the full result
    # set (e.g. the projection scheduler, which must collect every
    # open invoice within a horizon). ``offset`` is 0-based and applied
    # AFTER server-side sort+filter; the chat read tool keeps the
    # default 0 so its single-page behaviour is unchanged. The cap
    # mirrors a sane upper bound — pages beyond 10k rows almost
    # certainly indicate the caller forgot to pass date_from/date_to.
    offset: int = Field(default=0, ge=0, le=10_000)


class OpenInvoiceRow(BaseModel):
    """Normalized open-invoice row. Storage-engine field names do NOT
    appear here — only Nova-facing display fields."""

    model_config = ConfigDict(extra="forbid")

    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    customer: Optional[str] = None
    amount: float = 0.0
    open_balance: float = 0.0
    bucket: Optional[str] = None
    class_name: Optional[str] = None
    location: Optional[str] = None


class OpenInvoiceQueryResult(BaseModel):
    """Result of querying the open-invoice snapshot source."""

    model_config = ConfigDict(extra="forbid")

    total_matched: int = 0
    total_amount: float = 0.0
    total_open_balance: float = 0.0
    rows: list[OpenInvoiceRow] = Field(default_factory=list)
    rows_returned: int = 0
    preview_truncated: bool = False
    snapshot_synced_at: Optional[str] = None
    # When True, the helper had to fall back to in-helper filtering
    # (e.g. because the requested filter requires a side-join the
    # underlying adapter does not push). Kept transparent for the
    # tool branch to surface in its response notes.
    used_post_query_filtering: bool = False


class IssuedInvoiceQuery(BaseModel):
    """Logical filter object for the issued-invoices source.

    Required fields: date_from, date_to. The issued-invoice tool is
    period-bound by design — refuse without an explicit window.
    """

    model_config = ConfigDict(extra="forbid")

    date_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    search: Optional[str] = None
    customer: Optional[str] = None
    class_name: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None
    include_rows: bool = True
    limit: int = Field(default=100, ge=1, le=500)


class IssuedInvoiceRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_date: Optional[str] = None
    doc_num: Optional[str] = None
    customer: Optional[str] = None
    total_amount: float = 0.0
    balance: Optional[float] = None
    status: Optional[str] = None
    class_name: Optional[str] = None
    location: Optional[str] = None


class IssuedInvoiceQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_count: int = 0
    total_invoice_amount: float = 0.0
    rows: list[IssuedInvoiceRow] = Field(default_factory=list)
    rows_returned: int = 0
    preview_truncated: bool = False
    snapshot_synced_at: Optional[str] = None
    # Whether the underlying source carries paid/closed invoices in
    # addition to open ones. ``True`` for ar_invoices (AEGISAXIOM today);
    # ``"unknown"`` if a future adapter cannot self-describe.
    # Closed Union[bool, Literal["unknown"]] so the assignment sites
    # are type-checked against the surface the API contract advertises
    # — assigning a stray string would be flagged at import.
    includes_paid_and_closed: Union[bool, Literal["unknown"]] = True
    used_post_query_filtering: bool = False
    # True when class_name was applied as a post-query filter on the
    # preview window — invoice_count / total_invoice_amount in that
    # case reflect ONLY the preview, not the full period. Surfaced
    # explicitly so the caller can warn the user instead of quoting
    # the constrained totals as if they covered the full window.
    class_filtered_preview_only: bool = False
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Adapter — open-invoice snapshot source (current Mongo storage backs this)
# ---------------------------------------------------------------------------
#
# The storage engine is hidden inside this function. The current
# implementation uses Mongo's aggregation framework to push date /
# bucket / search filters and totals into the query layer. A future
# PostgreSQL adapter should replace ONLY this private function while
# preserving the public ``query_open_invoice_rows`` signature.


async def query_open_invoice_rows(
    filters: OpenInvoiceQuery,
    *,
    db: Any = None,
) -> OpenInvoiceQueryResult:
    """Query the open-invoice snapshot source with server-side filtering.

    The adapter pushes date / bucket / search filters into the storage
    engine's query plan. Class-name filtering requires a join into the
    issued-invoice register (AEGISAXIOM schema today: ``ar_invoices`` carries
    QBO ``ClassRef`` per line); when set, this helper does the join +
    final filter inside the helper and reports
    ``used_post_query_filtering=True`` so the caller can disclose it.
    """
    if db is None:
        db = get_qbo_db()

    # ---- Build the logical $match for the unwound line items -------
    # Storage-engine syntax stays inside this function. The public
    # contract is OpenInvoiceQueryResult.
    match_stage: dict[str, Any] = {"line_items.is_summary": {"$ne": True}}
    if filters.date_from:
        match_stage.setdefault("line_items.Date", {})["$gte"] = filters.date_from
    if filters.date_to:
        match_stage.setdefault("line_items.Date", {})["$lte"] = filters.date_to
    if filters.bucket:
        # The aging bucket label lives on the line item as ``section``.
        match_stage["line_items.section"] = {
            "$regex": re.escape(filters.bucket),
            "$options": "i",
        }
    if filters.search:
        # Substring match across customer name and invoice doc number.
        s = re.escape(filters.search.strip())
        match_stage["$or"] = [
            {"line_items.Customer": {"$regex": s, "$options": "i"}},
            {"line_items.Num": {"$regex": s, "$options": "i"}},
        ]

    # The snapshot source stores numeric fields as strings in the
    # current schema (Amount, Open Balance). $sum of a string field
    # silently returns 0 in Mongo, so we coerce via $convert with a
    # 0 fallback for unparseable values BEFORE summing. The coercion
    # is internal to the data layer; the public result still carries
    # server-computed totals — this stays on the preferred path
    # (totals computed by the data store), not the Python fallback.
    _amount_double = {
        "$convert": {
            "input": "$line_items.Amount",
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }
    _open_balance_double = {
        "$convert": {
            "input": "$line_items.Open Balance",
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }
    pipeline = [
        {"$sort": {"synced_at": -1}},
        {"$limit": 1},
        {"$unwind": "$line_items"},
        {"$match": match_stage},
        {
            "$facet": {
                "preview": [
                    {"$sort": {"line_items.Date": -1}},
                    # $skip is a no-op when offset=0 (the chat tool
                    # read path) so existing callers keep their
                    # single-page behaviour. The projection scheduler
                    # advances offset across pages to walk the full
                    # result set.
                    {"$skip": filters.offset},
                    {"$limit": filters.limit},
                    {
                        "$project": {
                            "_id": 0,
                            "invoice_number": "$line_items.Num",
                            "invoice_date": "$line_items.Date",
                            "due_date": "$line_items.Due Date",
                            "customer": "$line_items.Customer",
                            "amount": _amount_double,
                            "open_balance": _open_balance_double,
                            "bucket": "$line_items.section",
                            "location": "$line_items.Location",
                        }
                    },
                ],
                "totals": [
                    {
                        "$group": {
                            "_id": None,
                            "count": {"$sum": 1},
                            "amount": {"$sum": _amount_double},
                            "open_balance": {"$sum": _open_balance_double},
                        }
                    }
                ],
                "snapshot_meta": [
                    # Only snapshot_meta_list[0] is consumed downstream,
                    # so cap the sub-pipeline at one document. Without
                    # the $limit, $project would run on every doc the
                    # outer $sort+$limit produced (here 1, so still
                    # safe today), but the cap makes the contract
                    # explicit and protects against an outer-pipeline
                    # change later.
                    {"$limit": 1},
                    {"$project": {"_id": 0, "synced_at": 1}},
                ],
            }
        },
    ]

    cursor = db.reports_ar_aging_detail.aggregate(pipeline)
    docs = await cursor.to_list(length=1)
    if not docs:
        # Source unavailable — empty collection or no match. Caller
        # treats this as the "no data" case.
        return OpenInvoiceQueryResult()

    facet = docs[0]
    preview = facet.get("preview") or []
    totals_list = facet.get("totals") or [{}]
    totals = totals_list[0] if totals_list else {}
    snapshot_meta_list = facet.get("snapshot_meta") or [{}]
    snapshot_synced_at = (snapshot_meta_list[0] or {}).get("synced_at")
    if snapshot_synced_at is not None and not isinstance(snapshot_synced_at, str):
        snapshot_synced_at = str(snapshot_synced_at)

    total_matched = int(totals.get("count", 0) or 0)
    total_amount = float(totals.get("amount", 0.0) or 0.0)
    total_open_balance = float(totals.get("open_balance", 0.0) or 0.0)

    # ---- Class-name filtering via side join (when requested) -------
    # The aging snapshot doesn't carry QBO class on its rows, so a
    # class filter requires looking up each preview row's class in
    # the issued-invoice register. We do that for the (already
    # date-filtered) preview rows. When class_name is set, totals are
    # recomputed in Python from the surviving subset. The result
    # reports used_post_query_filtering=True for transparency.
    used_post_query_filtering = False
    rows: list[OpenInvoiceRow] = []
    if filters.class_name:
        used_post_query_filtering = True
        rows_with_class = await _attach_class_and_filter(
            db, preview, filters.class_name
        )
        rows = [OpenInvoiceRow(**r) for r in rows_with_class]
        total_matched = len(rows)
        total_amount = sum(r.amount for r in rows)
        total_open_balance = sum(r.open_balance for r in rows)
    else:
        # Without a class filter we still attach class_name where we
        # can (best-effort). It's a display-only lookup; missing class
        # is normal.
        rows_with_class = await _attach_class_only(db, preview)
        rows = [OpenInvoiceRow(**r) for r in rows_with_class]

    rows_returned = len(rows)
    preview_truncated = total_matched > rows_returned

    return OpenInvoiceQueryResult(
        total_matched=total_matched,
        total_amount=round(total_amount, 2),
        total_open_balance=round(total_open_balance, 2),
        rows=rows,
        rows_returned=rows_returned,
        preview_truncated=preview_truncated,
        snapshot_synced_at=snapshot_synced_at,
        used_post_query_filtering=used_post_query_filtering,
    )


async def _attach_class_only(db: Any, preview_rows: list[dict]) -> list[dict]:
    """Best-effort class_name attach with NO filter. Returns rows
    enriched with class_name where the issued-invoice register
    carries it; rows where the class lookup misses get class_name=None.
    """
    if not preview_rows:
        return []
    class_map = await _build_class_map(db, preview_rows)
    out: list[dict] = []
    for r in preview_rows:
        inv_num = r.get("invoice_number")
        out.append({**r, "class_name": class_map.get(str(inv_num) if inv_num else "")})
    return out


async def _attach_class_and_filter(
    db: Any, preview_rows: list[dict], class_filter: str
) -> list[dict]:
    """Side-join class_name and drop rows that don't match the filter."""
    if not preview_rows:
        return []
    class_map = await _build_class_map(db, preview_rows)
    # Case-insensitive comparison: QBO class names land in the snapshot
    # with whatever capitalization the source ledger uses, but the
    # filter value typically comes from a user prompt or a stored
    # filter and may differ in case. Normalize both sides.
    needle = (class_filter or "").strip().lower()
    out: list[dict] = []
    for r in preview_rows:
        inv_num = r.get("invoice_number")
        cls = class_map.get(str(inv_num) if inv_num else "")
        if (cls or "").strip().lower() != needle:
            continue
        out.append({**r, "class_name": cls})
    return out


async def _build_class_map(db: Any, preview_rows: list[dict]) -> dict[str, str]:
    """One side-query against the issued-invoice register to fetch
    class names for the preview rows. Storage-specific syntax stays
    inside this helper.
    """
    nums = {
        str(r.get("invoice_number"))
        for r in preview_rows
        if r.get("invoice_number") is not None
    }
    if not nums:
        return {}
    class_map: dict[str, str] = {}
    cursor = db.ar_invoices.find(
        {"raw_data.DocNumber": {"$in": list(nums)}},
        {
            "_id": 0,
            "raw_data.DocNumber": 1,
            "raw_data.Line": 1,
        },
    )
    async for doc in cursor:
        raw = doc.get("raw_data") or {}
        dn = str(raw.get("DocNumber") or "")
        if not dn:
            continue
        for line in raw.get("Line") or []:
            cls_ref = ((line or {}).get("SalesItemLineDetail") or {}).get("ClassRef") or {}
            name = cls_ref.get("name")
            if name:
                class_map[dn] = name
                break
    return class_map


# ---------------------------------------------------------------------------
# Adapter — issued-invoices source (the all-invoices register)
# ---------------------------------------------------------------------------
#
# Backed by ``ar_invoices`` in AEGISAXIOM today. That register includes
# both open and paid/closed invoices (Balance == 0 indicates a paid
# / closed invoice). When the source's paid/closed coverage is
# uncertain (future adapter), the result's
# ``includes_paid_and_closed`` may be set to ``"unknown"``.


_ISSUED_INVOICE_INCLUDES_PAID_AND_CLOSED = True


async def query_issued_invoice_totals(
    filters: IssuedInvoiceQuery,
    *,
    db: Any = None,
) -> IssuedInvoiceQueryResult:
    """Query the issued-invoices source with server-side filtering.

    Pushes date, customer, search (doc-number / customer substring),
    location, and status filters into the storage engine. Class-name
    filtering requires a nested-line lookup the current adapter
    cannot push cleanly, so when set the helper post-filters the
    preview rows and recomputes totals in Python (transparently
    flagged via ``used_post_query_filtering=True``).
    """
    if db is None:
        db = get_qbo_db()

    match: dict[str, Any] = {
        "raw_data.TxnDate": {"$gte": filters.date_from, "$lte": filters.date_to},
    }
    if filters.customer:
        match["raw_data.CustomerRef.name"] = {
            "$regex": re.escape(filters.customer),
            "$options": "i",
        }
    if filters.search:
        s = re.escape(filters.search.strip())
        match["$or"] = [
            {"raw_data.DocNumber": {"$regex": s, "$options": "i"}},
            {"raw_data.CustomerRef.name": {"$regex": s, "$options": "i"}},
        ]
    if filters.location:
        match["raw_data.DepartmentRef.name"] = {
            "$regex": re.escape(filters.location),
            "$options": "i",
        }
    if filters.status:
        # Best-available status in this register is EmailStatus
        # (NotSet / EmailSent). Kept narrow on purpose — the source
        # has no first-class status enum.
        match["raw_data.EmailStatus"] = {
            "$regex": re.escape(filters.status),
            "$options": "i",
        }

    # Defensive numeric coercion: TotalAmt and Balance are usually
    # float in this source but the helper stays safe against future
    # ingestion changes (e.g. a sync that lands strings). $convert
    # with onError: 0 keeps bad rows from poisoning the total while
    # still letting the data store do the sum.
    _total_amt_double = {
        "$convert": {
            "input": "$raw_data.TotalAmt",
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }
    _balance_double = {
        "$convert": {
            "input": "$raw_data.Balance",
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }
    # ``preview`` is built when the caller wants rows OR when a
    # class-name post-filter needs preview rows to operate on. Mongo
    # rejects ``$limit: 0`` outright, so the previous
    # ``filters.limit if filters.include_rows else 0`` pattern crashed
    # the default INVOICE_TOTAL path (include_rows=False). When neither
    # rows nor class filtering are needed the preview sub-pipeline is
    # skipped entirely.
    needs_preview = filters.include_rows or bool(filters.class_name)
    preview_stage: list[dict[str, Any]] = (
        [
            {"$sort": {"raw_data.TxnDate": -1}},
            {"$limit": filters.limit},
            {
                "$project": {
                    "_id": 0,
                    "invoice_date": "$raw_data.TxnDate",
                    "doc_num": "$raw_data.DocNumber",
                    "customer": "$raw_data.CustomerRef.name",
                    "total_amount": _total_amt_double,
                    "balance": _balance_double,
                    "status": "$raw_data.EmailStatus",
                    "location": "$raw_data.DepartmentRef.name",
                }
            },
        ]
        if needs_preview
        else []
    )
    pipeline = [
        {"$match": match},
        {
            "$facet": {
                "preview": preview_stage,
                "totals": [
                    {
                        "$group": {
                            "_id": None,
                            "count": {"$sum": 1},
                            "total": {"$sum": _total_amt_double},
                        }
                    }
                ],
                "snapshot_meta": [
                    {"$sort": {"synced_at": -1}},
                    {"$limit": 1},
                    {"$project": {"_id": 0, "synced_at": 1}},
                ],
            }
        },
    ]
    cursor = db.ar_invoices.aggregate(pipeline)
    docs = await cursor.to_list(length=1)
    if not docs:
        return IssuedInvoiceQueryResult(
            includes_paid_and_closed=_ISSUED_INVOICE_INCLUDES_PAID_AND_CLOSED,
        )

    facet = docs[0]
    preview = facet.get("preview") or []
    totals_list = facet.get("totals") or [{}]
    totals = totals_list[0] if totals_list else {}
    snap_list = facet.get("snapshot_meta") or [{}]
    snap = (snap_list[0] or {}).get("synced_at")
    if snap is not None and not isinstance(snap, str):
        snap = str(snap)

    invoice_count = int(totals.get("count", 0) or 0)
    total_invoice_amount = float(totals.get("total", 0.0) or 0.0)

    used_post_query_filtering = False
    class_filtered_preview_only = False
    warnings: list[str] = []
    if filters.class_name:
        used_post_query_filtering = True
        class_filtered_preview_only = True
        preview = await _filter_issued_by_class(db, preview, filters.class_name, filters)
        # The class-name post-filter operates on the preview window
        # only, so totals here cover the preview, not the full period.
        # Surfaced via ``class_filtered_preview_only`` and a warning
        # entry so the caller doesn't quote a constrained total as if
        # it covered the entire window.
        invoice_count = len(preview)
        total_invoice_amount = sum(float(r.get("total_amount") or 0.0) for r in preview)
        warnings.append(
            "class filter applied post-query, totals reflect preview "
            "window"
        )

    rows = (
        [IssuedInvoiceRow(**r) for r in preview]
        if filters.include_rows
        else []
    )
    rows_returned = len(rows)
    # preview_truncated is meaningful only when rows are returned. When
    # class_filtered_preview_only is set, invoice_count was reset to
    # len(rows) and the comparison would always be False — keep the
    # original semantics by gating on include_rows.
    preview_truncated = invoice_count > rows_returned and filters.include_rows

    return IssuedInvoiceQueryResult(
        invoice_count=invoice_count,
        total_invoice_amount=round(total_invoice_amount, 2),
        rows=rows,
        rows_returned=rows_returned,
        preview_truncated=preview_truncated,
        snapshot_synced_at=snap,
        includes_paid_and_closed=_ISSUED_INVOICE_INCLUDES_PAID_AND_CLOSED,
        used_post_query_filtering=used_post_query_filtering,
        class_filtered_preview_only=class_filtered_preview_only,
        warnings=warnings,
    )


async def _filter_issued_by_class(
    db: Any,
    preview_rows: list[dict],
    class_filter: str,
    filters: IssuedInvoiceQuery,
) -> list[dict]:
    """Class-name post-filter on the issued-invoice preview window.

    Looks up each preview row's class via the issued-invoice register
    and drops rows that don't match.
    """
    if not preview_rows:
        return []
    nums = {str(r.get("doc_num")) for r in preview_rows if r.get("doc_num") is not None}
    if not nums:
        return []
    cursor = db.ar_invoices.find(
        {"raw_data.DocNumber": {"$in": list(nums)}},
        {"_id": 0, "raw_data.DocNumber": 1, "raw_data.Line": 1},
    )
    class_map: dict[str, str] = {}
    async for doc in cursor:
        raw = doc.get("raw_data") or {}
        dn = str(raw.get("DocNumber") or "")
        if not dn:
            continue
        for line in raw.get("Line") or []:
            cls_ref = ((line or {}).get("SalesItemLineDetail") or {}).get("ClassRef") or {}
            name = cls_ref.get("name")
            if name:
                class_map[dn] = name
                break
    needle = (class_filter or "").strip().lower()
    out: list[dict] = []
    for r in preview_rows:
        cls = class_map.get(str(r.get("doc_num"))) if r.get("doc_num") else None
        if (cls or "").strip().lower() != needle:
            continue
        r2 = dict(r)
        r2["class_name"] = cls
        out.append(r2)
    return out


__all__ = [
    "OpenInvoiceQuery",
    "OpenInvoiceRow",
    "OpenInvoiceQueryResult",
    "IssuedInvoiceQuery",
    "IssuedInvoiceRow",
    "IssuedInvoiceQueryResult",
    "query_open_invoice_rows",
    "query_issued_invoice_totals",
]
