"""HTTP surface for the AEGISAXIOM-triggered QBO finance sync.

Endpoints:

  * ``POST /api/qbo/sync/trigger`` — human or AI-finance role asks AEGISAXIOM
    to start a sync. AEGISAXIOM persists a sync_request record, signs a JSON
    payload, and POSTs it to the configured n8n webhook URL. n8n owns
    the actual QuickBooks ingestion. AEGISAXIOM does NOT call QBO.
  * ``POST /api/qbo/sync/callback`` — n8n posts here when the workflow
    finishes (or hits a fatal error). The HMAC signature on the
    request body is verified before any state is updated.
  * ``GET /api/qbo/sync/status/{sync_request_id}`` — read a single
    sync_request record.
  * ``GET /api/qbo/sync/latest`` — freshness summary for the most
    recent sync.

n8n's role and AEGISAXIOM's role are documented end-to-end in
docs/qbo/N8N_QBO_SYNC_CONTRACT.md.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from auth_helper import get_current_user, has_perm
from database import get_db
from services import audit_service
from services.qbo_sync_service import (
    QboSyncConfigError,
    QboSyncSigningError,
    QboSyncSignatureError,
    STATUS_ACKNOWLEDGED,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    SUPPORTED_BASES,
    SUPPORTED_MODES,
    SUPPORTED_SOURCES,
    acquire_sync_slot,
    build_n8n_payload,
    build_sync_request_record,
    callback_url,
    find_latest_any_status,
    find_latest_completed,
    find_running,
    get_finance_data_freshness,
    get_sync_request,
    insert_sync_request,
    new_sync_request_id,
    post_to_n8n,
    reconcile_callback_status,
    release_sync_slot,
    stale_after_seconds,
    trigger_enabled,
    update_sync_request,
    verify_callback_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qbo/sync", tags=["qbo-sync"])


# Permission keys used by this router. The trigger endpoint is the
# only mutating action; the read endpoints reuse the existing
# finance-meta read permission so we don't multiply read-only keys
# unnecessarily. Add ``finance.sync.trigger`` to the role seed in
# ``routes/roles.py`` so admin / finance roles grant it.
_PERM_TRIGGER = "finance.sync.trigger"
_PERM_VIEW = "finance.meta.view"

# Callback payload caps. These guard against a signed-but-malicious
# replay (or a runaway n8n error branch) writing an unbounded
# document into Mongo. The byte cap below applies to the full raw
# request body before parsing, so it bounds the cost of the JSON
# decode itself; the count caps live on the Pydantic validators.
_MAX_ERROR_ENTRIES = 200
_MAX_COUNTS_KEYS = 50
_MAX_CALLBACK_BYTES = 256 * 1024  # 256 KiB


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class TriggerRequest(BaseModel):
    """Body of POST /api/qbo/sync/trigger.

    Fields are validated against closed enums so an LLM-built request
    can't smuggle a nonexistent source like ``"raw_data.Line"`` into
    n8n. The validator returns a 400 with a list of accepted values
    on any miss.
    """

    model_config = ConfigDict(extra="forbid")

    sources: List[str] = Field(default_factory=lambda: list(SUPPORTED_SOURCES))
    mode: str = "incremental"
    basis: str = "accrual"
    date_from: Optional[str] = Field(default=None, pattern=_DATE_PATTERN)
    date_to: Optional[str] = Field(default=None, pattern=_DATE_PATTERN)
    reason: Optional[str] = Field(default=None, max_length=200)
    requested_source: str = "manual"

    @field_validator("sources")
    @classmethod
    def _validate_sources(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("at least one source is required")
        bad = [s for s in v if s not in SUPPORTED_SOURCES]
        if bad:
            raise ValueError(
                f"unsupported source(s) {bad!r}; supported: {list(SUPPORTED_SOURCES)}"
            )
        # de-duplicate while preserving order
        seen = set()
        out: List[str] = []
        for s in v:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in SUPPORTED_MODES:
            raise ValueError(
                f"unsupported mode {v!r}; supported: {list(SUPPORTED_MODES)}"
            )
        return v

    @field_validator("basis")
    @classmethod
    def _validate_basis(cls, v: str) -> str:
        if v not in SUPPORTED_BASES:
            raise ValueError(
                f"unsupported basis {v!r}; supported: {list(SUPPORTED_BASES)}"
            )
        return v

    @field_validator("requested_source")
    @classmethod
    def _validate_requested_source(cls, v: str) -> str:
        # Closed set — the audit log groups by this so a typo would
        # quietly create a new bucket.
        allowed = {"manual", "nova", "schedule", "admin"}
        if v not in allowed:
            raise ValueError(
                f"unsupported requested_source {v!r}; supported: {sorted(allowed)}"
            )
        return v

    @model_validator(mode="after")
    def _validate_date_range_coherence(self) -> "TriggerRequest":
        """Reject an inverted date range so the n8n workflow does not
        waste downstream cycles on a window that can never yield rows.
        Both date_from and date_to already passed the pattern check so
        the parse below is safe. The error message is fixed and does
        not echo either input value.
        """
        if self.date_from and self.date_to:
            from datetime import date as _date
            parsed_from = _date.fromisoformat(self.date_from)
            parsed_to = _date.fromisoformat(self.date_to)
            if parsed_from > parsed_to:
                raise ValueError(
                    "date_from must be on or before date_to"
                )
        return self


class CallbackBody(BaseModel):
    """n8n -> AEGISAXIOM callback. Closed shape; extra fields are rejected
    so n8n contract drift surfaces as a 400 rather than as silent
    state corruption.

    counts / errors are capped so a signed-but-malicious replay (or a
    runaway n8n branch that loops on error) can't write an unbounded
    document into Mongo. Caps:
      * errors: at most _MAX_ERROR_ENTRIES list items
      * counts: at most _MAX_COUNTS_KEYS top-level keys
      * combined serialized size below _MAX_CALLBACK_BYTES
    """

    model_config = ConfigDict(extra="forbid")

    sync_request_id: str
    status: str
    n8n_execution_id: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    # counts is intentionally flat: each value MUST be a JSON number
    # (int/float). The validator rejects nested dict/list values to
    # prevent a malicious or runaway n8n branch from writing
    # arbitrarily nested structures into the sync_request record. The
    # n8n contract documents counts as ``{<source>: <int>}`` only.
    counts: Optional[dict] = None
    errors: Optional[list] = None

    @field_validator("counts")
    @classmethod
    def _cap_counts(cls, v):
        if v is None:
            return v
        if len(v) > _MAX_COUNTS_KEYS:
            raise ValueError(
                f"counts has {len(v)} keys; cap is {_MAX_COUNTS_KEYS}"
            )
        # Per-value type enforcement: counts MUST be flat
        # {str: int|float}. Reject dict / list / nested structures
        # so the callback cannot smuggle arbitrary JSON shapes into
        # the sync_request record.
        for k, val in v.items():
            if not isinstance(k, str):
                raise ValueError(
                    "counts keys must be strings"
                )
            # bool is a subclass of int in Python — exclude it
            # explicitly because a True/False here is almost
            # certainly a contract drift on the n8n side.
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(
                    f"counts[{k!r}] must be int or float; got "
                    f"{type(val).__name__}"
                )
        return v

    @field_validator("errors")
    @classmethod
    def _cap_errors(cls, v):
        if v is None:
            return v
        if len(v) > _MAX_ERROR_ENTRIES:
            raise ValueError(
                f"errors list has {len(v)} entries; cap is "
                f"{_MAX_ERROR_ENTRIES}"
            )
        return v

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in {STATUS_RUNNING, STATUS_COMPLETE, STATUS_FAILED}:
            raise ValueError(
                f"callback status {v!r} not allowed; "
                f"must be one of {STATUS_RUNNING}/{STATUS_COMPLETE}/{STATUS_FAILED}"
            )
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_dict(current_user: Any) -> dict:
    if isinstance(current_user, dict):
        return current_user
    if hasattr(current_user, "model_dump"):
        return current_user.model_dump()
    # SimpleNamespace, dataclass, or any object exposing a __dict__ —
    # the auth layer's User shape varies across tests.
    if hasattr(current_user, "__dict__"):
        return dict(current_user.__dict__)
    return dict(current_user)


def _safe_iso_parse(s: Optional[str]) -> Optional[datetime]:
    """Permissive ISO-8601 parser. Returns None on any failure rather
    than raising — bad timestamps from n8n shouldn't break the
    callback path; we just leave the field unset and log a warning.
    """
    if not s:
        return None
    try:
        # datetime.fromisoformat doesn't accept 'Z' before py3.11; trim
        # explicitly.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001 — defensive parse
        logger.warning("qbo_sync callback: could not parse timestamp %r", s)
        return None


# Explicit non underscore prefixed fields that must be stripped
# from the public read API response. Underscore prefixed keys are
# already removed by the loop below. is_active_lock is an internal
# concurrency sentinel and would otherwise leak through any caller
# that holds finance.meta.view.
_REDACT_EXPLICIT_KEYS: frozenset[str] = frozenset({
    "is_active_lock",
})


def _redact_record(record: dict) -> dict:
    """Drop fields the read API does not need to surface and convert
    datetimes to ISO strings so the response is JSON serialisable
    without help from the framework encoder. The exclusion set
    covers both underscore prefixed plumbing keys and the explicit
    list in _REDACT_EXPLICIT_KEYS."""
    out: dict[str, Any] = {}
    for k, v in record.items():
        if k.startswith("_") or k in _REDACT_EXPLICIT_KEYS:
            continue
        if isinstance(v, datetime):
            v_norm = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
            out[k] = v_norm.isoformat()
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# POST /api/qbo/sync/trigger
# ---------------------------------------------------------------------------


@router.post("/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_qbo_sync(
    body: TriggerRequest,
    current_user=Depends(get_current_user),
):
    """Ask AEGISAXIOM to ask n8n to run the QBO ingestion workflow.

    Returns 202 + ``{sync_request_id, status, status_url}`` IMMEDIATELY
    once n8n acknowledges. The caller polls ``GET status/{id}`` (or
    waits for the LLM-facing freshness signal) for completion.
    """
    user = _user_dict(current_user)
    if not has_perm(user, _PERM_TRIGGER):
        raise HTTPException(
            status_code=403,
            detail=f"{_PERM_TRIGGER} permission required",
        )
    if not trigger_enabled():
        raise HTTPException(
            status_code=503,
            detail="QBO sync trigger is disabled (QBO_SYNC_TRIGGER_ENABLED).",
        )

    db = get_db()
    sync_request_id = new_sync_request_id()
    record = build_sync_request_record(
        sync_request_id=sync_request_id,
        requested_by_user_id=user.get("user_id"),
        requested_source=body.requested_source,
        sources=body.sources,
        mode=body.mode,
        basis=body.basis,
        date_from=body.date_from,
        date_to=body.date_to,
    )
    # Atomic concurrency guard. The partial unique index on
    # is_active_lock="global" (see server.ensure_indexes) ensures
    # only one record can hold the slot at a time, so a true race
    # between two simultaneous triggers results in exactly one new
    # sync — the loser gets a 409. This replaces the previous
    # check-then-act (find_running + insert_sync_request) which
    # could admit two parallel syncs. The 409 response code is
    # preserved so existing callers and tests do not have to change.
    acquired, existing = await acquire_sync_slot(db, record)
    if acquired is None:
        raise HTTPException(
            status_code=409,
            detail="a sync is already in progress",
        )

    audit_details = {
        "sync_request_id": sync_request_id,
        "requested_source": body.requested_source,
        "sources": list(body.sources),
        "mode": body.mode,
        "basis": body.basis,
        "date_from": body.date_from,
        "date_to": body.date_to,
        "reason": body.reason,
    }
    await audit_service.log_action(
        action="qbo.sync.triggered",
        actor_id=user.get("user_id") or "system",
        actor_email=user.get("email") or "",
        resource_type="qbo_sync_request",
        resource_id=sync_request_id,
        details=audit_details,
    )

    try:
        cb_url = callback_url()
        payload = build_n8n_payload(record=record, callback_url_value=cb_url)
        accepted, n8n_execution_id, error = await post_to_n8n(
            payload=payload, sync_request_id=sync_request_id
        )
    except QboSyncConfigError as exc:
        # PR cleanup B3. The exception message contains the missing
        # env var name. The public sync_request errors array surfaces
        # via the status endpoint to any caller with finance.meta.view
        # so the DB record carries only a fixed scrubbed string. The
        # full message stays in the server log via logger.error and
        # in the audit log details.
        logger.error("qbo_sync trigger configuration error %s", exc)
        await update_sync_request(
            db,
            sync_request_id,
            set_fields={
                "status": STATUS_FAILED,
                "errors": [{
                    "reason": "configuration",
                    "detail": "server configuration error",
                }],
                "finished_at": datetime.now(timezone.utc),
            },
        )
        # Release the active sync slot. Without this the partial
        # unique index would block future syncs after a config error.
        await release_sync_slot(db, sync_request_id)
        await audit_service.log_action(
            action="qbo.sync.failed",
            actor_id=user.get("user_id") or "system",
            actor_email=user.get("email") or "",
            resource_type="qbo_sync_request",
            resource_id=sync_request_id,
            details={"reason": "configuration", "error": str(exc)},
        )
        raise HTTPException(
            status_code=503,
            detail="QBO sync is misconfigured on the server.",
        )
    except (QboSyncSigningError, TypeError) as exc:
        # The outbound payload contained a non-JSON-canonical value
        # (datetime / ObjectId / custom object). Without this branch
        # the exception escaped as an uncontrolled FastAPI 500 and
        # left the sync record stuck in queued + the active-sync
        # slot locked. Treat it the same way as a config error:
        # record the failure, release the slot, surface a 503 with
        # a user-safe detail. Audit captures the class of the
        # offending value only — never the payload values
        # themselves, which may include user_id / time data.
        await update_sync_request(
            db,
            sync_request_id,
            set_fields={
                "status": STATUS_FAILED,
                "errors": [{"reason": "signing", "detail": "non-canonical payload"}],
                "finished_at": datetime.now(timezone.utc),
            },
        )
        await release_sync_slot(db, sync_request_id)
        await audit_service.log_action(
            action="qbo.sync.failed",
            actor_id=user.get("user_id") or "system",
            actor_email=user.get("email") or "",
            resource_type="qbo_sync_request",
            resource_id=sync_request_id,
            details={
                "reason": "signing",
                "error_class": exc.__class__.__name__,
            },
        )
        logger.exception(
            "qbo_sync outbound payload signing failed for %s",
            sync_request_id,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Failed to serialize outbound QBO sync payload "
                "for signing."
            ),
        )

    if not accepted:
        # n8n refused the trigger — record the failure and surface a
        # 502. The original sync_request_id remains in the DB so the
        # operator can correlate the failure with the audit log.
        await update_sync_request(
            db,
            sync_request_id,
            set_fields={
                "status": STATUS_FAILED,
                "errors": [{"reason": "n8n_unreachable", "detail": error or "unknown"}],
                "finished_at": datetime.now(timezone.utc),
            },
        )
        await release_sync_slot(db, sync_request_id)
        await audit_service.log_action(
            action="qbo.sync.failed",
            actor_id=user.get("user_id") or "system",
            actor_email=user.get("email") or "",
            resource_type="qbo_sync_request",
            resource_id=sync_request_id,
            details={"reason": "n8n_unreachable", "error": error},
        )
        raise HTTPException(
            status_code=502,
            detail="n8n did not accept the sync request.",
        )

    # n8n accepted — flip to ``acknowledged`` so a status check shows
    # the handoff happened. Running / complete / failed transitions
    # come in via the callback.
    set_fields: dict[str, Any] = {
        "status": STATUS_ACKNOWLEDGED,
        "last_heartbeat_at": datetime.now(timezone.utc),
    }
    if n8n_execution_id:
        set_fields["n8n_execution_id"] = n8n_execution_id
    await update_sync_request(db, sync_request_id, set_fields=set_fields)

    await audit_service.log_action(
        action="qbo.sync.acknowledged",
        actor_id=user.get("user_id") or "system",
        actor_email=user.get("email") or "",
        resource_type="qbo_sync_request",
        resource_id=sync_request_id,
        details={"n8n_execution_id": n8n_execution_id},
    )

    return {
        "sync_request_id": sync_request_id,
        "status": STATUS_ACKNOWLEDGED,
        "status_url": f"/api/qbo/sync/status/{sync_request_id}",
        "n8n_execution_id": n8n_execution_id,
    }


# ---------------------------------------------------------------------------
# POST /api/qbo/sync/callback
# ---------------------------------------------------------------------------


@router.post("/callback")
async def qbo_sync_callback(
    request: Request,
    x_n8n_signature: Optional[str] = Header(None, alias="X-N8N-Signature"),
    x_aegisaxiom_sync_request_id: Optional[str] = Header(
        None, alias="X-AEGISAXIOM-Sync-Request-Id"
    ),
):
    """Receive completion notifications from n8n.

    NEVER trusts a normal user session — n8n is the caller and
    authenticates with a shared HMAC secret. The signature is verified
    against the EXACT bytes of the request body. Body parsing happens
    only after a successful verification, so a forged body can't tour
    the validator before being rejected.
    """
    # PR cleanup A5. The routing header is mandatory. Check it
    # before signature verification so an attacker who lifted a
    # signature for a different request cannot replay it by
    # dropping the header. Empty string counts as missing.
    if not x_aegisaxiom_sync_request_id:
        raise HTTPException(
            status_code=400,
            detail="X-AEGISAXIOM-Sync-Request-Id header is required",
        )

    raw_body = await request.body()
    if len(raw_body) > _MAX_CALLBACK_BYTES:
        # Reject before HMAC verification. An oversized body would
        # otherwise force the verifier to hash a multi megabyte
        # input. The cap is well above any realistic callback.
        raise HTTPException(
            status_code=413,
            detail="callback body exceeds size limit",
        )
    try:
        verify_callback_signature(raw_body, x_n8n_signature)
    except QboSyncConfigError as exc:
        # Misconfigured deployment: the inbound secret env var is not
        # set. Refuse the callback rather than accepting unsigned
        # state changes.
        logger.error("qbo_sync callback: %s", exc)
        raise HTTPException(status_code=503, detail="callback verification disabled")
    except QboSyncSignatureError as exc:
        # Don't echo the failure detail in the response — that gives
        # an attacker a side channel. The audit log captures it.
        logger.warning("qbo_sync callback: signature failure (%s)", exc)
        raise HTTPException(status_code=401, detail="invalid signature")

    # Parse the validated body. Pydantic gives us a 400 on shape
    # drift, which is the correct surface for "n8n sent us garbage."
    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON body")
    try:
        body = CallbackBody(**data)
    except Exception as exc:  # noqa: BLE001 — surface validation
        raise HTTPException(status_code=400, detail=str(exc))

    # Defence in depth. The signed body sync_request_id must match
    # the routing header. The header presence guard fired earlier
    # before signature verification, so x_aegisaxiom_sync_request_id is
    # already known to be a non empty string here. Mismatch with
    # the signed body still rejects so an attacker who lifted a
    # signature cannot reuse it against a different record id.
    if x_aegisaxiom_sync_request_id != body.sync_request_id:
        raise HTTPException(
            status_code=400,
            detail="header / body sync_request_id mismatch",
        )

    db = get_db()
    record = await get_sync_request(db, body.sync_request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown sync_request_id")

    next_status, accepted = reconcile_callback_status(
        current_status=record["status"],
        incoming_status=body.status,
    )

    set_fields: dict[str, Any] = {
        "callback_received": True,
        "last_heartbeat_at": datetime.now(timezone.utc),
    }
    if accepted:
        set_fields["status"] = next_status
    if body.n8n_execution_id and not record.get("n8n_execution_id"):
        set_fields["n8n_execution_id"] = body.n8n_execution_id

    started_at = _safe_iso_parse(body.started_at)
    if started_at and not record.get("started_at"):
        set_fields["started_at"] = started_at
    finished_at = _safe_iso_parse(body.finished_at)
    # Only stamp finished_at on terminal transitions. The earlier
    # "or accepted" branch let a forward-progressing running callback
    # that happened to carry a finished_at field overwrite the
    # field on a still-live record — corrupting the freshness signal
    # consumed by get_finance_data_freshness. A running sync has no
    # finished_at by definition; ignore the value if n8n sends one
    # alongside running.
    if finished_at and body.status in (STATUS_COMPLETE, STATUS_FAILED):
        set_fields["finished_at"] = finished_at

    if body.counts is not None:
        # Replace, don't accumulate — duplicate callbacks must not
        # double-count.
        set_fields["counts"] = dict(body.counts)
    if body.errors is not None:
        set_fields["errors"] = list(body.errors)

    await update_sync_request(db, body.sync_request_id, set_fields=set_fields)
    # Release the active-sync slot once the record reaches a terminal
    # status so the next queued sync can acquire. release_sync_slot
    # is idempotent — calling it on a record that does not hold the
    # lock is a no-op.
    applied_status = set_fields.get("status", record.get("status"))
    if applied_status in (STATUS_COMPLETE, STATUS_FAILED):
        await release_sync_slot(db, body.sync_request_id)

    audit_action_map = {
        STATUS_COMPLETE: "qbo.sync.completed",
        STATUS_FAILED: "qbo.sync.failed",
    }
    audit_action = audit_action_map.get(body.status, "qbo.sync.callback_received")
    await audit_service.log_action(
        action=audit_action,
        actor_id="n8n",
        actor_email="",
        resource_type="qbo_sync_request",
        resource_id=body.sync_request_id,
        details={
            "incoming_status": body.status,
            "applied_status": set_fields.get("status", record["status"]),
            "accepted": accepted,
            "n8n_execution_id": body.n8n_execution_id,
            "counts": body.counts,
            "error_count": len(body.errors or []),
        },
    )

    return {
        "sync_request_id": body.sync_request_id,
        "status": set_fields.get("status", record["status"]),
        "accepted": accepted,
    }


# ---------------------------------------------------------------------------
# GET /api/qbo/sync/status/{sync_request_id}
# ---------------------------------------------------------------------------


@router.get("/status/{sync_request_id}")
async def qbo_sync_status(
    sync_request_id: str,
    current_user=Depends(get_current_user),
):
    user = _user_dict(current_user)
    if not has_perm(user, _PERM_VIEW):
        raise HTTPException(
            status_code=403,
            detail=f"{_PERM_VIEW} permission required",
        )

    db = get_db()
    record = await get_sync_request(db, sync_request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown sync_request_id")
    return _redact_record(record)


# ---------------------------------------------------------------------------
# GET /api/qbo/sync/latest
# ---------------------------------------------------------------------------


@router.get("/latest")
async def qbo_sync_latest(current_user=Depends(get_current_user)):
    user = _user_dict(current_user)
    if not has_perm(user, _PERM_VIEW):
        raise HTTPException(
            status_code=403,
            detail=f"{_PERM_VIEW} permission required",
        )

    db = get_db()
    freshness = await get_finance_data_freshness(db)
    last_completed = await find_latest_completed(db)
    running = await find_running(db)
    latest_any = await find_latest_any_status(db)

    last_status = (latest_any or {}).get("status")
    last_error: Optional[Any] = None
    if latest_any:
        errors = latest_any.get("errors") or []
        if errors:
            last_error = errors[-1]

    return {
        "last_successful_sync_at": freshness["last_successful_sync_at"],
        "last_status": last_status,
        "sync_lag_seconds": freshness["sync_lag_seconds"],
        "is_stale": freshness["is_stale"],
        "running": running is not None,
        "last_error": last_error,
        "stale_after_seconds": stale_after_seconds(),
        "last_completed_sync_request_id": (
            (last_completed or {}).get("sync_request_id")
        ),
    }


__all__ = ["router"]
