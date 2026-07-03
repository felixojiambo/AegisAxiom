"""SSE ticket store — short-lived, single-use exchange tokens for the
EventSource auth handshake.

v1.4 / Realtime Safety — replaces the v1.3 `?access_token=<session_token>`
query-param fallback used by `routes/projects_events.py`. Putting the
long-lived session token in the URL exposed it to LB/proxy access logs,
browser history, and any caching middlebox between client and server.

The exchange
------------
1. Client POSTs to `/api/projects/{project_id}/events/ticket` with normal
   cookie/Bearer auth → server returns a short-TTL, single-use opaque
   `ticket` bound to `(user_id, project_id)`.
2. Client opens `EventSource(.../events?ticket=<ticket>)`.
3. Server's SSE auth dependency (`get_sse_user`) consumes the ticket on
   first read, verifies the bound `project_id` matches the path, and
   loads the user. Subsequent reads of the same ticket fail.

Security properties
-------------------
- 30-second TTL (configurable per-issue) — narrow exposure window for the
  URL-borne token.
- Single-use — `consume_ticket` atomically marks the row consumed; a second
  consume returns None. Even if the URL leaks to a log, the ticket is
  already spent.
- 32 random bytes (`secrets.token_urlsafe(32)`) — not enumerable, not
  predictable, not session-derived.
- Project-bound — a ticket issued for project A returns `project_id=A`;
  the SSE handler MUST re-check that the path project_id matches before
  honoring the ticket. This prevents a compromised viewer of project A
  from upgrading to a stream of project B.

Storage
-------
Mongo collection `sse_tickets` with a TTL index on `expires_at`. Mongo's
TTL monitor reaps expired rows automatically (60-second sweep). The
collection is small (typical row: 8 fields, ~250 bytes) and short-lived,
so the TTL index is sufficient — no in-process LRU is layered on top.

Multi-instance note: Mongo-backed storage works correctly across Cloud Run
instances. An in-process dict cache would NOT — an instance that issued
a ticket might not be the one that handles the SSE connect.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pymongo import ReturnDocument

from database import get_db

logger = logging.getLogger(__name__)


_COLLECTION = "sse_tickets"
_TTL_INDEX_ENSURED = False
_DEFAULT_TTL_SECONDS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_ticket() -> str:
    """32 random bytes, URL-safe base64. ~43 chars; not enumerable."""
    return secrets.token_urlsafe(32)


async def _ensure_indexes(db: Any) -> None:
    """Idempotent: ensure the TTL index on `expires_at` + the unique index
    on `ticket` exist.

    Mongo TTL semantics: rows with `expires_at < now` are deleted by the
    TTL monitor (sweep cadence is ~60s). `expireAfterSeconds=0` means
    "the TTL clock IS the value of expires_at," not "0 seconds after."
    """
    global _TTL_INDEX_ENSURED
    if _TTL_INDEX_ENSURED:
        return
    try:
        await db[_COLLECTION].create_index(
            "expires_at",
            name="sse_tickets_expires_at_ttl",
            expireAfterSeconds=0,
            background=True,
        )
        await db[_COLLECTION].create_index(
            "ticket",
            name="sse_tickets_ticket_unique",
            unique=True,
            background=True,
        )
        _TTL_INDEX_ENSURED = True
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"sse_tickets index ensure failed (non-fatal): {e}")


async def issue_ticket(
    user_id: str,
    project_id: str,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> str:
    """Generate and persist a single-use SSE ticket.

    Returns the opaque ticket string. The caller must perform RBAC
    BEFORE calling this — the ticket store does not re-check permissions.

    The ticket binds to `(user_id, project_id)` so a ticket issued for
    project A cannot connect to project B's stream (the SSE handler
    re-validates the binding on consume).
    """
    db = get_db()
    await _ensure_indexes(db)

    now = _utc_now()
    ticket = _generate_ticket()
    expires_at = now + timedelta(seconds=int(ttl_seconds))

    await db[_COLLECTION].insert_one({
        "ticket": ticket,
        "user_id": user_id,
        "project_id": project_id,
        "issued_at": now,
        "expires_at": expires_at,
        "consumed_at": None,
    })
    return ticket


async def consume_ticket(ticket: str) -> Optional[Dict[str, Any]]:
    """Atomically validate + consume a ticket.

    Returns `{user_id, project_id}` on success, None on failure (unknown
    ticket, already consumed, or expired).

    Implementation: `find_one_and_update` with filter requiring
    `consumed_at: None` AND `expires_at > now` — this is the atomicity
    boundary. A second consumer racing the first sees the row with
    `consumed_at` set and gets None back.
    """
    if not ticket:
        return None

    db = get_db()
    now = _utc_now()

    row = await db[_COLLECTION].find_one_and_update(
        {
            "ticket": ticket,
            "consumed_at": None,
            "expires_at": {"$gt": now},
        },
        {"$set": {"consumed_at": now}},
        # return the doc AS it was AFTER the update so we know the
        # consume succeeded (the matching predicate guarantees it was
        # unconsumed and unexpired).
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0, "user_id": 1, "project_id": 1},
    )
    if not row:
        return None
    return {"user_id": row["user_id"], "project_id": row["project_id"]}
