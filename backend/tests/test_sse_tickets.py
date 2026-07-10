"""Tests for the SSE ticket store (`services.sse_tickets`).

The ticket store is small but security-sensitive — these tests pin the
behavior the SSE auth dependency relies on:

  - issue_ticket persists a row bound to (user_id, project_id) with a
    bounded expiration.
  - consume_ticket is atomic, single-use, time-bounded, and returns the
    bound identity on success / None on every failure mode.
  - The filter passed to `find_one_and_update` enforces both the
    `consumed_at IS NULL` and `expires_at > now` guards — this is the
    atomicity boundary, and the SSE auth depends on it.

Rather than spin up a real Mongo, we use a tiny in-process fake that
implements the small surface `consume_ticket` actually touches
(`insert_one`, `find_one_and_update`, `create_index`). This keeps the
tests fast + deterministic and exercises the real query shape.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from services import sse_tickets


# ---------------------------------------------------------------------------
# Fake Mongo collection — just enough for the ticket store
# ---------------------------------------------------------------------------
class _FakeCollection:
    """In-process stand-in for `db['sse_tickets']`.

    Supports the exact method surface `sse_tickets` calls:
      - create_index(...)         — no-op
      - insert_one(doc)
      - find_one_and_update(filter, update, *, return_document, projection)

    The filter matcher implements only the operators we use:
      - exact equality
      - `{"$gt": value}` for `expires_at`
      - `consumed_at: None` (exact None match)
    """
    def __init__(self):
        self._rows: List[Dict[str, Any]] = []

    async def create_index(self, *args, **kwargs):
        return None

    async def insert_one(self, doc):
        self._rows.append(dict(doc))
        return None

    def _matches(self, row, flt):
        for key, expected in flt.items():
            actual = row.get(key)
            if isinstance(expected, dict) and "$gt" in expected:
                if not (actual is not None and actual > expected["$gt"]):
                    return False
            else:
                if actual != expected:
                    return False
        return True

    async def find_one_and_update(self, flt, update, *, return_document=None, projection=None):
        # Find the first matching row (deterministic — we never insert
        # two rows with the same ticket in practice; the unique index
        # enforces that in real Mongo).
        for row in self._rows:
            if self._matches(row, flt):
                # Apply $set
                set_block = update.get("$set", {})
                row.update(set_block)
                if projection:
                    # Inclusion projection: keep only fields with value 1
                    # (or truthy != 0). The `_id: 0` exclusion is implicit
                    # because we never add `_id` to fake rows.
                    return {k: row.get(k) for k, include in projection.items()
                            if include and k in row}
                return dict(row)
        return None

    # Test-only inspection helpers
    def all_rows(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._rows]


class _FakeDb:
    def __init__(self):
        self._collections: Dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]


@pytest.fixture(autouse=True)
def ticket_db(monkeypatch) -> _FakeDb:
    """Each test gets a fresh fake db and a reset TTL-ensured flag.

    `_TTL_INDEX_ENSURED` is a module-level idempotency latch — if a prior
    test set it to True against a real Mongo (or a fake one), a later
    test running against a fresh fake would skip index creation but, more
    importantly, would still work because the fake's create_index is a
    no-op. We reset it anyway for hygiene + symmetry with real-Mongo
    behavior."""
    fake_db = _FakeDb()
    monkeypatch.setattr(sse_tickets, "get_db", lambda: fake_db)
    monkeypatch.setattr(sse_tickets, "_TTL_INDEX_ENSURED", False)
    return fake_db


# ---------------------------------------------------------------------------
# issue_ticket
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_issue_ticket_returns_opaque_string():
    ticket = await sse_tickets.issue_ticket("user_grace", "p1")
    assert isinstance(ticket, str)
    # secrets.token_urlsafe(32) → 43 chars of url-safe base64, no padding.
    assert len(ticket) >= 32
    # No path-traversal / json-injection characters.
    assert not any(ch in ticket for ch in ("/", "+", "=", "?", "&", " "))


@pytest.mark.asyncio
async def test_issue_ticket_persists_binding(ticket_db):
    db = ticket_db
    await sse_tickets.issue_ticket("user_grace", "p1", ttl_seconds=30)

    rows = db["sse_tickets"].all_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == "user_grace"
    assert row["project_id"] == "p1"
    assert row["consumed_at"] is None
    # expires_at is in the future by ~30s; allow generous slack.
    now = datetime.now(timezone.utc)
    assert row["expires_at"] > now
    assert row["expires_at"] < now + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_issue_ticket_each_call_is_unique():
    """Two calls must produce different tickets — the URL-safe random
    is the only safety net against guessing."""
    a = await sse_tickets.issue_ticket("user_grace", "p1")
    b = await sse_tickets.issue_ticket("user_grace", "p1")
    assert a != b


# ---------------------------------------------------------------------------
# consume_ticket
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_consume_ticket_happy_path():
    ticket = await sse_tickets.issue_ticket("user_grace", "p1")
    consumed = await sse_tickets.consume_ticket(ticket)
    assert consumed == {"user_id": "user_grace", "project_id": "p1"}


@pytest.mark.asyncio
async def test_consume_ticket_is_single_use():
    """Second consume of the same ticket must return None — even if the
    first consumer never opened the SSE stream."""
    ticket = await sse_tickets.issue_ticket("user_grace", "p1")
    first = await sse_tickets.consume_ticket(ticket)
    assert first is not None

    second = await sse_tickets.consume_ticket(ticket)
    assert second is None


@pytest.mark.asyncio
async def test_consume_ticket_rejects_unknown():
    """A ticket that never existed → None (not an exception)."""
    consumed = await sse_tickets.consume_ticket("tkt_does_not_exist")
    assert consumed is None


@pytest.mark.asyncio
async def test_consume_ticket_rejects_empty_string():
    """`?ticket=` with no value (or None) → None. Belt-and-braces guard
    so we never accidentally match the first uninitialized row."""
    assert await sse_tickets.consume_ticket("") is None
    assert await sse_tickets.consume_ticket(None) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_consume_ticket_rejects_expired(ticket_db):
    """A row whose `expires_at` is in the past must NOT be consumable.

    Mongo's TTL monitor eventually reaps these, but the sweep cadence is
    ~60 seconds — between issuance and the sweep, an expired row could
    sit in the collection. The filter's `expires_at > now` guard is what
    keeps it inert.
    """
    db = ticket_db
    # Issue with a very short TTL, then jam the row to expired.
    ticket = await sse_tickets.issue_ticket("user_grace", "p1", ttl_seconds=1)
    rows = db["sse_tickets"].all_rows()
    assert len(rows) == 1
    # Directly mutate the underlying row to simulate the clock moving past expiry.
    db["sse_tickets"]._rows[0]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
    )

    consumed = await sse_tickets.consume_ticket(ticket)
    assert consumed is None


@pytest.mark.asyncio
async def test_consume_ticket_returns_distinct_bindings_for_distinct_tickets():
    """Cross-tenant safety: two tickets, two users, two projects — each
    consume returns exactly its own binding (no cross-contamination)."""
    t1 = await sse_tickets.issue_ticket("user_grace", "project_a")
    t2 = await sse_tickets.issue_ticket("user_henry", "project_b")

    c1 = await sse_tickets.consume_ticket(t1)
    c2 = await sse_tickets.consume_ticket(t2)

    assert c1 == {"user_id": "user_grace", "project_id": "project_a"}
    assert c2 == {"user_id": "user_henry", "project_id": "project_b"}


@pytest.mark.asyncio
async def test_concurrent_consume_only_one_wins():
    """Atomicity: if two coroutines race to consume the same ticket,
    exactly one wins. The other gets None.

    Note: this test uses the in-process fake which is single-threaded
    + cooperative. Real Mongo enforces atomicity at the storage layer
    via `findAndModify`. We exercise it here at the contract level: the
    second call must never see an unconsumed row, no matter the order
    of completion.
    """
    ticket = await sse_tickets.issue_ticket("user_grace", "p1")

    results = await asyncio.gather(
        sse_tickets.consume_ticket(ticket),
        sse_tickets.consume_ticket(ticket),
    )
    wins = [r for r in results if r is not None]
    losses = [r for r in results if r is None]
    assert len(wins) == 1
    assert len(losses) == 1
    assert wins[0] == {"user_id": "user_grace", "project_id": "p1"}
