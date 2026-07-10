"""Cashflow Planner foundation — Friday week-ending normalization,
unpaid past-due roll-forward, and an audit trail.

Scope of this slice (MVP, backend-only):

  * Pure date helpers that snap any expected receipt / payment date to
    a Friday week-ending bucket, and resolve the "active" Friday
    week-ending for a given ``today``.
  * A frozen normalized row shape (``CashflowItem``) used as the input
    to the planner. Adapters in ``invoice_repository`` / finance
    snapshots are expected to populate this shape from the existing
    Nova / finance sources (``finance_drafts``, ``ar_invoices``,
    ``reports_ar_aging_detail``, finance snapshots); this module
    does NOT introduce a new source system.
  * ``roll_unpaid_past_due_items(items, today)`` — pure function that
    pushes every unpaid item whose Friday week-ending falls before the
    active week onto the active week and emits an audit record per
    rolled item. Paid / synced / terminal items are never moved, and
    unpaid items already at or beyond the active week are left alone.
  * ``WeekForecastBucket`` — per-Friday aggregate carrying expected
    A/R, A/P, and net cash flow.
  * ``bucket_items_into_weeks(items)`` — fold normalized items into
    those aggregates.
  * ``write_cashflow_audit_records(records, db=None)`` — append-only
    writer to the ``nova_cashflow_audit`` collection. Mirrors the
    Nova ``projection_runs`` persistence pattern (app DB, no
    transactions, audit failure never raises).

The audit record schema is fixed:

    item_id, item_type, old_week_ending_date, new_week_ending_date,
    reason="unpaid_past_due_roll_forward", status_at_change, amount,
    source_system, changed_at

Status classification: callers normalize their source-system status
into the ``CashflowItem.status`` string. ``_TERMINAL_STATUSES`` is the
closed set this module treats as "paid / settled". Anything else
counts as still-outstanding and is eligible for roll-forward when
past-due. This conservative default matches the product rule from the
Cashflow Planner blueprint: "if we don't *know* it's paid, keep it
visible and keep rolling it forward until it is".

Nothing in this module writes back to external systems (QuickBooks,
Zoho, Stripe, bank feeds, Google Sheets). The audit collection is the
single side effect.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Literal, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ConfigDict, Field

from .db import get_app_db


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROLL_FORWARD_REASON = "unpaid_past_due_roll_forward"

CASHFLOW_AUDIT_COLLECTION = "nova_cashflow_audit"

ItemType = Literal["ar", "ap"]

# Statuses we treat as terminal — already settled, do not roll forward.
# Kept lowercase; callers' status strings are matched case-insensitively.
# Values come from existing AEGISAXIOM / finance sources:
#   - finance_drafts.sync_status: "Synced" (terminal once QBO posts back)
#   - ar_invoices: Balance == 0 → caller emits "paid" / "closed"
#   - finance snapshots: caller emits "posted" once landed in GL
_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "paid",
    "closed",
    "settled",
    "synced",
    "posted",
    "void",
    "voided",
    "cancelled",
    "canceled",
})


def is_terminal_status(status: str | None) -> bool:
    """Return True if ``status`` indicates the item is already settled.

    Comparison is case-insensitive and whitespace-trimmed. ``None`` or
    an empty string is treated as non-terminal — if we don't know, we
    keep the item visible (the cashflow-planner product rule).
    """
    if not status:
        return False
    return status.strip().lower() in _TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Week-ending date arithmetic
# ---------------------------------------------------------------------------
#
# ``weekday()`` returns Monday=0 .. Sunday=6, so Friday=4. The number of
# days from any weekday to the next (or same-day) Friday is
# ``(4 - weekday) % 7``. Saturday/Sunday correctly roll to the
# following Friday because the modulo gives 6 and 5 respectively.

_FRIDAY = 4


def to_friday_week_ending(d: date) -> date:
    """Snap any calendar date to its Friday week-ending bucket.

    Mon-Fri → that week's Friday. Sat/Sun → next Friday.

    Examples:
        Tue 2026-01-13 → Fri 2026-01-16
        Fri 2026-01-16 → Fri 2026-01-16
        Sat 2026-01-17 → Fri 2026-01-23
    """
    if isinstance(d, datetime):
        d = d.date()
    days_until_friday = (_FRIDAY - d.weekday()) % 7
    return d + timedelta(days=days_until_friday)


def active_week_ending(today: date) -> date:
    """Return the Friday that anchors the active forecast week.

    For MVP this is identical to ``to_friday_week_ending(today)``:
    Mon-Fri returns the same week's Friday; Sat/Sun rolls to next
    Friday. Kept as a separate symbol so callers can swap in tenant-
    specific cutover rules later without touching every call site.
    """
    return to_friday_week_ending(today)


# ---------------------------------------------------------------------------
# Normalized row shape
# ---------------------------------------------------------------------------


class CashflowItem(BaseModel):
    """Normalized A/R or A/P row consumed by the planner.

    Adapters living next to existing sources populate this from:
      * ``finance_drafts``         (sync_status + due_date)
      * ``ar_invoices``            (Balance + DueDate)
      * ``reports_ar_aging_detail``(line_items.Open Balance + Due Date)
      * Nova finance snapshots    (already-aggregated A/R, A/P)

    No new ingestion is added in this slice. Frozen so a downstream
    caller can't accidentally mutate the source row — every mutation
    is expressed by emitting a new item alongside an audit record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    item_type: ItemType
    counterparty: str
    document_number: Optional[str] = None
    source_date: Optional[date] = None
    due_date: Optional[date] = None
    expected_date: date
    week_ending_date: date
    amount: float
    status: str
    source_system: str
    last_checked_at: datetime
    # Free-form bag for adapters to attach origin metadata (e.g. the
    # original due_date prior to a roll-forward). The planner writes
    # ``original_week_ending_date`` here when it rolls an item.
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Audit record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CashflowAuditRecord:
    """One audit entry per roll-forward event.

    Dataclass (not Pydantic) so equality / dict-conversion stays cheap
    inside the pure roll-forward function. The persistence helper
    converts to a Mongo-friendly dict at write time.
    """

    item_id: str
    item_type: ItemType
    old_week_ending_date: date
    new_week_ending_date: date
    reason: str
    status_at_change: str
    amount: float
    source_system: str
    changed_at: datetime
    document_number: Optional[str] = None
    counterparty: Optional[str] = None

    @property
    def audit_key(self) -> str:
        """Deterministic idempotency key.

        Two calls that move the same item between the same two Friday
        week-endings collapse to a single audit row. The writer queries
        by ``audit_key`` and skips inserts that already exist, so a
        repeated roll-forward run (cron, retries, demo replay) does
        not spam duplicate rows. Shape mirrors the scheduled-event
        dedupe keys in ``scheduled_events.py``:

            unpaid_past_due_roll_forward:{item_type}:{item_id}:
                {old_friday}:{new_friday}
        """
        return (
            f"{self.reason}:{self.item_type}:{self.item_id}:"
            f"{self.old_week_ending_date.isoformat()}:"
            f"{self.new_week_ending_date.isoformat()}"
        )

    def to_doc(self) -> dict[str, Any]:
        """Serialize for Mongo insert. Dates → YYYY-MM-DD strings."""
        return {
            "audit_id": f"pcfa_{uuid.uuid4().hex[:16]}",
            "audit_key": self.audit_key,
            "item_id": self.item_id,
            "item_type": self.item_type,
            "old_week_ending_date": self.old_week_ending_date.isoformat(),
            "new_week_ending_date": self.new_week_ending_date.isoformat(),
            "reason": self.reason,
            "status_at_change": self.status_at_change,
            "amount": float(self.amount),
            "source_system": self.source_system,
            "changed_at": self.changed_at,
            "document_number": self.document_number,
            "counterparty": self.counterparty,
        }


# ---------------------------------------------------------------------------
# Roll-forward
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollForwardResult:
    """Output of ``roll_unpaid_past_due_items``.

    * ``items`` — every input item, in order, with rolled items
      replaced by a new instance carrying the active week's Friday and
      ``metadata["original_week_ending_date"]`` set to the prior value.
    * ``audit_records`` — one entry per roll-forward. Same order as
      the rolled items appeared in the input.
    * ``active_week_ending`` — convenience copy of the anchor Friday
      so callers don't have to recompute it.
    """

    items: list[CashflowItem]
    audit_records: list[CashflowAuditRecord]
    active_week_ending: date


def roll_unpaid_past_due_items(
    items: Sequence[CashflowItem],
    today: date,
    *,
    now: Optional[datetime] = None,
) -> RollForwardResult:
    """Push every unpaid, past-due item onto the active Friday.

    Rules (product-critical — do not soften):

      1. Determine the active Friday from ``today`` via
         ``active_week_ending``.
      2. For each item:
         - If ``is_terminal_status(status)`` → leave untouched.
         - Else if ``week_ending_date < active`` → emit a NEW item with
           ``week_ending_date = active`` and stamp the prior value into
           ``metadata["original_week_ending_date"]``. Emit one audit
           record per rolled item.
         - Else → leave untouched.
      3. Past-due unpaid items are NEVER dropped, even if their
         original expected date sits far in the past.

    ``now`` is injectable for deterministic tests; defaults to
    ``datetime.now(timezone.utc)`` at call time.
    """
    active = active_week_ending(today)
    changed_at = now if now is not None else datetime.now(timezone.utc)

    rolled_items: list[CashflowItem] = []
    audit_records: list[CashflowAuditRecord] = []

    for item in items:
        if is_terminal_status(item.status):
            rolled_items.append(item)
            continue
        if item.week_ending_date >= active:
            rolled_items.append(item)
            continue

        original_week_ending = item.week_ending_date
        # Preserve any prior metadata; stamp the original date so the
        # audit record is reconstructable from the item alone.
        new_metadata = dict(item.metadata or {})
        # Only stamp the very first roll-forward's original date.
        # Subsequent rolls of an already-rolled item would otherwise
        # overwrite the *true* original with an intermediate value.
        new_metadata.setdefault(
            "original_week_ending_date", original_week_ending.isoformat()
        )
        new_metadata["last_roll_forward_from"] = original_week_ending.isoformat()
        new_metadata["last_roll_forward_at"] = changed_at.isoformat()

        rolled = item.model_copy(update={
            "week_ending_date": active,
            "metadata": new_metadata,
        })
        rolled_items.append(rolled)
        audit_records.append(CashflowAuditRecord(
            item_id=item.item_id,
            item_type=item.item_type,
            old_week_ending_date=original_week_ending,
            new_week_ending_date=active,
            reason=ROLL_FORWARD_REASON,
            status_at_change=item.status,
            amount=item.amount,
            source_system=item.source_system,
            changed_at=changed_at,
            document_number=item.document_number,
            counterparty=item.counterparty,
        ))

    return RollForwardResult(
        items=rolled_items,
        audit_records=audit_records,
        active_week_ending=active,
    )


# ---------------------------------------------------------------------------
# Forecast bucket (Friday → expected A/R, A/P, net cash flow)
# ---------------------------------------------------------------------------


class WeekForecastBucket(BaseModel):
    """One Friday week-ending bucket in the planner output.

    Compatible-by-addition with the existing ``ProjectionOutput``: this
    is an opt-in extension caller can request alongside the legacy
    ``weeks`` list rather than a replacement, so existing Nova
    consumers keep their contract.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    week_ending: date
    expected_ar: float = 0.0
    expected_ap: float = 0.0
    net_cash_flow: float = 0.0


def bucket_items_into_weeks(
    items: Iterable[CashflowItem],
) -> list[WeekForecastBucket]:
    """Fold normalized items into per-Friday buckets.

    Buckets are returned in ascending ``week_ending`` order. A/R sums
    into ``expected_ar``, A/P into ``expected_ap``, and
    ``net_cash_flow`` = A/R minus A/P. Terminal-status items are
    excluded from the forecast — they have already happened and don't
    represent expected future cash. Callers that want a "what was
    collected" view should compute that separately.
    """
    by_week: dict[date, dict[str, float]] = {}
    for item in items:
        if is_terminal_status(item.status):
            continue
        wk = item.week_ending_date
        slot = by_week.setdefault(wk, {"ar": 0.0, "ap": 0.0})
        if item.item_type == "ar":
            slot["ar"] += float(item.amount)
        elif item.item_type == "ap":
            slot["ap"] += float(item.amount)

    buckets: list[WeekForecastBucket] = []
    for wk in sorted(by_week):
        ar = round(by_week[wk]["ar"], 2)
        ap = round(by_week[wk]["ap"], 2)
        buckets.append(WeekForecastBucket(
            week_ending=wk,
            expected_ar=ar,
            expected_ap=ap,
            net_cash_flow=round(ar - ap, 2),
        ))
    return buckets


# ---------------------------------------------------------------------------
# Audit persistence
# ---------------------------------------------------------------------------


async def write_cashflow_audit_records(
    records: Sequence[CashflowAuditRecord],
    db: AsyncIOMotorDatabase | None = None,
) -> int:
    """Append audit records to ``nova_cashflow_audit`` idempotently.

    Returns the number of NEW rows actually written. Records whose
    ``audit_key`` already exists in the collection are silently
    skipped — a repeated planner run (cron, retry, demo replay) will
    not duplicate audit rows for the same item / old→new movement.

    A unique index on ``audit_key`` is the durable enforcement layer
    (see the migration that this slice's follow-up will land); the
    pre-check here is the defence-in-depth path that keeps existing
    deployments correct until that index ships.

    Audit must never raise into the caller's response path, so a
    Mongo failure degrades to a logged warning and a 0 return —
    same posture as ``services.audit_service.log_action``.
    """
    if not records:
        return 0
    try:
        if db is None:
            db = get_app_db()
        coll = db[CASHFLOW_AUDIT_COLLECTION]

        # Pre-filter records whose audit_key already exists. Single
        # round-trip with $in keeps this O(1) per planner run even
        # when the audit collection grows.
        keys = [r.audit_key for r in records]
        existing_cursor = coll.find(
            {"audit_key": {"$in": keys}}, {"_id": 0, "audit_key": 1},
        )
        existing: set[str] = set()
        async for doc in existing_cursor:
            if "audit_key" in doc:
                existing.add(doc["audit_key"])

        new_docs = [
            r.to_doc() for r in records if r.audit_key not in existing
        ]
        if not new_docs:
            return 0
        result = await coll.insert_many(new_docs)
        return len(result.inserted_ids)
    except Exception as exc:  # noqa: BLE001 — audit must never raise
        logger.error(
            "Failed to write nova_cashflow_audit rows (%d records): %s",
            len(records), exc,
        )
        return 0


__all__ = [
    "ROLL_FORWARD_REASON",
    "CASHFLOW_AUDIT_COLLECTION",
    "CashflowItem",
    "CashflowAuditRecord",
    "RollForwardResult",
    "WeekForecastBucket",
    "active_week_ending",
    "bucket_items_into_weeks",
    "is_terminal_status",
    "roll_unpaid_past_due_items",
    "to_friday_week_ending",
    "write_cashflow_audit_records",
]
