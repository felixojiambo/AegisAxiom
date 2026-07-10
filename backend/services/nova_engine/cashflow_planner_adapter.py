"""Live finance-data adapter for the Cashflow Planner.

Pulls A/R and A/P rows from existing AEGISAXIOM finance collections into the
``CashflowItem`` shape the planner expects. No new ingestion sources,
no external API calls, no writes back to source systems — the adapter
is read-only.

MVP source preference (in priority order):

  1. ``finance_drafts`` — the Financial Command Center draft table.
     Carries both A/R invoices (``kind=ar_invoice``) and A/P bills
     (``kind=ap_bill``) in a single shape, with explicit ``due_date``,
     ``amount``, ``sync_status``, and ``counterparty`` fields. This is
     the cleanest source for MVP and is the only one this slice reads.

  2. ``ar_invoices`` / ``reports_ar_aging_detail`` — issued-invoice
     register and aging snapshot. Reserved for the next slice when we
     decide we need broader A/R coverage. NOT consumed here because
     joining their snapshots with ``finance_drafts`` carries a real
     risk of double-counting the same logical invoice, and the
     reconciliation logic is more than this slice can safely land.

Dedupe key: ``finance_drafts.draft_id`` is unique in the source
collection, so within a single planner run the planner cannot see two
``CashflowItem`` instances with the same ``item_id``. When future
slices add a second source (``ar_invoices``), the dedupe key the
adapter must produce is ``(item_type, draft_id_or_invoice_doc_num)``
so a row that appears in both finance_drafts and ar_invoices for the
same logical doc collapses to one item.

Field mapping (finance_drafts → CashflowItem):

    draft_id          → item_id
    kind              → item_type ("ar_invoice" → "ar",
                                   "ap_bill"    → "ap")
    counterparty      → counterparty
    draft_id          → document_number (until a real doc-num exists)
    created_at        → source_date
    due_date          → due_date AND expected_date (MVP)
    amount            → amount
    sync_status       → status (normalised via _normalise_status)
    "finance_drafts"  → source_system
    updated_at or now → last_checked_at

Rows with no usable ``due_date`` are skipped with a warning. Rows
with a missing / non-numeric ``amount`` are also skipped. The adapter
never raises on a single bad row — it counts and reports them in the
``LoadResult.warnings`` block.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from .cashflow_planner import (
    CashflowItem,
    is_terminal_status,
    to_friday_week_ending,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status normalisation
# ---------------------------------------------------------------------------
#
# The pure planner already knows which statuses are terminal (see
# ``cashflow_planner.is_terminal_status``). The adapter's job is just
# to convert source-specific labels into a stable lowercase string so
# the rest of the pipeline doesn't have to know about Zoho / QBO /
# finance_drafts capitalisation.

_STATUS_TO_PLANNER: dict[str, str] = {
    # finance_drafts sync_status values
    "draft": "draft",
    "sent": "sent",
    "synced": "synced",
    "failed": "failed",
    # ar_invoices / reports_ar_aging_detail observed values (kept for
    # the next slice; harmless when finance_drafts is the only source)
    "paid": "paid",
    "closed": "closed",
    "open": "open",
    "outstanding": "outstanding",
    "unpaid": "unpaid",
    "settled": "settled",
    "posted": "posted",
    "void": "void",
    "voided": "voided",
    "cancelled": "cancelled",
    "canceled": "canceled",
    "pending": "pending",
}


def normalise_status(raw: Any) -> str:
    """Lowercase + strip the source status string.

    Returns the original (lowercased) token when the value isn't in
    our known table — the planner's terminal-status check is the
    final arbiter, and keeping unknown values visible lets a reviewer
    see what came in.
    """
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    return _STATUS_TO_PLANNER.get(s, s)


# ---------------------------------------------------------------------------
# Row → CashflowItem
# ---------------------------------------------------------------------------


_KIND_TO_ITEM_TYPE: dict[str, str] = {
    "ar_invoice": "ar",
    "ap_bill": "ap",
}


def _parse_iso_date(value: Any) -> date | None:
    """Tolerate either a ``date`` instance, a ``datetime`` instance,
    or a YYYY-MM-DD string. Returns None on anything else.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        # Mongo motor returns timezone-aware datetimes; if the source
        # row carries a naive datetime, leave it naive — the planner
        # never compares last_checked_at to anything except itself.
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _coerce_amount(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        # bool is a subclass of int; explicit reject so True doesn't
        # become 1.0.
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        s = raw.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class AdapterWarning:
    """One per-row issue the adapter chose to skip rather than fail
    on. Surfaced in ``LoadResult.warnings`` so the route can log /
    return them without dropping the rest of the planner output.
    """

    kind: str
    draft_id: str | None
    detail: str


@dataclass
class LoadResult:
    """Output of ``load_cashflow_items_from_finance_sources``.

    Kept as a small dataclass (not Pydantic) because the route doesn't
    need to validate it — the planner does that downstream.
    """

    items: list[CashflowItem] = field(default_factory=list)
    skipped_count: int = 0
    warnings: list[AdapterWarning] = field(default_factory=list)
    # Per-source counts so the chat / portal can quote
    # "Loaded N rows from finance_drafts". Today that's the only
    # source; the next slice adds entries for ar_invoices etc.
    source_counts: dict[str, int] = field(default_factory=dict)


def row_to_cashflow_item(
    row: dict[str, Any],
    *,
    now: datetime,
) -> tuple[CashflowItem | None, AdapterWarning | None]:
    """Convert one ``finance_drafts`` row into a ``CashflowItem``.

    Returns ``(item, None)`` on success or ``(None, warning)`` when
    the row is unusable. Pure — no I/O, no globals — so the test
    surface can call it directly with synthetic rows.
    """
    draft_id = row.get("draft_id")
    kind = row.get("kind")

    if not draft_id:
        return None, AdapterWarning(
            kind="missing_draft_id", draft_id=None,
            detail="finance_drafts row has no draft_id",
        )

    item_type = _KIND_TO_ITEM_TYPE.get(str(kind or "").strip())
    if item_type is None:
        return None, AdapterWarning(
            kind="unsupported_kind", draft_id=draft_id,
            detail=f"finance_drafts kind {kind!r} is not ar_invoice / ap_bill",
        )

    amount = _coerce_amount(row.get("amount"))
    if amount is None:
        return None, AdapterWarning(
            kind="missing_amount", draft_id=draft_id,
            detail="finance_drafts row has no usable amount",
        )

    due_date = _parse_iso_date(row.get("due_date"))
    if due_date is None:
        return None, AdapterWarning(
            kind="missing_due_date", draft_id=draft_id,
            detail="finance_drafts row has no usable due_date",
        )

    source_date = _parse_iso_date(row.get("created_at"))
    last_checked = (
        _parse_iso_datetime(row.get("updated_at"))
        or _parse_iso_datetime(row.get("created_at"))
        or now
    )
    status = normalise_status(row.get("sync_status"))
    counterparty = row.get("counterparty") or ""

    week_ending = to_friday_week_ending(due_date)

    item = CashflowItem(
        item_id=str(draft_id),
        item_type=item_type,  # type: ignore[arg-type]
        counterparty=str(counterparty),
        document_number=str(draft_id),
        source_date=source_date,
        due_date=due_date,
        expected_date=due_date,
        week_ending_date=week_ending,
        amount=amount,
        status=status,
        source_system="finance_drafts",
        last_checked_at=last_checked,
        metadata={
            "kind": str(kind),
            "counterparty_id": row.get("counterparty_id"),
            "source_origin": row.get("source_origin"),
            "qbo_id": row.get("qbo_id"),
        },
    )
    return item, None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


async def _stream_finance_drafts(
    db: AsyncIOMotorDatabase,
    *,
    kinds: Iterable[str] = ("ar_invoice", "ap_bill"),
) -> list[dict[str, Any]]:
    """Pull every relevant finance_drafts row. The collection is
    intended to be O(thousands) for a single tenant; no pagination
    is needed for MVP. If future tenants exceed that, batch with
    ``$gte`` on draft_id.
    """
    query: dict[str, Any] = {"kind": {"$in": list(kinds)}}
    cursor = db["finance_drafts"].find(query, {"_id": 0})
    return [doc async for doc in cursor]


async def load_cashflow_items_from_finance_sources(
    db: AsyncIOMotorDatabase,
    *,
    tenant_id: Optional[str] = None,
    as_of: Optional[date] = None,
    horizon_weeks: int = 13,
    now: Optional[datetime] = None,
) -> LoadResult:
    """Load normalised cashflow items from existing AEGISAXIOM finance data.

    Args:
        db: Motor database handle. Caller supplies it so tests can
            pass a fake without standing up Mongo.
        tenant_id: reserved for the multi-tenant slice. Today AEGISAXIOM is
            single-tenant in practice and finance_drafts has no
            ``tenant_id`` field, so this argument is accepted but
            not used — passing a value is silently a no-op.
        as_of: the planner-side anchor date. Not used to filter the
            source rows in this slice (we want past-due items to
            survive so the planner can roll them forward); it's
            forwarded to the route caller for display.
        horizon_weeks: reserved for a future date-range filter. Not
            used in this slice because the planner consumes the full
            row set and the active-week roll-forward decides what to
            keep.
        now: injectable wall-clock for deterministic tests. Defaults
            to ``datetime.now(timezone.utc)``.

    Returns:
        ``LoadResult`` carrying items + per-row warnings + source
        counts.

    No writes happen here. No external systems are touched. The only
    side effect is the read against ``db["finance_drafts"]``.
    """
    # ``tenant_id`` / ``as_of`` / ``horizon_weeks`` are accepted to
    # match the route's eventual signature — see the docstring above
    # for why they're inert today.
    _ = tenant_id, as_of, horizon_weeks

    eff_now = now if now is not None else datetime.now(timezone.utc)

    rows = await _stream_finance_drafts(db)

    items: list[CashflowItem] = []
    warnings: list[AdapterWarning] = []
    for row in rows:
        item, warn = row_to_cashflow_item(row, now=eff_now)
        if item is not None:
            items.append(item)
        elif warn is not None:
            warnings.append(warn)

    return LoadResult(
        items=items,
        skipped_count=len(warnings),
        warnings=warnings,
        source_counts={
            "finance_drafts": len(items),
        },
    )


def summarise_warnings(warnings: list[AdapterWarning]) -> list[dict[str, Any]]:
    """Compact warning payload for inclusion in the route response.

    The route exposes ``warnings`` to the caller so they can fix
    upstream data; this helper keeps the dataclass shape out of the
    JSON response so we don't accidentally leak internals.
    """
    return [
        {"kind": w.kind, "draft_id": w.draft_id, "detail": w.detail}
        for w in warnings
    ]


__all__ = [
    "AdapterWarning",
    "LoadResult",
    "load_cashflow_items_from_finance_sources",
    "normalise_status",
    "row_to_cashflow_item",
    "summarise_warnings",
    # Re-export so callers don't have to know the planner module name.
    "is_terminal_status",
]
