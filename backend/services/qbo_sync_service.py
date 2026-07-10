"""AEGISAXIOM-side orchestration for QBO finance sync.

n8n owns QBO ingestion. AEGISAXIOM only:

  * authenticates the human caller and authorizes the action
  * persists a sync_request record (id, who/what/why, lifecycle status)
  * signs a JSON payload and POSTs it to the configured n8n webhook URL
  * verifies the HMAC signature on the n8n callback that reports
    completion
  * exposes a freshness signal so Nova / dashboards can warn when
    the data is stale

This module deliberately does NOT call QuickBooks. It does not import
``intuitlib``, does not touch QBO OAuth tokens, and does not know what
realm IDs exist. The realm_id, QBO OAuth credentials, and Mongo write
credentials all live in n8n. AEGISAXIOM holds only the symmetric secrets
needed to talk to n8n.

Configuration lives in env vars. Defaults are intentionally absent so
a missing variable surfaces as a 503/configuration error rather than
as a silent fall-through to a wrong URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# n8n production webhook URL (NOT the test URL — the test URL only
# fires while the n8n editor is listening). AEGISAXIOM never falls back to
# the test URL.
_ENV_N8N_WEBHOOK_URL = "N8N_QBO_SYNC_WEBHOOK_URL"
# Symmetric secret used for the HMAC signature on outbound trigger
# requests. n8n verifies this inside the workflow.
_ENV_N8N_SHARED_SECRET = "N8N_QBO_SYNC_SECRET"
# Distinct symmetric secret used for the HMAC signature on inbound
# callbacks from n8n. Kept separate from N8N_QBO_SYNC_SECRET so a
# leaked outbound secret can't forge inbound completion callbacks.
_ENV_AEGISAXIOM_CALLBACK_SECRET = "AEGISAXIOM_QBO_SYNC_CALLBACK_SECRET"
# Public AEGISAXIOM API base URL (e.g. https://api.aegisaxiom.example.com).
# Used to build the absolute callback URL handed to n8n. Never
# hardcoded — would otherwise pin AEGISAXIOM to a single deployment.
_ENV_AEGISAXIOM_API_BASE = "AEGISAXIOM_PUBLIC_API_BASE_URL"
# Feature flag — set to "false" to reject every trigger (incident
# response / read-only mode).
_ENV_TRIGGER_ENABLED = "QBO_SYNC_TRIGGER_ENABLED"
# Staleness threshold for the freshness signal, in seconds. Default
# 6 hours — long enough that a missed nightly sync surfaces, short
# enough that a manual mid-day sync clears it.
_ENV_STALE_AFTER_SECONDS = "QBO_SYNC_STALE_AFTER_SECONDS"


# Closed sets used by the trigger-endpoint validator. Adding a new
# value here without updating the n8n workflow is a contract bug, so
# the lists stay narrow and explicit.
SUPPORTED_SOURCES: tuple[str, ...] = (
    "gl",
    "invoices",
    "bills",
    "transactions",
    "chart_of_accounts",
    "ar_aging",
)
SUPPORTED_MODES: tuple[str, ...] = ("incremental", "range_backfill")
SUPPORTED_BASES: tuple[str, ...] = ("accrual", "cash", "both")

# Statuses that the AEGISAXIOM sync_request record can carry. Sequence is
# documented; transition validation lives in
# ``record_callback_update``.
STATUS_QUEUED = "queued"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"
_TERMINAL_STATUSES = {STATUS_COMPLETE, STATUS_FAILED}

SYNC_REQUESTS_COLLECTION = "qbo_sync_requests"

# Default callback timeout when calling n8n. Short — n8n is expected
# to respond immediately and continue the workflow asynchronously.
_N8N_TRIGGER_TIMEOUT_SECONDS = 10.0


def _env_required(name: str) -> str:
    """Read a required env var; raise a clear error if missing.

    Used by the trigger path so a misconfigured deployment fails loud
    at request time rather than POSTing to ``None``.
    """
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise QboSyncConfigError(f"{name} is not configured.")
    return value.strip()


def trigger_enabled() -> bool:
    """Honour the QBO_SYNC_TRIGGER_ENABLED kill switch. Defaults to
    False so a fresh deployment that hasn't been intentionally enabled
    can't accidentally start triggering syncs."""
    raw = (os.environ.get(_ENV_TRIGGER_ENABLED) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def stale_after_seconds() -> int:
    raw = (os.environ.get(_ENV_STALE_AFTER_SECONDS) or "").strip()
    if not raw:
        return 6 * 60 * 60  # 6 hours
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning(
            "%s=%r is not a positive int; defaulting to 6h.",
            _ENV_STALE_AFTER_SECONDS,
            raw,
        )
        return 6 * 60 * 60


def callback_url() -> str:
    """Build the absolute callback URL n8n should POST to."""
    base = _env_required(_ENV_AEGISAXIOM_API_BASE).rstrip("/")
    return f"{base}/api/qbo/sync/callback"


def n8n_webhook_url() -> str:
    return _env_required(_ENV_N8N_WEBHOOK_URL)


def _outbound_secret() -> str:
    return _env_required(_ENV_N8N_SHARED_SECRET)


def _inbound_secret() -> str:
    return _env_required(_ENV_AEGISAXIOM_CALLBACK_SECRET)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QboSyncConfigError(RuntimeError):
    """Raised when an env var required by the sync layer is missing.

    The trigger / callback routes catch this and translate it into a
    503 with a "configuration" reason so the response is actionable.
    """


class QboSyncSignatureError(RuntimeError):
    """Raised by ``verify_callback_signature`` when the HMAC fails.

    Routes turn this into 401. Body content is NOT echoed — the
    signature check ran before any deserialization.
    """


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------


class QboSyncSigningError(ValueError):
    """Raised when an outbound n8n payload cannot be canonically
    JSON-serialised for HMAC signing. Surfaces a controlled error
    instead of silently coercing non-serialisable values (datetime,
    ObjectId, custom objects) via ``default=str`` — that path was
    silently breaking HMAC canonicalisation when n8n re-serialised
    the same bytes without the coercion."""


def _strict_default(value):
    """Strict default for json.dumps. Raise TypeError when the
    encoder hits a value that is not JSON native so the HMAC
    canonical form cannot diverge between sender and receiver via
    silent string coercion. The thrown TypeError names the value
    type so the operator can see exactly which field broke the
    payload."""
    raise TypeError(
        "non serializable value of type "
        + type(value).__name__
        + " in HMAC payload"
    )


def _serialize_for_signing(payload: dict[str, Any]) -> bytes:
    """Canonical JSON serialization used on both sides of the wire.

    sort_keys plus separators give a single byte sequence both
    sides can reproduce. The strict default callable above raises
    TypeError on any non JSON native value so a datetime or
    ObjectId added by a future code path fails loudly here rather
    than producing bytes the receiver cannot reproduce."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_strict_default,
    ).encode("utf-8")


def sign_outbound_payload(payload: dict[str, Any]) -> tuple[str, bytes]:
    """Return (signature_header_value, body_bytes) for the n8n trigger.

    The signature is ``"sha256=<hex>"``; body bytes are what the
    caller MUST send verbatim — re-serializing on the wire would
    invalidate the digest. The secret never appears in the return
    value.
    """
    body = _serialize_for_signing(payload)
    digest = hmac.new(
        _outbound_secret().encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}", body


def verify_callback_signature(raw_body: bytes, signature_header: Optional[str]) -> None:
    """Verify the X-N8N-Signature header on an inbound callback.

    Raises QboSyncSignatureError on any failure (missing header, wrong
    prefix, bad hex, mismatch). Uses ``hmac.compare_digest`` so the
    verification timing doesn't leak prefix information about the
    expected digest.
    """
    if not signature_header:
        raise QboSyncSignatureError("missing signature")
    if not signature_header.startswith("sha256="):
        raise QboSyncSignatureError("unsupported signature scheme")
    provided = signature_header.split("=", 1)[1].strip()
    expected = hmac.new(
        _inbound_secret().encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise QboSyncSignatureError("signature mismatch")


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def new_sync_request_id() -> str:
    return f"qbosync_{uuid.uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def build_sync_request_record(
    *,
    sync_request_id: str,
    requested_by_user_id: Optional[str],
    requested_source: str,
    sources: Iterable[str],
    mode: str,
    basis: str,
    date_from: Optional[str],
    date_to: Optional[str],
) -> dict[str, Any]:
    """Build the persisted record. Values are intentionally simple
    (str / list / None / datetime / dict) so the same record can be
    serialized by the API's JSON encoder without further coercion.

    Status starts at ``queued``. Endpoints flip to ``acknowledged``
    after n8n returns 2xx, then ``running`` / ``complete`` / ``failed``
    via the callback. Counts and errors are populated by the callback;
    started_at / finished_at come from n8n's clock when present.
    """
    now = _utcnow()
    return {
        "sync_request_id": sync_request_id,
        "requested_by_user_id": requested_by_user_id,
        "requested_source": requested_source,
        "mode": mode,
        "sources": list(sources),
        "basis": basis,
        "date_from": date_from,
        "date_to": date_to,
        "status": STATUS_QUEUED,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "last_heartbeat_at": now,
        "counts": {},
        "errors": [],
        "n8n_execution_id": None,
        "callback_received": False,
    }


async def insert_sync_request(db: Any, record: dict[str, Any]) -> None:
    await db[SYNC_REQUESTS_COLLECTION].insert_one(record)


# Sentinel constants for the atomic concurrency guard.
_ACTIVE_LOCK_KEY = "is_active_lock"
_ACTIVE_LOCK_VALUE = "global"


async def acquire_sync_slot(
    db: Any, record: dict[str, Any],
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Atomically claim the "one active sync at a time" slot.

    Returns ``(acquired_record, existing_running)``. Exactly one of
    the two is non-None:

      * ``(acquired_record, None)`` — the caller now owns the slot.
        ``record`` was inserted with ``is_active_lock="global"`` so a
        partial unique index on that field rejects any concurrent
        attempt with a DuplicateKeyError.
      * ``(None, existing_running)`` — another sync already holds the
        slot. The caller should return its existing-running response
        without firing a second n8n trigger.

    Two layers of protection:

      1. A non-atomic ``find_running`` pre-check rejects records that
         already exist in an active state (queued / acknowledged /
         running) even when they were inserted without the partial-
         unique-index lock field. This preserves the legacy advisory
         guard for records produced before the index existed and for
         test fixtures that inject records directly.
      2. The actual ``insert_one`` carries
         ``is_active_lock="global"``, which the partial unique index
         rejects with ``DuplicateKeyError`` on a concurrent racer.
         This is the strict-atomic layer: two simultaneous callers
         cannot both succeed because Mongo serialises the unique-
         index check.

    The lock is released by ``release_sync_slot`` (called from the
    terminal-status transition path) which unsets the lock field
    so the next sync can acquire.

    Schema note: the partial unique index lives on
    ``{is_active_lock: 1}`` with ``partialFilterExpression={
    is_active_lock: "global"}``. The index is created at startup in
    ``server.ensure_indexes``.
    """
    try:
        from pymongo.errors import DuplicateKeyError
    except ImportError:  # pragma: no cover — pymongo is in requirements
        DuplicateKeyError = Exception  # type: ignore[assignment]

    # Layer 1: legacy advisory check.
    existing = await find_running(db)
    if existing is not None:
        return None, existing

    # Layer 2: atomic insert with the lock field. Wins the race
    # against any concurrent caller that passed the same pre-check.
    locked_record = dict(record)
    locked_record[_ACTIVE_LOCK_KEY] = _ACTIVE_LOCK_VALUE
    try:
        await db[SYNC_REQUESTS_COLLECTION].insert_one(locked_record)
        return locked_record, None
    except DuplicateKeyError:
        existing = await find_running(db)
        return None, existing


async def release_sync_slot(db: Any, sync_request_id: str) -> None:
    """Release the active-sync slot held by ``sync_request_id``.

    Called from the terminal-status transition path so the next
    queued/triggered sync can acquire the slot. Safe to call on a
    record that no longer holds the lock — the $unset is a no-op."""
    await db[SYNC_REQUESTS_COLLECTION].update_one(
        {"sync_request_id": sync_request_id, _ACTIVE_LOCK_KEY: _ACTIVE_LOCK_VALUE},
        {"$unset": {_ACTIVE_LOCK_KEY: ""}},
    )


async def get_sync_request(db: Any, sync_request_id: str) -> Optional[dict[str, Any]]:
    return await db[SYNC_REQUESTS_COLLECTION].find_one(
        {"sync_request_id": sync_request_id},
        {"_id": 0},
    )


async def update_sync_request(
    db: Any,
    sync_request_id: str,
    *,
    set_fields: Optional[dict[str, Any]] = None,
) -> None:
    if not set_fields:
        return
    await db[SYNC_REQUESTS_COLLECTION].update_one(
        {"sync_request_id": sync_request_id},
        {"$set": set_fields},
    )


async def find_latest_completed(db: Any) -> Optional[dict[str, Any]]:
    return await db[SYNC_REQUESTS_COLLECTION].find_one(
        {"status": STATUS_COMPLETE, "finished_at": {"$ne": None}},
        {"_id": 0},
        sort=[("finished_at", -1)],
    )


async def find_running(db: Any) -> Optional[dict[str, Any]]:
    return await db[SYNC_REQUESTS_COLLECTION].find_one(
        {"status": {"$in": [STATUS_QUEUED, STATUS_ACKNOWLEDGED, STATUS_RUNNING]}},
        {"_id": 0},
        sort=[("created_at", -1)],
    )


async def find_latest_any_status(db: Any) -> Optional[dict[str, Any]]:
    return await db[SYNC_REQUESTS_COLLECTION].find_one(
        {},
        {"_id": 0},
        sort=[("created_at", -1)],
    )


# ---------------------------------------------------------------------------
# State-transition rules for inbound callbacks
# ---------------------------------------------------------------------------


def reconcile_callback_status(
    *,
    current_status: str,
    incoming_status: str,
) -> tuple[str, bool]:
    """Decide what the new status should be after a callback.

    Returns ``(next_status, accepted)``. ``accepted=False`` means the
    caller should record the callback (so duplicates remain visible
    in the audit trail) but NOT regress the state — e.g. a ``running``
    callback arriving AFTER ``complete`` is a late n8n heartbeat for
    a finished run; we keep ``complete`` and flag the caller.

    Rules:
      * any -> same status: idempotent, accepted (counts/errors will
        merge into the latest snapshot)
      * not-yet-terminal -> running / complete / failed: accept the
        progression
      * complete -> running / failed: reject — terminal states are
        sticky
      * failed -> running: reject — failed is terminal
      * failed -> complete: reject — failed is terminal (operator
        retry should create a new sync_request_id)
    """
    if incoming_status == current_status:
        return current_status, True
    if current_status in _TERMINAL_STATUSES:
        return current_status, False
    if incoming_status not in (STATUS_RUNNING, STATUS_COMPLETE, STATUS_FAILED):
        # Unknown / out-of-band incoming status — don't move the
        # record. Validator rejects these upstream anyway; this is a
        # defensive belt.
        return current_status, False
    return incoming_status, True


# ---------------------------------------------------------------------------
# Outbound trigger
# ---------------------------------------------------------------------------


async def post_to_n8n(
    *,
    payload: dict[str, Any],
    sync_request_id: str,
    timeout: float = _N8N_TRIGGER_TIMEOUT_SECONDS,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """POST the signed payload to the n8n webhook.

    Returns ``(accepted, n8n_execution_id_or_none, error_or_none)``.
    Never raises — network errors are translated into ``accepted=False``
    so the caller can record them on the sync_request record. The
    exception text is returned but NOT logged at WARNING level with
    the body, because the body contains the user_id (low-risk PII)
    and we'd rather not pollute warning logs with it on every retry.

    A separate ``client`` may be passed by tests to avoid hitting the
    network. The client is NOT closed inside this helper when one is
    passed in — caller-owned.
    """
    own_client = client is None
    # sign_outbound_payload() and n8n_webhook_url() can raise
    # QboSyncConfigError when an env var is missing. They MUST run
    # inside the try block so the explicit `except QboSyncConfigError`
    # below catches and re-raises them — keeping them above the try
    # made the except clause dead (the exception still propagated,
    # but via the implicit fall-through, not the documented branch).
    try:
        signature, body = sign_outbound_payload(payload)
        headers = {
            "Content-Type": "application/json",
            "X-AEGISAXIOM-Signature": signature,
            "X-AEGISAXIOM-Sync-Request-Id": sync_request_id,
        }
        url = n8n_webhook_url()

        if own_client:
            client = httpx.AsyncClient(timeout=timeout)
        try:
            resp = await client.post(url, content=body, headers=headers)
        finally:
            if own_client and client is not None:
                await client.aclose()
    except httpx.HTTPError as exc:
        return False, None, f"http error: {exc.__class__.__name__}"
    except QboSyncConfigError:
        # bubble up — caller treats this as a 503 / config issue
        raise

    if resp.status_code // 100 != 2:
        # Body is NOT logged — n8n's error response can echo headers
        # we'd rather not see in our logs. Status code + length is
        # enough to triage.
        return False, None, f"n8n returned status {resp.status_code}"

    n8n_execution_id: Optional[str] = None
    try:
        data = resp.json()
        if isinstance(data, dict):
            value = data.get("n8n_execution_id")
            if isinstance(value, str) and value:
                n8n_execution_id = value
    except Exception:  # noqa: BLE001 — non-JSON ack is acceptable
        pass
    return True, n8n_execution_id, None


def build_n8n_payload(
    *,
    record: dict[str, Any],
    callback_url_value: str,
) -> dict[str, Any]:
    """Build the body sent to n8n. Mirrors the contract documented in
    docs/qbo/N8N_QBO_SYNC_CONTRACT.md exactly — both sides must stay
    in sync."""
    return {
        "sync_request_id": record["sync_request_id"],
        "requested_by_user_id": record.get("requested_by_user_id"),
        "requested_source": record["requested_source"],
        "sources": list(record["sources"]),
        "mode": record["mode"],
        "basis": record["basis"],
        "date_from": record.get("date_from"),
        "date_to": record.get("date_to"),
        "callback_url": callback_url_value,
        "requested_at": _iso(record["created_at"]),
    }


# ---------------------------------------------------------------------------
# Freshness signal (consumed by Nova + dashboards)
# ---------------------------------------------------------------------------


async def get_finance_data_freshness(db: Any) -> dict[str, Any]:
    """Return the AEGISAXIOM freshness signal.

    Reads the ``qbo_sync_requests`` collection only. NEVER calls
    QuickBooks. NEVER calls n8n. Nova's freshness branch consumes
    this dict directly — no further translation needed.

    Output shape:

      {
        "last_successful_sync_at": "<iso8601 | null>",
        "sync_lag_seconds": <int | null>,
        "is_stale": <bool>,
        "warning": "<human string | null>"
      }
    """
    record = await find_latest_completed(db)
    threshold = stale_after_seconds()
    if not record or not record.get("finished_at"):
        # Never had a successful sync — treat as stale.
        return {
            "last_successful_sync_at": None,
            "sync_lag_seconds": None,
            "is_stale": True,
            "warning": (
                "Finance data may be stale. No successful QBO sync has "
                "been recorded yet."
            ),
        }
    finished_at: datetime = record["finished_at"]
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    lag = (_utcnow() - finished_at).total_seconds()
    is_stale = lag > threshold
    warning: Optional[str] = None
    if is_stale:
        warning = (
            "Finance data may be stale. Last successful QBO sync was "
            f"{_iso(finished_at)}."
        )
    return {
        "last_successful_sync_at": _iso(finished_at),
        "sync_lag_seconds": int(lag),
        "is_stale": is_stale,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Finance-data presence + freshness snapshot
# ---------------------------------------------------------------------------
#
# get_finance_data_freshness above answers ONLY "how stale is the AEGISAXIOM
# sync metadata?". A missing qbo_sync_requests record means freshness is
# unknown — it does NOT mean the underlying finance collections are
# empty. Nova was previously over-interpreting a "no sync record"
# warning as "no finance data exists at all," so this helper inspects
# the actual finance collections and surfaces both signals separately.

# Collections that back Nova's read tools. Order matters only for the
# rendered listing; usability decisions key off general_ledger
# specifically because every projection / GL aggregation tool reads
# from it.
_FINANCE_COLLECTIONS: tuple[str, ...] = (
    "general_ledger",
    "ar_invoices",
    "ap_bills",
    "transactions",
    "chart_of_accounts",
    "reports_ar_aging_detail",
)


async def _safe_count(coll: Any) -> int:
    """count_documents({}) wrapped — return 0 if the collection doesn't
    exist or the driver raises. Nova must never crash because a
    finance collection happens not to be provisioned in this tenant."""
    try:
        value = await coll.count_documents({})
    except Exception as exc:  # noqa: BLE001
        logger.debug("count_documents failed for finance collection: %s", exc)
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _safe_latest_synced_at(coll: Any) -> Optional[str]:
    """Return the most recent ``synced_at`` ISO timestamp recorded on a
    row in this collection, or None if the field isn't carried."""
    try:
        doc = await coll.find_one(
            {"synced_at": {"$ne": None}},
            {"synced_at": 1, "_id": 0},
            sort=[("synced_at", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("find_one(synced_at) failed: %s", exc)
        return None
    if not doc:
        return None
    value = doc.get("synced_at")
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, str) and value:
        return value
    return None


async def _safe_max_tx_date(coll: Any) -> Optional[str]:
    """Return the maximum ``tx_date`` in general_ledger as YYYY-MM-DD,
    or None if the collection is empty or the field is missing."""
    pipeline = [{"$group": {"_id": None, "max_date": {"$max": "$tx_date"}}}]
    try:
        cursor = coll.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
    except Exception as exc:  # noqa: BLE001
        logger.debug("aggregate(max tx_date) failed: %s", exc)
        return None
    if not docs:
        return None
    raw = docs[0].get("max_date")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    # date object
    try:
        return raw.isoformat()  # type: ignore[union-attr]
    except AttributeError:
        if isinstance(raw, str):
            return raw[:10]
        return None


async def _safe_distinct_basis(coll: Any) -> list[str]:
    """Return the distinct ``basis`` values present in general_ledger,
    or [] if the field is absent. Sorted for deterministic output."""
    try:
        values = await coll.distinct("basis")
    except Exception as exc:  # noqa: BLE001
        logger.debug("distinct(basis) failed: %s", exc)
        return []
    cleaned = [v for v in values if isinstance(v, str) and v]
    return sorted(set(cleaned))


# Sort key for by_basis output. Accrual ranks before cash because the
# P&L / projection engine reads accrual; everything else falls back to
# alphabetical so unknown basis values still land in a deterministic
# spot.
_BASIS_SORT_PRIORITY: dict[str, int] = {"accrual": 0, "cash": 1}


def _basis_sort_key(basis: str) -> tuple[int, str]:
    return (_BASIS_SORT_PRIORITY.get(basis, 99), basis)


def _coerce_tx_date_iso(raw: Any) -> Optional[str]:
    """Normalize a tx_date value (datetime, date, or YYYY-MM-DD string)
    to an ISO YYYY-MM-DD string, or None if uncoercible."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    try:
        return raw.isoformat()  # type: ignore[union-attr]
    except AttributeError:
        if isinstance(raw, str):
            return raw[:10]
        return None


def _coerce_synced_at_iso(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _iso(raw)
    if isinstance(raw, str) and raw:
        return raw
    return None


async def _safe_gl_status_by_basis(coll: Any) -> list[dict[str, Any]]:
    """Group general_ledger by basis and return per-basis row_count,
    latest_tx_date, latest_synced_at. Bases with no rows or with an
    unusable basis label (empty string / non-string) are dropped.
    Sorted accrual-first then alphabetical so the output is stable
    across runs.
    """
    pipeline = [
        {
            "$group": {
                "_id": "$basis",
                "row_count": {"$sum": 1},
                "latest_tx_date": {"$max": "$tx_date"},
                "latest_synced_at": {"$max": "$synced_at"},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    try:
        cursor = coll.aggregate(pipeline)
        # All basis values fit in memory — there are at most a handful
        # (accrual / cash / unknown). Unbounded to_list is fine here.
        docs = await cursor.to_list(length=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("aggregate(by basis) failed: %s", exc)
        return []

    entries: list[dict[str, Any]] = []
    for doc in docs:
        basis = doc.get("_id")
        if not isinstance(basis, str) or not basis:
            # Drop rows whose basis field is missing or non-string —
            # they're not addressable by Nova's per-basis tools and
            # would crowd the report with a meaningless "null" bucket.
            continue
        try:
            row_count = int(doc.get("row_count") or 0)
        except (TypeError, ValueError):
            row_count = 0
        if row_count <= 0:
            continue
        entries.append(
            {
                "basis": basis,
                "row_count": row_count,
                "latest_tx_date": _coerce_tx_date_iso(doc.get("latest_tx_date")),
                "latest_synced_at": _coerce_synced_at_iso(doc.get("latest_synced_at")),
            }
        )

    entries.sort(key=lambda e: _basis_sort_key(e["basis"]))
    return entries


def _latest_source_timestamp(general_ledger_block: dict[str, Any]) -> Optional[str]:
    """Pick the best source-data timestamp to quote to a user.

    Order:
      1. general_ledger.latest_synced_at — the most recent moment a row
         was written into Mongo from QBO. This is the closest thing to
         "when did the data last change?" that doesn't depend on the
         qbo_sync_requests log.
      2. general_ledger.latest_tx_date — fall back to the most recent
         transaction date carried in the GL when no synced_at column
         is populated.

    Returns None when neither is available. Never returns a value
    pulled from qbo_sync_requests — that field is a sync-log artifact,
    not source-data freshness.
    """
    latest_synced_at = general_ledger_block.get("latest_synced_at")
    if isinstance(latest_synced_at, str) and latest_synced_at:
        return latest_synced_at
    latest_tx_date = general_ledger_block.get("latest_tx_date")
    if isinstance(latest_tx_date, str) and latest_tx_date:
        return latest_tx_date
    return None


def build_finance_data_age_note(
    status: dict[str, Any],
    *,
    audience: str = "user",
) -> Optional[str]:
    """Render a one-line data-age note from a get_finance_data_status payload.

    audience="user" (default):
      - Speaks in source-data terms ("latest available finance data in AEGISAXIOM").
      - Never mentions qbo_sync_requests, "stale", or "no successful QBO sync".
      - Returns "Finance data is not available for this question yet."
        when finance_data_present is false.

    audience="admin":
      - Surfaces the diagnostic sync-log warning from sync_status.warning
        verbatim, so dashboards / debug consumers can still see it.

    Pure function — does no I/O. Used by callers (Nova, the FastAPI
    routes, the admin diagnostics page) so the user-vs-admin wording
    rules live in one place.
    """
    data_status = status.get("data_status") or {}
    sync_status = status.get("sync_status") or {}
    finance_data_present = bool(data_status.get("finance_data_present"))
    gl_block = data_status.get("general_ledger") or {}

    if audience == "admin":
        admin_warning = sync_status.get("warning")
        if isinstance(admin_warning, str) and admin_warning:
            return admin_warning
        last_sync = sync_status.get("last_successful_sync_at")
        if isinstance(last_sync, str) and last_sync:
            return f"Last successful AEGISAXIOM-triggered QBO sync at {last_sync}."
        return "No successful AEGISAXIOM-triggered QBO sync has been recorded yet."

    # audience="user" (default)
    if not finance_data_present:
        return "Finance data is not available for this question yet."

    timestamp = _latest_source_timestamp(gl_block)
    if timestamp:
        return (
            f"Based on the latest available finance data in AEGISAXIOM as of "
            f"{timestamp}."
        )
    return "Based on the latest available finance data in AEGISAXIOM."


async def get_finance_data_status(db: Any) -> dict[str, Any]:
    """Read-only snapshot of AEGISAXIOM sync freshness AND finance-collection
    presence. NEVER calls QuickBooks. NEVER calls n8n.

    A missing ``qbo_sync_requests`` record means the AEGISAXIOM-triggered sync
    has not run yet — freshness is unknown. It does NOT mean the
    finance collections are empty. Callers (Nova) should consult
    ``data_status.finance_data_present`` and
    ``data_status.general_ledger_usable`` for actual data availability,
    and quote ``sync_status.warning`` only as freshness commentary.

    Output shape:

      {
        "user_facing_data_note": "Based on the latest available finance data in AEGISAXIOM as of <ts>.",
        "admin_sync_note": "<diagnostic line for admin dashboards / sync screens>",
        "sync_status": {
          "last_successful_sync_at": "<iso8601 | null>",
          "sync_lag_seconds": <int | null>,
          "is_stale": <bool>,
          "warning": "<human string | null>"  # admin/diagnostic only
        },
        "data_status": {
          "finance_data_present": <bool>,
          "general_ledger_usable": <bool>,
          "general_ledger": {
            "collection": "general_ledger",
            "exists": <bool>,
            "row_count": <int>,
            "latest_tx_date": "<YYYY-MM-DD | null>",  # ALL-BASIS — see by_basis for accrual / cash separately
            "latest_tx_date_basis": "all",
            "available_basis_values": ["<...>"],
            "latest_synced_at": "<iso8601 | null>",
            "by_basis": [
              {
                "basis": "<accrual | cash | ...>",
                "row_count": <int>,
                "latest_tx_date": "<YYYY-MM-DD | null>",
                "latest_synced_at": "<iso8601 | null>"
              },
              ...
            ]
          },
          "collections": {
            "<name>": {
              "collection": "<name>",
              "exists": <bool>,
              "row_count": <int>,
              "latest_synced_at": "<iso8601 | null>"
            },
            ...
          }
        },
        "testing_status": {
          "usable_for_finance_testing": <bool>,
          "reason": "<human string>",
          "warnings": ["<...>"]
        }
      }
    """
    sync_status = await get_finance_data_freshness(db)

    gl_coll = db["general_ledger"]
    gl_count = await _safe_count(gl_coll)
    gl_latest_tx_date = await _safe_max_tx_date(gl_coll) if gl_count > 0 else None
    gl_latest_synced_at = await _safe_latest_synced_at(gl_coll) if gl_count > 0 else None
    gl_by_basis = await _safe_gl_status_by_basis(gl_coll) if gl_count > 0 else []
    # Derive available basis values from the by_basis aggregation when
    # we have it — same source of truth, same rows, no chance of drift
    # between distinct() and the per-basis numbers Nova will quote.
    gl_basis_values = sorted({entry["basis"] for entry in gl_by_basis})

    general_ledger_block = {
        "collection": "general_ledger",
        "exists": gl_count > 0,
        "row_count": gl_count,
        # latest_tx_date here is the ALL-BASIS max. Per-basis dates may
        # diverge (e.g. cash rows extending past accrual rows); callers
        # must read by_basis when they need the accrual or cash answer.
        "latest_tx_date": gl_latest_tx_date,
        "latest_tx_date_basis": "all",
        "available_basis_values": gl_basis_values,
        "latest_synced_at": gl_latest_synced_at,
        "by_basis": gl_by_basis,
    }

    collections_block: dict[str, dict[str, Any]] = {}
    finance_data_present = gl_count > 0
    for name in _FINANCE_COLLECTIONS:
        if name == "general_ledger":
            continue
        coll = db[name]
        count = await _safe_count(coll)
        if count > 0:
            finance_data_present = True
        collections_block[name] = {
            "collection": name,
            "exists": count > 0,
            "row_count": count,
            "latest_synced_at": await _safe_latest_synced_at(coll) if count > 0 else None,
        }

    general_ledger_usable = gl_count > 0 and gl_latest_tx_date is not None
    usable_for_finance_testing = general_ledger_usable

    warnings: list[str] = []
    if sync_status.get("last_successful_sync_at") is None:
        if finance_data_present:
            # Override the original "no sync recorded" warning so the
            # LLM cannot mistake it for "data missing".
            sync_status = {
                **sync_status,
                "warning": (
                    "No AEGISAXIOM-triggered QBO sync has been recorded yet, so "
                    "freshness is unknown. Finance collections contain "
                    "data — treat any quoted totals as of unknown sync "
                    "age, not as missing."
                ),
            }
            warnings.append("freshness_unknown_data_present")
        else:
            sync_status = {
                **sync_status,
                "warning": (
                    "No AEGISAXIOM-triggered QBO sync has been recorded yet and "
                    "the finance collections are empty. The finance data "
                    "lake is unavailable."
                ),
            }
            warnings.append("no_sync_and_no_data")
    elif sync_status.get("is_stale"):
        warnings.append("sync_stale")

    if not general_ledger_usable:
        warnings.append("general_ledger_unavailable")

    if usable_for_finance_testing:
        reason = (
            f"general_ledger has {gl_count} rows; latest tx_date "
            f"{gl_latest_tx_date}."
        )
    elif gl_count > 0:
        reason = (
            "general_ledger has rows but no tx_date is set; cannot run "
            "date-windowed queries."
        )
    elif finance_data_present:
        reason = (
            "general_ledger is empty; some other finance collections "
            "carry rows but Nova's GL-backed tools cannot run."
        )
    else:
        reason = "All finance collections are empty in this environment."

    payload: dict[str, Any] = {
        "sync_status": sync_status,
        "data_status": {
            "finance_data_present": finance_data_present,
            "general_ledger_usable": general_ledger_usable,
            "general_ledger": general_ledger_block,
            "collections": collections_block,
        },
        "testing_status": {
            "usable_for_finance_testing": usable_for_finance_testing,
            "reason": reason,
            "warnings": warnings,
        },
    }
    # User-facing and admin-facing data-age lines are derived from the
    # rest of the payload so callers don't have to re-implement the
    # wording rules. Nova prefers user_facing_data_note in normal
    # finance answers; admin dashboards / freshness-diagnostics screens
    # use admin_sync_note.
    payload["user_facing_data_note"] = build_finance_data_age_note(payload, audience="user")
    payload["admin_sync_note"] = build_finance_data_age_note(payload, audience="admin")
    return payload


__all__ = [
    "QboSyncConfigError",
    "QboSyncSignatureError",
    "SUPPORTED_BASES",
    "SUPPORTED_MODES",
    "SUPPORTED_SOURCES",
    "SYNC_REQUESTS_COLLECTION",
    "STATUS_ACKNOWLEDGED",
    "STATUS_COMPLETE",
    "STATUS_FAILED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "QboSyncSigningError",
    "acquire_sync_slot",
    "build_finance_data_age_note",
    "build_n8n_payload",
    "build_sync_request_record",
    "callback_url",
    "find_latest_any_status",
    "find_latest_completed",
    "find_running",
    "get_finance_data_freshness",
    "get_finance_data_status",
    "get_sync_request",
    "insert_sync_request",
    "n8n_webhook_url",
    "new_sync_request_id",
    "post_to_n8n",
    "reconcile_callback_status",
    "release_sync_slot",
    "sign_outbound_payload",
    "stale_after_seconds",
    "trigger_enabled",
    "update_sync_request",
    "verify_callback_signature",
]
